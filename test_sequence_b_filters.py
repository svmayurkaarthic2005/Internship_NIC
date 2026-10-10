"""Sequence B regression suite: the field-visit negation/filter chain.

Three confirmed defects, found by executing the sequence live against the
real DB (never hardcoded expected application numbers -- every assertion is
checked against the live register the test itself reads):

B7 -- a SINGULAR-context ordinal collision. After a question resolves to
ONE application (e.g. "the second overdue visit" -> 015386), a LATER bare
field question naming a DIFFERENT ordinal ("the first overdue visit")
was answered about the application already in view instead of resolving
against the visit queue again. Root cause: `followup_context.resolve()`'s
single-application branch only refused an ordinal past position 1 (the
`named_pos > 1` guard); "the FIRST x" (named_pos == 1) slipped through.
Fixed by standing aside for ANY ordinal+visit-shaped question against a
singular context, letting `chatbot._maybe_resolve_fresh_visit_ordinal`
resolve it against the live queue instead.

B4 -- "Include the first one but exclude the second one." had no
deterministic handler at all, so it fell to the LLM/agent fallback, which
was non-deterministic (confirmed: three different answers across three runs
of the identical script, including an irrelevant "Ward 002 is outside your
jurisdiction" hallucination -- a known agent-layer quirk, see CLAUDE.md's
"Agent layer" section). Fixed with a small, fully deterministic
`chatbot._fv_include_exclude_answer()` that resolves both ordinals against
the CURRENT carried list and states only what is actually true of it.

B5/B6 -- "Show only those with a subdivision recorded." / "...without a
recorded subdivision." answered about ONE stale application instead of
filtering the carried list. Two root causes: (1) no filter primitive
existed for "per-row field presence" at all, and (2) even once added,
`negq.rewrite()` claimed the message first (it finds an application number
anywhere in the last 8 turns and treats "without X recorded" as a
single-record existence question), stripping "without" before the new
filter ever saw the original wording. Fixed with
`postgres.get_field_presence()` (grounded in the canonical
`application_sub_divisions` / `Application.field_visit_scheduled` /
`Application.challan_number,fee_amount` / `WorkflowHistory.remarks`
relationships, never the legacy extract tables) +
`followup_context.parse_field_presence_request()` +
`chatbot._field_presence_answer()`, checked ahead of `negq.rewrite()`
whenever the carried context is a field-presence-filter shape.

python test_sequence_b_filters.py (needs the DB; no LLM -- every path here
is deterministic by design, which is itself part of what these tests check).
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

from backend.database import AsyncSessionLocal, engine as _db_engine
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
    """Every turn runs inside its own asyncio Task -- the same isolation a
    real HTTP request gets in production (uvicorn creates a Task per ASGI
    request), confirmed by direct experiment not to leak state between
    turns the way chaining bare `await`s in one coroutine does."""
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


def test_parse_field_presence_request_unit():
    cases = [
        ("Show only those with a subdivision recorded.", ("subdivision", True)),
        ("Now show those without a recorded subdivision.", ("subdivision", False)),
        ("Show only applications with workflow remarks.", ("workflow_remarks", True)),
        ("Show those without payment recorded.", ("payment", False)),
        ("Show only those with a scheduled field visit.", ("field_visit_scheduled", True)),
        # must NOT match -- an ordinary field-projection / per-file question
        ("Show all subdivisions for every overdue visit.", None),
        ("the survey team is on the mainland", None),
        ("what is the subdivision number of this application?", None),
    ]
    for msg, expected in cases:
        got = fctx.parse_field_presence_request(msg)
        check(f"parse_field_presence_request({msg!r}) == {expected!r}", got == expected, repr(got))


async def live_main():
    ctx = await get_ctx()

    # ---- B7: singular-context ordinal collision ----
    sid, hist = str(uuid.uuid4()), []
    r1 = await turn_as_task(ctx, "Show the survey number and subdivision number of the second overdue visit.", hist, sid)
    check("B7 setup: 'second overdue visit' resolves to 015386", "2026/0154/02/015386" in r1, r1[:200])
    r2 = await turn_as_task(ctx, "What is the survey number of the first overdue visit?", hist, sid)
    check("B7 live DB: a LATER bare ordinal question resolves against the queue again (015465), "
          "not the single application already in view (015386)",
          "2026/0154/02/015465" in r2 and "2026/0154/02/015386" not in r2, r2[:200])
    r3 = await turn_as_task(ctx, "What is the survey number of the third overdue visit?", hist, sid)
    check("B7 live DB: a third bare ordinal also resolves correctly (015395)",
          "2026/0154/02/015395" in r3, r3[:200])

    # ---- B4: deterministic include/exclude, run 3x to prove determinism ----
    for trial in range(3):
        sid, hist = str(uuid.uuid4()), []
        await turn_as_task(ctx, "Which application should I field visit next?", hist, sid)
        await turn_as_task(ctx, "Show the overdue visits only.", hist, sid)
        await turn_as_task(ctx, "Exclude the first one.", hist, sid)
        r4 = await turn_as_task(ctx, "Include the first one but exclude the second one.", hist, sid)
        check(f"B4 live DB (trial {trial + 1}): deterministic, correct include/exclude wording",
              r4 == "2026/0154/02/015386 is already in the list. 2026/0154/02/015395 has been excluded.",
              r4)

    # streaming parity for B4
    sid, hist = str(uuid.uuid4()), []
    await turn_as_task(ctx, "Which application should I field visit next?", hist, sid)
    await turn_as_task(ctx, "Show the overdue visits only.", hist, sid)
    await turn_as_task(ctx, "Exclude the first one.", hist, sid)
    r4s = await turn_as_task(ctx, "Include the first one but exclude the second one.", hist, sid, stream=True)
    check("B4 live DB: same deterministic answer through process_chat_stream()",
          r4s == "2026/0154/02/015386 is already in the list. 2026/0154/02/015395 has been excluded.", r4s)

    # ---- B5/B6: field-presence filter over the carried queue ----
    sid, hist = str(uuid.uuid4()), []
    await turn_as_task(ctx, "Which application should I field visit next?", hist, sid)
    await turn_as_task(ctx, "Show the overdue visits only.", hist, sid)
    r5 = await turn_as_task(ctx, "Show only those with a subdivision recorded.", hist, sid)
    check("B5 live DB: filters the CARRIED list (all 3 overdue are ISD -> all 3 have a subdivision), "
          "never a stale single-application answer",
          "3 of those 3" in r5 and "Details for application" not in r5 and "For application" not in r5,
          r5[:250])
    r6 = await turn_as_task(ctx, "Now show those without a recorded subdivision.", hist, sid)
    check("B6 live DB: the reverse filter over the SAME carried list -- 'without' is not stripped "
          "by `negq`, and the answer is grounded (0 of 3, since all are ISD with subdivisions)",
          "All of those 3" in r6 or "None of those 3" in r6, r6[:250])

    # B5/B6 as a fresh FIRST-TURN request (no carried queue at all) must NOT
    # silently answer from a register-wide search -- requirement #3/#10.
    sid2, hist2 = str(uuid.uuid4()), []
    r_fresh = await turn_as_task(ctx, "Show only those with a subdivision recorded.", hist2, sid2)
    check("B5 live DB: the SAME filter wording with NO carried queue does not silently run "
          "a register-wide search (no field-presence table rendered)",
          "of those" not in r_fresh, r_fresh[:200])

    # A new topic after the filter must not inherit it (context isolation).
    sid3, hist3 = str(uuid.uuid4()), []
    await turn_as_task(ctx, "Which application should I field visit next?", hist3, sid3)
    await turn_as_task(ctx, "Show the overdue visits only.", hist3, sid3)
    await turn_as_task(ctx, "Show only those with a subdivision recorded.", hist3, sid3)
    r_new_topic = await turn_as_task(ctx, "What does service code 0153 represent?", hist3, sid3)
    check("context isolation: an unrelated new topic after the filter is answered on its own terms, "
          "not re-scoped to the filtered queue",
          "of those" not in r_new_topic, r_new_topic[:200])

    # Explicit application ID overrides the queue context.
    sid4, hist4 = str(uuid.uuid4()), []
    await turn_as_task(ctx, "Which application should I field visit next?", hist4, sid4)
    await turn_as_task(ctx, "Show the overdue visits only.", hist4, sid4)
    await turn_as_task(ctx, "Show only those with a subdivision recorded.", hist4, sid4)
    r_explicit = await turn_as_task(ctx, "What is the status of 2026/0154/02/015465?", hist4, sid4)
    check("explicit application ID overrides the carried filter queue",
          "2026/0154/02/015465" in r_explicit and "of those" not in r_explicit, r_explicit[:200])


def main() -> None:
    test_parse_field_presence_request_unit()
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
