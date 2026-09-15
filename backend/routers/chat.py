"""
Chat Router
Endpoints for chatbot interactions
"""
from fastapi import (
    APIRouter, Depends, HTTPException, BackgroundTasks, status,
    UploadFile, File, Form,
)
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field
from typing import Optional, List, Any, Dict
from datetime import datetime
from uuid import UUID
import asyncio

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
    get_officer_sessions
)
from backend.models import AuditLog
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
            detail=f"Error processing chat message: {str(e)}"
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
            
        return StreamingResponse(
            process_chat_stream(
                message=request.message,
                session_id=request.session_id,
                officer=current_officer,
                db=db,
                chat_history=request.chat_history
            ),
            media_type="text/event-stream"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in stream_chat_message: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error processing stream request: {str(e)}"
        )


# ── Attachments ──────────────────────────────────────────────────────────────
# The officer is taken from the JWT; the session and every document are checked
# against that officer server-side. Nothing in the request body decides who the
# caller is, and a document_id that is not theirs is indistinguishable from one
# that does not exist.

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
    raw = await file.read()

    # Size is measured on the bytes actually read, not on a client header.
    if len(raw) > settings.UPLOAD_MAX_FILE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds the "
                   f"{settings.UPLOAD_MAX_FILE_BYTES // (1024 * 1024)} MB limit.")
    if not raw:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="The file is empty.")

    # Ownership of the session before anything is parsed or written.
    try:
        await attachment_store.verify_session(db, session_id, current_officer.officer_id)
    except attachment_store.AttachmentAccessError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

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

    # Extraction runs in a worker thread under a wall-clock budget: a crafted
    # file must not hold a request open indefinitely.
    try:
        extracted = await asyncio.wait_for(
            asyncio.to_thread(doc_extract.extract, ext, raw),
            timeout=settings.UPLOAD_EXTRACTION_TIMEOUT_SECONDS)
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
        return StandardResponse.success_response(
            data={"supported": True, "document_id": str(doc.id), "filename": name,
                  "extraction_status": doc.extraction_status, "chars": 0,
                  "attached_files": [], "attached_count": 0},
            message=doc.status_detail or
                    "No selectable text found — this assistant does not read images.")

    active = await attachment_store.active_documents(
        db, current_officer.officer_id, session_id)
    names = [d.filename for d in active]
    msg = (f'"{name}" attached ({doc.char_count} characters'
           + (", trimmed" if extracted.truncated else "")
           + f"). {len(names)} file(s) attached this session — ask your question "
             f"about them now.")
    if doc.csv_row_count is not None:
        msg += (f" {doc.csv_row_count} data row(s) stored, so counts and totals "
                f"are computed from the rows themselves.")
    return StandardResponse.success_response(
        data={"supported": True, "document_id": str(doc.id), "filename": name,
              "extraction_status": doc.extraction_status,
              "chars": doc.char_count, "pages": doc.page_count,
              "chunks": doc.chunk_count, "csv_rows": doc.csv_row_count,
              "truncated": extracted.truncated,
              "attached_files": names, "attached_count": len(names),
              "max_files": settings.UPLOAD_MAX_DOCS_PER_SESSION},
        message=msg)


@router.get("/sessions/{session_id}/attachments", response_model=StandardResponse)
async def list_session_attachments(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    current_officer: OfficerContext = Depends(get_current_officer),
):
    """List the attachments of one of the officer's own sessions."""
    try:
        await attachment_store.verify_session(db, session_id, current_officer.officer_id)
    except attachment_store.AttachmentAccessError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    docs = await attachment_store.active_documents(
        db, current_officer.officer_id, session_id)
    return StandardResponse.success_response(
        data={"attachments": [
            {"document_id": str(d.id), "filename": d.filename,
             "type": d.file_ext, "chars": d.char_count, "pages": d.page_count,
             "csv_rows": d.csv_row_count,
             "extraction_status": d.extraction_status,
             "created_at": d.created_at.isoformat() if d.created_at else None,
             "expires_at": d.expires_at.isoformat() if d.expires_at else None}
            for d in docs], "count": len(docs)},
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
            detail=f"Error retrieving chat sessions: {str(e)}"
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
        
        # Get history
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
            detail=f"Error retrieving chat history: {str(e)}"
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
            detail=f"Error creating chat session: {str(e)}"
        )
