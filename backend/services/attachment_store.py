"""Persistent storage, authorization and evidence retrieval for chat attachments.

An attachment used to live in a dict in the worker process: it vanished on
restart, it was keyed by a client-supplied session id, and the whole of it was
pasted into the prompt. This module replaces that with three PostgreSQL tables
(`chat_attachments`, `attachment_chunks`, `attachment_rows`) and a retrieval
step, so an answer is built from the few chunks that bear on the question and
each one still knows where it came from.

Three boundaries are load-bearing:

1. **The officer comes from the JWT, never from the request body.** Every
   function here takes an `officer_id` the caller derived from
   `get_current_officer()`, and every statement filters on it *and* on the
   session. A `document_id` from the client is a lookup key, never a grant --
   one belonging to another officer resolves to "not found", exactly as a
   made-up id does.
2. **Private attachments are not in `knowledge_embeddings`.** That table holds
   the shared SIS policy corpus every officer may read. An officer's uploaded
   file lives in its own tables and is never returned by
   `pgvector_store.similarity_search()`.
3. **Retention is explicit.** Rows carry `expires_at` and a sweep removes what
   is past it, so a restart loses nothing that has not expired -- the failure
   mode the in-memory store had.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import delete, func, select, text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models import (AttachmentChunk, AttachmentRow, ChatAttachment,
                            ChatSession)
from backend.services import doc_extract
from backend.services.doc_extract import (ExtractedDocument, Segment,
                                          citation_label)
from backend.utils.logger import get_logger

logger = get_logger(__name__)


class AttachmentAccessError(Exception):
    """The officer may not touch this session or document."""


# ─────────────────────────────────────────────────────────────────────────────
# Evidence
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Evidence:
    """One retrieved chunk, with the citation rendered from its stored location."""
    document_id: str
    filename: str
    chunk_index: int
    content: str
    citation: str
    location: Dict[str, Any]
    score: float = 0.0
    lexical: float = 0.0
    vector: Optional[float] = None


# ─────────────────────────────────────────────────────────────────────────────
# Raw-file storage — server-generated names, outside any served directory
# ─────────────────────────────────────────────────────────────────────────────

def storage_dir() -> Path:
    d = Path(settings.UPLOAD_STORAGE_DIR)
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:      # Windows / mounted filesystems
        pass
    return d


def _write_raw(raw: bytes, ext: str) -> Optional[str]:
    """Persist the original bytes under a name the client had no part in."""
    if not settings.UPLOAD_STORE_RAW_FILES:
        return None
    safe_ext = ext if re.fullmatch(r"\.[a-z0-9]{1,8}", ext or "") else ".bin"
    stored = f"{uuid.uuid4().hex}{safe_ext}"
    path = storage_dir() / stored
    with open(path, "wb") as fh:
        fh.write(raw)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return stored


def _remove_raw(stored_name: Optional[str]) -> None:
    if not stored_name:
        return
    # Defence in depth: the name is server-generated, and it is still resolved
    # inside the storage directory rather than joined blindly.
    candidate = (storage_dir() / os.path.basename(stored_name)).resolve()
    if candidate.parent != storage_dir().resolve():
        return
    try:
        candidate.unlink(missing_ok=True)
    except OSError as e:
        logger.warning(f"attachment raw file not removed: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Authorization
# ─────────────────────────────────────────────────────────────────────────────

def _as_uuid(value: Any) -> Optional[uuid.UUID]:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


async def verify_session(db: AsyncSession, session_id: Any,
                         officer_id: Any) -> ChatSession:
    """Confirm the chat session exists and belongs to this officer."""
    sid, oid = _as_uuid(session_id), _as_uuid(officer_id)
    if sid is None or oid is None:
        raise AttachmentAccessError("Invalid session or officer identifier.")
    row = (await db.execute(
        select(ChatSession).where(ChatSession.id == sid))).scalar_one_or_none()
    if row is None:
        raise AttachmentAccessError("Chat session not found.")
    if row.officer_id != oid:
        # Deliberately the same wording an unknown session gets: whether a
        # session exists is not something another officer gets to learn.
        raise AttachmentAccessError("Chat session not found.")
    return row


# ─────────────────────────────────────────────────────────────────────────────
# Chunking
# ─────────────────────────────────────────────────────────────────────────────

def _merge_location(a: Dict[str, Any], b: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Combine two adjacent locations of the same kind, or None if they can't be."""
    if a.get("kind") != b.get("kind"):
        return None
    kind = a.get("kind")
    if kind == "page":
        # Deliberately never merged. "order.pdf, pages 1-3" on a three-page
        # order is a citation that points at the whole document, which is the
        # same as not citing anything; a short page keeps its own chunk.
        return None
    if kind in ("paragraphs", "rows", "lines"):
        first = a.get("start")
        last = b.get("end") or b.get("start")
        if first is None or last is None or last < first:
            return None
        return {"kind": kind, "start": first, "end": last}
    return None            # tables are never merged: "table 2" must stay exact


