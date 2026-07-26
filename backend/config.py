"""
Configuration management using pydantic-settings
"""
import json
from pathlib import Path
from typing import List
from pydantic_settings import BaseSettings
from pydantic import validator

# Absolute path to the project root (the directory containing this file's parent)
_PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _resolve_env_file() -> str:
    """Find the environment file from the project root or its parent directory."""
    candidates = [
        Path.cwd() / ".env",
        Path(__file__).resolve().parents[1] / ".env",
        Path(__file__).resolve().parents[2] / ".env",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return ".env"


class Settings(BaseSettings):
    # Database
    DATABASE_URL: str
    SYNC_DATABASE_URL: str
    
    # Security
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480
    
    # Ollama / LLM
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    LLM_MODEL: str = "llama3.1:8b"
    EMBEDDING_MODEL: str = "nomic-embed-text"
    
    
    # Environment
    ENVIRONMENT: str = "development"
    
    # CORS
    CORS_ORIGINS: str | List[str] = '["http://localhost:3000","http://127.0.0.1:5500","http://localhost:5500","http://localhost:8080"]'
    
    @validator("CORS_ORIGINS", pre=True)
    def parse_cors_origins(cls, v):
        if isinstance(v, str):
            return json.loads(v)
        return v
    
    class Config:
        env_file = _resolve_env_file()
        env_file_encoding = "utf-8"
        case_sensitive = True


# Global settings instance
settings = Settings()
