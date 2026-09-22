"""
Third pass over the LoRA/QLoRA dataset: MERGE-application coverage (the
"show display merge" / typo'd "mrge" investigation) plus workflow-knowledge
Q&A extracted directly from the prose reference documents.

  1. MERGE-specific questions, single-turn + typo'd, run through the real
     pipeline for all 3 officers. The DB has zero seeded MERGE applications
     (service code 0155) -- see `appl_log_urban_demo.csv`, genuinely 0 rows --
     so every listing answer is correctly "No applications found"; the value
     here is exercising the *routing*, including the typo-tolerance fix in
     `rag.py`'s `_has_merge_token` (was `\\bmerge?\\b`, missed transpositions
     like "mrge").
  2. `extract_section_qa()` -- generic "=== HEADER ===" section splitter over
     land_rules.txt, workflow_guide.txt and survey_manual.txt. No LLM, no DB:
     each section's own text becomes the answer, paired with a few natural
     question phrasings of its header. This is static department policy
     (ISD/NISD workflow, the 15-day rule, merge eligibility, required
     documents, escalation, land classification codes, ...) -- permanent
     procedural facts, not the "changing government records" (real
     application/CAN numbers) the redaction pass exists to keep out.

Usage:
    python -m backend.sample_db.build_lora_dataset_docs --out lora_dataset_docs.jsonl
"""
from __future__ import annotations

import argparse
import asyncio
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
    SYSTEM_PROMPT, DatasetBuilder, build_officer_context, _corrupt,
)
from backend.utils.helpers import SIS_URBAN_SERVICES

_DOCS_DIR = _ROOT / "backend" / "documents"

# ── 1b. All 30 official service codes, both phrasings ───────────────────────
# "show <code> applications" on anything but 0153/0154/0155 used to silently
# fall through to the officer's whole pending queue, discarding the code
# entirely -- the same bug class as the MERGE one, just for the other 27
# codes. Both phrasings are included so the fix (routing to
# service_code_lookup instead of a generic listing) is exercised for real.
SERVICE_CODE_QUESTIONS = [
    q for code in sorted(SIS_URBAN_SERVICES)
    for q in (f"what is {code}", f"show {code} applications",
               f"how many {code} applications do I have")
]

# ── 1c. Every service by its official NAME instead of its digit code ────────
# "what is street master" was not recognised at all before
# `find_service_code_by_name()` in helpers.py -- the routing only ever looked
# for a 3-4 digit token. Both phrasings, plus one deterministic single-letter-
# transposition typo per name, exercise the same fix the numeric-code pass
# above exercises for typo'd digits.
SERVICE_NAME_QUESTIONS = []
for _code, _info in sorted(SIS_URBAN_SERVICES.items()):
    _name = _info["name"]
    SERVICE_NAME_QUESTIONS.append(f"what is {_name}")
    SERVICE_NAME_QUESTIONS.append(f"show {_name} applications")
    _typo_q = _corrupt(f"what is {_name}")
    if _typo_q:
        SERVICE_NAME_QUESTIONS.append(_typo_q)

# ── 1. MERGE-specific questions ──────────────────────────────────────────────
MERGE_QUESTIONS = [
    "show display merge",
    "show merge applications",
    "shw mrge applicatons",
    "displey merg aplications",
    "list my merge applications",
    "how many merge applications do I have",
    "show emrge applications",
    "mrege applications",
    "give me merg applications",
    "merge applications in my ward",
    "show pending merge applications",
    "what is a merge application",
    "what is service code 0155",
    "explain the merge application process",
    "what are the eligibility rules for a merge application",
    "can two survey numbers with different owners be merged",
    "is a merge application ISD or NISD",
]

# ── 2. Section-based extraction from the prose reference docs ───────────────
_SECTION_RE = re.compile(r"^=== (.+?) ===\s*$", re.MULTILINE)
_MAX_SECTION_CHARS = 900

_QUESTION_TEMPLATES = [
    "What does the SIS guide say about {h}?",
    "Explain {h}",
    "What are the rules for {h}?",
]


def extract_section_qa() -> list[dict]:
    examples = []
    for fname in ("land_rules.txt", "workflow_guide.txt", "survey_manual.txt"):
        path = _DOCS_DIR / fname
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        headers = list(_SECTION_RE.finditer(text))
        for i, m in enumerate(headers):
            header = m.group(1).strip()
            start = m.end()
            end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
            body = text[start:end].strip()
            body = re.sub(r"\n{3,}", "\n\n", body)
            if len(body) > _MAX_SECTION_CHARS:
                body = body[:_MAX_SECTION_CHARS].rsplit("\n", 1)[0] + "\n..."
            if not body:
                continue
            h_lower = re.sub(r"\s*\([^)]*\)\s*", " ", header).strip().lower()
            for template in _QUESTION_TEMPLATES:
                examples.append({
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": template.format(h=h_lower)},
                        {"role": "assistant", "content": body},
                    ],
                    "meta": {"source": fname, "section": header},
                })
    return examples


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
        for q in MERGE_QUESTIONS:
            tasks.append(builder.single_turn(officer, ctx, q, {"source": "merge"}))
        for q in SERVICE_CODE_QUESTIONS:
            tasks.append(builder.single_turn(officer, ctx, q, {"source": "service_code"}))
        for q in SERVICE_NAME_QUESTIONS:
            tasks.append(builder.single_turn(officer, ctx, q, {"source": "service_name"}))

    print(f"Running {len(tasks)} tasks (concurrency={concurrency}) ...")
    for i in range(0, len(tasks), 100):
        await asyncio.gather(*tasks[i:i + 100])
        print(f"  {min(i + 100, len(tasks))}/{len(tasks)} tasks done, "
              f"{len(builder.examples)} examples so far")

    all_examples = builder.examples + extract_section_qa()
    with out_path.open("w", encoding="utf-8") as f:
        for ex in all_examples:
            f.write(json.dumps(ex, ensure_ascii=False) + "\n")

    print(f"\nWrote {len(all_examples)} examples to {out_path} "
          f"({len(builder.examples)} MERGE + {len(all_examples) - len(builder.examples)} doc-section)")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=Path("lora_dataset_docs.jsonl"))
    parser.add_argument("--concurrency", type=int, default=8)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.out, args.concurrency)))
