"""Every way of asking to remove the conversation -- and every way of asking what that does.

1. CLEAR   wordings (English / Tamil / Tanglish) must be carried out as a clear
2. INFO    questions ("what happens if I clear the conversation?") must be ANSWERED, in the
           officer's language, and never carried out
3. OTHER   messages that mention the same verbs about the REGISTER must not be read as a clear

python test_clear_words.py          # no LLM
"""
import asyncio
import logging
import re
import sys
import uuid

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
logging.disable(logging.CRITICAL)

from sqlalchemy import select

from backend.database import AsyncSessionLocal, engine
from backend.models import ChatMessage, SISOfficer
from backend.sample_db.build_lora_dataset import build_officer_context, _flatten_html
from backend.services import chatbot, rag

FAILS = []

CLEAR = [
    # English
    "remove the conversation", "remove this conversation", "remove conversation", "expunge the conversation",
    "expunge this chat", "expunge chat", "purge the conversation", "purge chat history", "discard the conversation",
    "discard this chat", "delete the conversation", "delete conversation", "delete this conversation", "delete chat",
    "delete my chat", "delete the chat history", "erase the conversation", "erase this chat", "erase chat history",
    "wipe the conversation", "wipe this chat", "wipe chat", "wipe out the conversation", "clear the entire conversation",
    "clear whole conversation", "clear all messages", "clear my chat", "clear this chat", "clear the history",
    "get rid of the conversation", "get rid of this chat", "get rid of the chat history", "forget this conversation",
    "forget the conversation", "forget everything", "start over", "start fresh", "fresh start", "begin again",
    "restart the conversation", "restart chat", "restart", "reset the conversation", "reset this chat", "new conversation",
    "new session", "start a new conversation", "open a new chat", "end this conversation and start a new one",
    "please clear the conversation", "could you please delete this chat", "can you wipe the chat history",
    "I want to delete the conversation", "I want to clear the chat", "remove all the messages", "remove chat history",
    "delete all messages", "delete everything in this chat", "clear convo", "delete convo", "wipe convo",
    # Tamil
    "உரையாடலை அழி", "உரையாடலை நீக்கு", "இந்த உரையாடலை அழிக்கவும்", "உரையாடலை நீக்கவும்", "அரட்டையை அழி",
    "அரட்டையை நீக்கு", "சாட்டை அழி", "சாட் ஹிஸ்டரியை அழிக்கவும்", "உரையாடல் வரலாற்றை அழி", "எல்லா செய்திகளையும் அழி",
    "புதிய உரையாடல்", "புதிதாகத் தொடங்கு", "உரையாடலைத் துடை", "உரையாடலை அகற்று",
    # Tanglish
    "conversation ah azhi", "chat ah azhichidu", "chat ah delete pannu", "conversation ah delete pannunga",
    "conversation ah remove pannu", "chat ah clear pannu", "chat clear pannunga", "clear pannu", "clear panren",
    "pazhaya chat ah delete pannu", "pazhaya conversation ah neekku", "conversation ah neekkunga", "chat ah neekku",
    "chat history ah azhichidu", "ellaa messages um delete pannu", "muzhu conversation um clear pannu",
    "puthusa start pannu", "puthu chat start pannu", "convo ah clear pannu",
    "delete my whole chat", "remove the previous conversation", "conversation remove pannu", "delete all the earlier messages",
    "expunge everything", "purge everything", "wipe everything", "clear the screen", "erase our conversation",
    "இந்த சாட்டை நீக்கு", "சாட் கிளியர் பண்ணு", "chat ah clear seyyu", "old chat ah azhichidu", "clear pannidu",
    "conversation ah delete panidu", "chat delete pannu da", "clear the chat pls", "cls chat", "clear conversation history now",
    "kindly wipe the entire chat history", "delete the chat and start again",
]

INFO = [
    # (message, topic keyword that the answer must carry)
    ("what happens if I clear the conversation?", "new"),
    ("what happens if i wipe the entire conversation", "new"),
    ("what will happen if I delete this chat", "new"),
    ("what happens when I remove the conversation", "new"),
    ("what if I expunge the conversation", "new"),
    ("what does clear chat do", "new"),
    ("what does clearing the conversation mean", "new"),
    ("what happens to my applications if I clear the chat", "applications"),
    ("will my applications be deleted if I wipe the conversation", "applications"),
    ("does clearing the chat delete any records", "records"),
    ("will I lose my data if I delete the conversation", "records"),
    ("is it safe to clear the conversation", "records"),
    ("can I get back the conversation after I clear it", "come back"),
    ("can I undo clear chat", "come back"),
    ("can I recover a deleted conversation", "come back"),
    ("is the chat history restored after wiping it", "come back"),
    ("how do I clear the conversation", "clear"),
    ("how to delete chat history", "clear"),
    ("how can I start a new chat", "clear"),
    ("what happens if I purge the chat history and start over", "new"),
    ("does wiping the conversation change the register", "records"),
    ("if I clear the chat will the assistant remember anything", "remember"),
    ("will you forget everything if I clear the conversation", "remember"),
    # Tamil
    ("உரையாடலை அழித்தால் என்ன ஆகும்", "புதிய"),
    ("உரையாடலை நீக்கினால் என்ன நடக்கும்", "புதிய"),
    ("சாட்டை அழித்தால் என் விண்ணப்பங்கள் அழியுமா", "விண்ணப்ப"),
    ("உரையாடலை அழித்தால் பதிவுகள் நீங்குமா", "பதிவு"),
    ("அழித்த உரையாடலை மீண்டும் பெற முடியுமா", "மீண்டும்"),
    ("அரட்டையை அழிப்பது எப்படி", "clear"),
    ("உரையாடலை அழிப்பது பாதுகாப்பானதா", "பதிவு"),
    # Tanglish
    ("conversation ah clear pannaa enna aagum", "புதிய"),
    ("chat ah delete panna enna nadakkum", "புதிய"),
    ("chat clear panna applications azhiyumaa", "விண்ணப்ப"),
    ("conversation clear pannina data poidumaa", "பதிவு"),
    ("clear pannitta thirumba kedaikkumaa", "மீண்டும்"),
    ("chat ah eppadi clear pannanum", "clear"),
    ("clear panna enna aagum", "புதிய"),
    ("wipe panna enna nadakkum", "புதிய"),
    ("what happens to the conversation if I delete it", "new"),
    ("does deleting the conversation also delete my applications", "applications"),
    ("what will be lost if I clear the chat", "new"),
    ("explain what clear chat does", "new"),
    ("before I wipe the conversation, will my applications stay", "applications"),
    ("will the applications remain if I purge the chat", "applications"),
    ("என் உரையாடலை நீக்கினால் விண்ணப்பங்கள் போய்விடுமா", "விண்ணப்ப"),
    ("chat delete pannina applications enna aagum", "விண்ணப்ப"),
    ("conversation azhichaa enna aagum", "புதிய"),
    ("wipe pannaa applications poidumaa", "விண்ணப்ப"),
]

