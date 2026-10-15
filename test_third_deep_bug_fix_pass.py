"""
Regression test suite for SIS Chatbot — Third Deep Bug-Fix Pass: 15 Additional Findings.
Covers Issues 1 through 15 (including 15A and 15B) deterministically without external API or production dependencies.
"""
import re
from unittest.mock import MagicMock, patch

import pytest

from backend.schemas import OfficerContext
from backend.services.rag import (
    extract_application_number,
    extract_application_numbers,
)
from backend.services.chatbot.chat_smalltalk import (
    _APP_NO_HISTORY_RE,
    _GLOSSARY,
    _GLOSSARY_ALIAS,
    _MUTATION_OBJECT_RE,
    _field_visit_rule_topic,
    _igrs_can_rule_topic,
    _is_other_officer_request,
    _resolve_unknown_from_confirmation,
    _special_scope_kind,
    _unknown_from_reply,
    _unknown_from_word,
    _unknown_message_kind,
    _unknown_number_qualifier,
    _unknown_qualifier,
    _unknown_term,
    _workflow_steps_answer,
    _workflow_steps_topic,
)
from backend.services.chatbot import qualifier_guard


# =============================================================================
# Issue 1: _unknown_from_word confuses file formats with application channels
# =============================================================================
def test_issue_1_unknown_from_file_formats():
    # File formats and attachment descriptors must NOT be treated as unknown application channels
    file_format_queries = [
        "Show applications from csv",
        "Show applications from pdf",
        "List applications from file",
        "Show applications from upload",
        "Show applications from docx",
        "Show applications from sheet",
        "Show applications from report",
        "Show applications from deed",
    ]
    for q in file_format_queries:
        assert _unknown_from_word(q) is None, f"Expected None for '{q}', got {_unknown_from_word(q)}"

    # csv must never suggest CSC
    csv_reply = _unknown_from_reply("csv", "en")
    assert "Did you mean CSC?" not in csv_reply

    # Genuine channel typos should still suggest the correct channel
    sro_reply = _unknown_from_reply("sto", "en")
    assert "Sub-Registrar (SRO)" in sro_reply

    # Confirmation turn must not rewrite a file reference confirmation into live database CSC query
    chat_hist = [{"role": "assistant", "content": "I don't recognise 'csv' as a source of applications."}]
    assert _resolve_unknown_from_confirmation("yes", chat_hist) is None

    # Genuine channel confirmation still works
    chat_hist_valid = [{"role": "assistant", "content": "I don't recognise 'sto'. Did you mean Sub-Registrar (SRO)?"}]
    assert _resolve_unknown_from_confirmation("yes", chat_hist_valid) == "applications from sub_registrar"


# =============================================================================
# Issue 2: _unknown_qualifier rejects legitimate attachment references
# =============================================================================
def test_issue_2_unknown_qualifier_attachment_references():
    queries = [
        "Show pending applications in the uploaded file",
        "Show ISD applications in this CSV",
        "List pending applications from the PDF",
        "Show records in the uploaded document",
    ]
    for q in queries:
        unk_words = qualifier_guard.unknown_words(q)
        assert not unk_words, f"qualifier_guard flagged words {unk_words} for '{q}'"
        unk_q = _unknown_qualifier(q)
        assert not unk_q, f"_unknown_qualifier flagged {unk_q} for '{q}'"

    # Genuine unknown qualifier is still caught
    unknown_query = "Show pending applications in atlantis77"
    assert _unknown_qualifier(unknown_query) != []


# =============================================================================
# Issue 3: _FORMAT_UNSUPPORTED_RE wrongly rejects CSV-row queries
# =============================================================================
def test_issue_3_format_unsupported_csv_queries():
    # Legitimate CSV-row queries must NOT be rejected as format_unsupported
    supported_csv_queries = [
        "Return the CSV rows where status is pending",
        "Return data in CSV for ward 5",
        "Return CSV data from the uploaded file",
    ]
    for q in supported_csv_queries:
        assert _special_scope_kind(q) != "format_unsupported", f"Falsely rejected '{q}' as format_unsupported"

    # Truly unsupported raw export requests MUST still be rejected
    unsupported_export_queries = [
        "Return the result as CSV",
        "Export the result as CSV",
        "Return valid JSON",
        "convert this output to json",
    ]
    for q in unsupported_export_queries:
        assert _special_scope_kind(q) == "format_unsupported", f"Expected format_unsupported for '{q}'"


