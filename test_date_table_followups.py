"""Date phrases and table follow-ups (sort, columns, filters) after a listing.

Each chain is a conversation; every step names a substring the answer must
carry and one it must not. No LLM (stubbed).

python test_date_table_followups.py            # process_chat
python test_date_table_followups.py --stream   # process_chat_stream (same chains)
"""
import json
import asyncio
import logging
import re
import sys
import uuid
from datetime import date

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
logging.disable(logging.CRITICAL)

from sqlalchemy import select

from backend.database import AsyncSessionLocal, engine
from backend.models import SISOfficer
from backend.sample_db.build_lora_dataset import build_officer_context, _flatten_html
from backend.services import chatbot, rag
from backend.services.rag import extract_date_range

FAILS = []


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
        FAILS.append(f"{label}: {detail[:170]}")


from datetime import timedelta as _td
_T = date.today()
_LW0 = _T - _td(days=_T.weekday() + 7)
_LAST_WEEK = f"{_LW0.isoformat()} to {(_LW0 + _td(days=6)).isoformat()}"
_THIS_MONTH = _T.strftime("%B %Y").lower()
_LM = (_T.replace(day=1) - _td(days=1))
_LAST_MONTH = _LM.strftime("%B %Y").lower()

# (message, must contain (lowercase) or None, must NOT contain or None)
CHAINS = [
    [("show applications in june 2019", "june 2019", None),
     ("only isd", "isd applications in june 2019", "field visit"),
     ("sort by date", "nothing to change", "specify the application"),
     ("newest first", "nothing to change", "hello")],
    [("show applications this month", _THIS_MONTH, None),
     ("how many", None, None),
     ("last month", _LAST_MONTH, "[[llm]]"),
     ("and last week", _LAST_WEEK, "[[llm]]")],
    [("show isd applications", "found", None),
     ("sort by date descending", "newest first", "specify the application"),
     ("sotr by date", "sorted by submission date", "specify the application"),
     ("oldest first", "oldest first", "the oldest of"),
     ("only the date and status", "submission date", None),
     ("add ward column", "ward", "read the records"),
     ("remove ward column", None, "read the records")],
    [("show nisd applications", "found", None),
     ("sort by status", "sorted by status", None),
     ("date mattum kaatu", "சமர்ப்பித்த தேதி", None),
     ("தேதி மட்டும் காட்டு", "சமர்ப்பித்த தேதி", None)],
    [("show pending applications", "found", None),
     ("after that", None, "hello")],
    # stale state / hallucination / unnecessary answers
    [("show isd applications", "found", None),
     ("and merge", None, None),
     ("back to isd", "found", "service code")],
    [("show csc applications", "found", None),
     ("exclude rejected", "not rejected", "which one do you mean"),
     ("only citizen", "citizen", "[[llm]]")],
    [("show pending applications", "found", None), ("reject it", "cannot change", "no rejection")],
    [("ok", "ask me", None), ("yes", "what would you like to know", "[[llm]]"), ("no", "no problem", "hello")],
    [("compare me with other officers", "own jurisdiction", "[[llm]]"),
     ("applications on 31 february 2026", "not a valid date", None),
     ("next year applications", "2027", None),
     ("how many applications will be approved next month", "predict", None)],
    [("ennoda applications ellam delete pannu", "என்னால்", "[[llm]]"),
     ("எல்லா விண்ணப்பங்களையும் நீக்கு", "என்னால்", "உரையாடல் அழிக்கப்பட்டது"),
     ("எனது விண்ணப்பங்கள்", "விண்ணப்பங்கள் கிடைத்தன", "[[llm]]")],
    [("survey 5 owners", "owners for survey", None),
     ("what is the father name of the owner of survey 5", "owners for survey", "service code")],
    [("how many applications did I approve in 2024", "2024", None),
     ("in june", "june", None),
     ("what about rejected", "rejected", "approved")],
    [("what is isd", "meaning", "[[llm]]"),
     ("what is nisd", "not involving", None),
     ("explain workflow process", "depends on the application type", "[[llm]]"),
     ("what is fmb", "not part of this register", "[[llm]]"),
     ("what is the area", "which application or list", "[[llm]]")],
    [("show pending applications", "found", None),
     ("what is nisd", "meaning", "no applications found"),
     ("what is 0153", "meaning", "found")],
    [("what is the district code of thoothukudi", "28", "[[llm]]"),
     ("what is the district code", "28", "specify the application"),
     ("what are the district codes", "thoothukudi 28", "[[llm]]"),
     ("what is the taluk code of thoothukudi", "kovilpatti", "[[llm]]"),
     ("taluk code", "kovilpatti", "[[llm]]"),
     ("how many days does isd take", "30-35", "specify the application"),
     ("isd sla", "30-35", "[[llm]]"),
     ("what is the sla", "sla", "[[llm]]"),
     ("what is the last date to apply", "not in the sis register", "specify the application")],
    # positional requests: a row named with its own subject is a fresh question
    [("display 2nd row of nisd applications", "2nd of", "found 28"),
     ("display 100th row of nisd applications", "no 100th application", None),
     ("show the 2nd and 3rd nisd applications", "rows 2, 3", None),
     ("show even rows of nisd applications", "even rows", None)],
    [("the 5th row", "which application or list", "[[llm]]")],
    [("show isd applications", "found", None),
     ("display 2nd row of nisd applications", "nisd", "details for"),
     ("show 3rd row of isd applications", "3rd of", "details for")],
    [("show pending applications", "found", None),
     ("display 2nd row of nisd applications", "2nd of", "listed only")],
    [("show nisd applications", "found", None),
     ("the 2nd one", "details for", None),
     ("and the next one", "details for", "list or application"),
     ("previous one", "details for", "list or application"),
     ("the last one", "details for", "in view"),
     ("the first one", "details for", "in view")],
    [("what is your role", "sis", "specify the application"),
     ("what is your name", "sis ai assistant", "applicant"),
     ("who made you", "developed me", "hello"),
     ("are you chatgpt", "language model", "[[llm]]"),
     ("which model are you", "language model", "hello"),
     ("what is the time now", "server time", "[[llm]]"),
     ("what is 5 times 6", "sis assistant", "[[llm]]")],
    [("show nisd applications", "found", None),
     ("what is your role", "sis", "details for"),
     ("what is your name", "sis ai assistant", "applicant name")],
    [("nisd table", "found", "[[llm]]"),
     ("dispaly secnd applicaton in nisd tabel", "2nd of", "[[llm]]"),
     ("display table", "application no", None)],
    [("applications without igrs number", "igrs", "which application or list")],
    [("who is this", "sis assistant", "which application or list")],
    # long messages, several requests in one, praise / complaint, Tamil headers, slips
    [("Good afternoon sir, I hope you are doing well. I have been working on the survey department files since morning and there is a lot going on in my ward today, people are calling continuously about their patta transfers and some of them are very worried about the delay, so before I go for lunch and then the field visits I just want to quickly check one small thing because my supervisor asked me about it and I could not answer, so please show me my pending applications",
      "found", "compare")],
    [("show pending applications and how many isd applications", "1. show pending applications", None)],
    [("what is isd and what is nisd", "1. what is isd", None)],
    [("compare isd and nisd", "isd", "1. compare")],
    [("pending applications kaattu and isd evlo iruku", "1. pending applications kaattu", None)],
    [("good job", "glad it helps", "hello"), ("romba nalla irukku", "நன்றி", "hello"), ("good morning", "good morning", None)],
    [("you are useless", "check it against the register", "[[llm]]"), ("nee waste", "check it against the register", "[[llm]]")],
    [("withwiky", "could not understand", "[[llm]]"), ("hmmm okk", "ask me", "[[llm]]")],
    [("what llm model is used", "language model", "not specified")],
    [("எனது நிலுவை விண்ணப்பங்கள்", "விண்ணப்ப எண் | வகை", "application no.")],
    [("elam application kami", "விண்ணப்ப", "pending applications"), ("ellam applications kaattu", "விண்ணப்ப", "pending applications")],
    [("show all applications", "found", None), ("elam application kami", "விண்ணப்ப", "pending applications")],
    [("what time does the office open", "not in the sis register", "[[llm]]"),
     ("what is the stamp duty", "not in the sis register", "[[llm]]"),
     ("அலுவலக நேரம் என்ன", "என்னிடம் இல்லை", "[[llm]]")],
    [("what is the price of isd", "recorded", "[[llm]]"),
     ("isd cost evlo", "₹", "[[llm]]"),
     ("what is the price", "recorded", "[[llm]]"),
     ("ISD விலை என்ன", "பதிவேட்டின்படி", "[[llm]]")],
    [("status of 2026/0154/28/001197", "pending", None),
     ("how many pending", "pending application", "field visit"),
     ("how long has it been pending", "days", "[[llm]]")],
    # opinions of a person are declined, not read as "I do not understand"
    [("what do u think abt me", "don't form opinions", "not sure what you mean")],
    [("what do i think abt u?", "don't form opinions", "not sure what you mean")],
    [("what do you think about me", "don't form opinions", "You are")],
    [("nee ennai pathi enna ninaikira", "கருத்தோ உணர்வோ", "[[llm]]")],
    # an unrecognised source is asked about, never ignored (which answered with the whole desk queue)
    [("display applications ftom sri", "Did you mean Sub-Registrar", "Found")],
    [("show applications from sri", "don't recognise", "Found")],
    [("list aplications frm xyz", "don't recognise", "Found")],
    [("display applications ftom csc", "Found", "don't recognise")],
    # a scope with a verb and no noun is a list request; a year after the last month of a span covers both months
    [("display between jan and feb 2025", "January–February 2025", "[[llm]]")],
    [("display from jan to feb 2025", "January–February 2025", "[[llm]]")],
    [("show between march and may 2023", "March–May 2023", "[[llm]]")],
    [("display 2023", "Found", "[[llm]]")],
    [("display rejected", "Found", "[[llm]]")],
    [("how many applications in jan and feb 2025", "January–February 2025", "Applications filed")],
    [("how many applications", "in total", "[[llm]]"), ("between jan and feb 2025", "January–February 2025", "January 2026")],
    [("jan - mar 2023", "Found", "[[llm]]")],
    # "define <term>" is a definition -- never a fee column / a list / the LLM
    [("define fee", "Fee = what an applicant pays", "Found")],
    [("show my NISD applications", "Found", None), ("define fee", "Fee = what an applicant pays", "Application No.")],
    [("what is fee", "Fee = what an applicant pays", "Found")],
    [("fee meaning", "Fee = what an applicant pays", "Found")],
    [("fee endral enna", "கட்டணம் =", "Found")],
    [("கட்டணம் என்றால் என்ன", "கட்டணம் =", "பதிவேட்டின்படி")],
    [("define overdue", "15 working days", "Found")],
    [("define pending", "waiting for the officer", "Found")],
    [("define approved", "Approved (also called completed)", "[[llm]]")],
    [("define sla", "service time limit", "[[llm]]")],
    [("define CAN", "Citizen Access Number", "[[llm]]")],
    [("define IGRS", "IGRS = Inspector General of Registration and Stamps", "[[llm]]")],
    [("define field visit", "on-site verification", "[[llm]]")],
    [("define encroachment", "Encroachment =", "[[llm]]")],
    [("define litigation", "Litigation =", "[[llm]]")],
    [("define channel", "Channel = how an application reached", "[[llm]]")],
    [("define jurisdiction", "Jurisdiction =", "[[llm]]")],
    [("define survey number", "A survey number identifies", "Please specify")],
    [("define ward", "Ward = a division", "Please specify")],
    [("what is the fee for ISD", "ISD", "Fee = what an applicant pays")],
    # which service needs a field visit is a rule -- never the officer's own visit table, in any context
    [("which service does SIS need to field visit", "ISD (0154, Involving Sub-Division) and MERGE (0155)", "Field Visits Summary")],
    [("how many field visits do I have", "Field Visits Summary", None), ("which service does SIS need to field visit", "ISD (0154, Involving Sub-Division) and MERGE (0155)", "Field Visits Summary")],
    [("show ISD applications", "Found", None), ("which service needs field visit", "ISD (0154, Involving Sub-Division) and MERGE (0155)", "Found")],
    [("is field visit needed for ISD", "ISD (0154, Involving Sub-Division) and MERGE (0155)", "Field Visits Summary")],
    [("do all applications need field visit", "NISD (0153, Not Involving Sub-Division) needs no field visit", "Field Visits Summary")],
    [("which services do not need field visit", "NISD (0153, Not Involving Sub-Division) does not need a field visit", "are needed for ISD")],
    [("evlo service ku field visit venum", "கள ஆய்வு தேவை", "Field Visits Summary")],
    [("எந்த சேவைக்கு கள ஆய்வு தேவை", "NISD (0153) க்கு கள ஆய்வு தேவையில்லை", "Field Visits Summary")],
    # applications that are not in the register cannot be listed -- never a list of the ones that are
    [("what application not present in sis", "I can only list applications that are recorded in your SIS register", "Found")],
    [("show applications", "Found", None), ("what application not present in sis", "I can only list applications that are recorded", "Found")],
    [("which applications are not in sis", "I can only list applications that are recorded", "Found")],
    [("what applications are missing", "I can only list applications that are recorded", "[[llm]]")],
    [("which application is not in the register", "I can only list applications that are recorded", "[[llm]]")],
    [("what is not present", "I can only list applications that are recorded", "Found")],
    [("applications not at SIS stage", "are no longer at the SIS desk", "Found")],
    [("applications not in my jurisdiction", "not visible to me", "Found")],
    # IGRS is the Inspector General of Registration and Stamps -- never a made-up "Indian Registration Society"
    [("what is IGRS", "Inspector General of Registration and Stamps", "Indian Registration Society")],
    [("what is IGRS", "Inspector General of Registration and Stamps", "Indian"), ("full form", "IGRS = Inspector General of Registration and Stamps.", "the department")],
    [("what is IGRS", "Inspector General of Registration and Stamps", "Indian"), ("its full form", "IGRS = Inspector General of Registration and Stamps", "[[llm]]")],
    [("igrs full form", "IGRS = Inspector General of Registration and Stamps", "Indian")],
    [("full form of IGRS", "IGRS = Inspector General of Registration and Stamps.", "the department")],
    [("IGRS என்றால் என்ன", "Inspector General of Registration and Stamps", "Indian")],
    [("what is SRO", "Sub-Registrar Office", None), ("full form", "SRO = Sub-Registrar Office", "[[llm]]")],
    [("what is CSC", "Common Service Centre", None), ("full form", "CSC = Common Service Centre", "[[llm]]")],
    [("define CAN", "Citizen Access Number", None), ("full form", "CAN = Citizen Access Number", "[[llm]]")],
    [("what is ISD", "ISD", None), ("full form", "ISD = Involving Sub-Division", "[[llm]]")],
    [("full form of xyz", "will not guess", "[[llm]]")],
    [("full form", "The full form of which term", "[[llm]]")],
    [("what is IGRS number", "Only Sub-Registrar (SRO) referrals carry an IGRS Form 6 number", "Indian")],
    # a negated SRO question is about what is NOT the Sub-Registrar, not a definition of SRO
    [("what is not sro", "CSC counter", "Sub-Registrar Office, where")],
    [("what is non sro", "revenue camp", "Sub-Registrar Office, where")],
    [("what does it mean if not sro", "CSC counter", "Sub-Registrar Office, where")],
    [("SRO illana enna", "CSC கவுண்டர்", "Sub-Registrar Office")],
    [("what is sro", "Sub-Registrar Office, where", "CSC counter, or by a citizen")],
    # a word nothing can filter by is named, never ignored
    [("show applications in xyz", "don't know how to filter", "Found")],
    [("display sri applications", "don't know how to filter", "Found")],
    [("how many applications from sri", "don't know how to filter", "There is")],
    [("show urgent applications", "don't know how to filter", "Found")],
    [("show high value applications", "don't know how to filter", "Found")],
    [("sri la irunthu applications kaattu", "வடிகட்ட எனக்குத் தெரியவில்லை", "கிடைத்தன")],
    [("show my aplications from june", "Found", "don't know how")],
    [("how many isd aplicatons are pendng", "ISD", "don't know how")],
    [("show approvd applications", "Found", "don't know how")],
    [("show applications from june", "Found", "don't recognise")],
    [("display applications from sro", "Found", "don't recognise")],
]


