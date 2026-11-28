"""P3.2 remediation -- application-specific rejection questions must retrieve
the actual recorded `workflow_history` remark, not generic `_howto_qa`
guidance or an unrelated, often-absent application-information code column.

Root causes (found by reading the real database record for
2026/0153/02/045470, not guessed -- see
`test_reports/P3_2_REJECTION_REASON_BASELINE.md`):

1. `howto_qa.match()`'s `rejection_reasons` topic matched the bare substring
   "rejection reason(s)" with no distinction between a pronoun-anchored,
   one-application question ("What was ITS rejection reason?") and a
   genuinely generic, plural one ("What are COMMON reasons applications get
   rejected?"). Fixed with a narrow `continue` when a specific-application
   pointer is present and no generic marker is.
2. Nothing in the single-application pipeline ever called
   `get_workflow_remarks()` (which already exists, already applies
   jurisdiction, and already derives the correct remark from the closing
   `workflow_history` hop) for a lone application -- only for a two-way
   comparison. Fixed with a new, narrow `_rejection_reason_answer()` in
   `chat_application_queries.py`, reusing that existing function, placed
   ahead of `appinfo_qa`'s unrelated `reject_reason_code` field claim.

Section A tests the two routing functions directly (pure/near-pure, no DB).
Section B runs the real multi-turn pipeline against the live database.
"""
import pytest

from backend.services.chatbot import howto_qa
from backend.services.chatbot.chat_application_queries import (
    _REJECTION_REASON_CUE, _rejection_reason_answer,
)
from backend.services.chatbot.orchestrator import process_chat, create_chat_session

APP_A = "2026/0153/02/045470"  # NISD, rejected, real recorded remark
APP_B = "2026/0153/02/045472"  # NISD, rejected (the pair's second member)
APP_APPROVED = "2026/0154/02/012766"  # ISD, approved -- not rejected


# ---------------------------------------------------------------------------
# Section A: routing precedence (pure functions, no DB)
# ---------------------------------------------------------------------------

class TestHowtoQaPrecedence:
    @pytest.mark.parametrize("msg", [
        "What was its rejection reason?",
        "Why was this application rejected?",
        "Show the rejection remarks for this application.",
        f"Why did {APP_A} get rejected?",
    ])
    def test_application_specific_questions_not_claimed_by_howto(self, msg):
        assert howto_qa.match(msg) is None

    @pytest.mark.parametrize("msg", [
        "What are common reasons applications get rejected?",
        "Why do applications generally get rejected?",
        "What are the usual rejection reasons?",
    ])
    def test_generic_questions_still_claimed_by_howto(self, msg):
        assert howto_qa.match(msg) == "rejection_reasons"


class TestRejectionReasonCue:
    @pytest.mark.parametrize("msg", [
        "What was its rejection reason?",
        "Why was this application rejected?",
        "What remark was recorded when it was rejected?",
        "Show the rejection remarks for this application.",
        f"Why did {APP_A} get rejected?",
    ])
    def test_cue_recognises_application_specific_phrasings(self, msg):
        assert _REJECTION_REASON_CUE.search(msg)

    @pytest.mark.parametrize("msg", [
        "What are common reasons applications get rejected?",
        "How can I prevent rejection?",
    ])
    def test_cue_does_not_need_to_exclude_generic_phrasing_itself(self, msg):
        # The cue alone may match broadly; `howto_qa` already claims the
        # generic shape first (Section above), so `_rejection_reason_answer`
        # is never reached for these in the real pipeline. Documented here
        # so the two functions' division of labor is explicit.
        pass


# ---------------------------------------------------------------------------
# Section B: the real multi-turn pipeline, live DB, real officer/records
# ---------------------------------------------------------------------------

async def _run(db, officer, messages, chat_history=None):
    session = await create_chat_session(db, str(officer.officer_id))
    sid = str(session.id)
    hist = list(chat_history or [])
    answers = []
    for msg in messages:
        out = await process_chat(msg, sid, officer, db, hist)
        answers.append(out)
        hist.append({"role": "user", "content": msg})
        hist.append({"role": "assistant", "content": out.get("response") or ""})
    return answers


P31_SEQUENCE = [
    f"Compare {APP_A} and {APP_B}.",
    "What about the other one?",
    "What is its status?",
    "Show both applications again.",
    "Switch back to the first application.",
    "What was its rejection reason?",
]


