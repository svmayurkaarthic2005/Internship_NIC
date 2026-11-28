"""P3.4 -- an explicit current application together with a reference into the EARLIER comparison.

Before: "Compare C with the first application from our earlier comparison" got a generic "which
applications?"; "status of the second application from our earlier comparison" got a register-wide
status breakdown; "show both applications from our previous comparison" got "your most recent
application"; "what about the other one?" after C silently picked B (or timed out on the LLM).

Fix: `followup_context.rewrite_historical_refs()` turns an explicit historical reference into the
application number(s) it names -- from `prior_pair`, kept by `_carry_prior_pair` -- so the ordinary
explicit-ID path resolves, orders and re-authorizes them; it asks instead of guessing when it cannot.
Real pipeline on the live DB unless a test says "mocked".
"""
import json

import pytest

from backend.services import followup_context as fctx
from backend.services.chatbot.orchestrator import (
    process_chat, process_chat_stream, create_chat_session, _load_followup_context,
)

A = "2026/0153/02/045470"
B = "2026/0153/02/045472"
C = "2026/0154/02/012766"
BAD = "2026/0154/02/999999"
STALE = [
    {"role": "user", "content": f"Compare {C} and {BAD}."},
    {"role": "assistant", "content": f"**{C}** vs **{BAD}**"},
]
CONFLICT = [  # client claims the earlier comparison was C/BAD and the focus is A
    {"role": "user", "content": f"Compare {C} and {BAD}."},
    {"role": "assistant", "content": f"**{C}** vs **{BAD}**"},
    {"role": "user", "content": f"Show the status of {A}."},
    {"role": "assistant", "content": f"The Status for {A} is: rejected"},
]
SETUP = [f"Compare {A} and {B}.", f"Show the status of {C}."]
VARIANTS = ["normal", "none", "stale", "conflict"]


async def _session(db, officer):
    return str((await create_chat_session(db, str(officer.officer_id))).id)


async def _run(db, officer, turns, variant="normal", sid=None):
    sid = sid or await _session(db, officer)
    hist, outs = [], []
    for m in turns:
        sent = {"normal": hist, "none": None, "stale": STALE, "conflict": CONFLICT}[variant]
        out = await process_chat(m, sid, officer, db, list(sent) if sent is not None else None)
        outs.append(out)
        hist += [{"role": "user", "content": m}, {"role": "assistant", "content": out.get("response") or ""}]
    return sid, outs


def _r(out):
    return out.get("response") or ""


# ---- pure: rewrite + prior-pair bookkeeping (no DB) ---------------------------------------

def _after_c():
    return fctx.FollowupContext(entity=fctx.ENTITY_APPLICATION, application_numbers=[C], active_applications=[C],
                                current_application=C, filters={"prior_pair": [A, B]})


class TestRewrite:
    @pytest.mark.parametrize("msg,expected", [
        (f"Compare {C} with the first application from our earlier comparison.", f"Compare {C} with {A}."),
        (f"Compare {C} with the second application from our previous comparison.", f"Compare {C} with {B}."),
        ("Compare this with the first application from our earlier comparison.", f"Compare {C} with {A}."),
        ("Show the difference between this application and the first one we compared earlier.",
         f"Show the difference between {C} and {A}."),
        ("What is the status of the second application from our earlier comparison?", f"What is the status of {B}?"),
        ("Show both applications from our previous comparison again.", f"Compare {A} and {B}."),
    ])
    def test_rewrite_to_explicit_numbers_in_requested_order(self, msg, expected):
        assert fctx.rewrite_historical_refs(msg, _after_c()) == (expected, None)

    def test_three_entities_ask_instead_of_dropping_one(self):
        new, ask = fctx.rewrite_historical_refs(f"Compare {C} with both applications from our previous comparison.", _after_c())
        assert ask and A in ask and B in ask and C in ask and "which one" in ask

    def test_no_prior_pair_asks(self):
        _, ask = fctx.rewrite_historical_refs("Compare this with the first application from our earlier comparison.", None)
        assert ask and "no earlier comparison" in ask

    def test_this_without_a_current_application_asks(self):
        ctx = fctx.FollowupContext(entity=fctx.ENTITY_APPLICATION_LIST, application_numbers=[A, B], active_applications=[A, B])
        _, ask = fctx.rewrite_historical_refs("Compare this with the first application from our earlier comparison.", ctx)
        assert ask and "this" in ask.lower()

    def test_messages_without_a_historical_reference_are_untouched(self):
        for m in ["What about the other one?", "Show both applications again.", f"Compare {A} and {B}.",
                  "What is its status?", "Show the first one."]:
            assert fctx.rewrite_historical_refs(m, _after_c()) == (m, None)

    def test_other_one_after_unrelated_application_asks(self):
        res = fctx._resolve_pair_reference("What about the other one?", _after_c())
        assert res.ambiguous and A in res.clarification and B in res.clarification

    def test_other_one_inside_the_active_pair_still_works(self):  # P3.1 regression
        ctx = fctx.FollowupContext(entity=fctx.ENTITY_APPLICATION_LIST, application_numbers=[A, B],
                                   active_applications=[A, B], current_application=A)
        assert fctx._resolve_pair_reference("What about the other one?", ctx).application_number == B


