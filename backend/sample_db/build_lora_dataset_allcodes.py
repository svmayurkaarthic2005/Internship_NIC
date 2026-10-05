"""
Training data that treats every TAMILNILAM urban service code alike (0155 excluded: the earlier
training set already carries it).

Facts come from two places only: the service-code table + backend/documents (what each code is)
and a made-up per-officer register (synth_offs, below) -- never the live database. Answers are
composed here from those rows -- not from process_chat -- so they cannot inherit a pipeline bug,
and there is no real application, count or officer anywhere in the output to memorise.

Anti-memorisation: every application number in a question or answer is a fresh made-up serial
(same year / code / district, random six digits), questions are template x slot combinations
de-duplicated against every user turn already in train_augmented / validation / eval_set, each
code gets exactly the same quota of examples per category, and train / validation draw from two
disjoint synthetic-officer pools (different RNG seeds) so no register can be seen in training and
simply recalled on validation.

    python -m backend.sample_db.build_lora_dataset_allcodes
writes lora_training/train_allcodes_new.jsonl, validation_allcodes_new.jsonl, train_augmented_v2.jsonl
"""
from __future__ import annotations

import json
import logging
import random
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
logging.disable(logging.CRITICAL)
import structlog  # noqa: E402

structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.CRITICAL))

from backend.utils.helpers import SIS_URBAN_SERVICES  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
LT = ROOT / "lora_training"
DOC = ROOT / "backend" / "documents" / "tamilnilam_urban_services_and_districts.txt"
SYSTEM = ("You are the SIS AI Assistant for Tamil Nadu Sub Inspector Surveyor officers. Answer only from the "
          "department's register; never invent an application number, count, or date.")
EXCLUDE = {"0155"}
PER_CELL = {"meaning": 6, "fv": 4, "count": 10, "list": 5, "channel": 4, "ward": 3, "month": 3, "app": 5,
            "pct": 3, "fee": 3, "chain": 12, "cross": 4}  # chain is the multi-turn generator; given a bigger quota
RNG = random.Random(20261004)

CODES = [c for c in SIS_URBAN_SERVICES if c not in EXCLUDE]
INFO = {c: SIS_URBAN_SERVICES[c] for c in CODES}
DESC = {m.group(1): m.group(2).strip().rstrip(".") for m in
        re.finditer(r"^- (\d{4}): .*? — (.+)$", DOC.read_text(encoding="utf-8"), re.M)}
ST_TA = {"approved": "ஒப்புதல் அளிக்கப்பட்ட", "pending": "நிலுவையில் உள்ள", "rejected": "நிராகரிக்கப்பட்ட"}
CH = {"CSC": ["CSC", "CSC counter", "common service centre"], "sub_registrar": ["SRO", "Sub-Registrar", "sub registrar"],
      "citizen": ["citizen portal", "citizen", "online portal"]}
CH_LABEL = {"CSC": "CSC", "sub_registrar": "Sub-Registrar", "citizen": "citizen portal"}
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]


def full(c): return f"{c} ({INFO[c]['name']})"


def refs(c):
    n = INFO[c]["name"]
    out = [c, f"service code {c}", f"code {c}", n, n.lower()]
    if c in ("0153", "0154"):
        out += [INFO[c]["short"]]
    return out


def typo(q: str) -> str:
    ws = q.split()
    idx = [i for i, w in enumerate(ws) if len(w) >= 6 and w.isalpha() and w.isascii()]
    if not idx:
        return q
    i = RNG.choice(idx)
    w = ws[i]
    k = RNG.randrange(1, len(w) - 2)
    ws[i] = w[:k] + w[k + 1] + w[k] + w[k + 2:]
    return " ".join(ws)


def fake_app(c, year=None):
    return f"{year or RNG.choice([2024, 2025, 2026])}/{c}/{RNG.choice(['02', '28', '28'])}/{RNG.randint(100, 999999):06d}"


def pick(lst): return RNG.choice(lst)


def pos_pick(options, n, p=0.7):
    """Prefer an option that has data (p of the time), so 'No applications ...' does not become the default answer."""
    pos = [o for o in options if n(o) > 0]
    return pick(pos) if pos and RNG.random() < p else pick(options)


def rand_counts():
    if RNG.random() < 0.25:
        return {"approved": 0, "pending": 0, "rejected": 0}
    d = {"approved": RNG.randint(0, 9), "pending": RNG.randint(0, 9), "rejected": RNG.randint(0, 4)}
    if not (d["approved"] or d["pending"]):
        d["approved"] = RNG.randint(1, 9)
    return d


# ---------------------------------------------------------------- per-officer facts
class Facts:
    def __init__(self, rows):
        self.rows = rows
        self.by = {c: [r for r in rows if r["service_code"] == c] for c in CODES}
        self.wards = sorted({r["ward_number"] for r in rows if r["ward_number"] not in (None, "N/A")})

    def n(self, c, **kw):
        return len(self.sel(c, **kw))

    def sel(self, c=None, status=None, chan=None, ward=None, ym=None):
        out = self.rows if c is None else self.by[c]
        if status: out = [r for r in out if r["status"] == status]
        if chan: out = [r for r in out if r["submission_channel"] == chan]
        if ward: out = [r for r in out if r["ward_number"] == ward]
        if ym: out = [r for r in out if (r["submission_date"] or "")[:7] == ym]
        return out


