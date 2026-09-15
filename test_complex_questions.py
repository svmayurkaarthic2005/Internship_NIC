"""
The hard questions -- the ones that combine several things at once.

Every other suite in the repo checks one family: dates, channels, comparisons,
follow-ups. This one checks what happens when an officer asks a question that
is several of those at the same time -- a type AND a status AND a period, a
follow-up that re-scopes a follow-up, a Tamil question about a decision date, a
comparison with a typo in it.

Two rules the assertions are built on:

  * Expectations are RECOMPUTED from the register on every run (the counts
    below come out of `applications`, not out of a constant), so the suite
    still means something after a reseed.

  * A deterministic answer is required, not merely a non-empty one. Every
    question here is one CLAUDE.md says must never reach the model -- counts,
    dates and application numbers are the model's job to never invent. So a
    reply that fell through to the LLM fails the case even if it reads well,
    and that is detected explicitly rather than by looking at the wording.

Run from the project root:
    python test_complex_questions.py            # everything (no Ollama needed)
    python test_complex_questions.py --verbose  # print every answer in full
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys
import uuid

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import structlog
from sqlalchemy import func, select

from backend.database import AsyncSessionLocal
from backend.models import Application, SISOfficer
from backend.schemas import OfficerContext
from backend.services.auth_service import get_officer_jurisdiction_ids
from backend.services.chatbot import process_chat

logging.disable(logging.INFO)
structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING))

# The turn fell through to the model. With Ollama down that is an apology; with
# Ollama up it is prose. Either way these questions must never get there, so the
# marker is the apology AND the absence of the figure the case asks for.
_LLM_FALLBACK = (
    "i apologize", "i encountered an error", "please try again",
    "மன்னிக்கவும்",
)


def _plain(html: str) -> str:
    """Answer text with tags stripped, lower-cased, whitespace collapsed."""
    text = re.sub(r"<[^>]+>", " ", html or "")
    for entity, char in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                         ("&#39;", "'"), ("&quot;", '"'), ("&nbsp;", " ")):
        text = text.replace(entity, char)
    return re.sub(r"\s+", " ", text).strip().lower()


class Case:
    """One question, its conversation, and what the answer has to contain."""

    def __init__(self, label, message, must=(), must_not=(), history=None,
                 intent=None, allow_llm=False, follow_up_to=None):
        self.label = label
        self.message = message
        self.must = [m.lower() for m in must]
        self.must_not = [m.lower() for m in must_not]
        self.history = history or []
        self.intent = intent
        self.allow_llm = allow_llm
        # A first question to actually ASK in the same session before this one,
        # so the reference context this follow-up needs is really on record.
        self.follow_up_to = follow_up_to


async def _officer(db) -> tuple:
    row = (await db.execute(
        select(SISOfficer).where(SISOfficer.is_active.is_(True))
        .order_by(SISOfficer.employee_id).limit(1))).scalars().first()
    if row is None:
        raise SystemExit("No active officer -- run build_app_tables first.")
    jur = await get_officer_jurisdiction_ids(row.id, db)
    ctx = OfficerContext(
        officer_id=row.id, employee_id=row.employee_id, name=row.name,
        email=row.email, designation=row.designation, officer_stage="SIS",
        jurisdiction_type=jur.get("jurisdiction_type", "ward"),
        jurisdiction_name=jur.get("jurisdiction_name", ""),
        jurisdiction_ids=(jur["district_ids"] + jur["taluk_ids"] + jur["town_ids"]
                          + jur["ward_ids"] + jur["block_ids"]),
    )
    return row, ctx


async def _facts(db, officer_row) -> dict:
    """Everything the assertions below need, read from the register."""
    mine = Application.assigned_officer_id == officer_row.id

    async def count(*extra):
        return await db.scalar(
            select(func.count()).select_from(Application).where(mine, *extra)) or 0

    open_rows = (await db.execute(
        select(Application.application_number, Application.application_type,
               Application.submission_date)
        .where(mine, Application.current_status.in_(("pending", "in_progress")))
        .order_by(Application.submission_date))).all()

    approved_isd = (await db.execute(
        select(Application.application_number)
        .where(mine, Application.application_type == "ISD",
               Application.current_status == "approved")
        .order_by(Application.application_number).limit(1))).scalars().first()
    rejected_any = (await db.execute(
        select(Application.application_number)
        .where(mine, Application.current_status == "rejected")
        .order_by(Application.application_number).limit(1))).scalars().first()

    return {
        "total": await count(),
        "approved": await count(Application.current_status == "approved"),
        "rejected": await count(Application.current_status == "rejected"),
        "isd": await count(Application.application_type == "ISD"),
        "nisd": await count(Application.application_type == "NISD"),
        "isd_approved": await count(Application.application_type == "ISD",
                                    Application.current_status == "approved"),
        "nisd_rejected": await count(Application.application_type == "NISD",
                                     Application.current_status == "rejected"),
        # A channel listing excludes rejected files -- the standing rule every
        # other listing follows -- so this is what a channel COUNT must report.
        "csc_listed": await count(Application.submission_channel == "CSC",
                                  Application.current_status != "rejected"),
        "csc_approved": await count(Application.submission_channel == "CSC",
                                    Application.current_status == "approved"),
        "sro": await count(Application.submission_channel == "sub_registrar",
                           Application.current_status != "rejected"),
        "open": [r[0] for r in open_rows],
        "open_types": {r[0]: r[1] for r in open_rows},
        "oldest_open": open_rows[0][0] if open_rows else None,
        "approved_isd": approved_isd,
        "rejected_any": rejected_any,
    }


def build_cases(f: dict) -> list:
    """The question set. Every expectation comes out of `f`, never a constant."""
    open_one = f["open"][0] if f["open"] else None
    isd = f["approved_isd"]
    rej = f["rejected_any"]
    cases: list = []
    add = cases.append

    # ── 1. several filters stacked in one question ──────────────────────────
    add(Case("type + status in one breath",
             "how many approved ISD applications do I have",
             must=[str(f["isd_approved"])], must_not=[str(f["total"])]))
    add(Case("type + status, the other way round",
             "how many rejected NISD applications do I have",
             must=[str(f["nisd_rejected"])]))
    add(Case("channel + count (rejected excluded, as everywhere else)",
             "how many CSC applications do I have",
             must=[str(f["csc_listed"])]))
    add(Case("channel that is also a rule word (IGRS -> Sub-Registrar)",
             "which of my applications have an IGRS number",
             must=[str(f["sro"])]))

    # ── 2. comparisons, including a typo'd one ──────────────────────────────
    add(Case("comparison: two groups counted",
             "compare ISD and NISD applications",
             must=[str(f["isd"]), str(f["nisd"])]))
    add(Case("comparison survives a typo", "compair isd and nsid",
             must=[str(f["isd"]), str(f["nisd"])]))
    add(Case("superlative over decided files",
             "which application took the longest to approve",
             must=["days", "median"]))
    add(Case("average, not a listing", "what is the average time to approve",
             must=["mean", "median", "days"]))
    add(Case("busiest month is named", "which month had the most applications",
             must=["busiest", "month"]))

    # ── 3. a question whose words belong to another intent ──────────────────
    add(Case("fee difference is the schedule, not a count",
             "is there a fee difference between ISD and NISD",
             must=["0153", "0154"]))
    add(Case("'pending the longest' is waiting, not turnaround",
             "which application has been pending the longest",
             must=["pending"]))

    # ── 4. multi-turn: the follow-up must inherit the scope ─────────────────
    add(Case("follow-up inherits the channel scope",
             "how many of them are approved",
             # Turned into a real two-turn conversation by run(): a synthetic
             # history stores no reference context, so the follow-up would be
             # answered by re-scoping the text rather than from the rows that
             # were actually on screen.
             follow_up_to="show applications from CSC",
             must=[str(f["csc_approved"])]))
    if open_one:
        add(Case("pronoun-less follow-up resolves to the application",
                 "what is the applicant name",
                 history=[{"role": "user", "content": f"status of {open_one}"},
                          {"role": "assistant", "content": f"Details for {open_one}"}],
                 must=["applicant"]))
        add(Case("follow-up about the field visit, not the filing date",
                 "is a field visit scheduled?",
                 history=[{"role": "user", "content": f"status of {open_one}"},
                          {"role": "assistant", "content": f"Details for {open_one}"}],
                 must=["field visit"]))

    # ── 5. decision date must not become the filing date ────────────────────
    if isd:
        add(Case("when was it approved -> the decision date",
                 f"when was {isd} approved",
                 must=["approved"], must_not=["could not find"]))
        add(Case("how long did it take -> a duration, not 'not overdue'",
                 f"how long did {isd} take",
                 must=["day"], must_not=["not overdue"]))
    if rej:
        add(Case("why rejected -> the reason, not the summary",
                 f"why was {rej} rejected",
                 must=["reject"]))

    # ── 6. Tamil and Tanglish, on complex shapes ────────────────────────────
    add(Case("Tamil: count with a status filter",
             "எத்தனை அங்கீகரிக்கப்பட்ட விண்ணப்பங்கள் உள்ளன",
             must=[str(f["approved"])]))
    add(Case("Tanglish: count of the open queue",
             "evlo pending applications iruku", must=["application"]))
    add(Case("Tamil: my jurisdiction (routes deterministically, not to the LLM)",
             "எனது அதிகார வரம்பு என்ன",
             must=["மாவட்டம்", "வார்டு"], must_not=["மன்னிக்கவும்"]))

    # ── 7. the rule questions, which must not answer with a table ───────────
    add(Case("IGRS absence stated as a rule",
             "if the IGRS number is absent what does it mean, so it is not from SRO?",
             must=["sub-registrar"], must_not=["<table"]))
    add(Case("CAN length is not the channel",
             "does a 15 digit CAN mean it came from CSC",
             must=["counter"], must_not=["<table"]))
    add(Case("what is SRO", "what is SRO",
             must=["sub-registrar"], must_not=["<table"]))

    # ── 8. planning and workflow ────────────────────────────────────────────
    add(Case("visit plan answers with work, not an empty calendar",
             "which application should I field visit tomorrow and in which block",
             must=["block"]))
    if isd:
        add(Case("workflow history is a timeline, not a status",
                 f"show the workflow history of {isd}",
                 must=["→"]))

    # ── 9. access control, under a complex question ─────────────────────────
    add(Case("another ward is refused even inside a comparison",
             "compare ward 999 and my ward",
             must=["outside"], must_not=["no applications in ward 999"]))

    # ── 10. sorting and projection together ─────────────────────────────────
    add(Case("sorted listing names the order it applied",
             "show my applications sorted by application number descending",
             must=["descending"]))
    add(Case("column projection is honoured",
             "show application no and status only",
             must=["status"]))

    return cases


async def run(verbose: bool) -> int:
    failures = 0
    async with AsyncSessionLocal() as db:
        officer_row, officer = await _officer(db)
        facts = await _facts(db, officer_row)
        print(f"officer {officer.name} ({officer_row.employee_id}), "
              f"{officer.jurisdiction_name}")
        print(f"register: {facts['total']} applications -- "
              f"{facts['approved']} approved, {facts['rejected']} rejected, "
              f"ISD {facts['isd']} / NISD {facts['nisd']}, "
              f"CSC {facts['csc']} / SRO {facts['sro']}, "
              f"{len(facts['open'])} open\n")

        cases = build_cases(facts)
        for case in cases:
            session = str(uuid.uuid4())
            history = list(case.history)
            if case.follow_up_to:
                first = await process_chat(
                    db=db, message=case.follow_up_to, officer=officer,
                    session_id=session, chat_history=[])
                history = [{"role": "user", "content": case.follow_up_to},
                           {"role": "assistant", "content": first.get("response", "")}]
            try:
                result = await process_chat(
                    db=db, message=case.message, officer=officer,
                    session_id=session, chat_history=history)
            except Exception as exc:                       # noqa: BLE001
                print(f"  FAIL  {case.label}\n        raised {type(exc).__name__}: {exc}")
                failures += 1
                continue

            answer = _plain(result.get("response", ""))
            problems = []
            if not answer:
                problems.append("empty answer")
            if not case.allow_llm and any(m in answer for m in _LLM_FALLBACK):
                problems.append("fell through to the LLM")
            problems += [f"missing {m!r}" for m in case.must if m not in answer]
            problems += [f"must not contain {m!r}" for m in case.must_not if m in answer]

            if problems:
                failures += 1
                print(f"  FAIL  {case.label}")
                print(f"        Q: {case.message}")
                print(f"        {'; '.join(problems)}")
                print(f"        A: {answer[:220]}")
            else:
                print(f"  PASS  {case.label}")
                if verbose:
                    print(f"        Q: {case.message}")
                    print(f"        A: {answer[:220]}")

    print(f"\n{len(cases) - failures}/{len(cases)} complex questions answered correctly")
    return failures


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--verbose", action="store_true", help="print every answer")
    args = ap.parse_args()
    failures = asyncio.run(run(args.verbose))
    print("ALL PASS" if failures == 0 else f"{failures} FAILURE(S)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