def build_chunks(filename: str, segments: Sequence[Segment],
                 chunk_chars: Optional[int] = None,
                 overlap: Optional[int] = None) -> List[Tuple[str, Dict[str, Any]]]:
    """Turn located segments into (text, location) chunks.

    Adjacent segments of the same kind are merged while they fit, so a short
    page does not become its own chunk; a long one is split, every piece
    keeping that page's location. A citation is therefore always at least as
    precise as the text it labels.
    """
    size = chunk_chars or settings.UPLOAD_CHUNK_CHARS
    lap = overlap if overlap is not None else settings.UPLOAD_CHUNK_OVERLAP
    out: List[Tuple[str, Dict[str, Any]]] = []
    pending_text = ""
    pending_loc: Optional[Dict[str, Any]] = None

    def flush():
        nonlocal pending_text, pending_loc
        if pending_text.strip() and pending_loc is not None:
            out.append((pending_text.strip(), pending_loc))
        pending_text, pending_loc = "", None

    for seg in segments:
        body = (seg.text or "").strip()
        if not body:
            continue
        if len(body) > size:
            flush()
            start = 0
            while start < len(body):
                piece = body[start:start + size]
                out.append((piece.strip(), dict(seg.location)))
                if start + size >= len(body):
                    break
                start += max(1, size - lap)
            continue
        if pending_loc is None:
            pending_text, pending_loc = body, dict(seg.location)
            continue
        merged = _merge_location(pending_loc, seg.location)
        if merged is not None and len(pending_text) + len(body) + 1 <= size:
            pending_text = f"{pending_text}\n{body}"
            pending_loc = merged
        else:
            flush()
            pending_text, pending_loc = body, dict(seg.location)
    flush()
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Persisting an upload
# ─────────────────────────────────────────────────────────────────────────────

async def _embed_chunks(texts: Sequence[str]) -> List[Optional[List[float]]]:
    """Embed chunk texts, or return Nones. An embedding failure never fails an
    upload: retrieval falls back to the lexical score, which needs no Ollama."""
    if not settings.UPLOAD_EMBEDDINGS_ENABLED or not texts:
        return [None] * len(texts)
    try:
        from backend.services.embeddings import batch_embed
        vectors = await asyncio.to_thread(batch_embed, list(texts))
        if len(vectors) != len(texts):
            return [None] * len(texts)
        return list(vectors)
    except Exception as e:
        logger.warning(f"attachment embeddings unavailable, using lexical retrieval only: {e}")
        return [None] * len(texts)


