"""
Fix the 14 application_count typo examples where the status word is dropped
from the answer ("There are no applications in March 2026...", not "There are
no rejected applications..."). Confirmed via _probe_typo_status.py that this
is NOT current live behavior -- the app now correctly resolves a typo'd status
word and names it in the answer. These 14 examples are stale, generated
before that fix landed in chatbot.py. Regenerate with their ORIGINAL
(typo'd) question text run live against the real DB, same approach as
fix_negation_records.py.

Usage:
    python fix_status_label_records.py
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import uuid
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
logging.disable(logging.CRITICAL)

from sqlalchemy import select

from backend.database import AsyncSessionLocal
from backend.models import SISOfficer
from backend.schemas import OfficerContext
from backend.services import chatbot
from backend.sample_db.build_lora_dataset import build_officer_context, _flatten_html

TRAIN = Path("train_augmented.jsonl")
LINE_NUMBERS = [39, 45, 73, 127, 152, 467, 499, 509, 573, 597, 700, 832, 885, 1140]


async def main():
    lines = TRAIN.read_text(encoding="utf-8").splitlines()
    records = {ln: json.loads(lines[ln - 1]) for ln in LINE_NUMBERS}

    async with AsyncSessionLocal() as db:
        officer_cache: dict[str, OfficerContext] = {}
        for ln, rec in records.items():
            officer_email = rec["meta"]["officer"]
            question = rec["messages"][1]["content"]
            old_answer = rec["messages"][2]["content"]

            if officer_email not in officer_cache:
                result = await db.execute(select(SISOfficer).where(SISOfficer.email == officer_email))
                officer = result.scalar_one()
                officer_cache[officer_email] = await build_officer_context(db, officer)
            ctx = officer_cache[officer_email]

            result = await chatbot.process_chat(question, str(uuid.uuid4()), ctx, db, chat_history=[])
            new_answer = _flatten_html(result.get("response") or "")

            print(f"line {ln}: {question!r}")
            print(f"  old: {old_answer}")
            print(f"  new: {new_answer}")

            if not new_answer:
                print(f"  !! empty response, leaving line {ln} unchanged")
                continue
            rec["messages"][2]["content"] = new_answer

    for ln, rec in records.items():
        lines[ln - 1] = json.dumps(rec, ensure_ascii=False)

    TRAIN.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"patched {len(records)} line(s) in {TRAIN}")


if __name__ == "__main__":
    asyncio.run(main())
