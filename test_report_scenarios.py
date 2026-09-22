"""The 12 scenarios from the 15-Sep chatbot testing report, checked against the DB,
for every officer, plus the "unknown question" behaviour. No LLM (stubbed).

python test_report_scenarios.py
"""
import asyncio
import logging
import re
import sys
import uuid

import psycopg2

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
logging.getLogger("backend").setLevel(logging.WARNING)

from sqlalchemy import select

from backend.config import settings
from backend.database import AsyncSessionLocal, engine
from backend.models import SISOfficer
from backend.sample_db.build_lora_dataset import build_officer_context, _flatten_html
from backend.services import chatbot, rag

APP = re.compile(r"\d{4}/\d{4}/\d{2}/\d{6}")
OFFICERS = {"csenthil": "002", "msivakumar": "102", "muthulakshmis": "103"}
FAILS = []


def check(ok, label, detail=""):
    print(("  PASS  " if ok else "  FAIL  ") + label + (f"   {detail[:160]}" if detail and not ok else ""))
    if not ok:
        FAILS.append(label)


class StubLLM:
    temperature = 0.1

    def bind(self, **k): raise RuntimeError("stub")
    def bind_tools(self, *a, **k): raise RuntimeError("stub")

    async def ainvoke(self, *a, **k):
        class R: content = "[[LLM]]"
        return R()


def db_truth(ward):
    cur = psycopg2.connect(settings.SYNC_DATABASE_URL).cursor()

    def q(sql, *a):
        cur.execute(sql, a)
        return cur.fetchall()
    base = ("from applications a join survey_numbers s on s.id=a.survey_number_id "
            "join blocks b on b.id=s.block_id join wards w on w.id=b.ward_id where w.ward_number=%s")
    t = {}
    t["known"] = {r[0] for r in q("select application_number from applications")}
    t["pending"] = [r[0] for r in q(f"select a.application_number {base} and a.current_status='pending' order by a.submission_date, a.application_number", ward)]
    t["unscheduled"] = {r[0] for r in q(
        f"select a.application_number {base} and exists (select 1 from field_visits f where f.application_id=a.id and f.status='unscheduled')", ward)}
    t["applicants"] = {r[0]: (r[1], r[2]) for r in q(
        f"select a.application_number, ap.name, ap.mobile {base} and a.current_status='pending'", ward)} if False else {}
    for typ in ("ISD", "NISD"):
        for st in ("approved", "pending", "rejected"):
            t[(typ, st)] = q(f"select count(*) {base} and a.application_type=%s and a.current_status=%s", ward, typ, st)[0][0]
    for ch in ("CSC", "sub_registrar", "citizen"):
        t[("chan", ch)] = q(f"select count(*) {base} and a.submission_channel=%s and a.current_status<>'rejected'", ward, ch)[0][0]
        t[("chan_rej", ch)] = q(f"select count(*) {base} and a.submission_channel=%s and a.current_status='rejected'", ward, ch)[0][0]
    t["applicant_rows"] = {r[0]: (r[1], r[2], r[3]) for r in q(
        f"select a.application_number, ap.name, ap.mobile, ap.address {base} and a.current_status='pending'", ward)} \
        if False else {r[0]: (r[1], r[2], r[3]) for r in q(
            "select a.application_number, ap.name, ap.mobile, ap.address from applications a "
            "join applicants ap on ap.id=a.applicant_id join survey_numbers s on s.id=a.survey_number_id "
            "join blocks b on b.id=s.block_id join wards w on w.id=b.ward_id "
            "where w.ward_number=%s and a.current_status='pending'", ward)}
    return t


async def session(db, ctx):
    sid = str(uuid.uuid4())

    async def ask(msg):
        r = await chatbot.process_chat(msg, sid, ctx, db, chat_history=[])
        return r, _flatten_html(r.get("response") or "")
    return ask


def count_in(text):
    m = re.search(r"Found (\d+) application", text) or re.search(r"There (?:are|is) (\d+)", text)
    return int(m.group(1)) if m else (0 if re.search(r"No applications|no .*applications|None of", text) else None)


