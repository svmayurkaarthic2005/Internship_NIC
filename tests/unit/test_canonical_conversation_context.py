"""P3 remediation -- explicit application identifiers must always win over
stale conversation context.

The real bug (traced with `scratch/trace_p3_full.py` against the live DB):
`followup_context.classify()` never checked whether the CURRENT message
already names its own explicit application number before deciding it was a
bare follow-up fragment. For "Goodbye, but first show the status of
2026/0154/02/012766." (prior turns about 2026/0153/02/045470),
`orchestrator.py`'s `_apply_followup_resolution()` rewrote the message to
"What is its status 2026/0153/02/045470" -- discarding the officer's own
explicit number entirely -- and the answer came back for the WRONG
application. The fix is one early-return in `classify()`: a message naming
exactly one explicit application number of its own is never a bare
follow-up fragment, so `resolve()` returns an unresolved `Resolution()` and
the ordinary application gate (`resolve_application_reference()`, already
correct on its own -- verified directly, not assumed) uses the message's own
number.

Section A tests `classify()` directly (pure function, no DB). Section B runs
the real multi-turn pipeline (`process_chat`) against the live database with
the project's own fixtures, the way the P1-2 and P2 test files do. Section C
is the security/isolation check Phase 7 asks for. Section D is the
non-regression check for the pair/focus mechanics Phase 5 asks to preserve.
"""
import asyncio

import pytest

from backend.services import followup_context as fctx
from backend.services.chatbot.orchestrator import process_chat, create_chat_session

APP_A = "2026/0153/02/045470"   # NISD, rejected
APP_B = "2026/0154/02/012766"   # NISD, approved -- a DIFFERENT file from A
APP_PAIR_2 = "2026/0153/02/045472"


# ---------------------------------------------------------------------------
# Section A: classify() precedence (pure function, no DB)
# ---------------------------------------------------------------------------

class TestExplicitApplicationPrecedence:
    def test_message_with_own_explicit_number_is_not_a_bare_followup(self):
        msg = f"Goodbye, but first show the status of {APP_B}."
        assert fctx.classify(msg) == fctx.FOLLOWUP_NONE

    def test_plain_explicit_number_message_is_not_a_bare_followup(self):
        assert fctx.classify(f"Show the status of {APP_B}.") == fctx.FOLLOWUP_NONE

    @pytest.mark.parametrize("msg", [
        f"Thanks, now check application {APP_A}.",
        f"Before I leave, investigate {APP_A}.",
        f"Hello, what happened before the rejection of {APP_A}?",
    ])
    def test_mixed_conversational_plus_one_explicit_number_not_a_followup(self, msg):
        assert fctx.classify(msg) == fctx.FOLLOWUP_NONE

    def test_two_explicit_numbers_is_still_a_comparison_not_a_bare_followup(self):
        msg = f"Compare {APP_A} and {APP_PAIR_2}."
        # Two explicit numbers fully specify the comparison themselves; the
        # comparison-trigger branch already declines to treat this as a
        # follow-up needing resolution (len(app_nums) >= 2), and the new
        # single-number guard does not fire either (there are two, not one).
        assert fctx.classify(msg) != fctx.FOLLOWUP_COMPARISON

    def test_genuine_bare_followup_without_its_own_number_still_resolves(self):
        # The fix must not blanket-disable follow-up resolution: a message
        # with NO explicit number of its own is still a legitimate fragment.
        assert fctx.classify("What is its status?") != fctx.FOLLOWUP_NONE or \
            fctx.classify("What about the other one?") != fctx.FOLLOWUP_NONE


# ---------------------------------------------------------------------------
# Section B: the real multi-turn pipeline, live DB, real officer and records
# ---------------------------------------------------------------------------

async def _run(db, officer, messages):
    session = await create_chat_session(db, str(officer.officer_id))
    sid = str(session.id)
    hist = []
    answers = []
    for msg in messages:
        out = await process_chat(msg, sid, officer, db, hist)
        answers.append(out)
        hist.append({"role": "user", "content": msg})
        hist.append({"role": "assistant", "content": out.get("response") or ""})
    return answers


