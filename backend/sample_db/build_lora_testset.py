"""
Held-out TEST set for the service-code / F-Line / TSLR training data.

Nothing here is trained on: every question uses phrasings (frames and field synonyms) that the training builders never use,
and any question whose text already appears in a train / validation file is dropped. Answers come from the same sources as
the training data (register facts, code table, and the real chat pipeline), so after fine-tuning, run the model on these
questions (eval_heldout.py) -- a model that only memorised the training phrasings or records will fall over here.

    python -m backend.sample_db.build_lora_testset
writes lora_training/test_heldout_allcodes.jsonl and test_heldout_flinetslr.jsonl
"""
from __future__ import annotations

import asyncio
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from backend.sample_db import build_lora_dataset_allcodes as A  # noqa: E402
from backend.sample_db import build_lora_dataset_flinetslr as T  # noqa: E402

HELD_Q = {
    "meaning": {"en": ["could you tell me what {r} stands for", "give me a short note on {r}", "{r}?", "i need to understand {r}"],
                "ta": ["{r} எதற்கு பயன்படுத்தப்படுகிறது", "{r} என்ற சேவையை விளக்குங்கள்"],
                "tg": ["{r} nu sonnaa enna", "{r} service pathi sollunga"]},
    "fv": {"en": ["do officers have to inspect the site for {r}", "{r}: is an on-site check part of it", "will I visit the land for {r}"],
           "ta": ["{r} க்கு நேரில் சென்று பார்க்க வேண்டுமா"], "tg": ["{r} ku site visit pannanuma"]},
    "count": {"en": ["{r}: how many are {st}", "tell me the {st} total for {r}", "what is the number of {st} files in {r}",
                     "any {st} under {r}, and how many"],
              "ta": ["{r} இல் {stt} கோப்புகள் எவ்வளவு"], "tg": ["{r} la {st} evlo file irukku"]},
    "countall": {"en": ["{r}: how many in total", "what is my {r} workload count", "size of my {r} queue"],
                 "ta": ["{r} மொத்தம் எவ்வளவு"], "tg": ["{r} mothama evlo"]},
    "list": {"en": ["can you list the {st} ones under {r}", "{st} {r} files - show them", "i want to see {st} {r} applications"],
             "ta": ["{r} {stt} கோப்புகளை எனக்கு காட்டுங்கள்"], "tg": ["{r} {st} files list kudunga"]},
    "listall": {"en": ["can you list everything under {r}", "{r} files - show them all", "i want to see my {r} applications"],
                "ta": ["{r} கோப்புகளை எனக்கு காட்டுங்கள்"], "tg": ["{r} files ellam kaattunga"]},
    "channel": {"en": ["of my {r} files how many arrived via {ch}", "{ch} submissions under {r}", "{r} - {ch} ones only, how many"],
                "ta": ["{r} இல் {ch} மூலம் வந்தவை எத்தனை"], "tg": ["{r} la {ch} moolama vandhadhu evlo"]},
    "ward": {"en": ["{r} files for ward {w} - how many", "ward {w} {r} count", "in ward {w}, how many {r} do I have"],
             "ta": ["வார்டு {w} இல் உள்ள {r} எண்ணிக்கை"], "tg": ["ward {w} {r} evlo"]},
    "month": {"en": ["{r} files filed during {m} {y}", "how many {r} came in during {m} {y}", "{m} {y} intake of {r}"],
              "ta": ["{m} {y} மாதத்தில் {r} எத்தனை"], "tg": ["{m} {y} la {r} evlo vandhuchu"]},
    "pct": {"en": ["how much of {r} is {st}, in percent", "{r}: {st} rate", "what proportion of {r} files are {st}"],
            "ta": ["{r} இல் {stt} விகிதம் என்ன"], "tg": ["{r} la {st} evlo percent"]},
    "fee": {"en": ["what do applicants pay for {r}", "is there a fixed charge for {r}", "{r} fee details"],
            "ta": ["{r} க்கு செலுத்த வேண்டிய தொகை என்ன"], "tg": ["{r} ku evlo pay pannanum"]},
    "sla": {"en": ["how long should {r} take to finish", "what is the turnaround for {r}", "{r} deadline in working days"],
            "ta": ["{r} முடிக்க எவ்வளவு நாள் ஆகும்"], "tg": ["{r} mudiya evlo naal"]},
}

HELD_FL_FRAMES = {"en": ["can you give me the {f} for {a}", "{a}: {f}?", "for the f-line file {a}, what's the {f}", "look up {f} on {a}"],
                  "ta": ["{a} க்கான {f} என்ன", "{a} - {f} தெரிவிக்கவும்"], "tg": ["{a} oda {f} sollu", "{a} {f} enna aachu"]}