# about the REGISTER (or a different noun): must NOT be carried out as a clear, and not answered as one
OTHER = [
    "clear my pending applications", "delete all my applications", "remove row 2", "clear the knowledge base",
    "wipe the database", "erase all records", "delete application 2026/0154/28/001197", "reset the database",
    "what is a conversation", "explain the workflow", "show the history of application 2026/0154/28/001197",
    "history of my field visits", "how many messages have I sent", "எல்லா விண்ணப்பங்களையும் நீக்கு",
    "ennoda applications ellam delete pannu", "clear the overdue flag", "clear applications", "delete the record",
    "clear my overdue applications", "delete the conversation table from the database", "what is the chat history table",
    "remove the chat feature", "delete row 2", "how many chat messages are stored", "clear the sort", "remove the first application",
    "wipe the survey record",
]


class Stub:
    temperature = 0.1
    def bind(self, **k): raise RuntimeError("stub")
    def bind_tools(self, *a, **k): raise RuntimeError("stub")
    async def ainvoke(self, *a, **k):
        class R: content = "[[LLM]]"
        return R()
    async def astream(self, *a, **k):
        class R: content = "[[LLM]]"
        yield R()


def check(ok, label, detail=""):
    if not ok:
        FAILS.append(f"{label}: {detail[:160]}")


async def main():
    rag.llm = Stub()
    for m in CLEAR:
        k = chatbot._contentless_message(m)
        check(k == "clear", f"CLEAR {m!r}", str(k))
    for m, _ in INFO:
        check(chatbot._contentless_message(m) != "clear", f"INFO {m!r} must not be carried out", "")
        check(chatbot._special_scope_kind(m) == "clear_info", f"INFO {m!r} is recognised", str(chatbot._special_scope_kind(m)))
    for m in OTHER:
        check(chatbot._contentless_message(m) != "clear", f"OTHER {m!r} must not clear", "")
        check(chatbot._special_scope_kind(m) != "clear_info", f"OTHER {m!r} is not a clear question", "")
    async with AsyncSessionLocal() as db:
        row = (await db.execute(select(SISOfficer).where(SISOfficer.email == "msivakumar@sis.tn.gov.in"))).scalars().first()
        ctx = await build_officer_context(db, row)
        for setup in (None, "show nisd applications"):
            for m, topic in INFO:
                s = await chatbot.create_chat_session(db, str(row.id)); sid = str(s.id)
                if setup:
                    await chatbot.process_chat(setup, sid, ctx, db, chat_history=[])
                n0 = len((await db.execute(select(ChatMessage.id).where(ChatMessage.session_id == sid))).all())
                r = await chatbot.process_chat(m, sid, ctx, db, chat_history=[])
                t = re.sub(r"\s+", " ", _flatten_html(r.get("response") or ""))
                check(r.get("action") != "clear_chat", f"E2E INFO {m!r} must not clear", t)
                check(topic.lower() in t.lower(), f"E2E INFO {m!r} answers about {topic!r}", t)
                check("[[LLM]]" not in t, f"E2E INFO {m!r} did not reach the model", t)
                ta = bool(re.search(r"[஀-௿]", m)) or bool(re.search(r"\b(?:pann\w*|panr\w*|aagum|nadakkum|azhi\w*|poidum\w*|kedaikk\w*|eppadi)\b", m.lower()))
                if ta:
                    check(bool(re.search(r"[஀-௿]", t)), f"E2E INFO {m!r} answered in Tamil", t)
                else:
                    check(not re.search(r"[஀-௿]", t), f"E2E INFO {m!r} answered in English", t)
        for m in CLEAR[::7]:
            s = await chatbot.create_chat_session(db, str(row.id)); sid = str(s.id)
            await chatbot.process_chat("show nisd applications", sid, ctx, db, chat_history=[])
            r = await chatbot.process_chat(m, sid, ctx, db, chat_history=[])
            check(r.get("action") == "clear_chat", f"E2E CLEAR {m!r}", str(r.get("intent")))
    await engine.dispose()
    print("ALL PASSED" if not FAILS else f"FAILED ({len(FAILS)}):\n  - " + "\n  - ".join(FAILS[:60]))
    return 0 if not FAILS else 1

raise SystemExit(asyncio.run(main()))
