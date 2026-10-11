"""Regression suite for three follow-up gaps found in the "Phase 2" audit of
the ordinal/multi-field follow-up fix (see test_ordinal_multifield_followups.py
for the original bug).

A. Ward/block-scoped ordinal selection
---------------------------------------
"Show my overdue field visits in Ward 001." then "...the second overdue
visit" picked the second row of the WHOLE jurisdiction, not the second row
of the ward the officer actually named -- the scope word was accepted by the
intent parser but never reached the query. Two separate root causes, both in
`backend/services/chatbot.py`:

1. The foreign-ward-denial check (`_foreign_geo_denial`) was gated by the
   SAME flag (`_skip_keyword_check`) that exempts field-visit intents from
   the unrelated "requested a BROADER jurisdiction level" check -- so a named
   ward/block the officer does not hold at all was silently ignored instead
   of refused, for `fv_overdue_inspections` and every other field-visit
   intent. Fixed with a narrower `_skip_foreign_check` flag that carries only
   the exemptions that are actually about avoiding a false "foreign ward"
   read (service-code names, column projections, record modules), not the
   field-visit carve-out.
2. The `fv_overdue_inspections` query itself never read a ward/block named
   in the message at all -- `get_jurisdiction_filter()` already scopes it to
   the officer's WHOLE jurisdiction, and nothing narrowed it further. Fixed
   by extracting `(ward, block)` via `_geo_scope()` and adding the matching
   `.where(Ward.ward_number == ...)` / `.where(Block.block_number == ...)`,
   the same way `get_visit_plan()` already does.

A third, independently-discovered bug surfaced while verifying the fix:
`fv_overdue_inspections`'s row dicts never set `is_overdue`, so
`followup_context.build_context()`'s generic `field_visits` splitter (which
relies on that flag to tell an overdue row from an on-schedule one) counted
ZERO of them as overdue and sliced any "second overdue visit" follow-up to
nothing. Every row this query returns is overdue by construction (the
query's own WHERE clause enforces it); fixed by setting the flag explicitly.

A fourth, also independently discovered: a carried list of exactly ONE row
took a different code path in `followup_context.resolve()` (the `len(numbers)
== 1` branch) that never considered per-row field projection at all, so
"application number of the first overdue visit" over a ONE-row ward/block-
narrowed list hit the same generic-intent-routing trap the original bug did.
Fixed by widening the `about_visit` per-row-field check (added for the
original bug) to this branch too.

B. Multiple explicit ordinal positions in one request
-------------------------------------------------------
`ordinal_index()` -- built for ONE position -- returns only the FIRST
ordinal word it finds, so "the first, second and third overdue visits" /
"the first and fourth pending visits" silently answered about row 1 alone,
with no sign the rest had been dropped. Fixed with
`followup_context.multi_ordinal_pick()` (extracts every distinct position
named, in the order named) wired into `classify()` /`resolve()`, with an
honest "there is no number N" clarification when any named position is out
of range -- never a silent partial answer.

A second root cause surfaced verifying the fix: `chatbot._fv_followup()`
unconditionally re-derived a FRESH, criteria-filtered field-visits query
from the fragment's own words for any `FOLLOWUP_LIST_REFINE`/`LIST_FIELD`
resolution over a visit-scoped context -- which has no concept of "position
1 and 4" and would have silently answered with every row matching no
criteria (i.e. the whole table), discarding the exact rows `resolve()` had
already picked. Fixed by standing aside when the message names explicit
ordinal positions, letting the generic application-list renderer use
`resolution.application_numbers` directly.

C. Exclusion clauses treated as filters, not separate questions
------------------------------------------------------------------
"Show the second overdue visit, excluding rescheduled visits." was split by
BOTH `chatbot._big_plan()` and `chatbot._plan_requests()` into two unrelated
"questions" ("Show the second overdue visit" / "excluding rescheduled
visits."), because "rescheduled" was not in `_NEG_TERM_RE`'s exclusion
vocabulary (completed/unscheduled/scheduled/overdue only) and the field-visit
status vocabulary neither splitter's `neg_scope`/`_parse_negation` guard knew
about ever recognised it as a filter at all. Fixed by:
  * adding "rescheduled" as its own `_FV_CRITERIA` / `_fv_row_matches` /
    `_FV_LABELS` / `_NEG_TERM_RE` / `_VISIT_KEY` entry (distinct from
    "scheduled", which already covers both for other purposes), and
  * widening both splitters' negation guard to also stand aside when
    `_parse_negation(text)[1]` (the visit-status exclusion set) is non-empty,
    not only when the application-level `neg_scope.parse()` recognises
    something.

Known, NOT fixed here (reported, not silently passed over):
  * "Show the three oldest eligible visits, excluding completed ones." --
    "N oldest" is a superlative+count shape (`_FV_SUPERLATIVE_RE` +
    `_FV_COUNT_RE`), not an ordinal pick; no existing mechanism sorts the
    carried list by age and takes the top N, so this still falls through to
    the generic unfiltered listing.
  * "...but exclude records without a scheduled date." -- the periphrastic
    "without a scheduled date" is not recognised as a synonym for the
    "unscheduled" status; only the literal status words are.
  * "For each of the second and fourth visits, give the application number
    and scheduled date." -- `_compound_split`'s "for each of X, Y" shape
    still splits this into two unrelated fragments before the multi-ordinal
    fix ever sees it.

Live-DB tests only; no LLM anywhere. `python test_ordinal_scope_and_exclusion.py`
"""
from __future__ import annotations

