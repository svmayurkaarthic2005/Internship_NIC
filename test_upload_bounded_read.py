"""Regression: an oversized upload must be rejected without buffering the
whole body first.

Root cause (backend/routers/chat.py, `upload_chat_file`): the endpoint used
to do `raw = await file.read()` -- a single unbounded read -- and only
checked `len(raw)` against `UPLOAD_MAX_FILE_BYTES` afterwards. A client
sending a multi-gigabyte body against a 5 MB limit was fully read into
memory before being rejected.

Fix: `_read_bounded()` reads in `UPLOAD_READ_CHUNK_BYTES` (256 KB) steps and
raises as soon as the running total crosses the limit, so at most one chunk
past the limit is ever held.

This test fakes `UploadFile.read(n)` directly (no real HTTP request, no
FastAPI TestClient) so it can prove HOW MUCH was actually pulled from the
source, not just that the final result was correctly sized.

Run: python test_upload_bounded_read.py
"""
import asyncio
import sys

from fastapi import HTTPException

from backend.routers.chat import _read_bounded, UPLOAD_READ_CHUNK_BYTES

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


class _FakeUploadFile:
    """Mimics `UploadFile.read(n)`: hands back at most `n` bytes per call
    from a conceptually unbounded source, and counts how much was pulled."""

    def __init__(self, total_size: int):
        self.remaining = total_size
        self.bytes_served = 0

    async def read(self, n: int) -> bytes:
        take = min(n, self.remaining)
        self.remaining -= take
        self.bytes_served += take
        return b"x" * take


MAX = 5 * 1024 * 1024  # mirrors UPLOAD_MAX_FILE_BYTES default


async def _run():
    # 1) A body far larger than the limit must be rejected, and the source
    #    must never have been asked for anywhere close to its full size.
    huge = _FakeUploadFile(500 * 1024 * 1024)  # 500 MB
    try:
        await _read_bounded(huge, MAX)
        check("a 500 MB body raises instead of succeeding", False)
    except HTTPException as e:
        check("a 500 MB body is rejected with 413", e.status_code == 413, e.status_code)
        check("rejecting it never pulled anywhere near the full 500 MB "
              "(bounded, not buffered whole)",
              huge.bytes_served <= MAX + UPLOAD_READ_CHUNK_BYTES * 2,
              f"pulled {huge.bytes_served} bytes")

    # 2) A body exactly at the limit must succeed.
    exact = _FakeUploadFile(MAX)
    result = await _read_bounded(exact, MAX)
    check("a body exactly at the limit is accepted", len(result) == MAX, len(result))

    # 3) One byte over the limit must be rejected.
    over = _FakeUploadFile(MAX + 1)
    try:
        await _read_bounded(over, MAX)
        check("one byte over the limit raises", False)
    except HTTPException as e:
        check("one byte over the limit is rejected with 413", e.status_code == 413)

    # 4) An ordinary small file is read back byte-for-byte, unchanged.
    small = _FakeUploadFile(1234)
    result = await _read_bounded(small, MAX)
    check("a small, well-under-limit file is read back in full", len(result) == 1234)

    # 5) An empty file returns empty bytes (the caller's own 422 check fires
    #    on this, not inside _read_bounded).
    empty = _FakeUploadFile(0)
    result = await _read_bounded(empty, MAX)
    check("an empty file reads back as b''", result == b"")


asyncio.run(_run())
print(f"{total - len(bad)}/{total} ok")
sys.exit(1 if bad else 0)