async def save_document(
    db: AsyncSession,
    officer_id: Any,
    session_id: Any,
    filename: str,
    ext: str,
    mime_type: str,
    raw: bytes,
    extracted: ExtractedDocument,
) -> ChatAttachment:
    """Persist one uploaded file, its chunks and (for CSV) its rows.

    The caller has already verified the session belongs to the officer and that
    the bytes match the extension.
    """
    oid, sid = _as_uuid(officer_id), _as_uuid(session_id)
    if oid is None or sid is None:
        raise AttachmentAccessError("Invalid session or officer identifier.")

    now = datetime.now(timezone.utc)
    doc = ChatAttachment(
        officer_id=oid,
        session_id=sid,
        filename=filename,
        stored_name=_write_raw(raw, ext),
        file_ext=ext,
        mime_type=(mime_type or "")[:120],
        byte_size=len(raw),
        content_hash=hashlib.sha256(raw).hexdigest(),
        extraction_status=extracted.status,
        status_detail=extracted.detail or None,
        char_count=extracted.char_count,
        page_count=extracted.page_count,
        csv_headers=extracted.csv_headers,
        csv_row_count=len(extracted.csv_rows) if extracted.csv_rows is not None else None,
        is_active=(extracted.status == doc_extract.STATUS_OK),
        expires_at=now + timedelta(hours=settings.UPLOAD_RETENTION_HOURS),
    )
    db.add(doc)
    await db.flush()      # assigns doc.id

    if extracted.status == doc_extract.STATUS_OK:
        chunks = build_chunks(filename, extracted.segments)
        vectors = await _embed_chunks([c[0] for c in chunks])
        for i, ((body, location), vector) in enumerate(zip(chunks, vectors)):
            db.add(AttachmentChunk(
                document_id=doc.id,
                officer_id=oid,
                session_id=sid,
                chunk_index=i,
                content=body,
                content_hash=hashlib.sha256(body.encode("utf-8")).hexdigest(),
                char_count=len(body),
                location=location,
                citation=citation_label(filename, location) or filename,
                embedding=vector,
            ))
        doc.chunk_count = len(chunks)

        if extracted.csv_rows is not None:
            for n, row in enumerate(extracted.csv_rows, start=1):
                db.add(AttachmentRow(document_id=doc.id, officer_id=oid,
                                     session_id=sid, row_number=n, data=row))

    await db.flush()
    await _retire_surplus(db, oid, sid, keep_id=doc.id)
    await db.commit()
    await db.refresh(doc)
    # Content is never logged — only what the file is and how much of it there is.
    logger.info(
        "chat attachment stored",
        document_id=str(doc.id), ext=ext, bytes=len(raw),
        status=doc.extraction_status, chunks=doc.chunk_count,
        csv_rows=doc.csv_row_count,
    )
    return doc


async def _retire_surplus(db: AsyncSession, officer_id: uuid.UUID,
                          session_id: uuid.UUID, keep_id: uuid.UUID) -> None:
    """Keep only the newest N attachments answerable in a session."""
    limit = max(1, settings.UPLOAD_MAX_DOCS_PER_SESSION)
    rows = (await db.execute(
        select(ChatAttachment)
        .where(ChatAttachment.officer_id == officer_id,
               ChatAttachment.session_id == session_id,
               ChatAttachment.is_active.is_(True))
        .order_by(ChatAttachment.created_at.desc(), ChatAttachment.id)
    )).scalars().all()
    for extra in rows[limit:]:
        if extra.id != keep_id:
            extra.is_active = False


# ─────────────────────────────────────────────────────────────────────────────
# Reading back
# ─────────────────────────────────────────────────────────────────────────────

async def active_documents(db: AsyncSession, officer_id: Any,
                           session_id: Any) -> List[ChatAttachment]:
    """The attachments this officer may currently be answered from, newest first."""
    oid, sid = _as_uuid(officer_id), _as_uuid(session_id)
    if oid is None or sid is None:
        return []
    now = datetime.now(timezone.utc)
    rows = (await db.execute(
        select(ChatAttachment)
        .where(ChatAttachment.officer_id == oid,
               ChatAttachment.session_id == sid,
               ChatAttachment.is_active.is_(True),
               ChatAttachment.extraction_status == doc_extract.STATUS_OK,
               ChatAttachment.expires_at > now)
        .order_by(ChatAttachment.created_at.desc())
    )).scalars().all()
    return list(rows)


async def get_document(db: AsyncSession, officer_id: Any, session_id: Any,
                       document_id: Any) -> Optional[ChatAttachment]:
    """Fetch one attachment, scoped to its owner and session. None if not theirs."""
    oid, sid, did = _as_uuid(officer_id), _as_uuid(session_id), _as_uuid(document_id)
    if oid is None or sid is None or did is None:
        return None
    return (await db.execute(
        select(ChatAttachment).where(ChatAttachment.id == did,
                                     ChatAttachment.officer_id == oid,
                                     ChatAttachment.session_id == sid)
    )).scalar_one_or_none()