import asyncio
import re
import sys
import uuid

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

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


def check(name: str, condition: bool, detail: str = "") -> None:
    _results.append((name, condition, detail))
    print(f"{'ok ' if condition else 'FAIL'} {name}" + (f" -- {detail}" if detail and not condition else ""))


def strip_html(s: str) -> str:
    return re.sub(r"<[^>]+>", " ", s or "")


# ── Unit-level tests (no DB) ─────────────────────────────────────────────

def test_multi_ordinal_pick_unit():
    picked, idx = fctx.multi_ordinal_pick("Show the first, second and third overdue visits.", ["A", "B", "C"])
    check("B. multi_ordinal_pick extracts all three positions in order", (picked, idx) == (["A", "B", "C"], [1, 2, 3]))
    picked, idx = fctx.multi_ordinal_pick("Give the first and fourth pending visits.", ["A", "B", "C"])
    check("B. multi_ordinal_pick keeps only the in-range ones but reports every position asked for",
          picked == ["A"] and idx == [1, 4])
    check("B. mentions_multi_ordinal is False for a single ordinal",
          not fctx.mentions_multi_ordinal("the second one"))
    check("B. mentions_multi_ordinal is True for two or more",
          fctx.mentions_multi_ordinal("the first and third"))


def test_rescheduled_criterion_unit():
    check("C. 'rescheduled' is its own _fv_row_matches criterion, distinct from 'scheduled'",
          chatbot._fv_row_matches({"status": "rescheduled"}, "rescheduled") is True
          and chatbot._fv_row_matches({"status": "scheduled"}, "rescheduled") is False)
    check("C. 'scheduled' criterion still covers both (unchanged, backward compatible)",
          chatbot._fv_row_matches({"status": "rescheduled"}, "scheduled") is True
          and chatbot._fv_row_matches({"status": "scheduled"}, "scheduled") is True)
    _, visit, _ = chatbot._parse_negation("excluding rescheduled visits")
    check("C. _parse_negation recognises 'rescheduled' as an excludable visit status", visit == {"rescheduled"})


def test_splitters_stand_aside_for_visit_exclusion():
    msg = "Show the second overdue visit, excluding rescheduled visits."
    check("C. _big_plan does not split an exclusion clause into a separate question",
          chatbot._big_plan(msg) is None)
    check("C. _plan_requests does not split it either",
          chatbot._plan_requests(msg) is None)


# ── Live-DB tests (real officer, real jurisdiction, both chat paths) ──────

async def officer_context(db, officer) -> OfficerContext:
    jur = await get_officer_jurisdiction_ids(officer.id, db)
    ids = (jur["district_ids"] + jur["taluk_ids"] + jur["town_ids"] + jur["ward_ids"] + jur["block_ids"])
    return OfficerContext(officer_id=officer.id, employee_id=officer.employee_id, name=officer.name,
        email=officer.email, designation=officer.designation,
        jurisdiction_type=jur["jurisdiction_type"], jurisdiction_name=jur["jurisdiction_name"],
        jurisdiction_ids=[i for i in ids if i])


async def get_ctx() -> OfficerContext:
    async with AsyncSessionLocal() as db:
        res = await db.execute(select(SISOfficer).where(SISOfficer.email == OFFICER_EMAIL))
        return await officer_context(db, res.scalar_one())


