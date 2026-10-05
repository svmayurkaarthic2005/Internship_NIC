"""
v3 CORRECTION set -- small and targeted, from the first full evaluation (best-350: 18-28% WRONG).

What the evaluation found, and what each generator teaches:

  chain3      the application rows in the prompt had NO channel column, yet follow-ups asked for it and the model learned to
              GUESS one. Rows now carry the channel; a value that is not in the prompt (applicant, area, patta ...) is answered
              "that is not in the data I was given", never invented.
  absent      F-Line / TSLR fields asked of a file that has no such record: answer "no F-Line / TSLR record is held" (the
              pipeline's own wording, English and Tamil) instead of writing fields that do not exist; and a file number that
              is not in the register is "does not exist".
  codemap     each service code <-> its name, many phrasings, English / Tamil / Tanglish, with the neighbouring codes it gets
              confused with (0164 -> 0161 ...) as contrastive pairs.
  counts      "how many" = not rejected (+ how many were left out), "including rejected" = total, "rejected" = rejected; the
              figures for every code are printed in the prompt (not rejected / total), so nothing has to be added up.

The original evaluation files are NOT touched and share no question with this set. A separate small benchmark
(bench_v3_extra.jsonl) uses frames that are never trained on.

    python -m backend.sample_db.build_lora_dataset_v3
"""
from __future__ import annotations

import json
import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from backend.sample_db import build_lora_dataset_allcodes as A  # noqa: E402

LT = A.LT
INFO, CODES, SYSTEM = A.INFO, A.CODES, A.SYSTEM
CH_EN = {"CSC": "CSC", "sub_registrar": "Sub-Registrar", "citizen": "citizen portal"}


def norm(s): return re.sub(r"\W+", " ", s.lower()).strip()


def full(c): return f"{c} ({INFO[c]['name']})"


# ------------------------------------------------------------------ prompt
def ctx3(key, F, c=None, rows=()):
    wards = ", ".join(f"Ward {w}" for w in F.wards) or "none assigned"
    L = [f"Officer: {A.officer_name(key)}, Sub Inspector Surveyor. Jurisdiction (wards held): {wards}.",
         "Register figures for this jurisdiction (all service codes; 'not rejected' = approved + pending):"]
    tot = rej = 0
    for x in CODES:
        d = {k: F.n(x, status=k) for k in ("approved", "pending", "rejected")}
        if sum(d.values()):
            L.append(f"{x}: approved {d['approved']}, pending {d['pending']}, rejected {d['rejected']}; "
                     f"not rejected {d['approved'] + d['pending']}; total {sum(d.values())}")
            tot += sum(d.values()); rej += d["rejected"]
    L.append(f"All codes: not rejected {tot - rej}; rejected {rej}; total {tot}" if tot else "No applications in this jurisdiction.")
    if tot:
        L.append("By ward (all codes): " + ", ".join(f"{w} {len(F.sel(None, ward=w))}" for w in F.wards))
    if rows:
        L.append("Application rows referred to (Application No. | Type | Survey | Sub-division | Status | Stage | Submitted | Ward | Block | Channel):")
        for r in rows:
            k = r["service_code"]
            L.append(f"{r['application_number']} | {INFO[k]['name']} | {r['survey_no']} | {r['subdivision_no']} | {r['status'].capitalize()} | "
                     f"{r['stage']} | {r['submission_date']} | {r['ward_number']} | {r['block_number']} | {CH_EN[r['submission_channel']]} |")
    return SYSTEM + "\n\n" + "\n".join(L)


def conv(system, *pairs):
    m = [{"role": "system", "content": system}]
    for q, a in pairs:
        m += [{"role": "user", "content": q}, {"role": "assistant", "content": a}]
    return m


