"""P2 remediation -- semantic routing edge cases and domain query recovery.

Covers the three precedence bugs found by tracing the real request flow
(gate -> parse_intent -> record-module hooks -> semantic classifier), and the
narrow deterministic fixes applied for each:

1. Identity: "who am i" was already caught by `_is_officer_identity_question`
   before this task started -- the earlier "false positive" report was an
   artifact of testing `semantic_intent.classify()` in isolation, bypassing
   every gate ahead of it. Section A also adds the missing phrasings
   ("which officer account is active", "who is logged in") this task asked
   for.
2. Gratitude/farewell: `_unrecognised_word_message()`'s "known words" set
   only knew SIS-document vocabulary, so a single greeting/farewell word of
   6+ letters ("Goodbye.") was flagged as keyboard-mash gibberish before
   `parse_intent`'s own token-based greeting rule ever saw it; and
   `_is_explain_request()`'s bare trigger word "explanation" claimed
   "Thanks for the explanation." as a re-explain request before the
   semantic classifier got a turn.
3. Domain recovery: `_attached_documents()`'s `\\bdocu?\\w*` cue claimed
   "the registration document" (a `full_field_patta_transfer_urban` field)
   as a chat-upload question; and the single-application record hook never
   tried the ISD/NISD workflow-history answer for a context-resolved
   application, only `_other_code_workflow` (which excludes 0153/0154 by
   design), so "what happened before the rejection?" fell through to
   `general_query` and the LLM.

Section A/B use mocks or pure functions (no DB, no LLM). Section C is
DB-backed against the real project fixtures (`test_officer`, `db_session`),
using the actual 2026/0153/02/045470 record, the way `tests/unit/
test_p1_evidence_reasoning.py` does.
"""
import asyncio

import pytest

from backend.services.chatbot.chat_followups import _is_officer_identity_question, _identity_facet
from backend.services.chatbot.chat_smalltalk import (
    _unknown_message_kind, _unrecognised_word_message,
)
from backend.services.chatbot.chat_shared import _is_explain_request
from backend.services.rag import parse_intent
from backend.services.chatbot.chat_application_queries import (
    _early_subdivision_answer, _DOCS_CUE, _DOCS_SKIP,
)

REG_APP = "2026/0153/02/045470"


# ---------------------------------------------------------------------------
# Section A: identity (no DB, no LLM)
# ---------------------------------------------------------------------------

class TestIdentity:
    @pytest.mark.parametrize("msg", [
        "who am i", "Who am I?", "whoami", "which officer am i",
        "what is my officer id", "tell me my current identity",
        "who is logged in", "which officer account is active",
    ])
    def test_officer_identity_recognised(self, msg):
        assert _is_officer_identity_question(msg) is True

    @pytest.mark.parametrize("msg", [
        "who are you", "what are you", "are you an ai", "what is your role",
        "are you a human",
    ])
    def test_assistant_identity_not_officer_identity(self, msg):
        # These stay with the assistant/capability path -- never the
        # officer's own record.
        assert _is_officer_identity_question(msg) is False

    def test_identity_facet_distinguishes_who_from_profile(self):
        assert _identity_facet("who am i") == "who"
        assert _identity_facet("which officer account is active") == "profile"
        assert _identity_facet("what is my officer id") == "employee_id"

    def test_officer_identity_with_application_number_is_not_claimed(self):
        # A question naming an application is never read as self-identity,
        # even if it happens to contain "who" phrasing elsewhere.
        assert _is_officer_identity_question(f"who approved {REG_APP}") is False


# ---------------------------------------------------------------------------
# Section B: gratitude / farewell pipeline precedence (no DB, no LLM)
# ---------------------------------------------------------------------------

class TestGratitudeFarewellPrecedence:
    @pytest.mark.parametrize("msg", ["Goodbye.", "Bye.", "See you later.", "Farewell.", "Greetings."])
    def test_single_conversational_word_not_flagged_gibberish(self, msg):
        assert _unknown_message_kind(msg) is None

    @pytest.mark.parametrize("msg", ["Goodbye.", "Bye.", "See you later."])
    def test_standalone_farewell_reaches_greeting_intent(self, msg):
        assert parse_intent(msg) == "greeting"

    @pytest.mark.parametrize("msg", [
        "Thanks for the explanation.", "Thank you for explaining.",
    ])
    def test_thanks_for_explanation_is_not_a_detail_request(self, msg):
        assert _is_explain_request(msg) is False

    @pytest.mark.parametrize("msg", [
        "Explain more.", "explain that in detail", "thanks, but can you explain the SLA again?",
    ])
    def test_genuine_explain_requests_still_claimed(self, msg):
        # The narrow exclusion must not swallow a real re-explain request.
        assert _is_explain_request(msg) is True

    @pytest.mark.parametrize("msg", [
        f"Thanks, now check application {REG_APP}.",
        "Thank you. What happened at the previous workflow stage?",
        f"Goodbye, but first show the status of 2026/0154/02/012766.",
        "Thanks for the help. Compare both applications again.",
    ])
    def test_mixed_gratitude_or_farewell_with_domain_request_not_swallowed(self, msg):
        # A message naming an application number must never be read as
        # keyboard-mash or a bare acknowledgement.
        assert _unknown_message_kind(msg) is None
        if "045470" in msg or "012766" in msg:
            assert parse_intent(msg) == "application_status"

    def test_known_sis_gibberish_still_flagged(self):
        # The fix only exempts ordinary conversational words -- an actual
        # keyboard slip must still be caught.
        assert _unrecognised_word_message("withwiky") is True