async def turn(ctx, message, hist=None, sid=None, stream=False):
    sid = sid or str(uuid.uuid4())
    hist = hist if hist is not None else []
    if stream:
        chunks = []
        async with AsyncSessionLocal() as db:
            async for ev in chatbot.process_chat_stream(
                    db=db, message=message, officer=ctx, chat_history=hist, session_id=sid):
                chunks.append(ev)
        import json as _json
        resp = ""
        for c in chunks:
            s = c.decode() if isinstance(c, bytes) else c
            if isinstance(s, str) and s.startswith("data: "):
                try:
                    resp += _json.loads(s[6:]).get("content", "")
                except Exception:
                    pass
    else:
        async with AsyncSessionLocal() as db:
            r = await chatbot.process_chat(db=db, message=message, officer=ctx, chat_history=hist, session_id=sid)
        resp = (r.get("response") if isinstance(r, dict) else r) or ""
    hist.append({"role": "user", "content": message})
    hist.append({"role": "assistant", "content": resp})
    return resp, sid


async def live_main():
    ctx = await get_ctx()

    # ── A. Ward/block scoping survives both the refusal path and the pick ──
    r, sid = await turn(ctx, "Show my overdue field visits in Ward 12.")
    check("A. a ward the officer does not hold is refused by name, not silently answered",
          "outside your assigned jurisdiction" in strip_html(r) and "Ward 12" in strip_html(r), r[:200])

    r1, sid1 = await turn(ctx, "Show my overdue field visits in Block 0054.")
    check("A. a block filter narrows the overdue listing to just that block",
          "Found  1  field visit" in strip_html(r1) or "Found 1 field visit" in strip_html(r1), strip_html(r1)[:200])
    hist1 = [{"role": "user", "content": "Show my overdue field visits in Block 0054."},
             {"role": "assistant", "content": r1}]
    r1b, _ = await turn(ctx, "Give me the application number, survey number, and subdivision number of the first overdue visit.",
                         hist=hist1, sid=sid1)
    check("A. the block-scoped single-row follow-up answers every requested field",
          "2026/0154/02/015465" in strip_html(r1b) and "application" in strip_html(r1b).lower(), strip_html(r1b)[:300])
    r1c, _ = await turn(ctx, "Give me the application number of the second overdue visit.", hist=hist1, sid=sid1)
    check("A. an out-of-range ordinal in the SCOPED (1-row) set is refused, not answered from the wider jurisdiction",
          "only 1 application" in strip_html(r1c) and "2026/0154/02/015386" not in strip_html(r1c), strip_html(r1c)[:200])

    r2, sid2 = await turn(ctx, "Show my overdue field visits in Ward 005.")
    check("A. a zero-match in-jurisdiction ward says so honestly, not a jurisdiction refusal",
          "past its scheduled date" in strip_html(r2).lower() or "no field visit" in strip_html(r2).lower(), strip_html(r2)[:200])

    # ── B. Multiple explicit ordinal positions ──────────────────────────────
    r0, sidb = await turn(ctx, "Which application should I field visit next?")
    overdue = re.findall(r"2026/0154/02/\d+", r0.split("Applications awaiting")[0])
    histb = [{"role": "user", "content": "Which application should I field visit next?"},
             {"role": "assistant", "content": r0}]
    r3, _ = await turn(ctx, "Show the first, second and third overdue visits.", hist=histb, sid=sidb)
    check("B. 'first, second and third' resolves to all 3 named rows, in order",
          f"{len(overdue)} of those {len(overdue)}" in strip_html(r3), strip_html(r3)[:200])

    r0c, sidc = await turn(ctx, "Which application should I field visit next?")
    histc = [{"role": "user", "content": "Which application should I field visit next?"},
             {"role": "assistant", "content": r0c}]
    r4, _ = await turn(ctx, "Give the first and tenth overdue visits.", hist=histc, sid=sidc)
    check("B. an out-of-range position among several named ones is refused, not silently dropped",
          "no number 10" in strip_html(r4) or "there is no number 10" in strip_html(r4), strip_html(r4)[:200])

    # ── C. Exclusion clause stays attached to the same request ─────────────
    r0d, sidd = await turn(ctx, "Which application should I field visit next?")
    histd = [{"role": "user", "content": "Which application should I field visit next?"},
              {"role": "assistant", "content": r0d}]
    r5, _ = await turn(ctx, "Show the second overdue visit, excluding rescheduled visits.", hist=histd, sid=sidd)
    second = overdue[1] if len(overdue) > 1 else None
    check("C. the exclusion clause does not get split into an unrelated second question",
          bool(second) and second in strip_html(r5) and "1. Show" not in strip_html(r5), strip_html(r5)[:200])


def main() -> None:
    test_multi_ordinal_pick_unit()
    test_rescheduled_criterion_unit()
    test_splitters_stand_aside_for_visit_exclusion()
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