FAKE: dict = {}
LAST: dict = {"rows": [], "code": None}
NAME_POOL = ["Arun Kumar", "Meena Sundaram", "Selvam Pandian", "Divya Krishnan", "Anbu Mani", "Lakshmi Narayanan", "Ravi Shankar",
             "Priya Devi", "Karthik Raja", "Nithya Thangaraj", "Murugan Velu", "Kavitha Raman", "Senthil Nathan", "Revathi Gopal",
             "Bala Subramani", "Saranya Dhanapal", "Vignesh Babu", "Deepa Chandran", "Manoj Prabhu", "Gayathri Mohan"]


def officer_name(key):
    return NAME_POOL[sum(map(ord, key)) % len(NAME_POOL)]


def fake_for(r, c):
    k = r["application_number"]
    if k not in FAKE:
        FAKE[k] = fake_app(c, int(k[:4]))
    return FAKE[k]


def row_line(r, c):
    return (f"{fake_for(r, c)} | {INFO[c]['name']} | {r['survey_no']} | {r['subdivision_no']} | {r['status'].capitalize()} | "
            f"{r['stage']} | {r['submission_date']} | {r['ward_number']} | {r['block_number']} |")


def officer_context(name, F, c=None, rows=()):
    """What the model is GIVEN about this officer: wards held and the register figures the answer is read from.
    Different officers hold different wards, so the same question has different answers -- they must come from here."""
    wards = ", ".join(f"Ward {w}" for w in F.wards) or "none assigned"
    lines = [f"Officer: {name}, Sub Inspector Surveyor. Jurisdiction (wards held): {wards}.",
             "Register figures for this jurisdiction (rejected files included; all service codes):"]
    tot = 0
    for x in CODES:
        d = {k: F.n(x, status=k) for k in ("approved", "pending", "rejected")}
        if sum(d.values()):
            tot += sum(d.values())
            lines.append(f"{x}: " + ", ".join(f"{k} {v}" for k, v in d.items() if v))
    lines.append(f"Total: {tot}" if tot else "No applications in this jurisdiction.")
    if tot:
        lines.append("By ward (all codes): " + ", ".join(f"{w} {len(F.sel(None, ward=w))}" for w in F.wards))
    if c and F.by.get(c):
        ch = {k: len(F.sel(c, chan=k)) for k in CH}
        wd = {w: len(F.sel(c, ward=w)) for w in F.wards}
        mo = {}
        for r in F.by[c]:
            mo[(r["submission_date"] or "")[:7]] = mo.get((r["submission_date"] or "")[:7], 0) + 1
        lines.append(f"For {c}: channels " + ", ".join(f"{CH_LABEL[k]} {v}" for k, v in ch.items() if v) +
                     "; wards " + ", ".join(f"{w} {v}" for w, v in wd.items() if v) +
                     "; months " + ", ".join(f"{k} {v}" for k, v in sorted(mo.items()) if k))
    if rows:
        lines.append("Application rows referred to (Application No. | Type | Survey | Sub-division | Status | Stage | Submitted | Ward | Block):")
        lines += [row_line(r, r["service_code"]) for r in rows]
    return SYSTEM + "\n\n" + "\n".join(lines)


DATA_KINDS = {"count", "list", "channel", "ward", "month", "pct", "cross", "app", "chain", "chain_deep"}


def with_officer(m, F, key, c):
    if m:
        m[0]["content"] = officer_context(officer_name(key), F, c, LAST["rows"])
    return m


def table(rows, c, ta):
    if not rows:
        return ""
    LAST["rows"] = list(rows[:5])
    head = "Application No. | Type | Survey No. | Sub-Divisions | Status | Stage | Submitted | Ward | Block |"
    lines = [head]
    for r in rows[:5]:
        lines.append(row_line(r, c))
    if len(rows) > 5:
        lines.append(f"... ({len(rows) - 5} more row(s) omitted)")
    return "\n".join(lines)


# ---------------------------------------------------------------- answers
def A_count(c, st, n, ta, hidden=0):
    if ta:
        if n == 0:
            return f"உங்கள் அதிகார எல்லையில் {full(c)} கீழ் {ST_TA[st] if st else ''} விண்ணப்பங்கள் இல்லை.".replace("  ", " ")
        return f"{full(c)} கீழ் {n} {ST_TA[st] if st else ''} விண்ணப்பம்(கள்) உள்ளன.".replace("  ", " ")
    if n == 0:
        return f"No {st + ' ' if st else ''}applications under {full(c)} found in your jurisdiction."
    s = f"Found {n} {st + ' ' if st else ''}application(s) under {full(c)}."
    if hidden and not st:
        s += f" {hidden} rejected application(s) matching this are left out; ask for rejected applications to see them."
    return s


def meaning(c, ta):
    fv = INFO[c]["requires_field_visit"]
    d = DESC.get(c, INFO[c]["name"])
    if ta:
        return (f"சேவை குறியீடு {c} = {INFO[c]['name']} ({INFO[c]['short']}) — TAMILNILAM நகர்ப்புற சேவை; {d}. "
                f"{'கள ஆய்வு தேவை.' if fv else 'கள ஆய்வு தேவையில்லை.'} இந்த குறியீட்டின் விண்ணப்பங்களை எண்ணிக்கை, நிலை, வழி, வார்டு, "
                f"பிளாக், மாதம் வாரியாகப் பார்க்கலாம்.")
    return (f"Service code {c} is {INFO[c]['name']} ({INFO[c]['short']}), a TAMILNILAM urban service: {d}. "
            f"{'It needs a field visit.' if fv else 'It does not need a field visit.'} Applications under it are in the "
            f"register like any other code: ask for a count or list by status, channel, ward, block or month, or for one file by number.")


def fv_answer(c, ta):
    fv = INFO[c]["requires_field_visit"]
    if ta:
        return f"{full(c)} சேவைக்கு " + ("கள ஆய்வு தேவை." if fv else "கள ஆய்வு தேவையில்லை.")
    return f"{full(c)} " + ("needs a field visit." if fv else "does not need a field visit.")


