"""
Final assistant-label correctness audit for lora_training/train_v2_clean.jsonl (or any jsonl of
{"messages": [...]} / {"messages": [...], "meta": {...}} rows).

Rule being checked: "answer only from the supplied authoritative context; never invent a value."
For every assistant turn, every number it states must already be visible to the model at that
point in the conversation (the system prompt, or an earlier turn) -- or be a simple sum /
difference / percentage of numbers that are, computed within one register-figures block (the
shape every generator in this pipeline actually uses: approved+pending+rejected=total, k/t=pct).
A number that fails both is flagged.

Separately, for the two sources where an owner/applicant NAME can appear at all (flinetslr, and
any surviving "old" rows -- the allcodes/jurisdiction generators never model a person), every
Title-Case run in an assistant answer must appear in the context too, after subtracting a
stoplist of role/office/boilerplate phrases that are not names.

This is a static, automated first pass -- it flags candidates, it does not claim certainty. Each
category's examples should be read before trusting the auto-classification.

    python -m backend.sample_db.audit_train_grounding
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LT = ROOT / "lora_training"
sys.path.insert(0, str(ROOT))
from backend.sample_db.build_lora_dataset_allcodes import CODES  # noqa: E402

NUM_RE = re.compile(r"\d[\d,]*\.?\d*")
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2})?|\d{4}/\d{4}/\d{1,3}/\d{3,9}")
YEAR_RE = re.compile(r"^20\d\d$")
JUR_REFUSAL_RE = re.compile(r"outside your (assigned )?jurisdiction|not (in|within) your jurisdiction|"
                             r"no such application|does not exist in the register", re.I)
NAME_BIGRAM_RE = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})\b")

STOP_PHRASES = {
    "Sub Inspector", "Inspector Surveyor", "Sub Inspector Surveyor", "Senior Draughtsman",
    "Deputy Inspector", "Deputy Inspector Surveyor", "Zonal Level", "Zonal Level Tahsildar",
    "Common Service", "Common Service Centre", "Citizen Portal", "Sub Registrar", "Service Code",
    "Application No", "Application Rows", "Field Visit", "Not Involving", "Involving Sub",
    "Involving Sub Division", "Land Type", "Sub Division", "TAMILNILAM Urban", "Register Figures",
    "By Ward", "For Code", "Tamil Nadu", "Sub Inspector's", "Application Status", "Status Pending",
    "Status Approved", "Status Rejected", "Ward Held", "Jurisdiction Wards", "Record Data",
    "Payment Mode", "House Tax", "Old TSLR", "Proof Of", "Senior Draughtsman's",
}


def load(name):
    p = LT / name
    return [json.loads(l) for l in p.open(encoding="utf-8")] if p.exists() else []


def nums_in(text):
    return {m.replace(",", "") for m in NUM_RE.findall(text)}


def same_line_derivable(x, context_text):
    """x is derivable as a sum / difference / percentage of numbers that co-occur on one line of
    context_text (the shape every register-figures / count-breakdown line in this pipeline uses)."""
    try:
        xv = float(x)
    except ValueError:
        return False
    for line in context_text.split("\n"):
        vals = [float(v) for v in nums_in(line) if v]
        if len(vals) < 2:
            continue
        for i, a in enumerate(vals):
            for b in vals[i + 1:]:
                if abs(a + b - xv) < 0.05 or abs(a - b - xv) < 0.05 or abs(b - a - xv) < 0.05:
                    return True
                if b and abs(round(100 * a / b, 1) - xv) < 0.05:
                    return True
                if a and abs(round(100 * b / a, 1) - xv) < 0.05:
                    return True
        if len(vals) >= 2 and abs(sum(vals[:-1]) - vals[-1]) < 0.05:   # a+b+c=total on one line
            return True
    return False


def audit_row(r, idx):
    """-> list of (category, detail) issues found in this row."""
    issues = []
    msgs = r["messages"]
    system = msgs[0]["content"]
    known = system
    i = 1
    first_app = None
    m0 = DATE_RE.search(known)
    while i < len(msgs):
        user = msgs[i]["content"]
        asst = msgs[i + 1]["content"] if i + 1 < len(msgs) else ""
        known_before = known + "\n" + user
        app_here = DATE_RE.findall(user) or DATE_RE.findall(known)
        if app_here and not first_app:
            first_app = app_here[0]

        # category 5: jurisdiction / "does not exist" refusal after the application was already
        # established (named earlier in this same conversation) -- CONTEXT_LOSS
        if JUR_REFUSAL_RE.search(asst) and first_app and first_app in known_before and first_app in asst:
            issues.append(("CONTEXT_LOSS", f"turn {i // 2 + 1}: refuses/denies {first_app} after it was already established"))

        # numeric grounding: category 2/3/7/8/9. Reference lookups (service-code meaning / fee /
        # SLA / field-visit requirement) state fixed, documented facts, not register data, so they
        # need no register context -- only genuinely data-shaped answers (count/list/channel/ward/
        # month/pct/app/chain/cross/context) are held to "every number must be grounded".
        group = r.get("meta", {}).get("group", "")
        if group in ("meaning", "fv", "fee", "sla"):
            i += 2; known = known_before + "\n" + asst; continue
        asst_nums = nums_in(asst)
        known_nums = nums_in(known_before)
        for n in asst_nums:
            if n in known_nums or YEAR_RE.match(n) or len(n) <= 1 or n in CODES:
                continue
            if same_line_derivable(n, known_before) or same_line_derivable(n, asst):
                continue
            issues.append(("UNSUPPORTED_FIELD", f"turn {i // 2 + 1}: value '{n}' in answer not found in, or derivable from, prior context"))

        # names, only meaningful where a person can appear at all
        if r.get("meta", {}).get("source") in ("flinetslr", "old", None) or "meta" not in r:
            for cand in NAME_BIGRAM_RE.findall(asst):
                if cand in STOP_PHRASES or any(cand in sp or sp in cand for sp in STOP_PHRASES):
                    continue
                if cand not in known_before:
                    issues.append(("WRONG_LABEL", f"turn {i // 2 + 1}: name-like phrase '{cand}' in answer not present in context"))

        known = known_before + "\n" + asst
        i += 2
    return issues


def main():
    rows = load("train_augmented_v2.jsonl")   # has meta, for source-aware checks; same rows as train_v2_clean.jsonl
    if not rows:
        print("train_augmented_v2.jsonl not found"); return
    by_cat = Counter()
    bad_rows = {}
    for idx, r in enumerate(rows):
        issues = audit_row(r, idx)
        if issues:
            bad_rows[idx] = issues
            for cat, _ in issues:
                by_cat[cat] += 1

    print(f"total rows scanned: {len(rows)}")
    print(f"rows with at least one flagged issue: {len(bad_rows)}")
    print(f"issues by category: {dict(by_cat)}")

    examples_shown = Counter()
    example_lines = []
    for idx, issues in bad_rows.items():
        for cat, detail in issues:
            if examples_shown[cat] < 15:
                examples_shown[cat] += 1
                r = rows[idx]
                src = r.get("meta", {}).get("source", "?")
                example_lines.append(f"\n--- {cat} (row {idx}, source={src}) ---")
                example_lines.append(f"  {detail}")
                example_lines.append(f"  system[:250]: {r['messages'][0]['content'][:250]!r}")
                for m in r["messages"][1:]:
                    example_lines.append(f"  {m['role']}: {m['content'][:250]!r}")

    (LT / "train_audit_examples.txt").write_text("\n".join(example_lines), encoding="utf-8")
    out = {"total_rows": len(rows), "bad_rows": len(bad_rows), "by_category": dict(by_cat),
           "bad_row_indices": sorted(bad_rows.keys())}
    (LT / "train_audit_report.json").write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print("Wrote lora_training/train_audit_report.json and train_audit_examples.txt")


if __name__ == "__main__":
    main()
