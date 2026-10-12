"""Regression: cross-checking application numbers an officer uploads against
the register, and the negation follow-ups ("don't show those"/"don't show
these ones") that come after such an answer -- including OCR'd text, where
the numbers were never in a named column at all.

Found by reproducing live, not assumed:

1. `csv_ops.parse_csv_question()` resolved a column for "list the application
   numbers in the file" but its OWN final return statement (reached whenever
   the question matched an explicit op word like "list"/"show me", as
   opposed to the bare-projection shortcut earlier in the function) dropped
   it again -- `CsvOperation(op=op, conditions=conds)`, no `column=`. Every
   such question then rendered "0 match(es)" even when every row matched,
   because `execute()` only turns `res.value` into the per-row list
   `render_csv_result` expects when `operation.column` is set, and otherwise
   leaves it the bare int `len(matched)` -- `isinstance(int, list)` is
   False, so `render_csv_result` printed zero matches for a genuine filter.
2. The attachment layer answers ONLY from a file's own text and never
   queries the database at all, so "are these application numbers in our
   database?" found no textual evidence, was a "weak" (non-explicit) claim,
   and silently fell through to an unrelated register-wide listing
   (`all_service_code_applications`, 44 rows) that answered a question
   nobody asked -- a confident non-answer. There was no mechanism anywhere
   in the pipeline that actually compared an uploaded number against the
   register at all.
3. Once that first cross-check turn mis-routed, the follow-up chain broke:
   `attachment_topic_active()` came back False for the NEXT turn (it was
   never an attachment turn to begin with), so "what is the mystery_code
   field?" fell to `rag.parse_intent`'s bare-"field" heuristic and got
   answered with an unrelated field-VISIT summary table instead.
4. Even once rebuilt, `_REGISTER_SHAPED_RE` in `attachment_qa.py` matched the
   bare word "show" in "don't show those, just show the one in our
   database" / "don't show these ones" and forced `plan_answer()` to decline
   regardless of `attachment_topic_active`, so both negations fell through
   to an unrelated `pending_applications` listing (17 rows) instead of
   staying with the file.

No DB, no LLM -- classifier/regex/pure-function logic only.
Run: python test_attachment_db_crosscheck.py
"""
import asyncio
import sys
import uuid

from backend.services import csv_ops
from backend.services import attachment_qa as aq
from backend.schemas import OfficerContext

bad = []
total = 0


def check(name, condition, detail=""):
    global total
    total += 1
    if condition:
        print(f"ok   {name}")
    else:
        bad.append((name, detail))
        print(f"FAIL {name}" + (f" -- {detail}" if detail else ""))


# ── 1. csv_ops: the dropped column on the explicit-op-word path ───────────
HEADERS = ["application_number", "owner_name", "mystery_code"]
ROWS = [
    {"application_number": "2026/0154/02/015386", "owner_name": "A", "mystery_code": "XQ-99"},
    {"application_number": "2099/0154/99/999999", "owner_name": "B", "mystery_code": "XQ-12"},
    {"application_number": "ABC123", "owner_name": "C", "mystery_code": "XQ-45"},
]

for q in ("list the application numbers in the file",
          "show me the application numbers",
          "which rows have an application number"):
    op = csv_ops.parse_csv_question(q, HEADERS, ROWS)
    check(f'"{q}" resolves a column to project', op is not None and op.column == "application_number",
          op)
    if op is not None:
        res = csv_ops.execute(op, HEADERS, ROWS)
        check(f'"{q}" -> execute() returns all 3 rows as a list, not a bare int',
              isinstance(res.value, list) and len(res.value) == 3, res.value)
        text, _ = aq.render_csv_result("apps_check.csv", res, "en")
        check(f'"{q}" -> rendered text reports "3 match(es)", not "0 match(es)"',
              text.startswith("3 match(es)"), text)

# A genuinely columnless, conditionless filter question must not report a
# false "0 match(es)" either -- it has nothing to project, so it is left
# unanswered here (None) rather than answered wrong.
vague = csv_ops.parse_csv_question("don't show that one, just show the valid ones", HEADERS, ROWS)
check('a vague filter with no resolvable column and no condition returns None '
      "(falls through instead of a false zero)", vague is None, vague)

# An ordinary count with no column is untouched -- OP_COUNT never reads
# `operation.column` in `execute()`.
cnt = csv_ops.parse_csv_question("how many rows are there", HEADERS, ROWS)
check('"how many rows are there" still parses as a count', cnt is not None and cnt.op == csv_ops.OP_COUNT, cnt)
if cnt is not None:
    res = csv_ops.execute(cnt, HEADERS, ROWS)
    check("count executes to 3 over all rows", res.value == 3, res.value)


