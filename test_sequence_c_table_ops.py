"""Regression tests for defects found while executing Sequence C live
(negation + table/column operations over a register-wide listing).

Each defect below was reproduced against the real DB before being fixed --
never assumed from reading the code. Six confirmed, fixed here:

C1 -- `_plan_requests()` (chatbot.py) split "show only application number
and subdivision number" -- ONE per-row column-selection request over the
carried list -- into two bogus separate "requests" ("application number" /
"subdivision number."), because it has no concept of a column list. The
second half then had no row of its own to be a fragment of and asked "which
one do you mean?" instead of adding the column. Fixed by making
`_plan_requests` stand aside whenever `followup_context.field_projections()`
already reads the whole message as one column request.

C2 -- `followup_context._PROJECT_FIELDS`'s "subdivisions" entry only listed
PLURAL needles ("sub-divisions", "subdivisions", ...), so "subdivision
number" (singular, the natural way an officer names ONE column) matched
nothing and was silently dropped from a column list. Fixed by adding the
singular needles.

C3 -- "Show the full table again." (resetting a narrowed column projection
back to the default set) matched no trigger in `field_projections()` at all
-- it names no field of its own -- so it fell through every follow-up layer
all the way to the LLM/agent fallback, which improvised an unrelated
jurisdiction refusal from stale ward numbers in chat_history. Fixed with a
`_COLUMN_RESET_RE` recognising "full/whole/complete/entire/original table",
"all columns", "reset the columns", etc., returned as the standard column
set.

C4 -- `rag._is_register_wide_listing()` read the bare word "survey" ANYWHERE
in the message as "this is a question about one specific survey, stay on
the narrow ISD/NISD queue" -- so naming "survey number" as one of several
desired COLUMNS of an otherwise register-wide listing ("... as a table with
application number, status, survey number, subdivision number, and
submission date") silently narrowed a 126-application ward-wide listing down
to the 17-row ISD/NISD-only desk queue, and dropped the ward scope with it
(the two listings inherit a carried ward through different code paths).
Fixed by recognising "survey (number)" named alongside "application number"
with no digit attached as a column name, not a per-survey question.

C5 -- `followup_context._carried_list_status()` read a query_type label like
"Applications excluding rejected" (produced by a negation follow-up, e.g.
"show applications that are not rejected") for the substring "rejected" and
carried it FORWARD as the positive status filter on the NEXT follow-up --
so "remove the status column" was silently re-queried as rejected-only and
came back with none of the 105 rows the officer could see on screen ("None
of those applications are available to read right now"). Fixed by skipping
the phrase-matching scan entirely when the label says "excluding".

C6 -- `chatbot._is_mutation_request()` refused "Add the subdivision number
after the survey number." (a column reorder/insert request) as if it were a
request to change the land record, because "subdivision" and "survey" are
both real `_MUTATION_OBJECTS` words (for an actual subdivision/survey
change) and the existing column-request carve-out (`_ADD_COLUMNS_RE`) only
recognised a plain "add X, Y" list, not "add X before/after Y". Fixed with a
dedicated `_ADD_REORDER_COLUMN_RE` carve-out, and "subdivision" was also
added to `_ADD_COLUMNS_RE` itself (it was missing from the plain-list form
too).

Also reproduces `_big_plan()`'s existing, still-correct behaviour on a
genuine bundle of several independent requests after a listing ("... how
many are CSC, how many are ISD, how many NISD, the oldest one and the
newest one") to prove the C1/C4 narrowing did not regress it -- this is the
exact case `test_big_message.py` already covers and failed during this
investigation before the guard was narrowed to require every later field to
be a bare column noun.

python test_sequence_c_table_ops.py (needs the DB; no LLM -- every path
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
from backend.services import rag
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


async def turn_as_task(ctx, msg, hist, sid):
    """Each turn in its own asyncio Task -- the same isolation a real HTTP
    request gets in production; see test_sequence_b_filters.py's docstring."""
    async def _inner():
        async with AsyncSessionLocal() as db:
            r = await chatbot.process_chat(db=db, message=msg, officer=ctx, chat_history=hist, session_id=sid)
        return (r.get("response") if isinstance(r, dict) else r) or ""
    resp = await asyncio.create_task(_inner())
    hist.append({"role": "user", "content": msg})
    hist.append({"role": "assistant", "content": resp})
    return resp


