"""
Async SQLAlchemy database setup
"""
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import URL
from sqlalchemy.engine import make_url
from typing import AsyncGenerator
import sys

from backend.config import settings


# Parse connection parameters from URL for Windows compatibility
def get_engine_url():
    """Get database URL with Windows-specific fixes.

    On Windows the URL is rebuilt component-by-component to avoid an asyncpg
    DNS issue, but the components come from DATABASE_URL -- they used to be
    hardcoded, which silently ignored .env and pinned the app to one database.
    """
    url = make_url(settings.DATABASE_URL)
    if sys.platform == "win32":
        return URL.create(
            drivername="postgresql+asyncpg",
            username=url.username,
            password=url.password,
            host="127.0.0.1" if url.host in (None, "localhost") else url.host,
            port=url.port or 5432,
            database=url.database,
            query={"ssl": "disable"},
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