async def csv_data(db: AsyncSession, doc: ChatAttachment
                   ) -> Tuple[List[str], List[Dict[str, str]]]:
    """Headers and rows of a stored CSV, in file order."""
    headers = list(doc.csv_headers or [])
    rows = (await db.execute(
        select(AttachmentRow.data)
        .where(AttachmentRow.document_id == doc.id)
        .order_by(AttachmentRow.row_number)
    )).scalars().all()
    return headers, [dict(r) for r in rows]


async def delete_document(db: AsyncSession, officer_id: Any, session_id: Any,
                          document_id: Any) -> bool:
    doc = await get_document(db, officer_id, session_id, document_id)
    if doc is None:
        return False
    stored = doc.stored_name
    await db.execute(delete(ChatAttachment).where(ChatAttachment.id == doc.id))
    await db.commit()
    _remove_raw(stored)
    logger.info("chat attachment deleted", document_id=str(document_id))
    return True


async def cleanup_expired(db: AsyncSession) -> int:
    """Drop attachments past their retention window. Safe to run at any time."""
    now = datetime.now(timezone.utc)
    stale = (await db.execute(
        select(ChatAttachment.id, ChatAttachment.stored_name)
        .where(ChatAttachment.expires_at <= now)
    )).all()
    if not stale:
        return 0
    ids = [row[0] for row in stale]
    await db.execute(delete(ChatAttachment).where(ChatAttachment.id.in_(ids)))
    await db.commit()
    for _, stored in stale:
        _remove_raw(stored)
    logger.info(f"chat attachments expired and removed: {len(ids)}")
    return len(ids)


# ─────────────────────────────────────────────────────────────────────────────
# Retrieval
# ─────────────────────────────────────────────────────────────────────────────

_TOKEN_RE = re.compile(r"[0-9A-Za-z஀-௿/_-]+", re.UNICODE)

_STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "of", "in",
    "on", "at", "to", "for", "from", "by", "with", "and", "or", "it", "its",
    "this", "that", "these", "those", "what", "which", "who", "whom", "when",
    "where", "why", "how", "do", "does", "did", "can", "could", "should",
    "would", "my", "me", "i", "you", "your", "we", "us", "there", "here",
    "any", "all", "please", "tell", "show", "give", "about", "file", "doc",
    "document", "uploaded", "upload", "attached", "attachment", "pdf", "csv",
    "txt", "docx",
}


# Tamil interrogatives and auxiliaries. An officer asking "எப்போது ஒப்புதல்
# அளிக்கப்பட்டது?" is asking one thing — "approved" — and the other two words
# are grammar. Counting them as unmatched content words dragged a perfectly
# answerable question below the evidence floor.
_TA_STOPWORDS = {
    "எப்போது", "எப்பொழுது", "என்ன", "யார்", "யாரு", "எங்கே", "எங்கு", "எது",
    "எத்தனை", "எவ்வளவு", "ஏன்", "எப்படி", "உள்ளது", "இருக்கு", "இருக்கிறது",
    "சொல்", "சொல்லுங்கள்", "கூறு", "தயவு", "செய்து", "பற்றி", "இதில்", "அதில்",
    "enna", "eppo", "eppadi", "yaaru", "yaar", "edhu", "evlo", "evvalavu",
    "irukku", "irukkum", "panna", "pannunga", "pannaanga", "solunga", "sollu",
    # Bare case markers. "order_A.txt இல் கட்டணம் எவ்வளவு?" left "இல்" ("in")
    # standing as a content word of its own, which no English order copy can
    # ever carry -- grammar counted against the evidence.
    "இல்", "இல", "ல்", "ஐ", "க்கு", "ஆல்", "ஓடு", "உடன்", "இன்", "அது", "இது",
    "la", "ku", "oda", "kku",
}
# Passive / participle tails: a Tamil token ending in one of these is a verb
# form, not the thing being asked about.
_TA_SUFFIX_STOP = ("ப்பட்டது", "ப்பட்ட", "க்கப்பட்டது", "கிறது", "ஆகியுள்ளது")