# ------------------------------------------------------------------ phrasings (train frames vs held-out bench frames)
FR = {
    "status": {"train": ["tell me where {a} stands", "{a} - current position?", "what stage is {a} at", "is {a} still open"],
               "bench": ["is {a} done yet", "{a} progress", "how far has {a} got"]},
    "date": {"train": ["filing date?", "on which date did it come in", "date of submission", "ta:எப்போது சமர்ப்பிக்கப்பட்டது", "tg:epo submit pannanga"],
             "bench": ["when did this reach us", "date it was lodged", "ta:சமர்ப்பித்த நாள் என்ன"]},
    "ward": {"train": ["location of the file", "block and ward", "where is it located", "ta:எந்த வார்டு, பிளாக்", "tg:ward block enna"],
             "bench": ["which ward does it fall in", "its ward and block?", "ta:வார்டு எண் சொல்லுங்கள்"]},
    "channel": {"train": ["who sent it - CSC or SRO or portal", "source of the application", "came via which route", "ta:எந்த வழியில் வந்தது", "tg:channel enna"],
                "bench": ["through which counter did it arrive", "how did it reach us", "ta:இது எந்த மூலம் சமர்ப்பிக்கப்பட்டது"]},
    "survey": {"train": ["survey details?", "parcel number?", "which survey does it cover", "ta:சர்வே எண் என்ன", "tg:survey number enna"],
               "bench": ["its survey and sub-division", "what survey is it on", "ta:எந்த சர்வே எண்"]},
    "unknown": {"train": ["who is the applicant", "what is the land area", "owner name?", "applicant mobile number", "what is the patta number",
                          "ta:விண்ணப்பதாரர் யார்", "tg:applicant yaaru"],
                "bench": ["name of the person who applied", "how big is the plot", "ta:உரிமையாளர் பெயர் என்ன", "tg:mobile number enna"]},
}
FL_FIELDS = [("SIS recommendation", "SIS பரிந்துரை"), ("DIS remarks", "DIS குறிப்புரை"), ("transaction status", "பரிவர்த்தனை நிலை"),
             ("total boundaries", "எல்லைகள் எண்ணிக்கை"), ("sketch", "வரைபடம்"), ("scheduled date", "திட்டமிட்ட தேதி"),
             ("SIS verify date", "SIS சரிபார்ப்பு தேதி"), ("DIS user", "DIS பயனர்"), ("DIS reason", "DIS காரணம்")]
TS_FIELDS = [("challan date", "சலான் தேதி"), ("challan number", "சலான் எண்"), ("payment mode", "பணம் செலுத்திய முறை"), ("treasury", "கருவூலம்"),
             ("amount paid", "தொகை"), ("door number", "கதவு எண்"), ("old patta number", "பழைய பட்டா எண்"), ("submitted date", "சமர்ப்பித்த தேதி")]
ABS_FRAMES = {"train": {"en": ["what is the {f} for {a}", "{a}: {f}?", "show the {k} {f} of {a}", "does {a} have a {k} {f}"],
                        "tg": ["{a} {k} {f} enna"], "ta": ["{a} க்கான {k} {ta} என்ன"]},
              "bench": {"en": ["give me the {f} recorded against {a}", "{k} {f} of file {a}", "can you tell me {a}'s {k} {f}"],
                        "tg": ["{a} oda {k} {f} sollu"], "ta": ["{a} - {k} {ta} தெரிவிக்கவும்"]}}


def pf(split, kind):
    return "bench" if split == "bench" else "train"


def pickfr(rng, kind, split):
    f = rng.choice(FR[kind][pf(split, kind)])
    lang = "en"
    if f.startswith("ta:"):
        lang, f = "ta", f[3:]
    elif f.startswith("tg:"):
        lang, f = "ta", f[3:]          # the app answers a Tanglish turn in Tamil
    return f, lang


# ------------------------------------------------------------------ generators
def g_chain3(rng, F, key, c, split):
    rows = F.sel(c)
    if not rows:
        return None
    r = rng.choice(rows)
    a = r["application_number"]
    pairs = [(rng.choice(FR["status"][pf(split, "status")]).format(a=a),
              f"Application {a} (service {c}, {INFO[c]['name']}) is {r['status']} (stage: {r['stage']}).")]
    kinds = rng.sample(["date", "ward", "channel", "survey"], rng.randint(2, 3))
    if rng.random() < 0.5:
        kinds.append("unknown")
    rng.shuffle(kinds)
    for k in kinds:
        q, lang = pickfr(rng, k, split)
        ta = lang == "ta"
        ch = CH_EN[r["submission_channel"]]
        ans = {"date": (f"{a} {r['submission_date']} அன்று சமர்ப்பிக்கப்பட்டது." if ta else f"{a} was submitted on {r['submission_date']}."),
               "ward": (f"{a} வார்டு {r['ward_number']}, பிளாக் {r['block_number']}-ல் உள்ளது." if ta else
                        f"{a} is in ward {r['ward_number']}, block {r['block_number']}."),
               "channel": (f"{a} {ch} வழியாக வந்தது." if ta else f"{a} came in through {ch}."),
               "survey": (f"{a} கணக்கெண் {r['survey_no']}, உட்பிரிவு {r['subdivision_no']}." if ta else
                          f"{a} is for survey {r['survey_no']}, sub-division {r['subdivision_no']}."),
               "unknown": (f"{a}-க்கு அது நான் பெற்ற தரவில் இல்லை; என்னிடம் உள்ள வரிசையில் வகை, சர்வே, நிலை, சமர்ப்பித்த தேதி, வார்டு, பிளாக், வழி மட்டுமே உள்ளன."
                           if ta else f"That is not in the data I was given for {a}: the row I have shows only its type, survey and sub-division, "
                                      f"status and stage, submission date, ward, block and channel.")}[k]
        pairs.append((q, ans))
    return conv(ctx3(key, F, c, [r]), *pairs)


