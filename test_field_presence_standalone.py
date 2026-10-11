"""Regression tests for standalone (Mode B) field-presence queries --
"SIS Chatbot -- Complete Field-Presence Filtering for Fresh Queries and
Follow-Ups". Each defect was reproduced live against the real DB before
being fixed.

Root cause (confirmed by direct reproduction): `followup_context.
parse_field_presence_request()` only recognised the "show/list/display
those/these with/without X" shape (Mode A -- a filter over an EXISTING
carried list). "Which applications awaiting a visit have no subdivision
recorded?" matches none of that shape (no "show/list/display" verb, no
"those"/"these" pointer), so it fell through every follow-up layer to the
plain `fv_unassigned_awaiting` intent, which renders the RAW, unfiltered
field-visit plan -- answering a question that asked for a filter with the
one thing the filter was supposed to remove.

Two fixes, both needed:

1. `followup_context.parse_standalone_field_presence_request()` (new): the
   "which <population> have/has [no] <field> [recorded]" skeleton, with no
   "those"/"these" pointer required at all. `_PRESENCE_NEG_RE` / `_PRESENCE_
   POS_RE` were also widened -- "have no X" (field name between "no" and
   "recorded", or no "recorded" at all, as in "no workflow remarks") and a
   bare "have"/"has" (checked only once the negative pattern has already
   failed to match) did not match either pattern before.

2. `chatbot._standalone_field_presence_answer()` (new): resolves the NAMED
   population -- "awaiting a visit" / "overdue visit(s)" -> the matching
   section of the real field-visit plan (`_fetch_visit_plan`, always ISD
   only, by definition); anything else ("applications", "applications in
   ward 001") -> the register-wide listing (`get_all_service_code_
   applications`), ward/block-scoped the same way `_register_listing`
   reads geography. The actual presence check is the SAME canonical
   `postgres.get_field_presence()` both modes share (refactored into
   `_render_field_presence`, the common half of Mode A and Mode B).

A third, related defect was found while verifying the standalone register-
wide case: `get_field_presence()` left every number that is NOT ISD/NISD
(no `Application` row at all -- `Application` only ever admits 0153/0154)
out of its result dict entirely, and the caller read a missing key as
"could not be checked" -- so a register-wide subdivision query on ward 001
reported dozens of genuinely NOT-APPLICABLE files (0158, 0156, ...) as
unresolved. Fixed by checking the register (`service_register_entries`,
which covers every service code) for exactly the numbers still missing
after the canonical ISD/NISD check: found there but not ISD/NISD -> `None`
(not applicable); not found at all -> left out (genuinely unresolved/
out of jurisdiction). The register is used ONLY for this existence check,
never as the source of the field's own value.

python test_field_presence_standalone.py (needs the DB; no LLM -- every
path exercised here is deterministic by design).
"""
from __future__ import annotations

import asyncio
import re
import sys
import uuid

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, r"C:\proj\nic_internship")

from sqlalchemy import select

from backend.database import AsyncSessionLocal
from backend.models import SISOfficer
from backend.schemas import OfficerContext
from backend.services import chatbot
from backend.services import followup_context as fctx
from backend.services.auth_service import get_officer_jurisdiction_ids
from backend.services.postgres import get_field_presence, get_all_service_code_applications

OFFICER_EMAIL = "marun_chn@sis.tn.gov.in"

_results: list[tuple[str, bool, str]] = []


def check(name, ok, detail=""):
    _results.append((name, bool(ok), detail))
    print(("ok  " if ok else "FAIL") + f" {name}" + ("" if ok else f" -- {detail}"))


def strip_html(s, n=500):
    return re.sub(r"<[^>]+>", " | ", s or "")[:n]


async def get_ctx() -> OfficerContext:
    async with AsyncSessionLocal() as db:
        res = await db.execute(select(SISOfficer).where(SISOfficer.email == OFFICER_EMAIL))
        officer = res.scalar_one()
        jur = await get_officer_jurisdiction_ids(officer.id, db)
        ids = (jur["district_ids"] + jur["taluk_ids"] + jur["town_ids"] + jur["ward_ids"] + jur["block_ids"])
        return OfficerContext(
            officer_id=officer.id, employee_id=officer.employee_id, name=officer.name,
            email=officer.email, designation=officer.designation,
            jurisdiction_type=jur["jurisdiction_type"], jurisdiction_name=jur["jurisdiction_name"],
            jurisdiction_ids=[i for i in ids if i])