# Domain equivalences, so a question in one script can find evidence written in
# the other. Keys are matched as a prefix of the officer's word (Tamil
# agglutinates, so "ஒப்புதலுக்கு" must still reach "ஒப்புத"); values are the
# words the document may be using instead. Kept small and domain-specific —
# this is a lookup table for SIS vocabulary, not a translator.
_ALIASES: Dict[str, Tuple[str, ...]] = {
    "ஒப்புத": ("approved", "approve", "approval", "ஒப்புதல்"),
    "நிராகரி": ("rejected", "reject", "rejection", "நிராகரிப்பு"),
    "விண்ணப்பதாரர": ("applicant", "விண்ணப்பதாரர்"),
    "விண்ணப்ப": ("application", "விண்ணப்பம்"),
    "எண": ("number", "no", "எண்"),
    "தேதி": ("date", "dated", "தேதி"),
    "நாள": ("date", "day", "நாள்"),
    "சர்வே": ("survey", "சர்வே"),
    "நில அளவை": ("survey", "நில அளவை"),
    "கட்டண": ("fee", "amount", "கட்டணம்"),
    "தொகை": ("amount", "total", "தொகை"),
    "உரிமையாளர": ("owner", "உரிமையாளர்"),
    "வார்ட": ("ward", "வார்டு"),
    "நிலை": ("status", "நிலை"),
    "பட்டா": ("patta", "பட்டா"),
    "கள ஆய்வ": ("field visit", "inspection", "inspected", "கள ஆய்வு"),
    "ஆய்வ": ("inspection", "inspected", "ஆய்வு"),
    "பரப்ப": ("extent", "area", "பரப்பளவு"),
    "பெயர": ("name", "பெயர்"),
    "approve": ("approved", "approval", "ஒப்புதல்"),
    "reject": ("rejected", "rejection", "நிராகரிப்பு"),
    "fee": ("fee", "amount", "கட்டணம்"),
    "survey": ("survey", "சர்வே"),
    "applicant": ("applicant", "விண்ணப்பதாரர்"),
    "application": ("application", "விண்ணப்பம்"),
    "ward": ("ward", "வார்டு"),
    "status": ("status", "நிலை"),
    "owner": ("owner", "உரிமையாளர்"),
    "date": ("date", "தேதி"),
}

_TAMIL_RE = re.compile(r"[஀-௿]")


def _tokens(text: str) -> List[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text or "")]


def _is_noise(token: str) -> bool:
    if token in _STOPWORDS or token in _TA_STOPWORDS or len(token) < 2:
        return True
    if _TAMIL_RE.search(token) and token.endswith(_TA_SUFFIX_STOP):
        return True
    return False


def _term_groups(question: str) -> List[Tuple[str, ...]]:
    """The question's content words, each with the equivalents it may appear as."""
    groups: List[Tuple[str, ...]] = []
    seen: set = set()
    for t in _tokens(question):
        if _is_noise(t) or t in seen:
            continue
        seen.add(t)
        alts = {t}
        for key, values in _ALIASES.items():
            if key in t or t.startswith(key):
                alts.update(values)
        groups.append(tuple(sorted(alts)))
    return groups


def _content_terms(question: str) -> List[str]:
    return [g[0] for g in _term_groups(question)]


def _present(alt: str, tokenised: str, raw: str) -> bool:
    if _TAMIL_RE.search(alt):
        # Tamil is matched as a substring, never with a word boundary: the
        # virama is not a word character, so \b finds boundaries mid-word.
        return alt in raw
    return re.search(rf"(?<![0-9a-z஀-௿]){re.escape(alt)}", tokenised) is not None


def strip_filenames(question: str, filenames: Sequence[str]) -> str:
    """Remove the given filenames (and their stems) from a question before it
    is scored against document content -- see the call site in
    `retrieve_evidence` for why."""
    out = question or ""
    for name in filenames:
        name = (name or "").strip()
        if not name:
            continue
        stem = name.rsplit(".", 1)[0]
        for token in {name, stem}:
            if len(token) >= 3:
                out = re.sub(re.escape(token), " ", out, flags=re.IGNORECASE)
    return out


