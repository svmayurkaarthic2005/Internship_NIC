"""
Regression test for CSVCOL-001 (confirmed, High severity).

Two symptoms confirmed live through the chatbot UI, 2026-10-13 (see
SIS_ServiceCode_FileDB_Failure_Cases.jsonl), both traced to the same root
cause:

  1. "List all the service codes in this file." (one CSV attached, header
     `service_code`) -> "I could not find this in the uploaded document."
     even though the column is right there and the equivalent literal
     phrasing "list the service_code column" answers correctly.
  2. "Compare the service codes in both files." (two CSVs attached) ->
     the deterministic multi-CSV comparison path never engages (because of
     the same failure as #1), so the question falls to the ungrounded
     LLM/retrieval path, which then states WRONG per-file figures for one
     of the two files (dropped a value, over-counted another) -- the
     project's own "CSV questions are computed, never narrated" rule
     (CLAUDE.md, "Chat attachments") is violated for this exact phrasing.

Root cause: `resolve_column()` in backend/services/csv_ops.py.
  - Its full-header match requires the header text to appear in the question
    bounded by `(?![a-z0-9])` on both sides. The header `service_code`
    normalises to "service code"; the natural plural question text has
    "service codes" -- the trailing "s" fails that right-hand boundary, so
    the full-header match never fires.
  - It then falls back to matching any SINGLE distinctive header word (>=3
    chars). "service" is a whole-word hit in the question AND a distinctive
    word of BOTH `service_code` and `service_description` (this fixture's
    other column), so two columns match -- the function only accepts a
    single match, so it returns None for an otherwise everyday question.

No DB, no LLM -- `resolve_column()` / `parse_csv_question()` are pure
functions over a headers/rows list. Run directly: `python
test_csv_plural_column_resolution.py`.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.services import csv_ops

HEADERS = ["row", "service_code", "service_description", "application_number", "status"]
ROWS = [
    {"row": "1", "service_code": "0154", "service_description": "Involving Subdivision",
     "application_number": "TEST/0154/99/000701", "status": "Pending"},
    {"row": "2", "service_code": "0178", "service_description": "F-Line Urban Demarcation",
     "application_number": "TEST/0178/99/000702", "status": "Approved"},
    {"row": "3", "service_code": "0158", "service_description": "Modification",
     "application_number": "TEST/0158/99/000703", "status": "Pending"},
    {"row": "4", "service_code": "0153", "service_description": "Not Involving Subdivision",
     "application_number": "TEST/0153/99/000704", "status": "Rejected"},
    {"row": "5", "service_code": "0154", "service_description": "Involving Subdivision",
     "application_number": "TEST/0154/99/000701", "status": "Pending"},
]


def test_literal_column_name_resolves():
    """Sanity check: the engine works fine when the header is quoted literally."""
    assert csv_ops.resolve_column("list the service_code column", HEADERS) == "service_code"


def test_natural_plural_phrasing_should_resolve_to_service_code():
    """FAILS on the current code: resolve_column() returns None."""
    col = csv_ops.resolve_column("List all the service codes in this file.", HEADERS)
    assert col == "service_code", (
        f"resolve_column() returned {col!r} for the natural plural phrasing "
        f"'service codes', instead of 'service_code' -- the singular/plural "
        f"mismatch plus the service_code/service_description word-overlap "
        f"ambiguity makes the match fail."
    )


def test_list_service_codes_should_produce_a_csv_operation():
    """FAILS on the current code: parse_csv_question() returns None, so this
    question never reaches the deterministic 'computed, never narrated' CSV
    engine at all -- it falls through to the LLM/retrieval path instead."""
    op = csv_ops.parse_csv_question("List all the service codes in this file.", HEADERS, ROWS)
    assert op is not None, (
        "parse_csv_question() returned None for an everyday CSV question "
        "naming an existing column in the plural -- the question silently "
        "skips the deterministic engine and is handed to the LLM, which is "
        "exactly what this project's CSV architecture is designed to prevent."
    )


if __name__ == "__main__":
    failures = []
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS: {name}")
            except AssertionError as e:
                failures.append(name)
                print(f"FAIL: {name}\n  {e}")
    print(f"\n{len(failures)} failing (expected: test_natural_plural_phrasing_should_resolve_to_service_code, "
          f"test_list_service_codes_should_produce_a_csv_operation, until CSVCOL-001 is fixed)")
    sys.exit(1 if failures else 0)
