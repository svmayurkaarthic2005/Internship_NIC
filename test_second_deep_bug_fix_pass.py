"""
Regression test suite for SIS Chatbot — Second Deep Bug-Fix Pass: 15 Additional Findings.
Covers Issues 1 through 15 deterministically without external API or production dependencies.
"""
import re
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.schemas import OfficerContext
from backend.services.chatbot.chat_smalltalk import (
    _GREETING_KIND_DESC,
    _POLITE_PREFIX,
    _contentless_reply,
    _fee_lookup_data,
    _fee_lookup_text,
    _greeting_kind,
    _is_capability_question,
    _is_mutation_request,
    _is_out_of_scope,
    _unknown_from_reply,
    _unknown_number_qualifier,
    _unrecognised_word_message,
    _wfh_fmt_dt,
    _workflow_history_answer,
    _workflow_steps_answer,
    _workflow_steps_topic,
)
from backend.services.chatbot.recheck import is_pushback


def _make_dummy_officer():
    import uuid
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
# Issue 1: Glossary alias can cause KeyError: 'subdivisions'
# =============================================================================
def test_issue_1_glossary_plural_hyphen_and_keyerror():
    # Singular, plural, hyphenated variants
    t1 = _workflow_steps_topic("What are subdivisions?")
    assert t1 == "GLOSS:subdivision"
    ans1 = _workflow_steps_answer(t1, is_tamil=False)
    assert "Sub-division" in ans1 or "sub-division" in ans1.lower()

    t2 = _workflow_steps_topic("Explain sub-divisions.")
    assert t2 == "GLOSS:subdivision"
    ans2 = _workflow_steps_answer(t2, is_tamil=False)
    assert "Sub-division" in ans2 or "sub-division" in ans2.lower()

    t3 = _workflow_steps_topic("What is a subdivision?")
    assert t3 == "GLOSS:subdivision"
    ans3 = _workflow_steps_answer(t3, is_tamil=False)
    assert "Sub-division" in ans3 or "sub-division" in ans3.lower()

    # Tamil variant
    ans_ta = _workflow_steps_answer(t1, is_tamil=True)
    assert "உட்பிரிவு" in ans_ta

    # Unknown glossary topic must never raise KeyError
    ans_unknown = _workflow_steps_answer("GLOSS:completely_unknown_term", is_tamil=False)
    assert "completely_unknown_term" in ans_unknown or "definition" in ans_unknown.lower()

    ans_unknown_ta = _workflow_steps_answer("GLOSS:completely_unknown_term", is_tamil=True)
    assert "பதிவேட்டில் இல்லை" in ans_unknown_ta


# =============================================================================
# Issue 2: Capability cues incorrectly match ordinary questions
# =============================================================================
def test_issue_2_capability_cues_vs_ordinary_questions():
    # Ordinary questions containing "what is this" or "who is this" must NOT be capability questions
    assert not _is_capability_question("What is this application?")
    assert not _is_capability_question("What is this application's status?")
    assert not _is_capability_question("What is this survey number's patta?")
    assert not _is_capability_question("What is this document about?")
    assert not _is_capability_question("Who is this applicant?")
    assert not _is_capability_question("Who is this owner?")

    # Genuine capability or identity questions must be recognised
    assert _is_capability_question("What can you do?")
    assert _is_capability_question("Who are you?")
    assert _is_capability_question("What can you help me with?")
    assert _is_capability_question("What is this bot?")


