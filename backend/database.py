"""
Async SQLAlchemy database setup
"""
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import URL
from typing import AsyncGenerator
import sys

from backend.config import settings


# Parse connection parameters from URL for Windows compatibility
def get_engine_url():
    """Get database URL with Windows-specific fixes"""
    if sys.platform == "win32":
        # On Windows with asyncpg, explicitly build URL to avoid DNS issues
        return URL.create(
            drivername="postgresql+asyncpg",
            username="postgres",
            password="Mayur@2005",
            host="127.0.0.1",
            port=5432,
            database="sis_chatbot",
            query={"ssl": "disable"}
        )
    return settings.DATABASE_URL


# Create async engine
engine = create_async_engine(
    get_engine_url(),
    echo=settings.ENVIRONMENT == "development",
    future=True,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20
)

# Async session maker
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False
)

# Base class for models
Base = declarative_base()


# Dependency for FastAPI
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Async database session dependency
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
