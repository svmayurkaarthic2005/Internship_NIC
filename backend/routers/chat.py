"""
Chat Router
Endpoints for chatbot interactions
"""
from fastapi import (
    APIRouter, Depends, HTTPException, BackgroundTasks, status,
    UploadFile, File, Form,
)
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Any, Dict
from datetime import datetime, timezone
from uuid import UUID
import asyncio
import concurrent.futures as _cf

from backend.config import settings
from backend.database import get_db
from backend.schemas import StandardResponse, OfficerContext
from backend.dependencies import get_current_officer
from backend.services import attachment_store
from backend.services import doc_extract
from backend.services.chatbot import (
    process_chat,
    process_chat_stream,
    create_chat_session,
    get_session_history,
    get_officer_sessions,
    save_chat_messages,
)
from backend.models import AuditLog, ChatSession
from backend.utils.logger import get_logger

router = APIRouter(prefix="/api/v1/chat", tags=["Chat"])
logger = get_logger(__name__)


# Request/Response schemas
class ChatRequest(BaseModel):
    """Chat message request"""
    message: str = Field(..., max_length=1000, description="User message")
    session_id: str = Field(..., description="Chat session UUID")
    language: str = Field(default="auto", description="Language (auto, en, ta, tanglish)")
    chat_history: Optional[list] = Field(default=None, description="Previous chat messages from sessionStorage")

    @field_validator("chat_history")
    @classmethod
    def _chat_history_items_are_role_content_dicts(cls, v):
        # The whole downstream pipeline (`chatbot.py`, ~30000 lines) reads
        # each entry as `m.get("role")` / `m.get("content")` with no
        # isinstance check anywhere -- a malformed entry (sessionStorage
        # corruption, a hand-crafted request) crashed deep in an unrelated
        # helper with a raw AttributeError surfaced as a 500. Validating the
        # shape here, once, turns that into the same controlled 422 every
        # other malformed field already gets, instead of chasing every one
        # of that function's many `.get()` call sites individually.
        if v is None:
            return v
        for i, item in enumerate(v):
            if not (isinstance(item, dict) and isinstance(item.get("role"), str)
                    and isinstance(item.get("content"), str)):
                raise ValueError(
                    f"chat_history[{i}] must be an object with string 'role' and 'content' fields")
        return v


class ChatResponse(BaseModel):
    """Chat message response"""
    response: str
    language: str
    session_id: str
    timestamp: str
    intent: Optional[str] = None
    sources: Optional[List[Any]] = Field(default_factory=list)
    context_used: bool = False
    response_time_ms: Optional[int] = None
    table_data: Optional[Dict[str, Any]] = None


class SessionCreateResponse(BaseModel):
    """New session response"""
    session_id: str
    session_token: str
    started_at: str


# Background task functions
async def log_chat_interaction(
    db: AsyncSession,
    officer_id: UUID,
    session_id: str,
    message: str,
    response_time_ms: int
):
    """Log chat interaction to audit log"""
    try:
        audit_entry = AuditLog(
            officer_id=officer_id,
            action="chat_interaction",
            entity_type="chat_session",
            entity_id=session_id,
            new_values={
                "message_length": len(message),
                "response_time_ms": response_time_ms
            },
            created_at=datetime.utcnow()
        )
        db.add(audit_entry)
        await db.commit()
    except Exception as e:
        logger.error(f"Error logging chat interaction: {e}")
        await db.rollback()


