""""How do you say these are from SRO?" -- the question about HOW a channel was decided.

Asked in any wording, with any spelling slip, and in any context (an SRO list on screen, a CSC list,
nothing at all), it is answered with the rule, plus -- when a list is on screen -- what the register
records for exactly those rows. It must never come back as a listing of applications.

    python test_channel_basis_questions.py            # DB, no LLM
"""
import asyncio, random, re, sys, uuid
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from sqlalchemy import select
from backend.database import AsyncSessionLocal
from backend.models import SISOfficer
from backend.services import rag
from backend.services.chatbot import process_chat
from test_followup_context import officer_context
from test_typo_followups import Stub, damage

fails = []
plain = lambda h: re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", h or ""))
NUM = re.compile(r"\d{4}/\d{4}/\d+/\d+")
def basis(t):
    """A plain-language 'how the channel was decided' answer: no internal names anywhere in it."""
    internal = any(w in t.lower() for w in ("source_name", "camp_flag", "log record", "database", "table", "column"))
    return (not internal) and ("reached the office" in t or "typed it in" in t or "revenue camp" in t
                               or "பதிவு குறிப்பு எண்" in t or "வந்த விதத்தை" in t)
PHRASES = ["how do you say these applications are from sro", "how do you know these are from sro",
           "how did you decide these are sro", "why are these sro applications", "what makes these sro",
           "proof these are from sro", "how is it sro", "how do you say it is from sro",
           "on what basis are these csc", "how do you determine the channel", "how do you identify sro applications",
           "how can you be sure these are from sro", "what is the basis for calling these csc"]
CTX = {"an SRO list": "show my sro applications", "a CSC list": "show my csc applications", "nothing": None}
NEGATIVE = [("show my sro applications", None), ("applications from csc", None), ("how many applications are from sro", None),
            ("list sro applications", None)]


def check(ok, label, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"\n        {detail}" if not ok and detail else ""))
    if not ok:
        fails.append(label)


async def main():
    rag.llm = Stub()
    rnd = random.Random(3)
    async with AsyncSessionLocal() as db:
        o = (await db.execute(select(SISOfficer).where(SISOfficer.email.like("csen%")))).scalars().first()
        off = await officer_context(db, o)

        async def ask(ctx_q, q):
            sid, hist = str(uuid.uuid4()), []
            if ctx_q:
                r = await process_chat(ctx_q, sid, off, db, [])
                hist = [{"role": "user", "content": ctx_q}, {"role": "assistant", "content": plain(r.get("response"))}]
            r = await process_chat(q, sid, off, db, hist)
            return r.get("intent"), plain(r.get("response"))

        print("── every wording, every context")
        for name, ctx_q in CTX.items():
            bad = [q for q in PHRASES if not basis((await ask(ctx_q, q))[1])]
            check(not bad, f"{len(PHRASES)} wordings after {name}", str(bad[:4]))
        print("── the evidence for the rows on screen is the register's, not a guess")
        listed = len(set(NUM.findall((await asyncio.gather(process_chat("show my sro applications", str(uuid.uuid4()), off, db, [])))[0]["response"])))
        i, t = await ask("show my sro applications", "how do you say these applications are from sro")
        check(f"all {listed} are recorded as Sub-Registrar files" in t and "all of them carry a registration reference number" in t, "SRO list: counts and reference numbers from the rows", t[-200:])
        check("camp" not in t.lower() and "CSC counter typed" not in t, "asked about SRO only: nothing about the other channels' rules", t[:200])
        i, t = await ask(None, "how do you say these are from sro")
        check("you are looking at" not in t, "no list on screen: the rule alone, no invented evidence")
        print("── with a spelling slip in every word")
        total = bad_n = 0
        for ctx_q in ("show my sro applications", None):
            for canon in PHRASES[:8]:
                words = canon.split()
                for wi, w in enumerate(words):
                    for kind, typo in damage(w.lower(), rnd):
                        q = " ".join(words[:wi] + [typo] + words[wi + 1:])
                        total += 1
                        if not basis((await ask(ctx_q, q))[1]):
                            bad_n += 1
                            print(f"        missed [{kind}] {q!r}")
        check(bad_n == 0, f"{total - bad_n}/{total} misspelled versions answered")
        for q in ["how do u sAY these applicatins aref from sro", "hw do yu knw thes are from sro", "how do you sya these r from sro",
                  "ivai sro nu eppadi solreenga", "இவை SRO என்று எப்படி சொல்கிறீர்கள்"]:
            check(basis((await ask("show my sro applications", q))[1]),
                  f"{q!r}")
        print("── a listing is still a listing")
        for q, c in NEGATIVE:
            i, t = await ask(c, q)
            check(not basis(t), f"{q!r} is not read as a question about the rule", t[:80])

        print("── one application: the same question, plain answer, no internal names")
        o2 = (await db.execute(select(SISOfficer).where(SISOfficer.email.like("msiva%")))).scalars().first()
        off2 = await officer_context(db, o2)
        A = "2022/0154/28/000156"      # a Sub-Registrar referral in this officer's ward
        singles = [f"how do you know {A} is from sro", f"how do you know {A} is sro", f"why is {A} sro",
                   f"what makes {A} an sro application", f"proof {A} is from sro", f"how can you be sure {A} is from sro",
                   f"how did you decide the channel of {A}", f"how do u sAY {A} is from sro", f"hw do yu knw {A} is frm sro"]
        for q in singles:
            r = await process_chat(q, str(uuid.uuid4()), off2, db, [])
            t = plain(r.get("response"))
            internal = any(w in t.lower() for w in ("source_name", "camp_flag", "urban_application_log", "column"))
            check(r.get("intent") == "submission_channel_check" and "Sub-Registrar" in t and not internal
                  and "camp" not in t.lower(), f"{q!r}", f"{r.get('intent')}: {t[:120]}")


asyncio.run(main())
print("\nFAILED:", fails if fails else "none")
sys.exit(1 if fails else 0)
