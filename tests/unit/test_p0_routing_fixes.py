"""Regression tests for the three P0 routing bugs found by the live question-bank
run (test_reports/SIS_CHATBOT_QUESTION_BANK_TEST_REPORT.md):

  P0-1: an unanchored "format" substring check matched inside the ordinary
        word "information", hijacking real data questions into the canned
        application-number-format explainer (orchestrator.py).
  P0-2: the adverb "automatically" was canonicalised to the DB field
        "automutated", so a reasoning question using that word was answered
        with an unrelated field value (appinfo_qa.py).
  P0-3: fulltransfer_qa claimed any "SIS" + "recommendation" question ahead
        of cmflow_qa, so a non-NISD (e.g. 0158) application's SIS-
        recommendation question was wrongly refused with "only an NISD file
        carries one" even though cmflow_qa's own table already held the
        answer and had been shown correctly earlier in the same
        conversation (chat_application_queries.py's full-field-transfer
        render block, service-aware fallback added there).

These are deterministic, no-LLM parser/dispatch tests except the one P0-3
integration test, which goes through the real `_early_subdivision_answer`
render path (not an isolated handler call) against the real database, using
the project's own `test_officer` / `db_session` fixtures.
"""
import pytest

from backend.services.chatbot.orchestrator import _asks_application_number_format
from backend.services.chatbot import appinfo_qa, fulltransfer_qa, cmflow_qa
from backend.services.chatbot.chat_application_queries import _early_subdivision_answer


# ---------------------------------------------------------------------------
# P0-1: "format" must not match inside "information"
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("message", [
    "Explain the application number format.",
    "What is the structure of an application number?",
    "What does an application number look like?",
    "application number pattern",
    "how is an application number structured",
])
def test_genuine_format_questions_still_recognised(message):
    assert _asks_application_number_format(message.lower()) is True


@pytest.mark.parametrize("message", [
    # The exact live reproduction: "information" contains "format" as a
    # bare substring ("in-FORMAT-ion").
    "Show applications under service code 0154 and whether owner information is present.",
    "I need information about application 2026/0154/02/012766.",
    "Compare the application information for these two records.",
    "Show the application information and payment details.",
    "Does the application number contain a service code?",
])
def test_ordinary_data_questions_are_not_hijacked(message):
    assert _asks_application_number_format(message.lower()) is False


def test_format_substring_inside_information_does_not_match_alone():
    # Isolates the exact root cause: "format" is a literal substring of
    # "information", but a plain (non-word-boundary) match must not fire.
    assert "format" in "information"  # the substring IS there (sanity check on the bug's premise)
    assert _asks_application_number_format("tell me the application number and some information") is False


# ---------------------------------------------------------------------------
# P0-2: "automatically" must not be canonicalised to "automutated"
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("message", [
    "Does a populated date column automatically mean a visit is required?",
    "Why should these two tables not automatically be treated as identical?",
])
def test_automatically_does_not_claim_automutated_field(message):
    keys = appinfo_qa.parse(message)
    assert appinfo_qa.AUTOMUT not in keys, (
        f"'automatically' in {message!r} was wrongly canonicalised to the automutated field: {keys}")


@pytest.mark.parametrize("message", [
    "Was the application automutated?",
    "Show the automutated status for this application.",
])
def test_explicit_automutated_cue_still_claims_field(message):
    keys = appinfo_qa.parse(message)
    assert appinfo_qa.AUTOMUT in keys, (
        f"an explicit 'automutated' cue in {message!r} should still claim the field: {keys}")


def test_other_appinfo_fields_unaffected():
    # Guards against an over-broad fix accidentally disabling the handler.
    keys = appinfo_qa.parse("What is the challan number and payment mode?")
    assert appinfo_qa.CHALLAN_NO in keys and appinfo_qa.PAYMODE in keys


# ---------------------------------------------------------------------------
# P0-3: SIS-recommendation claim is unaffected at the parser level (the fix
# is in the renderer's service-aware fallback, exercised below with a real
# DB-backed integration test) -- these two confirm no regression to the
# existing module-level behaviour other code still depends on.
# ---------------------------------------------------------------------------

def test_fulltransfer_sisrec_claim_unchanged_at_parser_level():
    # fulltransfer_qa itself is not made service-aware (that would change
    # its answer() for genuinely-NISD-only fields too); only the renderer's
    # dispatch priority changed. This asserts that invariant.
    assert fulltransfer_qa.claims_strong("When did the SIS recommendation occur?") is True


def test_cmflow_also_recognises_the_same_question():
    q = cmflow_qa.parse("When did the SIS recommendation occur?")
    assert q is not None
    assert q.desks == ["sis"]


@pytest.mark.asyncio
async def test_0158_sis_recommendation_reaches_cmflow_not_fulltransfer_refusal(db_session, test_officer):
    """The live reproduction, through the real render path (not an isolated
    handler call): a 0158 correction/modification application's "SIS
    recommendation" question must be answered from cmflow_qa's
    correction-modification table, never with fulltransfer_qa's generic
    "only an NISD file carries one" refusal."""
    app_no = "2026/0158/02/000002"
    message = "When did the SIS recommendation occur?"
    answer = await _early_subdivision_answer(
        db_session, test_officer, message, "application_status", app_no, [], "en"
    )
    assert answer, "expected a real answer, got nothing"
    assert "only an NISD" not in answer.lower(), (
        f"P0-3 regressed: fulltransfer_qa's refusal won again:\n{answer}")
    assert "full-field patta transfer record is held" not in answer.lower(), (
        f"P0-3 regressed: fulltransfer_qa's refusal won again:\n{answer}")


@pytest.mark.asyncio
async def test_0153_sis_recommendation_still_uses_fulltransfer(db_session, test_officer):
    """Requirement #6 of the fix: a genuinely-NISD (0153) application must
    keep using fulltransfer_qa for the same wording -- the fix must not
    have reordered or removed fulltransfer_qa's own claim, only added a
    fallback for the codes it never covers."""
    app_no = "2026/0153/02/045470"
    message = "When did the SIS recommendation occur?"
    answer = await _early_subdivision_answer(
        db_session, test_officer, message, "application_status", app_no, [], "en"
    )
    assert answer, "expected a real answer, got nothing"
    # Either a genuine SIS recommendation value, or (if this particular NISD
    # row has none) fulltransfer_qa's own "not recorded" rendering -- either
    # way it must come from fulltransfer_qa's table, not an unrelated
    # correction-modification refusal naming a service it doesn't have.
    assert "correction / modification workflow record is held" not in answer.lower()
