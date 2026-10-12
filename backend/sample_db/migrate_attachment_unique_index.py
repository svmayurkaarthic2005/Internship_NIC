"""One-off DDL: add the partial unique index that stops two concurrent,
identical uploads from creating two active `chat_attachments` rows.

`save_document()`'s own "reuse an existing upload of the same bytes" check
(backend/services/attachment_store.py) was read-then-write: two requests for
the same bytes, officer and session could both run the SELECT before either
committed its INSERT, both miss each other, and both insert -- an
application-level check can never close that window on its own. This adds
the DB-level guarantee: at most one ACTIVE row per (officer_id, session_id,
content_hash). It is PARTIAL (`WHERE is_active`) on purpose -- an expired or
retired row with the same hash is history, not a conflict, and
`_retire_surplus()` flipping an old row's `is_active` to False must never be
blocked by this index either.

Idempotent: safe to run again (`IF NOT EXISTS`). Checked before running that
no existing active duplicate would violate it (there were none in this
database at the time this was written) -- if a later run of this script
reports a violation, that is a real duplicate-upload bug to investigate
before adding the index, not something to work around by relaxing it.

This targets the LOCAL DEVELOPMENT database configured in `.env`
(127.0.0.1:5432, ENVIRONMENT=development) -- never run this against a
shared or production database without separately verifying the same
pre-condition there.

Run: python -m backend.sample_db.migrate_attachment_unique_index
"""
import asyncio

from sqlalchemy import text

from backend.database import AsyncSessionLocal, engine
from backend.config import settings


async def main():
    if settings.ENVIRONMENT != "development":
        raise SystemExit(
            f"ENVIRONMENT={settings.ENVIRONMENT!r}, not 'development' -- "
            f"refusing to run a schema migration automatically. Review and "
            f"run the DDL manually if this is really intended.")

    async with AsyncSessionLocal() as db:
        dupes = (await db.execute(text("""
            SELECT officer_id, session_id, content_hash, count(*)
            FROM chat_attachments WHERE is_active = true
            GROUP BY officer_id, session_id, content_hash HAVING count(*) > 1
        """))).all()
        if dupes:
            raise SystemExit(
                f"{len(dupes)} existing duplicate active attachment row group(s) found -- "
                f"resolve these first (they would violate the new index): {dupes}")

    async with engine.begin() as conn:
        await conn.execute(text("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_attachment_active_dedupe
            ON chat_attachments (officer_id, session_id, content_hash)
            WHERE is_active = true
        """))
    print("uq_attachment_active_dedupe created (or already existed).")


if __name__ == "__main__":
    asyncio.run(main())
