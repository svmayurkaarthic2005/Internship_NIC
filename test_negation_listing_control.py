"""
Regression test suite for SIS Chatbot — Negative Instruction & Listing Control.
Verifies Cases A, B, C, D, E for negative instructions involving applications:
- Case A: Explicitly prohibit listing ("dont show applications", Tamil/Tanglish, zero DB calls, acknowledgment only)
- Case B: Count-only requests ("Don't show applications, only tell me the count.") -> returns aggregates, suppresses row records and table_data
- Case C: Explicit positive list requests ("Show pending applications.") -> retains full list & table_data
- Case D: Negation within a filter ("Don't show rejected applications; show pending ones.") -> preserves positive listing with filter
- Case E: Multi-turn correction (Turn 1: "Show pending applications" -> Turn 2: "Actually, don't show applications")
"""
import asyncio
import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.schemas import OfficerContext
from backend.services.chatbot import process_chat, process_chat_stream
from backend.services.chatbot.chat_followups import _rescope_list_followup
from backend.services.chatbot.chat_response_formatting import _is_count_only_query
from backend.services.chatbot.chat_shared import is_prohibit_listing, _prohibit_listing_reply
from backend.services.chatbot.orchestrator import _build_table_data_raw
from backend.services.rag import parse_intent


def _make_officer():
    uid = uuid.uuid4()
    return OfficerContext(
        officer_id=uid,
        employee_id="SIS-001",
        name="Arun M",
        email="arun_m@sis.tn.gov.in",
        designation="SIS Officer",
        jurisdiction_type="ward",
        jurisdiction_name="Ambattur Ward 001",
        jurisdiction_ids=[uid],
    )


# =============================================================================
# Case A: Explicitly Prohibit Listing
# =============================================================================
def test_case_a_intent_detection_english():
    english_prohibitions = [
        "dont show applications",
        "don't show applications",
        "do not show applications",
        "please don't list applications",
        "stop showing applications",
        "no application list",
        "Actually, don't show applications",
        "don't show the application list",
        "do not display any applications",
        "stop listing applications",
    ]
    for q in english_prohibitions:
        assert is_prohibit_listing(q) is True, f"Expected is_prohibit_listing=True for '{q}'"
        assert parse_intent(q) == "prohibit_listing", f"Expected intent='prohibit_listing' for '{q}', got '{parse_intent(q)}'"


def test_case_a_intent_detection_tamil_and_tanglish():
    ta_tl_prohibitions = [
        ("Applications kaatta vendam", "prohibit_listing"),
        ("Application list panna vendam", "prohibit_listing"),
        ("Applications show pannadhe", "prohibit_listing"),
        ("விண்ணப்பங்களைக் காட்ட வேண்டாம்", "prohibit_listing"),
        ("விண்ணப்பப் பட்டியலைக் காட்டாதீர்கள்", "prohibit_listing"),
        ("விண்ணப்பங்களை காட்டாதே", "prohibit_listing"),
        ("விண்ணப்ப பட்டியல் வேண்டாம்", "prohibit_listing"),
        ("applications show panna vendam", "prohibit_listing"),
    ]
    for q, expected_intent in ta_tl_prohibitions:
        assert is_prohibit_listing(q) is True, f"Expected is_prohibit_listing=True for '{q}'"
        assert parse_intent(q) == expected_intent, f"Expected intent='{expected_intent}' for '{q}', got '{parse_intent(q)}'"


@pytest.mark.asyncio
async def test_case_a_non_streaming_zero_db_query_and_no_table():
    officer = _make_officer()
    db = AsyncMock()
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = None
    db.execute.return_value = mock_res

    with patch("backend.services.postgres.get_all_service_code_applications") as mock_all, \
         patch("backend.services.postgres.get_officer_applications") as mock_pending, \
         patch("backend.services.chatbot.orchestrator.save_chat_messages", new_callable=AsyncMock):

        # Test both 'dont' and 'don\'t'
        for msg in ["dont show applications", "don't show applications", "do not show applications"]:
            res = await process_chat(
                message=msg,
                session_id=str(uuid.uuid4()),
                officer=officer,
                db=db
            )
            # 1. Assert DB listing queries were NEVER called
            assert not mock_all.called, f"get_all_service_code_applications called for '{msg}'"
            assert not mock_pending.called, f"get_officer_applications called for '{msg}'"

            # 2. Assert short acknowledgment and intent
            assert "Understood. I won't display the application list." in res.get("response", "")
            assert res.get("intent") == "prohibit_listing"

            # 3. Assert table_data is None
            assert res.get("table_data") is None


@pytest.mark.asyncio
async def test_case_a_streaming_zero_db_query_and_no_partial_rows():
    officer = _make_officer()
    db = AsyncMock()
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = None
    db.execute.return_value = mock_res

    with patch("backend.services.postgres.get_all_service_code_applications") as mock_all, \
         patch("backend.services.postgres.get_officer_applications") as mock_pending, \
         patch("backend.services.chatbot.orchestrator.save_chat_messages", new_callable=AsyncMock):

        frames = []
        async for chunk in process_chat_stream(
            message="dont show applications",
            session_id=str(uuid.uuid4()),
            officer=officer,
            db=db
        ):
            frames.append(chunk)

        # 1. Zero DB listing queries executed
        assert not mock_all.called
        assert not mock_pending.called

        # 2. Exactly one response frame emitted
        assert len(frames) == 1
        payload = json.loads(frames[0].decode("utf-8").replace("data: ", "").strip())
        assert payload.get("content") == "Understood. I won't display the application list."
        assert "table_data" not in payload


