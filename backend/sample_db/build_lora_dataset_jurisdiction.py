"""
Many different officers, each with their own wards and register -- so the model learns to answer from the jurisdiction and
figures it is GIVEN, never from numbers it saw in training.

Officers are synthetic: wards, names, per-code counts, channels, dates and application numbers are all made up, drawn per
officer (some hold one ward, some three, some nothing at all; any of the 29 codes). The real register is not used, so there is
nothing to memorise. The same question therefore has a different correct answer for each officer, and a ward the officer does
not hold is refused by name. Whole officers are held out: validation officers are unseen in training, and the test officers
hold wards (061-100) that training never mentions and are asked with phrasings training never uses.

    python -m backend.sample_db.build_lora_dataset_jurisdiction
writes lora_training/train_jurisdiction_new.jsonl, validation_jurisdiction_new.jsonl, test_heldout_jurisdiction.jsonl
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from backend.sample_db import build_lora_dataset_allcodes as A  # noqa: E402
from backend.sample_db.build_lora_testset import HELD_Q  # noqa: E402

TRAIN_WARDS = [f"{i:03d}" for i in range(1, 61)]
TEST_WARDS = [f"{i:03d}" for i in range(61, 101)]
MONTHS = [f"{y}-{m:02d}" for y in (2025, 2026) for m in range(1, 13) if (y, m) <= (2026, 9)]


def synth_officer(rng, idx, pool):
    wards = sorted(rng.sample(pool, rng.choice([1, 1, 2, 2, 3])))
    n = rng.choice([0, 3, 8, 15, 25, 40, 60, 90])
    held = rng.sample(A.CODES, rng.randint(2, 9))
    wt = [rng.random() ** 2 + 0.05 for _ in held]
    rows, used = [], set()
    for _ in range(n):
        c = rng.choices(held, wt)[0]
        st = rng.choices(["approved", "pending", "rejected"], [5, 3, 2])[0]
        ym = rng.choice(MONTHS)
        day = rng.randint(1, 28)
        while True:
            app = f"{ym[:4]}/{c}/{rng.choice(['02', '28'])}/{rng.randint(100, 999999):06d}"
            if app not in used:
                used.add(app)
                break
        rows.append({"application_number": app, "service_code": c, "status": st,
                     "stage": "Open" if st == "pending" else "Completed",
                     "submission_channel": rng.choices(list(A.CH), [5, 3.5, 1.5])[0],
                     "submission_date": f"{ym}-{day:02d}", "ward_number": rng.choice(wards),
                     "block_number": f"{rng.randint(1, 60):04d}", "survey_no": str(rng.randint(1, 400)),
                     "subdivision_no": rng.randint(0, 6)})
    F = A.Facts(rows)
    F.wards = wards                       # held wards, even when the register has no file in one of them
    return f"officer-{idx}-{rng.random():.6f}", F


# ---- jurisdiction questions that need no code
def g_wards_held(F, lang, test):
    q = A.pick(["which wards do I hold", "what is my jurisdiction", "list my wards", "which wards am I responsible for",
                "show my assigned wards"] if lang == "en" and not test else
               ["tell me the wards under me", "my area of responsibility?", "wards assigned to me - which"] if lang == "en" else
               ["நான் எந்த வார்டுகளுக்கு பொறுப்பு"] if lang == "ta" else ["en ward enna enna", "naan yaar yaar ward paakkuren"])
    w = ", ".join(f"Ward {x}" for x in F.wards)
    a = (f"உங்கள் அதிகார எல்லை: {w}." if lang != "en" else f"You hold {w} ({len(F.wards)} ward{'s' if len(F.wards) > 1 else ''}).")
    return A.msgs((q, a))


def g_total(F, lang, test):
    q = A.pick(["how many applications do I have", "total number of my applications", "how many files are in my wards"] if not test else
               ["what is the size of my register", "count everything in my jurisdiction"])
    live = [r for r in F.rows if r["status"] != "rejected"]
    rej = len(F.rows) - len(live)
    if not F.rows:
        a = "No applications found in your jurisdiction."
    else:
        a = f"Found {len(live)} application(s) in your jurisdiction." + (
            f" {rej} rejected application(s) matching this are left out; ask for rejected applications to see them." if rej else "")
    return A.msgs((q, a))


def g_ward_all(F, lang, test, pool):
    outside = A.RNG.random() < 0.4
    w = A.pick([x for x in pool if x not in F.wards]) if outside else A.pick(F.wards)
    q = A.pick(([f"how many applications are in ward {w}", f"show applications in ward {w}", f"ward {w} count"] if not test else
                [f"what is in ward {w}", f"files under ward {w} - how many"]))
    if outside:
        a = (f"Ward {w} is outside your assigned jurisdiction. You can only see data for "
             f"{', '.join('Ward ' + x for x in F.wards)}. For another ward, please ask the officer who holds it or your supervising officer.")
    else:
        live = [r for r in F.sel(None, ward=w) if r["status"] != "rejected"]
        rej = len(F.sel(None, ward=w)) - len(live)
        a = (f"Found {len(live)} application(s) in Ward {w}." + (f" {rej} rejected application(s) matching this are left out; ask for rejected applications to see them." if rej else "")
             if F.sel(None, ward=w) else f"No applications found in Ward {w}.")
    return A.msgs((q, a))


def one(rng_unused, F, key, test, pool):
    """One example for a random question kind, with this officer's context in the system prompt."""
    kind = A.RNG.choices(["count", "countall", "list", "channel", "ward", "month", "pct", "cross", "app", "chain", "wards", "total", "wardall"],
                         [5, 2, 3, 2, 2, 1, 2, 2, 3, 4, 2, 2, 3])[0]  # chain (multi-turn) given more weight
    A.FAKE.clear(); A.LAST["rows"] = []
    lang = A.pick(A.LANGS)
    held = sorted({r["service_code"] for r in F.rows})
    c = A.pick(held) if held and A.RNG.random() < 0.6 else A.pick(A.CODES)
    if kind == "wards":
        return kind, c, A.with_officer(g_wards_held(F, lang, test), F, key, None)
    if kind == "total":
        return kind, None, A.with_officer(g_total(F, lang, test), F, key, None)
    if kind == "wardall":
        return kind, None, A.with_officer(g_ward_all(F, lang, test, pool), F, key, None)
    try:
        m = A.GEN["count" if kind == "countall" else kind](c, F, "en" if kind == "chain" else lang)
    except (IndexError, ValueError):   # an officer with no rows has no month / channel to ask about
        return None
    if m and kind in ("app", "chain") and not F.by[c]:
        return None
    return kind, c, A.with_officer(m, F, key, c) if m else None


