"""Regression: an upload that fails partway through must never leave an
orphaned raw file on disk, and any orphan that slips through anyway (e.g. a
process killed mid-write) is swept up later.

Root cause (backend/services/attachment_store.py, `save_document`): the raw
file was written to disk BEFORE the DB row/chunks were created and
committed, with no handling for the DB side failing afterwards -- a failed
`flush()`/`commit()` left the file sitting on disk with no `ChatAttachment`
row ever pointing at it, and nothing would ever come back to remove it.

Fix:
1. `save_document` now wraps the DB work in try/except; on any failure it
   rolls back the session and deletes the just-written raw file before
   re-raising, so a single failed upload never accumulates an orphan.
2. `sweep_orphan_raw_files()` is the backstop for whatever (1) cannot catch
   (a hard process kill between the write and the commit) -- it removes any
   file in the storage directory with no referencing `ChatAttachment` row,
   skipping anything written in the last `_ORPHAN_RAW_FILE_GRACE_SECONDS` so
   a still-in-flight upload's file is never mistaken for an orphan.

Needs the live DB (real ChatSession/ChatAttachment rows; no LLM, no Ollama).
Run: python test_attachment_storage_safety.py
"""
import asyncio
import os
import sys
import time
import uuid

bad = []
total = 0


def check(name, condition, detail=""):
    global total
    total += 1
    if condition:
        print(f"ok   {name}")
    else:
        bad.append((name, detail))
        print(f"FAIL {name}" + (f" -- {detail}" if detail else ""))