# ── 2. the cross-check cue and column finder ────────────────────────────
CROSS_CHECK_CUES = (
    "are these application numbers in our database?",
    "compare the data in this file with our database",
    "is the application number ABC123 a valid format?",
    "check this against the register",
    "do these exist in our records?",
)
for q in CROSS_CHECK_CUES:
    check(f'"{q}" is recognised as a database cross-check question',
          bool(aq._DB_CROSS_CHECK_RE.search(q)))

NOT_CROSS_CHECK = (
    "what does the file say?",
    "how many rows are approved?",
    "summarise the document",
)
for q in NOT_CROSS_CHECK:
    check(f'"{q}" is NOT read as a cross-check question',
          not aq._DB_CROSS_CHECK_RE.search(q))

check('"application_number" header is found as the app-number column',
      aq._find_app_number_column(HEADERS) == "application_number")
check('a file with no application-number-shaped header finds none',
      aq._find_app_number_column(["owner_name", "mystery_code"]) is None)


# ── 3. the register-shaped guard no longer trips on a bare negated "show" ──
NEGATED_CONTINUATIONS = (
    "don't show those, just show the one that's in our database",
    "don't show these ones",
    "dont display those that are not in our database",
)
for q in NEGATED_CONTINUATIONS:
    check(f'"{q}" is NOT register-shaped (stays with the attachment)',
          not aq._is_register_shaped(q))

FRESH_REGISTER_REQUESTS = (
    "show my pending applications",
    "how many applications are approved",
    "list my overdue applications",
)
for q in FRESH_REGISTER_REQUESTS:
    check(f'"{q}" is still register-shaped (a genuine fresh register query)',
          aq._is_register_shaped(q))


# ── 4. the format-check regex itself: fullmatch, not a loose search ────────
check('"2026/0154/02/015386" is a well-formed application number',
      bool(aq._APPLICATION_NO_RE.fullmatch("2026/0154/02/015386")))
check('"ABC123" is NOT a well-formed application number',
      not aq._APPLICATION_NO_RE.fullmatch("ABC123"))
check('"2099/0154/99/999999" (well-formed but fabricated) still matches the FORMAT check',
      bool(aq._APPLICATION_NO_RE.fullmatch("2099/0154/99/999999")))


# ── 5. a DB outage must read as "could not check", never "not found" ──────
class _BrokenDB:
    async def execute(self, *a, **k):
        from sqlalchemy.exc import OperationalError
        raise OperationalError("stmt", {}, Exception("connection refused"))


_OFFICER = OfficerContext(officer_id=uuid.uuid4(), employee_id="x", name="x", email="x",
                          designation="x", jurisdiction_type="block",
                          jurisdiction_name="x", jurisdiction_ids=[])

_db_fail_results = asyncio.run(aq._check_numbers_against_register(
    _BrokenDB(), _OFFICER, ["2026/0154/02/015386", "ABC123"]))
check("a well-formed number is 'unverifiable' (not 'not_found') when the DB lookup itself raises",
      _db_fail_results[0] == ("2026/0154/02/015386", "unverifiable"), _db_fail_results)
check("a malformed number is still caught as invalid_format without ever touching the DB",
      _db_fail_results[1] == ("ABC123", "invalid_format"), _db_fail_results)


# ── 6. a repeated ID in the file is checked once but reported with its count ──
class _StubDoc:
    filename = "dups.csv"
    id = uuid.uuid4()


_dup_rows = [
    {"application_number": "2099/0154/99/999999", "owner_name": "A"},
    {"application_number": "2099/0154/99/999999", "owner_name": "A dup"},
    {"application_number": "2099/0154/99/999999", "owner_name": "A dup again"},
    {"application_number": "ABC123", "owner_name": "B"},
]


class _EmptyHitDB:
    async def execute(self, *a, **k):
        class _R:
            def first(self):
                return None
        return _R()


_dup_plan = asyncio.run(aq._cross_check_app_numbers(
    _EmptyHitDB(), _OFFICER, _StubDoc(), ["application_number", "owner_name"], _dup_rows, "en"))
check("the duplicate ID is reported once, tagged '(3x in file)', not three separate lines",
      _dup_plan is not None and _dup_plan.text.count("2099/0154/99/999999") == 1
      and "(3x in file)" in _dup_plan.text, _dup_plan.text if _dup_plan else None)
check("the non-duplicate malformed ID carries no duplicate tag",
      _dup_plan is not None and "ABC123 —" in _dup_plan.text
      and "ABC123 — not a valid application-number format" in _dup_plan.text
      and "ABC123 — not a valid application-number format (" not in _dup_plan.text)