def build(seed, n_officers, per, pool, test, first_idx):
    A.RNG.seed(seed)
    rng = random.Random(seed)
    out, used = [], set()
    for i in range(n_officers):
        key, F = synth_officer(rng, first_idx + i, pool)
        made = tries = 0
        while made < per and tries < per * 12:
            tries += 1
            r = one(rng, F, key, test, pool)
            if not r or not r[2]:
                continue
            kind, c, m = r
            k = A.norm(m[1]["content"]) + "|" + A.norm(m[0]["content"])
            if k in used or "0155" in json.dumps(m, ensure_ascii=False):
                continue
            used.add(k)
            out.append({"messages": m, "meta": {"officer": key, "source": "jurisdiction", "group": kind, "service_code": c}})
            made += 1
    return out


def write(name, rows):
    with (A.LT / name).open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(name, len(rows))


if __name__ == "__main__":
    train = build(101, 80, 20, TRAIN_WARDS, False, 0)
    val = build(202, 10, 20, TRAIN_WARDS, False, 1000)           # unseen officers, wards from the training pool
    A.Q.update(HELD_Q)                                            # test: phrasings training never uses, wards 061-100
    test = build(303, 25, 12, TEST_WARDS, True, 2000)
    write("train_jurisdiction_new.jsonl", train)
    write("validation_jurisdiction_new.jsonl", val)
    write("test_heldout_jurisdiction.jsonl", test)
    import collections
    print(collections.Counter(r["meta"]["group"] for r in train))
    print("max system chars", max(len(r["messages"][0]["content"]) for r in train + test))