# =============================================================================
# Issue 3: Mutation detection uses raw substrings for object names
# =============================================================================
def test_issue_3_mutation_detection_bounded_matching():
    # Ordinary questions with substring collisions ("db" in "feedback", "all" in "overall", "ward" in listing)
    assert not _is_mutation_request("Schedule for tomorrow.")
    assert not _is_mutation_request("Can you show the schedule for tomorrow?")
    assert not _is_mutation_request("Show the overall status.")
    assert not _is_mutation_request("Show the ward details.")
    assert not _is_mutation_request("Show all pending applications.")
    assert not _is_mutation_request("Can you explain the database fields?")

    # Actual mutation attempts must be detected
    assert _is_mutation_request("modify db")
    assert _is_mutation_request("modify database")
    assert _is_mutation_request("delete all")
    assert _is_mutation_request("freeze ward 102")
    assert _is_mutation_request("delete application 2026/0153/28/001854")
    assert _is_mutation_request("மாற்று விண்ணப்பத்தை")


# =============================================================================
# Issue 4: Read-only column removal is incorrectly treated as a database mutation
# =============================================================================
def test_issue_4_column_removal_not_mutation():
    # Presentation-only column removal must NOT be treated as a database mutation
    assert not _is_mutation_request("Remove ward.")
    assert not _is_mutation_request("Drop block.")
    assert not _is_mutation_request("Remove survey number.")
    assert not _is_mutation_request("Hide owner.")
    assert not _is_mutation_request("Remove the status column.")
    assert not _is_mutation_request("Add ward and taluk.")
    assert not _is_mutation_request("Add block after ward.")

    # Negative tests: actual delete/drop of records/tables must remain blocked
    assert _is_mutation_request("delete application")
    assert _is_mutation_request("drop table")
    assert _is_mutation_request("remove record")
    assert _is_mutation_request("approve 2026/0153/28/001854")


# =============================================================================
# Issue 5: Mutation guard mishandles polite prefixes and shorthand
# =============================================================================
def test_issue_5_polite_prefixes_and_shorthand():
    # Requests with punctuation, shorthand, and polite phrases must be caught as mutations
    assert _is_mutation_request("Please, approve 2026/0153/28/001854")
    assert _is_mutation_request("Kindly, delete this application")
    assert _is_mutation_request("plz approve 2026/0153/28/001854")
    assert _is_mutation_request("can u delete this application")
    assert _is_mutation_request("could u approve this application")
    assert _is_mutation_request("will you modify this record")
    assert _is_mutation_request("Just delete this application")
    assert _is_mutation_request("could u plz approve 2026/0153/28/001854")

    # Read-only polite questions must remain read-only
    assert not _is_mutation_request("Please, show 2026/0153/28/001854")
    assert not _is_mutation_request("Can you please show all pending applications?")
    assert not _is_mutation_request("Could u explain the workflow?")


# =============================================================================
# Issue 6: ipl substring incorrectly triggers out-of-scope handling
# =============================================================================
def test_issue_6_out_of_scope_ipl_token_boundary():
    # Words containing 'ipl' like 'multiple' and 'triplicate' must NOT be out-of-scope
    assert not _is_out_of_scope("Are there multiple?")
    assert not _is_out_of_scope("Why are there multiple?")
    assert not _is_out_of_scope("Show multiple at once.")
    assert not _is_out_of_scope("Is it in triplicate?")

    # Genuine IPL questions must be detected as out-of-scope
    assert _is_out_of_scope("Who won the IPL match?")
    assert _is_out_of_scope("Tell me about IPL score.")
    assert _is_out_of_scope("ipl season schedule")


