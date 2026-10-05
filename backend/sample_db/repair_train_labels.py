"""
Post-hoc repair for the Mask() label-inconsistency bug (see build_lora_dataset_flinetslr.py's
Mask.pool, now fixed for future runs): the SAME real applicant name/address got two DIFFERENT
fake substitutes when it appeared under an English label ("Name") in the context card and a
Tamil one ("peyar") in an answer card, because the old cache key was the whole matched line, not
the captured value. That is a contradiction the masking step invented, not a real one -- so the
fix is to make every row internally consistent with its OWN context, not to throw the row away.

Rule: the context (system message) is authoritative. For every row, read the first Name / Address
field line in the system message as canonical, then overwrite any OTHER Name / Address field line
in the same row (any later message) to match it -- but ONLY when the row discusses exactly one
real application, since a legitimate multi-owner answer can show a second name on purpose.

    python -m backend.sample_db.repair_train_labels
writes a repaired lora_training/train_augmented_v2.jsonl / train_v2_clean.jsonl in place.
Validation and test files are never touched.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

LT = Path(__file__).resolve().parents[2] / "lora_training"
APP_RE = re.compile(r"\d{4}/\d{4}/\d{1,3}/\d{3,9}")
FIELD_RE = re.compile(r"^((?:Name|பெயர்|Address|முகவரி)\s*\|\s*)(.*?)(\s*\|\s*)$", re.M)


def canonical_fields(system_text):
    out = {}
    for m in FIELD_RE.finditer(system_text):
        label = "name" if m.group(1).strip().startswith(("Name", "பெயர்")) else "address"
        out.setdefault(label, m.group(2))
    return out


def repair_row(r):
    msgs = r["messages"]
    apps = set()
    for m in msgs:
        apps |= set(APP_RE.findall(m["content"]))
    if len(apps) != 1:
        return r, False   # more than one application in view: do not guess which name goes where
    canon = canonical_fields(msgs[0]["content"])
    if not canon:
        return r, False
    changed = False
    for m in msgs[1:]:
        def fix(mm):
            nonlocal changed
            label = "name" if mm.group(1).strip().startswith(("Name", "பெயர்")) else "address"
            want = canon.get(label)
            if want is not None and mm.group(2) != want:
                changed = True
                return mm.group(1) + want + mm.group(3)
            return mm.group(0)
        m["content"] = FIELD_RE.sub(fix, m["content"])
    return r, changed


def main():
    rows = [json.loads(l) for l in (LT / "train_augmented_v2.jsonl").open(encoding="utf-8")]
    n_fixed = n_candidates = 0
    for i, r in enumerate(rows):
        if r.get("meta", {}).get("source") != "flinetslr":
            continue
        if not any(FIELD_RE.search(m["content"]) for m in r["messages"][1:]):
            continue
        n_candidates += 1
        rows[i], changed = repair_row(r)
        if changed:
            n_fixed += 1

    print(f"flinetslr rows with a Name/Address line in an answer: {n_candidates}")
    print(f"rows with a real mismatch, repaired to match their own context: {n_fixed}")

    (LT / "train_augmented_v2.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    (LT / "train_v2_clean.jsonl").write_text(
        "".join(json.dumps({"messages": r["messages"]}, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    print(f"rewrote train_augmented_v2.jsonl / train_v2_clean.jsonl ({len(rows)} rows, unchanged count)")


if __name__ == "__main__":
    main()
