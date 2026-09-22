"""
Final hygiene pass on train_augmented.jsonl / validation.jsonl before QLoRA
training. Supersedes the ad-hoc clean_train_final.py (which is what shrank
train_augmented.jsonl from 3296 -> 3063 lines earlier this session).

Fixes four things that would actively hurt what the LoRA adapter learns:

1. HTML entities (&amp; &lt; &gt; &quot; &#39;) leaked into ~65 assistant
   answers -- the model would learn to output escaped HTML in a plain chat UI.
2. One 7.7KB untruncated owner-listing answer (108 owners, no omission note)
   -- inconsistent with the "...(N more row(s) omitted)" convention the other
   892 listing answers use, and long enough to get silently cut mid-list by
   train_qlora.py's MAX_SEQ_LEN=1024.
3. Train/validation leakage -- 21 validation examples were byte-identical to
   a training example, and 128/180 shared the same first user question with
   one. eval_loss over a leaked validation set can't tell memorization from
   generalization, and SFTConfig uses eval_loss to pick the saved checkpoint.
4. ~970 exact-duplicate training rows (same messages, different officer only
   because the answer text happened to be identical, e.g. "no approved
   applications this month" x3) -- collapsed to one copy each; doesn't remove
   legitimate repeats of boilerplate answers under DIFFERENT questions.

Backs up both files to *.bak before writing. Re-run is safe (idempotent).
"""
import html
import json
import re
import shutil
from pathlib import Path

TRAIN = Path("train_augmented.jsonl")
VAL = Path("validation.jsonl")
MAX_OWNERS_SHOWN = 8


def load(path):
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def save(path, rows):
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def unescape_all(rows):
    n = 0
    for r in rows:
        for m in r["messages"]:
            new = html.unescape(m["content"])
            if new != m["content"]:
                n += 1
                m["content"] = new
    return n


def first_user_text(r):
    for m in r["messages"]:
        if m["role"] == "user":
            return m["content"].strip().lower()
    return None


def message_key(r):
    return json.dumps(r["messages"], sort_keys=True)


_OWNER_LINE_RE = re.compile(r"^  • .+$", re.MULTILINE)
_OWNER_HEADER_RE = re.compile(r"^(Owners for .+?\()(\d+) record\(s\)\):$")


def truncate_owner_listing(rows):
    """Cap any un-truncated bullet-list owner dump to MAX_OWNERS_SHOWN lines,
    appending the same omission convention used by the table listings."""
    fixed = 0
    for r in rows:
        last = r["messages"][-1]
        if last["role"] != "assistant":
            continue
        lines = last["content"].split("\n")
        if not lines or not _OWNER_HEADER_RE.match(lines[0]):
            continue
        owner_lines = [l for l in lines[1:] if _OWNER_LINE_RE.match(l)]
        if len(owner_lines) <= MAX_OWNERS_SHOWN:
            continue
        total = len(owner_lines)
        kept = owner_lines[:MAX_OWNERS_SHOWN]
        omitted = total - MAX_OWNERS_SHOWN
        new_body = [lines[0]] + kept + [f"  ... ({omitted} more owner(s) omitted)"]
        last["content"] = "\n".join(new_body)
        fixed += 1
    return fixed


def main():
    shutil.copy(TRAIN, TRAIN.with_suffix(".jsonl.bak"))
    shutil.copy(VAL, VAL.with_suffix(".jsonl.bak"))

    train = load(TRAIN)
    val = load(VAL)
    print(f"loaded train={len(train)} val={len(val)}")

    n1 = unescape_all(train) + unescape_all(val)
    print(f"HTML-unescaped {n1} message(s)")

    n2 = truncate_owner_listing(train) + truncate_owner_listing(val)
    print(f"truncated {n2} oversized owner listing(s)")

    val_msg_keys = {message_key(r) for r in val}
    val_q_keys = {first_user_text(r) for r in val if first_user_text(r)}

    before = len(train)
    train = [r for r in train if message_key(r) not in val_msg_keys
             and first_user_text(r) not in val_q_keys]
    n3 = before - len(train)
    print(f"dropped {n3} train example(s) leaking into validation "
          f"(exact match or same first question)")

    seen = set()
    deduped = []
    for r in train:
        k = message_key(r)
        if k in seen:
            continue
        seen.add(k)
        deduped.append(r)
    n4 = len(train) - len(deduped)
    train = deduped
    print(f"dropped {n4} exact-duplicate train example(s)")

    save(TRAIN, train)
    save(VAL, val)
    print(f"final train={len(train)} val={len(val)}")


if __name__ == "__main__":
    main()