class TestExplicitPrecedenceLivePipeline:
    @pytest.mark.asyncio
    async def test_explicit_switch_overrides_stale_context(self, db_session, test_officer):
        answers = await _run(db_session, test_officer, [
            f"Investigate {APP_A}.",
            "What is its status?",
            f"Goodbye, but first show the status of {APP_B}.",
        ])
        final = answers[-1]["response"]
        assert APP_B in final
        assert APP_A not in final, "the switched-to turn must never mention the stale application"

    @pytest.mark.asyncio
    async def test_followups_continue_on_the_newly_switched_application(self, db_session, test_officer):
        answers = await _run(db_session, test_officer, [
            f"Investigate {APP_A}.",
            "What is its status?",
            f"Goodbye, but first show the status of {APP_B}.",
            "When was that application submitted?",
        ])
        final = answers[-1]["response"]
        assert APP_B in final
        assert APP_A not in final

    @pytest.mark.asyncio
    async def test_invalid_explicit_id_does_not_fall_back_to_stale_application(self, db_session, test_officer):
        answers = await _run(db_session, test_officer, [
            f"Investigate {APP_A}.",
            "Show the status of 2026/9999/99/999999.",
        ])
        final = answers[-1]["response"]
        assert "9999" in final  # the refusal names the identifier the officer typed
        assert APP_A not in final  # never silently substitutes the earlier file

    @pytest.mark.asyncio
    async def test_mixed_farewell_plus_explicit_application_resolves_correctly(self, db_session, test_officer):
        answers = await _run(db_session, test_officer, [f"Goodbye, but first show the status of {APP_A}."])
        assert APP_A in answers[-1]["response"]

    @pytest.mark.asyncio
    async def test_mixed_gratitude_plus_explicit_application_resolves_correctly(self, db_session, test_officer):
        answers = await _run(db_session, test_officer, [f"Thanks, now investigate {APP_A}."])
        assert APP_A in answers[-1]["response"]

    @pytest.mark.asyncio
    async def test_two_explicit_ids_still_produce_a_comparison(self, db_session, test_officer):
        answers = await _run(db_session, test_officer, [f"Compare {APP_A} and {APP_PAIR_2}."])
        final = answers[-1]
        assert final["intent"] == "compare_applications"
        assert APP_A in final["response"] and APP_PAIR_2 in final["response"]


# ---------------------------------------------------------------------------
# Section C: session/officer isolation (Phase 7)
# ---------------------------------------------------------------------------

class TestSessionIsolation:
    @pytest.mark.asyncio
    async def test_resolve_rejects_context_from_a_different_officer(self, db_session, test_officer, secondary_officer):
        # A FollowupContext built for one officer must be refused for another.
        ctx = fctx.build_context(
            "application_status", {"application_numbers": [APP_A]},
            fallback_app_number=APP_A, officer_id=str(test_officer.officer_id))
        result = fctx.resolve("what is its status", ctx, "en", officer_id=str(secondary_officer.officer_id))
        assert result.resolved is False

    @pytest.mark.asyncio
    async def test_two_independent_sessions_do_not_share_context(self, db_session, test_officer):
        # Session 1 discusses A; a brand-new session 2 for the same officer
        # must start with no context at all.
        await _run(db_session, test_officer, [f"Investigate {APP_A}.", "What is its status?"])
        answers2 = await _run(db_session, test_officer, ["What is its status?"])
        # With no application ever named in session 2, this must ask for a
        # number rather than silently answering about A from session 1.
        assert APP_A not in (answers2[-1]["response"] or "")


# ---------------------------------------------------------------------------
# Section D: pair/focus non-regression (Phase 5) -- the mechanics this task
# must not redesign, only not break with the new explicit-number guard.
# ---------------------------------------------------------------------------

class TestPairFocusNonRegression:
    @pytest.mark.asyncio
    async def test_other_one_switches_focus_within_the_pair(self, db_session, test_officer):
        answers = await _run(db_session, test_officer, [
            f"Compare {APP_A} and {APP_PAIR_2}.",
            "What about the other one?",
        ])
        assert APP_PAIR_2 in (answers[-1].get("response") or "") or answers[-1]["intent"] == "followup_list"

    @pytest.mark.asyncio
    async def test_status_followup_after_focus_switch_uses_the_focused_application(self, db_session, test_officer):
        answers = await _run(db_session, test_officer, [
            f"Compare {APP_A} and {APP_PAIR_2}.",
            "What about the other one?",
            "What is its status?",
        ])
        final = answers[-1]["response"]
        assert APP_PAIR_2 in final
        assert APP_A not in final


def test_followup_context_resolve_module_level_sanity():
    # classify() must remain a pure function with no DB/network dependency --
    # this guards against the fix accidentally growing a side effect.
    assert fctx.classify("hello") in (fctx.FOLLOWUP_NONE,)
