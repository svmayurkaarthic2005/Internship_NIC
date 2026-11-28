"""Regression tests for the P1 remediation (test_reports/P1_REMEDIATION_REPORT.md):

  P1-1: a "compare A and B" question naming two application numbers was
        answered about only the first one, and the follow-up "what about
        the other one?" returned the identical answer again -- the second
        application was never retrieved at all.
  Phase 5: a generic "compare X and Y" question with no service code in it
        was hijacked by calc_qa's service-code-count fallback, using a
        stale `codes_override` from context.

P1-2 (evidence-grounded reasoning for "why"/"does this mean" follow-ups) was
NOT implemented in this pass -- see the report's section H ("Remaining
issues") -- so there are no regression tests claiming it here.

The dispatch-level tests (A, C) are deterministic parser/guard tests, no DB
or LLM needed. The DB-backed tests (B) go through the real `_build_comparison`
/ `get_comparison` pipeline against the live database, using the project's
own `test_officer` / `db_session` fixtures -- not a mocked result.
"""
import pytest

from backend.services.chatbot.chat_shared import is_two_application_comparison, _cm_strong
from backend.services.chatbot import calc_qa
from backend.services.chatbot.chat_application_queries import _build_comparison
from backend.services import followup_context as fctx


# ---------------------------------------------------------------------------
# A. Dispatch guard: a two-application comparison must not be claimed by a
#    single-application record module (fulltransfer_qa / cmflow_qa / ...).
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("message", [
    "Compare 2026/0153/02/045470 and 2026/0153/02/045472. Compare their survey numbers, "
    "subdivisions, old patta numbers, registration deed references, final statuses and rejection remarks.",
    "What is the difference between 2026/0158/02/000002 and 2026/0158/02/001804?",
    "2026/0153/02/045470 vs 2026/0153/02/045472",
])
def test_two_application_comparison_detected(message):
    assert is_two_application_comparison(message) is True


@pytest.mark.parametrize("message", [
    "What is the status of 2026/0153/02/045470?",          # one application, no comparison
    "Compare ISD and NISD",                                  # comparison, but no application numbers
    "Show the SIS recommendation for 2026/0158/02/000002",  # one application
])
def test_single_application_not_misclassified_as_comparison(message):
    assert is_two_application_comparison(message) is False


def test_two_application_comparison_message_does_not_claim_cm_strong():
    # This is the actual dispatch bug: `_cm_strong()` (chat_shared.py) is the
    # gate that used to force `intent = "application_status"` ahead of
    # `compare_applications`, so the single-application record modules
    # answered about the FIRST number only.
    msg = ("Compare 2026/0153/02/045470 and 2026/0153/02/045472. Compare their survey numbers, "
           "subdivisions, old patta numbers, registration deed references, final statuses and rejection remarks.")
    assert _cm_strong(msg) is False


def test_single_application_sis_recommendation_still_claims_cm_strong():
    # Regression guard for the P0-3 fix this task must not undo: a genuine
    # single-application SIS-recommendation question must still be claimed.
    assert _cm_strong("When did the SIS recommendation occur?") is True


# ---------------------------------------------------------------------------
# B. The real two-application comparison, through `_build_comparison()` /
#    `get_comparison()` against the live database.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_two_application_comparison_returns_both_sides(db_session, test_officer):
    data = await _build_comparison(
        db_session, test_officer,
        "Compare 2026/0153/02/045470 and 2026/0153/02/045472. Compare their survey numbers, "
        "subdivisions, old patta numbers, registration deed references, final statuses and rejection remarks.",
        is_tamil=False,
    )
    assert data.get("found") is True
    assert data.get("kind") == "applications"
    left, right = data["left"], data["right"]
    assert left["application_number"] == "2026/0153/02/045470"
    assert right["application_number"] == "2026/0153/02/045472"
    # Each side's own fields, never copied from the other -- the two real
    # survey numbers are different, proving this is not the same record
    # rendered twice (the exact shape of the original bug).
    assert left["survey_no"] != right["survey_no"]
    assert left.get("old_patta_no") and right.get("old_patta_no")
    assert left["old_patta_no"] != right["old_patta_no"]
    # `multi_applications` is what lets followup_context.build_context()
    # carry BOTH application numbers forward -- without it only one survived
    # into the next turn's context.
    assert data.get("multi_applications") == [left, right]


