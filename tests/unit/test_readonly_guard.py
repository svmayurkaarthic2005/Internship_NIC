"""
Unit tests for the Read-Only Guard (readonly_guard.py).
Ensures chat turns cannot mutate domain/register data.
"""
import uuid
import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from backend.models import Application, ChatSession, ChatMessage
from backend.services.readonly_guard import chat_turn, ReadOnlyViolation, in_chat_turn
from backend.schemas import OfficerContext


@pytest.mark.asyncio
async def test_readonly_guard_context_var():
    assert not in_chat_turn()
    with chat_turn():
        assert in_chat_turn()
    assert not in_chat_turn()


@pytest.mark.asyncio
async def test_readonly_guard_blocks_domain_mutation(db_session: AsyncSession):
    """Attempting to insert/update domain tables during a chat turn must raise ReadOnlyViolation."""
    with chat_turn():
        fake_app = Application(
            id=uuid.uuid4(),
            application_number="2026/0154/99/999999",
            application_type="ISD",
            current_status="pending",
            current_stage="SIS"
        )
        db_session.add(fake_app)
        with pytest.raises(ReadOnlyViolation):
            await db_session.flush()

        await db_session.rollback()


@pytest.mark.asyncio
async def test_readonly_guard_allows_conversation_tables(
    db_session: AsyncSession,
    test_officer: OfficerContext
):
    """Conversation tables (chat_sessions, chat_messages) must be writable during a chat turn."""
    session_id = uuid.uuid4()
    with chat_turn():
        chat_sess = ChatSession(
            id=session_id,
            officer_id=test_officer.officer_id,
            session_token=f"token-{session_id}"
        )
        db_session.add(chat_sess)
        
        chat_msg = ChatMessage(
            id=uuid.uuid4(),
            session_id=session_id,
            role="user",
            content="Hello world"
        )
        db_session.add(chat_msg)
        
        # Flush should succeed without ReadOnlyViolation
        await db_session.flush()
        await db_session.rollback()
