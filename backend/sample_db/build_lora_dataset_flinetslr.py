"""
F-Line (0178) and TSLR-sketch (0156) record questions for the training set.

The questions are templates x every field x English / Tamil / Tanglish (+ typos), asked of the real files
through process_chat, so each answer is exactly what the deterministic handlers (fline_qa / tslr_qa / negq /
the chat follow-up layer) return. A made-up application number replaces every real one in the question and
the answer, user ids are masked and long ids (CAN, challan) are re-drawn, so nothing identifies a real file.
At most CAP examples are kept per real file so the few records are not learned by heart.

    python -m backend.sample_db.build_lora_dataset_flinetslr
writes lora_training/train_flinetslr_new.jsonl, validation_flinetslr_new.jsonl, and appends both to
train_augmented_v2.jsonl / validation_allcodes_new.jsonl's sibling files (see main()).
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
logging.disable(logging.CRITICAL)
import structlog  # noqa: E402

structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.CRITICAL))
from sqlalchemy import select, text  # noqa: E402

from backend.database import AsyncSessionLocal  # noqa: E402
from backend.models import SISOfficer  # noqa: E402
from backend.schemas import OfficerContext  # noqa: E402
from backend.services import chatbot  # noqa: E402
from backend.services.auth_service import get_officer_jurisdiction_ids  # noqa: E402
from backend.services.postgres import get_all_service_code_applications  # noqa: E402
from backend.sample_db.build_lora_dataset_allcodes import SYSTEM, norm, seen_questions, typo, LT, officer_name, CODES  # noqa: E402

RNG = random.Random(20261005)
CAP = 26
pick = RNG.choice

# The real F-Line/TSLR files are few (~11-29), so train/validation and the held-out test set must agree,
# independently of each other, on which real file goes where -- a fixed seed does that without any shared
# state file. main() (train+validation) and build_lora_testset.py (test) both call split_ids() and get the
# same partition, so a real file used while training is never the one the test set asks about.
ID_SPLIT_SEED = 51515


def split_ids(ids):
    """-> (trainval_ids, test_ids), deterministic from ID_SPLIT_SEED regardless of caller or call order."""
    ids = sorted(ids)
    rng = random.Random(ID_SPLIT_SEED)
    rng.shuffle(ids)
    n_test = max(1, round(len(ids) * 0.15)) if ids else 0
    return set(ids[n_test:]), set(ids[:n_test])

FL = {  # field -> (English phrasings, Tamil, Tanglish)
    "survey number": (["survey number", "survey no"], "சர்வே எண்", "survey number"),
    "sub division number": (["sub division number", "subdivision no"], "உட்பிரிவு எண்", "sub division number"),
    "patta number": (["patta number", "patta no"], "பட்டா எண்", "patta number"),
    "land type": (["land type"], "நில வகை", "land type"),
    "land type code": (["land type code"], "நில வகை குறியீடு", "land type code"),
    "extent": (["extent", "area"], "பரப்பளவு", "extent"),
    "govt or private": (["govt private code", "government or private"], "அரசு தனியார் குறியீடு", "govt private"),
    "transaction status": (["transaction status"], "பரிவர்த்தனை நிலை", "transaction status"),
    "total boundaries": (["total boundaries", "boundary count"], "எல்லைகள் எண்ணிக்கை", "boundaries evlo"),
    "sketch": (["sketch", "SIS sketch available"], "வரைபடம்", "sketch irukka"),
    "DIS sketch": (["DIS sketch"], "DIS வரைபடம்", "DIS sketch"),
    "SIS recommendation": (["SIS recommendation"], "SIS பரிந்துரை", "SIS parindhurai"),
    "SIS remarks": (["SIS remarks"], "SIS குறிப்புரை", "SIS kurippu"),
    "SIS reason": (["SIS reason"], "SIS காரணம்", "SIS karanam"),
    "SIS verify date": (["SIS verify date", "SIS verification date"], "SIS சரிபார்ப்பு தேதி", "SIS verify date"),
    "SIS user": (["SIS user name", "SIS username"], "SIS பயனர்", "SIS user"),
    "DIS recommendation": (["DIS recommendation"], "DIS பரிந்துரை", "DIS parindhurai"),
    "DIS remarks": (["DIS remarks"], "DIS குறிப்புரை", "DIS kurippu"),
    "DIS reason": (["DIS reason"], "DIS காரணம்", "DIS karanam"),
    "DIS verify date": (["DIS verify date"], "DIS சரிபார்ப்பு தேதி", "DIS verify date"),
    "DIS user": (["DIS user name"], "DIS பயனர்", "DIS user"),
    "scheduled date": (["scheduled date", "tobe scheduled date"], "திட்டமிட்ட தேதி", "scheduled date"),
    "applicant type": (["applicant type"], "விண்ணப்பதாரர் வகை", "applicant type"),
}
TS = {
    "survey number": (["survey number"], "சர்வே எண்", "survey number"),
    "sub division number": (["sub division number"], "உட்பிரிவு எண்", "sub division number"),
    "old patta number": (["old patta number", "old patta no"], "பழைய பட்டா எண்", "old patta number"),
    "door number": (["door number", "door no"], "கதவு எண்", "door number"),
    "land type": (["land type"], "நில வகை", "land type"),
    "type ecate": (["ecate type", "type ecate"], "ecate வகை", "ecate type"),
    "extent": (["extent", "extent u1 u2 u3"], "பரப்பளவு", "extent"),
    "submitted date": (["submitted date", "submission date"], "சமர்ப்பித்த தேதி", "submitted date"),
    "challan number": (["challan number", "challan no"], "சலான் எண்", "challan number"),
    "challan date": (["challan date"], "சலான் தேதி", "challan date"),
    "payment mode": (["payment mode"], "பணம் செலுத்திய முறை", "payment mode"),
    "payment details": (["payment details", "payment"], "பணம் செலுத்திய விவரம்", "payment details"),
    "treasury": (["treasury", "bank"], "கருவூலம்", "treasury"),
    "amount": (["amount paid", "amount"], "தொகை", "amount"),
    "CAN": (["CAN linked", "CAN record"], "CAN எண்", "CAN number"),
    "application number": (["linked application number"], "விண்ணப்ப எண்", "application number"),
    "district ward block": (["ward and block", "district taluk town ward block"], "வார்டு மற்றும் பிளாக்", "ward block"),
}
FL_FRAMES = {"en": ["{f} of {a}", "what is the {f} of f-line application {a}", "{a} {f}", "tell me the {f} for {a} (f-line)",
                    "f-line {f} for {a}", "{a} - what is its {f}"],
             "ta": ["{a} இன் {f} என்ன", "{a} F-Line {f} சொல்லுங்கள்"], "tg": ["{a} {f} enna", "{a} f-line {f} sollunga"]}
TS_FRAMES = {"en": ["tslr {f} of {a}", "what is the {f} in the tslr sketch record of {a}", "{a} tslr {f}", "show {f} from tslr for {a}",
                    "tslr sketch {f} for {a}"],
             "ta": ["{a} TSLR {f} என்ன", "{a} இன் TSLR {f} சொல்லுங்கள்"], "tg": ["{a} tslr {f} enna", "tslr {f} {a} sollunga"]}
FL_GENERAL = ["complete f-line details of {a}", "show the f-line record of {a}", "is there an f-line record for {a}",
              "is there no f-line record for {a}", "which f-line fields are blank for {a}", "which f-line fields are recorded for {a}",
              "what if the DIS recommendation is blank for {a}", "has SIS recommended {a}", "has DIS recommended {a}",
              "who has not recommended {a}", "{a} f-line illaya", "f-line details of {a} kaattu", "{a} F-Line விவரங்களை காட்டு"]
TS_GENERAL = ["complete tslr details of {a}", "is there a tslr sketch record for {a}", "is there no tslr record for {a}",
              "how many tslr rows does {a} have", "which tslr fields are blank for {a}", "compare tslr with the application {a}",
              "does the tslr survey match the application for {a}", "tslr sketch illaya {a}", "tslr details of {a} kaattu",
              "{a} TSLR விவரங்களை காட்டு", "what if the tslr challan is not paid for {a}", "tslr payment of {a} - is it complete"]
FOLLOW_FL = [("show f-line details of {a}", ["and the DIS remarks?", "who verified it", "what is its extent"]),
             ("{a} sis recommendation", ["and the remarks?", "what about DIS", "when was it verified"])]
FOLLOW_TS = [("show tslr details of {a}", ["and the amount?", "which treasury", "what is the challan date"]),
             ("tslr payment of {a}", ["and the amount?", "what is the payment mode", "which survey is it"])]


def plain(h):
    t = re.sub(r"<tr>", "\n", h or "")
    t = re.sub(r"</t[dh]>", " | ", t)
    t = re.sub(r"<br\s*/?>|</p>|</div>|</li>", "\n", t)
    t = re.sub(r"<[^>]+>", "", t)
    t = re.sub(r"[ \t]+\n", "\n", t)
    return re.sub(r"\n\s*\n+", "\n", t).strip()


FAKE_NAMES = ["Karthik R", "Meena S", "Selvam P", "Divya K", "Anbu M", "Ravi Kumar", "Priya D", "முருகன்", "கவிதா", "செல்வி", "ராஜேஷ்", "Arun V"]
FAKE_ADDR = ["No. 12, Gandhi Street, Sample Nagar", "Flat 4B, Rose Garden, Sample Town", "45, Anna Salai, Sample Colony", "7, Temple Road, Sample Puram"]
DB_NAMES: list = []


class Mask:
    """One made-up number per real one, the same in the question and the answer of one example. Also replaces people,
    addresses and mobile numbers, and drops the page's Copy button / emoji."""

    def __init__(self, users):
        self.users = sorted(users, key=len, reverse=True)
        self.map = {}

    def app(self, m):
        k = m.group(0)
        if k not in self.map:
            y, c, d, _ = k.split("/")
            self.map[k] = f"{y}/{c}/{d}/{RNG.randint(100, 999999):06d}"
        return self.map[k]

    def num(self, m):
        k = m.group(0)
        if k not in self.map:
            self.map[k] = "".join(str(RNG.randint(0, 9)) if i else str(RNG.randint(1, 9)) for i in range(len(k)))
        return self.map[k]

    def __call__(self, t):
        t = re.sub(r"\d{4}/\d{4}/\d{1,3}/\d+", self.app, t)
        t = re.sub(r"(?<![\d/.-])\d{9,}(?![\d/.-])", self.num, t)
        for u in self.users:
            t = re.sub(re.escape(u), "sis_user", t)
        # pool() is keyed on the VALUE being replaced, not the whole matched line: the same real
        # name/address shows up under an English label ("Name") in one render and a Tamil one
        # ("பெயர்") in another (same record, two languages), and keying on the full line gave each
        # label language its own fake substitute -- a contradiction the masking step invented,
        # not one the chatbot answered. Keying on the captured value fixes that.
        t = re.sub(r"(?m)^((?:Name|பெயர்)\s*\|\s*)(.*?)(\s*\|\s*)$", lambda m: m.group(1) + self.pool("n", FAKE_NAMES, m.group(2)) + m.group(3), t)
        t = re.sub(r"(?m)^((?:Address|முகவரி)\s*\|\s*)(.*?)(\s*\|\s*)$", lambda m: m.group(1) + self.pool("a", FAKE_ADDR, m.group(2)) + m.group(3), t)
        t = re.sub(r"(Applicant: )([^,\n]+)(,)", lambda m: m.group(1) + self.pool("n", FAKE_NAMES, m.group(2)) + m.group(3), t)
        for n in DB_NAMES:
            if n in t:
                t = t.replace(n, self.pool("n", FAKE_NAMES, n))
        t = t.replace("📋", "")
        t = "\n".join(l for l in t.split("\n") if l.strip() != "Copy")
        return re.sub(r"([A-Za-z\)])(\d+ (?:records|பதிவுகள்))", r"\1 - \2", t)

    def pool(self, kind, options, key):
        k = (kind, key)
        if k not in self.map:
            self.map[k] = RNG.choice(options)
        return self.map[k]


