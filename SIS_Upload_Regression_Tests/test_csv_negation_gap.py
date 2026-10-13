"""Regression test for a CONFIRMED, live-UI-reproduced defect (QA audit
2026-10-13, upload pipeline pass): backend/services/csv_ops.py's
parse_csv_question() does not recognise ANY negated/inequality phrasing
against a status column -- not even the explicit "!=" operator it already
lists in _COMPARATORS. Because parse_csv_question() returns None for these
questions, attachment_qa.py's deterministic CSV path
(csv_ops.execute(), "computed, never narrated" per CLAUDE.md) is skipped
entirely, and the question falls through to the ungrounded retrieval+LLM
path -- which, in the live reproduction below, silently dropped 3 of 10
genuinely-matching rows.

Reproduced live through the actual rendered chatbot UI
(http://localhost:3000/chatbot.html), officer marun_chn@sis.tn.gov.in, with
sis_survey_records_en.csv attached (12 rows; rows 3 and 7 are "Rejected", the
other 10 are "Pending" or "Approved"):

  "In sis_survey_records_en.csv, show all records except rejected."
     -> actual: "All records not rejected in sis_survey_records_en.csv are
        rows 1, 2, 4, 5, 8, 9, 10." (7 rows)
     -> expected: rows 1, 2, 4, 5, 6, 8, 9, 10, 11, 12 (10 rows -- every row
        except the 2 Rejected ones)
     -> rows 6, 11 and 12 (all non-rejected) are silently missing, with no
        indication the answer might be partial.

This test checks the cheap, DB-free, LLM-free half of the bug: that
parse_csv_question() fails to recognise the negation at all. It cannot by
itself prove the downstream LLM undercounting (that needs the full chat
pipeline) -- see SIS_Upload_Failure_Cases.jsonl / CSV-NEG-001 for the full
live-UI reproduction this narrower check is a regression guard for.

Run: python SIS_Upload_Regression_Tests/test_csv_negation_gap.py
"""
import csv
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.services import csv_ops  # noqa: E402
from backend.services.attachment_qa import render_csv_result  # noqa: E402

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


HEADERS = ["test_record_id", "synthetic_survey_no", "synthetic_subdivision_no",
           "synthetic_ward", "synthetic_block", "synthetic_application_ref",
           "synthetic_status", "synthetic_area_sqm", "synthetic_date", "notes"]

_FIXTURE = os.path.join(os.path.dirname(__file__), "..", "SIS_Upload_QA_Fixtures",
                         "sis_survey_records_en.csv")
with open(_FIXTURE, encoding="utf-8") as f:
    ROWS = list(csv.DictReader(f))

check("fixture loaded with 12 rows", len(ROWS) == 12, f"got {len(ROWS)}")

REJECTED_ROW_NUMBERS = {3, 7}
NON_REJECTED_ROW_NUMBERS = set(range(1, 13)) - REJECTED_ROW_NUMBERS

# ---------------------------------------------------------------------
# The confirmed, reproduced failure: none of these negated phrasings are
# recognised as a status filter at all.
# ---------------------------------------------------------------------
NEGATED_PHRASINGS = (
    "show all records except rejected",
    "show records not rejected",
    "synthetic_status is not rejected",
    "synthetic_status != rejected",
    "show records where synthetic_status is not rejected",
)
for q in NEGATED_PHRASINGS:
    op = csv_ops.parse_csv_question(q, HEADERS, ROWS)
    check(f'"{q}" is recognised as a status-exclusion filter', op is not None,
          "parse_csv_question() returned None -- this is the confirmed bug; "
          "the question silently escapes the deterministic, grounded "
          "computation path entirely")
    if op is not None:
        result = csv_ops.execute(op, HEADERS, ROWS)
        check(f'  ...and returns exactly the 10 non-rejected row numbers for "{q}"',
              set(result.rows) == NON_REJECTED_ROW_NUMBERS,
              f"got {sorted(result.rows)}, expected {sorted(NON_REJECTED_ROW_NUMBERS)}")
        # A column-less OP_FILTER (no field was asked for, just "show the
        # rows") leaves `result.value` as a bare int count, not a list --
        # `render_csv_result`'s OP_FILTER branch originally assumed it was
        # always a list and silently reported "0 match(es)" for this exact
        # shape despite `result.rows` (and the citation beside it) correctly
        # naming all 10 rows. Caught here at the text the officer actually
        # reads, not just the underlying CsvResult.
        text, _cites = render_csv_result("sis_survey_records_en.csv", result, "en")
        check(f'  ...and the rendered answer for "{q}" states 10 row(s), not 0',
              "10 row(s) match" in text or "10 match(es)" in text,
              f"rendered text: {text!r}")

# ---------------------------------------------------------------------
# Regression safeguard: a POSITIVE status filter must keep working --
# this is the one shape the parser already handles correctly.
# ---------------------------------------------------------------------
op = csv_ops.parse_csv_question("show me rejected records", HEADERS, ROWS)
check('"show me rejected records" (positive filter) is still recognised',
      op is not None)
if op is not None:
    result = csv_ops.execute(op, HEADERS, ROWS)
    check("  ...and returns exactly the 2 Rejected row numbers",
          set(result.rows) == REJECTED_ROW_NUMBERS, f"got {sorted(result.rows)}")

print(f"\n{total - len(bad)}/{total} ok")
sys.exit(1 if bad else 0)
