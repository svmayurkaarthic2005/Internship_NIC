"""
Second pass over the LoRA/QLoRA dataset: three more categories, on top of
`build_lora_dataset.py`'s routing-bank + typo + Tanglish + bug-scenario base.

  1. Follow-up / context handling (~1500) -- every documented follow-up shape
     (per-row field projection, ordinal, slice, aggregate, refine, bare-date
     re-scope, confirmation) crossed against a spread of listing setups, so
     the same follow-up is exercised over different scopes (CSC-only,
     ISD-only, pending-only, a period, a ward).
  2. Error / ambiguity cases (~1000) -- non-existent application numbers,
     invalid ward/block/survey references, ambiguous or typo'd channel
     names, missing-context field questions, mutation-request refusals,
     contentless messages, and out-of-scope questions. What officers get
     wrong is as much a training signal as what they get right.
  3. SIS terminology + intents (~2000, floor) -- the routing bank's
     type/status/service-code questions already cover most of this from the
     first pass; this file tops it up with the corpus's own FAQ, parsed
     directly out of backend/documents/faq_english.txt and faq_tamil.txt (no
     LLM, no DB -- these Q/A pairs are already the department's authoritative
     answer) plus a combinatorial SIS-glossary sweep (ISD/NISD/MERGE, roles,
     statuses, document requirements) in English, Tamil and Tanglish.

Reuses `DatasetBuilder`, `build_officer_context` and `_flatten_html` from
build_lora_dataset.py rather than re-implementing the DB/session plumbing.

Usage:
    python -m backend.sample_db.build_lora_dataset_extra --out lora_dataset_extra.jsonl
"""
from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import logging
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
logging.getLogger("backend").setLevel(logging.WARNING)

from sqlalchemy import select

from backend.database import AsyncSessionLocal
from backend.models import SISOfficer
from backend.sample_db.build_lora_dataset import (
    SYSTEM_PROMPT, DatasetBuilder, build_officer_context,
)

_DOCS_DIR = _ROOT / "backend" / "documents"

# ── 3a. Corpus FAQ extraction (no LLM, no DB) ────────────────────────────────
_QA_EN_RE = re.compile(r"^Q:\s*(.+?)\s*\nA:\s*(.+?)(?=\n\n|\Z)", re.MULTILINE | re.DOTALL)
_QA_TA_RE = re.compile(r"\*\*கே:\*\*\s*(.+?)\s*\n+\*\*ப:\*\*\s*(.+?)(?=\n\n|\Z)",
                        re.MULTILINE | re.DOTALL)


def extract_corpus_qa() -> list[dict]:
    examples = []
    en_path = _DOCS_DIR / "faq_english.txt"
    if en_path.exists():
        text = en_path.read_text(encoding="utf-8", errors="replace")
        for q, a in _QA_EN_RE.findall(text):
            q, a = " ".join(q.split()), " ".join(a.split())
            if q and a:
                examples.append({
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": q},
                        {"role": "assistant", "content": a},
                    ],
                    "meta": {"source": "faq_english"},
                })
    ta_path = _DOCS_DIR / "faq_tamil.txt"
    if ta_path.exists():
        text = ta_path.read_text(encoding="utf-16", errors="replace")
        for q, a in _QA_TA_RE.findall(text):
            q, a = " ".join(q.split()), " ".join(a.split())
            if q and a:
                examples.append({
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": q},
                        {"role": "assistant", "content": a},
                    ],
                    "meta": {"source": "faq_tamil"},
                })
    return examples


# ── 1. Follow-up / context handling ──────────────────────────────────────────
FOLLOWUP_SETUPS = [
    "Show my applications",
    "Show my pending applications",
    "Show my approved applications",
    "Show my rejected applications",
    "Show my overdue applications",
    "Show my ISD applications",
    "Show my NISD applications",
    "Show my merge applications",
    "Show applications from CSC",
    "Show applications from the Sub-Registrar",
    "Show my citizen channel applications",
    "Show pending ISD applications",
    "Show approved NISD applications",
    "Show applications from last month",
    "Show applications from this year",
    "Show applications in ward 102",
    "Show applications in ward 103",
    "Show applications from CSC that are pending",
]