def g_absent(rng, F, key, c, split, kind):
    """kind fline|tslr: asked of a file of code c that has no such record."""
    own = "0178" if kind == "fline" else "0156"
    rows = F.sel(c)
    if c == own or not rows:
        return None
    r = rng.choice(rows)
    a = r["application_number"]
    fields = FL_FIELDS if kind == "fline" else TS_FIELDS
    f, ta_f = rng.choice(fields)
    lang = rng.choices(["en", "tg", "ta"], [6, 2, 2])[0]
    fr = rng.choice(ABS_FRAMES[pf(split, kind)][lang])
    q = fr.format(f=f, a=a, ta=ta_f, k=("F-Line" if kind == "fline" else "TSLR"))
    if kind == "fline":
        ans = (f"விண்ணப்பம் {a}-க்கு F-Line (சேவை குறியீடு 0178) பதிவு இல்லை. F-Line புலங்கள் (பரிவர்த்தனை நிலை, எல்லைகள், ஓவியம், "
               f"SIS / DIS சரிபார்ப்பு) F-Line விண்ணப்பங்களுக்கு மட்டுமே உள்ளன.") if lang != "en" else \
              (f"No F-Line (service code 0178) record is held for application {a}. F-Line fields (transaction status, boundaries, sketch, "
               f"SIS / DIS verification) exist only for F-Line applications.")
    else:
        ans = (f"விண்ணப்பம் {a}-க்கு TSLR வரைபட பதிவு இல்லை. TSLR வரைபட விண்ணப்பத்துக்கு (சேவை குறியீடு 0156) மட்டுமே அது இருக்கும்.") \
            if lang != "en" else \
            (f"No TSLR sketch record is held for application {a}. Only a TSLR sketch application (service code 0156) carries one.")
    return conv(ctx3(key, F, c, [r]), (q, ans))


def g_missing_app(rng, F, key, c, split):
    a = A.fake_app(c)
    while any(x["application_number"] == a for x in F.rows):
        a = A.fake_app(c)
    lang = rng.choices(["en", "ta"], [3, 1])[0]
    q = rng.choice(["status of {a}", "{a} details please", "is {a} approved", "tell me about {a}"] if split != "bench" else
                   ["where does {a} stand", "look up {a}"]).format(a=a)
    if lang == "ta":
        q = f"{a} நிலை என்ன"
    ans = (f"விண்ணப்பம் {a} உங்கள் அதிகார எல்லையில் பதிவேட்டில் இல்லை. எண்ணைச் சரிபார்த்து மீண்டும் முயலவும்." if lang == "ta" else
           f"Application {a} does not exist in the register for your jurisdiction. Please check the application number and try again.")
    return conv(ctx3(key, F, c, []), (q, ans))


NEIGH = {c: [x for x in CODES if x != c and abs(int(x) - int(c)) <= 3] or [x for x in CODES if x != c][:3] for c in CODES}
CM_EN = {"train": ["what is the service code for {n}", "{n} code number", "service code of {nl} please", "which code does {n} use",
                   "is {n} {o1} or {c}?", "code for {s}"],
         "bench": ["tell me the number of the {nl} service", "{nl} - which code", "is the code of {n} {c} or {o1}"]}


