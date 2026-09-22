"""
LoRA/QLoRA dataset pass covering everything found and fixed in the long
negation / exclusion / follow-up bug-hunting session (2026-09-16-17): column
exclusion ("not along"/"without"/"remove"), row exclusion (by ordinal, by
even/odd, by application number), status/type negation ("not approved",
"neither ISD nor merge"), IGRS with/without, field-visit date negation,
vague-count ("show more"/"less"), self-identity/capability questions asked as
follow-ups ("who is this", "what is this", "are you sis", "who am i", "what
is today's date"), and nth-row/column follow-ups -- each with a typo'd and/or
Tamil/Tanglish variant, reusing the exact `DatasetBuilder` machinery
`build_lora_dataset.py` already established (real `process_chat()` calls
against the seeded officers, so every answer is grounded truth, not an
LLM-invented one).

A second, DB-free pass extracts workflow Q&A directly from the corpus
(`survey_manual.txt`, `tamilnilam_urban_services_and_districts.txt`) as
hand-authored pairs -- not a live Ollama call, so nothing here risks teaching
the model to imitate an ungrounded LLM answer, the same reason
`build_lora_dataset.py` excludes `workflow_knowledge`/`general_query` bank
questions. This is what "make sure it consist workflow related question"
asked for, answered from the two documents rather than generated.

Usage:
    python -m backend.sample_db.build_lora_dataset_session [--out FILE] [--concurrency N]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
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
    DatasetBuilder, build_officer_context,
)

# ── 1. Multi-turn chains over a carried application list ────────────────────
# Every chain opens with "show applications" (or a scoped variant) so a real
# 2-row list is in context, then drives the fixed behaviours across it.
SETUP = "show applications"

CHAINS: list[tuple[str, list[str]]] = [
    ("column_exclusion_along", [
        SETUP, "along district", "along taluk", "not along ward",
    ]),
    ("column_exclusion_synonyms", [
        SETUP, "without status", "not with stage",
        "dont display the survey number column", "exclude block",
    ]),
    ("column_exclusion_typo", [
        SETUP, "not alng ward", "witout stage", "exclde taluk",
    ]),
    ("column_exclusion_tanglish", [
        SETUP, "ward illama", "stage vendam", "adhuvodu district",
    ]),
    ("column_exclusion_mandatory_refused", [
        SETUP, "not along application no", "not along survey no",
        "not along sub division no",
    ]),
    ("row_exclusion_ordinal", [
        SETUP, "remove row 2", "remove the first application",
    ]),
    ("row_exclusion_typo", [
        SETUP, "remov row 2", "delete rw 2",
    ]),
    ("row_exclusion_by_number", [
        SETUP, "remove 2026/0154/28/001167",
    ]),
    ("even_odd_rows", [
        "show pending applications", "show applications in even rows",
        "show odd rows",
    ]),
    ("status_negation_single", [
        SETUP, "how many are not approved", "not pending applications",
    ]),
    ("status_negation_typo", [
        SETUP, "how mny not approved applications", "not pendng applications",
    ]),
    ("status_negation_compound", [
        SETUP, "not approved and not rejected",
        "neither approved nor rejected applications",
    ]),
    ("type_negation", [
        "show isd applications", "how many are not isd",
        "not isd applications",
    ]),
    ("type_negation_neither", [
        SETUP, "neither isd nor merge applications",
    ]),
    ("igrs_with_without", [
        "applications with igrs number", "applications without igrs number",
        "applications not having igrs number",
    ]),
    ("can_number_column", [
        SETUP, "along can number", "show application with can number",
        "not along can number",
    ]),
    ("can_number_typo_tanglish", [
        SETUP, "along can nummber", "can number illama",
    ]),
    ("channel_negation", [
        SETUP, "applications neither from csc nor sub registrar",
    ]),
    ("vague_count", [
        SETUP, "show more applications", "less kami", "more kami",
    ]),
    ("self_identity_followups", [
        SETUP, "who is this", "what is this", "are you sis",
        "are you a bot", "who am i", "what is todays date",
    ]),
    ("self_identity_tamil_tanglish", [
        SETUP, "இது யார்", "இது என்ன", "enna solra", "நான் யார்",
    ]),
    ("nth_row_column", [
        SETUP, "show 3rd row", "thrid row", "give me the 2nd column",
        "row nummber 3",
    ]),
    ("contentless_and_capability_start", [
        "hi show applications", "are u sis ?",
    ]),
    ("field_visit_date_negation", [
        "show my field visits", "field visits not on monday",
        "field visits not between 2026-01-01 and 2026-12-31",
    ]),
    ("today_date_no_context", [
        "what is todays date",
    ]),
]


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
        for label, turns in CHAINS:
            tasks.append(builder.bug_chain(officer, ctx, label, turns))

    print(f"Running {len(tasks)} chain tasks (concurrency={concurrency}) ...")
    done = 0
    for i in range(0, len(tasks), 100):
        await asyncio.gather(*tasks[i:i + 100])
        done += len(tasks[i:i + 100])
        print(f"  {done}/{len(tasks)} tasks done, {len(builder.examples)} examples so far")

    with out_path.open("w", encoding="utf-8") as f:
        for ex in builder.examples:
            f.write(json.dumps(ex, ensure_ascii=False) + "\n")

    print(f"\nWrote {len(builder.examples)} examples to {out_path}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=Path("lora_dataset_session.jsonl"))
    parser.add_argument("--concurrency", type=int, default=10)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.out, args.concurrency)))