# =============================================================================
# Issue 7: Tamil and Tanglish workflow-history questions reach translated answers
# =============================================================================
@pytest.mark.asyncio
async def test_issue_7_tamil_tanglish_workflow_history():
    dummy_db = AsyncMock()
    officer = _make_dummy_officer()
    app_no = "2026/0154/28/000100"

    mock_hops = [
        {
            "from_stage": "SIS",
            "to_stage": "SD",
            "performed_at": datetime(2026, 3, 10, 10, 30, tzinfo=timezone.utc),
            "remarks": "Field inspection completed successfully",
            "rejection_reason": None,
        },
        {
            "from_stage": "SD",
            "to_stage": "DIS",
            "performed_at": datetime(2026, 3, 12, 14, 0, tzinfo=timezone.utc),
            "remarks": "Subdivision sketch prepared",
            "rejection_reason": None,
        },
    ]

    with patch("backend.services.chatbot.chat_smalltalk._cb") as mock_cb:
        mock_facade = MagicMock()
        mock_facade.get_workflow_history = AsyncMock(return_value=mock_hops)
        mock_cb.return_value = mock_facade

        # 1. History count in Tamil
        ans_count_ta = await _workflow_history_answer(
            dummy_db, officer, "எத்தனை பணிப்பாய்வு பதிவுகள் உள்ளன?", app_no, is_tamil=True
        )
        assert ans_count_ta is not None
        assert "2 பணிப்பாய்வு பதிவுகள் உள்ளன" in ans_count_ta

        # 2. First action who in Tamil
        ans_first_who = await _workflow_history_answer(
            dummy_db, officer, "முதல் நடவடிக்கையை யார் செய்தது?", app_no, is_tamil=True
        )
        assert ans_first_who is not None
        assert "முதல் நடவடிக்கையை" in ans_first_who

        # 3. Latest remark in Tamil
        ans_remark_ta = await _workflow_history_answer(
            dummy_db, officer, "கடைசி குறிப்பு என்ன?", app_no, is_tamil=True
        )
        assert ans_remark_ta is not None
        assert "Subdivision sketch prepared" in ans_remark_ta

        # 4. Tanglish query
        ans_tanglish = await _workflow_history_answer(
            dummy_db, officer, "mudhal action enna?", app_no, is_tamil=True
        )
        assert ans_tanglish is not None
        assert "முதல் பணிப்பாய்வு நடவடிக்கை" in ans_tanglish


# =============================================================================
# Issue 8: Workflow timestamp formatting crashes on strings or date objects
# =============================================================================
def test_issue_8_wfh_fmt_dt_types():
    # 1. Aware datetime
    aware_dt = datetime(2026, 3, 10, 10, 30, tzinfo=timezone.utc)
    res_aware = _wfh_fmt_dt(aware_dt)
    assert res_aware is not None
    # UTC 10:30 + 5:30 = IST 16:00
    assert "2026-03-10 16:00" in res_aware

    # 2. Naive datetime
    naive_dt = datetime(2026, 3, 10, 14, 45)
    res_naive = _wfh_fmt_dt(naive_dt)
    assert res_naive == "2026-03-10 14:45"

    # 3. ISO formatted string
    iso_str = "2026-03-10T10:30:00+00:00"
    res_iso = _wfh_fmt_dt(iso_str)
    assert "2026-03-10 16:00" in res_iso

    # 4. Plain date string
    date_str = "2026-03-10 12:15:00"
    res_str = _wfh_fmt_dt(date_str)
    assert "2026-03-10 12:15" in res_str

    # 5. Date object
    d_obj = date(2026, 3, 10)
    res_date = _wfh_fmt_dt(d_obj)
    assert res_date == "2026-03-10 00:00"

    # 6. None or malformed
    assert _wfh_fmt_dt(None) is None
    assert _wfh_fmt_dt("") is None
    assert _wfh_fmt_dt("not a date") is None


# =============================================================================
# Issue 9: Punctuation prevents the unknown-word helper from recognizing a typo
# =============================================================================
def test_issue_9_unknown_word_with_punctuation():
    # Typo word with punctuation must be recognized as unrecognised word
    assert _unrecognised_word_message("withwiky")
    assert _unrecognised_word_message("withwiky?")
    assert _unrecognised_word_message("withwiky.")
    assert _unrecognised_word_message("withwiky!")
    assert _unrecognised_word_message("withwiky...")

    # Known domain acronyms must NOT be flagged as unrecognised
    assert not _unrecognised_word_message("TSLR?")
    assert not _unrecognised_word_message("FMB?")
    assert not _unrecognised_word_message("Show all?")


