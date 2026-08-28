"""In-memory, session-scoped store for text pulled from officer file uploads.

`POST /api/v1/chat/upload` parses a `.txt` / `.csv` the officer attaches and
keeps the extracted text here, keyed by chat session, so every later message in
that session can be answered from it — not only the turn it was uploaded on.

Single-process only (the app runs one uvicorn worker). Nothing is persisted; the
store clears on restart and entries expire, which is fine for a working
attachment. PDF / Word are handled by the router as "not supported yet" and
never reach this module.
"""
from __future__ import annotations

import time
from collections import OrderedDict
from threading import Lock

try:
    from backend.config import settings as _settings
    _CFG_MAX_CHARS = _settings.UPLOAD_MAX_DOC_CHARS
except Exception:  # keep importable outside the app
    _CFG_MAX_CHARS = 20_000

MAX_SESSIONS = 200               # cap total sessions held at once
MAX_DOCS_PER_SESSION = 5         # keep only the most recent uploads per session
MAX_CHARS_PER_DOC = _CFG_MAX_CHARS  # trim a very large file so prompts stay sane
TTL_SECONDS = 12 * 3600          # drop an upload after this long
DEFAULT_CONTEXT_CHARS = 16_000   # total budget when rendering docs into a prompt

_lock = Lock()
_store: "OrderedDict[str, list[dict]]" = OrderedDict()


def add(session_id: str, filename: str, text: str) -> dict:
    """Store extracted text for a session, returning the stored record."""
    text = (text or "").strip()
    truncated = len(text) > MAX_CHARS_PER_DOC
    if truncated:
        text = text[:MAX_CHARS_PER_DOC]
    doc = {
        "filename": filename,
        "text": text,
        "chars": len(text),
        "truncated": truncated,
        "at": time.time(),
    }
    with _lock:
        docs = [d for d in (_store.get(session_id) or []) if d["filename"] != filename]
        docs.append(doc)
        _store[session_id] = docs[-MAX_DOCS_PER_SESSION:]
        _store.move_to_end(session_id)
        while len(_store) > MAX_SESSIONS:
            _store.popitem(last=False)
    return doc


def get(session_id: str) -> list[dict]:
    """Return the (non-expired) uploads for a session, newest last."""
    now = time.time()
    with _lock:
        docs = [d for d in (_store.get(session_id) or []) if now - d["at"] < TTL_SECONDS]
        if docs:
            _store[session_id] = docs
        else:
            _store.pop(session_id, None)
        return list(docs)


def clear(session_id: str) -> None:
    with _lock:
        _store.pop(session_id, None)


def has_docs(session_id: str) -> bool:
    return bool(get(session_id))


def context_block(session_id: str, max_chars: int = DEFAULT_CONTEXT_CHARS) -> str:
    """Render every upload for the session into a prompt block, or '' if none.

    The budget is shared fairly: each doc gets at least an equal share, and any
    slice a small doc doesn't use is handed to the larger ones. So five files
    all appear, and a big file never starves the others out of the prompt.
    """
    docs = get(session_id)
    if not docs:
        return ""

    ordered = list(reversed(docs))  # most recent first
    per_doc = max(1, max_chars // len(ordered))
    # first pass: give each doc its share, collect leftover from the ones under it
    take = {}
    leftover = 0
    for i, d in enumerate(ordered):
        want = min(len(d["text"]), per_doc)
        take[i] = want
        leftover += per_doc - want
    # second pass: spread leftover over docs that still have more text
    for i, d in enumerate(ordered):
        if leftover <= 0:
            break
        room = len(d["text"]) - take[i]
        if room > 0:
            extra = min(room, leftover)
            take[i] += extra
            leftover -= extra

    parts = []
    for i, d in enumerate(ordered):
        snippet = d["text"][:take[i]]
        clipped = d["truncated"] or take[i] < len(d["text"])
        head = f'--- Uploaded file: {d["filename"]} ({d["chars"]} chars'
        head += ", showing part ---" if clipped else " ---"
        parts.append(f"{head}\n{snippet}")
    return "\n\n".join(parts)


def filenames(session_id: str) -> list[str]:
    return [d["filename"] for d in get(session_id)]
