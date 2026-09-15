"""
A hard read-only boundary around the chat path.

Why this exists
---------------
The chatbot answers questions about the department's register. It has no
business changing it, and an audit that says so is only true of the code as it
stood on the day it was run. This module makes it true at runtime instead:
while a chat turn is in flight, any attempt to write a table that is not part
of the conversation itself is refused by the database session before it
reaches PostgreSQL.

What it protects against is not one bug but a class of them -- a future intent
handler that "helpfully" updates a status, a tool added to `agent_tools.py`
without the read-only discipline, an LLM-driven code path that reaches a writer
by accident. None of those exist today; the audit behind this module found the
chatbot's write surface to be exactly the five conversation tables below. The
guard is what keeps that true tomorrow.

What a chat turn IS allowed to write
------------------------------------
Only the record of the conversation: the session, the messages, and the files
the officer uploaded to it. Everything else -- applications, field visits,
owners, survey numbers, workflow history, the geography masters, the CSV-shaped
source layer and `knowledge_embeddings` -- is read-only for the entire turn.

Scope
-----
The guard is armed by `chat_turn()` and is otherwise inert, so the paths that
legitimately write (login stamping `last_login`, the ingestion CLI rebuilding
the vector store, `build_app_tables.py` rebuilding the projection) are
untouched. It is deliberately NOT a global read-only engine: making the whole
application read-only would be a different, larger decision, and one that would
break `PUT /applications` the day someone implements it.
"""

from __future__ import annotations

import contextlib
import contextvars
import re
from typing import Iterator, Set

from sqlalchemy import event
from sqlalchemy.orm import Session

from backend.utils.logger import get_logger

logger = get_logger(__name__)


class ReadOnlyViolation(RuntimeError):
    """A chat turn tried to modify data it may only read."""


# The conversation's own record. These are the only tables a chat turn may
# write, and each is written by exactly one place:
#   chat_sessions      create / touch last_activity   (chatbot.py)
#   chat_messages      the transcript                 (chatbot.py)
#   chat_attachments   an uploaded file's reference   (attachment_store.py)
#   attachment_chunks  its retrievable evidence       (attachment_store.py)
#   attachment_rows    its CSV rows                   (attachment_store.py)
#   audit_logs         append-only trail              (routers/chat.py)
CONVERSATION_TABLES: Set[str] = {
    "chat_sessions",
    "chat_messages",
    "chat_attachments",
    "attachment_chunks",
    "attachment_rows",
    "audit_logs",
}

# Verbs that change data or schema. Matched at the start of a raw statement so
# a SELECT that merely contains the word "update" in a string literal is not
# caught.
_DML_DDL = re.compile(
    r"^\s*(insert|update|delete|truncate|drop|alter|create|grant|revoke|copy|"
    r"merge|refresh|reindex|vacuum|comment|set\s+role)\b",
    re.IGNORECASE,
)

_in_chat_turn: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "sis_in_chat_turn", default=False
)


def in_chat_turn() -> bool:
    """True while the guard is armed on this task."""
    return _in_chat_turn.get()


@contextlib.contextmanager
def chat_turn() -> Iterator[None]:
    """Arm the read-only guard for the duration of one chat turn.

    A contextvar, not a flag: each request is its own asyncio task with its own
    context, so arming one turn cannot arm or disarm another running beside it.
    """
    token = _in_chat_turn.set(True)
    try:
        yield
    finally:
        _in_chat_turn.reset(token)


def _table_of(obj: object) -> str:
    table = getattr(obj, "__tablename__", None)
    if table:
        return str(table)
    mapper_table = getattr(getattr(obj, "__table__", None), "name", None)
    return str(mapper_table or type(obj).__name__)


def _refuse(action: str, table: str) -> None:
    logger.error(
        f"readonly_guard: BLOCKED {action} on '{table}' during a chat turn"
    )
    raise ReadOnlyViolation(
        f"The assistant may not {action} '{table}'. A chat turn can only read "
        f"the department's records; the conversation's own tables "
        f"({', '.join(sorted(CONVERSATION_TABLES))}) are the sole exception."
    )


@event.listens_for(Session, "before_flush")
def _block_orm_writes(session, flush_context, instances) -> None:
    """Refuse an ORM insert / update / delete of anything but the transcript."""
    if not in_chat_turn():
        return
    for obj in session.new:
        table = _table_of(obj)
        if table not in CONVERSATION_TABLES:
            _refuse("create rows in", table)
    for obj in session.dirty:
        if not session.is_modified(obj, include_collections=False):
            continue
        table = _table_of(obj)
        if table not in CONVERSATION_TABLES:
            _refuse("modify", table)
    for obj in session.deleted:
        table = _table_of(obj)
        if table not in CONVERSATION_TABLES:
            _refuse("delete from", table)


@event.listens_for(Session, "do_orm_execute")
def _block_core_writes(orm_execute_state) -> None:
    """Refuse Core-level DML -- `delete(Model)`, `update(Model)`, `insert(...)`
    and raw `text("DELETE ...")` -- which never passes through a flush and so
    would slip past `before_flush` entirely."""
    if not in_chat_turn():
        return
    state = orm_execute_state
    if state.is_select:
        return

    if state.is_delete or state.is_update or state.is_insert:
        action = ("delete from" if state.is_delete
                  else "modify" if state.is_update else "create rows in")
        for table in _statement_tables(state.statement):
            if table not in CONVERSATION_TABLES:
                _refuse(action, table)
        return

    # Anything else reaching here is a textual statement. Read it directly.
    raw = str(getattr(state.statement, "text", "") or state.statement)
    if _DML_DDL.match(raw):
        _refuse("execute the statement", raw.strip().split()[0].upper())


def _statement_tables(statement) -> Set[str]:
    """Every table a Core DML statement touches, by name."""
    names: Set[str] = set()
    entity = getattr(statement, "entity_description", None)
    if entity and entity.get("name"):
        names.add(str(entity["name"]))
    table = getattr(statement, "table", None)
    if table is not None and getattr(table, "name", None):
        names.add(str(table.name))
    for frozen in getattr(statement, "_all_selected_columns", []) or []:
        parent = getattr(frozen, "table", None)
        if parent is not None and getattr(parent, "name", None):
            names.add(str(parent.name))
    # A statement we cannot read the target of is refused, not waved through:
    # an unrecognised shape is exactly when a guard must be conservative.
    return names or {"<unidentified table>"}