def fee_answer(c, ta, what="fee"):
    if what == "fee":
        if c == "0153":
            return "0153 (NISD): government fee Rs 100.00 plus a CSC service charge of Rs 60.00."
        if c == "0154":
            return "0154 (ISD): government fee Rs 400.00 plus a CSC service charge of Rs 60.00."
        return (f"The reference documents give a fee only for 0153 (NISD) and 0154 (ISD). No fee schedule is held for "
                f"{full(c)}, so I cannot state one.") if not ta else (
            f"குறிப்பு ஆவணங்கள் 0153 மற்றும் 0154-க்கு மட்டுமே கட்டணம் தருகின்றன; {full(c)}-க்கு கட்டண விவரம் இல்லை.")
    if c == "0153":
        return "0153 (NISD): 15-20 working days from submission to completion."
    if c == "0154":
        return "0154 (ISD): 30-35 working days from submission to completion."
    return (f"The reference documents give a time limit only for 0153 (15-20 working days) and 0154 (30-35 working days). "
            f"None is documented for {full(c)}.") if not ta else (
        f"குறிப்பு ஆவணங்கள் 0153 (15-20 வேலை நாட்கள்) மற்றும் 0154 (30-35 வேலை நாட்கள்)-க்கு மட்டுமே கால வரம்பு தருகின்றன; "
        f"{full(c)}-க்கு இல்லை.")


# ---------------------------------------------------------------- question templates
Q = {
    "meaning": {"en": ["what is {r}", "what does {r} mean", "explain {r}", "tell me about {r}", "meaning of {r}",
                       "what is {r} used for", "can you describe {r}"],
                "ta": ["{r} என்றால் என்ன", "{r} பற்றி சொல்லுங்கள்", "{r} என்பது என்ன சேவை"],
                "tg": ["{r} endral enna", "{r} na enna", "{r} patri sollunga", "{r} enna service"]},
    "fv": {"en": ["does {r} need a field visit", "is a field visit required for {r}", "is field inspection compulsory in {r}",
                  "{r} - field visit needed?"],
           "ta": ["{r} க்கு கள ஆய்வு தேவையா", "{r} சேவையில் கள ஆய்வு அவசியமா"],
           "tg": ["{r} ku field visit venuma", "{r} ku field visit thevaya"]},
    "count": {"en": ["how many {r} applications are {st}", "number of {st} {r} applications", "count of {st} {r} files",
                     "do I have any {st} {r} applications", "{r} {st} count please",
                     "how many applications under {r} are {st}"],
              "ta": ["{r} கீழ் எத்தனை {stt} விண்ணப்பங்கள் உள்ளன", "{r} {stt} விண்ணப்பங்களின் எண்ணிக்கை என்ன"],
              "tg": ["{r} la evlo {st} applications irukku", "{r} {st} applications evlo", "{r} kila {st} count sollunga"]},
    "countall": {"en": ["how many {r} applications do I have", "total {r} applications", "how many applications are there under {r}",
                        "count {r} applications"],
                 "ta": ["{r} கீழ் மொத்தம் எத்தனை விண்ணப்பங்கள்"],
                 "tg": ["{r} applications evlo irukku", "{r} total evlo"]},
    "list": {"en": ["show my {st} {r} applications", "list {st} {r} applications", "give me the {st} {r} files",
                    "display {st} applications of {r}"],
             "ta": ["{r} {stt} விண்ணப்பங்களை காட்டு", "{r} கீழ் உள்ள {stt} விண்ணப்பங்களை பட்டியலிடு"],
             "tg": ["{r} {st} applications kaattu", "{r} {st} list pannunga"]},
    "listall": {"en": ["show {r} applications", "list all {r} applications", "show applications under {r}", "display {r} files"],
                "ta": ["{r} விண்ணப்பங்களை காட்டு"], "tg": ["{r} applications kaattu", "{r} applications list pannunga"]},
    "channel": {"en": ["how many {r} applications came through {ch}", "{r} applications from {ch}",
                       "show {r} applications submitted via {ch}", "count of {ch} applications under {r}"],
                "ta": ["{r} கீழ் {ch} வழியாக எத்தனை விண்ணப்பங்கள்"],
                "tg": ["{r} la {ch} through evlo applications", "{ch} la vandha {r} applications evlo"]},
    "ward": {"en": ["how many {r} applications in ward {w}", "{r} applications in ward {w}", "count {r} files of ward {w}"],
             "ta": ["வார்டு {w} இல் {r} விண்ணப்பங்கள் எத்தனை"], "tg": ["ward {w} la {r} applications evlo"]},
    "month": {"en": ["how many {r} applications were submitted in {m} {y}", "{r} applications of {m} {y}",
                     "count {r} files filed in {m} {y}"],
              "ta": ["{m} {y} இல் {r} விண்ணப்பங்கள் எத்தனை"], "tg": ["{m} {y} la {r} applications evlo"]},
    "pct": {"en": ["what percentage of {r} applications are {st}", "{st} share of {r} applications",
                   "what fraction of my {r} files is {st}"],
            "ta": ["{r} விண்ணப்பங்களில் எத்தனை சதவீதம் {stt}"], "tg": ["{r} la {st} percentage evlo"]},
    "fee": {"en": ["what is the fee for {r}", "how much does {r} cost", "service charge for {r}"],
            "ta": ["{r} க்கு கட்டணம் என்ன"], "tg": ["{r} fee evlo"]},
    "sla": {"en": ["how many days does {r} take", "what is the SLA for {r}", "time limit for {r}"],
            "ta": ["{r} க்கு எத்தனை நாட்கள் ஆகும்"], "tg": ["{r} ku evlo naal aagum"]},
    "app": {"en": ["status of {a}", "show details of {a}", "when was {a} submitted", "which ward is {a} in",
                   "what is the survey number of {a}", "which channel did {a} come through", "is {a} approved",
                   "which service code is {a}"],
            "ta": ["{a} நிலை என்ன", "{a} விவரங்களை காட்டு"], "tg": ["{a} status enna", "{a} details kaattu"]},
}
LANGS = ["en"] * 6 + ["ta", "tg", "tg"]


