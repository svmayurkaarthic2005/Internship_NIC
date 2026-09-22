"""
Targeted expansion of Tamil / Tanglish / negation coverage for the LoRA
dataset -- the one gap the validation pass flagged (45 / 37 / 26 examples
out of 1,649). Every phrasing here is genuinely distinct (different
vocabulary, different sentence shape, different scope/status/type
combination), not a mechanical rewording of an existing example -- the
point is linguistic variety, not inflating the count.

Runs real `process_chat()` calls against the seeded officers, exactly like
`build_lora_dataset.py` / `build_lora_dataset_session.py`, so every answer
is grounded truth.

Usage:
    python -m backend.sample_db.build_lora_dataset_language --out lora_dataset_language.jsonl
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

# ── Standalone single-turn examples ──────────────────────────────────────────
SINGLE_TURN: list[str] = [
    # Tamil -- fresh phrasings, different verbs/scopes than the existing 45
    "நிலுவையில் உள்ள NISD விண்ணப்பங்களை காண்பி",
    "எனது கள ஆய்வுகள் எத்தனை நிலுவையில் உள்ளன",
    "வார்டு 002 இல் எத்தனை ISD விண்ணப்பங்கள் உள்ளன",
    "இந்த மாதம் நிராகரிக்கப்பட்ட விண்ணப்பங்கள் எத்தனை",
    "என் அதிகார எல்லையில் உள்ள தாலுகா எது",
    "இணைப்பு விண்ணப்பங்கள் ஏதேனும் உள்ளதா",
    "எனது பணிச்சுமை என்ன",
    "காலக்கெடு நெருங்கும் விண்ணப்பங்களை காட்டு",
    # Tanglish -- fresh phrasings
    "pending ah irukura NISD applications kaatu",
    "enaku evlo isd apps iruku",
    "indha vaaram field visit evlo iruku",
    "block 0015 la evlo applications iruku",
    "en jurisdiction la evlo ward iruku",
    "overdue aana applications evlo",
    "escalate pannanum na yarukku sollanum",
    "sub registrar la irunthu vantha applications evlo",
    # English negation -- fresh shapes, not just "not X"
    "completed applications mattum venam",
    "don't show completed applications",
    "exclude NISD applications",
    "I don't want to see rejected files",
    "leave out the ISD ones",
    "skip anything that is still pending",
    "give me everything except the escalated ones",
    "none of the approved ones, just the rest",
]

# ── Multi-turn chains: negation as a follow-up, in Tamil/Tanglish too ────────
CHAINS: list[tuple[str, list[str]]] = [
    ("negation_followup_tamil", [
        "விண்ணப்பங்களைக் காட்டு", "நிராகரிக்கப்பட்டவை இல்லாமல் காட்டு",
    ]),
    ("negation_followup_tanglish", [
        "show applications", "rejected aanadhu illama kaatu",
    ]),
    ("negation_exclude_type_tanglish", [
        "show applications", "isd venam, marathu kaatu",
    ]),
    ("tamil_scope_followup", [
        "விண்ணப்பங்களைக் காட்டு", "வார்டு எண் என்ன",
    ]),
    ("tanglish_count_followup", [
        "show applications", "evlo pending iruku",
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
        for q in SINGLE_TURN:
            tasks.append(builder.single_turn(officer, ctx, q, {"source": "language_expansion"}))
        for label, turns in CHAINS:
            tasks.append(builder.bug_chain(officer, ctx, label, turns))

    print(f"Running {len(tasks)} tasks (concurrency={concurrency}) ...")
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
    parser.add_argument("--out", type=Path, default=Path("lora_dataset_language.jsonl"))
    parser.add_argument("--concurrency", type=int, default=8)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.out, args.concurrency)))