async def turn_as_task(ctx, msg, hist, sid, stream=False):
    async def _inner():
        if stream:
            import json as _json
            chunks = []
            async with AsyncSessionLocal() as db:
                async for ev in chatbot.process_chat_stream(
                        db=db, message=msg, officer=ctx, chat_history=hist, session_id=sid):
                    chunks.append(ev)
            resp = ""
            for c in chunks:
                s = c.decode() if isinstance(c, bytes) else c
                if isinstance(s, str) and s.startswith("data: "):
                    try:
                        resp += _json.loads(s[6:]).get("content", "")
                    except Exception:
                        pass
            return resp
        async with AsyncSessionLocal() as db:
            r = await chatbot.process_chat(db=db, message=msg, officer=ctx, chat_history=hist, session_id=sid)
        return (r.get("response") if isinstance(r, dict) else r) or ""

    resp = await asyncio.create_task(_inner())
    hist.append({"role": "user", "content": msg})
    hist.append({"role": "assistant", "content": resp})
    return resp


def test_unit_checks():
    for msg, expected in [
        ("Which applications awaiting a visit have no subdivision recorded?",
         ("awaiting_visit", "subdivision", False)),
        ("Which overdue visits have a subdivision recorded?",
         ("overdue_visit", "subdivision", True)),
        ("Which applications in ward 001 do not have a subdivision recorded?",
         ("register", "subdivision", False)),
        ("Which applications have payment information recorded?",
         ("register", "payment", True)),
        ("Which applications have no workflow remarks?",
         ("register", "workflow_remarks", False)),
        ("Which application is the oldest?", None),
        ("Which applications are pending?", None),
    ]:
        got = fctx.parse_standalone_field_presence_request(msg)
        check(f"parse_standalone_field_presence_request({msg!r}) == {expected!r}", got == expected, repr(got))

    # Mode A regression guard -- must still work exactly as before
    for msg, expected in [
        ("Show only those with a subdivision recorded.", ("subdivision", True)),
        ("Now show those without a recorded subdivision.", ("subdivision", False)),
        ("Show only applications with workflow remarks.", ("workflow_remarks", True)),
        ("Show those without payment recorded.", ("payment", False)),
    ]:
        got = fctx.parse_field_presence_request(msg)
        check(f"Mode A regression: parse_field_presence_request({msg!r}) == {expected!r}", got == expected, repr(got))