def asked(kind, lang, **kw):
    t = pick(Q[kind][lang])
    q = t.format(**kw)
    if lang != "ta" and RNG.random() < 0.15:
        q = typo(q)
    if lang == "en" and RNG.random() < 0.3:
        q = q.lower() if RNG.random() < .5 else q.capitalize()
    return q


# ---------------------------------------------------------------- generators (one example each)
def msgs(*pairs):
    m = [{"role": "system", "content": SYSTEM}]
    for q, a in pairs:
        m += [{"role": "user", "content": q}, {"role": "assistant", "content": a}]
    return m


def g_meaning(c, F, lang):
    return msgs((asked("meaning", lang, r=pick(refs(c))), meaning(c, lang != "en")))


def g_fv(c, F, lang):
    return msgs((asked("fv", lang, r=pick(refs(c))), fv_answer(c, lang != "en")))


def g_count(c, F, lang):
    ta = lang != "en"
    if RNG.random() < .25:
        q = asked("countall", lang, r=pick(refs(c)))
        n = F.n(c) - F.n(c, status="rejected")
        return msgs((q, A_count(c, None, n, ta, F.n(c, status="rejected"))))
    st = pos_pick(["approved", "pending", "rejected"], lambda x: F.n(c, status=x))
    q = asked("count", lang, r=pick(refs(c)), st=st, stt=ST_TA[st])
    return msgs((q, A_count(c, st, F.n(c, status=st), ta)))


def g_list(c, F, lang):
    ta = lang != "en"
    if RNG.random() < .4:
        st = pos_pick(["approved", "pending", "rejected"], lambda x: F.n(c, status=x))
        q = asked("list", lang, r=pick(refs(c)), st=st, stt=ST_TA[st])
        rows = F.sel(c, status=st)
    else:
        q = asked("listall", lang, r=pick(refs(c)))
        rows = [r for r in F.sel(c) if r["status"] != "rejected"]
        st = None
    a = A_count(c, st, len(rows), ta, F.n(c, status="rejected") if not st else 0)
    if rows:
        a += "\n" + table(rows, c, ta)
    return msgs((q, a))


def g_channel(c, F, lang):
    ta = lang != "en"
    ch = pos_pick(list(CH), lambda x: len(F.sel(c, chan=x)))
    q = asked("channel", lang, r=pick(refs(c)), ch=pick(CH[ch]))
    n = len(F.sel(c, chan=ch))
    if ta:
        a = (f"{full(c)} கீழ் {CH_LABEL[ch]} வழியாக {n} விண்ணப்பம்(கள்) வந்துள்ளன." if n else
             f"{full(c)} கீழ் {CH_LABEL[ch]} வழியாக விண்ணப்பங்கள் இல்லை.")
    else:
        a = (f"Found {n} {CH_LABEL[ch]} application(s) under {full(c)}." if n else
             f"No {CH_LABEL[ch]} applications under {full(c)} found in your jurisdiction.")
    return msgs((q, a))


def g_ward(c, F, lang):
    ta = lang != "en"
    if RNG.random() < .25:  # a ward the officer does not hold
        w = f"{RNG.randint(200, 990)}"
        q = asked("ward", lang, r=pick(refs(c)), w=w)
        names = ", ".join(f"Ward {x}" for x in F.wards)
        a = (f"Ward {w} is outside your assigned jurisdiction. You can only see data for {names}. "
             f"For another ward, please ask the officer who holds it or your supervising officer.")
        return msgs((q, a))
    w = pos_pick(F.wards, lambda x: len(F.sel(c, ward=x)))
    q = asked("ward", lang, r=pick(refs(c)), w=w)
    n = len(F.sel(c, ward=w))
    if ta:
        a = f"வார்டு {w} இல் {full(c)} கீழ் {n} விண்ணப்பம்(கள்) உள்ளன." if n else f"வார்டு {w} இல் {full(c)} கீழ் விண்ணப்பங்கள் இல்லை."
    else:
        a = (f"Found {n} application(s) under {full(c)} in Ward {w}." if n else
             f"No applications under {full(c)} found in Ward {w}.")
    return msgs((q, a))


def g_month(c, F, lang):
    ta = lang != "en"
    yms = sorted({r["submission_date"][:7] for r in F.rows if r["submission_date"]})
    ym = pos_pick(yms, lambda x: len(F.sel(c, ym=x)))
    y, mo = int(ym[:4]), int(ym[5:])
    q = asked("month", lang, r=pick(refs(c)), m=MONTHS[mo - 1], y=y)
    n = len(F.sel(c, ym=ym))
    if ta:
        a = f"{MONTHS[mo - 1]} {y} இல் {full(c)} கீழ் {n} விண்ணப்பம்(கள்) சமர்ப்பிக்கப்பட்டன." if n else \
            f"{MONTHS[mo - 1]} {y} இல் {full(c)} கீழ் விண்ணப்பங்கள் இல்லை."
    else:
        a = (f"Found {n} application(s) under {full(c)} submitted in {MONTHS[mo - 1]} {y}." if n else
             f"No applications under {full(c)} were submitted in {MONTHS[mo - 1]} {y}.")
    return msgs((q, a))