async def setup():
    out = []
    async with AsyncSessionLocal() as db:
        users = {r[0] for r in (await db.execute(text(
            "SELECT sis_username FROM f_line_service_urban WHERE sis_username IS NOT NULL UNION SELECT dis_username FROM f_line_service_urban "
            "WHERE dis_username IS NOT NULL"))).all() if r[0]}
        for tbl, col in (("applicants", None), ("owners", "name")):
            try:
                cols = [col] if col else [x[0] for x in (await db.execute(text(
                    "SELECT column_name FROM information_schema.columns WHERE table_name='applicants'"))).all() if "name" in x[0]]
                for k in cols:
                    DB_NAMES.extend(str(r[0]).strip() for r in (await db.execute(text(f"SELECT {k} FROM {tbl}"))).all()
                                    if r[0] and len(str(r[0]).strip()) >= 6)
            except Exception:
                await db.rollback()
        DB_NAMES.sort(key=len, reverse=True)
        fl = {r[0] for r in (await db.execute(text("SELECT DISTINCT appl_id FROM f_line_service_urban"))).all()}
        ts = {r[0] for r in (await db.execute(text("SELECT DISTINCT appl_id FROM tslr_sketch_urban"))).all()}
        for o in (await db.execute(select(SISOfficer).order_by(SISOfficer.employee_id))).scalars().all():
            jur = await get_officer_jurisdiction_ids(o.id, db)
            ids = jur["district_ids"] + jur["taluk_ids"] + jur["town_ids"] + jur["ward_ids"] + jur["block_ids"]
            ctx = OfficerContext(officer_id=o.id, employee_id=o.employee_id, name=o.name, email=o.email, designation=o.designation,
                                 jurisdiction_type=jur["jurisdiction_type"], jurisdiction_name=jur["jurisdiction_name"],
                                 jurisdiction_ids=[i for i in ids if i])
            rows = (await get_all_service_code_applications(db, ctx, hide_rejected=False))["applications"]
            out.append({"ctx": ctx, "email": o.email, "apps": rows})
    return out, users, fl, ts