# Words that name the SHAPE of a field rather than which field it is. They sit
# in almost every land record, so on their own they are not evidence that the
# thing asked about is present: "what is the aadhaar number in order_B.txt?"
# scored 0.5 -- over the 0.4 floor -- because "number" appears all over an
# order copy while "aadhaar" appears nowhere in it, and llama3.1:8b duly
# answered *"The Aadhaar number is 133280199887766"*, which is the CAN number
# relabelled. A question whose distinctive words are all absent has no
# evidence, whatever its generic words match.
_GENERIC_FIELD_TERMS = {
    "number", "numbers", "no", "nos", "name", "names", "date", "dates",
    "detail", "details", "value", "values", "code", "codes", "amount",
    "amounts", "id", "reference", "field", "record", "records", "entry",
    "information", "info", "data", "document", "documents", "file", "files",
    "எண்", "எண்கள்", "பெயர்", "தேதி", "விவரம்", "விவரங்கள்", "மதிப்பு",
    "குறியீடு", "தொகை", "ஆவணம்", "கோப்பு",
}


def _is_generic_field_term(term: str) -> bool:
    return term in _GENERIC_FIELD_TERMS


def lexical_score(question: str, content: str) -> float:
    """Share of the question's content words the chunk carries, plus a phrase bonus.

    Deterministic and dependency-free, which is why it — not the embedding — is
    the floor that decides whether evidence exists at all. An answer must not
    hinge on whether Ollama happened to be up. A word counts as carried if the
    chunk holds it or any of its `_ALIASES` equivalents, so an officer's Tamil
    question reaches an English order copy and the other way round.
    """
    groups = _term_groups(question)
    if not groups:
        return 0.0
    tokenised = " ".join(_tokens(content))
    raw = (content or "").lower()
    if not tokenised:
        return 0.0
    carried = [any(_present(a, tokenised, raw) for a in g) for g in groups]
    # The distinctive half of the question has to land somewhere. If every
    # term that says WHICH field was asked about is missing, the generic ones
    # ("number", "name", "date") cannot carry the chunk over the floor.
    # Genericness is a property of the GROUP, not of its alphabetically first
    # member: `_term_groups` sorts the alias set, so "கட்டணம்" arrives as
    # ('amount', 'fee', 'கட்டணம்') and judging it by "amount" alone would
    # classify a fee question as generic. A group is generic only if every
    # spelling of it is.
    specific = [i for i, g in enumerate(groups)
                if not all(_is_generic_field_term(a) for a in g)]
    if specific and not any(carried[i] for i in specific):
        return 0.0
    hits = sum(1 for c in carried if c)
    score = hits / len(groups)
    phrase = " ".join(g[0] for g in groups[:4])
    if phrase and phrase in tokenised:
        score = min(1.0, score + 0.15)
    return score


async def _vector_distances(db: AsyncSession, question: str,
                            document_ids: Sequence[uuid.UUID]
                            ) -> Dict[Tuple[str, int], float]:
    """Cosine distance per chunk from pgvector, or {} when embeddings are unusable."""
    if not settings.UPLOAD_EMBEDDINGS_ENABLED or not document_ids:
        return {}
    try:
        from backend.services.embeddings import generate_embedding
        vec = await asyncio.to_thread(generate_embedding, question)
    except Exception as e:
        logger.info(f"attachment vector search skipped (no embedding): {e}")
        return {}
    try:
        rows = (await db.execute(sql_text("""
            SELECT document_id, chunk_index,
                   embedding <=> CAST(:vec AS vector) AS distance
            FROM attachment_chunks
            WHERE document_id = ANY(CAST(:ids AS uuid[]))
              AND embedding IS NOT NULL
        """), {"vec": "[" + ",".join(f"{v:.6f}" for v in vec) + "]",
               "ids": [str(d) for d in document_ids]})).all()
    except Exception as e:
        logger.warning(f"attachment vector search failed, lexical only: {e}")
        return {}
    return {(str(r[0]), int(r[1])): float(r[2]) for r in rows}