@pytest.mark.asyncio
async def test_comparison_context_carries_both_application_numbers(db_session, test_officer):
    data = await _build_comparison(
        db_session, test_officer,
        "Compare 2026/0153/02/045470 and 2026/0153/02/045472. Compare their survey numbers, "
        "subdivisions, old patta numbers, registration deed references, final statuses and rejection remarks.",
        is_tamil=False,
    )
    ctx = fctx.build_context("compare_applications", data, officer_id=str(test_officer.officer_id))
    assert ctx is not None
    assert set(ctx.application_numbers) == {"2026/0153/02/045470", "2026/0153/02/045472"}
    assert set(ctx.active_applications) == {"2026/0153/02/045470", "2026/0153/02/045472"}


def test_other_one_resolves_deterministically_with_a_known_pair():
    ctx = fctx.FollowupContext(
        entity=fctx.ENTITY_APPLICATION_LIST,
        application_numbers=["2026/0153/02/045470", "2026/0153/02/045472"],
        active_applications=["2026/0153/02/045470", "2026/0153/02/045472"],
    )
    res = fctx.resolve("What about the other one?", ctx)
    assert res.resolved
    assert res.application_number == "2026/0153/02/045472"


def test_other_one_without_a_known_pair_does_not_guess():
    # Regression guard: the new resolver must not fire (and must not guess)
    # when there are not exactly two known applications -- it stands aside
    # and leaves the pre-existing (unrelated to this task) single-application
    # behaviour exactly as it was.
    ctx = fctx.FollowupContext(
        entity=fctx.ENTITY_APPLICATION,
        application_numbers=["2026/0153/02/045470"],
        active_applications=["2026/0153/02/045470"],
        current_application="2026/0153/02/045470",
    )
    res = fctx.resolve("What about the other one?", ctx)
    assert res.application_number is None


# ---------------------------------------------------------------------------
# C. Phase 5 -- calc_qa's generic "compare X and Y" must not claim a message
#    that is unmistakably about two applications and names no service code.
# ---------------------------------------------------------------------------

def test_generic_compare_declines_when_message_is_about_applications():
    text = "Compare the application information for these two records."
    assert calc_qa.compare_codes(text, rows=[], codes_override=["0153", "0167"]) is None


def test_generic_compare_still_works_for_a_genuine_service_code_comparison():
    # 0153/0154 (ISD vs NISD) deliberately keeps its OWN comparator
    # (counts/durations) and is excluded from this generic one -- 0158/0159
    # (two correction/modification codes) exercises the generic path itself.
    rows = [{"service_code": "0158"}, {"service_code": "0158"}, {"service_code": "0159"}]
    out = calc_qa.compare_codes("Compare service code 0158 and 0159", rows)
    assert out is not None
    assert "0158" in out and "0159" in out


def test_generic_compare_still_uses_history_override_for_a_real_followup():
    # "now compare them again" after a genuine two-code discussion must
    # keep working -- the new guard only excludes the application-shaped
    # phrasing, not every history-based fallback.
    rows = [{"service_code": "0158"}, {"service_code": "0159"}]
    out = calc_qa.compare_codes("compare them again", rows, codes_override=["0158", "0159"])
    assert out is not None


