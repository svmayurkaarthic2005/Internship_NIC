"""
Unit tests for intent parsing and entity extraction.
"""
import pytest
from backend.services.rag import (
    parse_intent,
    extract_application_number,
    extract_survey_number,
    extract_submission_channels,
)


def test_intent_pending_applications():
    # Widened by equal-treatment rule to all service codes when asking for list with English 'applications'
    assert parse_intent("Show my pending applications") == "all_service_code_applications"
    assert parse_intent("show pending apps") == "all_service_code_applications"
    assert parse_intent("pending list kaattu") == "pending_applications"


def test_intent_application_status():
    assert parse_intent("What is the status of 2026/0154/28/000001?") == "application_status"
    assert parse_intent("2026/0153/28/000002 status enna") == "application_status"
    assert parse_intent("விண்ணப்ப எண் 2026/0154/28/000001 இன் நிலை என்ன?") == "application_status"


def test_intent_overdue_applications():
    assert parse_intent("Show overdue applications") == "overdue_applications"
    assert parse_intent("overdue files") == "overdue_applications"
    assert parse_intent("காலதாமதமான விண்ணப்பங்கள்") == "overdue_applications"
    assert parse_intent("தாமதமான விண்ணப்பங்கள்") == "overdue_applications"


def test_intent_field_visits():
    assert parse_intent("Show my field visits") == "field_visits"
    assert parse_intent("கள ஆய்வு அட்டவணை என்ன?") == "field_visits"
    assert parse_intent("When is my next site inspection?") == "fv_visit_plan"


def test_intent_service_code_lookup():
    assert parse_intent("What is service code 0153?") == "service_code_lookup"
    assert parse_intent("What is service code 0154?") == "service_code_lookup"
    assert parse_intent("0178 service code enna") == "service_code_lookup"


def test_extract_application_numbers():
    assert extract_application_number("Status of 2026/0154/28/000001") == "2026/0154/28/000001"
    assert extract_application_number("Details for 2026/0153/02/000002 please") == "2026/0153/02/000002"
    assert extract_application_number("No numbers here") is None


def test_extract_survey_numbers():
    assert extract_survey_number("Who owns survey number 1355?") == "1355"
    assert extract_survey_number("Survey 1355/1B12 details") == "1355/1B12"
    assert extract_survey_number("சர்வே எண் 145/2A") == "145/2A"


def test_extract_submission_channels():
    assert extract_submission_channels("Applications from CSC") == ["CSC"]
    assert extract_submission_channels("Show citizen portal applications") == ["citizen"]
    assert extract_submission_channels("Sub registrar office files") == ["sub_registrar"]