_FIELD_FOLLOWUPS = [
    "their wards", "their vards", "their blocks", "their blocs",
    "their status", "their satus", "their stage", "their type", "thier type",
    "their survey numbers", "their survay numbers", "their can numbers",
    "their can nubers", "their submission channel", "their applicant names",
    "their aplicant names", "their mobile numbers", "their fee amounts",
    "their submission dates", "their igrs numbers", "their irgs numbers",
    "application no with type", "application no with type and status",
    "application no only", "app number with can number",
    "application number and ward",
]

_ORDINAL_AGGREGATE_FOLLOWUPS = [
    "which one is oldest", "which one is newest", "which one is the oldst",
    "how many of them are approved", "how many of them are rejected",
    "how many of these are pending", "evlo approved irruku",
    "show only NISD", "show only ISD", "show only pending", "show only approved",
    "the first one", "the last one", "the first two", "the last two",
    "the 2nd one", "the 3rd one", "what is the total fee",
    "is that all", "thats all ?", "tahts it", "anything else",
    "last month",
]

FOLLOWUP_QUESTIONS = _FIELD_FOLLOWUPS + _ORDINAL_AGGREGATE_FOLLOWUPS

# ── 2. Error / ambiguity cases ────────────────────────────────────────────────
_FAKE_APP_NUMBERS = [
    "2099/0154/28/999999", "2099/0153/28/999998",
    "2020/0155/28/000000", "9999/0154/28/123456",
]
_ONE_APP_TEMPLATES = [
    "What is the status of {a}?", "Show me details of {a}",
    "Who is the applicant for {a}?", "What documents are missing in {a}?",
    "Why was {a} rejected?", "Is the sale deed registered for {a}?",
    "Is there any litigation on {a}?", "Show workflow history of {a}",
    "What is the CAN number for {a}?", "Which stage is {a} at?",
    "When was {a} submitted?", "Is {a} overdue?",
    "What is the field visit date for {a}?", "Is {a} ISD or NISD?",
]
NONEXISTENT_APP_QUESTIONS = [
    t.replace("{a}", app) for t, app in itertools.product(_ONE_APP_TEMPLATES, _FAKE_APP_NUMBERS)
]

_LIST_VERBS = ["Show me", "List", "Give me", "Display", "How many are there of",
               "I want to see", "Fetch", "Can you list", "Kindly show", "Please give"]
_BAD_SCOPES = ["ward 999", "ward 000", "block 9999", "survey 99999", "block 0000"]
INVALID_SCOPE_QUESTIONS = [
    f"{verb} applications in {scope}" for verb, scope in itertools.product(_LIST_VERBS, _BAD_SCOPES)
]

_CHANNEL_AMBIGUOUS_TOKENS = [
    "csc", "sro", "scs", "seo", "citizn", "cetizen", "sitizen",
    "sub registrer", "sub registrar", "sub-registrer",
]
AMBIGUOUS_CHANNEL_QUESTIONS = [
    f"{verb} {tok} applications" for verb, tok in
    itertools.product(_LIST_VERBS, _CHANNEL_AMBIGUOUS_TOKENS)
]

MISSING_CONTEXT_QUESTIONS = [
    "what is the status?", "show me details", "who is the applicant?",
    "what is the CAN number?", "is it overdue?", "which stage is it at?",
    "what documents are missing?", "when was it submitted?",
]

MUTATION_REFUSAL_QUESTIONS = [
    "delete all my applications", "clear the database",
    "update this application's status to approved",
    "remove this application from the register",
    "reset everything", "truncate the knowledge base",
    "mark 2026/0154/28/000011 as rejected", "change my jurisdiction to ward 999",
    "add a new application for me", "edit the applicant name on this file",
]

CONTENTLESS_QUESTIONS = [
    "ok", "...", "hmm", "clear", "cls", "exit", "logout", "quit",
    "சரி", "clear chat", "clear conversation history", "clear everything",
]