class TestPriorPairBookkeeping:
    def _pair(self, a, b, prior=None):
        return fctx.FollowupContext(entity=fctx.ENTITY_APPLICATION_LIST, application_numbers=[a, b],
                                    active_applications=[a, b], filters={"prior_pair": prior} if prior else {})

    def test_leaving_a_pair_for_a_new_application_records_it_in_order(self):
        ctx = fctx.build_context("application_status", {"application_number": C, "found": True},
                                 previous_context=self._pair(A, B))
        assert ctx.active_applications == [C]           # P3.1 rule kept
        assert ctx.filters["prior_pair"] == [A, B]      # order preserved

    def test_a_derived_comparison_keeps_the_original_prior_pair(self):
        ctx = fctx.build_context("compare_applications", {"multi_applications": [
            {"application_number": C}, {"application_number": A}]}, previous_context=_after_c())
        assert ctx.active_applications == [C, A] and ctx.filters["prior_pair"] == [A, B]

    def test_a_new_explicit_comparison_becomes_active_and_the_old_pair_becomes_prior(self):
        E, F = "2026/0153/02/045480", "2026/0153/02/045481"
        ctx = fctx.build_context("compare_applications", {"multi_applications": [
            {"application_number": E}, {"application_number": F}]}, previous_context=self._pair(A, B))
        assert ctx.active_applications == [E, F] and ctx.filters["prior_pair"] == [A, B]

    def test_restoring_the_prior_pair_drops_it(self):
        ctx = fctx.build_context("compare_applications", {"multi_applications": [
            {"application_number": A}, {"application_number": B}]}, previous_context=_after_c())
        assert ctx.active_applications == [A, B] and "prior_pair" not in (ctx.filters or {})


# ---- live pipeline ------------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("variant", VARIANTS)
async def test_explicit_c_with_first_member_of_prior_pair(db_session, test_officer, variant):
    _, outs = await _run(db_session, test_officer,
                         SETUP + [f"Compare {C} with the first application from our earlier comparison."], variant)
    final = _r(outs[-1])
    assert final.startswith(f"**{C}** vs **{A}**")       # [C, A], the order the officer gave
    assert B not in final and BAD not in final


@pytest.mark.asyncio
@pytest.mark.parametrize("variant", ["normal", "stale"])
async def test_explicit_c_with_second_member_of_prior_pair(db_session, test_officer, variant):
    _, outs = await _run(db_session, test_officer,
                         SETUP + [f"Compare {C} with the second application from our previous comparison."], variant)
    assert _r(outs[-1]).startswith(f"**{C}** vs **{B}**")


@pytest.mark.asyncio
@pytest.mark.parametrize("variant", VARIANTS)
async def test_other_one_after_c_asks(db_session, test_officer, variant):
    _, outs = await _run(db_session, test_officer, SETUP + ["What about the other one?"], variant)
    final = _r(outs[-1])
    assert "which application do you mean" in final.lower()
    assert "status for" not in final.lower() and "found 1 application" not in final.lower()


@pytest.mark.asyncio
async def test_c_with_both_from_earlier_comparison_asks_and_drops_nothing(db_session, test_officer):
    _, outs = await _run(db_session, test_officer,
                         SETUP + [f"Compare {C} with both applications from our previous comparison."], "stale")
    final = _r(outs[-1])
    assert A in final and B in final and C in final and "which one" in final
    assert outs[-1]["intent"] == "clarification_required"


@pytest.mark.asyncio
@pytest.mark.parametrize("msg", ["Show both applications again.",
                                 "Show both applications from our previous comparison again."])
async def test_show_both_again_restores_a_b(db_session, test_officer, msg):
    _, outs = await _run(db_session, test_officer, SETUP + [msg], "stale")
    assert _r(outs[-1]).startswith(f"**{A}** vs **{B}**")


@pytest.mark.asyncio
async def test_full_sequence_q1_to_q6(db_session, test_officer):
    _, outs = await _run(db_session, test_officer, SETUP + [
        f"Compare {C} with the first application from our earlier comparison.",
        "What is the status of the second application from our earlier comparison?",
        "What about the other one?",
        "Show both applications from our previous comparison again.",
    ], "stale")
    assert _r(outs[2]).startswith(f"**{C}** vs **{A}**")
    assert _r(outs[3]) == f"The Status for {B} is: rejected"
    assert "which application do you mean" in _r(outs[4]).lower()
    assert _r(outs[5]).startswith(f"**{A}** vs **{B}**")


@pytest.mark.asyncio
async def test_new_explicit_comparison_replaces_the_active_pair(db_session, test_officer):
    sid, outs = await _run(db_session, test_officer, [f"Compare {A} and {B}.", f"Compare {C} and {A}.",
                                                      "What about the other one?"])
    assert _r(outs[1]).startswith(f"**{C}** vs **{A}**")
    ctx = await _load_followup_context(db_session, sid, None, None, test_officer.officer_id)
    assert ctx is not None


