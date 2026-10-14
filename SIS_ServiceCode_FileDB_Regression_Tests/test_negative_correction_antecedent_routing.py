"""
Regression tests for FLINE-NEGATION-001a/b/c and NEGCORR-REGEX-GAP-001
(SIS_Context_And_PostUpload_Failure_Cases.jsonl).

Covers `backend.services.chatbot._route_negative_correction()`,
`_recent_nonattachment_claim()`, and `backend.services.chatbot.attachment_qa
.is_negative_correction()` -- the pure, no-DB parts of the fix. The parts
that need a real `db`/`session_id` (attachment_topic_active(),
_get_last_assistant_attachment_context(), _handle_negative_correction()'s
own recheck-comparison branch) were verified live through the chatbot UI
instead (see SIS_Context_And_PostUpload_Regression_Results.csv, CTX-01/02)
and are not re-asserted here.

No DB, no LLM. Run directly: `python test_negative_correction_antecedent_routing.py`.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.services.chatbot import attachment_qa as aq
from backend.services import chatbot


def _run(coro):
    return asyncio.run(coro)


# ── is_negative_correction() classification (NEGCORR-REGEX-GAP-001) ────────

def test_thats_incorrect_recognised():
    assert aq.is_negative_correction("that's incorrect") is True


def test_i_dont_think_they_are_the_same_recognised():
    assert aq.is_negative_correction("I don't think they are the same.") is True


def test_i_dont_think_the_files_have_the_same_service_codes_recognised():
    assert aq.is_negative_correction(
        "I don't think the files have the same service codes.") is True


def test_i_dont_think_the_definitions_are_correct_recognised():
    assert aq.is_negative_correction(
        "I don't think the F-Line and F-Line Appeal definitions are correct.") is True


def test_bare_i_dont_think_so_still_recognised():
    assert aq.is_negative_correction("No, I don't think so.") is True


def test_ordinary_questions_not_misclassified():
    """The widened regex must not start matching real SIS questions."""
    for q in ["what is my workload", "show my pending applications", "is survey 5 litigated"]:
        assert aq.is_negative_correction(q) is False, q


# ── _route_negative_correction() explicit-cue routing (no DB needed) ───────

def test_explicit_file_reference_routes_attachment():
    r = _run(chatbot._route_negative_correction("No, the other file.", [], None, "s", "en"))
    assert r == {"route": "attachment"}


def test_explicit_service_reference_routes_general():
    r = _run(chatbot._route_negative_correction(
        "I don't think the F-Line and F-Line Appeal definitions are correct.", [], None, "s", "en"))
    assert r == {"route": "general"}


def test_file_subject_with_service_word_still_routes_attachment():
    """FLINE-NEGATION-001b: 'the files HAVE the same service codes' names
    files as the grammatical subject even though 'service codes' also
    appears -- must not fall through unresolved."""
    r = _run(chatbot._route_negative_correction(
        "I don't think the files have the same service codes.", [], None, "s", "en"))
    assert r == {"route": "attachment"}


def test_not_a_negative_correction_returns_none():
    r = _run(chatbot._route_negative_correction("what is my workload", [], None, "s", "en"))
    assert r is None


# ── _recent_nonattachment_claim() history scanning ──────────────────────────

def test_claim_scan_ignores_csv_table_digits():
    """A rendered CSV table containing '0178' as a DATA VALUE must not be
    mistaken for a service-code CLAIM (FLINE-NEGATION-001a's root cause)."""
    hist = [{"role": "assistant",
             "content": "row 1: 0178\nrow 2: 0164\n[sis_svccode_fileB.csv, rows 1-5]"}]
    assert _run(chatbot._recent_nonattachment_claim(hist)) is None


def test_claim_scan_finds_real_service_code_answer():
    hist = [{"role": "assistant", "content": "Service code 0185 = F-Line Appeal..."}]
    assert _run(chatbot._recent_nonattachment_claim(hist)) == "service_code"


def test_claim_scan_respects_3_turn_lookback():
    hist = [{"role": "assistant", "content": "Service code 0185 = F-Line Appeal..."}]
    hist += [{"role": "assistant", "content": "unrelated filler"} for _ in range(3)]
    assert _run(chatbot._recent_nonattachment_claim(hist)) is None


if __name__ == "__main__":
    failures = []
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS: {name}")
            except AssertionError as e:
                failures.append(name)
                print(f"FAIL: {name}\n  {e}")
    print(f"\n{len(failures)} failing")
    sys.exit(1 if failures else 0)
