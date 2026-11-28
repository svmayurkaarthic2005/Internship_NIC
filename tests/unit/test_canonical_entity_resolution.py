"""P3.3 -- canonical server-side entity resolution, client-history isolation.

Root cause (see test_reports/P3_3_ENTITY_RESOLUTION_BASELINE.md): ~205 places
downstream of `process_chat` / `process_chat_stream` scan `chat_history` for an
application number when the message names none, and that list was the
client-supplied `ChatRequest.chat_history`. A stale copy resent every turn
outvoted the server's own context. Fix: both public entry points replace it
with `_server_transcript()` -- this officer's own stored session transcript.

All tests here run the real pipeline against the live database (no mocks),
except Section F's `_server_transcript` checks, which still use the real DB.
"""
import json

import pytest

from backend.services.chatbot.orchestrator import (
    process_chat, process_chat_stream, create_chat_session, _server_transcript,
)

APP_A = "2026/0153/02/045470"   # NISD, rejected, recorded remark "NO LINK ..."
APP_B = "2026/0153/02/045472"   # NISD, rejected
APP_STALE = "2026/0154/02/012766"   # ISD, approved -- only ever in the stale client copy
FAKE = "2026/0154/02/999999"

STALE = [
    {"role": "user", "content": f"Compare {APP_STALE} and {FAKE}."},
    {"role": "assistant", "content": f"**{APP_STALE}** vs **{FAKE}**"},
]

FOCUS_A = [f"Compare {APP_A} and {APP_B}.", "What about the other one?",
           "Switch back to the first application."]


async def _session(db, officer):
    return str((await create_chat_session(db, str(officer.officer_id))).id)


async def _run(db, officer, messages, client_history=None, sid=None):
    """Send every turn with the SAME fixed client history (the stale-client case)."""
    sid = sid or await _session(db, officer)
    outs = []
    for m in messages:
        outs.append(await process_chat(m, sid, officer, db, list(client_history or [])))
    return sid, outs


def _clean(text):
    return text or ""


# ---------------------------------------------------------------------------
# A. Stale client history never overrides the canonical focus
# ---------------------------------------------------------------------------

class TestStaleHistory:
    @pytest.mark.asyncio
    async def test_rejection_reason_follows_canonical_focus(self, db_session, test_officer):
        _, outs = await _run(db_session, test_officer, FOCUS_A + ["What was its rejection reason?"], STALE)
        final = _clean(outs[-1]["response"])
        assert APP_A in final and ("NO LINK" in final or "Linked Documents" in final)
        assert APP_STALE not in final and "999999" not in final

    @pytest.mark.asyncio
    async def test_stale_history_answer_equals_no_history_answer(self, db_session, test_officer):
        q = FOCUS_A + ["What was its rejection reason?"]
        _, stale = await _run(db_session, test_officer, q, STALE)
        _, none = await _run(db_session, test_officer, q, None)
        assert stale[-1]["response"] == none[-1]["response"]


# ---------------------------------------------------------------------------
# B. Explicit identifiers in the current message still win
# ---------------------------------------------------------------------------

class TestExplicitIds:
    @pytest.mark.asyncio
    async def test_explicit_id_beats_canonical_focus(self, db_session, test_officer):
        _, outs = await _run(db_session, test_officer,
                             FOCUS_A + [f"Show the status of {APP_STALE}."], STALE)
        final = _clean(outs[-1]["response"])
        assert APP_STALE in final and APP_A not in final

    @pytest.mark.asyncio
    async def test_two_ids_compare_in_message_order(self, db_session, test_officer):
        _, outs = await _run(db_session, test_officer, [f"Compare {APP_B} and {APP_A}."], STALE)
        final = _clean(outs[-1]["response"])
        assert final.index(APP_B) < final.index(APP_A)
        assert APP_STALE not in final


# ---------------------------------------------------------------------------
# C. Pair references resolve from the server-side pair, not the client copy
# ---------------------------------------------------------------------------

class TestPairReferences:
    @pytest.mark.asyncio
    async def test_other_one_is_the_pair_partner(self, db_session, test_officer):
        _, outs = await _run(db_session, test_officer,
                             [f"Compare {APP_A} and {APP_B}.", "What about the other one?",
                              "What is its status?"], STALE)
        final = _clean(outs[-1]["response"])
        assert APP_B in final and APP_STALE not in final

    @pytest.mark.asyncio
    async def test_both_again_is_the_server_pair(self, db_session, test_officer):
        _, outs = await _run(db_session, test_officer,
                             FOCUS_A + ["Show both applications again."], STALE)
        final = _clean(outs[-1]["response"])
        assert APP_A in final and APP_B in final and APP_STALE not in final


# ---------------------------------------------------------------------------
# D. Every handler family resolves the same canonical app under stale history
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("question", [
    "What is its status?",                    # status
    "Show its details.",                      # details card
    "Show its workflow history.",             # workflow
    "Why was this application rejected?",     # rejection reason
    "Who decided it?",                        # reasoning / decider
    "What is its SIS recommendation?",        # correction-modification record module
])
async def test_cross_handler_never_selects_client_history_app(db_session, test_officer, question):
    _, outs = await _run(db_session, test_officer, FOCUS_A + [question], STALE)
    final = _clean(outs[-1]["response"])
    assert APP_STALE not in final, question
    assert "999999" not in final, question


