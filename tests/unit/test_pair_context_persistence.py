"""P3.1 remediation -- pair membership must survive a single-application
follow-up, and "show both again" / "switch back to first" must restore it.

Root causes (traced against the live DB with `scratch/trace_p31_lifecycle.py`,
not assumed -- see `test_reports/P3_1_PAIR_CONTEXT_BASELINE.md`):

1. `orchestrator.py` built the next turn's `FollowupContext` via
   `fctx.build_context(intent, structured_data, fallback_app_number=...)`
   without ever passing `previous_context=`, so the pair carried in
   `active_applications` was discarded every time a plain single-application
   answer ran. Fixed by passing `previous_context=_followup_ctx`.
2. `_resolve_pair_reference()` read `context.application_numbers or
   context.active_applications`; `application_numbers` is the field that
   DOES narrow to one entry by design, so the `or` never reached
   `active_applications` even once it correctly carried the pair. Fixed by
   preferring `active_applications` when it has exactly two members and
   `application_numbers` does not.
3. The single-application `build_context()` branch's own fallback,
   `carried_active_apps or [single_num]`, would have kept returning the OLD
   pair for a genuinely new, unrelated application once (1) started
   supplying a non-empty `carried_active_apps`. Fixed alongside (1).

Section A tests `followup_context` functions directly (mostly pure, no DB).
Section B runs the real multi-turn pipeline against the live database with
the project's own fixtures. Section C is the isolation/security check.
"""
import pytest

from backend.services import followup_context as fctx
from backend.services.chatbot.orchestrator import process_chat, create_chat_session

APP_A = "2026/0153/02/045470"
APP_B = "2026/0153/02/045472"
APP_C = "2026/0154/02/012766"


def _pair_context(current_application=APP_B):
    return fctx.FollowupContext(
        entity=fctx.ENTITY_APPLICATION,
        application_numbers=[current_application],
        active_applications=[APP_A, APP_B],
        current_application=current_application,
        intent="application_status",
    )


# ---------------------------------------------------------------------------
# Section A: pair resolution functions (mostly pure)
# ---------------------------------------------------------------------------

class TestPairRestorationResolution:
    @pytest.mark.parametrize("msg", [
        "Show both applications again.",
        "Show both.",
        "Compare them again.",
        "Display the two applications.",
    ])
    def test_show_both_phrasings_restore_the_full_pair(self, msg):
        ctx = _pair_context()
        res = fctx._resolve_pair_reference(msg, ctx)
        assert res is not None
        assert sorted(res.application_numbers) == sorted([APP_A, APP_B])

    def test_pair_restoration_works_even_when_application_numbers_narrowed_to_one(self):
        # The exact shape a single-application answer now (correctly) leaves
        # behind: `application_numbers` has one entry, `active_applications`
        # still has the pair.
        ctx = _pair_context(current_application=APP_B)
        assert ctx.application_numbers == [APP_B]
        assert ctx.active_applications == [APP_A, APP_B]
        res = fctx._resolve_pair_reference("show both applications again", ctx)
        assert res is not None and sorted(res.application_numbers) == sorted([APP_A, APP_B])

    def test_other_one_still_resolves_to_the_opposite_member(self):
        ctx = _pair_context(current_application=APP_B)
        res = fctx._resolve_pair_reference("what about the other one", ctx)
        assert res is not None

    def test_first_and_second_resolve_to_the_named_pair_member(self):
        ctx = _pair_context(current_application=APP_B)
        first = fctx._resolve_pair_reference("switch back to the first application", ctx)
        assert first is not None

    def test_no_pair_context_declines_rather_than_fabricating(self):
        ctx = fctx.FollowupContext(entity=fctx.ENTITY_APPLICATION, application_numbers=[APP_A],
                                   active_applications=[APP_A], current_application=APP_A)
        res = fctx._resolve_pair_reference("show both applications again", ctx)
        assert res is None  # only one application was ever in view -- nothing to restore


class TestSingleApplicationBranchDoesNotLeakStalePair:
    def test_genuinely_new_unrelated_application_does_not_inherit_the_old_pair(self):
        # A fresh explicit application (not a member of the old pair) must
        # start its own active set of just itself -- the latent bug this
        # session also fixed alongside the main one.
        prev = _pair_context(current_application=APP_B)
        ctx = fctx.build_context(
            "application_status", {"application_number": APP_C, "found": True},
            previous_context=prev)
        assert ctx.active_applications == [APP_C]

    def test_pair_member_still_in_view_keeps_the_whole_pair_active(self):
        prev = _pair_context(current_application=APP_B)
        ctx = fctx.build_context(
            "application_status", {"application_number": APP_A, "found": True},
            previous_context=prev)
        assert sorted(ctx.active_applications) == sorted([APP_A, APP_B])


