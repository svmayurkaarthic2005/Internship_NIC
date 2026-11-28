"""P3.3 completion -- singular details / rejection-remark questions use the canonical focus.

Real pipeline, live DB (no mocks) unless a test says otherwise. Each behaviour test runs under
four client-history variants: accurate accumulated, none, a fixed stale copy, and a conflicting
copy that claims the officer focused the OTHER pair member.
"""
import pytest

from backend.services import followup_context as fctx
from backend.services.chatbot.chat_followups import _carried_details_message, _wants_carried_list_details
from backend.services.chatbot.orchestrator import (
    process_chat, create_chat_session, _load_followup_context,
)

A = "2026/0153/02/045470"
B = "2026/0153/02/045472"
C = "2026/0154/02/012766"
STALE = [
    {"role": "user", "content": f"Compare {C} and 2026/0154/02/999999."},
    {"role": "assistant", "content": f"**{C}** vs **2026/0154/02/999999**"},
]
CONFLICT = [  # client claims the focus is B
    {"role": "user", "content": f"Compare {A} and {B}."},
    {"role": "assistant", "content": f"**{A}** vs **{B}**"},
    {"role": "user", "content": "Switch to the second application."},
    {"role": "assistant", "content": f"Found 1 application(s): {B}"},
]
FOCUS_A = [f"Compare {A} and {B}.", "What about the other one?", "Switch back to the first application."]
VARIANTS = ["normal", "none", "stale", "conflict"]


async def _run(db, officer, turns, variant="normal"):
    sid = str((await create_chat_session(db, str(officer.officer_id))).id)
    hist, outs = [], []
    for m in turns:
        sent = {"normal": hist, "none": None, "stale": STALE, "conflict": CONFLICT}[variant]
        out = await process_chat(m, sid, officer, db, list(sent) if sent is not None else None)
        outs.append(out)
        hist += [{"role": "user", "content": m}, {"role": "assistant", "content": out.get("response") or ""}]
    return sid, outs


def _r(out):
    return out.get("response") or ""


# ---- pure helper checks (no DB) ---------------------------------------------

def _pair_ctx(current=None):
    return fctx.FollowupContext(entity=fctx.ENTITY_APPLICATION_LIST, application_numbers=[A, B],
                                active_applications=[A, B], current_application=current)


class TestSingularTarget:
    def test_focus_wins(self):
        assert fctx.singular_target("Show its details.", _pair_ctx(A)) == (A, [])

    def test_no_focus_asks(self):
        assert fctx.singular_target("Show its details.", _pair_ctx()) == (None, [A, B])

    def test_plural_is_not_singular(self):
        assert fctx.singular_target("Show their details.", _pair_ctx(A)) == (None, [])
        assert fctx.singular_target("Show both applications again.", _pair_ctx(A)) == (None, [])

    def test_ordinal_is_not_a_singular_pointer_and_still_picks_its_row(self):
        # positions are `_pick_from_listing`'s job; singular_target only reads "its / this application"
        assert fctx.singular_target("Show details of the second one.", _pair_ctx(A)) == (None, [])
        assert _carried_details_message(_pair_ctx(A), "Show details of the second one.") == f"show details for {B}"

    def test_this_list_is_not_a_singular_pointer(self):
        assert not fctx.points_at_one("Show details of this list.")


class TestCarriedDetailsMessage:
    def test_its_details_narrow_to_focus(self):
        assert _carried_details_message(_pair_ctx(A), "Show its details.") == f"show details for {A}"

    def test_their_details_keep_the_pair(self):
        assert _carried_details_message(_pair_ctx(A), "Show their details.") == f"show details for {A} and {B}"

    def test_no_focus_is_not_claimed_as_all(self):
        assert not _wants_carried_list_details("Show its details.", _pair_ctx())


def test_canonical_tiebreak_never_introduces_a_number():
    tok = fctx.CANONICAL.set(_pair_ctx())
    try:
        assert fctx.canonical_tiebreak(A) == (None, [A, B])      # inside the set, no focus: ask
        assert fctx.canonical_tiebreak(C) == (C, [])             # outside the set: untouched
        assert fctx.canonical_tiebreak(None) == (None, [])
    finally:
        fctx.CANONICAL.reset(tok)


# ---- live pipeline ------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("variant", VARIANTS)
async def test_its_details_after_switch_back_is_only_a(db_session, test_officer, variant):
    _, outs = await _run(db_session, test_officer, FOCUS_A + ["Show its details."], variant)
    final = _r(outs[-1])
    assert f"details for {A}" in final and B not in final and C not in final


@pytest.mark.asyncio
@pytest.mark.parametrize("variant", VARIANTS)
async def test_its_rejection_reason_is_a_recorded_remark(db_session, test_officer, variant):
    _, outs = await _run(db_session, test_officer, FOCUS_A + ["What was its rejection reason?"], variant)
    final = _r(outs[-1])
    assert A in final and B not in final and C not in final
    assert "NO LINK" in final  # A's actual workflow_history remark (P3.2 retrieval kept)


@pytest.mark.asyncio
@pytest.mark.parametrize("variant", ["normal", "stale"])
async def test_why_was_this_application_rejected_uses_focus(db_session, test_officer, variant):
    _, outs = await _run(db_session, test_officer, FOCUS_A + ["Why was this application rejected?"], variant)
    final = _r(outs[-1])
    assert A in final and B not in final


@pytest.mark.asyncio
async def test_both_still_returns_both_after_singular_answers(db_session, test_officer):
    sid, outs = await _run(db_session, test_officer, FOCUS_A + [
        "Show its details.", "What was its rejection reason?", "Show both applications again."], "stale")
    final = _r(outs[-1])
    assert A in final and B in final
    # pair membership survives the singular answers
    ctx = await _load_followup_context(db_session, sid, [], "Show both applications again.", test_officer.officer_id)
    assert set(ctx.active_applications or []) == {A, B}


@pytest.mark.asyncio
async def test_both_workflow_remarks_use_the_pair(db_session, test_officer):
    _, outs = await _run(db_session, test_officer, FOCUS_A + ["Show the workflow remarks of both applications."])
    final = _r(outs[-1])
    assert A in final and B in final


@pytest.mark.asyncio
async def test_explicit_new_id_overrides_focus_then_pair_returns(db_session, test_officer):
    _, outs = await _run(db_session, test_officer, FOCUS_A + [
        f"Show the status of {C}.", "Show both applications again."], "stale")
    assert C in _r(outs[-2]) and A not in _r(outs[-2])
    assert A in _r(outs[-1]) and B in _r(outs[-1])


@pytest.mark.asyncio
@pytest.mark.parametrize("question", [
    "Show its details.", "What was its rejection reason?", "Why was this application rejected?",
    "Show its workflow history.",
])
async def test_no_focus_asks_which_one(db_session, test_officer, question):
    _, outs = await _run(db_session, test_officer, [f"Compare {A} and {B}.", question], "stale")
    final = _r(outs[-1]).lower()
    assert "which one" in final, question
    assert "no link" not in final, question     # never answers for a guessed member