STREAM = "--stream" in sys.argv


async def turn(db, ctx, sid, msg, hist):
    """One answer's text via the selected entry point."""
    if not STREAM:
        r = await chatbot.process_chat(msg, sid, ctx, db, chat_history=hist)
        return (r.get("response") or "") + " " + json.dumps(r.get("table_data") or "", ensure_ascii=False)
    parts = []
    async for chunk in chatbot.process_chat_stream(msg, sid, ctx, db, chat_history=hist):
        for line in chunk.decode("utf-8", "replace").splitlines():
            if line.startswith("data:"):
                try:
                    ev = json.loads(line[5:].strip())
                except ValueError:
                    continue
                if isinstance(ev, dict):
                    if ev.get("content"):
                        parts.append(str(ev["content"]))
                    if ev.get("table_data"):
                        parts.append(json.dumps(ev["table_data"], ensure_ascii=False))
    return " ".join(parts)


async def run(db, ctx, chain, label):
    sid = str(uuid.uuid4())
    hist = []
    for msg, want, bad in chain:
        raw = await turn(db, ctx, sid, msg, hist)
        t = re.sub(r"\s+", " ", _flatten_html(raw)).lower()
        hist += [{"role": "user", "content": msg}, {"role": "assistant", "content": raw}]
        if want:
            check(want.lower() in t, f"{label} {msg!r} carries {want!r}", t)
        if bad:
            check(bad.lower() not in t, f"{label} {msg!r} lacks {bad!r}", t)
    return hist