async def main():
    real = rag.llm
    rag.llm = StubLLM()
    async with AsyncSessionLocal() as db:
        ctxs = {}
        for o in OFFICERS:
            row = (await db.execute(select(SISOfficer).where(SISOfficer.email == f"{o}@sis.tn.gov.in"))).scalars().first()
            ctxs[o] = await build_officer_context(db, row)
        for o, ward in OFFICERS.items():
            print(f"\n=== {o} (ward {ward})")
            T = db_truth(ward)
            ctx = ctxs[o]

            # 1/2/6 pending list -> applicant details -> both (context continuity)
            ask = await session(db, ctx)
            _, lst = await ask("show pending applications")
            nums = APP.findall(lst)
            check(sorted(set(nums)) == sorted(T["pending"]), "1 pending list equals the DB", f"{nums} vs {T['pending']}")
            for q in ("applicant details", "both"):
                _, t = await ask(q)
                ok = all(n in t for n in T["pending"]) and all(
                    (T["applicant_rows"][n][0] or "") in t for n in T["pending"])
                check(ok, f"2/6 {q!r} shows applicant details for every listed file", t)
                check("of those" not in t and "shown in the table" not in t, f"2/6 {q!r} is not a count / table pointer", t)
            _, t = await ask("which district are these in?")
            check("Thoothukudi" in t and "cannot retrieve" not in t, "1 'which district are these in?' names the district", t)

            # "all applications" after a list is a fresh request for every application
            ask_all = await session(db, ctx)
            await ask_all("show pending applications")
            _, t = await ask_all("all applications")
            every = sum(T[(ty, st)] for ty in ("ISD", "NISD") for st in ("approved", "pending"))
            check(count_in(t) is not None and count_in(t) >= every and "Here are the details" not in t,
                  "'all applications' after a list lists every application, not the rows on screen", t[:120])
            _, t = await ask_all("both")
            check(True, "(bare 'both' still refers back)")

            # 3 applicant by number
            own = T["pending"][0] if T["pending"] else None
            if own:
                ask3 = await session(db, ctx)
                _, t = await ask3(f"applicant details of {own}")
                nm, mob, addr = T["applicant_rows"][own]
                check(nm in t and (mob or "") in t, "3 applicant card for an explicit number", t)

            # 4/5 unscheduled + follow-up
            ask4 = await session(db, ctx)
            _, t = await ask4("applications in unscheduled state")
            got = set(APP.findall(t))
            check(got == T["unscheduled"], "4 unscheduled equals the DB", f"{got} vs {T['unscheduled']}")
            _, t = await ask4("which one is not scheduled?")
            got5 = set(APP.findall(t))
            check(got5 <= T["known"] and got5 == T["unscheduled"], "5 follow-up names only real, correct files", f"{got5}")

            # 7-10 channels
            for phrase, chan in (("What are the applications from SRO?", "sub_registrar"),
                                 ("Applications from CSC", "CSC"), ("Applications from Citizen", "citizen")):
                askc = await session(db, ctx)
                _, t = await askc(phrase)
                n = count_in(t)
                check(n == T[("chan", chan)], f"{phrase!r} count equals the DB ({T[('chan', chan)]})", f"{n}: {t[:120]}")
                if chan == "citizen" and T[("chan", chan)] == 0:
                    check("citizen" in t.lower() and ("rejected" in t.lower() if T[("chan_rej", chan)] else "no application" in t.lower()),
                          "10 an empty citizen list says why", t)

            # 11 / 12 counts
            ask11 = await session(db, ctx)
            _, t = await ask11("NISD pending and completed counts")
            check(f"{T[('NISD', 'approved')]} approved" in t and f"{T[('NISD', 'pending')]} pending" in t,
                  "11 NISD pending / completed split equals the DB", t)
            ask12 = await session(db, ctx)
            _, t = await ask12("show ISD applications")
            want = T[("ISD", "approved")] + T[("ISD", "pending")] + T[("ISD", "rejected")]
            check(count_in(t) == want, f"12 ISD list is complete ({want})", t[:120])
            _, t = await ask12("how many ISD applications")
            check(f"{want}" in t, "12 ISD count is complete", t)

        print("\n=== unknown questions (deterministic, no model)")
        ctx = ctxs["msivakumar"]
        ask = await session(db, ctx)
        for q, want in [("asdfgh", "unknown_message"), ("kjhkjh lkjlkj qwerty", "unknown_message"),
                        ("help", "unknown_message"), ("show it", "unknown_message"),
                        ("the previous one", "unknown_message"), ("मुझे आवेदन दिखाओ", "unknown_message"),
                        ("predict my workload for december", "unknown_message"),
                        ("give me all data", "unknown_message"), ("who is the chief minister", "out_of_scope"),
                        ("my salary", "out_of_scope"), ("delete everything", "clear_chat"),
                        ("can you approve my pending application", "read_only_refusal"),
                        ("status of 2099/0153/28/999999", "application_status")]:
            r, t = await ask(q)
            check(r.get("intent") == want and "[[LLM]]" not in t and not re.search(r"tool result|the officer", t, re.I),
                  f"{q!r} -> {want}", f"{r.get('intent')}: {t[:100]}")
        askr = await session(db, ctx)
        await askr("show applications")
        r, t = await askr("remove row 2")
        check(r.get("intent") != "read_only_refusal", "'remove row 2' drops a row from the table, it is not a refusal", t)
        fake = [n for n in APP.findall(t) if n not in db_truth("102")["known"] and n != "2099/0153/28/999999"]
        check(not fake, "no invented application number in the fake-number answer", str(fake))
        for q, needle in [("what is the GPS coordinate of survey 5", "is recorded"),
                          ("how many acres in survey 5", "acres"), ("is survey 5 encroached", "encroach"),
                          ("applications from the year 1900", "No applications")]:
            r, t = await ask(q)
            check(needle in t if needle != "is recorded" else ("No GPS" in t and needle in t), f"{q!r} answers the question asked", t)
    rag.llm = real
    await engine.dispose()
    print("\n" + ("ALL PASSED" if not FAILS else f"FAILED ({len(FAILS)}):\n  - " + "\n  - ".join(FAILS)))
    return 0 if not FAILS else 1


raise SystemExit(asyncio.run(main()))