@pytest.mark.asyncio
@pytest.mark.parametrize("question", [
    "What is its status?", "Show its details.", "Show its workflow history.",
    "Why was this application rejected?",
])
async def test_cross_handler_answers_only_the_canonical_focus(db_session, test_officer, question):
    # precedence 3: after "switch back to the first application" the focus, not the pair
    _, outs = await _run(db_session, test_officer, FOCUS_A + [question], STALE)
    final = _clean(outs[-1]["response"])
    assert APP_A in final, question
    assert APP_B not in final, question


@pytest.mark.asyncio
@pytest.mark.parametrize("question", [
    "Show its workflow history.", "Show its details.", "Why was this application rejected?",
])
async def test_no_focus_after_plain_compare_never_guesses_one(db_session, test_officer, question):
    # a pair with no chosen focus: "its" must not silently become the first member
    _, outs = await _run(db_session, test_officer, [f"Compare {APP_A} and {APP_B}.", question], STALE)
    final = _clean(outs[-1]["response"])
    assert APP_A in final and APP_B in final, question       # both offered as choices
    assert "which one" in final.lower(), question            # precedence 5: ask, never answer for both or one


@pytest.mark.asyncio
async def test_its_workflow_with_one_app_in_context_is_deterministic(db_session, test_officer):
    # was a general_query -> LLM timeout even with a single unambiguous application
    _, outs = await _run(db_session, test_officer,
                         [f"Show the status of {APP_A}.", "Show its workflow history."])
    final = _clean(outs[-1]["response"])
    assert f"Workflow history for {APP_A}" in final


def test_points_at_one():
    from backend.services import followup_context as fctx
    assert fctx.points_at_one("Show its details.")
    assert fctx.points_at_one("Why was this application rejected?")
    assert not fctx.points_at_one("Show both applications again.")
    assert not fctx.points_at_one("Show their details.")
    assert not fctx.points_at_one("Compare these two applications.")


# ---------------------------------------------------------------------------
# E. Fresh session: client history alone selects nothing -> clarification
# ---------------------------------------------------------------------------

class TestFreshSession:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("question", ["What is its status?", "What was its rejection reason?"])
    async def test_fresh_session_with_client_history_does_not_resolve(self, db_session, test_officer, question):
        _, outs = await _run(db_session, test_officer, [question], STALE)
        final = _clean(outs[-1]["response"])
        assert APP_STALE not in final and "999999" not in final and APP_A not in final

    @pytest.mark.asyncio
    async def test_stream_path_matches_non_stream(self, db_session, test_officer):
        sid, _ = await _run(db_session, test_officer, FOCUS_A, STALE)
        chunks = []
        async for raw in process_chat_stream("What was its rejection reason?", sid, test_officer,
                                             db_session, list(STALE)):
            line = raw.decode("utf-8") if isinstance(raw, bytes) else raw
            for part in line.split("\n"):
                if part.startswith("data: "):
                    try:
                        chunks.append(json.loads(part[6:]).get("content") or "")
                    except (ValueError, AttributeError):
                        pass
        text = "".join(chunks)
        assert APP_A in text and APP_STALE not in text


# ---------------------------------------------------------------------------
# F. Security / transcript isolation
# ---------------------------------------------------------------------------

class TestServerTranscript:
    @pytest.mark.asyncio
    async def test_transcript_is_server_side_and_ordered(self, db_session, test_officer):
        sid, _ = await _run(db_session, test_officer, [f"Show the status of {APP_A}."], STALE)
        hist = await _server_transcript(db_session, sid, test_officer.officer_id, STALE)
        assert [h["role"] for h in hist] == ["user", "assistant"]
        assert APP_A in hist[0]["content"]
        assert all(APP_STALE not in h["content"] for h in hist)

    @pytest.mark.asyncio
    async def test_other_officers_session_yields_nothing(self, db_session, test_officer, secondary_officer):
        sid, _ = await _run(db_session, test_officer, [f"Show the status of {APP_A}."])
        assert await _server_transcript(db_session, sid, secondary_officer.officer_id, STALE) == []

    @pytest.mark.asyncio
    async def test_unknown_session_yields_nothing(self, db_session, test_officer):
        import uuid
        assert await _server_transcript(db_session, str(uuid.uuid4()), test_officer.officer_id, STALE) == []


# ---------------------------------------------------------------------------
# G. Regression: normal (accurate) client history behaves as before
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_accurate_client_history_still_resolves(db_session, test_officer):
    sid = await _session(db_session, test_officer)
    hist = []
    out = None
    for m in FOCUS_A + ["What was its rejection reason?"]:
        out = await process_chat(m, sid, test_officer, db_session, list(hist))
        hist += [{"role": "user", "content": m}, {"role": "assistant", "content": out.get("response") or ""}]
    assert APP_A in out["response"]