@pytest.mark.asyncio
async def test_case_a_tamil_and_tanglish_responses():
    officer = _make_officer()
    db = AsyncMock()
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = None
    db.execute.return_value = mock_res

    with patch("backend.services.postgres.get_all_service_code_applications") as mock_all, \
         patch("backend.services.postgres.get_officer_applications") as mock_pending, \
         patch("backend.services.chatbot.orchestrator.save_chat_messages", new_callable=AsyncMock):

        # Tamil
        res_ta = await process_chat(
            message="விண்ணப்பங்களைக் காட்ட வேண்டாம்",
            session_id=str(uuid.uuid4()),
            officer=officer,
            db=db
        )
        assert not mock_all.called
        assert not mock_pending.called
        assert "புரிந்தது. விண்ணப்பப் பட்டியல் காட்டப்படாது." in res_ta.get("response", "")
        assert res_ta.get("table_data") is None

        # Tanglish
        res_tl = await process_chat(
            message="Applications kaatta vendam",
            session_id=str(uuid.uuid4()),
            officer=officer,
            db=db
        )
        assert not mock_all.called
        assert not mock_pending.called
        assert "Purinjadhu. Application list kaatta maatten." in res_tl.get("response", "")
        assert res_tl.get("table_data") is None


# =============================================================================
# Case B: Request Counts Without Records
# =============================================================================
def test_case_b_count_only_classification():
    count_only_queries = [
        "Don't show applications, only tell me the count.",
        "Do not list records; tell me how many are pending.",
        "No application details, just the total.",
        "don't list them, just give me the total number",
        "no application list, only count",
    ]
    for q in count_only_queries:
        assert _is_count_only_query(q) is True, f"Expected _is_count_only_query=True for '{q}'"
        assert is_prohibit_listing(q) is False, f"Expected is_prohibit_listing=False for count-only query '{q}'"


def test_case_b_table_data_suppression():
    dummy_structured_data = {
        "count": 44,
        "query_type": "Pending Applications",
        "applications": [
            {"application_number": "2026/0154/01/000001", "type": "ISD", "status": "Pending"},
            {"application_number": "2026/0153/01/000002", "type": "NISD", "status": "Pending"},
        ]
    }
    # For count-only queries, _build_table_data_raw must suppress table_data
    for q in [
        "Don't show applications, only tell me the count.",
        "Do not list records; tell me how many are pending.",
        "No application details, just the total."
    ]:
        td = _build_table_data_raw("all_service_code_applications", q, "user-1", dummy_structured_data)
        assert td is None, f"Expected table_data=None for count query '{q}'"


# =============================================================================
# Case C: Explicitly Request a List
# =============================================================================
def test_case_c_positive_listing_retained():
    positive_queries = [
        ("Show pending applications.", "all_service_code_applications"),
        ("List all ISD applications.", "isd_applications"),
        ("Show applications for ward 5.", "all_service_code_applications"),
    ]
    for q, expected_intent in positive_queries:
        assert is_prohibit_listing(q) is False, f"Expected is_prohibit_listing=False for '{q}'"
        assert parse_intent(q) == expected_intent, f"Expected '{expected_intent}' for '{q}'"


# =============================================================================
# Case D: Negation Within a Filter
# =============================================================================
def test_case_d_negation_within_filter_retains_positive_listing():
    filter_queries = [
        ("Show applications that are not rejected.", "all_service_code_applications"),
        ("Show applications without an owner mismatch.", "all_service_code_applications"),
        ("Don't show rejected applications; show pending ones.", "all_service_code_applications"),
    ]
    for q, expected_intent in filter_queries:
        assert is_prohibit_listing(q) is False, f"Expected is_prohibit_listing=False for filter query '{q}'"
        assert parse_intent(q) == expected_intent, f"Expected '{expected_intent}' for '{q}'"


# =============================================================================
# Case E: Correction of a Previous Request
# =============================================================================
def test_case_e_rescope_does_not_reinject_previous_list():
    chat_history = [
        {"role": "user", "content": "Show pending applications"},
        {"role": "assistant", "content": "Found 44 pending application(s)..."}
    ]
    current_turn = "Actually, don't show applications"
    # _rescope_list_followup must NOT merge or re-inject "Show pending applications" into the negative instruction
    rescoped = _rescope_list_followup(current_turn, chat_history)
    assert rescoped == current_turn, f"Expected '{current_turn}', but got '{rescoped}'"
    assert is_prohibit_listing(rescoped) is True
    assert parse_intent(rescoped) == "prohibit_listing"


@pytest.mark.asyncio
async def test_case_e_multiturn_correction_stops_listing():
    officer = _make_officer()
    db = AsyncMock()
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = None
    db.execute.return_value = mock_res

    chat_history = [
        {"role": "user", "content": "Show pending applications"},
        {"role": "assistant", "content": "Found 44 pending application(s)..."}
    ]

    with patch("backend.services.postgres.get_all_service_code_applications") as mock_all, \
         patch("backend.services.postgres.get_officer_applications") as mock_pending, \
         patch("backend.services.chatbot.orchestrator.save_chat_messages", new_callable=AsyncMock):

        res = await process_chat(
            message="Actually, don't show applications",
            session_id=str(uuid.uuid4()),
            officer=officer,
            db=db,
            chat_history=chat_history
        )

        # Listing queries must NOT be called on Turn 2
        assert not mock_all.called
        assert not mock_pending.called
        assert "Understood. I won't display the application list." in res.get("response", "")
        assert res.get("intent") == "prohibit_listing"
        assert res.get("table_data") is None