# =============================================================================
# Issue 10: Typo matching fails for insertions and deletions
# =============================================================================
def test_issue_10_typo_matching_insertions_deletions():
    # Insertion typos (1 extra char)
    reply_srro = _unknown_from_reply("srro", language="en")
    assert "Sub-Registrar (SRO)" in reply_srro

    reply_cssc = _unknown_from_reply("cssc", language="en")
    assert "CSC" in reply_cssc

    # Deletion typo (1 missing char)
    reply_so = _unknown_from_reply("so", language="en")
    assert "Sub-Registrar (SRO)" in reply_so

    # Substitution typo
    reply_sto = _unknown_from_reply("sto", language="en")
    assert "Sub-Registrar (SRO)" in reply_sto

    # Unrelated word should not guess
    reply_unrelated = _unknown_from_reply("xyzbanana", language="en")
    assert "Did you mean" not in reply_unrelated


# =============================================================================
# Issue 11: Greeting handling is inconsistent for afternoon greetings
# =============================================================================
def test_issue_11_afternoon_greeting_and_boundaries():
    assert "afternoon" in _GREETING_KIND_DESC

    # English afternoon
    assert _greeting_kind("good afternoon") == "afternoon"
    assert _greeting_kind("afternoon") == "afternoon"

    # Tamil afternoon
    assert _greeting_kind("மதிய வணக்கம்") == "afternoon"
    assert _greeting_kind("பிற்பகல் வணக்கம்") == "afternoon"

    # Morning & Evening
    assert _greeting_kind("good morning") == "morning"
    assert _greeting_kind("காலை வணக்கம்") == "morning"
    assert _greeting_kind("good evening") == "evening"
    assert _greeting_kind("மாலை வணக்கம்") == "evening"

    # Farewell vs opening greeting
    assert _greeting_kind("good night") == "farewell"
    assert _greeting_kind("இரவு வணக்கம்") == "farewell"

    # Boundary test: substring in names should not trigger greeting
    assert _greeting_kind("பூமாலை") == "greeting"  # does not trigger "evening" due to lookaround


# =============================================================================
# Issue 12: Multi-channel fee lookup drops requested channels and null fees cause errors
# =============================================================================
@pytest.mark.asyncio
async def test_issue_12_fee_lookup_multi_channel_and_null_handling():
    dummy_db = AsyncMock()
    officer = _make_dummy_officer()

    async def mock_get_recent_fees(db, off, application_type=None, channel=None, limit=5):
        # Return test fee data
        return {
            "fee_lookup": {
                "application_type": application_type,
                "channel": channel,
                "latest": [
                    {
                        "application_number": f"2026/0154/28/0000{1 if channel=='csc' else 2}",
                        "application_type": application_type,
                        "channel": channel,
                        "submission_date": "2026-03-01",
                        "fee_amount": 60.0 if channel == "csc" else 100.0,
                        "payment_mode": "Online",
                    }
                ],
                "total_with_fee": 1,
                "newer_without_fee": 0,
                "total_applications": 1,
            },
            "query_type": "Recorded Fee",
        }

    with patch("backend.services.chatbot.chat_smalltalk._cb") as mock_cb:
        mock_facade = MagicMock()
        mock_facade.extract_submission_channels = MagicMock(return_value=["csc", "sub_registrar"])
        mock_facade.get_recent_fees = AsyncMock(side_effect=mock_get_recent_fees)
        mock_cb.return_value = mock_facade

        data = await _fee_lookup_data(dummy_db, officer, "What is the fee for CSC and SRO?")
        groups = data["fee_lookup"]["groups"]
        # Both requested channels must be queried and preserved
        channels_found = {g.get("channel") for g in groups}
        assert "csc" in channels_found
        assert "sub_registrar" in channels_found

    # Test Null fee amount sorting without crash
    null_fee_data = {
        "fee_lookup": {
            "groups": [
                {
                    "application_type": "ISD",
                    "channel": "csc",
                    "total_applications": 2,
                    "latest": [
                        {"fee_amount": None, "application_number": "APP1", "submission_date": "2026-03-01"},
                        {"fee_amount": 60.0, "application_number": "APP2", "submission_date": "2026-03-02"},
                    ],
                    "newer_without_fee": 0,
                }
            ]
        }
    }
    # Must not raise TypeError: '<' not supported between instances of 'float' and 'NoneType'
    text = _fee_lookup_text(null_fee_data, is_tamil=False)
    assert "₹60.00" in text

    # Check that schema jargon like `service_fees` table is removed
    code_fee_data = {
        "fee_lookup": {
            "groups": [],
            "code_fee_infos": [
                {
                    "service_name": "Patta Transfer",
                    "service_code": "0154",
                    "fee_display": "₹60",
                    "total_fee": "₹60",
                    "sla_days": "15 days",
                }
            ],
        }
    }
    code_text = _fee_lookup_text(code_fee_data, is_tamil=False)
    assert "service_fees table" not in code_text
    assert "departmental rules" in code_text


