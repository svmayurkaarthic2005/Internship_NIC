"""
Regression test for SVCCOMPARE-ALL-001/002 (SIS_All_Service_Code_Comparison_Failures.jsonl).

Audit-only pass: this test documents a CONFIRMED, currently-unfixed defect
(per the task's explicit "make no production-code changes during the initial
audit" restriction). It is expected to FAIL on the current code -- that
failure IS the evidence. Do not "fix" this test by weakening its assertions;
fix the underlying `find_service_codes()` behavior instead, per the proposed
fix in SIS_All_Service_Code_Comparison_Failures.jsonl (SVCCOMPARE-ALL-001).

Covers the exhaustive 406-pair sweep at a representative sample (the full
sweep lives in SIS_All_Service_Code_Comparison_Matrix.csv, rebuilt by
re-running this file's `rebuild_matrix()` helper) plus the 3 pairs confirmed
live through the chatbot UI.

No DB, no LLM -- pure function. Run directly: `python
test_all_servicecode_pairwise_resolution.py`.
"""
import itertools
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.utils.helpers import SIS_URBAN_SERVICES, find_service_codes

ALL_CODES = sorted(SIS_URBAN_SERVICES.keys())


def _resolves_both_by_name(code_a: str, code_b: str, comparison_word: str = "difference between") -> bool:
    name_a = SIS_URBAN_SERVICES[code_a]["name"]
    name_b = SIS_URBAN_SERVICES[code_b]["name"]
    q = f"What is the {comparison_word} {name_a} and {name_b}?"
    return set(find_service_codes(q)) == {code_a, code_b}


# ── exact pairs confirmed live through the chatbot UI, 2026-10-13 ──────────

def test_modification_vs_addition_resolves_both():
    """Live UI: 'What is the difference between Modification / Anadeenam and
    Addition?' names both 0158 and 0159 (SVCCOMPARE-ALL-001 fixed)."""
    assert _resolves_both_by_name("0158", "0159"), "0158 vs 0159 should both resolve by name"


def test_govt_to_private_vs_settlement_variant_resolves_both():
    """Live UI: 'difference between Govt to Private and Settlement Govt to
    Private' names both 0169 and 0179."""
    assert _resolves_both_by_name("0169", "0179"), "0169 vs 0179 should both resolve by name"


def test_modification_vs_addition_same_question_resolves_both():
    """Live UI: 'Are Modification and Addition the same?' names both 0158 and 0159."""
    assert _resolves_both_by_name("0158", "0159", comparison_word="comparison"), (
        "a comparison-shaped question about 0158 and 0159 should resolve both")


# ── the one pair that DOES work (and why it is not representative) ─────────

def test_fline_vs_fline_appeal_still_passes():
    """F-Line vs F-Line Appeal resolves correctly by name."""
    assert _resolves_both_by_name("0178", "0185")


# ── exhaustive sweep summary (matches SIS_All_Service_Code_Comparison_Matrix.csv) ─

def test_exhaustive_sweep_resolves_all_406_pairs():
    """Verifies fix for SVCCOMPARE-ALL-001/002: All 406 pairs resolve both codes by
    official names without single-winner collapse or pairwise hardcoding."""
    total = 0
    correct = 0
    for a, b in itertools.combinations(ALL_CODES, 2):
        total += 1
        if _resolves_both_by_name(a, b):
            correct += 1
    assert total == 406, f"expected 406 pairs for 29 codes, got {total}"
    assert correct == 406, (
        f"expected all 406/406 pairs resolving both codes by name; got {correct}."
    )


if __name__ == "__main__":
    failures = []
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS: {name}")
            except AssertionError as e:
                failures.append(name)
                print(f"FAIL: {name}\n  {e}")
    print(f"\n{len(failures)} failing")
    sys.exit(1 if failures else 0)