# Endpoints
@router.post("", response_model=StandardResponse, status_code=status.HTTP_200_OK)
async def send_chat_message(
    request: ChatRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_officer: OfficerContext = Depends(get_current_officer)
):
    """
    Send a message to the AI chatbot and get a response
    
    - Detects language automatically (English, Tamil, or Tanglish)
    - Uses RAG (Retrieval-Augmented Generation) with pgvector
    - Queries structured data from PostgreSQL based on intent
    - Returns AI-generated response from Llama 3.1
    """
    try:
        # Validate session_id is a valid UUID
        try:
            session_uuid = UUID(request.session_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid session_id format. Must be a valid UUID."
            )
        
        # Process chat message
        result = await process_chat(
            message=request.message,
            session_id=request.session_id,
            officer=current_officer,
            db=db,
            chat_history=request.chat_history
        )
        
        # Schedule background tasks
        if "error" not in result:
            background_tasks.add_task(
                log_chat_interaction,
                db,
                current_officer.officer_id,
                request.session_id,
                request.message,
                result.get("response_time_ms", 0)
            )
        
        # Prepare response
        response_data = {
            "response": result["response"],
            "language": result["language"],
            "session_id": request.session_id,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "intent": result.get("intent"),
            "sources": result.get("sources", []),
            "context_used": result.get("context_used", False),
            "response_time_ms": result.get("response_time_ms"),
            "table_data": result.get("table_data"),
            # Present only when the turn is a command the client must carry
            # out -- currently "clear_chat", from a typed "clear".
            "action": result.get("action")
        }
        
        return StandardResponse.success_response(
            data=response_data,
            message="Chat response generated successfully"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in send_chat_message: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error processing chat message. Please try again."
        )


@router.post("/stream")
async def stream_chat_message(
    request: ChatRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_officer: OfficerContext = Depends(get_current_officer)
):
    """
    Stream a message response from the AI chatbot
    Uses Server-Sent Events (SSE)
    """
    try:
        try:
            session_uuid = UUID(request.session_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid session_id format. Must be a valid UUID."
            )
            
        async def _with_end_marker(chunks):
            # Lets the page tell a finished reply from one cut off mid-stream.
            async for chunk in chunks:
                yield chunk
            yield b'data: {"done": true}\n\n'

        return StreamingResponse(
            _with_end_marker(process_chat_stream(
                message=request.message,
                session_id=request.session_id,
                officer=current_officer,
                db=db,
                chat_history=request.chat_history
            )),
            media_type="text/event-stream"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in stream_chat_message: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error processing stream request. Please try again."
        )


# ── Attachments ──────────────────────────────────────────────────────────────
# The officer is taken from the JWT; the session and every document are checked
# against that officer server-side. Nothing in the request body decides who the
# caller is, and a document_id that is not theirs is indistinguishable from one
# that does not exist.

UPLOAD_READ_CHUNK_BYTES = 256 * 1024  # read in 256 KB steps, never the whole body at once


async def _read_bounded(file: UploadFile, max_bytes: int) -> bytes:
    """Read an UploadFile up to `max_bytes`, rejecting it the moment it is
    exceeded instead of reading the whole (possibly huge) body first.

    `Content-Length` is client-supplied and never trusted as proof of size --
    this measures what was actually read. Stops after at most one chunk past
    the limit, so a multi-gigabyte upload against a 5 MB ceiling never costs
    more than ~`max_bytes + UPLOAD_READ_CHUNK_BYTES` of memory.
    """
    chunks: list = []
    total = 0
    while True:
        chunk = await file.read(UPLOAD_READ_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"File exceeds the {max_bytes // (1024 * 1024)} MB limit.")
        chunks.append(chunk)
    return b"".join(chunks)