OUT_OF_SCOPE_QUESTIONS = [
    "What is the weather tomorrow?", "Who won the cricket match yesterday?",
    "Tell me a joke", "What is 456 multiplied by 789?",
    "Can you write Python code for me?", "What is the stock price of Reliance?",
    "Translate 'good morning' into French", "Who is the Prime Minister of India?",
    "Recommend a good movie", "What is my horoscope today?",
    "How do I apply for a passport?", "Book me a train ticket to Chennai",
    "wat is the weathr tomorow", "wat is 2+2",
]

ERROR_AMBIGUITY_QUESTIONS = (
    NONEXISTENT_APP_QUESTIONS + INVALID_SCOPE_QUESTIONS + AMBIGUOUS_CHANNEL_QUESTIONS
    + MISSING_CONTEXT_QUESTIONS + MUTATION_REFUSAL_QUESTIONS + CONTENTLESS_QUESTIONS
    + OUT_OF_SCOPE_QUESTIONS
)

# ── 3b. SIS glossary sweep (English / Tamil / Tanglish) ──────────────────────
_GLOSSARY_TERMS_EN = [
    "ISD", "NISD", "MERGE application", "CAN number", "IGRS Form 6 number",
    "Sub-Registrar", "CSC", "CSC", "Senior Draughtsman", "Tahsildar",
    "Deputy Inspector Surveyor", "DSC", "TSLR", "patta", "sub-division number",
    "encumbrance certificate", "service code 0153", "service code 0154",
    "service code 0155", "field visit", "workflow stage", "escalation",
]
_GLOSSARY_TEMPLATES_EN = ["What is {t}?", "What does {t} mean?", "Explain {t}"]
_GLOSSARY_TEMPLATES_TA = ["{t} என்றால் என்ன?", "{t} பற்றி விளக்கவும்"]
_GLOSSARY_TEMPLATES_TANGLISH = ["{t} nu sonna enna", "{t} pathi solunga"]

GLOSSARY_QUESTIONS = (
    [t.format(t=term) for t, term in itertools.product(_GLOSSARY_TEMPLATES_EN, _GLOSSARY_TERMS_EN)]
    + [t.format(t=term) for t, term in itertools.product(_GLOSSARY_TEMPLATES_TA, _GLOSSARY_TERMS_EN)]
    + [t.format(t=term) for t, term in itertools.product(_GLOSSARY_TEMPLATES_TANGLISH, _GLOSSARY_TERMS_EN)]
)


async def main(out_path: Path, concurrency: int) -> int:
    async with AsyncSessionLocal() as db:
        officers = (await db.execute(
            select(SISOfficer).where(SISOfficer.is_active == True)
            .order_by(SISOfficer.employee_id)
        )).scalars().all()
        if not officers:
            print("No active officers found -- run seed_sample_db.py first.")
            return 1
        officer_ctxs = [(o, await build_officer_context(db, o)) for o in officers]

    builder = DatasetBuilder(concurrency)
    tasks = []

    for officer, ctx in officer_ctxs:
        # 1. follow-ups, grouped by setup (one setup call per group)
        for setup in FOLLOWUP_SETUPS:
            tasks.append(builder.project_setup_group(officer, ctx, setup, FOLLOWUP_QUESTIONS))
        # 2. error / ambiguity, single-turn
        for q in ERROR_AMBIGUITY_QUESTIONS:
            tasks.append(builder.single_turn(officer, ctx, q, {"source": "error_ambiguity"}))
        # 3. glossary sweep, single-turn
        for q in GLOSSARY_QUESTIONS:
            tasks.append(builder.single_turn(officer, ctx, q, {"source": "glossary"}))

    print(f"Running {len(tasks)} tasks (concurrency={concurrency}) ...")
    for i in range(0, len(tasks), 200):
        await asyncio.gather(*tasks[i:i + 200])
        print(f"  {min(i + 200, len(tasks))}/{len(tasks)} tasks done, "
              f"{len(builder.examples)} examples so far")

    all_examples = builder.examples + extract_corpus_qa()
    with out_path.open("w", encoding="utf-8") as f:
        for ex in all_examples:
            f.write(json.dumps(ex, ensure_ascii=False) + "\n")

    print(f"\nWrote {len(all_examples)} examples to {out_path}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=Path("lora_dataset_extra.jsonl"))
    parser.add_argument("--concurrency", type=int, default=12)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.out, args.concurrency)))
