"""Negation over application lists -- "not pending", "except ISD and rejected", "neither ...
nor ...", "approved but not from CSC", "without a field visit", and the fragments that
add to them ("and not ISD", "show the rest"), in English, Tamil and Tanglish.

Every expectation is a predicate over the officer's own rows, read from the register, so it
still means something after a reseed.  No LLM.

    python test_negation_scope.py            # non-streaming
    python test_negation_scope.py --stream   # the streaming entry point
"""
import asyncio
import json
import logging
import re
import sys
import uuid

logging.disable(logging.CRITICAL)
from sqlalchemy import select

from backend.database import AsyncSessionLocal, engine
from backend.models import SISOfficer
from backend.sample_db.build_lora_dataset import build_officer_context, _flatten_html
from backend.services import chatbot, rag
from backend.services.neg_scope import parse
from backend.services.postgres import get_field_visits, get_officer_applications

STREAM = "--stream" in sys.argv
FAILS = []
NUM = re.compile(r"\d{4}/\d{4}/\d{2}/\d{6}")


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
        FAILS.append(f"{label}: {detail[:200]}")


async def turn(db, ctx, sid, msg, hist):
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


def st(r): return str(r.get("status") or "").lower().replace(" ", "_")
def ty(r): return str(r.get("application_type") or r.get("type") or "").upper()
def ch(r): return str(r.get("submission_channel") or "")