# ── Scope of this limit: PROCESS-LOCAL, not global ──────────────────────────
# `_extraction_semaphore` / `_extraction_executor` are plain module-level
# Python objects: the cap they enforce (UPLOAD_MAX_CONCURRENT_EXTRACTIONS)
# holds only within the one OS process that imported this module. That is
# exactly right for how this app is actually started -- `start_backend.ps1`
# and the `uvicorn backend.main:app` command in CLAUDE.md both launch a
# SINGLE process with no `--workers N` flag -- and verified against that
# command, not assumed: confirmed by reading start_backend.ps1 directly.
#
# It stops being sufficient the moment that changes. Running this app under
# `--workers 4` (or four separate containers/instances) gives each worker
# its OWN semaphore and its OWN thread pool, each independently allowing up
# to UPLOAD_MAX_CONCURRENT_EXTRACTIONS jobs -- so the REAL ceiling becomes
# `UPLOAD_MAX_CONCURRENT_EXTRACTIONS * worker_count`, invisibly, with no
# error and no log line pointing at why OCR load is higher than the config
# implies. Multi-worker deployment needs a limiter that lives OUTSIDE any
# one process -- a Redis/Postgres-backed semaphore, or routing OCR work
# through an external job queue with its own worker-count cap -- neither of
# which exists here. Do not raise `UPLOAD_MAX_CONCURRENT_EXTRACTIONS` to
# compensate for a multi-worker deployment without adding one of those; it
# would multiply the problem instead of fixing it.
#
# A dedicated pool (rather than `asyncio.to_thread`'s shared default
# executor) keeps OCR's thread usage isolated and its size tied directly to
# the same cap the semaphore enforces, and is shut down from `main.py`'s
# lifespan (`_extraction_executor.shutdown(wait=False, cancel_futures=True)`
# -- `wait=False` is deliberate: shutdown must not block on a stuck OCR job
# that may never finish; `cancel_futures=True` only cancels jobs that have
# not started running yet, consistent with the "cannot hard-kill a running
# thread" limitation documented on `_extract_bounded` below).
_extraction_semaphore = asyncio.Semaphore(settings.UPLOAD_MAX_CONCURRENT_EXTRACTIONS)
_extraction_executor = _cf.ThreadPoolExecutor(
    max_workers=settings.UPLOAD_MAX_CONCURRENT_EXTRACTIONS, thread_name_prefix="ocr-extract")


async def _extract_bounded(ext: str, raw: bytes):
    """Run `doc_extract.extract` under both a concurrency cap and a wall-clock
    timeout.

    `asyncio.wait_for` around a thread only stops AWAITING it -- the OCR call
    already running inside keeps using CPU after the request moves on. Left
    alone, repeated timed-out uploads (the same slow scan retried, or several
    officers at once) accumulate orphaned OCR threads with no ceiling.

    The semaphore slot is acquired before the job is submitted and released
    by a callback on the RAW `concurrent.futures.Future` the thread pool
    returns -- not on the asyncio wrapper `wait_for` cancels. This distinction
    is the whole point: cancelling the asyncio wrapper (what `wait_for`'s
    timeout does) unwinds immediately and, if the release were attached
    there instead, would free the slot the moment the CALLER stopped
    waiting -- while the real OS thread kept running underneath, defeating
    the cap. The raw future's own done-callback only fires when the thread
    itself actually finishes (success or exception), so an abandoned job
    still occupies its slot for as long as it genuinely runs, and THAT is
    what keeps total concurrent OCR work bounded regardless of how many
    requests time out and retry.

    ponytail: a thread cannot be hard-killed from here, so a pathological
    file can occupy one of the `UPLOAD_MAX_CONCURRENT_EXTRACTIONS` slots for
    as long as pypdf/Tesseract actually takes on it, not just for the
    request's own timeout window. True hard-cancellation needs the OCR work
    moved to a separate process (where `.terminate()` actually stops it) --
    not done here because this deployment has no reported backlog of runaway
    scans to justify the extra moving part; add it if that changes.
    """
    try:
        await asyncio.wait_for(_extraction_semaphore.acquire(),
                               timeout=settings.UPLOAD_QUEUE_WAIT_SECONDS)
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The server is busy processing other uploads right now. "
                   "Try again in a few moments.")

    real_future = _extraction_executor.submit(doc_extract.extract, ext, raw)
    real_future.add_done_callback(lambda _f: _extraction_semaphore.release())
    return await asyncio.wait_for(
        asyncio.wrap_future(real_future), timeout=settings.UPLOAD_EXTRACTION_TIMEOUT_SECONDS)

