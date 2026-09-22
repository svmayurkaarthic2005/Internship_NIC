"""
Fix the 6 negative-filter records flagged in review: "skip anything that is
still pending" (line 70, 1070, 1080) and "completed applications mattum
venam" (line 647, 771, 1067) are answered with exactly the records the officer
asked to be EXCLUDED.

This isn't a data-generation bug -- build_lora_dataset.py runs every question
through the real chatbot.process_chat(), so these are the live app's actual
output for a first-turn negated filter (the negation handling in
followup_context._EXCLUDE_TRIGGER_RE only fires for follow-ups, not a fresh
message). Reported separately as a production bug to fix in chatbot.py.

For the training data: keep the officer's original (negated) question, but
replace the answer with what the app actually produces for the equivalent
UNAMBIGUOUS phrasing -- run live against the real DB through the same
process_chat() pipeline the whole dataset was built from, so the replacement
is exactly as grounded as everything else in the file, not hand-typed.

  "skip anything that is still pending"     -> "show my in progress applications"
  "completed applications mattum venam"     -> "show my pending applications"
    (ACTIVE_STATUSES default already excludes approved/rejected, which is
    what "no completed ones" means for an unscoped listing)

Usage:
    python fix_negation_records.py
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

logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
logging.getLogger("backend").setLevel(logging.WARNING)

from sqlalchemy import select

from backend.database import AsyncSessionLocal
from backend.models import SISOfficer
from backend.schemas import OfficerContext
from backend.services import chatbot
from backend.services.auth_service import get_officer_jurisdiction_ids

TRAIN = Path("train_augmented.jsonl")

# 1-indexed line numbers -> the unambiguous phrasing that gets the CORRECT
# real answer for what the original (negated) question meant.
FIXES = {
    70: "show my in progress applications",
    647: "show my pending applications",
    771: "show my pending applications",
    1067: "show my pending applications",
    1070: "show my in progress applications",
    1080: "show my in progress applications",
}

_TAG_RE = None  # set below, imported from build_lora_dataset for identical rendering


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


async def main():
    from backend.sample_db.build_lora_dataset import _flatten_html

    lines = TRAIN.read_text(encoding="utf-8").splitlines()
    records = {ln: json.loads(lines[ln - 1]) for ln in FIXES}

    async with AsyncSessionLocal() as db:
        officer_cache: dict[str, OfficerContext] = {}
        for ln, rewritten_q in FIXES.items():
            rec = records[ln]
            officer_email = rec["meta"]["officer"]
            original_q = rec["messages"][1]["content"]
            old_answer = rec["messages"][2]["content"]

            if officer_email not in officer_cache:
                result = await db.execute(select(SISOfficer).where(SISOfficer.email == officer_email))
                officer = result.scalar_one()
                officer_cache[officer_email] = await build_officer_context(db, officer)
            ctx = officer_cache[officer_email]

            result = await chatbot.process_chat(rewritten_q, str(uuid.uuid4()), ctx, db, chat_history=[])
            new_answer = _flatten_html(result.get("response") or "")

            print(f"--- line {ln} ({officer_email}) ---")
            print("original Q:", original_q)
            print("old A:", old_answer[:150].replace("\n", " | "))
            print("rewritten Q used to fetch correct answer:", rewritten_q)
            print("new A:", new_answer[:150].replace("\n", " | "))
            print()

            if not new_answer:
                print(f"  !! empty response for line {ln}, leaving unchanged")
                continue
            rec["messages"][2]["content"] = new_answer
            rec["meta"]["source"] = rec["meta"].get("source", "") + "_negation_fixed"

    for ln, rec in records.items():
        lines[ln - 1] = json.dumps(rec, ensure_ascii=False)

    TRAIN.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"patched {len(records)} line(s) in {TRAIN}")


if __name__ == "__main__":
    asyncio.run(main())
