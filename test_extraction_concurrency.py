"""Regression: extraction/OCR concurrency is actually bounded, and a slot a
timed-out request gave up on stays occupied until the real work finishes --
not released early just because the caller stopped waiting.

Root cause this replaces: `asyncio.wait_for(asyncio.to_thread(...))` alone
only stops AWAITING a thread; the OCR call already running inside it keeps
consuming CPU. Nothing capped how many such threads could pile up, so
several uploads at once -- or the same slow file retried after each timeout
-- had no ceiling on concurrent OCR work.

Fix: `backend.routers.chat._extract_bounded()` wraps extraction in a
process-wide `asyncio.Semaphore` (`UPLOAD_MAX_CONCURRENT_EXTRACTIONS`),
acquired before the thread starts and released only when the thread itself
finishes (in a `finally`, not when the outer wait_for gives up) -- plus a
bounded queue-wait (`UPLOAD_QUEUE_WAIT_SECONDS`) that rejects a request with
503 rather than queueing it forever.

Exercises real threads via `asyncio.to_thread` (no event-loop mocking), but
no real OCR/Tesseract/DB -- `doc_extract.extract` is monkeypatched to a
controllable, sleeping stand-in.

Run: python test_extraction_concurrency.py
"""
import asyncio
import sys
import time

from fastapi import HTTPException

import backend.routers.chat as chat_router
from backend.services import doc_extract
from backend.config import settings

bad = []
total = 0


def check(name, condition, detail=""):
    global total
    total += 1
    if condition:
        print(f"ok   {name}")
    else:
        bad.append((name, detail))
        print(f"FAIL {name}" + (f" -- {detail}" if detail else ""))


_lock_counter = {"current": 0, "max": 0}
_counter_lock = __import__("threading").Lock()


def _slow_extract(ext, raw, delay=0.25):
    with _counter_lock:
        _lock_counter["current"] += 1
        _lock_counter["max"] = max(_lock_counter["max"], _lock_counter["current"])
    time.sleep(delay)
    with _counter_lock:
        _lock_counter["current"] -= 1
    return doc_extract.ExtractedDocument(status=doc_extract.STATUS_OK)


async def _run():
    # --- A) concurrency is actually capped ------------------------------
    chat_router._extraction_semaphore = asyncio.Semaphore(2)
    settings.UPLOAD_QUEUE_WAIT_SECONDS = 5.0
    settings.UPLOAD_EXTRACTION_TIMEOUT_SECONDS = 5.0
    orig_extract = doc_extract.extract
    doc_extract.extract = lambda ext, raw: _slow_extract(ext, raw, delay=0.25)
    _lock_counter["current"] = 0
    _lock_counter["max"] = 0
    try:
        await asyncio.gather(*[
            chat_router._extract_bounded("txt", b"x") for _ in range(5)
        ])
        check("5 concurrent extraction requests never exceed the cap of 2",
              _lock_counter["max"] <= 2, f"observed max concurrency {_lock_counter['max']}")
    finally:
        doc_extract.extract = orig_extract

    # --- B) backpressure: no free slot within the queue-wait -> 503 -----
    chat_router._extraction_semaphore = asyncio.Semaphore(1)
    settings.UPLOAD_QUEUE_WAIT_SECONDS = 0.1
    doc_extract.extract = lambda ext, raw: _slow_extract(ext, raw, delay=1.0)
    try:
        hog = asyncio.ensure_future(chat_router._extract_bounded("txt", b"x"))
        await asyncio.sleep(0.05)  # let the hog actually acquire the one slot
        try:
            await chat_router._extract_bounded("txt", b"y")
            check("a second request with no free slot is rejected, not queued forever", False)
        except HTTPException as e:
            check("a second request with no free slot inside the queue-wait gets 503",
                  e.status_code == 503, e.status_code)
        await hog
    finally:
        doc_extract.extract = orig_extract

    # --- C) a slot a timed-out caller gave up on stays occupied ---------
    chat_router._extraction_semaphore = asyncio.Semaphore(1)
    settings.UPLOAD_EXTRACTION_TIMEOUT_SECONDS = 0.05  # caller gives up almost immediately
    settings.UPLOAD_QUEUE_WAIT_SECONDS = 0.3
    doc_extract.extract = lambda ext, raw: _slow_extract(ext, raw, delay=0.5)
    try:
        try:
            await chat_router._extract_bounded("txt", b"x")
            check("the first call times out (caller stops waiting)", False)
        except asyncio.TimeoutError:
            check("the first call times out as expected", True)

        # The abandoned thread is still running (0.5s sleep). A second call
        # right away must find NO free slot and hit the queue-wait 503 --
        # proving the semaphore was NOT released just because the caller gave up.
        t0 = time.monotonic()
        try:
            await chat_router._extract_bounded("txt", b"y")
            check("a second call right after the timeout still finds no free slot", False)
        except HTTPException as e:
            elapsed = time.monotonic() - t0
            check("the orphaned job's slot is still held (second call waits out the "
                  "queue-wait and gets 503, proving the slot was not freed early)",
                  e.status_code == 503 and elapsed >= settings.UPLOAD_QUEUE_WAIT_SECONDS * 0.8,
                  f"status={e.status_code} elapsed={elapsed:.2f}s")

        # Once the orphaned job actually finishes (~0.5s after it started),
        # the slot becomes free again for a fresh call.
        await asyncio.sleep(0.6)
        doc_extract.extract = lambda ext, raw: doc_extract.ExtractedDocument(
            status=doc_extract.STATUS_OK)
        result = await chat_router._extract_bounded("txt", b"z")
        check("once the orphaned job genuinely finishes, the slot is free again",
              result.status == doc_extract.STATUS_OK)
    finally:
        doc_extract.extract = orig_extract


asyncio.run(_run())
print(f"{total - len(bad)}/{total} ok")
sys.exit(1 if bad else 0)