@router.post("/upload", response_model=StandardResponse)
async def upload_chat_file(
    file: UploadFile = File(...),
    session_id: str = Form(...),
    db: AsyncSession = Depends(get_db),
    current_officer: OfficerContext = Depends(get_current_officer),
):
    """
    Attach a .txt / .csv / .pdf / .docx file to one of the officer's own chat
    sessions.

    The text is extracted with its source locations (PDF page, DOCX paragraph
    or table, CSV row, TXT line), chunked and stored in PostgreSQL, so later
    questions are answered from the few chunks that bear on them — with a
    citation built from that stored location. A scanned / image-only PDF is
    recorded as `no_extractable_text`: there is no OCR here and never a guess
    at what the image said. The legacy binary .doc format is not read.
    """
    name = (file.filename or "file").strip()
    ext = doc_extract.normalise_extension(name)

    # Ownership of the session before a single byte of the body is read — a
    # request for a session the caller does not own is refused for the cost
    # of one DB query, never for the cost of reading whatever body it sent.
    try:
        await attachment_store.verify_session(db, session_id, current_officer.officer_id)
    except attachment_store.AttachmentAccessError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    # Bounded, chunked read. `Content-Length` is never trusted (a client can
    # lie or omit it), so the limit is enforced on bytes actually read: the
    # moment the running total crosses the configured ceiling, reading stops
    # and the request is rejected -- an oversized upload is never buffered
    # whole first. `file.read(n)` keeps returning the SAME amount at most,
    # not the file's total size, so this never holds more than one chunk past
    # the limit in memory regardless of how large the real body is.
    raw = await _read_bounded(file, settings.UPLOAD_MAX_FILE_BYTES)
    if not raw:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="The file is empty.")

    if ext in doc_extract.UNSUPPORTED_HINT:
        return StandardResponse.success_response(
            data={"supported": False, "filename": name},
            message=doc_extract.UNSUPPORTED_HINT[ext])

    if ext not in doc_extract.SUPPORTED_EXTS:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                            detail="Unsupported file type. Upload a .txt, .csv, "
                                   ".pdf or .docx file.")

    try:
        doc_extract.validate_signature(ext, raw, file.content_type or "")
    except doc_extract.UnsupportedFile as e:
        return StandardResponse.success_response(
            data={"supported": False, "filename": name}, message=str(e))
    except doc_extract.ExtractionError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=str(e))

    # Extraction runs in a worker thread, under both a wall-clock budget and a
    # process-wide concurrency cap (see `_extract_bounded`) -- a crafted file
    # must not hold a request open indefinitely, and several such files must
    # not be free to run without limit just because each request times out.
    try:
        extracted = await _extract_bounded(ext, raw)
    except asyncio.TimeoutError:
        logger.warning("attachment extraction timed out", ext=ext, bytes=len(raw))
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="This file took too long to read. Upload a smaller extract.")
    except doc_extract.UnsupportedFile as e:
        return StandardResponse.success_response(
            data={"supported": False, "filename": name}, message=str(e))
    except doc_extract.ExtractionError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=str(e))

    # Housekeeping is explicit and cheap; nothing expires by being forgotten.
    try:
        await attachment_store.cleanup_expired(db)
    except Exception as e:
        logger.warning(f"attachment cleanup skipped: {e}")

    doc = await attachment_store.save_document(
        db=db, officer_id=current_officer.officer_id, session_id=session_id,
        filename=name, ext=ext, mime_type=file.content_type or "",
        raw=raw, extracted=extracted)

    if doc.extraction_status == doc_extract.STATUS_NO_TEXT:
        no_text_msg = (doc.status_detail or
                        "No selectable text found — this assistant does not read images.")
        # Recorded as a chat turn (see the note below the main upload reply)
        # so a follow-up right after a no-text upload still knows the
        # conversation moved to this file, not to whatever application was
        # discussed before it.
        await save_chat_messages(
            db=db, session_id=session_id, user_message=f"[Uploaded: {name}]",
            assistant_message=no_text_msg, language="en", response_time_ms=0,
            officer_id=current_officer.officer_id,
            structured_context={"entity": "attachment", "document_ids": [str(doc.id)]})
        return StandardResponse.success_response(
            data={"supported": True, "document_id": str(doc.id), "filename": name,
                  "extraction_status": doc.extraction_status, "chars": 0,
                  "attached_files": [], "attached_count": 0},
            message=no_text_msg)

    active = await attachment_store.active_documents(
        db, current_officer.officer_id, session_id)
    names = [d.filename for d in active]
    msg = (f'"{name}" attached ({doc.char_count} characters'
           + (", trimmed" if extracted.truncated else "")
           + f"). {len(names)} file(s) attached this session — ask your question "
             f"about them now.")
    if extracted.ocr_pages:
        msg += (f" Page(s) {', '.join(str(p) for p in extracted.ocr_pages)} had no "
                f"selectable text and were read via OCR — verify critical numbers "
                f"against the original scan.")
    if extracted.failed_ocr_pages:
        msg += (f" Page(s) {', '.join(str(p) for p in extracted.failed_ocr_pages)} "
                f"could not be read at all (no selectable text, and OCR found "
                f"nothing) — they are not part of what this assistant can answer from.")
    if doc.csv_row_count is not None:
        msg += (f" {doc.csv_row_count} data row(s) stored, so counts and totals "
                f"are computed from the rows themselves.")
    retired = getattr(doc, "retired_filenames", None) or []
    if retired:
        msg += (f" This session keeps only the newest "
                 f"{settings.UPLOAD_MAX_DOCS_PER_SESSION} file(s) active, so "
                 f"{'the oldest' if len(retired) == 1 else f'the {len(retired)} oldest'} "
                 f"({', '.join(retired)}) {'is' if len(retired) == 1 else 'are'} "
                 f"no longer answerable — re-upload it if you still need it.")
    # An upload never went through process_chat(), so it never wrote a
    # chat_messages row -- every context mechanism that reads "the newest
    # assistant turn" (attachment_qa.attachment_topic_active,
    # _last_focused_document, and chatbot._load_followup_context) was blind to
    # it. A bare follow-up right after an upload ("explain what it is") then
    # fell through to whatever application was last discussed, appended that
    # application's number to the message, and answered about the wrong
    # thing entirely. Recording this turn closes the gap.
    await save_chat_messages(
        db=db, session_id=session_id, user_message=f"[Uploaded: {name}]",
        assistant_message=msg, language="en", response_time_ms=0,
        officer_id=current_officer.officer_id,
        structured_context={"entity": "attachment", "document_ids": [str(doc.id)]})
    return StandardResponse.success_response(
        data={"supported": True, "document_id": str(doc.id), "filename": name,
              "extraction_status": doc.extraction_status,
              "chars": doc.char_count, "pages": doc.page_count,
              "chunks": doc.chunk_count, "csv_rows": doc.csv_row_count,
              "truncated": extracted.truncated,
              "ocr_pages": extracted.ocr_pages,
              "failed_ocr_pages": extracted.failed_ocr_pages,
              # Authoritative, server-calculated -- the browser clock is never
              # the source of truth for when an upload stops being answerable.
              "expires_at": doc.expires_at.isoformat() if doc.expires_at else None,
              "attached_files": names, "attached_count": len(names),
              "max_files": settings.UPLOAD_MAX_DOCS_PER_SESSION,
              "retired_files": retired},
        message=msg)