def test_unit_checks():
    # C1
    msg = "Show only application number and subdivision number."
    check("C1: _plan_requests no longer splits a column-selection request",
          chatbot._plan_requests(msg) is None, repr(chatbot._plan_requests(msg)))
    # a genuine bundle of several independent asks must still split
    bundle = ("Pls give me pending applications in ward 001, how many are CSC, "
              "how many are ISD, how many NISD, the oldest one and the newest one")
    bp = chatbot._big_plan(bundle)
    check("C1 regression guard: a real multi-request bundle still splits into 5",
          bool(bp) and len(bp) == 5, repr(bp))

    # C2
    check("C2: field_projections recognises singular 'subdivision number'",
          fctx.field_projections(msg) == ["subdivisions"], repr(fctx.field_projections(msg)))

    # C3
    check("C3: 'show the full table again' resets to the standard column set",
          fctx.field_projections("Show the full table again.") == list(fctx._STANDARD_LISTING_COLUMNS),
          repr(fctx.field_projections("Show the full table again.")))

    # C4
    col_msg = ("Show applications in ward 001 as a table with application number, status, "
               "survey number, subdivision number, and submission date.")
    check("C4: naming survey number as a column no longer forces the narrow ISD/NISD queue",
          rag.parse_intent(col_msg) == "all_service_code_applications",
          rag.parse_intent(col_msg))
    check("C4 regression guard: a real per-survey question still stays narrow",
          rag.parse_intent("status of survey 120") != "all_service_code_applications")

    # C5
    ctx_excl = fctx.FollowupContext(
        entity=fctx.ENTITY_APPLICATION_LIST, application_numbers=["X"],
        filters={}, query_type="Applications excluding rejected", intent="all_service_code_applications")
    check("C5: a query_type naming an EXCLUDED status is never read as the carried positive status",
          fctx._carried_list_status(ctx_excl) is None, repr(fctx._carried_list_status(ctx_excl)))
    ctx_plain = fctx.FollowupContext(
        entity=fctx.ENTITY_APPLICATION_LIST, application_numbers=["X"],
        filters={}, query_type="Rejected Applications", intent="pending_applications")
    check("C5 regression guard: a genuinely rejected-only listing still carries 'rejected' forward",
          fctx._carried_list_status(ctx_plain) == "rejected")

    # C6
    check("C6: 'add the subdivision number after the survey number' is not a mutation",
          not chatbot._is_mutation_request("Add the subdivision number after the survey number."))
    check("C6 regression guard: a real mutation request is still refused",
          chatbot._is_mutation_request("Approve this application"))
    check("C6 regression guard: a real subdivision-record change request is still refused",
          chatbot._is_mutation_request("Change the subdivision status to approved"))


async def live_main():
    ctx = await get_ctx()
    sid, hist = str(uuid.uuid4()), []

    r1 = await turn_as_task(
        ctx, "Show applications in ward 001 as a table with application number, status, "
             "survey number, subdivision number, and submission date.", hist, sid)
    check("C1/C4 live: turn 1 renders ONE table, not a numbered multi-answer split",
          "<strong>1.</strong>" not in r1 and "1. applications" not in r1.lower(), strip_html(r1))
    check("C4 live: turn 1 is scoped to Ward 001 across every service code, not the 17-row ISD/NISD queue",
          "Ward 001" in r1 and "across every service code" in r1, strip_html(r1))

    r2 = await turn_as_task(ctx, "Show applications that are not rejected.", hist, sid)
    check("live: the negation follow-up keeps the Ward 001 scope",
          "Ward 001" in r2, strip_html(r2))

    r3 = await turn_as_task(ctx, "Remove the status column but keep the filter.", hist, sid)
    check("C5 live: removing a column over a negated listing does not collapse to zero rows",
          "None of those applications are available" not in r3, strip_html(r3))

    r4 = await turn_as_task(ctx, "Add the subdivision number after the survey number.", hist, sid)
    check("C6 live: adding/reordering a column is never refused as a mutation",
          "I can only read the records" not in r4, strip_html(r4))


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