async def ask(ctx, turns, history_seed=None):
    hist, pairs, intents = [], [], []
    async with AsyncSessionLocal() as db:
        sid = str(uuid.uuid4())
        for q in turns:
            try:
                r = await asyncio.wait_for(chatbot.process_chat(q, sid, ctx, db, list(hist)), timeout=8)
            except Exception:
                return None
            a = plain(r.get("response"))
            if not a or r.get("intent") in ("general_query", None) or "error processing" in a.lower():
                return None
            hist += [{"role": "user", "content": q}, {"role": "assistant", "content": r.get("response") or ""}]
            pairs.append((q, a)); intents.append(r.get("intent"))
    return pairs, intents


def questions(kind, a, officer_other):
    """(list of turn-lists) for one real application `a`."""
    F, G, FR, FOL = (FL, FL_GENERAL, FL_FRAMES, FOLLOW_FL) if kind == "fline" else (TS, TS_GENERAL, TS_FRAMES, FOLLOW_TS)
    out = []
    for f, (en, ta, tg) in F.items():
        lang = pick(["en", "en", "en", "ta", "tg", "tg"])
        fr = pick(FR[lang])
        q = fr.format(f=(pick(en) if lang == "en" else ta if lang == "ta" else tg), a=a)
        if lang != "ta" and RNG.random() < .2:
            q = typo(q)
        out.append([q])
    for g in G:
        out.append([g.format(a=a)])
    for first, rest in FOL:
        out.append([first.format(a=a)] + [pick(rest)])
        out.append([first.format(a=a)] + RNG.sample(rest, 2))
    return out


