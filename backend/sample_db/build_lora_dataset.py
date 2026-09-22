"""
Build a LoRA/QLoRA fine-tuning dataset from the REAL sis_chatbot_db.

Every answer comes from the actual chat pipeline (process_chat) against real
officers and real jurisdiction data -- the deterministic handlers, not the
LLM, produce it, so every example is grounded truth. Re-run after a reseed
and the dataset regenerates itself correctly.

Four sources, mixed together:
  1. `question_bank.build_bank()` -- the project's own 688-question routing
     bank (English + a Tamil block), reused rather than re-invented.
  2. Deterministic typo injection over a sample of (1) -- a single adjacent
     transposition in one content word, the exact corruption
     `is_token_typo_match` / `_correct_typos` exist to survive.
  3. Hand-written Tanglish phrasings for the core intents.
  4. Multi-turn "bug scenario" chains -- the specific continuity/typo/logic
     bugs found and fixed during this project's chat-testing sessions
     (channel-clarification continuity, "thats all"/"thats it" confirmation,
     multi-status carried context, missing-CAN negation, IGRS-typo
     continuity, CAN-over-a-list follow-up, citizen/CSC typo tolerance) --
     kept as one multi-turn example per chain so the narrative survives.

Field-projection follow-ups ("their wards", "application no with type", ...)
are (setup, follow-up) pairs: one 2-turn example per pair, all pairs sharing
one `setup` call per (officer, setup) so the shared list is only fetched once.

Output: JSONL, one {"messages": [...]} per line -- the standard SFT chat
format (system/user/assistant, in some examples repeated across turns) that
axolotl / unsloth / LLaMA-Factory and an Ollama Modelfile ADAPTER build all
accept directly. QLoRA changes how the base model loads at train time (4-bit,
bitsandbytes), never this file's shape.

Usage:
    python -m backend.sample_db.build_lora_dataset [--out FILE] [--concurrency N]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
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

# The pipeline logs every query at INFO; at a few thousand calls that is most
# of the wall-clock cost. Silencing it doesn't touch DB behaviour, only I/O.
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
logging.getLogger("backend").setLevel(logging.WARNING)

from sqlalchemy import select

from backend.database import AsyncSessionLocal
from backend.models import SISOfficer
from backend.schemas import OfficerContext
from backend.services import chatbot
from backend.services.auth_service import get_officer_jurisdiction_ids
from backend.sample_db.question_bank import build_bank

SYSTEM_PROMPT = (
    "You are the SIS AI Assistant for Tamil Nadu Sub Inspector Surveyor "
    "officers. Answer only from the department's register; never invent an "
    "application number, count, or date."
)

# ── 2. Deterministic typo injection ──────────────────────────────────────────
# A single adjacent-letter transposition in one content word (>=5 letters,
# swap positions 2/3) -- exactly the Damerau-Levenshtein-distance-1 error
# `is_token_typo_match` is built to absorb, and the shape officers actually
# type fast ("aplication" is a drop, "irgs" for "igrs" is a transposition --
# both covered; this generator does the transposition half deterministically
# so re-running produces the identical file).
_WORD_RE = re.compile(r"[A-Za-z]{5,}")


def _corrupt(text: str) -> str | None:
    m = _WORD_RE.search(text)
    if not m:
        return None
    w = m.group(0)
    swapped = w[:2] + w[3] + w[2] + w[4:]
    if swapped == w:
        return None
    return text[:m.start()] + swapped + text[m.end():]


# ── 3. Tanglish phrasings for the core intents ───────────────────────────────
TANGLISH = [
    "enaku evlo applications pending iruku",
    "en workload enna",
    "overdue applications evlo iruku",
    "isd applications kaatunga",
    "nisd applications list pannunga",
    "en jurisdiction enna",
    "csc la irundhu vantha applications kaatunga",
    "sub registrar vazhiya vantha applications enna",
    "field visit evlo pending iruku",
    "field visit date naan maatha mudiyuma",
    "en last application enna",
    "en last application approve aachaa",
    "isd nisd rendukum en vithiyasam",
    "0154 service code enna",
    "can number nu sonna enna",
    "can number evlo digit iruku",
    "igrs form 6 number irukaa CSC application ku",
    "yaaru sub registrar",
    "total fee evlo en applications ku",
    "ward 102 ward 103 compare pannunga",
    "isd nisd yaaru longer time edukum approve aaga",
    "enaku ippo enna pandra",  # what can you do
    "clear pannunga",
    "nandri",
    "vanakkam",
]

# ── 4. Multi-turn bug-scenario chains ────────────────────────────────────────
# Each chain is (label, [turn1, turn2, ...]) -- exactly the reproduction
# sequences used during this project's live-testing sessions, in English,
# Tamil and Tanglish. Kept as ONE multi-turn example per chain: the point is
# the model learning to carry a scope, a disambiguation, or a partial phrase
# across turns, which a split-up pairing would lose.
BUG_SCENARIO_CHAINS = [
    ("channel_clarify_sro_typo", [
        "seo anupuna application shw panu",
        "Sub-Registrar",
    ]),
    ("channel_clarify_csc_typo", [
        "scs anupuna application shw panu",
        "Common Service Centre",
    ]),
    ("channel_clarify_truncated_csc", [
        "csc anupuna application kaatunga",
        "ommon Service Centre",
    ]),
    ("thats_all_after_csc_listing", [
        "show applications from csc",
        "thats all ?",
    ]),
    ("thats_it_typo_confirmation", [
        "show applications",
        "tahts it",
    ]),
    ("missing_can_number_negation", [
        "display application without CAn number",
    ]),
    ("igrs_typo_continuity", [
        "show applicatioon from csc",
        "show their irgs",
    ]),
    ("can_number_over_whole_list", [
        "show all applications",
        "what is the can number of all the above application",
    ]),
    ("citizen_typo_tolerance_1", [
        "show citezin applications",
    ]),
    ("citizen_typo_tolerance_2", [
        "show cetizen applications",
    ]),
    ("multi_status_carried_context", [
        "show pending, in progress, escalated, approved and rejected applications",
        "show the pending ones",
    ]),
    ("field_projection_applicant_vs_can", [
        "show my applications",
        "their applicant name and status",
    ]),
    ("field_projection_app_no_only", [
        "show my applications",
        "application no only",
    ]),
    ("field_projection_multi_field", [
        "show applications from csc",
        "application no with type and status",
    ]),
    ("wards_blocks_plural", [
        "show my applications",
        "their wards and blocks",
    ]),
    ("scoped_refine_after_period_listing", [
        "show applications from last month",
        "show only NISD",
    ]),
    ("channel_clarify_tamil", [
        "csc la irundha application kaatunga scs",
        "பொது சேவை மையம்",
    ]),
    ("igrs_rule_not_a_listing", [
        "if the IGRS number is absent, what does it mean, so it's not from SRO?",
    ]),
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
_MAX_TABLE_ROWS = 8


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
    text = text.strip()
    # A 50-row table teaches the model to reproduce 50 rows verbatim, which is
    # neither realistic (the LLM never sees the real rows at inference) nor
    # useful (the deterministic table renderer already owns this, unchanged --
    # see CLAUDE.md's "numeric data always comes from the database"). Capped
    # to a sample plus the true count, which is the fact worth learning.
    lines = text.split("\n")
    if len(lines) > _MAX_TABLE_ROWS + 2:
        header, rows = lines[0], lines[1:]
        kept = rows[:_MAX_TABLE_ROWS]
        text = "\n".join([header, *kept,
                           f"... ({len(rows) - _MAX_TABLE_ROWS} more row(s) omitted)"])
    return text


_PROJECT_FOLLOWUPS = [
    "their wards", "their vards",
    "their blocks", "their blocs",
    "their status", "their satus",
    "their stage",
    "their type", "thier type",
    "their survey numbers", "their survay numbers",
    "their can numbers", "their can nubers",
    "their submission channel",
    "their applicant names", "their aplicant names",
    "their mobile numbers",
    "their fee amounts",
    "their submission dates",
    "their igrs numbers", "their irgs numbers",
    "application no with type",
    "application no with type and status",
    "application no only",
    "app number with can number",
    "application number and ward",
]

_PROJECT_SETUPS = [
    "Show my applications",
    "Show applications from CSC",
    "Show applications from the Sub-Registrar",
    "Show my ISD applications",
    "Show my pending applications",
]


class DatasetBuilder:
    def __init__(self, concurrency: int):
        self.sem = asyncio.Semaphore(concurrency)
        self.examples: list[dict] = []
        self.lock = asyncio.Lock()

    async def _chat(self, db, message: str, session_id: str, ctx: OfficerContext) -> dict:
        # A question that falls through every deterministic handler reaches
        # the agent's tool-calling loop, which calls llama3.1:8b -- serialized
        # on the one local Ollama instance, so a dozen of these running
        # concurrently queue up behind each other for minutes. Those answers
        # are also not what this dataset is for: it exists to teach the model
        # the deterministic, grounded answers it currently DOESN'T reliably
        # produce, and an LLM-generated answer is not ground truth to imitate.
        # A generous timeout is a safety net, not the primary defence -- see
        # `_bank_is_deterministic` below, which is the real filter.
        try:
            return await asyncio.wait_for(
                chatbot.process_chat(message, session_id, ctx, db, chat_history=[]),
                timeout=20.0)
        except asyncio.TimeoutError:
            return {}

    async def add(self, example: dict) -> None:
        async with self.lock:
            self.examples.append(example)

    async def single_turn(self, officer: SISOfficer, ctx: OfficerContext,
                           message: str, meta_extra: dict) -> None:
        async with self.sem:
            async with AsyncSessionLocal() as db:
                result = await self._chat(db, message, str(uuid.uuid4()), ctx)
        answer = _flatten_html(result.get("response") or "")
        if not answer:
            return
        await self.add({
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": message},
                {"role": "assistant", "content": answer},
            ],
            "meta": {"officer": officer.email, "intent": result.get("intent"), **meta_extra},
        })

    async def bug_chain(self, officer: SISOfficer, ctx: OfficerContext,
                         label: str, turns: list[str]) -> None:
        async with self.sem:
            async with AsyncSessionLocal() as db:
                session_id = str(uuid.uuid4())
                messages = [{"role": "system", "content": SYSTEM_PROMPT}]
                intents = []
                for turn in turns:
                    result = await self._chat(db, turn, session_id, ctx)
                    answer = _flatten_html(result.get("response") or "")
                    if not answer:
                        return
                    messages += [{"role": "user", "content": turn},
                                 {"role": "assistant", "content": answer}]
                    intents.append(result.get("intent"))
        await self.add({
            "messages": messages,
            "meta": {"officer": officer.email, "scenario": label, "intents": intents},
        })

    async def project_setup_group(self, officer: SISOfficer, ctx: OfficerContext,
                                   setup: str, followups: list[str]) -> None:
        async with self.sem:
            async with AsyncSessionLocal() as db:
                session_id = str(uuid.uuid4())
                setup_result = await self._chat(db, setup, session_id, ctx)
                setup_answer = _flatten_html(setup_result.get("response") or "")
                if not setup_answer:
                    return
                for message in followups:
                    result = await self._chat(db, message, session_id, ctx)
                    answer = _flatten_html(result.get("response") or "")
                    if not answer:
                        continue
                    await self.add({
                        "messages": [
                            {"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": setup},
                            {"role": "assistant", "content": setup_answer},
                            {"role": "user", "content": message},
                            {"role": "assistant", "content": answer},
                        ],
                        "meta": {"officer": officer.email, "setup": setup,
                                 "intent": result.get("intent")},
                    })


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

    # "workflow_knowledge" is almost entirely `expect="general_query"` --
    # procedural questions answered from the document corpus via the LLM/agent
    # fallback, not a deterministic handler (the 4 service-code exceptions
    # already carry a real `expect`, so they survive this filter). Excluded
    # for the reason in `DatasetBuilder._chat`: slow (serialized on Ollama)
    # and not grounded database truth to fine-tune the model toward.
    bank = [q for q in build_bank()
            if not (q.group == "workflow_knowledge" and q.expect == "general_query")]
    typo_sample = [q for i, q in enumerate(bank) if i % 3 == 0]

    builder = DatasetBuilder(concurrency)
    tasks = []

    for officer, ctx in officer_ctxs:
        # 1. the routing bank, verbatim
        for q in bank:
            tasks.append(builder.single_turn(officer, ctx, q.text, {"source": "bank", "group": q.group}))
        # 2. typo-injected variants of a third of the bank
        for q in typo_sample:
            corrupted = _corrupt(q.text)
            if corrupted:
                tasks.append(builder.single_turn(officer, ctx, corrupted,
                                                  {"source": "typo", "group": q.group}))
        # 3. Tanglish
        for q in TANGLISH:
            tasks.append(builder.single_turn(officer, ctx, q, {"source": "tanglish"}))
        # 4. bug-scenario chains
        for label, turns in BUG_SCENARIO_CHAINS:
            tasks.append(builder.bug_chain(officer, ctx, label, turns))
        # 5. field-projection follow-ups, grouped by setup (one setup call each)
        for setup in _PROJECT_SETUPS:
            tasks.append(builder.project_setup_group(officer, ctx, setup, _PROJECT_FOLLOWUPS))

    print(f"Running {len(tasks)} tasks (concurrency={concurrency}) ...")
    done = 0
    for i in range(0, len(tasks), 200):
        await asyncio.gather(*tasks[i:i + 200])
        done += len(tasks[i:i + 200])
        print(f"  {done}/{len(tasks)} tasks done, {len(builder.examples)} examples so far")

    with out_path.open("w", encoding="utf-8") as f:
        for ex in builder.examples:
            f.write(json.dumps(ex, ensure_ascii=False) + "\n")

    print(f"\nWrote {len(builder.examples)} examples to {out_path}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=Path("lora_dataset.jsonl"))
    parser.add_argument("--concurrency", type=int, default=12)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.out, args.concurrency)))
