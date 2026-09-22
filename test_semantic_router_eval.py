"""Semantic-router evaluation: greeting vs SIS, mixed and ambiguous messages.

The semantic layer is not the source of truth. Exact deterministic rules decide
first; only a short, digit-free, SIS-vocabulary-free message that no rule claimed
is ever asked. This measures the router as the chatbot actually uses it (the full
pipeline, LLM stubbed), and reports how much each greeting relied on the semantic
layer.

Pass criteria (a failed one exits non-zero):
  - greeting recall >= 0.90 for English / Tanglish / Tamil, each
  - false-greeting rate on SIS and mixed messages == 0
  - mixed messages routed to the expected SIS intent >= 0.90
  - ambiguous messages never answered with register data

python test_semantic_router_eval.py
"""
import asyncio
import collections
import logging
import sys
import uuid

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
logging.getLogger("backend").setLevel(logging.WARNING)

from sqlalchemy import select

from backend.database import AsyncSessionLocal, engine
from backend.models import SISOfficer
from backend.sample_db.build_lora_dataset import build_officer_context
from backend.services import chatbot, rag

GREETING_FAMILY = {"greeting", "small_talk"}
DATA_INTENTS = {"pending_applications", "isd_applications", "nisd_applications", "merge_applications",
                "application_status", "officer_workload", "overdue_applications", "field_visits",
                "survey_detail", "last_application", "fee_summary", "fee_lookup", "followup_list",
                "all_surveys_in_jurisdiction", "workload_by_type"}

GREETING = {
    "en": ["hi", "hello", "hey", "hey there", "hi there", "good morning", "good afternoon", "good evening",
           "hello assistant", "hi bro", "hey buddy whats up", "yo assistant", "howdy", "good day",
           "hello there sir", "how are you", "how are you today", "whats up", "hii", "hey hey",
           "thanks", "thank you", "thanks a lot", "thank u very much", "ok thanks a ton",
           "bye", "bye for now", "see you soon", "catch you later", "good night", "goodbye", "take care"],
    "tanglish": ["vanakkam", "vanakkam anna", "vanakkam sir", "hi da", "nalla irukeengala sir", "eppadi irukeenga",
                 "nandri", "nandri sir", "romba nandri", "poitu varen", "vanakam", "hello anna"],
    "ta": ["வணக்கம்", "காலை வணக்கம்", "மாலை வணக்கம்", "நன்றி", "நன்றி சார்", "இரவு வணக்கம்", "வணக்கம் சார்"],
}
# (message, acceptable intents) -- a greeting plus a real request is the request.
MIXED = [
    ("hi i need 0154 applications", {"isd_applications"}),
    ("hello i need 0154 application", {"isd_applications"}),
    ("hey show 0154 applications in ward 102", {"isd_applications", "pending_applications"}),
    ("vanakkam 0154 pending applications", {"pending_applications", "isd_applications"}),
    ("vanakkam 0153 applications", {"nisd_applications"}),
    ("good morning i need 0153 applications", {"nisd_applications"}),
    ("hi show my pending applications", {"pending_applications"}),
    ("hello show pending", {"pending_applications"}),
    ("hi what is ISD", {"service_code_lookup"}),
    ("hey what is NISD", {"service_code_lookup"}),
    ("hello what does 0154 mean", {"service_code_lookup"}),
    ("vanakkam sir ISD ku field visit venuma", {"service_code_lookup"}),
    ("hi does ISD need a field visit", {"service_code_lookup"}),
    ("good morning show overdue applications", {"overdue_applications"}),
    ("hi status of 2026/0154/28/001197", {"application_status"}),
    ("hello applicant details of 2026/0154/28/001197", {"application_status"}),
    ("thanks show my workload", {"officer_workload"}),
    ("hello what is the fee for ISD", {"fee_lookup"}),
    ("hi how many ISD applications do I have", {"isd_applications"}),
    ("hey applications from CSC", {"pending_applications"}),
    ("hello how many NISD pending", {"nisd_applications"}),
    ("bye show my last application", {"last_application"}),
    ("nandri show pending applications", {"pending_applications"}),
    ("hi ISD na enna", {"service_code_lookup"}),
    ("வணக்கம் எனது நிலுவை விண்ணப்பங்கள்", {"pending_applications"}),
    ("வணக்கம் 0154 என்றால் என்ன", {"service_code_lookup"}),
]
SIS = [
    "show pending applications", "how many applications do I have", "status of 2026/0154/28/001197",
    "what is ISD", "what is service code 0153", "my workload", "show overdue applications",
    "applications from CSC", "how many ISD approved", "which application is oldest",
    "field visits this week", "what is the fee for ISD", "difference between ISD and NISD",
    "show survey 5", "who owns survey 5", "is there litigation on survey 5", "my last application",
    "ISD ku field visit venuma", "NISD na enna", "pending list kaattu", "enna status", "fee evlo",
    "show unscheduled applications", "applications from SRO", "list my wards",
    "show it", "that one", "how long does it take", "is it approved", "who approved it", "total fee",
    "எனது நிலுவை விண்ணப்பங்கள்", "0154 என்றால் என்ன", "விண்ணப்ப நிலை என்ன",
]
AMBIGUOUS = ["hmm", "ok", "?", "what", "tell me something", "the same", "anything else", "one more thing",
             "please", "help", "asdfgh", "give me all data", "predict my workload", "மீண்டும்", "hmm ok"]