@router.get("/sessions/{session_id}/attachments", response_model=StandardResponse)
async def list_session_attachments(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    current_officer: OfficerContext = Depends(get_current_officer),
):
    """List the attachments of one of the officer's own sessions.

    Includes recently-retired/expired rows (not just currently-answerable
    ones) so the attachment panel can show an officer a file that just aged
    out of the session's active limit or expired, with a clear re-upload
    action, instead of the file simply vanishing from the list with no
    explanation -- the same "don't make the officer wonder if the assistant
    forgot" rule `_retire_surplus()`'s own officer-facing note already
    follows for the upload response.
    """
    try:
        await attachment_store.verify_session(db, session_id, current_officer.officer_id)
    except attachment_store.AttachmentAccessError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    docs = await attachment_store.recent_documents(
        db, current_officer.officer_id, session_id)
    now = datetime.now(timezone.utc)

    def _status(d):
        if not d.is_active:
            return "retired"
        if d.expires_at and d.expires_at <= now:
            return "expired"
        if d.extraction_status != doc_extract.STATUS_OK:
            return d.extraction_status  # 'no_extractable_text' | 'failed'
        return "active"

    return StandardResponse.success_response(
        data={"attachments": [
            {"document_id": str(d.id), "filename": d.filename,
             "type": d.file_ext, "chars": d.char_count, "pages": d.page_count,
             "csv_rows": d.csv_row_count,
             "extraction_status": d.extraction_status,
             "status_detail": d.status_detail,
             "lifecycle_status": _status(d),
             "created_at": d.created_at.isoformat() if d.created_at else None,
             "expires_at": d.expires_at.isoformat() if d.expires_at else None}
            for d in docs],
            "count": sum(1 for d in docs if _status(d) == "active"),
            "max_files": settings.UPLOAD_MAX_DOCS_PER_SESSION},
        message="Attachments retrieved successfully")


