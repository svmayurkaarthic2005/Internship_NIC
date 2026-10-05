"""
v3 = the clean v2 training set minus what the first evaluation showed was harmful, plus the targeted v3 correction examples.

Dropped from v2: every example whose assistant turn states the CHANNEL of a specific application while the prompt carries no channel
for it (the old application rows had no channel column, so those answers taught the model to guess one).

    python -m backend.sample_db.assemble_lora_v3
writes lora_training/train_v3_clean.jsonl / validation_v3_clean.jsonl (messages only) and train_v3.jsonl / validation_v3.jsonl (with meta)
The original evaluation files and bench_v3_extra.jsonl are never loaded for training.
"""
from __future__ import annotations

import json
import random
import re
from collections import Counter
from pathlib import Path

LT = Path(__file__).resolve().parents[2] / "lora_training"
RNG = random.Random(303)
CHAN = re.compile(r"came (?:in )?through|submitted through|came via|came in via|வழியாக வந்தது")  # "vazhiyaga vandhadhu"
APP = re.compile(r"\d{4}/\d{4}/\d{1,3}/\d+")


def load(f):
    p = LT / f
    return [json.loads(l) for l in p.open(encoding="utf-8")] if p.exists() else []


def txt(r): return json.dumps(r, ensure_ascii=False)


def guesses_channel(r):
    sysm = r["messages"][0]["content"]
    has_col = "Channel |" in sysm or "| Channel" in sysm or "Channel):" in sysm
    for m in r["messages"]:
        if m["role"] == "assistant" and CHAN.search(m["content"]) and APP.search(m["content"]) and not has_col:
            return True
    return False


def nz(s): return re.sub(r"\W+", " ", s.lower()).strip()


def key(r): return nz(r["messages"][1]["content"]) + "|" + nz(r["messages"][0]["content"])


def main():
    old_tr, old_va = load("train_augmented_v2.jsonl"), load("validation_v2.jsonl")
    keep_tr = [r for r in old_tr if not guesses_channel(r)]
    keep_va = [r for r in old_va if not guesses_channel(r)]
    print("v2 train", len(old_tr), "->", len(keep_tr), "(dropped", len(old_tr) - len(keep_tr), "channel-guessing rows) | v2 validation",
          len(old_va), "->", len(keep_va))
    train = keep_tr + load("train_v3_new.jsonl")
    RNG.shuffle(train)
    tk = {key(r) for r in train}
    val = [r for r in keep_va + load("validation_v3_new.jsonl") if key(r) not in tk]
    # also drop a validation example that is identical to a training example once every number is masked (a re-drawn copy)
    mask = lambda r: re.sub(r"\d+", "N", nz("||".join(m["content"] for m in r["messages"])))
    tm = {mask(r) for r in train}
    n0 = len(val)
    val = [r for r in val if mask(r) not in tm]
    print("validation rows dropped as masked copies of training rows:", n0 - len(val))
    bad = [r for r in train + val if re.search(r"(?<![\d/])0155(?![\d])|\bMERGE\b", txt(r))]
    assert not bad, "0155 / MERGE found"
    w = lambda name, rows, strip: (LT / name).write_text("".join(
        json.dumps({"messages": r["messages"]} if strip else r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    w("train_v3.jsonl", train, False); w("validation_v3.jsonl", val, False)
    w("train_v3_clean.jsonl", train, True); w("validation_v3_clean.jsonl", val, True)
    print("train_v3", len(train), "validation_v3", len(val))
    print("train sources:", Counter(r.get("meta", {}).get("source", "old") for r in train).most_common(6))
    print("v3 groups:", Counter(r["meta"]["group"] for r in train if r.get("meta", {}).get("source") == "v3"))


if __name__ == "__main__":
    main()
