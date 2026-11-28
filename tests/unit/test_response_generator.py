"""
Unit tests for Intelligent Response Generator (Phase 7).

Verifies:
1. Natural English responses (single & multi-record).
2. Natural Tamil responses in Tamil script (தமிழ்), preserving technical identifiers in Latin script.
3. Tanglish query handling with fluent Tamil script output.
4. Preservation of exact identifiers (application numbers, survey numbers, service codes).
5. Evidence verification (anti-hallucination, ungrounded number removal, count reconciliation).
6. Explanations of missing records and untracked fields.
7. Accessible HTML table rendering with total count integrity.
8. Proactive domain-aligned follow-up suggestions.
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from backend.schemas import OfficerContext
from backend.services.chatbot.response_generator import (
    ResponseContext,
    VerificationReport,
    GeneratedResponse,
    verify_response_evidence,
    render_accessible_table,
    format_single_record_summary,
    explain_missing_or_untracked,
    generate_proactive_followups,
    generate_intelligent_response,
    STATUS_TRANSLATIONS_TA,
    STAGE_TRANSLATIONS_TA,
    SERVICE_CODE_MAP,
)


@pytest.fixture
def sample_officer():
    import uuid
    return OfficerContext(
        officer_id=uuid.uuid4(),
        employee_id="SIS-001",
        name="Arun Kumar",
        email="arun@sis.tn.gov.in",
        designation="Sub Inspector Surveyor",
        jurisdiction_type="ward",
        jurisdiction_name="Ward 28, Ambattur",
        jurisdiction_ids=[uuid.uuid4()],
    )


# ─────────────────────────────────────────────────────────────────────────────
# 1. Multi-Record Table Formatting & Count Preservation
# ─────────────────────────────────────────────────────────────────────────────

class TestTableRendering:
    def test_render_accessible_table_english(self):
        records = [
            {
                "application_number": "2026/0154/28/000001",
                "type": "ISD",
                "status": "pending",
                "stage": "SIS",
                "applicant_name": "Ramanathan",
            },
            {
                "application_number": "2026/0154/28/000002",
                "type": "ISD",
                "status": "in_progress",
                "stage": "SD",
                "applicant_name": "Suresh",
            },
        ]
        html = render_accessible_table(records, total_count=10, language="en")
        assert "<table class='data-table'>" in html
        assert "<th>Application Number</th>" in html
        assert "<th>Status</th>" in html
        assert "Showing <strong>2</strong> of <strong>10</strong> total applications" in html
        assert "2026/0154/28/000001" in html
        assert "status-pending" in html
        assert "status-in_progress" in html

    def test_render_accessible_table_tamil(self):
        records = [
            {
                "application_number": "2026/0154/28/000001",
                "type": "ISD",
                "status": "pending",
                "stage": "SIS",
                "applicant_name": "செல்வராஜ்",
            }
        ]
        html = render_accessible_table(records, total_count=1, language="ta")
        assert "<table class='data-table'>" in html
        assert "<th>விண்ணப்ப எண்</th>" in html
        assert "<th>நிலை</th>" in html
        assert "<th>கட்டம்</th>" in html
        assert "மொத்தம் <strong>1</strong> விண்ணப்பங்கள் கண்டறியப்பட்டன" in html
        # Identifier in Latin script preserved
        assert "2026/0154/28/000001" in html
        # Status translated in chip
        assert STATUS_TRANSLATIONS_TA["pending"] in html


# ─────────────────────────────────────────────────────────────────────────────
# 2. Single Record Formatting & Multilingual Generation
# ─────────────────────────────────────────────────────────────────────────────

class TestSingleRecordFormatting:
    def test_single_record_summary_english_concise(self):
        rec = {
            "application_number": "2026/0154/28/000001",
            "type": "ISD",
            "status": "pending",
            "stage": "SIS",
            "applicant_name": "Kandasamy",
            "survey_no": "12/3A",
        }
        res = format_single_record_summary(rec, language="en", detail_level="concise")
        assert "Application 2026/0154/28/000001 (ISD)" in res
        assert "currently pending at the SIS desk" in res

    def test_single_record_summary_tamil_script(self):
        rec = {
            "application_number": "2026/0154/28/000001",
            "type": "ISD",
            "status": "pending",
            "stage": "SIS",
            "applicant_name": "கந்தசாமி",
            "survey_no": "12/3A",
            "days_pending": 14,
        }
        res = format_single_record_summary(rec, language="ta", detail_level="detailed")
        # Tamil script labels
        assert "விண்ணப்ப எண்: 2026/0154/28/000001" in res
        assert "நிலை: நிலுவையில் உள்ளது" in res
        assert "தற்போதைய கட்டம்: நில அளவர் (SIS)" in res
        assert "கணக்கெண் / சர்வே எண்: 12/3A" in res
        assert "நிலுவை காலம்: 14 நாட்கள்" in res
        # Latin identifiers intact
        assert "2026/0154/28/000001" in res
        assert "12/3A" in res

    def test_single_record_tanglish_request_yields_tamil(self):
        rec = {
            "application_number": "2025/0153/28/000005",
            "type": "NISD",
            "status": "approved",
            "stage": "TAHSILDAR",
            "applicant_name": "Murugan",
        }
        res = format_single_record_summary(rec, language="tanglish", detail_level="concise")
        # Tanglish target yields Tamil script
        assert "விண்ணப்பம் 2025/0153/28/000005" in res
        assert "அங்கீகரிக்கப்பட்டது" in res


# ─────────────────────────────────────────────────────────────────────────────
# 3. Evidence Verification (Anti-Hallucination)
# ─────────────────────────────────────────────────────────────────────────────

class TestEvidenceVerification:
    def test_verify_ungrounded_application_number_removed(self, sample_officer):
        ctx = ResponseContext(
            query="Status of my applications",
            language="en",
            db_evidence={
                "applications": [
                    {"application_number": "2026/0154/28/000001", "status": "pending"}
                ]
            },
            officer=sample_officer,
        )
        # LLM generated text containing a fabricated application number
        hallucinated_text = (
            "Application 2026/0154/28/000001 is pending. Also check application 2026/0154/28/999999 which is approved."
        )
        report = verify_response_evidence(hallucinated_text, ctx)
        assert not report.is_valid
        assert "2026/0154/28/000001" in report.verified_text
        assert "2026/0154/28/999999" not in report.verified_text
        assert "[Unverified Application Number Removed]" in report.verified_text
        assert any("Removed ungrounded application number" in c for c in report.corrections)

    def test_verify_reconciles_count_mismatch(self, sample_officer):
        ctx = ResponseContext(
            query="How many pending ISD applications?",
            language="en",
            db_evidence={"count": 3},
            officer=sample_officer,
        )
        hallucinated_text = "You have 10 applications currently pending in Ward 28."
        report = verify_response_evidence(hallucinated_text, ctx)
        assert not report.is_valid
        # Count 10 reconciled to authoritative count 3
        assert "3 applications currently pending" in report.verified_text
        assert any("Reconciled count from 10 to authoritative 3" in c for c in report.corrections)

    def test_verify_provenance_categorization(self, sample_officer):
        ctx = ResponseContext(
            query="Explain ISD workflow for 2026/0154/28/000001",
            language="en",
            db_evidence={
                "count": 1,
                "applications": [{"application_number": "2026/0154/28/000001"}]
            },
            officer=sample_officer,
        )
        text = (
            "Application 2026/0154/28/000001 is pending in Ward 28.\n"
            "According to the official manual, the SLA for ISD applications is 30 days.\n"
            "Court litigation details are not found in the register.\n"
            "Please coordinate with the village administrative officer."
        )
        report = verify_response_evidence(text, ctx)
        prov = report.provenance
        assert any("2026/0154/28/000001" in s for s in prov["retrieved_facts"])
        assert any("official manual" in s for s in prov["document_rules"])
        assert any("not found" in s for s in prov["unknowns"])
        assert any("village administrative officer" in s for s in prov["explanations"])


# ─────────────────────────────────────────────────────────────────────────────
# 4. Explaining Missing Records & Untracked Fields
# ─────────────────────────────────────────────────────────────────────────────

class TestExplainMissingOrUntracked:
    def test_explain_app_not_found_english(self, sample_officer):
        ctx = ResponseContext(
            query="Status of 2026/0154/28/000999",
            language="en",
            officer=sample_officer,
        )
        msg = explain_missing_or_untracked(ctx)
        assert "Application <strong>2026/0154/28/000999</strong> was not found" in msg
        assert "Ward 28, Ambattur" in msg

    def test_explain_app_not_found_tamil(self, sample_officer):
        ctx = ResponseContext(
            query="2026/0154/28/000999 நிலை என்ன?",
            language="ta",
            officer=sample_officer,
        )
        msg = explain_missing_or_untracked(ctx)
        assert "விண்ணப்ப எண் <strong>2026/0154/28/000999</strong>" in msg
        assert "காணப்படவில்லை" in msg
        assert "ஆளுகைப் பதிவேட்டில்" in msg

    def test_explain_untracked_field_bank_account(self, sample_officer):
        ctx = ResponseContext(
            query="What is the applicant's bank account number?",
            language="en",
            officer=sample_officer,
        )
        msg = explain_missing_or_untracked(ctx)
        assert "does not track bank account details" in msg

    def test_explain_untracked_field_court_case_tamil(self, sample_officer):
        ctx = ResponseContext(
            query="இந்த இடத்திற்கு court stay order உள்ளதா?",
            language="tanglish",
            officer=sample_officer,
        )
        msg = explain_missing_or_untracked(ctx)
        assert "நீதிமன்ற வழக்கு அல்லது தடை உத்தரவு" in msg
        assert "பராமரிக்கப்படுவதில்லை" in msg


# ─────────────────────────────────────────────────────────────────────────────
# 5. Proactive Follow-up Questions
# ─────────────────────────────────────────────────────────────────────────────

class TestProactiveFollowups:
    def test_followups_for_pending_isd_english(self):
        ctx = ResponseContext(
            query="Show pending ISD applications",
            language="en",
            intent="pending_applications",
        )
        followups = generate_proactive_followups(ctx)
        assert len(followups) > 0
        assert any("field visits" in f.lower() for f in followups)

    def test_followups_for_pending_isd_tamil(self):
        ctx = ResponseContext(
            query="நிலுவையில் உள்ள ISD விண்ணப்பங்கள்",
            language="ta",
            intent="pending_applications",
        )
        followups = generate_proactive_followups(ctx)
        assert len(followups) > 0
        assert any("கள ஆய்வு" in f for f in followups)

    def test_followups_for_application_status(self):
        ctx = ResponseContext(
            query="Status of 2026/0154/28/000001",
            language="en",
            intent="application_status",
        )
        followups = generate_proactive_followups(ctx)
        assert any("workflow history" in f.lower() for f in followups)


# ─────────────────────────────────────────────────────────────────────────────
# 6. End-to-End Intelligent Response Generation
# ─────────────────────────────────────────────────────────────────────────────

class TestGenerateIntelligentResponse:
    def test_generate_multi_record_response(self, sample_officer):
        ctx = ResponseContext(
            query="Show my pending applications",
            language="en",
            db_evidence={
                "count": 2,
                "applications": [
                    {
                        "application_number": "2026/0154/28/000001",
                        "type": "ISD",
                        "status": "pending",
                        "stage": "SIS",
                        "applicant_name": "Arun",
                    },
                    {
                        "application_number": "2026/0154/28/000002",
                        "type": "ISD",
                        "status": "pending",
                        "stage": "SIS",
                        "applicant_name": "Kumar",
                    },
                ],
            },
            officer=sample_officer,
        )
        resp = generate_intelligent_response(ctx)
        assert isinstance(resp, GeneratedResponse)
        assert resp.verified is True
        assert "<table class='data-table'>" in resp.html
        assert "2026/0154/28/000001" in resp.html
        assert len(resp.followups) > 0

    def test_generate_single_record_tamil_response(self, sample_officer):
        ctx = ResponseContext(
            query="2026/0154/28/000001 status enna?",
            language="tanglish",
            db_evidence={
                "count": 1,
                "applications": [
                    {
                        "application_number": "2026/0154/28/000001",
                        "type": "ISD",
                        "status": "pending",
                        "stage": "SIS",
                        "applicant_name": "ராஜா",
                        "survey_no": "45/2",
                    }
                ],
            },
            officer=sample_officer,
        )
        resp = generate_intelligent_response(ctx)
        assert resp.language == "ta"
        assert "2026/0154/28/000001" in resp.text
        assert "நிலுவையில் உள்ளது" in resp.text
        assert "45/2" in resp.text
        assert len(resp.followups) > 0

    def test_generate_procedural_rag_response(self, sample_officer):
        ctx = ResponseContext(
            query="What is the SLA for ISD applications?",
            language="en",
            doc_evidence=[
                {
                    "content": "As per Tamil Nilam rules, ISD applications have an SLA of 30 days for completion.",
                    "citation": "Tamil Nilam Citizen Charter 2024",
                }
            ],
            officer=sample_officer,
        )
        resp = generate_intelligent_response(ctx)
        assert "Tamil Nilam Citizen Charter 2024" in resp.text
        assert "30 days" in resp.text
        assert len(resp.sources) > 0