@router.delete("/sessions/{session_id}/attachments/{document_id}",
               response_model=StandardResponse)
async def delete_session_attachment(
    session_id: str,
    document_id: str,
    db: AsyncSession = Depends(get_db),
    current_officer: OfficerContext = Depends(get_current_officer),
):
    """Remove one attachment the officer owns, with its chunks, rows and file."""
    try:
        await attachment_store.verify_session(db, session_id, current_officer.officer_id)
    except attachment_store.AttachmentAccessError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    removed = await attachment_store.delete_document(
        db, current_officer.officer_id, session_id, document_id)
    if not removed:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Attachment not found.")
    return StandardResponse.success_response(
        data={"document_id": document_id}, message="Attachment removed.")


@router.get("/sessions", response_model=StandardResponse)
async def list_chat_sessions(
    db: AsyncSession = Depends(get_db),
    current_officer: OfficerContext = Depends(get_current_officer)
):
    """
    Get all chat sessions for the current officer
    """
    try:
        sessions = await get_officer_sessions(db, str(current_officer.officer_id))
        
        return StandardResponse.success_response(
            data={"sessions": sessions, "count": len(sessions)},
            message="Chat sessions retrieved successfully"
        )
        
    except Exception as e:
        logger.error(f"Error listing chat sessions: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error retrieving chat sessions. Please try again."
        )


@router.get("/sessions/{session_id}/history", response_model=StandardResponse)
async def get_chat_session_history(
    session_id: str,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    current_officer: OfficerContext = Depends(get_current_officer)
):
    """
    Get chat history for a specific session
    
    Returns the last 50 messages (or specified limit) in chronological order
    """
    try:
        # Validate session_id
        try:
            UUID(session_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid session_id format"
            )
        
        # A session id is a lookup key, never a grant: another officer's
        # session is indistinguishable from one that does not exist.
        owner = (await db.execute(
            select(ChatSession.officer_id).where(ChatSession.id == session_id))).scalar()
        if owner is None or str(owner) != str(current_officer.officer_id):
            history = []
        else:
            history = await get_session_history(db, session_id, limit)
        
        return StandardResponse.success_response(
            data={"messages": history, "count": len(history), "session_id": session_id},
            message="Chat history retrieved successfully"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting chat history: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error retrieving chat history. Please try again."
        )


@router.post("/sessions", response_model=StandardResponse, status_code=status.HTTP_201_CREATED)
async def create_new_chat_session(
    db: AsyncSession = Depends(get_db),
    current_officer: OfficerContext = Depends(get_current_officer)
):
    """
    Create a new chat session for the current officer
    
    Returns a new session_id that should be used for subsequent chat messages
    """
    try:
        session = await create_chat_session(db, str(current_officer.officer_id))
        
        response_data = {
            "session_id": str(session.id),
            "session_token": session.session_token,
            "started_at": session.started_at.isoformat() + "Z"
        }
        
        return StandardResponse.success_response(
            data=response_data,
            message="Chat session created successfully"
        )
        
    except Exception as e:
        logger.error(f"Error creating chat session: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error creating chat session. Please try again."
        )
