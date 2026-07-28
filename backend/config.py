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

# Official Tamil Nadu 38 District Codes & Names Mapping
DISTRICT_CODE_MAP = {
    "01": "Tiruvallur",
    "02": "Chennai",
    "03": "Kancheepuram",
    "04": "Vellore",
    "05": "Dharmapuri",
    "06": "Tiruvannamalai",
    "07": "Viluppuram",
    "08": "Salem",
    "09": "Namakkal",
    "10": "Erode",
    "11": "Nilgiris",
    "12": "Coimbatore",
    "13": "Dindigul",
    "14": "Karur",
    "15": "Tiruchirappalli",
    "16": "Perambalur",
    "17": "Ariyalur",
    "18": "Cuddalore",
    "19": "Nagapattinam",
    "20": "Tiruvarur",
    "21": "Thanjavur",
    "22": "Pudukkottai",
    "23": "Sivagangai",
    "24": "Madurai",
    "25": "Theni",
    "26": "Virudhunagar",
    "27": "Ramanathapuram",
    "28": "Thoothukudi",
    "29": "Tirunelveli",
    "30": "Kanniyakumari",
    "31": "Krishnagiri",
    "32": "Tiruppur",
    "33": "Kallakurichi",
    "34": "Chengalpattu",
    "35": "Ranipet",
    "36": "Tirupathur",
    "37": "Tenkasi",
    "38": "Mayiladuthurai"
}

DISTRICT_NAME_MAP = {name.lower(): code for code, name in DISTRICT_CODE_MAP.items()}
