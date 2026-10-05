"""
Assemble the final fine-tuning files from the generated parts.

  train_v2_clean.jsonl / validation_v2_clean.jsonl   messages only -- what train_qlora.py reads
  train_augmented_v2.jsonl / validation_v2.jsonl      the same rows with their meta (audit)
  test_heldout_*.jsonl                                never trained on (kept with meta; scored by eval_heldout.py)

Older rows are kept only when they teach something that is not a record: rows whose answer states register data (a list, a count,
"none this month", applicant names -- all read from a register that is no longer the live one) are dropped, as is anything about
service code 0155 / a MERGE type (neither exists) or the stale "only 0153 / 0154 are handled" wording. The new parts carry the
officer's wards and the data each answer is read from in their prompts, so nothing has to be remembered.

    python -m backend.sample_db.assemble_lora_final
"""
from __future__ import annotations

import json
import random
import re
import sys
from collections import Counter
from pathlib import Path

LT = Path(__file__).resolve().parents[2] / "lora_training"
RNG = random.Random(2026)
MAX_CHARS = 5200   # ~1000 tokens with Tamil; longer rows are cut by max_length in training, so they are dropped here

STALE = re.compile(r"schema even admits|schema admits|only 0153/0154|only the three|three application types|Not handled by the SIS register|"
                   r"not one of the urban service codes the SIS register holds|no application in the officer.s register uses|"
                   r"merge service code", re.I)
# \b0155\b (not the old (?<![\d/])0155(?![\d])): that lookbehind also excluded "0155" when preceded
# by "/", which is exactly the shape a real application number uses (".../0155/..."), so the one
# case this filter exists for was the one case it let through. \b alone still ignores a false-positive
# "0155" embedded inside a longer digit run (a CAN, a different application number).
BAD_CS = re.compile(r"\b0155\b|\bMERGE\b|Merge Subdiv")
MERGE_OK = re.compile(r"merge[ _-]?(?:id|applid|d application id)|merged[ _]application|merged with", re.I)
LIVE = re.compile(r"\d{4}/\d{4}/\d{1,3}/\d+|\bFound \d+|There (?:are|is) (?:\d+|no)|\b\d+ application\(s\)|Application No\.|"
                  r"No (?:\w+ ){0,3}applications?|applications? found|No .{0,40}found in your|இல்லை\.$")


def load(f):
    p = LT / f
    return [json.loads(l) for l in p.open(encoding="utf-8")] if p.exists() else []


def txt(r): return json.dumps(r, ensure_ascii=False)


def old_ok(r):
    t = txt(r)
    if BAD_CS.search(t) or STALE.search(t):
        return False
    if re.search(r"merg", t, re.I) and not MERGE_OK.search(t):
        return False
    # Checking only the LAST message let register data through when it was stated in an earlier
    # turn of a multi-turn row (a listing's own reply skips the LIVE wording, e.g. "13 of those 14
    # Sub-Registrar application(s)..."), so every message is checked, not just the final one.
    return not any(LIVE.search(m["content"]) for m in r["messages"])


def nz(s): return re.sub(r"\W+", " ", s.lower()).strip()


def qkey(r, with_ctx):
    q = nz([m["content"] for m in r["messages"] if m["role"] == "user"][0])
    return q + "|" + nz(r["messages"][0]["content"]) if with_ctx else q


APP_RE = re.compile(r"\d{4}/\d{4}/\d{1,3}/\d{3,9}")
NEG_RE = re.compile(r"\b(No |not held|does not exist|outside your|not recorded|cannot say|"
                     r"could not find|no such|not recognised|no application)|இல்லை|முடியாது|கிடைக்கவில்லை")
DBFACT_RE = re.compile(r"Register figures|Record data for the application|Application rows referred to|"
                        r"Register counts for this officer")


def template_key(r):
    """Question with every number blanked out -- the same template asked about a different
    application / ward / month collapses to one key, so near-duplicates (don't keep generating
    the same question for a different application number) can be capped."""
    q = [m["content"] for m in r["messages"] if m["role"] == "user"][0]
    return re.sub(r"\W+", " ", re.sub(r"\d+", "#", q.lower())).strip()


def cap_templates(rows, cap):
    """At most `cap` examples per question template, so one phrasing asked of N application
    numbers cannot dominate the set. Kept rows are chosen randomly, not by position."""
    groups: dict = {}
    for r in rows:
        groups.setdefault(template_key(r), []).append(r)
    out = []
    for g in groups.values():
        RNG.shuffle(g)
        out.extend(g[:cap])
    RNG.shuffle(out)
    return out


def intent_of(r):
    m = r.get("meta", {})
    return m.get("intent") or m.get("group") or "unknown"


def report(name, rows):
    app_ids, templates, neg, multi, dbfact = set(), Counter(), 0, 0, 0
    for r in rows:
        msgs = r["messages"]
        for m in msgs:
            app_ids |= set(APP_RE.findall(m["content"]))
        templates[template_key(r)] += 1
        if len(msgs) > 3:
            multi += 1
        if NEG_RE.search(msgs[-1]["content"]):
            neg += 1
        if DBFACT_RE.search(msgs[0]["content"]):
            dbfact += 1
    n = len(rows) or 1
    intents = Counter(intent_of(r) for r in rows)
    return {
        "name": name, "n": len(rows), "unique_app_ids": len(app_ids), "app_ids": app_ids,
        "unique_intents": len(intents), "examples_per_intent": intents.most_common(),
        "unique_templates": len(templates), "near_dup_rate": round(1 - len(templates) / n, 3),
        "pct_multiturn": round(100 * multi / n, 1), "pct_negative_abstention": round(100 * neg / n, 1),
        "pct_dbfact_heavy": round(100 * dbfact / n, 1),
    }