# (chain of messages, predicate over a row for the LAST message's answer; None = count only)
def cases(has_visit, completed_visit):
    P = lambda *ws: (lambda r: st(r) not in ws)
    return [
        # single and multiple exclusions
        (["show my applications that are not pending"], lambda r: st(r) != "pending"),
        (["how many applications are not approved"], lambda r: st(r) != "approved"),
        (["show applications except NISD"], lambda r: ty(r) != "NISD"),
        (["show all applications other than ISD and NISD"], lambda r: ty(r) not in ("ISD", "NISD")),
        (["show everything except rejected and pending"], P("rejected", "pending")),
        (["show applications except ISD, NISD and rejected"], lambda r: ty(r) not in ("ISD", "NISD") and st(r) != "rejected"),
        (["list applications which are neither approved nor rejected"], P("approved", "rejected")),
        (["list applications neither ISD nor MERGE"], lambda r: ty(r) not in ("ISD", "MERGE")),
        (["show applications that are not approved or rejected"], P("approved", "rejected")),
        (["not pending, not rejected, not in progress"], P("pending", "rejected", "in_progress")),
        (["list everything apart from rejected and in progress"], P("rejected", "in_progress")),
        (["I don't want NISD, show the rest"], lambda r: ty(r) != "NISD"),
        (["don't show pending ones"], lambda r: st(r) != "pending"),
        (["no ISD, only NISD not rejected"], lambda r: ty(r) == "NISD" and st(r) != "rejected"),
        # a positive scope kept beside the exclusion
        (["show approved applications but not from CSC"], lambda r: st(r) == "approved" and ch(r) != "CSC"),
        (["show NISD applications that are not from Sub Registrar"], lambda r: ty(r) == "NISD" and ch(r) != "sub_registrar"),
        (["show approved but not ISD"], lambda r: st(r) == "approved" and ty(r) != "ISD"),
        (["approved and pending applications but not NISD"], lambda r: st(r) in ("approved", "pending") and ty(r) != "NISD"),
        (["show ISD that are not rejected and not pending"], lambda r: ty(r) == "ISD" and st(r) not in ("rejected", "pending")),
        (["show my pendng aplications but not nisd"], lambda r: st(r) == "pending" and ty(r) != "NISD"),
        (["show aproved but not from csc"], lambda r: st(r) == "approved" and ch(r) != "CSC"),
        # a field visit
        (["applications without field visit"], lambda r: r["application_number"] not in has_visit),
        (["ISD applications without a completed field visit"], lambda r: ty(r) == "ISD" and r["application_number"] not in completed_visit),
        (["show me approved applications which don't have a field visit"], lambda r: st(r) == "approved" and r["application_number"] not in has_visit),
        # fragments that add to an exclusion, and "the rest"
        (["show my applications that are not pending", "not rejected either", "and not ISD"],
         lambda r: st(r) not in ("pending", "rejected") and ty(r) != "ISD"),
        (["show applications except NISD", "excluding approved ones too"], lambda r: ty(r) != "NISD" and st(r) != "approved"),
        (["show applications except NISD", "excluding approved ones too", "show the rest"],
         lambda r: ty(r) == "NISD" or st(r) == "approved"),
        (["not approved", "show the rest"], lambda r: st(r) == "approved"),
        (["how many are not ISD", "show them"], lambda r: ty(r) != "ISD"),
        (["show approved but not from csc", "and not ISD", "show them"],
         lambda r: st(r) == "approved" and ch(r) != "CSC" and ty(r) != "ISD"),
        (["how many applications are not approved", "and not pending"], lambda r: st(r) not in ("approved", "pending")),
        (["how many applications are neither pending nor rejected"], P("pending", "rejected")),
        # Tamil / Tanglish
        (["நிலுவையில் இல்லாத விண்ணப்பங்களை காட்டு"], lambda r: st(r) != "pending"),
        (["NISD தவிர மற்றவை"], lambda r: ty(r) != "NISD"),
        (["approved illama applications kaattu"], lambda r: st(r) != "approved"),
        (["pending alla approved mattum kaami"], lambda r: st(r) == "approved"),
        (["isd vendam nisd kaami"], lambda r: ty(r) == "NISD"),
        (["நிலுவையில் இல்லாத விண்ணப்பங்களை காட்டு", "நிராகரிக்கப்பட்டவை வேண்டாம்"], P("pending", "rejected")),
        # a survey number scopes the list to that survey's applications
        (["show applications on survey 24"], lambda r: str(r.get("survey_no")).split("/")[0] == "24"),
        (["how many applications in survey 24"], lambda r: str(r.get("survey_no")).split("/")[0] == "24"),
        (["show approved applications in survey 24"], lambda r: str(r.get("survey_no")).split("/")[0] == "24" and st(r) == "approved"),
        (["show applications in survey 99999"], lambda r: False),
        (["survey 24 applications kaattu"], lambda r: str(r.get("survey_no")).split("/")[0] == "24"),
        # several negations said at once narrow the list on screen (whatever its length)
        (["show approved applications", "not ISD and not from csc"], lambda r: st(r) == "approved" and ty(r) != "ISD" and ch(r) != "CSC"),
        (["show approved applications", "not ISD, not CSC"], lambda r: st(r) == "approved" and ty(r) != "ISD" and ch(r) != "CSC"),
        (["show ISD applications", "not rejected and not pending"], lambda r: ty(r) == "ISD" and st(r) not in ("rejected", "pending")),
        # "full / whole / entire / every" and "present in sis" ask for the whole register, not the desk queue
        (["show full applicatio present in sis"], lambda r: True),
        (["show full applications"], lambda r: True),
        (["show entire applications list"], lambda r: True),
        (["show whole applications"], lambda r: True),
        (["show every application"], lambda r: True),
        (["full list of applications"], lambda r: True),
        (["complete list of applications"], lambda r: True),
        (["show applications in database"], lambda r: True),
        (["show al applications"], lambda r: True),
        (["display full list"], lambda r: True),
        (["show full ISD applications"], lambda r: ty(r) == "ISD"),
        (["show every rejected application"], lambda r: st(r) == "rejected"),
        # either / or, neither / nor
        (["show either approved or rejected applications"], lambda r: st(r) in ("approved", "rejected")),
        (["either approved or rejected", "but not ISD"], lambda r: st(r) in ("approved", "rejected") and ty(r) != "ISD"),
        (["either ISD or NISD applications"], lambda r: ty(r) in ("ISD", "NISD")),
        (["show applications that are either pending or in progress"], lambda r: st(r) in ("pending", "in_progress")),
        (["either approved or pending but not ISD"], lambda r: st(r) in ("approved", "pending") and ty(r) != "ISD"),
        (["show ISD applications that are either approved or rejected"], lambda r: ty(r) == "ISD" and st(r) in ("approved", "rejected")),
        (["show approved applications from either CSC or sub registrar"], lambda r: st(r) == "approved" and ch(r) in ("CSC", "sub_registrar")),
        (["either from CSC or Sub Registrar"], lambda r: ch(r) in ("CSC", "sub_registrar") and st(r) != "rejected"),
        (["neither pending nor approved nor rejected"], lambda r: st(r) not in ("pending", "approved", "rejected")),
        (["neither ISD nor NISD applications"], lambda r: ty(r) not in ("ISD", "NISD")),
        (["neither CSC nor Sub Registrar applications"], lambda r: ch(r) not in ("CSC", "sub_registrar")),
        (["show ISD applications that are neither pending nor rejected"], lambda r: ty(r) == "ISD" and st(r) not in ("pending", "rejected")),
        (["how many applications are either approved or rejected"], lambda r: st(r) in ("approved", "rejected")),
        (["how many are neither ISD nor NISD"], lambda r: ty(r) not in ("ISD", "NISD")),
        (["neither approved nor rejected", "not NISD either", "show them"], lambda r: st(r) not in ("approved", "rejected") and ty(r) != "NISD"),
        (["approved allathu rejected applications kaattu"], lambda r: st(r) in ("approved", "rejected")),
        (["ISD allathu NISD applications"], lambda r: ty(r) in ("ISD", "NISD")),
        (["approved illana rejected applications"], lambda r: st(r) in ("approved", "rejected")),
        (["approved um illa rejected um illa applications"], lambda r: st(r) not in ("approved", "rejected")),
        (["ISD um illa NISD um illa"], lambda r: ty(r) not in ("ISD", "NISD")),
        (["அங்கீகரிக்கப்பட்ட அல்லது நிராகரிக்கப்பட்ட விண்ணப்பங்கள்"], lambda r: st(r) in ("approved", "rejected")),
        (["ISD அல்லது NISD விண்ணப்பங்களை காட்டு"], lambda r: ty(r) in ("ISD", "NISD")),
        (["ஒன்று அங்கீகரிக்கப்பட்ட அல்லது நிலுவையில் உள்ள விண்ணப்பங்கள்"], lambda r: st(r) in ("approved", "pending")),
        (["அங்கீகரிக்கப்பட்டதும் இல்லை நிராகரிக்கப்பட்டதும் இல்லை"], lambda r: st(r) not in ("approved", "rejected")),
        (["ISD-யும் இல்லை NISD-யும் இல்லை"], lambda r: ty(r) not in ("ISD", "NISD")),
    ]


