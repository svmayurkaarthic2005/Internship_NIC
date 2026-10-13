"""
Regression test for the comparison-follow-up requirement (CRITICAL Bug
Investigation task, section 3): "What is the difference between F-Line and
F-Line Appeal?" must name BOTH 0178 and 0185, not collapse to one.

Covers `find_service_codes()` in backend/utils/helpers.py, which now adds a
second code (via `_svc_name_alias_match`) alongside the keyword-scoring
winner ONLY when the message itself carries comparison/pairing language --
"what is F-Line Appeal?" alone must still name only 0185.

No DB, no LLM -- pure function. Run directly: `python
test_fline_appeal_multicode_comparison.py`.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.utils.helpers import find_service_codes


def test_bare_fline_names_only_0178():
    assert find_service_codes("what is f-line?") == ["0178"]


def test_bare_fline_appeal_names_only_0185():
    assert find_service_codes("What is F-Line Appeal?") == ["0185"]


def test_difference_question_names_both_codes():
    codes = find_service_codes("What is the difference between F-Line and F-Line Appeal?")
    assert set(codes) == {"0178", "0185"}, codes


def test_compare_phrasing_names_both_codes():
    codes = find_service_codes("Compare these two service codes: F-Line and F-Line Appeal.")
    assert set(codes) == {"0178", "0185"}, codes


def test_are_they_the_same_names_both_codes():
    codes = find_service_codes("Are F-Line and F-Line Appeal the same?")
    assert set(codes) == {"0178", "0185"}, codes


def test_unrelated_alias_still_single_code():
    """Sanity check: the two-code behaviour is gated on comparison language,
    not on every message that happens to name an alias."""
    assert find_service_codes("show f-line applications") == ["0178"]


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
    print(f"\n{len(failures)} failing")
    sys.exit(1 if failures else 0)