class StubLLM:
    temperature = 0.1

    def bind(self, **k): raise RuntimeError("stub")
    def bind_tools(self, *a, **k): raise RuntimeError("stub")

    async def ainvoke(self, *a, **k):
        class R: content = "[[LLM]]"
        return R()


async def route(db, ctx, message):
    """(final intent from the real pipeline, resolved-by: rule | semantic)."""
    rule_intent = rag.parse_intent(message)
    r = await chatbot.process_chat(message, str(uuid.uuid4()), ctx, db, chat_history=[])
    final = r.get("intent")
    by = "semantic" if (final == "greeting" and rule_intent == "general_query"
                        and chatbot._unknown_message_kind(message) is None) else "rule"
    return final, by


def pct(a, b):
    return f"{a}/{b} ({100 * a / b:.0f}%)" if b else "n/a"


async def main():
    real = rag.llm
    rag.llm = StubLLM()
    problems = []
    async with AsyncSessionLocal() as db:
        row = (await db.execute(select(SISOfficer).where(SISOfficer.email == "msivakumar@sis.tn.gov.in"))).scalars().first()
        ctx = await build_officer_context(db, row)

        print("GREETING recall (final intent is greeting / small talk)")
        for lang, msgs in GREETING.items():
            hit, sem, misses = 0, 0, []
            for m in msgs:
                final, by = await route(db, ctx, m)
                if final in GREETING_FAMILY:
                    hit += 1
                    sem += by == "semantic"
                else:
                    misses.append((m, final))
            print(f"  {lang:9} {pct(hit, len(msgs))}   resolved by the semantic layer: {sem}   by rules: {hit - sem}")
            for m, f in misses:
                print(f"      MISS {m!r} -> {f}")
            if hit / len(msgs) < 0.90:
                problems.append(f"greeting recall {lang}")

        print("\nMIXED greeting + request (must be the request, never a greeting)")
        ok = 0
        for m, want in MIXED:
            final, _ = await route(db, ctx, m)
            good = final in want
            ok += good
            if not good:
                print(f"      MISS {m!r} -> {final} (want {sorted(want)})")
            if final in GREETING_FAMILY:
                problems.append(f"mixed hijacked: {m}")
        print(f"  routed to the expected SIS intent: {pct(ok, len(MIXED))}")
        if ok / len(MIXED) < 0.90:
            problems.append("mixed routing")

        print("\nSIS messages (must NOT be a greeting)")
        false_g = []
        for m in SIS:
            final, _ = await route(db, ctx, m)
            if final in GREETING_FAMILY:
                false_g.append((m, final))
        print(f"  false greetings: {len(false_g)}/{len(SIS)}")
        for m, f in false_g:
            print(f"      HIJACK {m!r} -> {f}")
            problems.append(f"false greeting: {m}")

        print("\nAMBIGUOUS messages (must not be answered with register data)")
        unsafe = []
        dist = collections.Counter()
        for m in AMBIGUOUS:
            final, _ = await route(db, ctx, m)
            dist[final] += 1
            if final in DATA_INTENTS:
                unsafe.append((m, final))
        print(f"  outcomes: {dict(dist)}")
        for m, f in unsafe:
            print(f"      UNSAFE {m!r} -> {f}")
            problems.append(f"ambiguous answered with data: {m}")
    rag.llm = real
    await engine.dispose()
    print("\n" + ("ROUTER EVAL PASSED" if not problems else f"ROUTER EVAL FAILED ({len(problems)}):\n  - " + "\n  - ".join(problems)))
    return 0 if not problems else 1


raise SystemExit(asyncio.run(main()))