async def main():
    rag.llm = Stub()
    ALL = ["approved", "pending", "in_progress", "escalated", "rejected"]
    async with AsyncSessionLocal() as db:
        row = (await db.execute(select(SISOfficer).where(SISOfficer.email == "msivakumar@sis.tn.gov.in"))).scalars().first()
        ctx = await build_officer_context(db, row)
        base = (await get_officer_applications(db, ctx, status=ALL))["applications"]
        check(len(base) > 20, "base list is the whole register", str(len(base)))
        visits = (await get_field_visits(db, ctx))["field_visits"]
        has_visit = {v["application_number"] for v in visits}
        completed_visit = {v["application_number"] for v in visits if v.get("status") == "completed"}
        for chain, pred in cases(has_visit, completed_visit):
            sid, hist, raw = str(uuid.uuid4()), [], ""
            for msg in chain:
                raw = await turn(db, ctx, sid, msg, hist)
                hist += [{"role": "user", "content": msg}, {"role": "assistant", "content": raw}]
            want = {r["application_number"] for r in base if pred(r)}
            text = re.sub(r"\s+", " ", _flatten_html(raw))
            label = " | ".join(chain)
            got = set(NUM.findall(text))
            # a bare "not X" over the list on screen is answered as "N of those M are not X"
            m = re.search(r"(?:There are|Found)\s+(\d+)", text) or re.search(r"(\d+)\s+விண்ணப்ப", text) or re.match(r"\s*(\d+) of those", text)
            if re.match(r"\s*\d+ of those", text):
                got = set()
            n = int(m.group(1)) if m else 0
            check(n == len(want), f"{label!r}: count", f"want {len(want)} got {n}; {text[:100]}")
            check(got <= want, f"{label!r}: a row that should be excluded", f"{sorted(got - want)[:3]}")
        # parsing itself
        for txt, want in [("not approved or rejected", {"status": {"approved", "rejected"}}),
                          ("not approved and pending", {"status": {"approved"}}),
                          ("except approved and pending", {"status": {"approved", "pending"}}),
                          ("show all applications", {}),
                          ("applications without a survey number", {}),
                          ("no. of pending applications", {}),
                          ("show ward 102 applications", {}),
                          ("what is the status of the applications not in ward 103", {"ward": {"103"}}),
                          ("approved illama", {"status": {"approved"}}),
                          ("மறுக்கப்பட்ட தவிர", {"status": {"rejected"}})]:
            check(parse(txt)[0] == want, f"parse {txt!r}", str(parse(txt)[0]))
    await engine.dispose()
    print("FAILED:" if FAILS else "ALL PASSED")
    for f in FAILS:
        print(" ", f)
    sys.exit(1 if FAILS else 0)


asyncio.run(main())