def g_codemap(rng, F, key, c, split):
    n = INFO[c]["name"]; short = INFO[c]["short"].replace("_", " ").lower()
    o1 = rng.choice(NEIGH[c])
    kind = rng.choice(["en", "en", "inv", "ta", "tg", "contrast"])
    if kind == "en":
        q = rng.choice(CM_EN["train" if split != "bench" else "bench"]).format(n=n, nl=n.lower(), c=c, o1=o1, s=short)
        a = f"{n} is service code {c}." if "or" not in q else f"{n} is service code {c}, not {o1}."
    elif kind == "inv":
        q = rng.choice([f"{c} is which service", f"what does {c} stand for", f"service code {c}?"] if split != "bench" else
                       [f"name the service under {c}", f"{c} refers to what"])
        a = f"Service code {c} is {n}."
    elif kind == "ta":
        q = rng.choice([f"{n} க்கான சேவை குறியீடு என்ன", f"{c} என்றால் எந்த சேவை"] if split != "bench" else [f"{n} - குறியீடு எண் சொல்லுங்கள்"])
        a = f"{n} = சேவை குறியீடு {c}." if n in q else f"சேவை குறியீடு {c} = {n}."
    elif kind == "tg":
        q = rng.choice([f"{n} code enna", f"{c} enna service"] if split != "bench" else [f"{n} ku endha code", f"{c} yaar service"])
        a = f"{n} = சேவை குறியீடு {c}." if n in q else f"சேவை குறியீடு {c} = {n}."
    else:
        q = f"is {n} {o1} or {c}?" if split != "bench" else f"{n}: {c} illa {o1}?"
        a = f"{n} is service code {c}, not {o1} ({INFO[o1]['name']})."
    if rng.random() < 0.5:
        ent = {x: A.entry(x) for x in rng.sample(CODES, 5)}
        ent[c] = A.entry(c); ent[o1] = A.entry(o1)
        items = list(ent.items()); rng.shuffle(items)
        return conv(A.ctx_system(dict(items)), (q, a))
    return conv(ctx3(key, F, c), (q, a))


def g_counts(rng, F, key, c, split, lang):
    d = {k: F.n(c, status=k) for k in ("approved", "pending", "rejected")}
    nr, rj, t = d["approved"] + d["pending"], d["rejected"], sum(d.values())
    if rj == 0 or t == 0:
        return None
    ref = rng.choice(A.refs(c))
    S = "train" if split != "bench" else "bench"
    Q = {"train": {"act": ["how many {r} applications", "number of {r} files", "count {r} applications"],
                   "tot": ["how many {r} applications in total including rejected", "all {r} files with rejected ones", "{r} total count, rejected included"],
                   "rej": ["how many {r} applications were rejected", "{r} rejected count"]},
         "bench": {"act": ["size of my {r} list", "{r} files i hold"], "tot": ["{r} files, counting rejected too", "grand total of {r} including the rejected"],
                   "rej": ["{r} - how many got rejected", "rejected under {r}"]}}[S]
    TQ = {"act": "{r} கீழ் எத்தனை விண்ணப்பங்கள்", "tot": "{r} நிராகரிக்கப்பட்டவை உட்பட மொத்தம் எவ்வளவு", "rej": "{r} இல் எத்தனை நிராகரிக்கப்பட்டன"}
    kinds = ["act", "tot", "rej"]
    two = rng.random() < 0.45
    seq = ["act", "tot"] if two else [rng.choice(kinds)]
    pairs = []
    for k in seq:
        q = TQ[k].format(r=c) if lang == "ta" else rng.choice(Q[k]).format(r=ref)
        if k == "act":
            a = (f"{full(c)} கீழ் {nr} விண்ணப்பம்(கள்) உள்ளன. {rj} நிராகரிக்கப்பட்டவை சேர்க்கப்படவில்லை." if lang == "ta" else
                 (f"Found {nr} application(s) under {full(c)}. {rj} rejected application(s) matching this are left out; ask for rejected "
                  f"applications to see them.") if nr else
                 f"No applications under {full(c)} found in your jurisdiction. {rj} rejected application(s) matching this are left out; "
                 f"ask for rejected applications to see them.")
        elif k == "tot":
            a = (f"{full(c)} கீழ் நிராகரிக்கப்பட்டவை உட்பட மொத்தம் {t}: {d['approved']} ஒப்புதல், {d['pending']} நிலுவை, {rj} நிராகரிப்பு." if lang == "ta" else
                 f"Found {t} application(s) under {full(c)} including rejected: {d['approved']} approved, {d['pending']} pending, {rj} rejected.")
        else:
            a = (f"{full(c)} கீழ் {rj} நிராகரிக்கப்பட்ட விண்ணப்பம்(கள்) உள்ளன." if lang == "ta" else f"Found {rj} rejected application(s) under {full(c)}.")
        pairs.append((q, a))
    if two and lang == "en":
        pairs[1] = (rng.choice(["and including rejected?", "what if I count the rejected ones too", "total with rejected?"] if S == "train"
                               else ["and with the rejected added?", "counting rejected as well"]), pairs[1][1])
    return conv(ctx3(key, F, c), *pairs)