def print_report(r):
    print(f"\n=== {r['name']}: {r['n']} examples ===")
    print(f"  unique application IDs: {r['unique_app_ids']}")
    print(f"  unique intents: {r['unique_intents']}  (top: {r['examples_per_intent'][:8]})")
    print(f"  unique question templates: {r['unique_templates']}  (near-dup rate: {r['near_dup_rate']:.1%})")
    print(f"  multi-turn: {r['pct_multiturn']}%  negative/abstention: {r['pct_negative_abstention']}%  "
          f"db-fact-heavy: {r['pct_dbfact_heavy']}%")


def main():
    old_tr = [r for r in load("train_augmented.jsonl") if old_ok(r)]
    old_va = [r for r in load("validation.jsonl") if old_ok(r)]
    print("old train", len(load("train_augmented.jsonl")), "->", len(old_tr), "| old validation", len(load("validation.jsonl")), "->", len(old_va))
    new_tr = sum((load(f) for f in ("train_allcodes_new.jsonl", "train_flinetslr_new.jsonl", "train_jurisdiction_new.jsonl")), [])
    new_va = sum((load(f) for f in ("validation_allcodes_new.jsonl", "validation_flinetslr_new.jsonl", "validation_jurisdiction_new.jsonl")), [])
    # F-Line / TSLR are two codes of 29: keep their share small (all "no such record" rows, 40% of the rest)
    new_tr = [r for r in new_tr if r["meta"].get("source") != "flinetslr" or r["meta"]["group"].endswith("_absent") or RNG.random() < 0.4]
    ok = lambda r: len(txt(r)) <= MAX_CHARS * 1.2 and not BAD_CS.search(txt(r))
    train = [r for r in old_tr + new_tr if ok(r)]
    train = cap_templates(train, cap=6)          # no question template dominates on a different app number
    RNG.shuffle(train)
    tkeys = {qkey(r, True) for r in train}
    val = [r for r in old_va + new_va if ok(r) and qkey(r, True) not in tkeys]
    val = cap_templates(val, cap=2)
    tests = {}
    for f in ("test_heldout_allcodes.jsonl", "test_heldout_flinetslr.jsonl", "test_heldout_jurisdiction.jsonl"):
        rows = [r for r in load(f) if qkey(r, False) not in {qkey(x, False) for x in train + val} and ok(r)]
        tests[f] = rows
    w = lambda name, rows, strip=False: (LT / name).write_text("".join(
        json.dumps({"messages": r["messages"]} if strip else r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    w("train_augmented_v2.jsonl", train); w("validation_v2.jsonl", val)
    w("train_v2_clean.jsonl", train, True); w("validation_v2_clean.jsonl", val, True)
    for f, rows in tests.items():
        w(f, rows)
    print("train", len(train), "validation", len(val), {k: len(v) for k, v in tests.items()})
    print("train sources:", Counter(r.get("meta", {}).get("source", "old") for r in train).most_common())

    # ---- leakage checks + the requested report, computed from the FINAL written files ----
    rtr, rva = report("train", train), report("val", val)
    print_report(rtr); print_report(rva)
    qk_leak = {qkey(r, True) for r in train} & {qkey(r, True) for r in val}
    app_leak = rtr["app_ids"] & rva["app_ids"]
    off_leak = ({r["meta"].get("officer") for r in train if "meta" in r} &
                {r["meta"].get("officer") for r in val if "meta" in r}) - {None}
    print(f"\n=== leakage checks (train vs validation) ===")
    print(f"  exact question+context overlap: {len(qk_leak)}")
    print(f"  shared application IDs: {len(app_leak)}")
    print(f"  shared officer identities: {len(off_leak)} {sorted(off_leak) if off_leak else ''}")
    all_test_rows = sum(tests.values(), [])
    rte = report("test (all heldout)", all_test_rows)
    print_report(rte)
    test_app_overlap = rte["app_ids"] & (rtr["app_ids"] | rva["app_ids"])
    print(f"  test application IDs also in train/val: {len(test_app_overlap)}")

    cs_hits = [r for r in train + val + all_test_rows if BAD_CS.search(txt(r))]
    print(f"\n0155 / MERGE check: {'PASS, none found' if not cs_hits else f'FAIL, {len(cs_hits)} row(s) still carry it'}")

    summary = {k: {kk: vv for kk, vv in v.items() if kk != "app_ids"} for k, v in
               {"train": rtr, "validation": rva, "test": rte}.items()}
    summary["leakage"] = {"train_val_exact_qkey_overlap": len(qk_leak), "train_val_app_id_overlap": len(app_leak),
                           "train_val_officer_overlap": sorted(off_leak), "test_app_id_overlap_with_train_or_val": len(test_app_overlap)}
    (LT / "dataset_report.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print("\nWrote lora_training/dataset_report.json")
    return train, val, tests


if __name__ == "__main__":
    main()