@pytest.mark.asyncio
async def test_prior_pair_order_is_preserved(db_session, test_officer):
    sid, _ = await _run(db_session, test_officer, [f"Compare {B} and {A}.", f"Show the status of {C}."])
    ctx = await _load_followup_context(db_session, sid, None, None, test_officer.officer_id)
    assert ctx.filters["prior_pair"] == [B, A]
    _, outs = await _run(db_session, test_officer,
                         ["What is the status of the first application from our earlier comparison?"], sid=sid)
    assert _r(outs[-1]) == f"The Status for {B} is: rejected"


@pytest.mark.asyncio
async def test_invalid_c_never_falls_back(db_session, test_officer):
    _, outs = await _run(db_session, test_officer,
                         SETUP + [f"Compare {BAD} with the first application from our earlier comparison."], "stale")
    final = _r(outs[-1])
    assert "999999" in final
    assert not final.startswith(f"**{A}** vs") and f"**{C}**" not in final and B not in final


@pytest.mark.asyncio
async def test_inaccessible_historical_member_is_refused_not_substituted(db_session, test_officer, monkeypatch):
    # MOCKED access: every seeded ISD/NISD file is inside this officer's jurisdiction, so B is made
    # inaccessible after the pair is established; the rewritten number must still go through the gate.
    sid, _ = await _run(db_session, test_officer, SETUP)
    from backend.services import postgres
    real = postgres.lookup_application_access

    async def _deny_b(db, app_number, officer):
        facts = await real(db, app_number, officer)
        if app_number == B:
            facts = {**facts, "exists": True, "accessible": False}
        return facts
    monkeypatch.setattr(postgres, "lookup_application_access", _deny_b)
    _, outs = await _run(db_session, test_officer,
                         ["What is the status of the second application from our earlier comparison?"], sid=sid)
    final = _r(outs[-1])
    assert "rejected" not in final.lower()        # B's status never disclosed
    assert A not in final and C not in final      # and no other application substituted


@pytest.mark.asyncio
async def test_no_prior_pair_asks(db_session, test_officer):
    _, outs = await _run(db_session, test_officer, [
        f"Show the status of {C}.", "Compare this with the first application from our earlier comparison."])
    assert "no earlier comparison" in _r(outs[-1]).lower()


@pytest.mark.asyncio
async def test_fresh_session_cannot_reach_another_sessions_pair(db_session, test_officer):
    await _run(db_session, test_officer, SETUP)
    _, outs = await _run(db_session, test_officer,
                         ["What is the status of the second application from our earlier comparison?"], "conflict")
    final = _r(outs[-1])
    assert "no earlier comparison" in final.lower() and B not in final


@pytest.mark.asyncio
async def test_stale_and_conflicting_history_do_not_change_the_outcome(db_session, test_officer):
    q = SETUP + ["What is the status of the second application from our earlier comparison?"]
    answers = {v: _r((await _run(db_session, test_officer, q, v))[1][-1]) for v in VARIANTS}
    assert len(set(answers.values())) == 1 and answers["normal"] == f"The Status for {B} is: rejected"


@pytest.mark.asyncio
@pytest.mark.parametrize("question", [
    f"Compare {C} with the first application from our earlier comparison.",
    "What about the other one?",
    f"Compare {C} with both applications from our previous comparison.",
])
async def test_stream_matches_non_stream(db_session, test_officer, question):
    _, sync = await _run(db_session, test_officer, SETUP + [question], "stale")
    sid, _ = await _run(db_session, test_officer, SETUP, "stale")
    parts = []
    async for raw in process_chat_stream(question, sid, test_officer, db_session, list(STALE)):
        line = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        for p in line.split("\n"):
            if p.startswith("data: "):
                try:
                    parts.append(json.loads(p[6:]).get("content") or "")
                except (ValueError, AttributeError):
                    pass
    assert "".join(parts).strip() == _r(sync[-1]).strip()


# ---- regressions: P3 / P3.1 / P3.3 ---------------------------------------------------------------

@pytest.mark.asyncio
async def test_explicit_id_still_wins(db_session, test_officer):
    _, outs = await _run(db_session, test_officer, SETUP + [f"Show the status of {A}."], "stale")
    assert _r(outs[-1]) == f"The Status for {A} is: rejected"


@pytest.mark.asyncio
async def test_first_and_second_work_while_the_pair_is_active(db_session, test_officer):
    _, outs = await _run(db_session, test_officer, [f"Compare {A} and {B}.", "What about the other one?",
                                                    "What is its status?", "Switch back to the first application.",
                                                    "What is its status?"], "stale")
    assert _r(outs[2]) == f"The Status for {B} is: rejected"
    assert _r(outs[4]) == f"The Status for {A} is: rejected"


@pytest.mark.asyncio
async def test_singular_follow_up_uses_focus_after_historical_reference(db_session, test_officer):
    _, outs = await _run(db_session, test_officer, SETUP + [
        "What is the status of the second application from our earlier comparison?", "Show its details."], "stale")
    final = _r(outs[-1])
    assert f"details for {B}" in final and A not in final and C not in final
