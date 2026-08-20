"""
Speech-to-Text API Router
Handles audio upload and transcription
"""
from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
from fastapi.responses import JSONResponse
from typing import Optional
import logging

from ..services.speech_service import get_speech_service, SpeechService
from ..dependencies import get_current_officer

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/speech", tags=["speech"])


# Supported audio formats (base MIME types without codec information)
# Browser may send formats like "audio/webm;codecs=opus" which are normalized
# to "audio/webm" before validation
SUPPORTED_FORMATS = {
    "audio/webm",
    "audio/wav",
    "audio/wave",
    "audio/x-wav",
    "audio/ogg",
    "audio/mpeg",
    "audio/mp3"
}

# Maximum file size (10MB)
MAX_FILE_SIZE = 10 * 1024 * 1024


@router.post("/transcribe")
async def transcribe_audio(
    audio: UploadFile = File(...),
    language: Optional[str] = None,
    speech_service: SpeechService = Depends(get_speech_service),
    current_officer: dict = Depends(get_current_officer)
):
    """
    Transcribe audio to text using Faster-Whisper
    
    Args:
        audio: Audio file (webm, wav, ogg)
        language: Optional language code ('en', 'ta', etc.)
        
    Returns:
        JSON: {"text": "transcribed text", "language": "detected_language"}
    """
    try:
        logger.info(f"Received transcription request - File: {audio.filename}, Content-Type: {audio.content_type}")
        
        # Normalize content type by removing codec information
        # Browser sends "audio/webm;codecs=opus" but we need "audio/webm"
        content_type = audio.content_type.split(";")[0].strip() if audio.content_type else ""
        
        logger.info(f"Normalized content type: {content_type}")
        
        # Validate file type
        if content_type not in SUPPORTED_FORMATS:
            logger.warning(f"Unsupported format: {content_type} (original: {audio.content_type})")
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported audio format: {audio.content_type}. "
                       f"Supported formats: {', '.join(SUPPORTED_FORMATS)}"
            )
        
        # Read file content
        file_content = await audio.read()
        
        # Validate file size
        if len(file_content) > MAX_FILE_SIZE:
            raise HTTPException(
                status_code=400,
                detail=f"File too large. Maximum size: {MAX_FILE_SIZE / 1024 / 1024}MB"
            )
        
        if len(file_content) == 0:
            logger.warning("Empty audio file received")
            raise HTTPException(
                status_code=400,
                detail="Empty audio file"
            )
        
        logger.info(f"Processing audio file: {audio.filename} ({len(file_content)} bytes), Officer: {current_officer.officer_id}")
        
        # Process audio
        result = await speech_service.process_audio_file(file_content, audio.filename)
        
        if not result.get("text"):
            return JSONResponse(
                status_code=200,
                content={
                    "text": "",
                    "language": result.get("language", "unknown"),
                    "message": "No speech detected in audio"
                }
            )
        
        return JSONResponse(
            status_code=200,
            content={
                "text": result["text"],
                "language": result.get("language", "unknown"),
                "language_probability": result.get("language_probability", 0.0)
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        # Log the full error with traceback
        logger.error(f"Speech transcription error: {e}", exc_info=True)
        
        # Provide more detailed error message to user
        error_msg = str(e)
        if "ffmpeg" in error_msg.lower():
            detail = "FFmpeg not found. Please install FFmpeg to process audio files."
        elif "model" in error_msg.lower():
            detail = f"Speech model error: {error_msg}"
        elif "No speech" in error_msg:
            detail = "No speech detected in the audio. Please try speaking more clearly."
        else:
            detail = f"Speech recognition failed: {error_msg}"
        
        raise HTTPException(
            status_code=500,
            detail=detail
        )


@router.get("/health")
async def speech_health():
    """
    Check if speech service is ready
    """
    try:
        speech_service = get_speech_service()
        if speech_service.model is None:
            return JSONResponse(
                status_code=503,
                content={"status": "unavailable", "message": "Speech model not loaded"}
            )
        
        return JSONResponse(
            status_code=200,
            content={"status": "ready", "model": speech_service.model_size}
        )
    except Exception as e:
        logger.error(f"Speech health check failed: {e}")
        return JSONResponse(
            status_code=503,
            content={"status": "error", "message": str(e)}
        )