# =============================================================================
# Issue 4: _workflow_steps_topic hijacks questions about uploaded workflow documents
# =============================================================================
def test_issue_4_workflow_steps_attachment_docs():
    # Document-specific workflow questions must return None so attachment QA handles them
    doc_queries = [
        "What are the ISD workflow steps in this document?",
        "Explain the NISD workflow in workflow_notes.txt",
        "What happens after SIS in the uploaded document?",
        "Who approves ISD in the uploaded PDF?",
        "According to this document, what is the next NISD step?",
    ]
    for q in doc_queries:
        assert _workflow_steps_topic(q) is None, f"Expected None for '{q}', got {_workflow_steps_topic(q)}"

    # Genuinely general procedural workflow questions must still be answered
    assert _workflow_steps_topic("What are the ISD workflow steps?") == "ISD"
    assert _workflow_steps_topic("Explain the NISD workflow.") == "NISD"


# =============================================================================
# Issue 5: _field_visit_rule_topic hijacks historical questions and attachment follow-ups
# =============================================================================
def test_issue_5_field_visit_rule_history_reports():
    # Historical / report-specific inspection questions must return None
    report_queries = [
        "Who did the field visit?",
        "Who conducted the field inspection in this report?",
        "Why was a field inspection required here?",
        "What happened during the field inspection?",
        "Does this report say an inspection took place?",
    ]
    for q in report_queries:
        assert _field_visit_rule_topic(q) is None, f"Expected None for '{q}', got {_field_visit_rule_topic(q)}"

    # General procedural rules must still be handled
    general_rule = _field_visit_rule_topic("What are the general field-visit rules for NISD?")
    assert general_rule is not None


# =============================================================================
# Issue 6: Application-number formats are inconsistent across extractors
# =============================================================================
def test_issue_6_app_number_separators():
    test_cases = [
        ("2026/0154/28/001280", "2026/0154/28/001280"),
        ("2026 / 0154 / 28 / 001280", "2026/0154/28/001280"),
        ("2026_0154_28_001280", "2026/0154/28/001280"),
        ("field_inspection_report_2026_0154_28_001280.docx", "2026/0154/28/001280"),
        ("(2026/0154/28/001280).", "2026/0154/28/001280"),
    ]
    for text, expected in test_cases:
        extracted = extract_application_number(text)
        assert extracted == expected, f"Failed extract_application_number on '{text}': got {extracted}, expected {expected}"
        extracted_all = extract_application_numbers(text)
        assert expected in extracted_all, f"Failed extract_application_numbers on '{text}': got {extracted_all}"
        assert _MUTATION_OBJECT_RE.search(text), f"_MUTATION_OBJECT_RE failed to match '{text}'"
        assert _APP_NO_HISTORY_RE.search(text), f"_APP_NO_HISTORY_RE failed to match '{text}'"


# =============================================================================
# Issue 7: _unknown_number_qualifier misses document-structure locators
# =============================================================================
def test_issue_7_unknown_number_locators():
    locators = [
        "Show applications line 4",
        "Show applications in lines 4",
        "Show applications section 2",
        "Show applications paragraph 3",
        "Show applications column 5",
        "Show applications item 2",
        "Show applications point 4",
        "Show applications step 3",
        "Show applications stage 2",
        "Show applications part 1",
        "Show applications table 2",
        "Show applications sheet 2",
        "Show applications file 2",
        "Show applications in ward 5",
        "Show applications for block 3",
    ]
    for q in locators:
        assert _unknown_number_qualifier(q) is None, f"Incorrectly flagged locator as unknown number in '{q}'"

    # Bare numbers with no locator or geographic unit should still be flagged
    assert _unknown_number_qualifier("Show applications in 99") == "99"


