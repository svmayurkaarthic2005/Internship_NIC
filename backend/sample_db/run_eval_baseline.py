"""
Run the held-out evaluation set (`eval_set.jsonl`) against the CURRENT
pipeline -- deterministic handlers + the un-fine-tuned llama3.1:8b agent
fallback -- and record every answer, verbatim, as the pre-QLoRA baseline.

This set is never added to the training data (train.jsonl / validation.jsonl
come from the cleaned/deduplicated generation pass; this file is a separate,
hand-curated set built specifically so a comparison after fine-tuning is not
comparing the model against questions it was trained on).

A "||"-separated question ("show applications||their survey numbers") is a
2-turn conversation: the first half sets up context, the second is the turn
actually being evaluated (both are recorded).

Usage:
    python -m backend.sample_db.run_eval_baseline [--out FILE] [--officer EMAIL]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
import time
import uuid
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

from backend.database import AsyncSessionLocal, engine
from backend.models import SISOfficer
from backend.services import chatbot
from backend.sample_db.build_lora_dataset import build_officer_context, _flatten_html

_EVAL_SET = Path(__file__).resolve().parents[2] / "backend" / "sample_db" / "eval_set.jsonl"


async def run_one(db, ctx, question: str) -> dict:
    turns = question.split("||")
    session_id = str(uuid.uuid4())
    record = {"question": question, "turns": []}
    for turn in turns:
        t0 = time.perf_counter()
        try:
            result = await asyncio.wait_for(
                chatbot.process_chat(turn, session_id, ctx, db, chat_history=[]),
                timeout=60.0)
        except asyncio.TimeoutError:
            result = {"response": None, "intent": "TIMEOUT"}
        record["turns"].append({
            "message": turn,
            "intent": result.get("intent"),
            "answer": _flatten_html(result.get("response") or ""),
            "latency_s": round(time.perf_counter() - t0, 2),
        })
    return record


_FALLBACK_MARKERS = (
    "i could not find", "this is taking longer", "not in the sis register", "i do not know which",
    "i don't have an application or list", "please specify the application number", "i can only report",
    "i do not have that amount", "that is too broad",
)
_APP_NO = re.compile(r"\b20\d\d/0\d{3}/\d{1,3}/\d{4,6}\b")


async def classify_result(db, record: dict) -> str:
    """timeout | empty | fallback | unverified | answered  -- for the evaluated (last) turn.

    "unverified" = the answer names an application number that is not in the register,
    or (on the LLM path only) states a rupee amount / clock time / web address. Correctness
    of an ordinary answer needs a reference answer, which this set does not carry, so it
    is reported as `answered` and read by a person."""
    from sqlalchemy import text
    t = record["turns"][-1]
    ans = (t["answer"] or "").strip()
    if t["intent"] == "TIMEOUT":
        return "timeout"
    if not ans:
        return "empty"
    low = ans.lower()
    if any(m in low for m in _FALLBACK_MARKERS):
        return "fallback"
    for no in set(_APP_NO.findall(ans)):
        found = (await db.execute(text("SELECT 1 FROM applications WHERE application_number = :n LIMIT 1"),
                                  {"n": no})).first()
        if not found:
            return "unverified"
    if t["intent"] == "general_query":
        _, amounts = chatbot._scrub_unverified_amounts(ans, None)
        _, specifics = chatbot._scrub_unverified_specifics(ans, None)
        if amounts or specifics:
            return "unverified"
    return "answered"


def _pct(values, p):
    if not values:
        return 0.0
    values = sorted(values)
    return values[min(len(values) - 1, int(round(p / 100 * (len(values) - 1))))]


async def main(out_path: Path, officer_email: str | None) -> int:
    cases = []
    with _EVAL_SET.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    print(f"Loaded {len(cases)} eval cases from {_EVAL_SET}")

    async with AsyncSessionLocal() as db:
        q = select(SISOfficer).where(SISOfficer.is_active == True)
        if officer_email:
            q = q.where(SISOfficer.email == officer_email)
        officer_row = (await db.execute(q.order_by(SISOfficer.employee_id).limit(1))).scalars().first()
        if not officer_row:
            print("No matching officer found.")
            return 1
        ctx = await build_officer_context(db, officer_row)
        print(f"Running as {officer_row.email}")

    results = []
    async with AsyncSessionLocal() as db:
        for i, case in enumerate(cases):
            record = await run_one(db, ctx, case["question"])
            record["category"] = case.get("category")
            results.append(record)
            if (i + 1) % 10 == 0:
                print(f"  {i + 1}/{len(cases)} done")

    out_path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in results),
        encoding="utf-8")
    print(f"\nWrote {len(results)} results to {out_path}")

    from collections import Counter
    async with AsyncSessionLocal() as db:
        kinds = [await classify_result(db, r) for r in results]
    for r, k in zip(results, kinds):
        r["result"] = k
    out_path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in results), encoding="utf-8")
    counts = Counter(kinds)
    lat = [r["turns"][-1]["latency_s"] for r in results]
    llm = [r["turns"][-1]["latency_s"] for r in results if r["turns"][-1]["intent"] == "general_query"]
    print(f"total {len(results)}: answered {counts['answered']}   fallback {counts['fallback']}   "
          f"unverified {counts['unverified']}   empty {counts['empty']}   timeout {counts['timeout']}")
    print(f"latency  p50 {_pct(lat, 50):.1f}s   p95 {_pct(lat, 95):.1f}s   max {max(lat):.1f}s   "
          f"(LLM-path: {len(llm)} questions, p50 {_pct(llm, 50):.1f}s, p95 {_pct(llm, 95):.1f}s)")
    for r, k in zip(results, kinds):
        if k in ("timeout", "empty", "unverified"):
            print(f"  {k.upper():10s} {r['question'][:90]}")
    await engine.dispose()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=Path("eval_baseline_results.jsonl"))
    parser.add_argument("--officer", type=str, default=None)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.out, args.officer)))