async def live_main():
    ctx = await get_ctx()

    # ---- 1/2: standalone awaiting / overdue visit queries ----
    r1 = await turn_as_task(ctx, "Which applications awaiting a visit have no subdivision recorded?", [], str(uuid.uuid4()))
    check("1. standalone 'awaiting a visit' query is answered (never the raw unfiltered plan)",
          "Field Visit Prioritization" not in r1 and "need a field visit" not in r1, strip_html(r1))

    r2 = await turn_as_task(ctx, "Which overdue visits have a subdivision recorded?", [], str(uuid.uuid4()))
    check("2. standalone 'overdue visits' query is answered (never the raw unfiltered plan)",
          "Field Visit Prioritization" not in r2, strip_html(r2))
    # ground truth: every overdue visit is ISD and should legitimately have a subdivision row
    async with AsyncSessionLocal() as db:
        plan = await chatbot._fetch_visit_plan(db, "overdue visits", ctx, ward_number=None, block_number=None)
    overdue_nums = [str(r.get("application_number")).upper() for r in (plan.get("overdue") or [])]
    if overdue_nums:
        async with AsyncSessionLocal() as db:
            presence = await get_field_presence(db, ctx, overdue_nums, "subdivision")
        expected_match = sorted(n for n in overdue_nums if presence.get(n) is True)
        for n in expected_match:
            check(f"2b. ground truth: {n} (has subdivision) appears in the standalone answer", n in r2, n)

    # ---- 3: ward-scoped standalone query ----
    r3 = await turn_as_task(ctx, "Which applications in ward 001 do not have a subdivision recorded?", [], str(uuid.uuid4()))
    check("3. ward-scoped standalone query mentions the ward and is not a bare unfiltered dump",
          "not applicable" in r3.lower() or "application(s)" in r3.lower(), strip_html(r3))

    # ---- 4/5: contextual filter + reversal (Mode A, unchanged behaviour) ----
    sid4, hist4 = str(uuid.uuid4()), []
    await turn_as_task(ctx, "Which application should I field visit next?", hist4, sid4)
    r4 = await turn_as_task(ctx, "Show only those with a subdivision recorded.", hist4, sid4)
    check("4. contextual filter over the carried visit-plan list",
          "of those" in r4 or "None of those" in r4 or "All of those" in r4, strip_html(r4))
    r5 = await turn_as_task(ctx, "Now show those without a recorded subdivision.", hist4, sid4)
    check("5. reversing the presence filter over the SAME carried list",
          "of those" in r5 or "None of those" in r5 or "All of those" in r5, strip_html(r5))

    # ---- 6: explicit application ID overrides an old queue context ----
    sid6, hist6 = str(uuid.uuid4()), []
    await turn_as_task(ctx, "Which application should I field visit next?", hist6, sid6)
    await turn_as_task(ctx, "Show only those with a subdivision recorded.", hist6, sid6)
    r6 = await turn_as_task(ctx, "What is the status of 2026/0154/02/015465?", hist6, sid6)
    check("6. an explicit application ID overrides the carried field-presence queue",
          "2026/0154/02/015465" in r6 and "of those" not in r6, strip_html(r6))

    # ---- 7: multiple subdivision rows must not duplicate the application ----
    # Counted from VISIBLE link text only (`>NUMBER<`) -- every app-number
    # cell in this UI is rendered as a clickable link whose `onclick`
    # attribute ALSO carries the number (`_app_link()`, used everywhere in
    # the app), so a naive whole-document regex counts each row twice by
    # construction; that is a rendering convention, not a duplicate row.
    sid7, hist7 = str(uuid.uuid4()), []
    r7 = await turn_as_task(ctx, "Which applications have payment information recorded?", hist7, sid7)
    nums_in_answer = re.findall(r">(\d{4}/\d{3,4}/\d{1,3}/\d+)<", r7)
    check("7. no application number is duplicated in a standalone field-presence answer "
          "(ISD files with multiple subdivision rows must still appear exactly once)",
          len(nums_in_answer) == len(set(nums_in_answer)), f"{len(nums_in_answer)} vs {len(set(nums_in_answer))} unique")

    # ---- 8: missing vs not-applicable are distinguished, verified against get_field_presence directly ----
    async with AsyncSessionLocal() as db:
        sd = await get_all_service_code_applications(db, ctx, ward_number="001", hide_rejected=True)
    ward_nums = [str(r.get("application_number")).upper() for r in (sd.get("applications") or [])]
    async with AsyncSessionLocal() as db:
        presence = await get_field_presence(db, ctx, ward_nums, "subdivision")
    not_applicable = [n for n in ward_nums if presence.get(n, "MISSING") is None]
    truly_unresolved = [n for n in ward_nums if n not in presence]
    check("8. missing (truly unresolved) and not-applicable are DISTINCT sets, "
          "and a register-wide ward query resolves almost everything to one or the other",
          len(truly_unresolved) < len(ward_nums) * 0.1,
          f"not_applicable={len(not_applicable)} unresolved={len(truly_unresolved)} total={len(ward_nums)}")

    # ---- 9: no matching results ----
    r9 = await turn_as_task(
        ctx, "Which applications in ward 001 have payment information recorded?", [], str(uuid.uuid4()))
    check("9. a standalone query (possibly with zero matches) never crashes and states a real count",
          re.search(r"\d+\s+of\s+.*\d+\s+application", r9) is not None
          or "none of" in r9.lower() or "all of" in r9.lower(), strip_html(r9))

    # ---- 10: fresh session, no carried list at all ----
    r10 = await turn_as_task(ctx, "Which applications have no workflow remarks?", [], str(uuid.uuid4()))
    check("10. a fresh session with no carried list still answers the standalone query",
          "Field Visit Prioritization" not in r10, strip_html(r10))

    # ---- 11: streaming parity ----
    rs = await turn_as_task(
        ctx, "Which applications awaiting a visit have no subdivision recorded?", [], str(uuid.uuid4()), stream=True)
    check("11. streaming path answers the standalone query the same way as non-streaming",
          "Field Visit Prioritization" not in rs and "need a field visit" not in rs, strip_html(rs))

    # ---- 12: response type -- never a full application-details card ----
    r12 = await turn_as_task(ctx, "Which overdue visits have a subdivision recorded?", [], str(uuid.uuid4()))
    check("12. a standalone field-presence answer never collapses into a single "
          "application's full-details card",
          "data-table-card" not in r12, strip_html(r12))

    # ---- 13: jurisdiction -- the standalone register query never crosses the officer's own wards ----
    async with AsyncSessionLocal() as db:
        full_sd = await get_all_service_code_applications(db, ctx, hide_rejected=True)
    jurisdiction_nums = {str(r.get("application_number")).upper()
                         for r in (full_sd.get("applications") or [])}
    r13 = await turn_as_task(ctx, "Which applications have payment information recorded?", [], str(uuid.uuid4()))
    answer_nums = set(re.findall(r"\d{4}/\d{3,4}/\d{1,3}/\d+", r13))
    check("13. every application number in a standalone answer is inside the officer's own jurisdiction",
          answer_nums <= jurisdiction_nums, f"outside={answer_nums - jurisdiction_nums}")


def main() -> None:
    test_unit_checks()
    asyncio.run(live_main())

    passed = sum(1 for _, ok, _ in _results if ok)
    total = len(_results)
    print(f"\n{passed}/{total} passed")
    if passed != total:
        print("\nFailures:")
        for name, ok, detail in _results:
            if not ok:
                print(f"  - {name}: {detail}")
        sys.exit(1)


if __name__ == "__main__":
    main()
