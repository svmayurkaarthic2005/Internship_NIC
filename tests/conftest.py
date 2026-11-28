"""
Pytest configuration and shared fixtures for SIS Chatbot evaluation.
"""
import os
import sys
from typing import AsyncGenerator
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import NullPool

# Ensure backend package is in python path
sys.path.insert(0, os.path.abspath("."))

from backend.database import get_engine_url
from backend.models import SISOfficer
from backend.schemas import OfficerContext
from backend.services.auth_service import get_officer_jurisdiction_ids

# Test engine using NullPool to prevent connection pooling across closed asyncio loops
test_engine = create_async_engine(get_engine_url(), poolclass=NullPool)
TestSessionLocal = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture(scope="function")
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Provide a real async database session from the live configured DB."""
    async with TestSessionLocal() as session:
        yield session


@pytest_asyncio.fixture(scope="function")
async def test_officer(db_session: AsyncSession) -> OfficerContext:
    """
    Retrieve primary test officer (Arun M - Ward SIS Ambattur).
    Uses synthetic test credentials seeded in SISchatbot.
    """
    result = await db_session.execute(
        select(SISOfficer).where(SISOfficer.email == "arun_m@sis.tn.gov.in")
    )
    officer = result.scalar_one_or_none()
    assert officer is not None, "Test officer 'arun_m@sis.tn.gov.in' not found in database."

    jurisdiction_data = await get_officer_jurisdiction_ids(officer.id, db_session)
    all_jurisdiction_ids = (
        jurisdiction_data["district_ids"] +
        jurisdiction_data["taluk_ids"] +
        jurisdiction_data["town_ids"] +
        jurisdiction_data["ward_ids"] +
        jurisdiction_data["block_ids"]
    )

    return OfficerContext(
        officer_id=officer.id,
        employee_id=officer.employee_id,
        name=officer.name,
        email=officer.email,
        designation=officer.designation,
        role=officer.designation,
        jurisdiction_type=jurisdiction_data["jurisdiction_type"],
        jurisdiction_name=jurisdiction_data["jurisdiction_name"],
        jurisdiction_ids=all_jurisdiction_ids,
        officer_stage="SIS"
    )


@pytest_asyncio.fixture(scope="function")
async def secondary_officer(db_session: AsyncSession) -> OfficerContext:
    """
    Retrieve secondary test officer (M Arun - Korattur / Padi) for out-of-jurisdiction tests.
    """
    result = await db_session.execute(
        select(SISOfficer).where(SISOfficer.email == "marun_chn@sis.tn.gov.in")
    )
    officer = result.scalar_one_or_none()
    assert officer is not None, "Test officer 'marun_chn@sis.tn.gov.in' not found in database."

    jurisdiction_data = await get_officer_jurisdiction_ids(officer.id, db_session)
    all_jurisdiction_ids = (
        jurisdiction_data["district_ids"] +
        jurisdiction_data["taluk_ids"] +
        jurisdiction_data["town_ids"] +
        jurisdiction_data["ward_ids"] +
        jurisdiction_data["block_ids"]
    )

    return OfficerContext(
        officer_id=officer.id,
        employee_id=officer.employee_id,
        name=officer.name,
        email=officer.email,
        designation=officer.designation,
        role=officer.designation,
        jurisdiction_type=jurisdiction_data["jurisdiction_type"],
        jurisdiction_name=jurisdiction_data["jurisdiction_name"],
        jurisdiction_ids=all_jurisdiction_ids,
        officer_stage="SIS"
    )
