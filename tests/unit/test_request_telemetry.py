"""Request telemetry: the recorders were never called (every duration logged 0.0), `model_used` was a
hardcoded "llama3.1:8b", and /chat/stream emitted no record at all."""
import asyncio
import json

import pytest

from backend.config import settings
from backend.services import llm_gate, observability as obs


def test_model_used_is_the_configured_model():
    assert obs.RequestMetrics().model_used == settings.LLM_MODEL


def test_recorders_are_noops_outside_a_request():
    tok = obs.current_metrics.set(None)
    try:
        obs.record_llm(5.0)
        obs.record_retrieval(5.0)
        obs.record_tool("x", 5.0)        # nothing to record into; must not raise
    finally:
        obs.current_metrics.reset(tok)


@pytest.mark.asyncio
async def test_llm_slot_records_generation_time():
    m = obs.RequestMetrics()
    tok = obs.current_metrics.set(m)
    try:
        async with llm_gate.llm_slot():
            await asyncio.sleep(0.05)
    finally:
        obs.current_metrics.reset(tok)
    assert m.llm_duration_ms >= 40


def test_retrieval_is_timed(monkeypatch):
    from backend.services import pgvector_store
    monkeypatch.setattr(pgvector_store, "_similarity_search_impl", lambda *a, **k: [{"x": 1}])
    m = obs.RequestMetrics()
    tok = obs.current_metrics.set(m)
    try:
        assert pgvector_store.similarity_search("q") == [{"x": 1}]
    finally:
        obs.current_metrics.reset(tok)
    assert m.retrieval_duration_ms > 0


@pytest.mark.asyncio
async def test_tool_calls_are_recorded_by_name(monkeypatch):
    from backend.services.chatbot import agent_tools

    async def _impl(ctx, name, raw_args, allowed=None):
        await asyncio.sleep(0.01)
        return {"ok": True}
    monkeypatch.setattr(agent_tools, "_execute_tool_impl", _impl)
    m = obs.RequestMetrics()
    tok = obs.current_metrics.set(m)
    try:
        await agent_tools.execute_tool(None, "count_applications", {})
    finally:
        obs.current_metrics.reset(tok)
    assert m.tools_called == ["count_applications"] and m.tool_duration_ms >= 5


def test_finalize_payload_carries_the_recorded_values():
    m = obs.RequestMetrics()
    m.record_llm_call(120.0, {"prompt_tokens": 10, "completion_tokens": 5})
    m.record_retrieval(30.0)
    p = m.finalize()
    assert p["llm_duration_ms"] == 120.0 and p["retrieval_duration_ms"] == 30.0
    assert p["token_usage"] == {"prompt_tokens": 10, "completion_tokens": 5}
    assert p["model_used"] == settings.LLM_MODEL


@pytest.mark.asyncio
async def test_stream_route_finalizes_one_record(db_session, test_officer, monkeypatch):
    """Drive the real /stream route function with a stub pipeline; its wrapper must finalize once."""
    from backend.routers import chat as chat_router
    from backend.services.chatbot.orchestrator import create_chat_session

    async def _fake_stream(**kw):
        yield ("data: " + json.dumps({"content": "hi", "intent": "greeting"}) + "\n\n").encode()
    monkeypatch.setattr(chat_router, "process_chat_stream", _fake_stream)
    seen = []
    real_finalize = obs.RequestMetrics.finalize

    def _spy(self):
        seen.append(self)
        return real_finalize(self)
    monkeypatch.setattr(obs.RequestMetrics, "finalize", _spy)

    class _Req:
        async def is_disconnected(self):
            return False
    sid = str((await create_chat_session(db_session, str(test_officer.officer_id))).id)
    req = chat_router.ChatRequest(message="hello", session_id=sid)
    resp = await chat_router.stream_chat_message(req, None, _Req(), db_session, test_officer)
    body = [c async for c in resp.body_iterator]
    assert body[-1] == b'data: {"done": true}\n\n'
    assert len(seen) == 1 and seen[0].intent == "greeting" and seen[0].model_used == settings.LLM_MODEL
