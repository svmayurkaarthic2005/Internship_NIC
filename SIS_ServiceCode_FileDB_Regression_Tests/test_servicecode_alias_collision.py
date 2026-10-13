"""
Regression test for SVCCODE-001 (confirmed, High severity).

"What is F-Line Appeal?" (service code 0185) is answered as if it were
"What is F-Line?" (service code 0178) -- confirmed live through the chatbot
UI, 2026-10-13 (see SIS_ServiceCode_FileDB_Failure_Cases.jsonl).

Root cause: backend/utils/helpers.py's `_svc_name_alias_match()` returns 0178
on ANY text containing the substring "f-line" / "f line" / "fline", with no
check for whether the surrounding words (e.g. "Appeal") name a DIFFERENT,
longer official service name that also happens to contain that substring.
It runs before `find_service_code_by_name()`'s keyword-overlap scoring ever
gets a chance to see "Appeal" and prefer 0185.

No DB, no LLM -- this is a pure function. Run directly: `python
test_servicecode_alias_collision.py`.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.utils.helpers import find_service_code_by_name, SIS_URBAN_SERVICES


def test_bare_fline_resolves_to_0178():
    assert find_service_code_by_name("what is f-line?") == "0178"


def test_fline_appeal_should_resolve_to_0185():
    """FAILS on the current code: returns '0178' instead of '0185'."""
    code = find_service_code_by_name("what is F-Line Appeal?")
    assert code == "0185", (
        f"'F-Line Appeal' resolved to {code!r} "
        f"({SIS_URBAN_SERVICES.get(code, {}).get('name')!r}) instead of 0185 "
        f"({SIS_URBAN_SERVICES['0185']['name']!r}) -- the alias short-circuit "
        f"in _svc_name_alias_match() fires on the substring 'f-line' before "
        f"the word 'Appeal' is ever considered."
    )


def test_difference_question_should_not_collapse_to_one_code():
    """FAILS on the current code for the same reason."""
    code = find_service_code_by_name("difference between F-Line and F-Line Appeal")
    assert code != "0178" or code is None, (
        "A question naming BOTH 'F-Line' and 'F-Line Appeal' resolved to a "
        "single code (0178) via the alias match, so the difference can never "
        "be computed -- the question should at minimum not collapse to one code."
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
    print(f"\n{len(failures)} failing (expected: test_fline_appeal_should_resolve_to_0185, "
          f"test_difference_question_should_not_collapse_to_one_code, until SVCCODE-001 is fixed)")
    sys.exit(1 if failures else 0)
