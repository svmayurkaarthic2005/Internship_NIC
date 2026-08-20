"""
Speech-to-Text Service using Faster-Whisper
Provides offline, cross-browser compatible speech recognition
"""
import os
import tempfile
from typing import Optional
from faster_whisper import WhisperModel
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


class SpeechService:
    """
    Speech-to-Text service using Faster-Whisper model
    """
    
    def __init__(self):
        self.model: Optional[WhisperModel] = None
        self.model_size = "small"  # Options: tiny, base, small, medium, large
        self.device = "cpu"  # Use CPU by default
        self.compute_type = "int8"  # Optimize for CPU
        
    def initialize(self):
        """
        Initialize the Faster-Whisper model
        Should be called once during application startup
        """
        try:
            logger.info(f"Loading Faster-Whisper model: {self.model_size}")
            self.model = WhisperModel(
                self.model_size,
                device=self.device,
                compute_type=self.compute_type,
                download_root=None,  # Use default cache directory
                num_workers=1
            )
            logger.info("✓ Faster-Whisper model loaded successfully")
        except Exception as e:
            logger.error(f"Failed to load Faster-Whisper model: {e}")
            raise
    
    def transcribe_audio(self, audio_file_path: str, language: str = None) -> dict:
        """
        Transcribe audio file to text
        
        Args:
            audio_file_path: Path to audio file (webm, wav, ogg)
            language: Optional language code (e.g., 'en', 'ta')
        
        Returns:
            dict: {"text": "transcribed text", "language": "detected_language"}
        """
        if not self.model:
            raise RuntimeError("Speech model not initialized. Call initialize() first.")
        
        try:
            logger.info(f"Transcribing audio file: {audio_file_path}")
            
            # Transcribe with faster-whisper
            segments, info = self.model.transcribe(
                audio_file_path,
                language=language,  # None = auto-detect
                beam_size=5,
                vad_filter=True,  # Voice Activity Detection
                vad_parameters=dict(
                    min_silence_duration_ms=500,
                    threshold=0.5
                )
            )
            
            # Combine all segments
            transcription = " ".join([segment.text.strip() for segment in segments])
            
            logger.info(f"✓ Transcription complete: {transcription[:100]}...")
            
            return {
                "text": transcription.strip(),
                "language": info.language,
                "language_probability": info.language_probability
            }
            
        except Exception as e:
            logger.error(f"Transcription error: {e}")
            raise
    
    async def process_audio_file(self, file_content: bytes, filename: str) -> dict:
        """
        Process uploaded audio file
        
        Args:
            file_content: Audio file bytes
            filename: Original filename
        
        Returns:
            dict: Transcription result
        """
        # Create temporary file
        temp_dir = tempfile.gettempdir()
        temp_path = os.path.join(temp_dir, f"audio_{os.getpid()}_{filename}")
        
        try:
            # Write audio to temporary file
            with open(temp_path, 'wb') as f:
                f.write(file_content)
            
            # Transcribe
            result = self.transcribe_audio(temp_path)
            
            return result
            
        finally:
            # Clean up temporary file
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception as e:
                    logger.warning(f"Failed to delete temp file {temp_path}: {e}")


# Global instance
speech_service = SpeechService()


def get_speech_service() -> SpeechService:
    """
    Get the global speech service instance
    """
    return speech_service
