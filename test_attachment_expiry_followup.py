"""Regression: a vague pronoun follow-up ("what about its status?") asked
after the officer's uploaded document has expired must get the deterministic
"please re-upload" notice, not fall through to the ordinary SIS pipeline
and answer from something else (or nothing).

Root cause (backend/services/attachment_qa.py, `_maybe_expired_notice`):
it only fires when the message names a file explicitly -- a filename, a
page/filetype reference, or one of `_DOC_WORDS` ("the file", "the
document", ...). A bare pronoun ("it", "its") is in none of those, so after
`plan_answer()` finds no active document (`active_documents()` already
filters by `expires_at > now`) AND `_maybe_expired_notice` also declines,
the turn silently falls through to the ordinary follow-up/intent pipeline
with no idea an attachment was ever relevant.

Fix: `_maybe_expired_notice` also fires on a bare singular back-reference
("it"/"its"/"that document"/Tamil "அது"/"இது") WHEN the immediately
preceding assistant turn was itself about an attachment (the same
`attachment_topic_active` signal `plan_answer` already uses elsewhere) --
so a genuine continuation of an attachment conversation still gets the
re-upload notice after expiry, while an unrelated bare "it" with no such
history (and no document ever having existed) is correctly left alone.

Needs the live DB (real ChatSession/ChatAttachment/ChatMessage rows; no
Ollama -- this path never reaches the LLM).
Run: python test_attachment_expiry_followup.py
"""
import asyncio
import sys
import uuid
from datetime import datetime, timedelta, timezone

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

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
    from backend.schemas import OfficerContext
    from backend.services.auth_service import get_officer_jurisdiction_ids
    from backend.services import chatbot as cb, attachment_store
    from backend.services.doc_extract import ExtractedDocument, Segment, STATUS_OK

    async def make_ctx(email):
        async with AsyncSessionLocal() as db:
            res = await db.execute(select(SISOfficer).where(SISOfficer.email == email))
            officer = res.scalar_one()
            jur = await get_officer_jurisdiction_ids(officer.id, db)
            ids = (jur["district_ids"] + jur["taluk_ids"] + jur["town_ids"]
                   + jur["ward_ids"] + jur["block_ids"])
            return officer, OfficerContext(
                officer_id=officer.id, employee_id=officer.employee_id, name=officer.name,
                email=officer.email, designation=officer.designation,
                jurisdiction_type=jur["jurisdiction_type"], jurisdiction_name=jur["jurisdiction_name"],
                jurisdiction_ids=[i for i in ids if i])

    officer, ctx = await make_ctx("marun_chn@sis.tn.gov.in")

    async def turn(msg, hist, sid):
        async with AsyncSessionLocal() as db:
            r = await cb.process_chat(db=db, message=msg, officer=ctx, chat_history=hist, session_id=sid)
        resp = (r.get("response") if isinstance(r, dict) else r) or ""
        hist.append({"role": "user", "content": msg})
        hist.append({"role": "assistant", "content": resp})
        return r

    hist = []
    async with AsyncSessionLocal() as db:
        session = await cb.create_chat_session(db, str(officer.id))
        sid = str(session.id)

    async with AsyncSessionLocal() as db:
        doc = await attachment_store.save_document(
            db=db, officer_id=officer.id, session_id=sid,
            filename="order_copy.txt", ext=".txt", mime_type="text/plain",
            raw=b"Order approving patta transfer for application number 2026/0154/02/013642",
            extracted=ExtractedDocument(
                status=STATUS_OK,
                segments=[Segment("Order approving patta transfer for application "
                                  "number 2026/0154/02/013642",
                                  {"kind": "lines", "start": 1, "end": 1})]))
        doc_id = doc.id

    # Turn 1: ask about the (still active) document -- establishes attachment focus.
    r1 = await turn("in order_copy.txt, what does it say about the approval?", hist, sid)
    check("turn 1: the still-active document answers the question",
          "2026/0154/02/013642" in (r1.get("response") or ""), r1.get("response")[:200])

    # Force the attachment to be expired, as if UPLOAD_RETENTION_MINUTES had
    # already passed -- the real expiry path, not a mocked clock.
    async with AsyncSessionLocal() as db:
        row = (await db.execute(
            select(ChatAttachment).where(ChatAttachment.id == doc_id))).scalar_one()
        row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        await db.commit()

    # Turn 2: a bare pronoun follow-up, right after an attachment-focused turn.
    r2 = await turn("what about its status?", hist, sid)
    resp2 = (r2.get("response") or "")
    check("turn 2: the expired-document re-upload notice fires for a bare "
          "pronoun follow-up right after an attachment-focused turn",
          "upload" in resp2.lower() and ("again" in resp2.lower() or "re-upload" in resp2.lower()),
          resp2)
    check("turn 2: the expired document's old content is never echoed back "
          "as if it were still readable",
          "2026/0154/02/013642" not in resp2, resp2)

    # Turn 3 (separate session): the SAME bare "its status" with NO attachment
    # ever having been part of this conversation must NOT be swept into the
    # expired-document notice -- it has nothing to do with a file.
    sid2 = str(uuid.uuid4())
    async with AsyncSessionLocal() as db:
        session2 = await cb.create_chat_session(db, str(officer.id))
        sid2 = str(session2.id)
    hist2 = []
    r3 = await turn("what about its status?", hist2, sid2)
    resp3 = (r3.get("response") or "")
    check("turn 3: the SAME bare pronoun with no prior attachment focus at "
          "all is NOT misread as an expired-document question",
          "upload" not in resp3.lower() or "re-upload" not in resp3.lower(),
          resp3)

    # cleanup
    async with AsyncSessionLocal() as db:
        await attachment_store.delete_document(db, officer.id, sid, doc_id)

    # ---- Follow-up after DELETION (not expiry) must behave the same way:
    #      no recovered content, a clear re-upload notice for an explicit
    #      reference, and no leakage into an unrelated answer. ----
    sid4 = str(uuid.uuid4())
    async with AsyncSessionLocal() as db:
        session4 = await cb.create_chat_session(db, str(officer.id))
        sid4 = str(session4.id)
    hist4 = []
    async with AsyncSessionLocal() as db:
        doc4 = await attachment_store.save_document(
            db=db, officer_id=officer.id, session_id=sid4,
            filename="memo_for_deletion.txt", ext=".txt", mime_type="text/plain",
            raw=b"Order of approval for patta transfer, application number 2026/0154/02/099911",
            extracted=ExtractedDocument(
                status=STATUS_OK,
                segments=[Segment("Order approving patta transfer for application "
                                  "number 2026/0154/02/099911",
                                  {"kind": "lines", "start": 1, "end": 1})]))
        doc4_id = doc4.id
    r_del_1 = await turn("in memo_for_deletion.txt, what application number does it mention?", hist4, sid4)
    check("deletion scenario setup: the active document answers first",
          "2026/0154/02/099911" in (r_del_1.get("response") or ""), r_del_1.get("response"))
    async with AsyncSessionLocal() as db:
        removed = await attachment_store.delete_document(db, officer.id, sid4, doc4_id)
    check("the document was actually deleted", removed)
    r_del_2 = await turn("what about its status?", hist4, sid4)
    resp_del_2 = (r_del_2.get("response") or "")
    check('after DELETION, a bare "its status" follow-up right after an '
          "attachment-focused turn gets the re-upload notice, not a silent "
          "fallthrough",
          "upload" in resp_del_2.lower(), resp_del_2)
    check("after deletion, the deleted document's content is never echoed back",
          "2026/0154/02/099911" not in resp_del_2, resp_del_2)

    # ---- Follow-up after starting a NEW CHAT (fresh session, same officer):
    #      the old session's attachment focus must not leak into a session
    #      that never saw an upload at all. ----
    sid5 = str(uuid.uuid4())
    async with AsyncSessionLocal() as db:
        session5 = await cb.create_chat_session(db, str(officer.id))
        sid5 = str(session5.id)
    hist5 = []
    r_new = await turn("what about its status?", hist5, sid5)
    resp_new = (r_new.get("response") or "")
    check("a brand-new chat session with no upload of its own does not "
          "inherit another session's attachment focus",
          "upload" not in resp_new.lower() or "re-upload" not in resp_new.lower(),
          resp_new)

    # ---- Multiple active documents with an AMBIGUOUS bare reference: the
    #      assistant must ask which file, never silently pick one. ----
    sid6 = str(uuid.uuid4())
    async with AsyncSessionLocal() as db:
        session6 = await cb.create_chat_session(db, str(officer.id))
        sid6 = str(session6.id)
        docA = await attachment_store.save_document(
            db=db, officer_id=officer.id, session_id=sid6,
            filename="file_a.txt", ext=".txt", mime_type="text/plain",
            raw=b"File A: approved on 2026-01-01",
            extracted=ExtractedDocument(
                status=STATUS_OK,
                segments=[Segment("File A: approved on 2026-01-01",
                                  {"kind": "lines", "start": 1, "end": 1})]))
        docB = await attachment_store.save_document(
            db=db, officer_id=officer.id, session_id=sid6,
            filename="file_b.txt", ext=".txt", mime_type="text/plain",
            raw=b"File B: rejected on 2026-02-02",
            extracted=ExtractedDocument(
                status=STATUS_OK,
                segments=[Segment("File B: rejected on 2026-02-02",
                                  {"kind": "lines", "start": 1, "end": 1})]))
    hist6 = []
    r_ambig = await turn("summarize it", hist6, sid6)
    resp_ambig = (r_ambig.get("response") or "")
    check("with two active, equally-unfocused documents, a bare reference "
          "asks which file instead of silently picking one",
          "which" in resp_ambig.lower() or "file_a" in resp_ambig.lower()
          and "file_b" in resp_ambig.lower(), resp_ambig)
    check("an ambiguous multi-document reference never invents a merged "
          "answer from both files at once",
          not ("2026-01-01" in resp_ambig and "2026-02-02" in resp_ambig), resp_ambig)
    async with AsyncSessionLocal() as db:
        await attachment_store.delete_document(db, officer.id, sid6, docA.id)
        await attachment_store.delete_document(db, officer.id, sid6, docB.id)

    # ---- An ORDINARY SIS register question while an attachment is active
    #      must still go to the register, not be hijacked by the file. ----
    sid7 = str(uuid.uuid4())
    async with AsyncSessionLocal() as db:
        session7 = await cb.create_chat_session(db, str(officer.id))
        sid7 = str(session7.id)
        await attachment_store.save_document(
            db=db, officer_id=officer.id, session_id=sid7,
            filename="unrelated_memo.txt", ext=".txt", mime_type="text/plain",
            raw=b"This memo has nothing to do with any specific application.",
            extracted=ExtractedDocument(
                status=STATUS_OK,
                segments=[Segment("This memo has nothing to do with any specific application.",
                                  {"kind": "lines", "start": 1, "end": 1})]))
    hist7 = []
    r_sis = await turn("show my pending applications", hist7, sid7)
    check("an ordinary 'show my pending applications' with an unrelated "
          "attachment active still reaches the register, not the attachment "
          "evidence path",
          r_sis.get("intent") not in ("general_query", None)
          and "could not find this in the uploaded" not in (r_sis.get("response") or "").lower(),
          r_sis.get("intent"))

    # ---- Tanglish/Tamil bare back-references ("adhoda", "andha document")
    #      right after an attachment-focused turn must resolve the same way
    #      "its"/"that" does in English. ----
    sid8 = str(uuid.uuid4())
    async with AsyncSessionLocal() as db:
        session8 = await cb.create_chat_session(db, str(officer.id))
        sid8 = str(session8.id)
        doc8 = await attachment_store.save_document(
            db=db, officer_id=officer.id, session_id=sid8,
            filename="tanglish_test.txt", ext=".txt", mime_type="text/plain",
            raw=b"Order approving patta transfer for application number 2026/0154/02/055221",
            extracted=ExtractedDocument(
                status=STATUS_OK,
                segments=[Segment("Order approving patta transfer for application "
                                  "number 2026/0154/02/055221",
                                  {"kind": "lines", "start": 1, "end": 1})]))
        doc8_id = doc8.id
    hist8 = []
    await turn("in tanglish_test.txt, enna irukku approval pathi?", hist8, sid8)
    async with AsyncSessionLocal() as db:
        row8 = (await db.execute(
            select(ChatAttachment).where(ChatAttachment.id == doc8_id))).scalar_one()
        row8.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        await db.commit()
    r_tanglish = await turn("andha document status enna?", hist8, sid8)
    resp_tanglish = (r_tanglish.get("response") or "")
    check('a Tanglish bare back-reference ("andha document") after expiry '
          "also gets the re-upload notice (Tanglish input is detected as "
          "Tamil, so the notice itself is in Tamil script -- checked for "
          "either language's wording)",
          "upload" in resp_tanglish.lower() or "பதிவேற்ற" in resp_tanglish, resp_tanglish)
    async with AsyncSessionLocal() as db:
        await attachment_store.delete_document(db, officer.id, sid8, doc8_id)


asyncio.run(_run())
print(f"{total - len(bad)}/{total} ok")
sys.exit(1 if bad else 0)