_REC: dict = {}


async def record_text(ctx, app):
    """The file's details card as the register shows it -- what the model is GIVEN to answer from."""
    if app not in _REC:
        r = await ask(ctx, [f"show details of {app}"])
        _REC[app] = (r[0][0][1] if r else "")[:2200]
    return _REC[app]


def head(o):
    wards = sorted({r["ward_number"] for r in o["apps"] if r["ward_number"] not in (None, "N/A")})
    return (f"{SYSTEM}\n\nOfficer: {officer_name(o['email'])}, Sub Inspector Surveyor. Jurisdiction (wards held): "
            f"{', '.join('Ward ' + w for w in wards)}.\n\nRecord data for the application asked about (from the register):\n")


def grounded(pairs, rec):
    """Every figure / date / code in an answer must be in the record it was given (or in the question): otherwise the
    model would have to remember the real record, which is exactly what must not happen."""
    for q, a in pairs:
        t = re.sub(r"\d{4}/\d{4}/\d{1,3}/\d+", " ", a)
        for tok in re.findall(r"\d[\d./-]*\d|\d{2,}", t):
            if tok in q or tok in rec or tok in CODES or re.fullmatch(r"20\d\d", tok):
                continue
            return False
    return True


async def main_async(mode="trainval"):
    offs, users, fl, ts = await setup()
    seen = seen_questions()
    for f in LT.glob("*_new.jsonl"):
        for line in f.open(encoding="utf-8"):
            for m in json.loads(line)["messages"]:
                if m["role"] == "user":
                    seen.add(norm(m["content"]))
    examples = []
    seen = {k for k in seen}
    fl_tv, fl_te = split_ids(fl)
    ts_tv, ts_te = split_ids(ts)
    # Excluded from "other" (the negative/absent-record examples) too: a real file held out for one
    # kind must not resurface as the "no such record" example inside the other kind's block, or a
    # test-only file would leak into training through that back door.
    other_excl = (fl_te | ts_te) if mode == "trainval" else (fl_tv | ts_tv)
    for kind, pool, target_pool in (("fline", fl, fl_tv if mode == "trainval" else fl_te),
                                     ("tslr", ts, ts_tv if mode == "trainval" else ts_te)):
        for o in offs:
            mine = [r["application_number"] for r in o["apps"] if r["application_number"] in target_pool]
            other = [r["application_number"] for r in o["apps"]
                     if r["application_number"] not in pool and r["application_number"] not in other_excl]
            targets = list(mine)
            for app in targets:
                qs = questions(kind, app, other)
                RNG.shuffle(qs)
                kept = 0
                for turns in qs:
                    if kept >= CAP:
                        break
                    res = await ask(o["ctx"], turns)
                    if not res:
                        continue
                    pairs, intents = res
                    mk = Mask(users)
                    rec = mk(await record_text(o["ctx"], app))
                    pairs = [(mk(q), mk(a)) for q, a in pairs]
                    key = norm(pairs[0][0]) + "|" + o["email"]
                    if key in seen or not grounded(pairs, rec) or any(re.search(r"\b0155\b", x) for p in pairs for x in p):
                        continue
                    seen.add(key)
                    m = [{"role": "system", "content": head(o) + rec}]
                    for q, a in pairs:
                        m += [{"role": "user", "content": q}, {"role": "assistant", "content": a}]
                    examples.append({"messages": m, "meta": {"officer": o["email"], "source": "flinetslr", "group": kind,
                                                              "intent": intents[-1], "real_app": app}})
                    kept += 1
            print(kind, o["email"], len(examples), flush=True)
            # files that have no such record: the answer must say so, not invent one
            for app in RNG.sample(other, min(len(other), 6)):
                for turns in RNG.sample(questions(kind, app, other), 5):
                    res = await ask(o["ctx"], turns[:1])
                    if not res:
                        continue
                    mk = Mask(users)
                    rec = mk(await record_text(o["ctx"], app))
                    q, a = mk(res[0][0][0]), mk(res[0][0][1])
                    if norm(q) + "|" + o["email"] in seen or not grounded([(q, a)], rec):
                        continue
                    seen.add(norm(q) + "|" + o["email"])
                    examples.append({"messages": [{"role": "system", "content": head(o) + rec}, {"role": "user", "content": q},
                                                  {"role": "assistant", "content": a}],
                                     "meta": {"officer": o["email"], "source": "flinetslr", "group": kind + "_absent",
                                              "intent": res[1][0], "real_app": app}})
    return examples


def main():
    ex = asyncio.run(main_async())
    RNG.shuffle(ex)
    # Split by the REAL underlying application, never by example: every example about the same real
    # file (fline/tslr target or an "other" real file shown for an absent-record answer) goes to the
    # same side, so no real record's content can be seen in training and recognised on validation.
    apps = sorted({e["meta"]["real_app"] for e in ex})
    RNG.shuffle(apps)
    nval_apps = max(1, len(apps) // 10)
    val_apps = set(apps[:nval_apps])
    val = [e for e in ex if e["meta"]["real_app"] in val_apps]
    tr = [e for e in ex if e["meta"]["real_app"] not in val_apps]
    for name, rows in (("train_flinetslr_new.jsonl", tr), ("validation_flinetslr_new.jsonl", val)):
        with (LT / name).open("w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print("train", len(tr), "val", len(val))
    from collections import Counter
    print(Counter(r["meta"]["group"] for r in ex))
    print(Counter(r["meta"]["intent"] for r in ex).most_common(8))


if __name__ == "__main__":
    main()