def g_register(rng, F, key, c, split, lang):
    tot = len(F.rows); rj = len(F.sel(None, status="rejected")); nr = tot - rj
    if not tot or not rj:
        return None
    S = "train" if split != "bench" else "bench"
    qa = rng.choice(["act", "tot"])
    if qa == "act":
        q = rng.choice(["how many applications do I have", "total number of my files", "how big is my register"] if S == "train" else
                       ["what is the size of my register", "count of everything I hold"])
        a = f"Found {nr} application(s) in your jurisdiction. {rj} rejected application(s) matching this are left out; ask for rejected applications to see them."
    else:
        q = rng.choice(["how many applications do I have including rejected", "total of my files, rejected too"] if S == "train" else
                       ["size of my register counting rejected", "everything I hold, rejected included"])
        a = f"Found {tot} application(s) in your jurisdiction including rejected: {nr} not rejected, {rj} rejected."
    return conv(ctx3(key, F), (q, a))


# ------------------------------------------------------------------ build
def build(seed, n_off, per, split):
    A.RNG.seed(seed)
    rng = random.Random(seed)
    offs = A.synth_offs(seed, n_off)
    out, used = [], set()

    def add(kind, c, m):
        if not m:
            return False
        k = norm(m[1]["content"]) + "|" + norm(m[0]["content"])
        if k in used or "0155" in json.dumps(m, ensure_ascii=False):
            return False
        used.add(k)
        out.append({"messages": m, "meta": {"source": "v3", "group": kind, "service_code": c, "split": split}})
        return True

    plan = [("chain3", per["chain3"]), ("absent_fline", per["absent"]), ("absent_tslr", per["absent"]), ("codemap", per["codemap"]),
            ("counts", per["counts"])]
    for kind, n in plan:
        for c in CODES:
            made = tries = 0
            while made < n and tries < n * 60:
                tries += 1
                key, F = offs[rng.randrange(len(offs))]
                if kind in ("chain3", "absent_fline", "absent_tslr") and not F.by[c]:
                    continue
                if kind == "chain3":
                    m = g_chain3(rng, F, key, c, split)
                elif kind == "absent_fline":
                    m = g_absent(rng, F, key, c, split, "fline")
                elif kind == "absent_tslr":
                    m = g_absent(rng, F, key, c, split, "tslr")
                elif kind == "codemap":
                    m = g_codemap(rng, F, key, c, split)
                else:
                    m = g_counts(rng, F, key, c, split, rng.choices(["en", "ta"], [4, 1])[0])
                if add(kind, c, m):
                    made += 1
    for _ in range(per["register"]):
        key, F = offs[rng.randrange(len(offs))]
        add("register", None, g_register(rng, F, key, None, split, "en"))
    for _ in range(per["missing"]):
        key, F = offs[rng.randrange(len(offs))]
        add("missing_app", None, g_missing_app(rng, F, key, rng.choice(CODES), split))
    return out


def write(name, rows):
    with (LT / name).open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(name, len(rows))


if __name__ == "__main__":
    P = {"chain3": 5, "absent": 5, "codemap": 10, "counts": 6, "register": 50, "missing": 70}
    train = build(71001, 90, P, "train")
    val = build(71002, 14, {k: max(1, v // 6) for k, v in P.items()}, "val")
    bench = build(71003, 14, {k: max(1, v // 4) for k, v in P.items()}, "bench")
    # no question shared with the ORIGINAL evaluation files, and no first question shared between train and val / bench
    orig = set()
    for f in ("test_heldout_allcodes.jsonl", "test_heldout_jurisdiction.jsonl", "test_heldout_flinetslr.jsonl"):
        for line in (LT / f).open(encoding="utf-8"):
            for m in json.loads(line)["messages"]:
                if m["role"] == "user":
                    orig.add(norm(m["content"]))
    q1 = lambda r: norm(r["messages"][1]["content"])
    tq = {q1(r) for r in train}
    val = [r for r in val if q1(r) not in orig]
    bench = [r for r in bench if q1(r) not in orig and q1(r) not in tq]
    train = [r for r in train if q1(r) not in orig]
    write("train_v3_new.jsonl", train)
    write("validation_v3_new.jsonl", val)
    write("bench_v3_extra.jsonl", bench)
    import collections
    print(collections.Counter(r["meta"]["group"] for r in train))
