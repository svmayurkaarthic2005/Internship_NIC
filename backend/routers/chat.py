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

from backend.database import get_db
from backend.schemas import StandardResponse, OfficerContext
from backend.dependencies import get_current_officer
from backend.services import upload_store
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
            "table_data": result.get("table_data")
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


_UPLOAD_MAX_BYTES = 5 * 1024 * 1024


@router.post("/upload", response_model=StandardResponse)
async def upload_chat_file(
    file: UploadFile = File(...),
    session_id: str = Form(...),
    current_officer: OfficerContext = Depends(get_current_officer),
):
    """
    Attach a .txt / .csv / .pdf / .docx file to a chat session.

    The text is extracted and kept in a session-scoped store so subsequent chat
    messages in that session can be answered from it. The legacy binary .doc
    format is reported as not supported (save as .docx / PDF or paste the text);
    everything else is rejected.
    """
    name = (file.filename or "file").strip()
    ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
    raw = await file.read()

    if len(raw) > _UPLOAD_MAX_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            detail="File exceeds the 5 MB limit.")

    if ext in doc_extract.UNSUPPORTED_HINT:
        return StandardResponse.success_response(
            data={"supported": False, "filename": name},
            message=doc_extract.UNSUPPORTED_HINT[ext])

    if ext not in doc_extract.SUPPORTED_EXTS:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                            detail="Unsupported file type. Upload a .txt, .csv, .pdf or .docx file.")

    try:
        content = doc_extract.extract_text(ext, raw)
    except doc_extract.ExtractionError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))

    if not content.strip():
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="The file appears to be empty.")

    doc = upload_store.add(session_id, name, content)
    attached = upload_store.filenames(session_id)
    logger.info(f"Chat upload: '{name}' ({doc['chars']} chars) -> session {session_id} "
                f"[{len(attached)}/{upload_store.MAX_DOCS_PER_SESSION} files]")
    msg = (f'"{name}" attached ({doc["chars"]} characters'
           + (", trimmed" if doc["truncated"] else "")
           + f"). {len(attached)} file(s) attached this session — ask your question about them now.")
    if len(attached) >= upload_store.MAX_DOCS_PER_SESSION:
        msg += (f" (Limit is {upload_store.MAX_DOCS_PER_SESSION}; the oldest is dropped "
                f"when you add more.)")
    return StandardResponse.success_response(
        data={"supported": True, "filename": name, "chars": doc["chars"],
              "truncated": doc["truncated"], "attached_files": attached,
              "attached_count": len(attached),
              "max_files": upload_store.MAX_DOCS_PER_SESSION},
        message=msg)


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