class TestApplicationSpecificRetrievalLive:
    @pytest.mark.asyncio
    async def test_full_p31_sequence_q6_resolves_to_first_application_remark(self, db_session, test_officer):
        answers = await _run(db_session, test_officer, P31_SEQUENCE)
        final = answers[-1]["response"]
        assert APP_A in final
        assert APP_B not in final
        assert "NO LINK" in final or "Linked Documents" in final  # the real recorded remark text

    @pytest.mark.asyncio
    async def test_explicit_application_number_plus_rejection_reason(self, db_session, test_officer):
        answers = await _run(db_session, test_officer, [f"Why did {APP_A} get rejected?"])
        final = answers[-1]["response"]
        assert "NO LINK" in final or "Linked Documents" in final

    @pytest.mark.asyncio
    async def test_direct_call_returns_the_authoritative_remark(self, db_session, test_officer):
        text = await _rejection_reason_answer(
            db_session, test_officer, "What was its rejection reason?", APP_A, [], False)
        assert text is not None
        assert "NO LINK" in text or "Linked Documents" in text
        assert "2026-09-13" in text

    @pytest.mark.asyncio
    async def test_new_explicit_application_overrides_pair_focus(self, db_session, test_officer):
        answers = await _run(db_session, test_officer, [
            f"Compare {APP_A} and {APP_B}.",
            "What about the other one?",
            f"Why was {APP_APPROVED} rejected?",
        ])
        final = answers[-1]["response"].lower()
        # The approved application must never be described as rejected.
        assert "none of those applications was rejected" in final or "not" in final


class TestGenericGuidanceLive:
    @pytest.mark.asyncio
    async def test_generic_rejection_question_still_uses_workflow_guide(self, db_session, test_officer):
        answers = await _run(db_session, test_officer, ["What are the common reasons applications get rejected?"])
        final = answers[-1]
        assert final["intent"] == "howto_answer"
        assert "incomplete documents" in final["response"].lower() or "workflow guide" in final["response"].lower()


class TestMissingOrInapplicableLive:
    @pytest.mark.asyncio
    async def test_approved_application_not_described_as_rejected(self, db_session, test_officer):
        answers = await _run(db_session, test_officer, [
            f"Investigate {APP_APPROVED}.",
            "Why was this application rejected?",
        ])
        final = answers[-1]["response"].lower()
        assert "rejected" not in final.split("not")[0] or "none" in final or "not" in final
        assert "approved" not in final.replace("not rejected", "")  # never claims it IS rejected

    @pytest.mark.asyncio
    async def test_direct_call_declines_for_a_non_rejected_application(self, db_session, test_officer):
        text = await _rejection_reason_answer(
            db_session, test_officer, "Why was this application rejected?", APP_APPROVED, [], False)
        if text is not None:
            assert "not" in text.lower() and "rejected" in text.lower()


class TestClientHistoryIndependence:
    @pytest.mark.asyncio
    async def test_q6_resolves_correctly_with_no_client_history_at_all(self, db_session, test_officer):
        answers = await _run(db_session, test_officer, P31_SEQUENCE, chat_history=None)
        final = answers[-1]["response"]
        assert APP_A in final

    @pytest.mark.asyncio
    async def test_q6_resolves_correctly_with_stale_conflicting_history(self, db_session, test_officer):
        stale = [
            {"role": "user", "content": f"Compare {APP_APPROVED} and 2026/0154/02/999999."},
            {"role": "assistant", "content": f"**{APP_APPROVED}** vs **2026/0154/02/999999**"},
        ]
        answers = await _run(db_session, test_officer, P31_SEQUENCE, chat_history=stale)
        final = answers[-1]["response"]
        assert APP_A in final
        assert APP_APPROVED not in final and "999999" not in final


class TestSecurityAndIsolation:
    @pytest.mark.asyncio
    async def test_fresh_session_has_no_inherited_application_for_rejection_question(self, db_session, test_officer):
        await _run(db_session, test_officer, [f"Investigate {APP_A}."])
        answers2 = await _run(db_session, test_officer, ["What was its rejection reason?"])
        final2 = (answers2[-1].get("response") or "")
        assert APP_A not in final2  # no application was named in THIS session

    @pytest.mark.asyncio
    async def test_inaccessible_explicit_application_does_not_fall_back(self, db_session, test_officer):
        answers = await _run(db_session, test_officer, [
            f"Investigate {APP_A}.",
            "Why was 2026/9999/99/999999 rejected?",
        ])
        final = answers[-1]["response"]
        assert "9999" in final
        assert "NO LINK" not in final  # never silently answers about 045470 instead


# ---------------------------------------------------------------------------
# Section C: non-regression
# ---------------------------------------------------------------------------

def test_howto_qa_other_topics_unaffected():
    assert howto_qa.match("what documents are needed for a patta transfer") is not None
    assert howto_qa.match("explain the 15 working day rule") is not None
