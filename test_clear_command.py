# -*- coding: utf-8 -*-
"""Test the typed "clear" command -- what counts as one, and what it does.

A wipe is destructive from the officer's side: the transcript and the stored
history both go. So this suite checks it from both ends -- that every ordinary
wording of the command is recognised, that nothing which merely *contains* a
wipe verb is (a listing query, a question about the record, an
acknowledgement), and that a recognised one really produces the instruction
the frontend acts on.

    python test_clear_command.py            # classification + end-to-end (DB)
    python test_clear_command.py --routing  # classification only, no database

No LLM is needed either way: a contentless turn is answered before any model
is reached, which is the whole point of it.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import uuid
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.services.chatbot import (_contentless_message, _contentless_reply,
                                      _is_mutation_request)

# ─────────────────────────────────────────────────────────────────────────────
# 1. What counts as a clear
#
# `None` means "not contentless" -- the message carries a question and goes
# down the ordinary pipeline.
# ─────────────────────────────────────────────────────────────────────────────
CLASSIFICATION = [
    # -- the command itself, in the wordings officers actually type -----------
    ("clear", "clear"),
    ("Clear", "clear"),
    ("  clear  ", "clear"),
    ("clear.", "clear"),
    ("clear!", "clear"),
    ("clear chat", "clear"),
    ("clear the chat", "clear"),
    ("clear this chat", "clear"),
    ("clear my chat", "clear"),
    ("clear chats", "clear"),
    ("clear conversation", "clear"),
    ("clear the conversation", "clear"),
    ("clear this conversation", "clear"),
    ("clear the above conversation", "clear"),
    ("clear above chat", "clear"),
    ("clear all", "clear"),
    ("clear all chats", "clear"),
    ("clear all messages", "clear"),
    ("clear history", "clear"),
    ("clear chat history", "clear"),
    ("clear my chat history", "clear"),
    ("clear the history please", "clear"),
    ("clear everything", "clear"),
    ("clear screen", "clear"),
    ("clear transcript", "clear"),
    ("clear this session", "clear"),
    ("clear chat pls", "clear"),
    ("please clear the chat", "clear"),
    ("kindly clear the conversation", "clear"),
    # the verb need not come first
    ("chat clear", "clear"),
    ("chat history clear", "clear"),
    ("conversation clear", "clear"),
    # other wipe verbs
    ("cls", "clear"),
    ("clr", "clear"),
    ("reset", "clear"),
    ("reset chat", "clear"),
    ("reset the conversation", "clear"),
    ("restart", "clear"),
    ("start over", "clear"),
    ("wipe", "clear"),
    ("wipe the chat", "clear"),
    ("wipe chat history", "clear"),
    ("erase", "clear"),
    ("erase everything", "clear"),
    ("erase this conversation", "clear"),
    ("delete this chat", "clear"),
    ("delete the conversation", "clear"),
    ("delete chat history", "clear"),
    ("delete all messages", "clear"),
    ("remove chat history", "clear"),
    ("remove this conversation", "clear"),
    ("clean the chat", "clear"),
    ("flush the chat", "clear"),
    # the new-chat wording is the same command from the officer's side
    ("new chat", "clear"),
    ("newchat", "clear"),
    ("new conversation", "clear"),
    ("start a new chat", "clear"),
    ("start new conversation", "clear"),
    ("begin a new chat", "clear"),
    ("open a new chat", "clear"),
    # Tamil / Tanglish
    (u"அழி", "clear"),
    (u"அழிக்கவும்", "clear"),
    (u"உரையாடலை அழி", "clear"),
    (u"உரையாடலை அழிக்கவும்", "clear"),
    (u"அரட்டையை நீக்கு", "clear"),
    (u"மீட்டமை", "clear"),
    (u"புதிய உரையாடல்", "clear"),
    ("azhi", "clear"),

    # -- long polite phrasings (English) -------------------------------------
    # These cover the token-punctuation-stripping fix: "Hey," must not block
    # "hey" from matching _CLEAR_FILLER. The content check (not the length)
    # is what ensures safety -- every case below has no word that is neither
    # a wipe verb, a transcript noun, nor filler.
    ("Can you please clear this whole conversation for me now", "clear"),
    ("I want you to clear all of this chat history right now please", "clear"),
    ("Hey, could you go ahead and clear the entire conversation history for me",
     "clear"),
    ("Please delete everything in this chat and start fresh", "clear"),
    ("I would like to reset this conversation completely if you can", "clear"),
    ("Can you wipe out all the messages in this current chat session", "clear"),
    ("Go ahead and clear all my chat messages please", "clear"),
    ("Would you mind clearing the entire conversation thread for me?", "clear"),
    # A genuinely long (>40 token) but still pure filler/verb/noun request --
    # a hard word-count cap here once rejected this outright, contradicting
    # the "no length limit, safety is content-based" design above.
    ("please can you kindly go ahead and clear the entire whole current "
     "previous old full conversation history chat messages session thread "
     "now right completely totally fully entirely for me us right now please",
     "clear"),
    # Just as long, but carries real content throughout -- must NOT clear.
    ("please can you kindly go ahead and tell me right now how many pending "
     "applications overdue field visits and survey numbers i have in my "
     "ward for this month completely and totally if that is okay thanks",
     None),

    # -- long polite phrasings (Tamil) ---------------------------------------
    # Tamil is matched as substrings -- the virama is not a word boundary.
    # Long Tamil sentences that still only express the wipe command.
    (u"தயவுசெய்து இந்த முழு உரையாடலையும் அழித்துவிடுங்கள்", "clear"),
    (u"இந்த அரட்டை வரலாறு முழுவதையும் இப்போது நீக்கிவிடு", "clear"),
    (u"நண்பரே, தயவுசெய்து இந்த சாட் ஹிஸ்டரி முழுவதையும் அழிக்க முடியுமா",
     "clear"),
    (u"இந்த உரையாடலை முழுவதுமாக அழித்து புதிதாக தொடங்கவும்", "clear"),

    # -- NOT a clear: a wipe verb aimed at the register, not the transcript ---
    ("clear my pending applications", None),
    ("clear all overdue applications", None),
    ("clear ward 102 applications", None),
    ("clear the sub-division remarks", None),
    ("clear the encroachment note for 2026/0154/28/001280", None),
    ("delete application 2026/0154/28/001280", None),
    ("remove the field visit for survey 5", None),
    ("reset the status of 2022/0153/28/000254", None),
    ("wipe the knowledge base", None),
    ("clear the database", None),
    ("clear all data", None),
    ("erase all records", None),

    # -- NOT a clear: an ordinary question that happens to carry the word ----
    ("is the sketch clear", None),
    ("everything is clear", None),
    ("all clear", None),
    ("is it clear now", None),
    ("the boundary is not clear", None),
    ("new application", None),
    ("start survey 5", None),
    ("open 2026/0154/28/001280", None),
    ("how many applications are approved", None),
    ("show my pending applications", None),
    ("what is 0153?", None),

    # -- long real questions that must NOT be treated as a clear -------------
    # These carry domain words ("pending", "applications", "jurisdiction",
    # "விண்ணப்பங்கள்") that are neither the verb, a transcript noun, nor filler.
    ("Can you please tell me how many pending applications I currently have "
     "in my jurisdiction", None),
    (u"தயவுசெய்து எனது நிலுவையில் உள்ள விண்ணப்பங்களின் எண்ணிக்கையை கூறுங்கள்",
     None),

    # -- the other contentless kinds are unaffected --------------------------
    ("ok", "ack"),
    ("okay", "ack"),
    ("hmm", "ack"),
    ("...", "ack"),
    (u"சரி", "ack"),
    ("done", "ack"),
    ("exit", "session_command"),
    ("logout", "session_command"),
    ("log out", "session_command"),
    ("quit", "session_command"),
    # yes / no can answer a disambiguation the assistant itself asked, so they
    # must stay ordinary messages.
    ("yes", None),
    ("no", None),
]


def check_classification() -> int:
    fails = 0
    for message, expected in CLASSIFICATION:
        got = _contentless_message(message)
        if got != expected:
            fails += 1
            print("  FAIL  %-46s expected=%-16s got=%s"
                  % (ascii(message), expected, got))
    print("classification: %d cases, %d failed" % (len(CLASSIFICATION), fails))

    # A wipe command must never be claimed by the mutation refusal instead.
    # The clear path runs first in both chat paths, but nothing should depend
    # on that ordering.
    shadowed = [m for m, e in CLASSIFICATION
                if e == "clear" and _is_mutation_request(m)]
    print("mutation-refusal overlap: %d (want 0)" % len(shadowed))
    for m in shadowed:
        print("  FAIL  %s reads as a mutation request" % ascii(m))
    fails += len(shadowed)

    # ...while a real mutation request still is one.
    for m in ("clear the database", "erase all records", "wipe the knowledge base"):
        if not _is_mutation_request(m):
            fails += 1
            print("  FAIL  %s no longer reads as a mutation request" % ascii(m))

    # The reply follows the officer's script, like everywhere else in the app.
    en = _contentless_reply("clear", "en")
    for lang in ("ta", "tanglish"):
        ta = _contentless_reply("clear", lang)
        if ta == en or not any("஀" <= ch <= "௿" for ch in ta):
            fails += 1
            print("  FAIL  clear reply for %s is not Tamil" % lang)
    print("reply language: en=%r ta=Tamil" % en)
    return fails


# ─────────────────────────────────────────────────────────────────────────────
# 2. What a clear turn actually emits
#
# The frontend wipes the transcript on `action="clear_chat"`, so the contract
# is: exactly that action, the confirmation text beside it, and NOTHING
# written to chat_messages -- the session it would be written to is about to
# be replaced.
# ─────────────────────────────────────────────────────────────────────────────
E2E_CLEAR = ["clear", "clear the conversation", "clear chat history",
             "delete this chat", "chat history clear", "new chat",
             u"உரையாடலை அழி"]
E2E_OTHER = ["ok", "exit", "show my pending applications"]


def _frames(raw: str):
    out = []
    for event in raw.split("\n\n"):
        event = event.strip()
        if event.startswith("data: "):
            try:
                out.append(json.loads(event[6:]))
            except ValueError:
                out.append({"_unparsed": event[:80]})
    return out


async def check_end_to_end() -> int:
    logging.disable(logging.INFO)
    from sqlalchemy import func, select

    from backend.database import AsyncSessionLocal
    from backend.models import ChatMessage, SISOfficer
    from backend.schemas import OfficerContext
    from backend.services.auth_service import get_officer_jurisdiction_ids
    from backend.services.chatbot import (create_chat_session, process_chat,
                                          process_chat_stream)

    fails = 0
    async with AsyncSessionLocal() as db:
        officer = (await db.execute(select(SISOfficer).limit(1))).scalars().first()
        if officer is None:
            print("no officers seeded -- run the sample_db build first")
            return 1
        jur = await get_officer_jurisdiction_ids(officer.id, db)
        ids = (jur["district_ids"] + jur["taluk_ids"] + jur["town_ids"]
               + jur["ward_ids"] + jur["block_ids"])
        ctx = OfficerContext(
            officer_id=officer.id, employee_id=officer.employee_id,
            name=officer.name, email=officer.email,
            designation=officer.designation,
            jurisdiction_type=jur["jurisdiction_type"],
            jurisdiction_name=jur["jurisdiction_name"],
            jurisdiction_ids=[i for i in ids if i])

        session = await create_chat_session(db, officer.id)
        session_id = str(session.id)
        print("\nofficer: %s   session: %s" % (officer.name, session_id))

        async def stored() -> int:
            return (await db.execute(
                select(func.count()).select_from(ChatMessage)
                .where(ChatMessage.session_id == uuid.UUID(session_id)))).scalar()

        for message in E2E_CLEAR + E2E_OTHER:
            wants_clear = message in E2E_CLEAR
            before = await stored()
            raw = b""
            async for chunk in process_chat_stream(
                    message=message, session_id=session_id, officer=ctx,
                    db=db, chat_history=[]):
                raw += chunk if isinstance(chunk, bytes) else chunk.encode("utf-8")
            frames = _frames(raw.decode("utf-8"))
            action = next((f.get("action") for f in frames if f.get("action")), None)
            text = "".join(f.get("content", "") for f in frames)
            await db.commit()
            after = await stored()

            problems = []
            if (action == "clear_chat") != wants_clear:
                problems.append("action=%s" % action)
            if wants_clear and after != before:
                problems.append("clear turn was persisted (%d->%d)" % (before, after))
            if not wants_clear and after <= before:
                problems.append("ordinary turn was not persisted")
            if wants_clear and len(frames) != 1:
                problems.append("%d frames" % len(frames))
            if wants_clear and not text.strip():
                problems.append("no confirmation text")
            fails += bool(problems)
            print("  %s %-42s action=%-11s rows %d->%d %s"
                  % ("FAIL" if problems else "ok  ", ascii(message), action,
                     before, after, "; ".join(problems)))

        # The non-streaming path carries the same contract.
        for message in ("clear", "clear the conversation", "ok"):
            wants_clear = message != "ok"
            result = await process_chat(message=message, session_id=session_id,
                                        officer=ctx, db=db, chat_history=[])
            bad = (result.get("action") == "clear_chat") != wants_clear
            fails += bad
            print("  %s %-42s action=%-11s intent=%s"
                  % ("FAIL" if bad else "ok  ", ascii("non-stream: " + message),
                     result.get("action"), result.get("intent")))
    return fails


def main() -> int:
    routing_only = "--routing" in sys.argv
    print("=" * 72)
    print("clear command")
    print("=" * 72)
    fails = check_classification()
    if not routing_only:
        fails += asyncio.run(check_end_to_end())
    print("\n%s (%d failure(s))" % ("PASS" if not fails else "FAIL", fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
