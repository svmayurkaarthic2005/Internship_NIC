"""Ollama cancellation / timeout cleanup. No Ollama needed: a fake model stands in."""
import asyncio

from backend.config import settings
from backend.services import llm_gate, rag


class FakeStream:
    """An async iterator that records whether it was closed."""
    def __init__(self, chunks, hang_after=None):
        self.chunks, self.hang_after, self.closed, self.i = list(chunks), hang_after, False, 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.hang_after is not None and self.i >= self.hang_after:
            await asyncio.sleep(3600)
        if self.i >= len(self.chunks):
            raise StopAsyncIteration
        c = self.chunks[self.i]
        self.i += 1
        return c

    async def aclose(self):
        self.closed = True


class Chunk:
    def __init__(self, content):
        self.content = content


def fast_settings(**kw):
    old = {k: getattr(settings, k) for k in kw}
    for k, v in kw.items():
        setattr(settings, k, v)
    return old


def restore(old):
    for k, v in old.items():
        setattr(settings, k, v)


async def collect(agen):
    return [x async for x in agen]


async def t_slot_limit():
    old = fast_settings(LLM_MAX_CONCURRENT=1, LLM_QUEUE_WAIT_SECONDS=0.2)
    try:
        async with llm_gate.llm_slot():
            try:
                async with llm_gate.llm_slot():
                    raise AssertionError("second slot should not be granted")
            except llm_gate.LLMBusy:
                pass
        async with llm_gate.llm_slot():      # released afterwards
            pass
    finally:
        restore(old)


async def t_first_token_deadline_closes_stream():
    old = fast_settings(LLM_FIRST_TOKEN_TIMEOUT_SECONDS=0.2)
    fs = FakeStream(["a"], hang_after=0)
    try:
        await collect(llm_gate.guarded_stream(lambda: fs))
        raise AssertionError("expected LLMStalled")
    except llm_gate.LLMStalled:
        pass
    finally:
        restore(old)
    assert fs.closed


async def t_idle_deadline_after_partial_output():
    old = fast_settings(LLM_STREAM_IDLE_SECONDS=0.2)
    fs = FakeStream(["a", "b"], hang_after=2)
    got = []
    try:
        async for c in llm_gate.guarded_stream(lambda: fs):
            got.append(c)
        raise AssertionError("expected LLMStalled")
    except llm_gate.LLMStalled:
        pass
    finally:
        restore(old)
    assert got == ["a", "b"] and fs.closed


async def t_cancel_closes_stream_and_frees_slot():
    old = fast_settings(LLM_MAX_CONCURRENT=1, LLM_QUEUE_WAIT_SECONDS=0.3, LLM_FIRST_TOKEN_TIMEOUT_SECONDS=30)
    fs = FakeStream([Chunk("x")] * 3, hang_after=1)
    real, rag.llm = rag.llm, type("L", (), {"astream": staticmethod(lambda p: fs)})()

    async def consume():
        return [c async for c in rag.call_llama_stream("q")]

    try:
        task = asyncio.create_task(consume())
        await asyncio.sleep(0.2)
        task.cancel()                       # the client went away
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert fs.closed, "cancelled stream must be closed so Ollama stops generating"
        async with llm_gate.llm_slot():      # the slot came back
            pass
    finally:
        rag.llm = real
        restore(old)


async def t_call_llama_stream_messages():
    old = fast_settings(LLM_MAX_CONCURRENT=1, LLM_QUEUE_WAIT_SECONDS=0.2, LLM_FIRST_TOKEN_TIMEOUT_SECONDS=0.2)
    real = rag.llm
    try:
        rag.llm = type("L", (), {"astream": staticmethod(lambda p: FakeStream([Chunk("ok")]))})()
        assert "".join(await collect(rag.call_llama_stream("q"))) == "ok"

        rag.llm = type("L", (), {"astream": staticmethod(lambda p: FakeStream([], hang_after=0))})()
        out = "".join(await collect(rag.call_llama_stream("q")))
        assert "stopped responding" in out, out

        async with llm_gate.llm_slot():      # occupy the only slot
            out = "".join(await collect(rag.call_llama_stream("q")))
        assert "busy" in out, out
    finally:
        rag.llm = real
        restore(old)


async def main():
    for name, fn in list(globals().items()):
        if name.startswith("t_"):
            await fn()
            print("ok", name)

asyncio.run(main())
