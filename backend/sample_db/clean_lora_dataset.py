"""
Turn the raw generated LoRA dataset into a clean QLoRA training set.

The raw file (`lora_dataset_final.jsonl`) was built by running every question
against THREE seeded officers and, for the follow-up sweep, every (setup x
follow-up) combination -- so a large fraction of it is the same linguistic
pattern repeated with a different officer's real counts/dates/wards standing
in for the only thing that actually varies. Fine-tuning on that teaches the
model to associate a phrasing with a *specific number* it saw three times
over, which is exactly the memorization risk QLoRA must not learn: those
numbers change on the next reseed, and PostgreSQL/RAG stays the sole source
of truth for them at inference time. This script's job is to keep the
*linguistic* variety (phrasing, typos, Tamil/Tanglish, scope shape,
follow-up structure) while collapsing the repeats that differ only in which
officer's data answered them.

Never touches the raw file. Reads `lora_dataset_final.jsonl`, writes:
  lora_dataset_clean.jsonl        the deduplicated, categorized, capped set
  lora_dataset_train.jsonl        95% split
  lora_dataset_val.jsonl          5% split
  lora_dataset_clean_report.json  the machine-readable version of the report

Pure text processing -- no DB, no LLM -- so it costs nothing to re-run after
the raw file changes.

Usage:
    python -m backend.sample_db.clean_lora_dataset
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
from collections import Counter
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── Normalization for duplicate detection ────────────────────────────────────
# Two examples are the "same pattern" when their USER turns are identical
# after normalizing whitespace/case -- this is deliberately NOT fuzzy (no
# typo-folding, no digit-stripping): a typo'd phrasing or a different ward
# number in the QUESTION itself is exactly the linguistic variation this
# dataset must keep. What collapses is the case documented above: the same
# user text run three times, once per officer, producing three near-
# identical conversations that differ only in the assistant's real numbers.
_WS_RE = re.compile(r"\s+")


def _user_key(ex: dict) -> str:
    # Officer is part of the key: the same literal question text asked of
    # different officers gets different (jurisdiction-dependent) answers, so
    # collapsing across officers here silently discarded every officer's
    # answer but whichever one happened to be generated first.
    parts = [m["content"].strip().lower() for m in ex.get("messages", [])
              if m.get("role") == "user"]
    officer = (ex.get("meta", {}) or {}).get("officer", "")
    return officer + "||" + _WS_RE.sub(" ", " || ".join(parts))


def _last_answer(ex: dict) -> str:
    for m in reversed(ex.get("messages", [])):
        if m.get("role") == "assistant":
            return m["content"]
    return ""


_TAMIL_RE = re.compile(r"[஀-௿]")
_TANGLISH_HINTS = re.compile(
    r"\b(evlo|evvalavu|enna|eppo|yaaru|yaar|naan|namma|iruku|irukku|kaami|"
    r"venum|maasam|illama|vendam|solra|adhu|idhu|panu|pannu|kaatu|kaattu)\b",
    re.IGNORECASE)
_TYPO_HINT_SOURCES = {"typo"}
_STATUS_WORDS_RE = re.compile(
    r"\b(pending|approved|rejected|escalated|in[ _-]?progress|status)\b",
    re.IGNORECASE)
_TYPE_WORDS_RE = re.compile(r"\b(isd|nisd|merge)\b", re.IGNORECASE)
_SCOPE_WORDS_RE = re.compile(
    r"\b(ward|block|district|taluk|town|jurisdiction)\b", re.IGNORECASE)
_COUNT_WORDS_RE = re.compile(
    r"\b(how many|count|total|no\.? of|number of)\b", re.IGNORECASE)
_LIST_WORDS_RE = re.compile(r"\b(show|list|display|view|give)\b", re.IGNORECASE)
# "exclude" (not just "excluding"), "don't"/"do not", "leave out", "skip",
# and the Tanglish "venam"/"vendam"/"illama" shapes -- the same vocabulary
# `followup_context._EXCLUDE_TRIGGER_RE` recognizes functionally, matched
# here too so the report's negation COUNT reflects what the negation
# handling code actually covers instead of only the "not/without" subset.
_NEGATION_RE = re.compile(
    r"\b(not|without|except|excluding|exclude|neither|nor|no\s+need"
    r"|don'?t|do\s+not|leave\s+out|skip|venam|vendam|illama)\b",
    re.IGNORECASE)
_OUT_OF_SCOPE_HINTS = {"out_of_scope", "error_ambiguity"}
_BUSINESS_RULE_HINTS = {"workflow_corpus", "faq_english", "faq_tamil",
                        "land_rules.txt", "service_code", "service_name",
                        "glossary"}


def categorize(ex: dict) -> list[str]:
    """Every category tag that applies -- an example can carry several."""
    meta = ex.get("meta", {}) or {}
    user_text = " ".join(m["content"] for m in ex.get("messages", [])
                          if m.get("role") == "user")
    tags = []

    if meta.get("source") in _TYPO_HINT_SOURCES:
        tags.append("typo_noisy")
    if _TAMIL_RE.search(user_text):
        tags.append("tamil")
    elif _TANGLISH_HINTS.search(user_text):
        tags.append("tanglish")
    if _TYPE_WORDS_RE.search(user_text):
        tags.append("type_isd_nisd_merge")
    if _SCOPE_WORDS_RE.search(user_text):
        tags.append("scope_ward_block_jurisdiction")
    if _COUNT_WORDS_RE.search(user_text):
        tags.append("count_query")
    elif _LIST_WORDS_RE.search(user_text):
        tags.append("list_query")
    if _NEGATION_RE.search(user_text):
        tags.append("negation")
    if len([m for m in ex.get("messages", []) if m.get("role") == "user"]) > 1:
        tags.append("followup_conversation")
    if (meta.get("source") in _OUT_OF_SCOPE_HINTS
            or meta.get("group") == "out_of_scope"
            or meta.get("scenario") == "contentless_and_capability_start"):
        tags.append("out_of_scope_refusal")
    if (meta.get("source") in _BUSINESS_RULE_HINTS
            or meta.get("group") == "workflow_knowledge"):
        tags.append("business_rule")
    if not tags:
        tags.append("other")
    return tags


# ── DB-result-heavy examples (requirement 6: keep FEWER of these) ───────────
# An answer that states a real count/list result -- "Found N application(s)",
# "There are N ...", a rendered <table> -- is the shape QLoRA should learn to
# PRODUCE, not the specific N it happened to see. A handful survive verbatim
# for end-to-end format demonstration; the rest of the dedup already thins
# them by officer, this caps the remainder by intent so heavy list/table
# groups (the biggest raw-file categories: type+scope, status+period,
# type+period, ...) don't dominate the clean set numerically.
_DB_RESULT_RE = re.compile(
    r"found\s+<strong>\d|there\s+(?:are|is)\s+\d|class='data-table'"
    r"|of those \d+ application", re.IGNORECASE)
_MAX_DB_RESULT_PER_PATTERN_GROUP = 4  # per (meta group/intent) bucket


def main(raw_path: Path, clean_path: Path, train_path: Path, val_path: Path,
         report_path: Path, val_fraction: float, max_identical_answer: int,
         seed: int) -> int:
    raw: list[dict] = []
    with raw_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                raw.append(json.loads(line))
    raw_count = len(raw)

    # ── Pass 1: dedup by identical user-turn text (the officer-repeat case) ──
    seen_user: dict[str, dict] = {}
    exact_dupes = 0
    for ex in raw:
        key = _user_key(ex)
        if not key:
            continue
        if key in seen_user:
            exact_dupes += 1
            continue
        seen_user[key] = ex
    deduped = list(seen_user.values())

    # ── Pass 2: cap repeated identical assistant answers ─────────────────────
    # A boilerplate answer ("No applications found.", the capability blurb,
    # a mutation refusal) legitimately recurs across many DIFFERENT user
    # phrasings -- that repetition is exactly what teaches the model the
    # answer is fixed regardless of how the question was typed. Capped, not
    # removed outright, so the format is still over-represented on purpose,
    # just not thousands of times.
    answer_counts: Counter[str] = Counter(_last_answer(ex) for ex in deduped)
    kept_per_answer: Counter[str] = Counter()
    near_dupe_capped = 0
    pass2: list[dict] = []
    random.Random(seed).shuffle(deduped)  # so capping doesn't systematically favour one officer/order
    for ex in deduped:
        ans = _last_answer(ex)
        if answer_counts[ans] > max_identical_answer:
            if kept_per_answer[ans] >= max_identical_answer:
                near_dupe_capped += 1
                continue
            kept_per_answer[ans] += 1
        pass2.append(ex)

    # ── Pass 3: thin DB-result-heavy examples per meta group/intent ──────────
    group_seen: Counter[tuple] = Counter()
    db_result_thinned = 0
    final: list[dict] = []
    for ex in pass2:
        ans = _last_answer(ex)
        if _DB_RESULT_RE.search(ans):
            meta = ex.get("meta", {}) or {}
            bucket = (meta.get("group") or meta.get("scenario") or meta.get("source"),
                     meta.get("intent"))
            group_seen[bucket] += 1
            if group_seen[bucket] > _MAX_DB_RESULT_PER_PATTERN_GROUP:
                db_result_thinned += 1
                continue
        final.append(ex)

    random.Random(seed + 1).shuffle(final)

    # ── Categorize the surviving set for the report ──────────────────────────
    cat_counts: Counter[str] = Counter()
    for ex in final:
        for tag in categorize(ex):
            cat_counts[tag] += 1

    # ── Train/val split ───────────────────────────────────────────────────────
    n_val = max(1, int(len(final) * val_fraction))
    val_set = final[:n_val]
    train_set = final[n_val:]

    clean_path.write_text(
        "".join(json.dumps(ex, ensure_ascii=False) + "\n" for ex in final),
        encoding="utf-8")
    train_path.write_text(
        "".join(json.dumps(ex, ensure_ascii=False) + "\n" for ex in train_set),
        encoding="utf-8")
    val_path.write_text(
        "".join(json.dumps(ex, ensure_ascii=False) + "\n" for ex in val_set),
        encoding="utf-8")

    report = {
        "raw_count": raw_count,
        "exact_user_text_duplicates_removed": exact_dupes,
        "near_duplicate_answers_capped": near_dupe_capped,
        "db_result_examples_thinned": db_result_thinned,
        "clean_count": len(final),
        "train_count": len(train_set),
        "val_count": len(val_set),
        "categories": dict(sorted(cat_counts.items(), key=lambda kv: -kv[1])),
    }
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False),
                            encoding="utf-8")

    print(f"raw:              {raw_count}")
    print(f"exact dupes:      {exact_dupes}  (same user text, different officer's data)")
    print(f"answer-capped:    {near_dupe_capped}  (identical boilerplate answer, over the cap of {max_identical_answer})")
    print(f"db-result thinned:{db_result_thinned}  (capped to {_MAX_DB_RESULT_PER_PATTERN_GROUP} per group/intent)")
    print(f"clean:            {len(final)}")
    print(f"train / val:      {len(train_set)} / {len(val_set)}")
    print("\ncategories (an example can carry more than one tag):")
    for tag, n in report["categories"].items():
        print(f"  {tag:<32} {n}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", type=Path, default=Path("lora_dataset_final.jsonl"))
    parser.add_argument("--clean", type=Path, default=Path("lora_dataset_clean.jsonl"))
    parser.add_argument("--train", type=Path, default=Path("lora_dataset_train.jsonl"))
    parser.add_argument("--val", type=Path, default=Path("lora_dataset_val.jsonl"))
    parser.add_argument("--report", type=Path, default=Path("lora_dataset_clean_report.json"))
    parser.add_argument("--val-fraction", type=float, default=0.05)
    parser.add_argument("--max-identical-answer", type=int, default=6)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    raise SystemExit(main(args.raw, args.clean, args.train, args.val, args.report,
                          args.val_fraction, args.max_identical_answer, args.seed))