# =============================================================================
# Issue 8: IGRS Form 6 glossary entry may be unreachable
# =============================================================================
def test_issue_8_igrs_form_6_glossary_reachability():
    queries = [
        "What is IGRS Form 6?",
        "Explain Form 6.",
        "What does IGRS Form 6 refer to?",
        "படிவம் 6 என்றால் என்ன?",
    ]
    for q in queries:
        topic = _workflow_steps_topic(q)
        assert topic in ("GLOSS:form6", "GLOSS:igrsform6", "GLOSS:igrsform6number"), f"Failed to resolve topic for '{q}', got {topic}"
        ans = _workflow_steps_answer(topic, is_tamil=False)
        assert ans and ("Form 6" in ans or "form 6" in ans.lower() or "form-6" in ans.lower()), f"Failed answer for '{q}': {ans}"

    # Negative test: Unrelated inputs must not map to form6
    assert _workflow_steps_topic("What is form 16?") != "GLOSS:form6"
    assert _workflow_steps_topic("What is table 6?") != "GLOSS:form6"


# =============================================================================
# Issue 9: _igrs_can_rule_topic intercepts listing and counting questions
# =============================================================================
def test_issue_9_can_rule_listing_count_masked():
    # Listing, counting, or partial extraction queries must NOT be intercepted by CAN rule topic
    data_queries = [
        "Show applications with a 15-digit CAN.",
        "How many applications have a 12-digit CAN?",
        "What are the last 4 digits of the CAN number?",
        "What are the first 3 digits of CAN?",
    ]
    for q in data_queries:
        assert _igrs_can_rule_topic(q) is None, f"Expected None for '{q}', got {_igrs_can_rule_topic(q)}"

    # Genuinely procedural questions about CAN format rules MUST be recognized
    assert _igrs_can_rule_topic("What is the required CAN format?") is not None
    assert _igrs_can_rule_topic("Is CAN 15 digits?") is not None
    assert _igrs_can_rule_topic("What is CAN?") is not None


# =============================================================================
# Issue 10: _OTHER_OFFICER_RE falsely blocks citizen and document-party queries
# =============================================================================
def test_issue_10_other_officer_citizen_names():
    # Citizen, applicant, vendor, or deed party queries must NOT be treated as cross-officer queries
    citizen_queries = [
        "Show applications submitted by Thiru Murugan.",
        "Applications filed by Smt Lakshmi.",
        "Was the deed executed by Mr Raman?",
        "Was the land sold by Mrs Lakshmi?",
    ]
    for q in citizen_queries:
        assert not _is_other_officer_request(q), f"Falsely flagged citizen query as officer request: '{q}'"
        assert _special_scope_kind(q) != "officer", f"_special_scope_kind flagged as officer: '{q}'"

    # Genuine other officer requests MUST still be blocked
    officer_queries = [
        "Show applications handled by SIS Suresh",
        "Show applications handled by officer Ramesh",
        "Applications for surveyor Senthil",
    ]
    for q in officer_queries:
        assert _is_other_officer_request(q), f"Failed to flag genuine officer query: '{q}'"


# =============================================================================
# Issue 11: _UNHELD_RE rejects supported SLA and Collector questions
# =============================================================================
def test_issue_11_unheld_holidays_sla_collector():
    # SLA calculations with holidays, Collector road addresses, and Collector appeal roles must NOT be unheld
    supported_queries = [
        "Are holidays counted in the 15 working days SLA?",
        "How are working days calculated when holidays occur?",
        "What is the Collector's role in survey appeals?",
        "What does the Collector do in the appeal workflow?",
        "Applications on Collector Office Road",
    ]
    for q in supported_queries:
        assert _special_scope_kind(q) != "unheld", f"Falsely rejected supported query as unheld: '{q}'"

    # Genuinely unheld requests must still be rejected
    unheld_queries = [
        "What are your office hours on Sunday?",
        "What is the holiday list for next year?",
    ]
    for q in unheld_queries:
        assert _special_scope_kind(q) == "unheld", f"Expected unheld for '{q}'"


# =============================================================================
# Issue 12: _ASSISTANT_IDENTITY_RE intercepts questions starting with “What are you…”
# =============================================================================
def test_issue_12_assistant_identity_disambiguation():
    # Operational queries about what the assistant is displaying, comparing, or counting must NOT be identity queries
    operational_queries = [
        "What are you showing in this table?",
        "What are you comparing here?",
        "What are you counting as overdue?",
        "What are you reading from the PDF?",
    ]
    for q in operational_queries:
        assert _special_scope_kind(q) != "identity", f"Falsely classified operational query as identity: '{q}'"

    # Genuine identity questions MUST be recognized
    identity_queries = [
        "Who are you?",
        "What are you?",
    ]
    for q in identity_queries:
        assert _special_scope_kind(q) == "identity", f"Failed to classify identity query: '{q}'"