async def leading_chunks(
    db: AsyncSession,
    officer_id: Any,
    session_id: Any,
    document_ids: Sequence[Any],
    per_document: int = 3,
) -> List[Evidence]:
    """The opening chunks of each document, for a request with no search terms.

    "Compare order.pdf and report.docx" names its sources but carries no words
    to retrieve on, so the lexical floor rejects everything and the officer gets
    a refusal to a perfectly reasonable question. These are still real stored
    chunks with real citations — the same evidence, selected by position instead
    of by score — so nothing about the grounding rules changes.
    """
    oid, sid = _as_uuid(officer_id), _as_uuid(session_id)
    dids = [d for d in (_as_uuid(x) for x in document_ids) if d is not None]
    if oid is None or sid is None or not dids:
        return []
    out: List[Evidence] = []
    for did in dids:
        rows = (await db.execute(
            select(AttachmentChunk, ChatAttachment.filename)
            .join(ChatAttachment, ChatAttachment.id == AttachmentChunk.document_id)
            .where(AttachmentChunk.document_id == did,
                   AttachmentChunk.officer_id == oid,
                   AttachmentChunk.session_id == sid,
                   ChatAttachment.is_active.is_(True))
            .order_by(AttachmentChunk.chunk_index)
            .limit(max(1, per_document))
        )).all()
        for chunk, filename in rows:
            out.append(Evidence(
                document_id=str(chunk.document_id), filename=filename,
                chunk_index=chunk.chunk_index, content=chunk.content,
                citation=chunk.citation or filename,
                location=dict(chunk.location or {}), score=0.0))
    return out


async def chunks_covering_location(
    db: AsyncSession,
    officer_id: Any,
    session_id: Any,
    document_ids: Sequence[Any],
    kind: str,
    number: int,
) -> List[Evidence]:
    """The chunk(s) whose stored location actually covers this page / line /
    paragraph / table number, regardless of how few content words the
    question carries.

    "What is on line 5 of workflow_notes.txt?" and "does page 3 mention the
    fee?" ask about STRUCTURE, not content — there is nothing for the lexical
    or vector scorer in `retrieve_evidence` to match, so both were refused
    with "I could not find this in the uploaded document" even when the
    location plainly exists and was already stored with exactly the range
    needed to answer. `location["kind"]` already carries this shape
    (`doc_extract.py`): "page" (a single page number), "lines" / "paragraphs"
    (a start/end range), "table" (a table number, with its own row range).
    Returned with score=0.0, the same convention `leading_chunks` uses for
    evidence selected by position rather than by relevance score — a
    positional match is not less real evidence, so it is never held to the
    lexical floor.
    """
    oid, sid = _as_uuid(officer_id), _as_uuid(session_id)
    dids = [d for d in (_as_uuid(x) for x in document_ids) if d is not None]
    if oid is None or sid is None or not dids:
        return []
    rows = (await db.execute(
        select(AttachmentChunk, ChatAttachment.filename)
        .join(ChatAttachment, ChatAttachment.id == AttachmentChunk.document_id)
        .where(AttachmentChunk.document_id.in_(dids),
               AttachmentChunk.officer_id == oid,
               AttachmentChunk.session_id == sid,
               ChatAttachment.officer_id == oid,
               ChatAttachment.session_id == sid,
               ChatAttachment.is_active.is_(True))
        .order_by(AttachmentChunk.document_id, AttachmentChunk.chunk_index)
    )).all()
    out: List[Evidence] = []
    for chunk, filename in rows:
        loc = chunk.location or {}
        if loc.get("kind") != kind:
            continue
        if kind == "page":
            hit = loc.get("page") == number
        elif kind == "table":
            hit = loc.get("table") == number
        else:  # "lines" / "paragraphs" — a start..end range
            start, end = loc.get("start"), loc.get("end")
            hit = start is not None and start <= number <= (end if end is not None else start)
        if hit:
            out.append(Evidence(
                document_id=str(chunk.document_id), filename=filename,
                chunk_index=chunk.chunk_index, content=chunk.content,
                citation=chunk.citation or filename,
                location=dict(loc), score=0.0))
    return out