# =============================================================================
# Issue 13: Negative follow-ups are incorrectly interpreted as acknowledgements
# =============================================================================
def test_issue_13_negative_followup_corrections():
    # Negative pushback expressions
    assert is_pushback("apdi illa")
    assert is_pushback("appadi illa")
    assert is_pushback("illa apdi illa")
    assert is_pushback("அப்படி இல்லை")

    # In contentless reply after an assistant factual answer, it must NOT reply with "No problem"
    history = [
        {"role": "user", "content": "How many applications are pending?"},
        {"role": "assistant", "content": "There are 5 pending applications in your jurisdiction."},
    ]

    res_en = _contentless_reply("none", "en", message="apdi illa", chat_history=history)
    assert "No problem" not in res_en
    assert "Sorry about that" in res_en

    res_ta = _contentless_reply("none", "ta", message="அப்படி இல்லை", chat_history=history)
    assert "சரி, பரவாயில்லை" not in res_ta
    assert "மன்னிக்கவும்" in res_ta

    # Genuine standalone dismissal should still say No problem
    res_dismiss = _contentless_reply("none", "en", message="never mind", chat_history=history)
    assert "No problem" in res_dismiss


# =============================================================================
# Issue 14: Numeric qualifier detection rejects valid service codes, district codes, and ward filters
# =============================================================================
def test_issue_14_numeric_qualifiers():
    # 1. Valid service code 154 (0154 ISD) should NOT be flagged as unknown number
    assert _unknown_number_qualifier("Show applications for 154.") is None

    # 2. Valid district code 28 (Chennai) should NOT be flagged as unknown number
    assert _unknown_number_qualifier("Show applications in 28.") is None

    # 3. Ward filters with number preceding word
    assert _unknown_number_qualifier("Show applications in 5 ward.") is None
    assert _unknown_number_qualifier("Show applications in 5 வார்டு.") is None
    assert _unknown_number_qualifier("Show applications in 19 ward.") is None
    assert _unknown_number_qualifier("Show applications in 19th ward.") is None

    # 4. Genuinely unknown bare number qualifier should be caught
    assert _unknown_number_qualifier("Show applications in 999.") == "999"


# =============================================================================
# Issue 15: Module-level direct imports and monkeypatch compatibility
# =============================================================================
def test_issue_15_facade_monkeypatch_compatibility():
    import backend.services.chatbot as chatbot

    # Verify that monkeypatching parse_intent on chatbot facade affects _unknown_qualifier
    with patch.object(chatbot, "parse_intent", return_value="pending_applications"):
        from backend.services.chatbot.chat_smalltalk import _unknown_qualifier
        # Since parse_intent is patched to return a listing intent, qualifier check runs
        res = _unknown_qualifier("show applications in 999")
        assert res == ["999"]