HELD_TS_FRAMES = {"en": ["can you give me the tslr {f} for {a}", "{a}: tslr {f}?", "for the tslr sketch file {a}, what's the {f}", "look up tslr {f} on {a}"],
                  "ta": ["{a} க்கான TSLR {f} என்ன", "{a} - TSLR {f} தெரிவிக்கவும்"], "tg": ["{a} oda tslr {f} sollu", "tslr {f} {a} enna aachu"]}
HELD_FL_GENERAL = ["give me the full f-line record for {a}", "does {a} have an f-line record or not", "{a} f-line - which fields are empty",
                   "has the SIS desk recommended {a}", "f-line {a} complete summary"]
HELD_TS_GENERAL = ["give me the full tslr record for {a}", "does {a} have a tslr record or not", "{a} tslr - which fields are empty",
                   "tslr rows count for {a}", "tslr {a} complete summary"]
HELD_FOLLOW_FL = [("tell me about the f-line application {a}", ["what about its DIS recommendation", "and the patta?"])]
HELD_FOLLOW_TS = [("give the tslr sketch details for {a}", ["what was paid", "and the door number?"])]


def part1():
    A.Q.update(HELD_Q)
    A.RNG.seed(777)
    offs = A.synth_offs(90003, 12)  # a third, disjoint synthetic-officer pool -- unseen by train and validation
    seen = A.seen_questions()
    for f in A.LT.glob("*_new.jsonl"):
        for line in f.open(encoding="utf-8"):
            for m in json.loads(line)["messages"]:
                if m["role"] == "user":
                    seen.add(A.norm(m["content"]))
    out, used = [], set(seen)
    kinds = ["meaning", "fv", "count", "list", "channel", "ward", "month", "pct", "fee", "cross"]
    for kind in kinds:
        for c in A.CODES:
            made = tries = 0
            while made < 2 and tries < 60:
                tries += 1
                email, F = offs[tries % len(offs)]
                A.FAKE.clear(); A.LAST["rows"] = []
                m = A.GEN[kind](c, F, A.pick(A.LANGS))
                if m and kind in A.DATA_KINDS:
                    m = A.with_officer(m, F, email, c)   # the officer's wards and register figures are in the prompt
                if m and A.norm(m[1]["content"]) not in used:
                    used.add(A.norm(m[1]["content"]))
                    out.append({"messages": m, "meta": {"officer": email, "source": "heldout", "group": kind, "service_code": c}})
                    made += 1
    for email, m, code in A.ctx_examples(offs):
        if A.norm(m[1]["content"]) not in used:
            used.add(A.norm(m[1]["content"]))
            out.append({"messages": m, "meta": {"officer": email, "source": "heldout", "group": "context", "service_code": code}})

    # Deliberately deep multi-turn chains, test-only (never in train/validation): a review of the
    # assembled dataset flagged multi-turn test coverage (1.2%) as the one thing worth strengthening,
    # and specifically pointed at this shape -- a run of follow-ups that never repeat the application
    # number, which is exactly where the live chatbot once lost context on "and the remarks?".
    for c in A.CODES:
        made = tries = 0
        while made < 4 and tries < 80:
            tries += 1
            email, F = offs[tries % len(offs)]
            A.FAKE.clear(); A.LAST["rows"] = []
            m = A.GEN["chain_deep"](c, F, "en")
            if m:
                m = A.with_officer(m, F, email, c)
            if m and A.norm(m[1]["content"]) not in used:
                used.add(A.norm(m[1]["content"]))
                out.append({"messages": m, "meta": {"officer": email, "source": "heldout", "group": "chain_deep", "service_code": c}})
                made += 1
    return out


def part2():
    T.FL_FRAMES, T.TS_FRAMES = HELD_FL_FRAMES, HELD_TS_FRAMES
    T.FL_GENERAL, T.TS_GENERAL = HELD_FL_GENERAL, HELD_TS_GENERAL
    T.FOLLOW_FL, T.FOLLOW_TS = HELD_FOLLOW_FL, HELD_FOLLOW_TS
    T.CAP = 5
    T.RNG.seed(4242)
    return asyncio.run(T.main_async(mode="test"))  # the real files held out of split_ids(), never used in train/val


def write(name, rows):
    with (A.LT / name).open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(name, len(rows))


if __name__ == "__main__":
    write("test_heldout_allcodes.jsonl", part1())
    write("test_heldout_flinetslr.jsonl", part2())