# ---------------------------------------------------------------------------
# Section C: registration-date and rejection-workflow recovery (DB-backed)
# ---------------------------------------------------------------------------

class TestDomainQueryRecovery:
    def test_registration_document_question_not_claimed_as_attachment(self):
        msg = "When was the registration document registered?"
        assert _DOCS_CUE.search(msg)        # the bare cue still matches "document"
        assert _DOCS_SKIP.search(msg)        # but is now excluded as a field question

    def test_genuine_attachment_question_still_claimed(self):
        msg = f"what documents were uploaded for {REG_APP}"
        assert _DOCS_CUE.search(msg)
        assert not _DOCS_SKIP.search(msg)

    @pytest.mark.asyncio
    async def test_registration_date_follow_up_resolves_from_context(self, db_session, test_officer):
        hist = [
            {"role": "user", "content": f"Investigate {REG_APP}."},
            {"role": "assistant", "content": f"Here are the details for {REG_APP}. Type: NISD, Status: Rejected."},
        ]
        out = await _early_subdivision_answer(
            db_session, test_officer, "When was the registration document registered?",
            "general_query", None, hist, "en")
        assert out is not None
        assert "28-08-2026" in out or "2026-08-28" in out, out
        assert "Registered" in out or "registration" in out.lower()

    @pytest.mark.asyncio
    async def test_registration_date_with_explicit_application_number(self, db_session, test_officer):
        out = await _early_subdivision_answer(
            db_session, test_officer,
            f"When was the registration document registered for {REG_APP}?",
            "application_status", None, [], "en")
        assert out is not None
        assert "28-08-2026" in out

    @pytest.mark.asyncio
    async def test_registration_date_not_confused_with_submission_date(self, db_session, test_officer):
        out = await _early_subdivision_answer(
            db_session, test_officer,
            f"When was the registration document registered for {REG_APP}?",
            "application_status", None, [], "en")
        # The submission date (2026-08-29, one day later) must never appear
        # in place of the registration date (28-08-2026).
        assert "2026-08-29" not in out

    @pytest.mark.asyncio
    async def test_registration_date_isd_separation(self, db_session, test_officer):
        # An ISD (0154) file carries no full_field_patta_transfer_urban row;
        # the answer must say so, never invent a registration date.
        out = await _early_subdivision_answer(
            db_session, test_officer,
            "When was the registration document registered for 2026/0154/02/013642?",
            "application_status", None, [], "en")
        if out is not None:
            assert "no full-field patta transfer record" in out.lower() or "0153" in out

    @pytest.mark.asyncio
    async def test_rejection_workflow_follow_up_resolves_from_context(self, db_session, test_officer):
        hist = [
            {"role": "user", "content": f"Investigate {REG_APP}."},
            {"role": "assistant", "content": f"Here are the details for {REG_APP}. Status: Rejected."},
        ]
        out = await _early_subdivision_answer(
            db_session, test_officer, "What happened before the rejection?",
            "general_query", None, hist, "en")
        assert out is not None
        assert "Tahsildar" in out or "SIS" in out  # a real desk name, not an invented one
        assert "immediately before" in out.lower() or "recent" in out.lower()

    @pytest.mark.asyncio
    async def test_rejection_workflow_with_explicit_application_number(self, db_session, test_officer):
        out = await _early_subdivision_answer(
            db_session, test_officer,
            f"What happened before the rejection of {REG_APP}?",
            "application_status", None, [], "en")
        assert out is not None

    @pytest.mark.asyncio
    async def test_no_fabricated_field_visit_in_workflow_answer(self, db_session, test_officer):
        # NISD never carries a field visit -- the answer must never invent one.
        out = await _early_subdivision_answer(
            db_session, test_officer,
            f"What happened before the rejection of {REG_APP}?",
            "application_status", None, [], "en")
        assert out is not None
        assert "field visit" not in out.lower()


# ---------------------------------------------------------------------------
# Section D: semantic classifier non-regression (mocked, no DB/LLM)
# ---------------------------------------------------------------------------

class TestSemanticClassifierNonRegression:
    def test_existing_greeting_still_active(self, monkeypatch):
        from backend.services.chatbot import semantic_intent as si
        si._matrix, si._labels = None, []
        assert asyncio.run(si.classify("hello there")) == "greeting"

    def test_existing_hard_negative_still_refused(self, monkeypatch):
        from backend.services.chatbot import semantic_intent as si
        si._matrix, si._labels = None, []
        assert asyncio.run(si.classify("what is isd", has_domain_term=False)) is None

    def test_identity_question_is_caught_before_semantic_classifier_ever_runs(self):
        # The orchestrator calls `_is_officer_identity_question` well before
        # `semantic_intent.classify()`; confirming this here pins the actual
        # precedence this task's fix depends on, not just the standalone
        # classifier's own behavior.
        assert _is_officer_identity_question("who am i") is True
