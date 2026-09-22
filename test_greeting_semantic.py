"""Conversational messages: semantic detection + LLM-written replies.

python test_greeting_semantic.py           # classifier + gates + fallback + live LLM turns
python test_greeting_semantic.py --fast    # skips the turns that call the LLM

Needs Ollama (nomic-embed-text) and the database; the LLM turns also need the chat model.
"""
import asyncio
import logging
import re
import sys
import uuid

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
logging.getLogger("backend").setLevel(logging.WARNING)

from sqlalchemy import select

from backend.database import AsyncSessionLocal, engine
from backend.models import SISOfficer
from backend.sample_db.build_lora_dataset import build_officer_context, _flatten_html
from backend.services import chatbot, rag, semantic_intent as si

FAST = "--fast" in sys.argv
TAMIL = re.compile(r"[஀-௿]")
FAILS = []


def check(ok, label, detail=""):
    print(("  PASS  " if ok else "  FAIL  ") + label + (f"   {detail}" if detail and not ok else ""))
    if not ok:
        FAILS.append(label)


CONVERSATIONAL = [
    "hey buddy whats up", "yo assistant", "good day", "hello there sir", "hi bro",
    "how are you today", "nalla irukeengala sir", "thank u very much", "ok thanks a ton",
    "nandri sir", "see you soon", "bye for now", "catch you later", "hii", "howdy",
]
WORK = [
    "show pending", "ISD ku field visit venuma", "what is the fee", "fee for ISD",
    "any overdue", "which one is oldest", "status please", "enna status", "details of it",
    "show my files", "how many files", "field visit when", "open applications",
    "how long does it take", "is it approved", "explain merge", "who approved it", "total fee",
]


async def classifier():
    print("1. semantic classifier (embeddings)")
    for m in CONVERSATIONAL:
        k = await si.classify(m)
        check(k is not None, f"conversational: {m!r}", f"got {k}")
    for m in WORK:
        k = await si.classify(m)
        check(k is None, f"work, not chit-chat: {m!r}", f"got {k}")


async def gates():
    print("2. gates (no embedding is even attempted)")
    check(not si.looks_short_and_plain("hello 2026/0154/28/001280", False), "digits -> not asked")
    check(not si.looks_short_and_plain("vanakkam", True), "SIS vocabulary -> not asked")
    check(not si.looks_short_and_plain("வணக்கம்", False), "Tamil script -> keyword rules only")
    check(not si.looks_short_and_plain("one two three four five six seven eight nine ten", False),
          "long message -> not asked")
    check(si.looks_short_and_plain("hey buddy", False), "short plain message -> asked")


def reply_guard():
    print("3. generated-reply guard")
    ok = chatbot._greeting_reply_ok
    check(ok("Hello! How can I help with your applications today?"), "plain reply accepted")
    check(not ok("You have 5 pending applications."), "digit -> rejected")
    check(not ok("Application 2026/0154/28/001280 is pending"), "application number -> rejected")
    check(not ok("see https://example.com"), "link -> rejected")
    check(not ok(""), "empty -> rejected")
    check(not ok("x" * 500), "over-long -> rejected")
    check(chatbot._greeting_kind("bye then") == "farewell", "kind: farewell")
    check(chatbot._greeting_kind("thanks a lot") == "thanks", "kind: thanks")
    check(chatbot._greeting_kind("good morning") == "morning", "kind: morning")
    check(chatbot._greeting_kind("hey", "smalltalk") == "smalltalk", "kind: semantic hint used")


async def turn(db, ctx, message, history=None):
    return await chatbot.process_chat(message, str(uuid.uuid4()), ctx, db, chat_history=history or [])


async def fallback(db, ctx):
    print("4. LLM down -> the fixed greeting is used")
    real = rag.llm

    class Down:
        def bind(self, **kw):
            raise RuntimeError("model unavailable")

    rag.llm = Down()
    try:
        r = await turn(db, ctx, "hello")
    finally:
        rag.llm = real
    text = _flatten_html(r.get("response") or "")
    check(r.get("intent") == "greeting" and "SIS" in text, "fixed greeting on LLM failure", text[:80])


async def live(db, ctx):
    print("5. live turns (LLM writes the reply)")
    cases = [
        ("hello", "en"), ("hey buddy whats up", "en"), ("thanks a lot", "en"),
        ("bye for now", "en"), ("vanakkam", "ta"), ("nandri", "ta"),
    ]
    for msg, lang in cases:
        r = await turn(db, ctx, msg)
        text = _flatten_html(r.get("response") or "")
        good = r.get("intent") == "greeting" and bool(text) and not re.search(r"\d", text)
        if lang == "ta":
            good = good and bool(TAMIL.search(text))
        else:
            good = good and not TAMIL.search(text)
        check(good, f"{msg!r} -> greeting in {lang}", f"{r.get('intent')}: {text[:100]}")
    print("6. work messages are not hijacked")
    for msg, want in [("ISD ku field visit venuma?", "service_code_lookup"),
                      ("show pending applications", "pending_applications"),
                      ("what is the fee for ISD?", "fee_lookup"),
                      ("how long does it take", None)]:
        r = await turn(db, ctx, msg)
        got = r.get("intent")
        check(got != "greeting" and (want is None or got == want), f"{msg!r} stays {want or 'non-greeting'}", f"got {got}")


MIXED = [
    ("hi i need 0154 application", "isd_applications"),
    ("hello i need 0154 application", "isd_applications"),
    ("hey show 0154 applications in ward 10", "isd_applications"),
    ("vanakkam 0154 pending applications", "pending_applications"),
    ("hi what is ISD", "service_code_lookup"),
    ("hi, show my pending applications", "pending_applications"),
    ("good morning i need 0153 applications", "nisd_applications"),
    ("vanakkam sir ISD ku field visit venuma", "service_code_lookup"),
]


def mixed_routing():
    print("7. a greeting is a modifier: greeting + request routes to the request")
    for msg, want in MIXED:
        got = rag.parse_intent(msg)
        check(got == want, f"{msg!r} -> {want}", f"got {got}")


async def mixed_turn(db, ctx):
    print("8. greeting + request: the reply carries only real records")
    from backend.models import Application
    known = {r[0] for r in (await db.execute(select(Application.application_number))).all()}
    for msg in ("hi i need 0154 application", "vanakkam 0154 pending applications"):
        r = await turn(db, ctx, msg)
        text = _flatten_html(r.get("response") or "")
        nums = set(re.findall(r"\d{4}/\d{4}/\d{2}/\d{6}", text))
        check(r.get("intent") != "greeting" and nums and nums <= known,
              f"{msg!r}: not a greeting, {len(nums)} application number(s), all in the register",
              f"intent={r.get('intent')} unknown={sorted(nums - known)[:3]}")


async def main():
    await classifier()
    await gates()
    reply_guard()
    mixed_routing()
    async with AsyncSessionLocal() as db:
        row = (await db.execute(select(SISOfficer).where(SISOfficer.email == "msivakumar@sis.tn.gov.in"))).scalars().first()
        ctx = await build_officer_context(db, row)
    async with AsyncSessionLocal() as db:
        await fallback(db, ctx)
        if not FAST:
            await live(db, ctx)
        await mixed_turn(db, ctx)
    await engine.dispose()
    print("\n" + ("ALL PASSED" if not FAILS else f"FAILED ({len(FAILS)}):\n  - " + "\n  - ".join(FAILS)))
    return 0 if not FAILS else 1


raise SystemExit(asyncio.run(main()))