async def _run():
    from sqlalchemy import select
    from backend.database import AsyncSessionLocal
    from backend.models import SISOfficer, ChatAttachment
    from backend.services import attachment_store, doc_extract
    from backend.services.chatbot import create_chat_session
    from backend.services.doc_extract import ExtractedDocument, Segment

    async with AsyncSessionLocal() as db:
        officer = (await db.execute(
            select(SISOfficer).where(SISOfficer.email == "marun_chn@sis.tn.gov.in")
        )).scalar_one()
        session = await create_chat_session(db, str(officer.id))
        session_id = session.id

    # ---- 1) a DB-side failure during save_document must not orphan the raw file ----
    orig_build_chunks = attachment_store.build_chunks
    attachment_store.build_chunks = lambda *a, **kw: (_ for _ in ()).throw(
        RuntimeError("simulated DB-path failure"))
    stored_names_before = set(p.name for p in attachment_store.storage_dir().iterdir())
    try:
        async with AsyncSessionLocal() as db:
            try:
                await attachment_store.save_document(
                    db=db, officer_id=officer.id, session_id=session_id,
                    filename="orphan_test.txt", ext=".txt", mime_type="text/plain",
                    raw=b"hello world, this is a test attachment body",
                    extracted=ExtractedDocument(
                        status=doc_extract.STATUS_OK,
                        segments=[Segment("hello world", {"kind": "lines", "start": 1, "end": 1})]))
                check("save_document raises when the DB-side step fails", False)
            except RuntimeError:
                check("save_document propagates the simulated DB-side failure", True)
    finally:
        attachment_store.build_chunks = orig_build_chunks

    stored_names_after = set(p.name for p in attachment_store.storage_dir().iterdir())
    check("no new raw file was left behind after the failed save_document call",
          stored_names_after == stored_names_before,
          f"new files: {stored_names_after - stored_names_before}")

    async with AsyncSessionLocal() as db:
        leftover = (await db.execute(
            select(ChatAttachment).where(ChatAttachment.session_id == session_id)
        )).scalars().all()
        check("no ChatAttachment row was committed for the failed upload",
              len(leftover) == 0, [str(r.id) for r in leftover])

    # ---- 2) a genuinely successful upload is unaffected by the above path ----
    async with AsyncSessionLocal() as db:
        doc = await attachment_store.save_document(
            db=db, officer_id=officer.id, session_id=session_id,
            filename="real_test.txt", ext=".txt", mime_type="text/plain",
            raw=b"a perfectly normal attachment body",
            extracted=ExtractedDocument(
                status=doc_extract.STATUS_OK,
                segments=[Segment("a perfectly normal attachment body",
                                  {"kind": "lines", "start": 1, "end": 1})]))
        check("a normal upload still succeeds and gets a stored_name",
              bool(doc.stored_name))
        real_stored_name = doc.stored_name
        real_doc_id = doc.id

    # ---- 3) the orphan sweep removes an old unreferenced file but leaves the
    #         referenced one and a freshly-written (grace-period) one alone ----
    storage = attachment_store.storage_dir()
    orphan_old = storage / f"{uuid.uuid4().hex}.txt"
    orphan_old.write_bytes(b"nobody points at me")
    old_time = time.time() - attachment_store._ORPHAN_RAW_FILE_GRACE_SECONDS - 120
    os.utime(orphan_old, (old_time, old_time))

    orphan_fresh = storage / f"{uuid.uuid4().hex}.txt"
    orphan_fresh.write_bytes(b"i was just written, give me a grace period")

    try:
        async with AsyncSessionLocal() as db:
            removed = await attachment_store.sweep_orphan_raw_files(db)
        check("the sweep removed at least the one old orphan file", removed >= 1, removed)
        check("the old, unreferenced file is gone", not orphan_old.exists())
        check("a freshly-written, unreferenced file is left alone (grace period)",
              orphan_fresh.exists())
        check("the real, DB-referenced file from step 2 survives the sweep",
              (storage / real_stored_name).exists())
    finally:
        orphan_fresh.unlink(missing_ok=True)
        orphan_old.unlink(missing_ok=True)

    # ---- cleanup: remove the real test attachment we created ----
    async with AsyncSessionLocal() as db:
        await attachment_store.delete_document(db, officer.id, session_id, real_doc_id)

    # ---- 4) a failure AFTER a successful commit must NOT delete the raw file
    #         or otherwise undo work that is already durably stored. The only
    #         `except` blocks in `save_document` wrap the code BEFORE
    #         `await db.commit()`; `db.refresh(doc)` and everything after it
    #         sit outside that try/except entirely, so an exception there
    #         propagates to the caller untouched -- no rollback, no
    #         `_remove_raw`, because there is nothing left to undo. ----
    async with AsyncSessionLocal() as db2:
        raw_bytes = b"a committed-then-refresh-fails attachment body"
        orig_refresh = db2.refresh

        async def _failing_refresh(instance, *a, **kw):
            raise RuntimeError("simulated failure AFTER commit (e.g. a dropped connection)")

        db2.refresh = _failing_refresh
        try:
            await attachment_store.save_document(
                db=db2, officer_id=officer.id, session_id=session_id,
                filename="post_commit_fail.txt", ext=".txt", mime_type="text/plain",
                raw=raw_bytes,
                extracted=ExtractedDocument(
                    status=doc_extract.STATUS_OK,
                    segments=[Segment("a committed-then-refresh-fails attachment body",
                                      {"kind": "lines", "start": 1, "end": 1})]))
            check("save_document raises when the post-commit refresh fails", False)
        except RuntimeError:
            check("the simulated post-commit failure propagates (not swallowed)", True)
        finally:
            db2.refresh = orig_refresh

    digest = __import__("hashlib").sha256(raw_bytes).hexdigest()
    async with AsyncSessionLocal() as db3:
        committed = (await db3.execute(
            select(ChatAttachment).where(
                ChatAttachment.session_id == session_id,
                ChatAttachment.content_hash == digest))).scalar_one_or_none()
        check("the DB row from BEFORE the failed refresh is still there and still active "
              "-- a post-commit failure must never be treated as if the upload failed",
              committed is not None and committed.is_active, committed)
        committed_stored_name = committed.stored_name if committed else None

    check("the raw file the (already-committed) row points to still exists on disk -- "
          "it was never deleted just because `db.refresh` failed afterwards",
          bool(committed_stored_name)
          and (attachment_store.storage_dir() / committed_stored_name).exists())

    # cleanup
    if committed is not None:
        async with AsyncSessionLocal() as db:
            await attachment_store.delete_document(db, officer.id, session_id, committed.id)

    # ---- 5) two concurrent uploads of the IDENTICAL bytes must never create
    #         two active rows -- `uq_attachment_active_dedupe` (a partial
    #         unique index, active rows only) plus `save_document`'s
    #         IntegrityError recovery path turn the race into "exactly one
    #         wins, the other transparently reuses it", not a crash and not
    #         a silent duplicate. ----
    race_bytes = b"two officers' browsers racing the exact same upload"
    race_digest = __import__("hashlib").sha256(race_bytes).hexdigest()

    async def _one_upload():
        async with AsyncSessionLocal() as db:
            return await attachment_store.save_document(
                db=db, officer_id=officer.id, session_id=session_id,
                filename="race_test.txt", ext=".txt", mime_type="text/plain",
                raw=race_bytes,
                extracted=ExtractedDocument(
                    status=doc_extract.STATUS_OK,
                    segments=[Segment("two officers' browsers racing the exact same upload",
                                      {"kind": "lines", "start": 1, "end": 1})]))

    results = await asyncio.gather(_one_upload(), _one_upload(), return_exceptions=True)
    exceptions = [r for r in results if isinstance(r, BaseException)]
    check("neither side of a concurrent identical upload raises an unhandled exception",
          not exceptions, exceptions)
    ids = sorted({str(r.id) for r in results if not isinstance(r, BaseException)})
    check("both concurrent uploads resolve to the SAME document (one side won, "
          "the other reused it) rather than two separate rows",
          len(ids) == 1, ids)

    async with AsyncSessionLocal() as db:
        active_rows = (await db.execute(
            select(ChatAttachment).where(
                ChatAttachment.session_id == session_id,
                ChatAttachment.content_hash == race_digest,
                ChatAttachment.is_active.is_(True)))).scalars().all()
        check("exactly one ACTIVE row exists in the database for the raced content hash",
              len(active_rows) == 1, [str(r.id) for r in active_rows])
        for r in active_rows:
            await attachment_store.delete_document(db, officer.id, session_id, r.id)


asyncio.run(_run())
print(f"{total - len(bad)}/{total} ok")
sys.exit(1 if bad else 0)
