"""
Build a LoRA fine-tuning dataset from the REAL sis_chatbot_db.

Runs a curated set of officer questions through the actual chat pipeline
(process_chat), for each seeded officer, against their real jurisdiction data.
The deterministic handlers -- not the LLM -- produce every answer, so every
example is grounded truth: real application numbers, real counts, real IGRS/
CAN rules, taken from whatever the database holds right now. Re-run this after
a reseed and the dataset regenerates itself correctly.

Only single-turn examples are captured (no chat_history threading): a
follow-up's correct answer depends on exactly what was rendered the turn
before, which is a moving target across officers/reseeds and adds nothing a
fine-tune needs beyond "here's an SIS answer for a self-contained question".

Output: JSONL, one {"messages": [...]} per line, the standard SFT chat
format (system/user/assistant) most LoRA trainers (axolotl, unsloth,
Ollama/llama.cpp via a Modelfile ADAPTER build) accept directly.

Usage:
    python -m backend.sample_db.build_lora_dataset [--out FILE] [--per-officer N]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import uuid
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from sqlalchemy import select

from backend.database import AsyncSessionLocal
from backend.models import SISOfficer
from backend.schemas import OfficerContext
from backend.services import chatbot
from backend.services.auth_service import get_officer_jurisdiction_ids

SYSTEM_PROMPT = (
    "You are the SIS AI Assistant for Tamil Nadu Sub Inspector Surveyor "
    "officers. Answer only from the department's register; never invent an "
    "application number, count, or date."
)

# One self-contained question per documented behaviour in CLAUDE.md -- the
# shapes that were hand-verified during this project's bug-fix history, so
# the dataset teaches the model the same distinctions the Python guards
# enforce, in the model's own words.
QUESTIONS = [
    # workload / listing
    "How many applications are pending with me?",
    "Show me my overdue applications",
    "What is my workload?",
    "Show my ISD applications",
    "Show my NISD applications",
    "List my approved applications",
    "Show applications from CSC",
    "Show applications from the Sub-Registrar",
    "Show my citizen channel applications",
    "What is my jurisdiction?",
    # service codes
    "What is service code 0154?",
    "What is service code 0153?",
    "What is the difference between ISD and NISD?",
    "How many service codes start with 016?",
    "What is 0015?",
    # IGRS / CAN rules
    "If the IGRS number is absent, does that mean it's not from the SRO?",
    "Who is the Sub-Registrar?",
    "What is a CAN number?",
    "How long is a CAN number?",
    "Do CSC applications have an IGRS Form 6 number?",
    # field visits
    "How many field visits do I have?",
    "Which field visits are overdue?",
    "Which field visits are unscheduled?",
    "Are there any scheduling conflicts?",
    "Which application should I field visit tomorrow?",
    "Can I change the field visit date myself?",
    # last application
    "What was my last application?",
    "Is my last application approved?",
    "What was my last rejected application?",
    # comparisons
    "Compare ISD and NISD",
    "Which took longer to approve, ISD or NISD?",
    "Compare ward 102 and ward 103",
    "What is the average time to approve an application?",
    # fee
    "What is the total fee for my applications?",
    "What is the fee for service code 0154?",
    # out of scope / contentless
    "What is the weather tomorrow?",
    "What can you do?",
    "clear",
    "thanks",
    "hi",
]


async def build_officer_context(db, officer: SISOfficer) -> OfficerContext:
    jur = await get_officer_jurisdiction_ids(officer.id, db)
    ids = (jur["district_ids"] + jur["taluk_ids"] + jur["town_ids"]
           + jur["ward_ids"] + jur["block_ids"])
    return OfficerContext(
        officer_id=officer.id, employee_id=officer.employee_id, name=officer.name,
        email=officer.email, designation=officer.designation,
        jurisdiction_type=jur["jurisdiction_type"],
        jurisdiction_name=jur["jurisdiction_name"],
        jurisdiction_ids=[i for i in ids if i])


# A deterministic answer's data-table is real HTML (see table_renderer.js);
# the model being fine-tuned only ever writes prose, so the table is flattened
# to " | "-joined rows instead of taught as markup to reproduce.
_TAG_RE = re.compile(r"<[^>]+>")


def _flatten_html(answer: str) -> str:
    if "<table" not in answer and "<div" not in answer:
        return answer.strip()
    text = re.sub(r"</tr>", "\n", answer)
    text = re.sub(r"</t[hd]>", " | ", text)
    text = re.sub(r"</div>", "\n", text)
    text = _TAG_RE.sub("", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{2,}", "\n", text)
    text = re.sub(r"^\s*\|\s*", "", text, flags=re.MULTILINE)
    return text.strip()


async def run_officer(db, officer: SISOfficer, per_officer: int) -> list[dict]:
    ctx = await build_officer_context(db, officer)
    session_id = str(uuid.uuid4())
    examples = []
    for message in QUESTIONS[:per_officer]:
        result = await chatbot.process_chat(message, session_id, ctx, db, chat_history=[])
        answer = _flatten_html(result.get("response") or "")
        if not answer:
            continue
        examples.append({
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": message},
                {"role": "assistant", "content": answer},
            ],
            "meta": {"officer": officer.email, "intent": result.get("intent")},
        })
        # Each turn writes its own chat_messages row (see readonly_guard docs);
        # a fresh session per question keeps one officer's examples from being
        # read as follow-ups of each other.
        session_id = str(uuid.uuid4())
    return examples


async def main(out_path: Path, per_officer: int) -> int:
    async with AsyncSessionLocal() as db:
        officers = (await db.execute(
            select(SISOfficer).where(SISOfficer.is_active == True)
            .order_by(SISOfficer.employee_id)
        )).scalars().all()
        if not officers:
            print("No active officers found -- run seed_sample_db.py first.")
            return 1

        all_examples = []
        for officer in officers:
            print(f"Running {len(QUESTIONS[:per_officer])} questions as {officer.email} ...")
            all_examples.extend(await run_officer(db, officer, per_officer))

    with out_path.open("w", encoding="utf-8") as f:
        for ex in all_examples:
            f.write(json.dumps(ex, ensure_ascii=False) + "\n")

    print(f"\nWrote {len(all_examples)} examples to {out_path}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=Path("lora_dataset.jsonl"))
    parser.add_argument("--per-officer", type=int, default=len(QUESTIONS))
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.out, args.per_officer)))