# ── 7. filtering a PREVIOUS cross-check by outcome category ("show only
# the invalid ones" / "don't show the ones not in our database") ──────────
#
# Found live: the word "validate" (carried into a later turn by the
# ordinary short-message continuation logic, which appends the previous
# user message) contains the bare substring "valid" -- a naive `in` check
# read it as the officer asking to filter down to the "valid" group, before
# any filterable result even existed yet.
check('"validate these application numbers..." does NOT match the "valid" filter group '
      "(no word-boundary collision with the verb)",
      aq._cc_matched_group("validate these application numbers against our database") is None)
check('"show only the invalid ones" matches the invalid_format group',
      aq._cc_matched_group("show only the invalid ones") == {"invalid_format"})
check('"don\'t show the ones that are not in our database" matches the not_found group',
      aq._cc_matched_group("don't show the ones that are not in our database") == {"not_found"})
check('"show only the valid ones" matches every well-formed outcome',
      aq._cc_matched_group("show only the valid ones")
      == {"not_found", "in_jurisdiction", "outside_jurisdiction"})
check('a plain "don\'t display these." (no category word) matches nothing -- '
      "too vague to filter by, left to the ordinary re-show",
      aq._cc_matched_group("don't display these.") is None)

_cc_prev_results = [
    {"value": "2026/0154/02/015386", "label": "in_jurisdiction", "count": 1},
    {"value": "2026/0154/02/015395", "label": "in_jurisdiction", "count": 1},
    {"value": "2099/0154/99/999999", "label": "not_found", "count": 1},
    {"value": "ABC123", "label": "invalid_format", "count": 1},
]


class _StubDB:
    """Stands in for a real session: `_crosscheck_filter_followup` reads
    the previous turn's stored crosscheck through one query only."""
    def __init__(self, stored):
        self._stored = stored

    async def execute(self, *a, **k):
        class _R:
            def __init__(self, v):
                self._v = v

            def scalar_one_or_none(self):
                return self._v
        return _R(self._stored)


async def _run_filter(message, stored_crosscheck):
    return await aq._crosscheck_filter_followup(_StubDB(stored_crosscheck), "sid", message, "en")


_stored_row = {"entity": "attachment", "crosscheck": _cc_prev_results,
              "document_ids": ["11111111-1111-1111-1111-111111111111"],
              "crosscheck_source": "f.csv, rows 1-4"}
_plan_only_invalid = asyncio.run(_run_filter("show only the invalid ones", _stored_row))
check('"show only the invalid ones" filters the PREVIOUS result to exactly ABC123',
      _plan_only_invalid is not None and "ABC123" in _plan_only_invalid.text
      and "2026/0154/02/015386" not in _plan_only_invalid.text
      and "1 of 4" in _plan_only_invalid.text,
      _plan_only_invalid.text if _plan_only_invalid else None)

_plan_exclude_notfound = asyncio.run(_run_filter(
    "don't show the ones not in our database", _stored_row))
check('"don\'t show the ones not in our database" EXCLUDES not_found, keeps the rest',
      _plan_exclude_notfound is not None
      and "2099/0154/99/999999" not in _plan_exclude_notfound.text
      and "2026/0154/02/015386" in _plan_exclude_notfound.text
      and "ABC123" in _plan_exclude_notfound.text
      and "3 of 4" in _plan_exclude_notfound.text,
      _plan_exclude_notfound.text if _plan_exclude_notfound else None)

_plan_no_prev = asyncio.run(_run_filter("show only the invalid ones", None))
check("with no previous cross-check at all, the filter follow-up declines (None) "
      "rather than inventing a result", _plan_no_prev is None)

_plan_no_group = asyncio.run(_run_filter("exclude the first and third", _stored_row))
check('"exclude the first and third" (a POSITION, not a category) is NOT handled here -- '
      "declines rather than guessing an arbitrary row order",
      _plan_no_group is None)

for _undo_msg in ("undo that filter", "restore the original list", "bring back those I excluded"):
    _plan_undo = asyncio.run(_run_filter(_undo_msg, _stored_row))
    check(f'"{_undo_msg}" gets an honest decline (no filter history is kept), '
          "not a fall-through to the LLM where it would invent a plausible history",
          _plan_undo is not None and "no history" in _plan_undo.text.lower()
          and "status = pending" not in _plan_undo.text,
          _plan_undo.text if _plan_undo else None)

print(f"{total - len(bad)}/{total} ok")
sys.exit(1 if bad else 0)