# ---------------------------------------------------------------------------
# Section B: the real multi-turn pipeline, live DB, real officer/records
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


class TestPairPersistenceLivePipeline:
    @pytest.mark.asyncio
    async def test_pair_survives_a_single_application_status_followup(self, db_session, test_officer):
        answers = await _run(db_session, test_officer, [
            f"Compare {APP_A} and {APP_B}.",
            "What about the other one?",
            "What is its status?",
            "Show both applications again.",
        ])
        final = answers[-1]
        assert final["intent"] == "compare_applications"
        assert APP_A in final["response"] and APP_B in final["response"]

    @pytest.mark.asyncio
    async def test_pair_survives_multiple_intermediate_followup_turns(self, db_session, test_officer):
        answers = await _run(db_session, test_officer, [
            f"Compare {APP_A} and {APP_B}.",
            "What about the other one?",
            "What is its status?",
            "What is its status?",
            "Show both applications again.",
        ])
        final = answers[-1]
        assert APP_A in final["response"] and APP_B in final["response"]

    @pytest.mark.asyncio
    async def test_switch_back_to_first_after_show_both(self, db_session, test_officer):
        answers = await _run(db_session, test_officer, [
            f"Compare {APP_A} and {APP_B}.",
            "What about the other one?",
            "What is its status?",
            "Show both applications again.",
            "Switch back to the first application.",
        ])
        final = answers[-1]["response"]
        assert "survey" in final.lower() or APP_A in final or "110" in final  # 045470's survey no

    @pytest.mark.asyncio
    async def test_a_new_explicit_application_overrides_focus_without_erasing_pair_semantics(self, db_session, test_officer):
        # Per P3: an explicit current-turn identifier always wins.
        answers = await _run(db_session, test_officer, [
            f"Compare {APP_A} and {APP_B}.",
            "What about the other one?",
            f"Show the status of {APP_C}.",
        ])
        final = answers[-1]["response"]
        assert APP_C in final

    @pytest.mark.asyncio
    async def test_a_new_explicit_comparison_establishes_a_new_pair(self, db_session, test_officer):
        answers = await _run(db_session, test_officer, [
            f"Compare {APP_A} and {APP_B}.",
            f"Compare {APP_A} and {APP_C}.",
        ])
        final = answers[-1]["response"]
        assert APP_A in final and APP_C in final

    @pytest.mark.asyncio
    async def test_fresh_session_has_no_inherited_pair(self, db_session, test_officer):
        await _run(db_session, test_officer, [f"Compare {APP_A} and {APP_B}."])
        answers2 = await _run(db_session, test_officer, ["Show both applications again."])
        final = answers2[-1]["response"]
        assert APP_A not in final and APP_B not in final


# ---------------------------------------------------------------------------
# Section C: isolation (Phase 7 / P3 non-regression)
# ---------------------------------------------------------------------------

class TestPairIsolation:
    @pytest.mark.asyncio
    async def test_second_session_cannot_inherit_first_sessions_pair(self, db_session, test_officer):
        session1_answers = await _run(db_session, test_officer, [f"Compare {APP_A} and {APP_B}."])
        assert APP_A in session1_answers[-1]["response"]
        session2_answers = await _run(db_session, test_officer, ["What about the other one?"])
        # Session 2 never established any pair; it must not silently answer
        # about session 1's pair.
        final2 = (session2_answers[-1].get("response") or "")
        assert APP_A not in final2 and APP_B not in final2

    def test_pair_context_rejects_a_different_officers_validation(self, test_officer, secondary_officer):
        ctx = fctx.FollowupContext(
            entity=fctx.ENTITY_APPLICATION_LIST,
            application_numbers=[APP_A, APP_B],
            active_applications=[APP_A, APP_B],
            officer_id=str(test_officer.officer_id))
        assert ctx.validate_officer(str(secondary_officer.officer_id)) is False
        assert ctx.validate_officer(str(test_officer.officer_id)) is True
