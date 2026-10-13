"""
Regression test for SVCCOMPARE-ALL-004 (Architectural pairwise comparison disparity):
Documents that in _build_service_code_comparison_response:
1. Rich domain comparison (field visit, table format, negation, identity check, both similarities and differences)
   is hardcoded EXCLUSIVELY for set(exact) == {'0178', '0185'}.
2. For all other 405 pairs (e.g. 0153 vs 0154):
   - 'BOTH' mode returns ONLY the similarity sentence and drops all differences.
   - 'wants_table' is ignored (returns plain text/bullets, no markdown table).
   - 'AMBIGUOUS_COMPARISON' fails to clarify and falls back to full difference text.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.services.chatbot import _build_service_code_comparison_response


def test_fline_vs_appeal_has_full_table():
    """0178 vs 0185 properly renders a markdown table when requested."""
    resp = _build_service_code_comparison_response(
        ["0178", "0185"],
        "Show only the differences between 0178 and 0185 in a table"
    )
    assert "| Attribute |" in resp and "F-Line" in resp, (
        f"Expected markdown table in 0178 vs 0185 response, got:\n{resp}"
    )
    print("PASS: 0178 vs 0185 renders markdown table")


def test_isd_vs_nisd_renders_table():
    """Verifies SVCCOMPARE-ALL-004 resolved: For 0153 vs 0154, table request produces markdown table."""
    resp = _build_service_code_comparison_response(
        ["0153", "0154"],
        "Show only the differences between 0153 and 0154 in a table"
    )
    assert "| Attribute |" in resp and "|---" in resp, (
        f"Expected markdown table for 0153 vs 0154, got:\n{resp}"
    )
    print("PASS: 0153 vs 0154 produces markdown table")


def test_isd_vs_nisd_both_renders_similarities_and_differences():
    """Verifies SVCCOMPARE-ALL-004 resolved: 'BOTH' mode returns both similarities and differences."""
    resp = _build_service_code_comparison_response(
        ["0153", "0154"],
        "Show both similarities and differences between 0153 and 0154"
    )
    assert "Similarities" in resp and "Differences" in resp, (
        f"Expected both Similarities and Differences sections in response, got:\n{resp}"
    )
    print("PASS: 'BOTH' mode returns both sections for 0153 vs 0154")


def test_isd_vs_nisd_ambiguous_clarifies():
    """Verifies SVCCOMPARE-ALL-004 resolved: 'AMBIGUOUS_COMPARISON' asks clarifying question."""
    resp = _build_service_code_comparison_response(
        ["0153", "0154"],
        "What is not the difference between 0153 and 0154?"
    )
    assert "clarify" in resp.lower() or "similar" in resp.lower(), (
        f"Expected clarification in response, got:\n{resp}"
    )
    print("PASS: Ambiguous comparison asks for clarification for 0153 vs 0154")


if __name__ == "__main__":
    test_fline_vs_appeal_has_full_table()
    test_isd_vs_nisd_renders_table()
    test_isd_vs_nisd_both_renders_similarities_and_differences()
    test_isd_vs_nisd_ambiguous_clarifies()
    print("ALL PAIRWISE ARCHITECTURAL DISPARITY TESTS PASSED (Generalization verified).")