def g_pct(c, F, lang):
    ta = lang != "en"
    st = pos_pick(["approved", "pending", "rejected"], lambda x: F.n(c, status=x))
    q = asked("pct", lang, r=pick(refs(c)), st=st, stt=ST_TA[st])
    t = F.n(c)
    if not t:
        a = (f"{full(c)} கீழ் விண்ணப்பங்கள் இல்லை, எனவே சதவீதம் கூற முடியாது." if ta else
             f"There are no applications under {full(c)}, so no percentage can be given.")
    else:
        k = F.n(c, status=st)
        p = round(100 * k / t, 1)
        a = (f"{full(c)} கீழ் {t} விண்ணப்பங்களில் {k} {ST_TA[st]} — {p}%." if ta else
             f"{k} of {t} application(s) under {full(c)} are {st} — {p}%.")
    return msgs((q, a))


def g_fee(c, F, lang):
    kind = pick(["fee", "sla"])
    q = asked(kind, lang, r=pick(refs(c)))
    return msgs((q, fee_answer(c, lang != "en", kind)))


def g_app(c, F, lang):
    rows = F.sel(c)
    if not rows:  # a code with no file in this officer's wards: an unknown number is "does not exist"
        a = fake_app(c)
        q = asked("app", lang, a=a)
        return msgs((q, f"Application {a} does not exist in the register for your jurisdiction. Please check the application number and try again."
                    if lang == "en" else f"விண்ணப்பம் {a} உங்கள் அதிகார எல்லையில் பதிவேட்டில் இல்லை. எண்ணைச் சரிபார்த்து மீண்டும் முயலவும்."))
    r = pick(rows)
    a = fake_for(r, c)
    LAST["rows"] = [r]
    ta = lang != "en"
    kind = pick(["status", "details", "date", "ward", "survey", "channel", "approved", "code"]) if lang == "en" else pick(["status", "details"])
    q = {"status": f"status of {a}", "details": f"show details of {a}", "date": f"when was {a} submitted",
         "ward": f"which ward is {a} in", "survey": f"what is the survey number of {a}",
         "channel": f"which channel did {a} come through", "approved": f"is {a} approved",
         "code": f"which service code is {a}"}[kind]
    if lang == "ta":
        q = pick([f"{a} நிலை என்ன", f"{a} விவரங்களை காட்டு"]); kind = "status" if "நிலை" in q else "details"
    elif lang == "tg":
        q = pick([f"{a} status enna", f"{a} details kaattu"]); kind = "status" if "status" in q else "details"
    if RNG.random() < .2 and lang != "ta":
        q = typo(q)
    stt = r["status"]
    card = (f"{a}: Service {c} - {INFO[c]['name']}\nStatus: {stt} ({r['stage']})\nSubmitted: {r['submission_date']}\n"
            f"Survey / sub-division: {r['survey_no']} / {r['subdivision_no']}\nWard / block: {r['ward_number']} / {r['block_number']}\n"
            f"Channel: {CH_LABEL.get(r['submission_channel'], r['submission_channel'])}")
    ans = {"status": f"Application {a} (service {c}, {INFO[c]['name']}) is {stt} (stage: {r['stage']}).",
           "details": card,
           "date": f"{a} was submitted on {r['submission_date']}.",
           "ward": f"{a} is in ward {r['ward_number']}, block {r['block_number']}.",
           "survey": f"{a} is for survey {r['survey_no']}, sub-division {r['subdivision_no']}.",
           "channel": f"{a} came in through {CH_LABEL.get(r['submission_channel'], r['submission_channel'])}.",
           "approved": (f"Yes, {a} is approved." if stt == "approved" else f"No, {a} is {stt}."),
           "code": f"{a} is service code {c}: {INFO[c]['name']}."}[kind]
    if ta:
        ans = ({"status": f"விண்ணப்பம் {a} (சேவை {c}, {INFO[c]['name']}) நிலை: {stt} ({r['stage']}).",
                "details": card}[kind])
    return msgs((q, ans))


def g_chain(c, F, lang):
    rows = [r for r in F.sel(c) if r["status"] != "rejected"]
    if len(rows) < 2:
        return None
    ref = pick(refs(c))
    q1 = pick([f"show {ref} applications", f"list my {ref} files", f"{ref} applications please"])
    a1 = A_count(c, None, len(rows), False, F.n(c, status="rejected")) + "\n" + table(rows, c, False)
    ap = sum(r["status"] == "approved" for r in rows)
    q2 = pick(["how many of them are approved", "how many are approved", "how many are pending"])
    k = ap if "approved" in q2 else len(rows) - ap
    st = "approved" if "approved" in q2 else "pending"
    a2 = f"{k} of those {len(rows)} {INFO[c]['name']} application(s) are {st}."
    pairs = [(q1, a1), (q2, a2)]
    ch = pick([x for x in CH if any(r["submission_channel"] == x for r in rows)] or [None])
    if ch:
        n = sum(r["submission_channel"] == ch for r in rows)
        pairs.append((pick([f"and from {CH[ch][0]}?", f"only {CH[ch][0]} ones"]),
                      f"{n} of those {len(rows)} {INFO[c]['name']} application(s) came through {CH_LABEL[ch]}."))
    return msgs(*pairs)


