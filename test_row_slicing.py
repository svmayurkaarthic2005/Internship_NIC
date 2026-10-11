"""Regression tests for row-slicing, ordinal/field resolution, and filter
restoration over the register-wide listing -- the defects Sequence C's live
run surfaced AFTER the six table-op fixes in test_sequence_c_table_ops.py.

Each defect was reproduced live against the real DB before being fixed.

R1 -- "Show only the first five rows." (no "details" word) was rewritten
into `chatbot._multi_details_message()` ("show details for A and B and C
and D and E"), which renders one FULL per-application card per number
through `application_status` -- and for a non-ISD/NISD number (`applications`
only ever admits 0153/0154), that handler cannot find it at all, so only the
one number that happened to resolve rendered anything. Root cause:
`followup_context.resolve()`'s `slice_pick` branch always set
`full_details=True` regardless of whether the message asked for "details" at
all. Fixed by checking `wants_full_details()`; a bare row-range request now
returns a plain REFINE resolution, rendered as a table slice through the
SAME `_scoped_list_answer` path "remove row 2" / "only NISD" already use.

R2 -- "What is the subdivision number in the second row?" (ordinal + field in
one message) always fell to `ordinal_pick`'s plain FOLLOWUP_SINGULAR branch,
which rewrites the message into "<question> <application number>" and lets
the GENERIC single-application handler answer it -- for ISD/NISD that often
has a per-field handler, but most other service codes do not, and the
generic fallback rendered the WHOLE details card for a question that named
one field. Fixed with a new `fields_for_each()` (the multi-field,
no-trigger-required counterpart of `field_for_each`) checked ahead of the
plain ordinal branch: an ordinal PLUS a recognised field now resolves as a
per-row projection scoped to that one picked row (`per_row_field`), reusing
`_project_field_answer` -- the same renderer "their wards" already uses for
every carried row, just scoped to one.

R3 -- Fixing R2 by narrowing `application_numbers` to the one picked row had
a side effect: the context SAVED for the next turn also narrowed to that one
row, so "exclude the second row" right after a field peek operated on a
1-row list instead of the table the row was picked from. Fixed with a new
`Resolution.row_peek` flag, set only by the ordinal+field branch, which
`chatbot._followup_out_context` reads to keep the PRIOR carried list
untouched for a peek instead of replacing it with the queried single row.

R4 -- "Show only application number and subdivision number." (and any
column-list naming "application number") silently reset a 129-row, ward-
scoped, register-wide listing down to a stale 17-row unscoped ISD/NISD
queue. Root cause: `followup_context._OWN_SUBJECT_RE` (and its fuzzy-typo
twin in `_has_own_subject`) read the bare word "application" as naming a
FRESH subject -- true for "how many applications do I have", false for
"application number", which names the ROW-KEY COLUMN of a table already on
screen. Fixed with a negative-lookahead exemption (regex) and a token-
adjacency check (fuzzy path) for singular "application" immediately followed
by "no"/"number"; "applications" (plural) is untouched.

R5 -- "Restore the original filter." fell through every follow-up layer (it
names no field/row/column word) to the LLM/agent fallback, which, having
nothing to look a "filter" up in, improvised an unrelated jurisdiction
refusal from stale ward numbers in chat_history. Fixed with a
`base_query_filters` snapshot taken once per register-listing CHAIN
(`chatbot._register_listing`, carried forward through every later re-listing
in the same chain including row-slicing/exclusion detours via a backward
scan of `chat_messages.structured_data`,`chatbot._find_base_query_filters`)
and a deterministic `_restore_original_filter_answer` that re-runs the
original query from it -- or asks, honestly, when no snapshot is on record,
rather than inventing one.

python test_row_slicing.py (needs the DB; no LLM -- every path exercised
here is deterministic by design).
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
    # R4 -- own-subject exemption
    check("R4: 'application number' no longer trips the own-subject gate",
          not fctx._has_own_subject(fctx._correct_typos(
              "Show only application number and subdivision number.").lower()))
    check("R4: classify() now reads it as a follow-up",
          fctx.classify("Show only application number and subdivision number.") != fctx.FOLLOWUP_NONE)
    check("R4 regression guard: 'how many applications do I have' still a fresh question",
          fctx.classify("how many applications do I have") == fctx.FOLLOWUP_NONE)
    check("R4 regression guard: 'show my pending applications' still a fresh question",
          fctx.classify("show my pending applications") == fctx.FOLLOWUP_NONE)
    check("R4 regression guard: an explicit application number is still a fresh question",
          fctx.classify("show application 2026/0154/02/015386") == fctx.FOLLOWUP_NONE)

    # R2 -- fields_for_each
    check("R2: fields_for_each finds a single field with no trigger word",
          fctx.fields_for_each("What is the subdivision number in the second row?") == ["subdivisions"])
    check("R2: fields_for_each finds multiple fields in the order named",
          fctx.fields_for_each("Give me the survey number and subdivision number of the third row.")
          == ["survey_no", "subdivisions"])
    check("R2 regression guard: fields_for_each returns None for a field-less sentence",
          fctx.fields_for_each("Show the third row now.") is None)

    # R1 -- slice_pick no longer forces full_details
    nums = [f"2026/0154/02/{i:06d}" for i in range(1, 11)]
    ctx = fctx.FollowupContext(entity=fctx.ENTITY_APPLICATION_LIST, application_numbers=nums,
                               filters={}, query_type="Applications", intent="all_service_code_applications")
    res = fctx.resolve("Show only the first five rows.", ctx, "en")
    check("R1: a bare row-range request resolves WITHOUT full_details",
          res.resolved and not res.full_details and res.kind == fctx.FOLLOWUP_LIST_REFINE
          and res.application_numbers == nums[:5],
          f"full_details={res.full_details} kind={res.kind} nums={res.application_numbers}")
    res2 = fctx.resolve("Give me full details of the first five rows.", ctx, "en")
    check("R1 regression guard: an EXPLICIT details request still sets full_details",
          res2.resolved and res2.full_details and res2.application_numbers == nums[:5],
          f"full_details={res2.full_details}")
    res3 = fctx.resolve("Show rows 4 to 8.", ctx, "en")
    check("R1: an explicit row range ('rows 4 to 8') resolves to exactly those rows",
          res3.resolved and res3.application_numbers == nums[3:8],
          f"nums={res3.application_numbers}")

    # R2 -- ordinal + field over a list resolves as a scoped per-row projection,
    # not a promotion to the single-application details card
    res4 = fctx.resolve("What is the subdivision number in the second row?", ctx, "en")
    check("R2: ordinal + field resolves to ONE row with per_row_field set (not full_details)",
          res4.resolved and res4.per_row_field == ["subdivisions"]
          and res4.application_numbers == [nums[1]] and not res4.full_details,
          f"per_row_field={res4.per_row_field} nums={res4.application_numbers}")
    check("R3: the ordinal+field resolution is flagged row_peek (must not narrow the carried list)",
          res4.row_peek is True)
    # Product decision (SIS Chatbot -- Final Context-Isolation and Table-State
    # Consistency Audit, part 3): a bare ordinal with NO field named and no
    # "full details" word renders a ONE-ROW TABLE in the currently displayed
    # columns, not a promotion to the full application-details card -- the
    # same `per_row_field` + `row_peek` shape as an ordinal+field question,
    # just defaulting to every current column instead of a named subset.
    res5 = fctx.resolve("Show the third row now.", ctx, "en")
    check("R2: a bare ordinal (no field, no 'full details') resolves as a one-row "
          "table in the DEFAULT listing columns, not the full details card",
          res5.resolved and res5.kind == fctx.FOLLOWUP_LIST_FIELD
          and res5.per_row_field == list(fctx._STANDARD_LISTING_COLUMNS)
          and res5.application_numbers == [nums[2]] and res5.row_peek,
          f"kind={res5.kind} per_row_field={res5.per_row_field} "
          f"nums={res5.application_numbers} row_peek={res5.row_peek}")
    res5b = fctx.resolve("Show the full details of the third row.", ctx, "en")
    check("R2 regression guard: an EXPLICIT 'full details' ordinal request still "
          "gets the full single-application card",
          res5b.resolved and res5b.kind == fctx.FOLLOWUP_SINGULAR
          and res5b.application_number == nums[2] and not res5b.row_peek,
          f"kind={res5b.kind} app={res5b.application_number} row_peek={res5b.row_peek}")

    # Out-of-range ordinal -- never clamped to another row
    res6 = fctx.resolve("What is the subdivision number in the 99th row?", ctx, "en")
    check("Out-of-range ordinal + field is refused (ambiguous), never silently clamped",
          res6.ambiguous and not res6.resolved, f"resolved={res6.resolved}")

    # Empty list -- resolve() must stand aside, not crash or invent a row
    empty_ctx = fctx.FollowupContext(entity=fctx.ENTITY_APPLICATION_LIST, application_numbers=[],
                                     filters={}, query_type="Applications", intent="all_service_code_applications")
    res7 = fctx.resolve("Show only the first five rows.", empty_ctx, "en")
    check("Empty carried list: a row-range request does not crash and claims nothing to slice",
          not (res7.resolved and res7.application_numbers), f"{res7.resolved} {res7.application_numbers}")

    # R4 -- the register-wide routing fix this depends on
    col_msg = ("Show applications in ward 001 as a table with application number, status, "
               "survey number, subdivision number, and submission date.")
    check("R4 (routing): naming survey number as a column no longer forces the narrow ISD/NISD queue",
          rag.parse_intent(col_msg) == "all_service_code_applications")

    # R5 -- restore-filter phrase recognition
    check("R5: '_RESTORE_FILTER_RE' matches the exact phrase",
          bool(chatbot._RESTORE_FILTER_RE.search("Restore the original filter.")))
    check("R5 regression guard: an unrelated sentence does not match",
          not chatbot._RESTORE_FILTER_RE.search("Show the original application."))


async def live_main():
    ctx = await get_ctx()

    # ---- R1/R2/R3/R4 end-to-end over a real register-wide listing ----
    sid, hist = str(uuid.uuid4()), []
    r1 = await turn_as_task(
        ctx, "Show applications in ward 001 as a table with application number, status, "
             "survey number, subdivision number, and submission date.", hist, sid)
    check("live: base listing renders as ONE table (not a numbered multi-answer split)",
          "<strong>1.</strong>" not in r1 and "1. applications" not in r1.lower(), strip_html(r1))
    m = re.search(r"Found\D*(\d+)\D*pending application", r1)
    base_total = int(m.group(1)) if m else None
    check("live: base listing is ward-scoped and register-wide (not the narrow ISD/NISD queue)",
          base_total is not None and base_total < 50 and "Ward 001" in r1 and "across every service code" in r1,
          f"base_total={base_total} {strip_html(r1)[:150]}")

    r2 = await turn_as_task(ctx, "Show applications that are not rejected.", hist, sid)
    check("live: negation follow-up keeps the Ward 001 scope (register listing, not narrowed)",
          "Ward 001" in r2, strip_html(r2))

    r3 = await turn_as_task(ctx, "Show only the first five rows.", hist, sid)
    check("R1 live: 'first five rows' renders a TABLE slice, never the mutation refusal "
          "or a bare single-application card",
          "I can only read the records" not in r3 and "📋" not in r3, strip_html(r3))

    r4 = await turn_as_task(ctx, "What is the subdivision number in the second row?", hist, sid)
    check("R2 live: an ordinal+field question renders a ONE-ROW field table, not a full details card",
          "📋" not in r4 and "FIELD" not in r4 and "Sub-Divisions" in r4 or "Sub-Division" in r4,
          strip_html(r4))

    r5 = await turn_as_task(ctx, "Exclude the second row from the current result.", hist, sid)
    check("R3 live: 'exclude the second row' right after a field peek operates on the ORIGINAL "
          "5-row slice (4 left), not a 1-row stale list (0 left / out-of-range)",
          "Removed 1, 4 left" in r5, strip_html(r5))

    r6 = await turn_as_task(ctx, "Show the third row now.", hist, sid)
    check("live: 'show the third row' after the exclusion resolves against the CURRENT "
          "4-row result, not a stale/empty one",
          "there is no number" not in r6.lower(), strip_html(r6))

    # ---- R4: the 152-vs-17 regression, derived from the live DB, never hardcoded ----
    sid2, hist2 = str(uuid.uuid4()), []
    await turn_as_task(ctx, "Show applications in ward 001.", hist2, sid2)
    r_incl = await turn_as_task(ctx, "Include rejected applications too.", hist2, sid2)
    m2 = re.search(r"Found\D*(\d+)\D*application", r_incl)
    full_total = int(m2.group(1)) if m2 else None
    check("R4 live setup: the full (rejected-included) ward-wide total was captured",
          full_total is not None and full_total > 0, strip_html(r_incl)[:150])
    r_proj = await turn_as_task(ctx, "Show only application number and subdivision number.", hist2, sid2)
    m3 = re.search(r"Found\s*(\d+)\s*application", r_proj)
    proj_total = int(m3.group(1)) if m3 else None
    check("R4 live: a column projection ('application number and subdivision number') preserves "
          "the exact record count and membership -- it must never shrink to an unrelated, "
          "unscoped ISD/NISD queue",
          proj_total == full_total, f"before={full_total} after={proj_total}")

    # ---- Streaming parity for R1/R2 ----
    sid3, hist3 = str(uuid.uuid4()), []
    await turn_as_task(ctx, "Show applications in ward 001.", hist3, sid3, stream=True)
    rs1 = await turn_as_task(ctx, "Show only the first five rows.", hist3, sid3, stream=True)
    check("Streaming: 'first five rows' also renders a table slice, not a mutation refusal",
          "I can only read the records" not in rs1, strip_html(rs1))
    rs2 = await turn_as_task(ctx, "What is the subdivision number in the second row?", hist3, sid3, stream=True)
    check("Streaming: ordinal+field also renders a one-row field answer, not a full card",
          "📋" not in rs2, strip_html(rs2))

    # ---- R5: filter restoration, live ----
    sid4, hist4 = str(uuid.uuid4()), []
    r_base = await turn_as_task(
        ctx, "Show applications in ward 001 as a table with application number, status, "
             "survey number, subdivision number, and submission date.", hist4, sid4)
    m4 = re.search(r"Found\D*(\d+)\D*pending application", r_base)
    original_total = int(m4.group(1)) if m4 else None
    await turn_as_task(ctx, "Do not exclude rejected applications.", hist4, sid4)
    await turn_as_task(ctx, "Show only the first five rows.", hist4, sid4)
    await turn_as_task(ctx, "Exclude the second row from the current result.", hist4, sid4)
    await turn_as_task(ctx, "Show the third row now.", hist4, sid4)  # detour: no field, loses base_query_filters locally
    await turn_as_task(ctx, "Include rejected applications too.", hist4, sid4)
    r_restored = await turn_as_task(ctx, "Restore the original filter.", hist4, sid4)
    m5 = re.search(r"Found\D*(\d+)\D*pending application", r_restored)
    restored_total = int(m5.group(1)) if m5 else None
    check("R5 live: 'restore the original filter' recovers the TRUE original scope, even after "
          "a row-exclusion chain and a field-less single-row detour that locally lost the snapshot",
          restored_total is not None and restored_total == original_total
          and "Ward 001" in r_restored,
          f"original={original_total} restored={restored_total} {strip_html(r_restored)[:150]}")
    check("R5 live: the restore answer says what it did, plainly",
          "restored" in r_restored.lower(), strip_html(r_restored)[:150])

    # No snapshot on record at all -- must ask, never hallucinate
    sid5, hist5 = str(uuid.uuid4()), []
    await turn_as_task(ctx, "What does service code 0153 represent?", hist5, sid5)
    r_norestore = await turn_as_task(ctx, "Restore the original filter.", hist5, sid5)
    check("R5 live: with no filter snapshot on record, the answer asks -- it never invents "
          "a filter or hallucinates an unrelated refusal",
          "no original filter on record" in r_norestore.lower(), strip_html(r_norestore)[:200])


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
