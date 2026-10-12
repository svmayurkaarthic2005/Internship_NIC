""""Overdue" has two meanings in this register (CLAUDE.md: the field-visit
past-its-scheduled-date sense, and the application-level 15-working-day SLA
sense), and three separate places used to collapse a bare "overdue visit(s)"
question into the wrong one, or into a stale application entirely:

1. `rag.parse_intent` required the full phrase "overdue FIELD visit(s)" / "...
   inspection(s)" to route to `fv_overdue_inspections`; a bare "overdue
   visit(s)" (no "field"/"site"/"inspection") fell through to the much looser
   `overdue_applications` check further down, which only needs the word
   "overdue" with no interrogative -- so "Show those two columns for all
   overdue visits." answered the 15-day SLA count (0) instead of the 3 visits
   actually past their scheduled date.
2. `followup_context.build_context`'s plain `field_visits` summary carried its
   rows in table (chronological) order, unlike `fv_visit_plan`'s rows, which
   are already overdue-first -- so "the second overdue visit" as a follow-up
   on a plain summary picked position 2 of the WHOLE list, which could easily
   be a completed visit from months ago.
3. `chatbot._early_subdivision_answer_inner` resolved an application-less
   message via a stale chat_history scan whenever it carried a subdivision_qa
   cue ("survey number and subdivision number") -- so even after fix #2, "the
   second overdue visit" with no own application number was answered about
   whichever application chat_history mentioned FIRST, never reaching the
   ordinal/overdue-aware list machinery at all.

No DB, no LLM: python test_overdue_visit_routing.py
"""
import sys

from backend.services.rag import parse_intent
from backend.services import followup_context as fc
from backend.services import chatbot as cb

OVERDUE_INTENT_CASES = [
    ("Show those two columns for all overdue visits.", "fv_overdue_inspections"),
    ("show the second overdue visit", "fv_overdue_inspections"),
    ("show all overdue visits", "fv_overdue_inspections"),
    ("which field visits are overdue?", "fv_overdue_inspections"),
    # "Give me the application number, survey number, and subdivision number
    # of the second overdue visit." -- `_is_list_action` (rag.py) recognised
    # "show"/"list"/"get"/... but not "give", so this (and `_big_plan()`'s
    # own per-field split of it, see test_big_message.py) fell through to the
    # generic `field_visits` intent, which carries no overdue-only, ordinal-
    # sorted set for "second" to resolve against -- the whole unfiltered
    # visit table was returned instead.
    ("Give me the application number, survey number, and subdivision number "
     "of the second overdue visit.", "fv_overdue_inspections"),
    ("give me the second overdue visit", "fv_overdue_inspections"),
    ("tell me the survey number of the second overdue visit", "fv_overdue_inspections"),
    # the application-level sense must be untouched
    ("how many applications are overdue", "overdue_applications"),
    ("show overdue applications", "overdue_applications"),
    ("is my application overdue", "overdue_applications"),
]

# A plain field-visit summary (not `fv_visit_plan`) must carry its rows
# overdue-first, same convention `fv_visit_plan` already uses, so an ordinal
# follow-up ("the second overdue visit") lands in the right section.
_ROWS = [
    {"application_number": "2026/0154/02/012766", "is_overdue": False},  # completed, oldest
    {"application_number": "2026/0154/02/013642", "is_overdue": False},  # completed
    {"application_number": "2026/0154/02/015465", "is_overdue": True},
    {"application_number": "2026/0154/02/015386", "is_overdue": True},
    {"application_number": "2026/0154/02/015395", "is_overdue": True},
    {"application_number": "2026/0154/02/015495", "is_overdue": False},  # unscheduled
]

ORDINAL_VISIT_REFERENCE_CASES = [
    ("What is the survey number and subdivision number of the second overdue visit?", True),
    ("show the last two overdue visits", True),
    ("show the first two overdue visits", True),
    # must stay False: names its own application, or isn't ordinal/slice-shaped
    ("what is the survey number of 2026/0154/02/012766?", False),
    ("show all overdue visits", False),           # no ordinal/slice
    ("what is the second application I filed?", False),  # no visit/inspection word
]


def run():
    failures = []

    for msg, expected in OVERDUE_INTENT_CASES:
        got = parse_intent(msg, prev_intent=None)
        if got != expected:
            failures.append(f"parse_intent({msg!r}) = {got!r}, expected {expected!r}")

    ctx = fc.build_context("field_visits", {"field_visits": _ROWS, "query_type": "Field Visits Summary"})
    if ctx is None:
        failures.append("build_context returned None for a non-empty field_visits list")
    else:
        expected_order = ["2026/0154/02/015465", "2026/0154/02/015386", "2026/0154/02/015395",
                           "2026/0154/02/012766", "2026/0154/02/013642", "2026/0154/02/015495"]
        if ctx.application_numbers != expected_order:
            failures.append(f"build_context order = {ctx.application_numbers}, expected {expected_order}")
        if (ctx.filters or {}).get("fv_overdue_count") != 3:
            failures.append(f"fv_overdue_count = {(ctx.filters or {}).get('fv_overdue_count')!r}, expected 3")

    for msg, expected in ORDINAL_VISIT_REFERENCE_CASES:
        got = cb._is_ordinal_visit_reference(msg)
        if got != expected:
            failures.append(f"_is_ordinal_visit_reference({msg!r}) = {got!r}, expected {expected!r}")

    # `_big_plan()` splits "Give me A, B, and C of the second overdue visit."
    # into one bare-field question per field ("application number of the
    # second overdue visit"), which carries no list verb of its own and so
    # parses fresh to the generic `field_visits`, not `fv_overdue_inspections`
    # -- `_maybe_resolve_fresh_visit_ordinal`'s own intent gate must accept
    # `field_visits` too (on top of `fv_visit_plan`/`fv_overdue_inspections`),
    # relying on `_is_ordinal_visit_reference` as the real, narrower gate.
    _split_piece = "application number of the second overdue visit"
    if parse_intent(_split_piece, prev_intent=None) != "field_visits":
        failures.append(f"parse_intent({_split_piece!r}) regressed away from field_visits "
                        "-- update the field_visits branch in _maybe_resolve_fresh_visit_ordinal's docstring/gate check")
    if not cb._is_ordinal_visit_reference(_split_piece):
        failures.append(f"_is_ordinal_visit_reference({_split_piece!r}) = False, expected True")
    import inspect
    _gate_src = inspect.getsource(cb._maybe_resolve_fresh_visit_ordinal)
    if '"field_visits"' not in _gate_src:
        failures.append("_maybe_resolve_fresh_visit_ordinal's intent gate no longer admits "
                        "'field_visits' -- a _big_plan()-split field question would stop "
                        "resolving its ordinal again")

    if failures:
        print(f"{len(failures)} failure(s):")
        for f in failures:
            print(" -", f)
        sys.exit(1)
    print(f"{len(OVERDUE_INTENT_CASES)} overdue-intent routing cases, "
          f"overdue-first field-visit ordering, "
          f"{len(ORDINAL_VISIT_REFERENCE_CASES)} ordinal-visit-reference cases: ok")


if __name__ == "__main__":
    run()