# ===========================================================================
# P1 Context Completion (test_reports/P1_CONTEXT_COMPLETION_REPORT.md):
#
#   Phase 3/4: the pair ("application_numbers") collapsed to a single
#   application the instant any follow-up focused on one member -- "which
#   one was submitted first?", "show both again" and "compare them again"
#   all failed afterward, because nothing distinguished PAIR MEMBERSHIP
#   from CURRENT FOCUS. Fixed via `_resolve_pair_reference()` (replacing
#   the narrower `_resolve_other_one()`), which never narrows
#   `application_numbers` -- only `current_application` moves.
#   Phase 5: a focus-narrowed single pick rendered through the list
#   renderer (which has no notion of a true single-application answer),
#   so "What is its status?" right after a pick answered with a list count
#   instead of the actual status.
#   Phase 7: `qualifier_guard` read the ordinary word "comparison" as an
#   unrecognised filter term.
#
# P1-2 (evidence-grounded reasoning) remains out of scope here too. Date/
# field corrections ("No, I meant the update date...") and a compound
# "Switch back to the first one. What was its rejection reason?" message
# are confirmed, in the live verification, to still be broken -- no
# regression test claims otherwise (see the report's "Remaining issues").
# ===========================================================================
from backend.services.chatbot import qualifier_guard as qg


# ---------------------------------------------------------------------------
# G. Pair persistence -- membership survives a focus switch
# ---------------------------------------------------------------------------

def _pair_ctx():
    return fctx.FollowupContext(
        entity=fctx.ENTITY_APPLICATION_LIST,
        application_numbers=["2026/0153/02/045470", "2026/0153/02/045472"],
        active_applications=["2026/0153/02/045470", "2026/0153/02/045472"],
    )


def test_initial_pair_has_both_ids_in_original_order():
    ctx = _pair_ctx()
    assert ctx.application_numbers == ["2026/0153/02/045470", "2026/0153/02/045472"]


def test_switching_focus_does_not_remove_either_pair_member():
    ctx = _pair_ctx()
    res = fctx.resolve("What about the other one?", ctx)
    assert res.resolved
    assert res.pair == ["2026/0153/02/045470", "2026/0153/02/045472"]
    assert res.application_number == "2026/0153/02/045472"
    # The RESOLUTION narrows to one for rendering, but the pair it came
    # from is still both -- `res.pair`, not `res.application_numbers`,
    # is what must survive to the next turn (see `_followup_out_context`'s
    # `row_peek` branch, which returns the PRIOR (pair) context, not a
    # context rebuilt from this narrowed result).
    assert len(res.application_numbers) == 1


def test_rebuilding_context_from_json_preserves_the_pair():
    ctx = _pair_ctx()
    raw = ctx.to_json()
    ctx2 = fctx.FollowupContext.from_json(raw)
    assert ctx2.application_numbers == ctx.application_numbers


# ---------------------------------------------------------------------------
# H. Follow-up resolution over the pair
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("message,expected", [
    ("What about the other one?", "2026/0153/02/045472"),
    ("The first one.", "2026/0153/02/045470"),
    ("Show the second application.", "2026/0153/02/045472"),
    ("Switch back to the first one.", "2026/0153/02/045470"),
    ("Go back to the other one.", "2026/0153/02/045472"),
])
def test_single_pick_resolves_against_original_pair_order(message, expected):
    ctx = _pair_ctx()
    res = fctx.resolve(message, ctx)
    assert res.resolved
    assert res.application_number == expected
    assert res.pair == ["2026/0153/02/045470", "2026/0153/02/045472"]


@pytest.mark.parametrize("message", [
    "Both of them.",
    "Show both applications again.",
    "Compare them again.",
])
def test_both_restores_the_full_pair(message):
    ctx = _pair_ctx()
    res = fctx.resolve(message, ctx)
    assert res.resolved
    assert res.is_comparison is True
    assert set(res.application_numbers) == {"2026/0153/02/045470", "2026/0153/02/045472"}


def test_superlative_question_resolves_against_the_pair_not_a_singleton():
    # "Which one was submitted first?" is a genuine two-application
    # question -- it must not be swallowed by the plain backref/"last
    # peeked" narrowing meant for "was THAT the rejected one?".
    ctx = _pair_ctx()
    res = fctx.resolve("Which one was submitted first?", ctx)
    assert res.resolved
    assert res.is_comparison is True
    assert set(res.application_numbers) == {"2026/0153/02/045470", "2026/0153/02/045472"}