# cross-code questions, each code appears in the same number of pairs
def g_cross(c, F, lang):
    o = pick([x for x in CODES if x != c])
    t = RNG.random()
    nc, no = F.n(c), F.n(o)
    if t < .3:
        q = pick([f"how many more {pick(refs(c))} than {pick(refs(o))} applications", f"difference between {c} and {o} application counts",
                  f"compare {c} and {o} applications"])
        a = f"{full(c)} has {nc} application(s) and {full(o)} has {no}: " + (
            "no difference." if nc == no else f"{abs(nc - no)} more under {c if nc > no else o}.")
    elif t < .55:
        q = pick([f"total of {c} and {o} applications", f"{c} plus {o} applications", f"how many applications under {c} and {o} together"])
        a = f"{c}: {nc} + {o}: {no} = {nc + no} application(s)."
    elif t < .75:
        tot = len(F.rows)
        q = pick([f"how many applications are not under {pick(refs(c))}", f"all applications except {c}", f"applications other than {c}"])
        a = f"{tot - nc} application(s) are not under {full(c)} ({tot} in all, {nc} under {c})."
    else:
        q = pick([f"{c} or {o} - which has more applications", f"which has more, {c} or {o}"])
        a = (f"Both have {nc}." if nc == no else f"{full(c if nc > no else o)} has more: {max(nc, no)} against {min(nc, no)}.")
    return msgs((q, a))


def g_chain_deep(c, F, lang):
    """A longer, context-carrying conversation about ONE application: status, then four
    follow-ups that never repeat the application number -- the shape that regressed once in
    the live chatbot (a recommendation question losing its application after 'and the
    remarks?'). Test-set only: see build_lora_testset.py."""
    rows = F.sel(c)
    if not rows:
        return None
    r = pick(rows)
    a = fake_for(r, c)
    LAST["rows"] = [r]   # the application's row is in the prompt: its ward, date, channel and survey are read from it
    pairs = [
        (pick([f"status of {a}", f"what is the status of {a}", f"{a} status please", f"is {a} approved or pending"]),
         f"Application {a} (service {c}, {INFO[c]['name']}) is {r['status']} (stage: {r['stage']})."),
        (pick(["when was it submitted", "and the submission date", "what date was it filed", "submitted when"]),
         f"{a} was submitted on {r['submission_date']}."),
        (pick(["which ward is it in", "and the ward", "what ward and block", "ward / block for it"]),
         f"{a} is in ward {r['ward_number']}, block {r['block_number']}."),
        (pick(["and the channel it came through", "which channel", "how was it submitted"]),
         f"{a} came in through {CH_LABEL.get(r['submission_channel'], r['submission_channel'])}."),
        (pick(["what about the survey number", "and the survey / sub-division", "survey no for it"]),
         f"{a} is for survey {r['survey_no']}, sub-division {r['subdivision_no']}."),
    ]
    return msgs(*pairs)


GEN = {"meaning": g_meaning, "fv": g_fv, "count": g_count, "list": g_list, "channel": g_channel, "ward": g_ward,
       "month": g_month, "app": g_app, "pct": g_pct, "fee": g_fee, "chain": g_chain, "cross": g_cross,
       "chain_deep": g_chain_deep}


# Only the codes in the table are used. The context-grounded examples teach the model to read the table / counts it is
# GIVEN, so a code added to the table later is handled the same way without any made-up code in the data.
NEW = {}


def ctx_system(entries, counts=None):
    s = SYSTEM + "\n\nService code reference (from the department's code table):\n"
    s += "\n".join(f"{c} {n} - {d}; {'field visit required' if fv else 'no field visit'}" for c, (n, d, fv) in entries.items())
    if counts is not None:
        body = "\n".join(f"{c}: " + ", ".join(f"{k} {v}" for k, v in sorted(d.items()) if v)
                         for c, d in counts.items() if sum(d.values()))
        s += "\n\nRegister counts for this officer's jurisdiction:\n" + (body or "no applications")
    return s


def cm(system, *pairs):
    m = msgs(*pairs)
    m[0]["content"] = system
    return m


def entry(c): return (INFO[c]["name"], DESC.get(c, INFO[c]["name"]), INFO[c]["requires_field_visit"])


def A_count_ctx(c, n, st, k, ta):
    if ta:
        return (f"{c} ({n}) கீழ் {k} {ST_TA[st]} விண்ணப்பம்(கள்) உள்ளன." if k
                else f"உங்கள் அதிகார எல்லையில் {c} ({n}) கீழ் {ST_TA[st]} விண்ணப்பங்கள் இல்லை.")
    return f"Found {k} {st} application(s) under {c} ({n})." if k else f"No {st} applications under {c} ({n}) found in your jurisdiction."