async def main():
    rag.llm = Stub()
    # one-sided written periods
    today = date.today()
    for q, want in [("applications before march 2025", (None, date(2025, 2, 28))),
                    ("applications until may 2025", (None, date(2025, 5, 31))),
                    ("applications after june 2025", (date(2025, 7, 1), None)),
                    ("applications since 2025", (date(2025, 1, 1), None)),
                    ("show applications before 2024", (None, date(2023, 12, 31))),
                    ("applications after 1 jan 2025", (date(2025, 1, 2), None))]:
        s, e = extract_date_range(q)
        check(s == want[0] and (e == want[1] or (want[1] is None and e is not None and e >= today)), f"range {q!r}", f"{s} {e}")
    # an LLM-written answer may not state an amount / clock time the data does not carry
    for txt, payload, gone in [("The fee for service code 0154 is Rs. 400.00.", None, "400"),
                               ("The office hours are 9:00 AM to 5:00 PM.", None, "9:00"),
                               ("Latest is Rs. 600.00. Also Rs. 400.", {"fee": 600.0}, "400")]:
        out, _note = chatbot._verify_answer_numbers(txt, payload)
        check(gone not in out, f"unverified figure removed from {txt!r}", out)
    out, _n = chatbot._verify_answer_numbers("Latest is Rs. 600.00.", {"fee": 600.0})
    check("600" in out, "a figure the data carries is kept", out)
    async with AsyncSessionLocal() as db:
        for name in ("msivakumar", "csenthil", "muthulakshmis"):
            row = (await db.execute(select(SISOfficer).where(SISOfficer.email == f"{name}@sis.tn.gov.in"))).scalars().first()
            ctx = await build_officer_context(db, row)
            for chain in CHAINS:
                if name != "msivakumar" and ("001197" in chain[0][0] or "survey 5" in chain[0][0]):
                    continue  # records that belong to msivakumar's ward
                await run(db, ctx, chain, name)
            # "before" really is before: every row it lists was filed earlier
            sid = str(uuid.uuid4())
            r = await chatbot.process_chat("applications before march 2025", sid, ctx, db, chat_history=[])
            dates = re.findall(r"\b(20\d\d-\d\d-\d\d)\b", _flatten_html(r.get("response") or ""))
            check(all(d < "2025-03-01" for d in dates), f"{name} 'before march 2025' lists only earlier files", str(dates[:5]))
            r = await chatbot.process_chat("applications after june 2025", sid, ctx, db, chat_history=[])
            dates = re.findall(r"\b(20\d\d-\d\d-\d\d)\b", _flatten_html(r.get("response") or ""))
            check(all(d >= "2025-07-01" for d in dates), f"{name} 'after june 2025' lists only later files", str(dates[:5]))
    # a fresh "sorted by X" (SQL ORDER BY) and a "sort by X" follow-up on the same
    # list must give the same order, for every field and both directions
    APP = re.compile(r"\b20\d\d/0\d{3}/\d+/\d+\b")
    async with AsyncSessionLocal() as db:
        for name in ("msivakumar", "csenthil"):
            row = (await db.execute(select(SISOfficer).where(SISOfficer.email == f"{name}@sis.tn.gov.in"))).scalars().first()
            ctx = await build_officer_context(db, row)
            for field, phrase in [("ward", "ward"), ("block", "block"), ("survey", "survey number"),
                                  ("fee", "fee"), ("applicant", "applicant name"), ("date", "date"),
                                  ("status", "status"), ("appno", "application number")]:
                for direction in ("", " descending"):
                    fresh = await turn(db, ctx, str(uuid.uuid4()), f"show nisd applications sorted by {phrase}{direction}", [])
                    fresh_order = list(dict.fromkeys(APP.findall(fresh)))
                    sid = str(uuid.uuid4())
                    hist = []
                    first = await turn(db, ctx, sid, "show nisd applications", hist)
                    hist += [{"role": "user", "content": "show nisd applications"}, {"role": "assistant", "content": first}]
                    follow = await turn(db, ctx, sid, f"sort by {phrase}{direction}", hist)
                    follow_order = list(dict.fromkeys(APP.findall(follow)))
                    check(len(fresh_order) > 1 and fresh_order == follow_order,
                          f"{name} sort by {phrase}{direction}: fresh == follow-up",
                          f"{len(fresh_order)} vs {len(follow_order)}")
    await engine.dispose()
    print("ALL PASSED" if not FAILS else f"FAILED ({len(FAILS)}):\n  - " + "\n  - ".join(FAILS[:30]))
    return 0 if not FAILS else 1

raise SystemExit(asyncio.run(main()))