# =============================================================================
# Issue 13: _FORECAST_RE blocks survey projections and legitimate workflow questions
# =============================================================================
def test_issue_13_forecast_projections_workflow():
    # Survey projections and legitimate workflow questions must NOT be blocked as forecast
    legit_queries = [
        "What is the map projection used in survey?",
        "Explain Cassini projection.",
        "How will applications be approved?",
        "When will an ISD application be completed?",
    ]
    for q in legit_queries:
        assert _unknown_message_kind(q) != "forecast", f"Falsely classified as forecast: '{q}'"

    # Genuinely speculative future outcome prediction requests MUST be classified as forecast
    forecast_queries = [
        "Will this application be approved?",
        "Can you predict the date this application will be approved?",
    ]
    for q in forecast_queries:
        assert _unknown_message_kind(q) == "forecast", f"Expected forecast for '{q}'"


# =============================================================================
# Issue 14: _UNSUPPORTED_SCRIPT_RE rejects Tamil/English input ending with । or ॥
# =============================================================================
def test_issue_14_unsupported_script_danda():
    # English and Tamil text ending with Devanagari danda / double danda must NOT be rejected
    valid_danda_inputs = [
        "This is an English sentence ending with \u0964",
        "இது ஒரு தமிழ் வாக்கியம்\u0964",
        "What is ISD\u0965",
    ]
    for text in valid_danda_inputs:
        assert _unknown_message_kind(text) != "script", f"Falsely rejected text with danda as script: '{text}'"

    # Genuinely unsupported script (e.g. Hindi Devanagari sentences) MUST be rejected
    hindi_text = "यह एक हिंदी वाक्य है"
    assert _unknown_message_kind(hindi_text) == "script", f"Expected script for Hindi text: '{hindi_text}'"

    # Normal Tamil and English text must remain supported
    assert _unknown_message_kind("Hello, how are you?") is None
    assert _unknown_message_kind("வணக்கம், உதவி தேவை.") is None


# =============================================================================
# Issue 15A: Potential KeyError in _workflow_steps_answer
# =============================================================================
def test_issue_15a_workflow_steps_answer_keyerror():
    # Unsupported topic must not raise KeyError and should return a fallback message
    ans_unknown_topic = _workflow_steps_answer("nonexistent_workflow_topic", is_tamil=False)
    assert ans_unknown_topic is not None
    assert "No workflow steps are recorded for 'nonexistent_workflow_topic'" in ans_unknown_topic

    ans_unknown_topic_ta = _workflow_steps_answer("nonexistent_workflow_topic", is_tamil=True)
    assert ans_unknown_topic_ta is not None
    assert "பணிப்பாய்வு விவரம் கிடைக்கவில்லை." in ans_unknown_topic_ta

    # Supported topics in English and Tamil (and brief)
    for topic in ("ISD", "NISD", "ALL"):
        ans_en = _workflow_steps_answer(topic, is_tamil=False)
        assert ans_en and len(ans_en) > 20
        ans_ta = _workflow_steps_answer(topic, is_tamil=True)
        assert ans_ta and len(ans_ta) > 20
        ans_brief = _workflow_steps_answer(topic, is_tamil=False, brief=True)
        assert ans_brief and len(ans_brief) > 20


# =============================================================================
# Issue 15B: _unknown_term fails for short acronyms and has broad stem-collision behavior
# =============================================================================
def test_issue_15b_unknown_term_acronyms_stem_collision():
    # Known short SIS acronyms and domain terms must NOT be flagged as unknown terms
    assert _unknown_term("What is RERA?") is None
    assert _unknown_term("What is DTCP?") is None
    assert _unknown_term("What is TSLR?") is None
    assert _unknown_term("What is ISD?") is None
    assert _unknown_term("What is NISD?") is None

    # Words like "internet" must not collide via stem overlap with internal/internship; properly detected as unknown term
    assert _unknown_term("What is internet?") == "internet"

    # Genuinely unknown 4-letter term MUST be recognized as unknown term
    unk = _unknown_term("What is zzzz?")
    assert unk == "zzzz", f"Expected 'zzzz', got {unk}"