def ctx_examples(offs):
    """Context-grounded examples: new codes, the whole table, unknown codes, counts read from context."""
    out = []
    kinds = ["meaning", "fv", "count", "countall", "pct", "fee", "sla", "count", "countall", "pct", "count", "meaning"]
    for c in CODES:
        n, d, fv = entry(c)
        for rep in range(12):
            email, F = offs[rep % len(offs)]
            ent = {x: entry(x) for x in RNG.sample([y for y in CODES if y != c], 3)}
            ent[c] = (n, d, fv)
            items = list(ent.items()); RNG.shuffle(items); ent = dict(items)
            ref = pick([c, f"service code {c}", n, n.lower()])
            cnts = {x: {s_: F.n(x, status=s_) for s_ in ("approved", "pending", "rejected")} for x in ent}
            cnts[c] = rand_counts()
            sysm = ctx_system(ent, cnts)
            ta = rep % 3 == 1
            kind = kinds[rep]
            if kind == "meaning":
                q = asked("meaning", "ta" if ta else pick(["en", "tg"]), r=ref)
                a = (f"சேவை குறியீடு {c} = {n} — {d}. {'கள ஆய்வு தேவை.' if fv else 'கள ஆய்வு தேவையில்லை.'}" if ta else
                     f"Service code {c} is {n}: {d}. {'It needs a field visit.' if fv else 'It does not need a field visit.'}")
            elif kind == "fv":
                q = asked("fv", "ta" if ta else "en", r=ref)
                a = (f"{c} ({n}) சேவைக்கு " + ("கள ஆய்வு தேவை." if fv else "கள ஆய்வு தேவையில்லை.")) if ta else \
                    f"{c} ({n}) " + ("needs a field visit." if fv else "does not need a field visit.")
            elif kind == "count":
                st = pick(["approved", "pending", "rejected"])
                q = asked("count", "ta" if ta else pick(["en", "tg"]), r=ref, st=st, stt=ST_TA[st])
                a = A_count_ctx(c, n, st, cnts[c][st], ta)
            elif kind == "countall":
                q = asked("countall", "en", r=ref)
                t = cnts[c]["approved"] + cnts[c]["pending"]
                a = ((f"Found {t} application(s) under {c} ({n})." +
                      (f" {cnts[c]['rejected']} rejected application(s) matching this are left out; ask for rejected applications to see them."
                       if cnts[c]["rejected"] else "")) if t else f"No applications under {c} ({n}) found in your jurisdiction.")
            elif kind == "pct":
                st = pick(["approved", "pending", "rejected"]); k = cnts[c][st]; t = sum(cnts[c].values())
                q = asked("pct", "en", r=ref, st=st, stt=ST_TA[st])
                a = (f"{k} of {t} application(s) under {c} ({n}) are {st} - {round(100 * k / t, 1)}%." if t else
                     f"There are no applications under {c} ({n}), so no percentage can be given.")
            elif kind == "fee":
                q = asked("fee", "en", r=ref)
                a = (f"The reference documents give a fee only for 0153 (NISD) and 0154 (ISD). No fee schedule is held for {c} ({n}), "
                     f"so I cannot state one.")
            else:
                q = asked("sla", "en", r=ref)
                a = (f"The reference documents give a time limit only for 0153 (15-20 working days) and 0154 (30-35 working days). "
                     f"None is documented for {c} ({n}).")
            out.append((email, cm(sysm, (q, a)), c))
    # the table is whatever is given: list it, never a remembered list
    for rep in range(40):
        email, F = offs[rep % len(offs)]
        ent = {c: entry(c) for c in RNG.sample(CODES, RNG.randint(4, 9))}
        items = list(ent.items()); RNG.shuffle(items); ent = dict(items)
        q = pick(["list all service codes", "which service codes can you answer about", "what are the service codes",
                  "show me every service code", "service codes enna enna irukku", "are all service codes handled the same way",
                  "do you cover only ISD and NISD"])
        body = "; ".join(f"{c} {n}" for c, (n, _, _) in sorted(ent.items()))
        a = (f"The code table lists {len(ent)} service codes: {body}." if "same way" not in q and "only ISD" not in q else
             f"No. Every code in the table - {', '.join(sorted(ent))} - is answered the same way: counts, lists, status, "
             f"channel, ward, block and month, and any file by number.")
        out.append((email, cm(ctx_system(ent), (q, a)), None))
    # a code that is NOT in the given table is never described or invented
    for rep in range(60):
        email, F = offs[rep % len(offs)]
        ent = {c: entry(c) for c in RNG.sample(CODES, RNG.randint(4, 8))}
        x = pick(["0100", "0166", "0174", "0177", "0180", "0182", "0186", "0210"] )
        q = pick([f"what is {x}", f"service code {x} meaning", f"{x} endral enna", f"how many {x} applications do I have",
                  f"is {x} a valid service code"])
        a = (f"I could not find '{x}' in the service code table I have, so I cannot say what it is or count it. If it is a new "
             f"code, the table needs updating; if you meant a ward, block or survey number, please say which.")
        out.append((email, cm(ctx_system(ent), (q, a)), None))
    # most / least / breakdown read from the counts GIVEN, including codes never seen in training
    for rep in range(90):
        email, F = offs[rep % len(offs)]
        pool = RNG.sample(CODES, RNG.randint(4, 8))
        ent = {c: entry(c) for c in pool}
        cnts = {c: {"approved": RNG.randint(0, 9), "pending": RNG.randint(0, 9), "rejected": RNG.randint(0, 4)} for c in ent}
        tot = {c: sum(v.values()) for c, v in cnts.items() if sum(v.values())}
        if len(tot) < 2:
            continue
        mx, mn = max(tot.values()), min(tot.values())
        nm = lambda c: f"{c} ({ent[c][0]})"
        kind = rep % 3
        if kind == 0:
            q = pick(["which service code has the most applications", "most applications under which code"])
            top = sorted(c for c, v in tot.items() if v == mx)
            a = (f"{', '.join(nm(c) for c in top)} - {mx} application(s){' each' if len(top) > 1 else ''}, the most of "
                 f"{len(tot)} codes ({sum(tot.values())} in all).")
        elif kind == 1:
            q = pick(["which service code has the least applications", "fewest applications under which code"])
            low = sorted(c for c, v in tot.items() if v == mn)
            a = (f"{', '.join(nm(c) for c in low)} - {mn} application(s){' each' if len(low) > 1 else ''}, the fewest of "
                 f"{len(tot)} codes with applications ({sum(tot.values())} in all).")
        else:
            q = pick(["break down my applications by service code", "applications per service code"])
            a = (f"Your {sum(tot.values())} application(s) are under {len(tot)} service codes. " +
                 "; ".join(f"{nm(c)}: {v}" for c, v in sorted(tot.items())) + ".")
        out.append((email, cm(ctx_system(ent, cnts), (q, a)), None))
    return out


