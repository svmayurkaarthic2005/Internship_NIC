# ==============================================================================
# Oversamples the service-code glossary facts (ISD=0154, NISD=0153, MERGE=0155,
# and the other 27 TAMILNILAM codes) into the training set.
#
# Why: the fine-tuned model recited the ISD glossary entry from
# lora_dataset_final.jsonl.json), while the actual training file (train.jsonl,
# 1585 examples) carried each code fact only a handful of times -- not enough
# repetition for an 8B LoRA to pin an exact 4-digit number.
#
# This script pulls every verified-correct glossary example for the three
# real service codes (ISD/NISD/MERGE) plus the other codes from the full
# cleaned dataset, deduplicates by (question, answer), generates a few cheap
# paraphrase wrappers per fact (no DB call needed -- the answer text doesn't
# depend on officer identity for these facts), and appends N copies into
# train.jsonl so gradient signal on these exact facts is much stronger.
# ==============================================================================

import json
import random
from pathlib import Path

random.seed(0)

SRC = Path("lora_dataset_final.jsonl")
TRAIN = Path("train.jsonl")
OUT = Path("train_augmented.jsonl")

# codes the app's applications table actually admits -- get the heaviest reinforcement
PRIMARY_TERMS = ["ISD", "NISD", "MERGE application", "service code 0153",
                  "service code 0154", "service code 0155"]
PRIMARY_REPEATS = 20   # copies per unique (question,answer) pair
OTHER_REPEATS = 6      # the remaining glossary terms get lighter reinforcement

PARAPHRASE_WRAPPERS = [
    "{q}",
    "Please tell me: {q}",
    "Can you explain, {q}",
    "I need to know -- {q}",
    "Quick question: {q}",
    "{q} Explain briefly.",
    "For the record, {q}",
]


def load_jsonl(path):
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def is_glossary(ex):
    return ex.get("meta", {}).get("source") == "glossary"


def user_text(ex):
    for m in ex["messages"]:
        if m["role"] == "user":
            return m["content"]
    return ""


def assistant_text(ex):
    for m in ex["messages"]:
        if m["role"] == "assistant":
            return m["content"]
    return ""


def matches_terms(text, terms):
    low = text.lower()
    return any(t.lower() in low for t in terms)


def main():
    all_examples = load_jsonl(SRC)
    train_examples = load_jsonl(TRAIN)

    glossary = [ex for ex in all_examples if is_glossary(ex)]

    # dedupe by (question, answer) -- same fact repeats verbatim across officers
    seen = {}
    for ex in glossary:
        key = (user_text(ex), assistant_text(ex))
        seen.setdefault(key, ex)
    unique_facts = list(seen.values())

    primary = [ex for ex in unique_facts if matches_terms(user_text(ex), PRIMARY_TERMS)]
    other = [ex for ex in unique_facts if ex not in primary]

    print(f"Unique glossary facts: {len(unique_facts)} "
          f"(primary codes: {len(primary)}, other: {len(other)})")

    reinforcement = []

    def add_repeats(ex, n):
        q = user_text(ex)
        a = assistant_text(ex)
        wrappers = random.sample(PARAPHRASE_WRAPPERS, min(n, len(PARAPHRASE_WRAPPERS)))
        # pad out with the plain form repeated if n > number of wrappers
        while len(wrappers) < n:
            wrappers.append("{q}")
        for w in wrappers[:n]:
            new_ex = {
                "messages": [
                    {"role": "system", "content": next(
                        m["content"] for m in ex["messages"] if m["role"] == "system")},
                    {"role": "user", "content": w.format(q=q)},
                    {"role": "assistant", "content": a},
                ],
                "meta": {**ex.get("meta", {}), "source": "glossary_reinforced"},
            }
            reinforcement.append(new_ex)

    for ex in primary:
        add_repeats(ex, PRIMARY_REPEATS)
    for ex in other:
        add_repeats(ex, OTHER_REPEATS)

    print(f"Reinforcement examples generated: {len(reinforcement)}")

    combined = train_examples + reinforcement
    random.shuffle(combined)

    with OUT.open("w", encoding="utf-8") as f:
        for ex in combined:
            f.write(json.dumps(ex, ensure_ascii=False) + "\n")

    print(f"Wrote {len(combined)} examples to {OUT} "
          f"(was {len(train_examples)}, +{len(reinforcement)})")


if __name__ == "__main__":
    main()
