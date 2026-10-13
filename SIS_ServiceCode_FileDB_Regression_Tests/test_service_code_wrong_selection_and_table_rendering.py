"""
Regression tests for:
1. Wrong service-code substitution (e.g. 0158/0159 overriding 0153/0154 from conversation history).
2. Distinction between service-definition comparisons and application register comparisons.
3. Markdown table structure generation and frontend HTML rendering.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.utils.helpers import find_service_codes, SIS_URBAN_SERVICES
from backend.services.chatbot import _calc_turn, _build_service_code_comparison_response
from backend.schemas import OfficerContext
from backend.database import AsyncSessionLocal
from backend.models import SISOfficer
from sqlalchemy import select


def get_mock_officer():
    import uuid
    return OfficerContext(
        officer_id=uuid.uuid4(),
        employee_id="SIS-002",
        name="M. Arun",
        designation="Ward SIS",
        email="marun_chn@sis.tn.gov.in",
        jurisdiction_type="ward",
        jurisdiction_name="Korattur",
        jurisdiction_ids=[]
    )


def test_pairwise_identifier_extraction():
    """Verifies exact identifiers extracted without history bleeding."""
    q1 = "Show only the differences between 0153 and 0154 in a table"
    assert find_service_codes(q1) == ["0153", "0154"]

    q2 = "Compare 0154 and 0153"
    assert find_service_codes(q2) == ["0154", "0153"]

    q3 = "Compare 0158 and 0159 in a table"
    assert find_service_codes(q3) == ["0158", "0159"]

    q4 = "Compare 0178 and 0185"
    assert find_service_codes(q4) == ["0178", "0185"]


def test_service_definition_comparison_output():
    """Verifies that definition comparison produces markdown table with 0153 and 0154."""
    resp = _build_service_code_comparison_response(["0153", "0154"], "Show only the differences between 0153 and 0154 in a table")
    assert "| Attribute |" in resp
    assert "0153" in resp and "0154" in resp
    assert "0158" not in resp and "0159" not in resp
    assert "Not Involving Subdivision" in resp
    assert "Involving Subdivision" in resp


def test_history_does_not_override_explicit_codes():
    """Verifies that when history mentions 0158 and 0159, query naming 0153 and 0154 NEVER returns 0158 or 0159."""
    async def _run():
        officer = get_mock_officer()
        history = [
            {"role": "user", "content": "What is the difference between Modification / Anadeenam and Addition?"},
            {"role": "assistant", "content": "0158 vs 0159 discussion..."}
        ]
        
        # 1. Definition comparison after history
        q_def = "Show only the differences between 0153 and 0154 in a table"
        ans_def = await _calc_turn(q_def, "test-session-1", officer, None, history)
        assert ans_def is not None
        rep_def = ans_def["response"]
        assert "0153" in rep_def and "0154" in rep_def, f"Expected 0153/0154, got:\n{rep_def}"
        assert "0158" not in rep_def and "0159" not in rep_def, f"0158/0159 leaked into response:\n{rep_def}"
        assert "| Attribute |" in rep_def

        # 2. Application register comparison after history
        q_app = "Compare 0153 and 0154 applications in a table"
        ans_app = await _calc_turn(q_app, "test-session-2", officer, None, history)
        assert ans_app is not None
        rep_app = ans_app["response"]
        assert "0153" in rep_app and "0154" in rep_app, f"Expected 0153/0154 in app table, got:\n{rep_app}"
        assert "0158" not in rep_app and "0159" not in rep_app, f"0158/0159 leaked into app table:\n{rep_app}"
        assert "Total Applications" in rep_app

        # 3. Inverted pair
        q_inv = "Compare 0154 and 0153"
        ans_inv = await _calc_turn(q_inv, "test-session-3", officer, None, history)
        assert ans_inv is not None
        assert "0154" in ans_inv["response"] and "0153" in ans_inv["response"]
        assert "0158" not in ans_inv["response"] and "0159" not in ans_inv["response"]

        # 4. Another explicit pair (0158 and 0159)
        q_other = "Compare 0158 and 0159 in a table"
        ans_other = await _calc_turn(q_other, "test-session-4", officer, None, history)
        assert ans_other is not None
        assert "0158" in ans_other["response"] and "0159" in ans_other["response"]
        assert "0153" not in ans_other["response"] and "0154" not in ans_other["response"]

    asyncio.run(_run())


def test_markdown_table_syntax_validity():
    """Verifies that generated markdown table strings have balanced columns and delimiter rows."""
    resp = _build_service_code_comparison_response(["0153", "0154"], "Show only the differences between 0153 and 0154 in a table")
    lines = [line.strip() for line in resp.split("\n") if line.strip().startswith("|")]
    assert len(lines) >= 3, f"Expected at least header, delimiter, and 1 row; got {len(lines)}"
    assert lines[1].startswith("|---"), f"Delimiter row invalid: {lines[1]}"
    col_counts = [line.count("|") for line in lines]
    assert len(set(col_counts)) == 1, f"Mismatched pipe column counts in table lines: {col_counts}"


if __name__ == "__main__":
    test_pairwise_identifier_extraction()
    print("PASS: test_pairwise_identifier_extraction")
    test_service_definition_comparison_output()
    print("PASS: test_service_definition_comparison_output")
    test_history_does_not_override_explicit_codes()
    print("PASS: test_history_does_not_override_explicit_codes")
    test_markdown_table_syntax_validity()
    print("PASS: test_markdown_table_syntax_validity")
    print("ALL SERVICE CODE WRONG RESULT & RENDERING REGRESSION TESTS PASSED.")