def synth_offs(seed, n):
    """Made-up officers with made-up registers across every code -- no live DB row, no real application
    number, anywhere. Train and validation each get their OWN officer pool (disjoint seed), so nothing
    about one officer's register can be memorised from training and recalled on validation."""
    rng = random.Random(seed)
    out = []
    for i in range(n):
        wards = sorted({f"{rng.randint(1, 180):03d}" for _ in range(rng.randint(1, 4))})
        held = rng.sample(CODES, rng.randint(8, len(CODES)))
        rows = []
        for c in held:
            for _ in range(rng.choice([0, 0, 1, 2, 3, 4, 6, 9, 13])):
                st = rng.choices(["approved", "pending", "rejected"], [5, 3, 2])[0]
                y = rng.choice([2024, 2025, 2026])
                mo = rng.randint(1, 9 if y == 2026 else 12)
                rows.append({
                    "application_number": fake_app(c, y), "service_code": c, "status": st,
                    "stage": "Open" if st == "pending" else "Completed",
                    "submission_channel": rng.choices(list(CH), [5, 3.5, 1.5])[0],
                    "submission_date": f"{y}-{mo:02d}-{rng.randint(1, 28):02d}",
                    "ward_number": rng.choice(wards), "block_number": f"{rng.randint(1, 60):04d}",
                    "survey_no": str(rng.randint(1, 400)), "subdivision_no": rng.randint(0, 6),
                })
        out.append((f"synth-{seed}-{i:03d}@sis.local", Facts(rows)))
    return out


def norm(q): return re.sub(r"\W+", " ", q.lower()).strip()


def seen_questions():
    s = set()
    for f in [LT / "train_augmented.jsonl", LT / "validation.jsonl", ROOT / "backend" / "sample_db" / "eval_set.jsonl"]:
        if f.exists():
            for line in f.open(encoding="utf-8"):
                try:
                    for m in json.loads(line).get("messages", []):
                        if m["role"] == "user":
                            s.add(norm(m["content"]))
                except Exception:
                    pass
    return s


def generate(offs, per_cell, used):
    """One split's examples, from one officer pool. `used` is the de-dup set shared across both
    splits, so the same (question, context) pair cannot land in both train and validation."""
    ex = []

    def add(email, kind, code, messages):
        firstq = messages[1]["content"]
        key = norm(firstq) + ("|" + norm(messages[0]["content"]) if len(messages[0]["content"]) > len(SYSTEM) else "")
        if key in used or "0155" in json.dumps(messages, ensure_ascii=False):
            return False
        used.add(key)
        ex.append({"messages": messages, "meta": {"officer": email, "source": "allcodes", "group": kind, "service_code": code}})
        return True

    for kind, n in per_cell.items():
        for c in CODES:
            made = tries = 0
            while made < n and tries < n * 40:
                tries += 1
                email, F = offs[(made + tries) % len(offs)]
                if kind in ("app", "chain"):
                    have = [o for o in offs if o[1].by[c]]
                    email, F = have[(made + tries) % len(have)] if have else (email, F)
                lang = pick(LANGS)
                FAKE.clear(); LAST["rows"] = []
                m = GEN[kind](c, F, lang if kind != "chain" else "en")
                if kind in DATA_KINDS:
                    m = with_officer(m, F, email, c)
                if m and add(email, kind, c, m):
                    made += 1
    for email, m, code in ctx_examples(offs):
        add(email, "context", code, m)

    # 18 of the 29 codes have no application in the register, so the data-driven groups are mostly "No applications ...".
    # Keep those answers to 40% of each group (the context-grounded examples give every code non-zero counts), so "none"
    # does not become the model's default answer.
    neg_re = re.compile(r"^(No |உங்கள் அதிகார எல்லையில்)|இல்லை\.$")
    for grp in ("count", "list", "channel", "month", "ward"):
        idx = [i for i, e in enumerate(ex) if e["meta"]["group"] == grp]
        neg = [i for i in idx if neg_re.search(ex[i]["messages"][-1]["content"].split(chr(10))[0])]
        keep_neg = int(0.4 * len(idx))
        drop = set(RNG.sample(neg, max(0, len(neg) - keep_neg))) if len(neg) > keep_neg else set()
        ex = [e for i, e in enumerate(ex) if i not in drop]
    RNG.shuffle(ex)
    return ex


def main():
    # Disjoint synthetic-officer pools: 36 for train, 10 for validation, different RNG seeds --
    # no officer, register or application number is shared between the two splits.
    train_offs = synth_offs(70001, 36)
    val_offs = synth_offs(80002, 10)
    used = set(seen_questions())
    tr = generate(train_offs, PER_CELL, used)
    val_full = generate(val_offs, PER_CELL, used)
    RNG.shuffle(val_full)
    val = val_full[:max(150, len(tr) // 8)]

    for name, rows in (("train_allcodes_new.jsonl", tr), ("validation_allcodes_new.jsonl", val)):
        with (LT / name).open("w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with (LT / "train_augmented_v2.jsonl").open("w", encoding="utf-8") as f:
        for line in (LT / "train_augmented.jsonl").open(encoding="utf-8"):
            f.write(line if line.endswith("\n") else line + "\n")
        for r in tr:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print("new train", len(tr), "new val", len(val))
    per = Counter(r["meta"]["service_code"] for r in tr)
    print("per code min/max:", min(v for k, v in per.items() if k), max(v for k, v in per.items() if k))
    print("0155 anywhere:", sum("0155" in json.dumps(r, ensure_ascii=False) for r in tr + val))


if __name__ == "__main__":
    main()
