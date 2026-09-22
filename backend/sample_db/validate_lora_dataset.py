"""
Final quality-gate validation over the cleaned LoRA dataset before it is
handed to a training run. Distinct from `clean_lora_dataset.py` (which
DECIDES what to keep): this only CHECKS the result and reports every issue
it finds, so a problem the cleaning pass missed is caught here rather than
baked into `train.jsonl` silently.

Checks, in order:
  1. JSON well-formedness + message-schema validity (system/user/assistant
     shape, alternating roles, non-empty content) for every record
  2. Exact duplicate user-turn sequences (should be ~0 post-cleaning --
     any survivor here is a real gap in the cleaning pass, not expected)
  3. Near-duplicate conversations (same user text after collapsing
     whitespace/punctuation -- catches the "trailing space" / "extra
     newline" class the exact-key dedup could miss)
  4. Database-result memorization risk: same real number/date repeated
     across many DIFFERENT questions (a concrete count the model could
     learn as a fixed fact rather than a question-shaped placeholder)
  5. Sensitive/placeholder leaks: unredacted application numbers, CAN
     numbers, mobile numbers, Aadhaar-shaped numbers, or anything that
     looks like a password/secret
  6. Intent distribution (from `meta.intent` / `meta.group` / `meta.scenario`)
  7. Tamil/Tanglish and negation coverage, with a sample of each so the
     report is checkable by eye, not just a count

Pure text processing -- no DB, no LLM.

Usage:
    python -m backend.sample_db.validate_lora_dataset lora_dataset_clean.jsonl
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\w\s]")


def _user_text(ex: dict) -> str:
    return " || ".join(m["content"] for m in ex.get("messages", [])
                        if m.get("role") == "user")


def _norm_loose(s: str) -> str:
    return _WS_RE.sub(" ", _PUNCT_RE.sub("", s.lower())).strip()


def _last_answer(ex: dict) -> str:
    for m in reversed(ex.get("messages", [])):
        if m.get("role") == "assistant":
            return m["content"]
    return ""


# ── 1. Schema validity ───────────────────────────────────────────────────────
def check_schema(ex: dict, idx: int) -> list[str]:
    errs = []
    msgs = ex.get("messages")
    if not isinstance(msgs, list) or not msgs:
        return [f"record {idx}: no 'messages' list"]
    if msgs[0].get("role") != "system":
        errs.append(f"record {idx}: first message is not 'system'")
    expect = "user"
    for i, m in enumerate(msgs[1:], start=1):
        role, content = m.get("role"), m.get("content")
        if role not in ("user", "assistant"):
            errs.append(f"record {idx} msg {i}: unexpected role {role!r}")
            continue
        if role != expect:
            errs.append(f"record {idx} msg {i}: expected {expect}, got {role}")
        expect = "assistant" if role == "user" else "user"
        if not content or not content.strip():
            errs.append(f"record {idx} msg {i}: empty content")
    if msgs[-1].get("role") != "assistant":
        errs.append(f"record {idx}: conversation does not end on assistant")
    return errs


# ── 4/5. Real-record leak / memorization patterns ────────────────────────────
_APP_NO_RE = re.compile(r"\b\d{4}/(?:0153|0154|0155)/\d{2}/\d{6}\b")
_APP_NO_UNREDACTED_RE = re.compile(r"\b\d{4}/(?:0153|0154|0155)/\d{2}/(?!000000\b)\d{6}\b")
_CAN_RE = re.compile(r"(?<!\d)(\d{12}|\d{15})(?!\d)")
_CAN_UNREDACTED_RE = re.compile(r"(?<!\d)(?!0{12}\b)(?!0{15}\b)(\d{12}|\d{15})(?!\d)")
_MOBILE_RE = re.compile(r"(?<!\d)([6-9]\d{9})(?!\d)")
_MOBILE_UNREDACTED_RE = re.compile(r"(?<!\d)(?!9000000000\b)([6-9]\d{9})(?!\d)")
# Excludes the all-zero shape the redaction pass already produces for a
# masked CAN/IGRS number ("000000000000") -- that is the SAFE placeholder,
# not a leak, and without this exclusion every already-redacted IGRS-number
# column in the dataset was re-flagged as if it were a real Aadhaar number.
_AADHAAR_RE = re.compile(r"(?<!\d)(?!(?:0\s?){12})\d{4}\s?\d{4}\s?\d{4}(?!\d)")
_SECRET_RE = re.compile(r"\b(password|passwd|secret\s*key|api[_\s]*key)\b", re.IGNORECASE)
_COUNT_STATEMENT_RE = re.compile(
    r"\b(there\s+(?:are|is)\s+(\d+)|found\s+<strong>(\d+)</strong>)", re.IGNORECASE)


def main(clean_path: Path, out_train: Path, out_val: Path,
         val_fraction: float, report_path: Path) -> int:
    lines = clean_path.read_text(encoding="utf-8").splitlines()
    records: list[dict] = []
    json_errors: list[str] = []
    for i, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as e:
            json_errors.append(f"line {i}: {e}")

    schema_errors: list[str] = []
    for i, ex in enumerate(records):
        schema_errors += check_schema(ex, i)

    # 2. exact duplicate user-turn sequences
    exact_seen: dict[str, int] = {}
    exact_dupes: list[tuple[int, int]] = []
    for i, ex in enumerate(records):
        key = _user_text(ex)
        if key in exact_seen:
            exact_dupes.append((exact_seen[key], i))
        else:
            exact_seen[key] = i

    # 3. near-duplicate (loose normalization: case/punct/whitespace only)
    loose_seen: dict[str, int] = {}
    near_dupes: list[tuple[int, int]] = []
    for i, ex in enumerate(records):
        key = _norm_loose(_user_text(ex))
        if not key:
            continue
        if key in loose_seen and loose_seen[key] != exact_seen.get(_user_text(ex)):
            near_dupes.append((loose_seen[key], i))
        elif key not in loose_seen:
            loose_seen[key] = i

    # 4. memorization risk: an identical concrete count repeated across many
    #    DIFFERENT questions (as opposed to the same question -- that's
    #    already the exact-dupe case above)
    count_to_questions: dict[str, set] = defaultdict(set)
    for ex in records:
        ans = _last_answer(ex)
        for m in _COUNT_STATEMENT_RE.finditer(ans):
            n = m.group(2) or m.group(3)
            if n:
                count_to_questions[n].add(_norm_loose(_user_text(ex)))
    memorization_risk = {n: len(qs) for n, qs in count_to_questions.items() if len(qs) >= 15}

    # 5. unredacted sensitive values
    leaks: list[str] = []
    for i, ex in enumerate(records):
        text = " ".join(m["content"] for m in ex.get("messages", []))
        if _APP_NO_UNREDACTED_RE.search(text):
            leaks.append(f"record {i}: unredacted application number")
        if _CAN_UNREDACTED_RE.search(text):
            leaks.append(f"record {i}: unredacted CAN-length number")
        if _MOBILE_UNREDACTED_RE.search(text):
            leaks.append(f"record {i}: unredacted mobile number")
        if _AADHAAR_RE.search(text):
            leaks.append(f"record {i}: Aadhaar-shaped number present")
        if _SECRET_RE.search(text):
            leaks.append(f"record {i}: secret/password keyword present")

    # 6. intent distribution
    intent_counts: Counter[str] = Counter()
    for ex in records:
        meta = ex.get("meta", {}) or {}
        intent_counts[meta.get("intent") or meta.get("group")
                       or meta.get("scenario") or meta.get("source") or "unknown"] += 1

    # 7. Tamil/Tanglish/negation coverage + samples
    tamil_re = re.compile(r"[஀-௿]")
    tanglish_re = re.compile(
        r"\b(evlo|evvalavu|enna|eppo|yaaru|yaar|naan|iruku|irukku|kaami|"
        r"venum|illama|vendam|solra|adhu|idhu|panu|pannu)\b", re.IGNORECASE)
    negation_re = re.compile(
        r"\b(not|without|except|excluding|exclude|neither|nor"
        r"|don'?t|do\s+not|leave\s+out|skip|venam|vendam|illama)\b",
        re.IGNORECASE)
    tamil_examples, tanglish_examples, negation_examples = [], [], []
    for i, ex in enumerate(records):
        u = _user_text(ex)
        if tamil_re.search(u):
            tamil_examples.append(i)
        elif tanglish_re.search(u):
            tanglish_examples.append(i)
        if negation_re.search(u):
            negation_examples.append(i)

    # ── Remove anything this pass found wrong, write the truly final split ──
    bad_idx = {i for i in range(len(records))
               if schema_errors and any(f"record {i}" in e or f"record {i}:" in e for e in schema_errors)}
    bad_idx |= {j for _, j in exact_dupes}       # keep first occurrence only
    bad_idx |= {j for _, j in near_dupes}
    bad_idx |= {i for i, ex in enumerate(records)
                if any(f"record {i}:" in leak for leak in leaks)}
    kept = [ex for i, ex in enumerate(records) if i not in bad_idx]

    n_val = max(1, int(len(kept) * val_fraction))
    val_set, train_set = kept[:n_val], kept[n_val:]
    out_train.write_text(
        "".join(json.dumps(ex, ensure_ascii=False) + "\n" for ex in train_set),
        encoding="utf-8")
    out_val.write_text(
        "".join(json.dumps(ex, ensure_ascii=False) + "\n" for ex in val_set),
        encoding="utf-8")

    report = {
        "input_count": len(records),
        "json_parse_errors": json_errors,
        "schema_errors": schema_errors,
        "exact_duplicate_pairs": len(exact_dupes),
        "near_duplicate_pairs": len(near_dupes),
        "memorization_risk_counts": memorization_risk,
        "sensitive_value_leaks": leaks,
        "intent_distribution": dict(intent_counts.most_common()),
        "tamil_count": len(tamil_examples),
        "tanglish_count": len(tanglish_examples),
        "negation_count": len(negation_examples),
        "tamil_sample_indices": tamil_examples[:5],
        "tanglish_sample_indices": tanglish_examples[:5],
        "negation_sample_indices": negation_examples[:5],
        "removed_for_final_split": len(bad_idx),
        "final_train_count": len(train_set),
        "final_val_count": len(val_set),
    }
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"input:                 {len(records)}")
    print(f"JSON parse errors:     {len(json_errors)}")
    print(f"schema errors:         {len(schema_errors)}")
    print(f"exact duplicate pairs: {len(exact_dupes)}")
    print(f"near duplicate pairs:  {len(near_dupes)}")
    print(f"memorization-risk counts (>=15 distinct questions share the same number): {memorization_risk}")
    print(f"sensitive leaks:       {len(leaks)}")
    print(f"tamil / tanglish / negation: {len(tamil_examples)} / {len(tanglish_examples)} / {len(negation_examples)}")
    print(f"\nintent distribution (top 15):")
    for k, v in intent_counts.most_common(15):
        print(f"  {k!s:<28} {v}")
    print(f"\nremoved for final split: {len(bad_idx)}")
    print(f"final train / val:       {len(train_set)} / {len(val_set)}")
    if json_errors or schema_errors or leaks:
        print("\n*** ISSUES FOUND -- see report for detail ***")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("clean", type=Path)
    parser.add_argument("--train-out", type=Path, default=Path("train.jsonl"))
    parser.add_argument("--val-out", type=Path, default=Path("validation.jsonl"))
    parser.add_argument("--val-fraction", type=float, default=0.05)
    parser.add_argument("--report", type=Path, default=Path("lora_dataset_validation_report.json"))
    args = parser.parse_args()
    raise SystemExit(main(args.clean, args.train_out, args.val_out,
                          args.val_fraction, args.report))