async def location_extent(
    db: AsyncSession,
    officer_id: Any,
    session_id: Any,
    document_ids: Sequence[Any],
    kind: str,
) -> Dict[str, int]:
    """The highest page / line / paragraph / table number actually stored for
    each of these documents, keyed by filename — so a request for a location
    that does not exist ("page 10" of a 3-page order) can be told the real
    extent instead of the generic "I could not find this" refusal, which
    reads as a failed search rather than as "there is no such page"."""
    oid, sid = _as_uuid(officer_id), _as_uuid(session_id)
    dids = [d for d in (_as_uuid(x) for x in document_ids) if d is not None]
    if oid is None or sid is None or not dids:
        return {}
    rows = (await db.execute(
        select(AttachmentChunk.location, ChatAttachment.filename)
        .join(ChatAttachment, ChatAttachment.id == AttachmentChunk.document_id)
        .where(AttachmentChunk.document_id.in_(dids),
               AttachmentChunk.officer_id == oid,
               AttachmentChunk.session_id == sid,
               ChatAttachment.is_active.is_(True))
    )).all()
    extents: Dict[str, int] = {}
    for loc, filename in rows:
        loc = loc or {}
        if loc.get("kind") != kind:
            continue
        value = loc.get("page") or loc.get("table") or loc.get("end") or loc.get("start")
        if value is None:
            continue
        extents[filename] = max(extents.get(filename, 0), value)
    return extents


async def retrieve_evidence(
    db: AsyncSession,
    officer_id: Any,
    session_id: Any,
    document_ids: Sequence[Any],
    question: str,
    top_k: Optional[int] = None,
    min_score: Optional[float] = None,
) -> List[Evidence]:
    """Return the chunks that bear on the question, from these documents only.

    `document_ids` is re-checked against the officer and session here, so a
    caller that resolved them loosely cannot widen the scope, and one document's
    chunks can never surface under another document's name.
    """
    oid, sid = _as_uuid(officer_id), _as_uuid(session_id)
    dids = [d for d in (_as_uuid(x) for x in document_ids) if d is not None]
    if oid is None or sid is None or not dids:
        return []
    k = top_k or settings.UPLOAD_RETRIEVAL_TOP_K
    floor = settings.UPLOAD_MIN_EVIDENCE_SCORE if min_score is None else min_score

    rows = (await db.execute(
        select(AttachmentChunk, ChatAttachment.filename)
        .join(ChatAttachment, ChatAttachment.id == AttachmentChunk.document_id)
        .where(AttachmentChunk.document_id.in_(dids),
               AttachmentChunk.officer_id == oid,
               AttachmentChunk.session_id == sid,
               ChatAttachment.officer_id == oid,
               ChatAttachment.session_id == sid,
               ChatAttachment.is_active.is_(True))
        .order_by(AttachmentChunk.document_id, AttachmentChunk.chunk_index)
    )).all()
    if not rows:
        return []

    # The officer is told to name the file ("In sale_deed.pdf, what is the
    # document number?") -- doing so used to cost the question a whole
    # lexical-score group, because the filename tokenises into a content word
    # (often one compound token, "sample_encumbrance_certificate") that will
    # never appear inside the document's own prose. "what is the issued by
    # mentioned in sample_encumbrance_certificate.pdf" scored 0.33 and was
    # refused as no-evidence purely because it named its own file. Strip the
    # filenames actually in play before scoring; `mentions_filename` already
    # used them for file SELECTION, so they carry no more information here.
    question_for_scoring = strip_filenames(question, {fn for _, fn in rows})

    distances = await _vector_distances(db, question_for_scoring, [r[0].document_id for r in rows])

    scored: List[Evidence] = []
    for chunk, filename in rows:
        lex = lexical_score(question_for_scoring, chunk.content)
        dist = distances.get((str(chunk.document_id), chunk.chunk_index))
        vec_score = (1.0 - dist) if dist is not None else None
        # Lexical decides whether there is evidence; the vector only reorders
        # among chunks that already carry the question's words, so a confident
        # embedding can never manufacture evidence out of an unrelated file.
        combined = lex if vec_score is None else (0.65 * lex + 0.35 * max(0.0, vec_score))
        scored.append(Evidence(
            document_id=str(chunk.document_id),
            filename=filename,
            chunk_index=chunk.chunk_index,
            content=chunk.content,
            citation=chunk.citation or filename,
            location=dict(chunk.location or {}),
            score=combined, lexical=lex, vector=vec_score,
        ))

    kept = [e for e in scored if e.lexical >= floor]
    kept.sort(key=lambda e: (-e.score, e.filename, e.chunk_index))
    return kept[:k]
