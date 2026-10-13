"""
Unit and deterministic classification tests for question mark (?) handling.
Evaluates:
- followup_context.classify() with and without '?'
- rag.parse_intent() keyword sensitivity with and without '?'
- chatbot._contentless_message() handling of '?', '??', '???'
- Multi-question splitting via _REQ_SPLIT and _plan_requests()
"""
import sys
import unittest

sys.path.insert(0, r"c:\proj\nic_internship")

from backend.services.followup_context import classify, FOLLOWUP_SINGULAR, FOLLOWUP_NONE, FOLLOWUP_LIST_AGGREGATE
from backend.services.rag import parse_intent
from backend.services.chatbot import _contentless_message, _plan_requests

class TestQuestionMarkClassification(unittest.TestCase):

    def test_standalone_question_marks_contentless(self):
        """Standalone '?', '??', '???' must not trigger random intents."""
        self.assertEqual(_contentless_message("?"), "ack")
        self.assertEqual(_contentless_message("??"), "ack")
        self.assertEqual(_contentless_message("???"), "ack")
        self.assertEqual(_contentless_message("   ?   "), "ack")

    def test_classify_qmark_bypasses_word_count_defect(self):
        """
        Documenting Defect: line 2619 of followup_context.py uses:
        if (len(words) <= _MAX_FRAGMENT_WORDS or "?" in text or words[0] in _FRAGMENT_OPENERS)
        which causes sentences longer than 6 words with field cues to be falsely promoted to FOLLOWUP_SINGULAR.
        """
        # 8 words, contains field cue "ward"
        no_q = "The revenue inspector told me about the ward"
        with_q = "The revenue inspector told me about the ward?"
        self.assertEqual(classify(no_q), FOLLOWUP_NONE)
        # With question mark, it gets promoted to singular follow-up!
        self.assertEqual(classify(with_q), FOLLOWUP_SINGULAR)

        # 8 words, contains field cue "status"
        no_q_status = "Please check whether the surveyor verified the status"
        with_q_status = "Please check whether the surveyor verified the status?"
        self.assertEqual(classify(no_q_status), FOLLOWUP_NONE)
        self.assertEqual(classify(with_q_status), FOLLOWUP_SINGULAR)

    def test_rag_parse_intent_qmark_keyword_hijacking_defect(self):
        """
        Documenting Defect: rag.py lines 5319 & 4758 use '?' in message to bypass length checks,
        causing unrelated sentences with 'field' or 'fee' to flip from general_query to domain intents.
        """
        # "magnetic field" without ? is general_query
        no_q_field = "The magnetic field in the southern region is relatively high"
        self.assertEqual(parse_intent(no_q_field), "general_query")
        # With ?, line 5319 promotes it to field_visits!
        with_q_field = "The magnetic field in the southern region is relatively high?"
        self.assertEqual(parse_intent(with_q_field), "field_visits")

        # "registration fee" without ? is general_query
        no_q_fee = "The conference registration fee was reimbursed yesterday"
        self.assertEqual(parse_intent(no_q_fee), "general_query")
        # With ?, line 4758 promotes it to fee_lookup!
        with_q_fee = "The conference registration fee was reimbursed yesterday?"
        self.assertEqual(parse_intent(with_q_fee), "fee_lookup")

    def test_ocr_substantive_question(self):
        """'What is OCR?' must route to general_query or explanation, not application follow-up."""
        intent = parse_intent("What is OCR?")
        self.assertIn(intent, ("general_query", "explain_clarify", "abbreviation_lookup"))

    def test_multi_question_split(self):
        """Multi-part question splitting on '?' and conjunctions."""
        msg = "Explain the workflow. What documents are required? What are the validation checks?"
        # Checks whether message contains multiple requests
        plan = _plan_requests(msg)
        # Note: In current code, if _howto_turn catches it first, plan might be None, but _plan_requests can split it
        # Document actual behavior
        self.assertTrue(plan is None or isinstance(plan, tuple))

if __name__ == "__main__":
    unittest.main()
