"""Limits and deadlines for calls to Ollama.

Ollama works through one queue, so a stalled or abandoned generation holds up every request
behind it. This module caps how many generations run at once and puts deadlines on streams.
"""
from __future__ import annotations

import asyncio
import contextlib
import time
from typing import AsyncIterator

from backend.config import settings
from backend.utils.logger import get_logger

logger = get_logger(__name__)


class LLMBusy(Exception):
    """No generation slot became free in time."""


class LLMStalled(Exception):
    """The model stopped producing output before the stream finished."""


_semaphore: asyncio.Semaphore | None = None
_semaphore_loop: asyncio.AbstractEventLoop | None = None


def _slots() -> asyncio.Semaphore:
    # A semaphore belongs to one event loop, so make a new one if the loop changed (tests).
    global _semaphore, _semaphore_loop
    loop = asyncio.get_running_loop()
    if _semaphore is None or _semaphore_loop is not loop:
        _semaphore = asyncio.Semaphore(max(1, settings.LLM_MAX_CONCURRENT))
        _semaphore_loop = loop
    return _semaphore


@contextlib.asynccontextmanager
async def llm_slot():
    """Hold a generation slot; raise LLMBusy if none is free within LLM_QUEUE_WAIT_SECONDS."""
    slots = _slots()
    try:
        await asyncio.wait_for(slots.acquire(), timeout=settings.LLM_QUEUE_WAIT_SECONDS)
    except asyncio.TimeoutError:
        logger.warning(f"LLM busy: no slot free within {settings.LLM_QUEUE_WAIT_SECONDS}s")
        raise LLMBusy() from None
    try:
        yield
    finally:
        slots.release()


async def guarded_stream(make_stream) -> AsyncIterator:
    """Yield from `make_stream()` with a first-token, an idle and a total deadline.

    Raises LLMStalled when one passes. The stream is always closed afterwards (also when the
    caller is cancelled because the client left); closing drops the connection, which is what
    tells Ollama to stop generating."""
    stream = make_stream()
    started = time.monotonic()
    first = True
    try:
        while True:
            wait = settings.LLM_FIRST_TOKEN_TIMEOUT_SECONDS if first else settings.LLM_STREAM_IDLE_SECONDS
            left = settings.LLM_STREAM_TOTAL_SECONDS - (time.monotonic() - started)
            if left <= 0:
                raise LLMStalled("total")
            try:
                chunk = await asyncio.wait_for(stream.__anext__(), timeout=min(wait, left))
            except StopAsyncIteration:
                return
            except asyncio.TimeoutError:
                raise LLMStalled("first token" if first else "idle") from None
            first = False
            yield chunk
    finally:
        close = getattr(stream, "aclose", None)
        if close is not None:
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await asyncio.shield(close())