def test_other_one_ambiguous_with_more_than_two_candidates():
    ctx = fctx.FollowupContext(
        entity=fctx.ENTITY_APPLICATION_LIST,
        application_numbers=["A", "B", "C"],
        active_applications=["A", "B", "C"],
    )
    res = fctx.resolve("What about the other one?", ctx)
    # Stands aside (does not guess a third candidate's "other"); the
    # pre-existing ambiguous-negation path still applies unchanged.
    assert res.application_number is None


def test_other_one_without_a_known_pair_is_unaffected():
    ctx = fctx.FollowupContext(
        entity=fctx.ENTITY_APPLICATION,
        application_numbers=["2026/0153/02/045470"],
        active_applications=["2026/0153/02/045470"],
        current_application="2026/0153/02/045470",
    )
    res = fctx.resolve("What about the other one?", ctx)
    assert res.application_number is None


# ---------------------------------------------------------------------------
# I. Rendering -- a focus switch must render that application's own data
# ---------------------------------------------------------------------------

def test_focus_switch_entity_becomes_single_application_not_list():
    # The actual live bug: a list context narrowed to ONE application via
    # `current_application` rendered through `_scoped_list_answer()`
    # (which has no notion of `FOLLOWUP_SINGULAR`) instead of the real
    # single-application pipeline -- "What is its status?" answered with a
    # bare list count instead of the actual status.
    ctx = fctx.FollowupContext(
        entity=fctx.ENTITY_APPLICATION_LIST,
        application_numbers=["2026/0153/02/045470", "2026/0153/02/045472"],
        active_applications=["2026/0153/02/045470", "2026/0153/02/045472"],
        current_application="2026/0153/02/045472",
    )
    res = fctx.resolve("What is its status?", ctx)
    assert res.kind == fctx.FOLLOWUP_SINGULAR
    assert res.entity == fctx.ENTITY_APPLICATION
    assert res.application_number == "2026/0153/02/045472"


@pytest.mark.asyncio
async def test_picking_b_never_returns_as_application_a(db_session, test_officer):
    # DB-backed: confirms the two real applications' own data genuinely
    # differ (not a copy/fabrication check repeated from the P1 report,
    # re-asserted here because this task's rendering path depends on it).
    data = await _build_comparison(
        db_session, test_officer,
        "Compare 2026/0153/02/045470 and 2026/0153/02/045472",
        is_tamil=False,
    )
    assert data["left"]["application_number"] == "2026/0153/02/045470"
    assert data["right"]["application_number"] == "2026/0153/02/045472"
    assert data["left"]["survey_no"] != data["right"]["survey_no"]


# ---------------------------------------------------------------------------
# J. Qualifier guard -- "comparison" is an ordinary word, not a filter
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("message", [
    "Show both applications again in one comparison.",
    "Compare them again.",
    "Compare service code 0153 and 0154.",
])
def test_qualifier_guard_no_longer_flags_comparison_wording(message):
    assert qg.unknown_words(message) == []


@pytest.mark.parametrize("message", [
    "Show only the rejected applications.",
    "Show the approved applications.",
])
def test_qualifier_guard_still_recognises_genuine_filters(message):
    assert qg.unknown_words(message) == []


def test_qualifier_guard_still_flags_a_genuine_unknown_word():
    # Non-regression: the guard must still catch what it was built for.
    assert qg.unknown_words("show applications from sri") == ["sri"]


# ---------------------------------------------------------------------------
# K. Session isolation -- a pair in one context object never answers for
#    another (the actual isolation mechanism is session-keyed storage in
#    `orchestrator.py` / `FollowupContextStore`, exercised live in Test D
#    of the report; this is the context-object-level guarantee: a context
#    with no pair at all resolves nothing, regardless of what some OTHER
#    context object elsewhere might hold).
# ---------------------------------------------------------------------------

def test_fresh_context_never_resolves_other_one():
    fresh_ctx = fctx.FollowupContext(entity=fctx.ENTITY_APPLICATION_LIST, application_numbers=[])
    res = fctx.resolve("What about the other one?", fresh_ctx)
    assert res.application_number is None
