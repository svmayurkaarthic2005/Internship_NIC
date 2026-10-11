"""Regression tests for Sequence D (context isolation) and the filter-
restoration chain-identity fix -- the "Final Context-Isolation and Table-
State Consistency Audit" task. Each defect was reproduced live against the
real DB before being fixed.

D1 -- `chatbot._resolve_rejected_widen()`'s "show rejected applications"
alternative had no end anchor, so "Show rejected applications in ward 005."
(a complete, self-contained listing request naming its OWN ward) matched it
too and was rewritten into "all pending approved rejected applications",
discarding "ward 005" entirely -- a 152-row, completely unscoped answer for
a question that asked for 2 rejected files in one specific ward. Fixed by
refusing the widen reading whenever the message names its own ward/block/
service code/year -- a continuation phrase never does.

D2 -- `base_query_filters` chain identity. Once D1 is fixed, "Show rejected
applications in ward 005." legitimately starts a NEW register-listing scope
-- but `chatbot._register_listing`'s snapshot inheritance only checked
*whether* a snapshot existed, never whether the carried one still describes
the SAME chain. "Restore the original filter." several turns later reached
straight past the new ward-005 scope and handed back the stale ward-001
pending snapshot, purely because it happened to be saved earlier in the
conversation. Fixed with a chain-boundary check: a carried snapshot is only
inherited when this query's own resolved ward/block/service_code does not
explicitly conflict with it; a genuine conflict starts a fresh snapshot.

D3 -- `chatbot._neg_scope.rewrite()` (the negation-over-a-LIST rewriter) has
no concept of a singular referent, so "Is it not rejected?" right after
"Show full application details for X" was read as the generic "not
rejected" list-exclusion phrase it recognises everywhere else -- rewritten
into a register-wide "excluding rejected" listing, discarding "it" (and the
one application it pointed at) entirely. Fixed by skipping `neg_scope`
whenever the carried context is a SINGLE application (`ENTITY_APPLICATION`),
the same way the pre-existing field-visit (`about_visit`) guard already
stands aside for its own domain.

D4 -- `chatbot._load_followup_context()`'s ordinal-lookback logic (used so
"what about the 2nd one?" after a single-application answer can still mean
"row 2 of the list before it") treated ANY ordinal word as a reason to walk
back to the nearest ENTITY_APPLICATION_LIST context -- including a visit-
specific ordinal ("the second OVERDUE VISIT"), which is a position in the
field-visit QUEUE, a different list entirely from whatever unrelated
application listing happened to sit in the lookback window. "Show the
survey and subdivision numbers of the second overdue visit." right after an
unrelated register listing silently picked the 2nd row of THAT listing --
a non-ISD file that can never have had a field visit at all -- instead of
falling through to `_maybe_resolve_fresh_visit_ordinal`, which queries the
real visit plan. Fixed by requiring a visit-ordinal phrase to only match a
context whose own filters say `about_visit`; every non-visit candidate (and
the final `parsed[0]` fallback) is skipped for that phrasing, standing aside
entirely rather than guessing.

python test_sequence_d_context.py (needs the DB; no LLM -- every path
exercised here is deterministic by design).
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

OFFICER_EMAIL = "marun_chn@sis.tn.gov.in"

_results: list[tuple[str, bool, str]] = []


def check(name, ok, detail=""):
    _results.append((name, bool(ok), detail))
    print(("ok  " if ok else "FAIL") + f" {name}" + ("" if ok else f" -- {detail}"))


def strip_html(s, n=400):
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
    # D1
    check("D1: 'Show rejected applications in ward 005.' is NOT read as a widen-continuation",
          chatbot._resolve_rejected_widen("Show rejected applications in ward 005.", []) is None)
    check("D1 regression guard: 'include rejected too' still widens",
          chatbot._resolve_rejected_widen("include rejected too", [])
          == "all pending approved rejected applications")
    check("D1 regression guard: a bare 'show rejected applications' (no scope of its own) still widens",
          chatbot._resolve_rejected_widen("show rejected applications", [])
          == "all pending approved rejected applications")

    # D3
    single_ctx = fctx.FollowupContext(entity=fctx.ENTITY_APPLICATION, application_numbers=["2026/0154/02/012547"],
                                      filters={}, query_type="Application Status", intent="application_status")
    list_ctx = fctx.FollowupContext(entity=fctx.ENTITY_APPLICATION_LIST,
                                    application_numbers=["2026/0154/02/012547", "2026/0154/02/012548"],
                                    filters={}, query_type="Applications", intent="all_service_code_applications")
    check("D3: resolve() still treats a singular context as singular for a negated status question",
          fctx.resolve("Is it not rejected?", single_ctx, "en").kind == fctx.FOLLOWUP_SINGULAR)
    check("D3 regression guard: a genuine list-negation ('not rejected') over a LIST still refines it",
          fctx.classify("not rejected") == fctx.FOLLOWUP_LIST_REFINE)


async def live_main():
    ctx = await get_ctx()

    # ---- D1 + D2: filter restoration must not cross a genuine topic change ----
    sid, hist = str(uuid.uuid4()), []
    r1 = await turn_as_task(
        ctx, "Show pending applications in ward 001 with application number, status, "
             "survey number, and subdivision number.", hist, sid)
    m1 = re.search(r"Found\D*(\d+)\D*pending application", r1)
    orig_total = int(m1.group(1)) if m1 else None
    await turn_as_task(ctx, "Show only the first five rows.", hist, sid)
    await turn_as_task(ctx, "Exclude the second row.", hist, sid)
    await turn_as_task(ctx, "Show the full table again.", hist, sid)
    r5 = await turn_as_task(ctx, "Show rejected applications in ward 005.", hist, sid)
    check("D1 live: 'show rejected applications in ward 005' is scoped to ward 005, "
          "not the previous ward-001 chain's filter",
          "Ward 005" in r5, strip_html(r5))
    m5 = re.search(r"Found\D*(\d+)\D*rejected application", r5)
    ward005_total = int(m5.group(1)) if m5 else None
    await turn_as_task(ctx, "How many applications were submitted in 2026?", hist, sid)
    r7 = await turn_as_task(ctx, "Restore the original filter.", hist, sid)
    check("D2 live: 'restore the original filter' recovers the CURRENT (ward-005) chain's "
          "own original scope, not the obsolete ward-001 one -- counts must differ from the "
          "ward-001 original unless they coincide by chance",
          "Ward 005" in r7 and (ward005_total is None or
           bool(re.search(rf"Found\D*{ward005_total}\D*rejected application", r7))),
          f"orig(ward001)={orig_total} ward005={ward005_total} restored={strip_html(r7)[:200]}")

    # ---- D3: a singular yes/no status question after a single-application answer ----
    sid2, hist2 = str(uuid.uuid4()), []
    await turn_as_task(ctx, "Show full application details for 2026/0154/02/012547.", hist2, sid2)
    r_status = await turn_as_task(ctx, "Is it not rejected?", hist2, sid2)
    check("D3 live: 'Is it not rejected?' answers about THAT ONE application, "
          "never a register-wide re-listing",
          "2026/0154/02/012547" in r_status and "across every service code" not in r_status,
          strip_html(r_status))

    # ---- D4: a visit-specific ordinal must never resolve against an unrelated application list ----
    sid3, hist3 = str(uuid.uuid4()), []
    await turn_as_task(ctx, "Show applications in ward 001.", hist3, sid3)
    await turn_as_task(ctx, "Are there any rejected applications?", hist3, sid3)
    r_visit = await turn_as_task(
        ctx, "Show the survey and subdivision numbers of the second overdue visit.", hist3, sid3)
    check("D4 live: a visit ordinal after an unrelated application listing resolves against "
          "the REAL field-visit plan (always an ISD/0154 file), never a row of that listing",
          "2026/0154/" in r_visit, strip_html(r_visit))
    # ground truth: re-derive which application IS the real 2nd overdue, independently
    async with AsyncSessionLocal() as db:
        plan = await chatbot._fetch_visit_plan(
            db, "second overdue visit", ctx, ward_number=None, block_number=None)
    overdue = plan.get("overdue") or []
    check("D4 live setup: the officer genuinely has at least 2 overdue ISD visits to test against",
          len(overdue) >= 2, f"{len(overdue)} overdue")
    if len(overdue) >= 2:
        expected = str(overdue[1].get("application_number") or "").upper()
        check("D4 live: the resolved application is EXACTLY the 2nd overdue visit, "
              "re-derived independently from the live DB (never hardcoded)",
              expected in r_visit.upper(), f"expected={expected} got={strip_html(r_visit)[:150]}")

    # Same check, fresh session (no prior turns at all) -- must resolve identically
    sid4, hist4 = str(uuid.uuid4()), []
    r_fresh = await turn_as_task(
        ctx, "Show the survey and subdivision numbers of the second overdue visit.", hist4, sid4)
    check("D4 live: a fresh session resolves the SAME visit ordinal the same way",
          "2026/0154/" in r_fresh, strip_html(r_fresh))

    # ---- Streaming parity for D3/D4 ----
    sid5, hist5 = str(uuid.uuid4()), []
    await turn_as_task(ctx, "Show full application details for 2026/0154/02/012547.", hist5, sid5, stream=True)
    rs = await turn_as_task(ctx, "Is it not rejected?", hist5, sid5, stream=True)
    check("Streaming: D3 also answers about the one application, not a re-listing",
          "2026/0154/02/012547" in rs and "across every service code" not in rs, strip_html(rs))


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
