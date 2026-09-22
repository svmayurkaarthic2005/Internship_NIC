"""No evidence, no factual LLM answer.

An unrouted message that carries no domain evidence must get a clarification --
never an improvised answer from the agent / plain-prompt fallback. Real
questions must still reach their pipelines.

    python test_no_evidence_guard.py            # classification + end-to-end (DB, no LLM)
    python test_no_evidence_guard.py --routing  # classification only
"""
import asyncio, re, sys, time, uuid
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from backend.services.chatbot import (_has_domain_evidence, _no_evidence_for_llm,
                                      _clarification_reply, _contentless_message)
from backend.services.rag import parse_intent

fails = []


def check(ok, label, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"\n        {detail}" if not ok and detail else ""))
    if not ok:
        fails.append(label)


NO_EVIDENCE = ["asdfgh", "qwerty", "xyzxyz", "blah blah", "whatxyz", "random words",
               "what do you mean", "hmm ok then whatever", "lkjhg mnbvc", "ஙஙஙஙஙஙஙங ஞஞஞஞஞஞ"]
EVIDENCE = ["what is ISD", "how many ISD applications", "0154 applications", "survey number 1355",
            "what documents are needed for ISD", "hi i need 0154 applications",
            "2022/0154/28/000156", "aplication status", "5/4A", "ward 102 pendng",
            "விண்ணப்ப நிலை என்ன", "which is oldest", "sla for nisd"]

print("── the gate (unrouted message)")
for q in NO_EVIDENCE:
    check(_no_evidence_for_llm(q, "general_query", None, q), f"no evidence: {q!r}")
for q in EVIDENCE:
    ev = _has_domain_evidence(q)
    # "which is oldest" is a follow-up: no evidence of its own, resolved earlier
    check(ev or q == "which is oldest", f"evidence: {q!r}")
check(not _no_evidence_for_llm("asdfgh", "pending_applications", None), "a routed intent is never gated")
check(not _no_evidence_for_llm("asdfgh", "general_query", {"applications": [1]}), "fetched data is evidence")

print("── the dismissal classifier is kept")
for q in ["poda", "get lost"]:
    check(_contentless_message(q) == "dismiss", f"dismiss: {q!r}")

from types import SimpleNamespace
CTX = SimpleNamespace(application_numbers=["2022/0154/28/000156"], entity="application")

print("── evidence hierarchy: the tier that justified it")
from backend.services.chatbot import _domain_evidence_tier as tier
for q, want in [("2022/0154/28/000156", "entity"), ("5/4A", "entity"), ("which ward", "vocabulary"),
                ("what is a DSC", "vocabulary"), ("sla for nisd", "vocabulary"),
                ("aplication status", "vocabulary"), ("asdfgh", None)]:
    check(tier(q) == want, f"tier({q!r}) == {want}", str(tier(q)))

print("── boundary: unknown wording + a live context is a follow-up, not gibberish")
for q in ["how long will this type take?", "இதற்கு எவ்வளவு நாள் ஆகும்?", "idhuku evlo naal aagum",
          "hw lng wil this tipe tak", "why was it rejected"]:
    check(not _no_evidence_for_llm(q, "general_query", None, "", CTX), f"context + {q!r} is not clarified")
    check(_no_evidence_for_llm(q, "general_query", None, "", None) or _has_domain_evidence(q),
          f"(no context) {q!r} is judged on its own words")
for q in ["asdfgh", "blah blah", "poda"]:
    check(_no_evidence_for_llm(q, "general_query", None, "", CTX), f"a live context does not excuse {q!r}")

print("── invariant: the gate never blocks a recognised SIS intent")
for q in ["pendng aplications", "show my field visits", "how many ISD applications", "what is 0153",
          "compare ward 102 and ward 103", "do any of my applications share the same IP?",
          "ISD விண்ணப்பங்கள்", "nisd applications kaatu"]:
    it = parse_intent(q)
    check(it != "general_query" and not _no_evidence_for_llm(q, it, None, q, None),
          f"routed ({it}) is never gated: {q!r}")

if "--routing" not in sys.argv:
    from sqlalchemy import select
    from backend.database import AsyncSessionLocal
    from backend.models import SISOfficer
    from backend.services.chatbot import process_chat
    from test_followup_context import officer_context

    async def run():
        async with AsyncSessionLocal() as db:
            o = (await db.execute(select(SISOfficer).where(SISOfficer.email.like("msiva%")))).scalars().first()
            off = await officer_context(db, o)

            async def ask(q):
                t0 = time.time()
                r = await process_chat(q, str(uuid.uuid4()), off, db, [])
                return re.sub(r"<[^>]+>", " ", r.get("response") or ""), time.time() - t0

            print("── end to end: clarification, no LLM")
            for q in NO_EVIDENCE[:6]:
                t, dt = await ask(q)
                check(t.strip().startswith(("I'm not sure what you mean", "I could not understand")) and dt < 8, f"clarified: {q!r}", f"{dt:.1f}s {t[:100]}")
            print("── end to end: Tamil / Tanglish duration follow-up after an application")
            for q in ["இதற்கு எவ்வளவு நாள் ஆகும்?", "idhuku evlo naal aagum"]:
                sid, hist = str(uuid.uuid4()), []
                for turn in ["show application 2022/0154/28/000156", q]:
                    r = await process_chat(turn, sid, off, db, list(hist))
                    t = re.sub(r"<[^>]+>", " ", r.get("response") or "")
                    hist += [{"role": "user", "content": turn}, {"role": "assistant", "content": t}]
                check("2022/0154/28/000156" in t and "94" in t and "not sure" not in t,
                      f"resolved from context, decided file takes 94 days: {q!r}", t[:120])
            print("── end to end: real questions are not clarified")
            for q in ["what is ISD", "how many ISD applications do I have", "0154 applications", "survey number 5"]:
                t, dt = await ask(q)
                check(t.strip() != _clarification_reply("en") and len(t.strip()) > 0, f"answered: {q!r}", t[:100])
    asyncio.run(run())

print("── routing of the negatives (must not fall to general_query where a handler exists)")
for q in ["how many ISD applications", "0154 applications", "what is ISD"]:
    check(parse_intent(q) != "general_query", f"routes: {q!r} -> {parse_intent(q)}")

print("\nFAILED:", fails if fails else "none")
sys.exit(1 if fails else 0)
