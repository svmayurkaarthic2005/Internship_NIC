"""
Run a matrix of SIS questions through the real chatbot against sis_chatbot_db.

Each case asserts something cheap but meaningful about the answer -- that it is
non-empty, that it does not claim "no applications" when the officer has some,
that a knowledge question comes back with the documented facts rather than a
list of applications, and so on. The point is to catch answers that are wrong
in kind, which is what a routing bug looks like.

Run from the project root:
    python -m backend.sample_db.test_questions            # all
    python -m backend.sample_db.test_questions --fast     # skip LLM-backed ones
"""
from __future__ import annotations

import asyncio
import re
import sys
import uuid
from pathlib import Path

# Names in the extracts are Tamil, and a Windows console defaults to cp1252,
# which cannot encode them. Same guard as backend/main.py.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from sqlalchemy import select, text

from backend.database import AsyncSessionLocal
from backend.models import Application, SISOfficer
from backend.schemas import OfficerContext
from backend.services.auth_service import get_officer_jurisdiction_ids
from backend.services.chatbot import process_chat


def strip_html(s: str) -> str:
    return re.sub(r"<[^>]+>", " ", s or "")


def contains_any(*words):
    """Any one of `words` must appear. Placeholders like {APP} are filled in
    from the fixtures before matching, same as in the question text."""
    def check(answer, ctx):
        low = strip_html(answer).lower()
        wanted = [ctx["subs"].get(w, w).lower() for w in words]
        hit = [w for w in wanted if w in low]
        return (True, "") if hit else (False, f"none of {wanted} in answer")
    return check


def not_empty(answer, _ctx):
    return (bool(strip_html(answer).strip()), "empty answer")


def no_false_zero(answer, ctx):
    """Must not claim there is nothing when the officer demonstrably has work."""
    low = strip_html(answer).lower()
    bad = ["no applications", "there are 0", "found 0", "no results", "none found"]
    if ctx["active"] > 0 and any(b in low for b in bad):
        return False, f"claims nothing while officer has {ctx['active']} active"
    return True, ""


def not_a_clarification(answer, _ctx):
    """A general knowledge question must be answered, not bounced back.

    Routing bugs show up exactly here: the question goes to an intent that can
    only work on one application, and the officer gets "please specify an
    application number" instead of the rule they asked about.
    """
    low = strip_html(answer).lower()
    bounces = ["specify an application number", "please provide an application",
               "which application", "provide the application number"]
    hit = [b for b in bounces if b in low]
    return (not hit, f"answered with a clarification prompt: {hit}")


def all_of(*checks):
    def check(answer, ctx):
        for c in checks:
            ok, why = c(answer, ctx)
            if not ok:
                return False, why
        return True, ""
    return check


# (question, needs_llm, assertion)
CASES = [
    # --- counts and queues -------------------------------------------------
    ("What is my workload?", False,
     all_of(not_empty, contains_any("workload", "active"))),
    ("How many applications are pending with me?", False,
     all_of(not_empty, no_false_zero)),
    ("Show me my overdue applications", False,
     all_of(not_empty, no_false_zero)),
    ("Show me my applications", False, all_of(not_empty, no_false_zero)),
    ("List my ISD applications", False, all_of(not_empty, no_false_zero)),
    ("List my NISD applications", False, all_of(not_empty, no_false_zero)),
    ("Which field visits are unscheduled?", False, not_empty),
    ("What is my jurisdiction?", False,
     all_of(not_empty, contains_any("ward", "thoothukudi", "block"))),

    # --- a specific application -------------------------------------------
    ("Show me details of application {APP}", False,
     all_of(not_empty, contains_any("{APP}"))),
    ("What is the status of {APP}?", False,
     all_of(not_empty, contains_any("{APP}", "status", "stage"))),
    ("Who is the applicant for {APP}?", False, not_empty),
    ("What documents are missing in {APP}?", False, not_empty),
    ("Show workflow history of {APP}", False, not_empty),

    # --- survey / ownership ------------------------------------------------
    ("Who owns survey number {SURVEY}?", False,
     all_of(not_empty, contains_any("owner", "share"))),
    ("Show me the details of survey number {SURVEY}", False, not_empty),

    # --- procedural knowledge (RAG / LLM) ----------------------------------
    ("What documents are required for an ISD application?", True,
     all_of(not_empty,
            contains_any("sale deed"),
            contains_any("encumbrance"),
            contains_any("sketch", "photo"))),
    ("What documents are required for NISD?", True,
     all_of(not_empty, contains_any("sale deed", "encumbrance", "patta"))),
    ("What is service code 0154?", False,
     all_of(not_empty, contains_any("isd", "sub-division", "subdivision"))),
    ("What is the 15 working day rule?", True,
     all_of(not_empty, not_a_clarification,
            contains_any("field visit"), contains_any("15"))),
    ("Who approves a change of field visit date?", True,
     all_of(not_empty, not_a_clarification, contains_any("tahsildar"))),
    ("What are the steps in the ISD workflow?", True,
     all_of(not_empty, not_a_clarification,
            contains_any("field visit", "sketch"), contains_any("tahsildar", "dis"))),
    ("What is the escalation process?", True,
     all_of(not_empty, not_a_clarification,
            contains_any("escalation", "escalated", "tahsildar", "dro"))),
    # The exact figures matter: ISD is 30-35 working days, NISD 15-20. Asserting
    # only "working days" let a confidently wrong answer (NISD's number quoted
    # for ISD) pass.
    ("How long does an ISD application take?", True,
     all_of(not_empty, not_a_clarification, contains_any("30-35", "30 - 35"))),
    ("How long does a NISD application take?", True,
     all_of(not_empty, not_a_clarification, contains_any("15-20", "15 - 20"))),
    ("What happens if an application is rejected?", True,
     all_of(not_empty, not_a_clarification,
            contains_any("resubmit", "resubmission"), contains_any("reason", "30 days"))),
]


async def build_context(db, officer):
    jur = await get_officer_jurisdiction_ids(officer.id, db)
    ids = (jur["district_ids"] + jur["taluk_ids"] + jur["town_ids"]
           + jur["ward_ids"] + jur["block_ids"])
    return OfficerContext(
        officer_id=officer.id, employee_id=officer.employee_id, name=officer.name,
        email=officer.email, designation=officer.designation,
        jurisdiction_type=jur["jurisdiction_type"],
        jurisdiction_name=jur["jurisdiction_name"],
        jurisdiction_ids=[i for i in ids if i])


async def main(fast: bool) -> int:
    async with AsyncSessionLocal() as db:
        dbname = (await db.execute(text("SELECT current_database()"))).scalar()
        print("database:", dbname)

        officer = (await db.execute(
            select(SISOfficer).order_by(SISOfficer.employee_id))).scalars().first()
        ctx = await build_context(db, officer)
        print(f"officer : {officer.employee_id} {officer.name} "
              f"({ctx.jurisdiction_type} {ctx.jurisdiction_name})")

        app_no = (await db.execute(
            select(Application.application_number)
            .where(Application.assigned_officer_id == officer.id)
            .where(Application.current_status != "approved").limit(1))).scalar()
        survey = (await db.execute(text("""
            SELECT s.survey_no FROM survey_numbers s
            JOIN applications a ON a.survey_number_id = s.id
            WHERE a.assigned_officer_id = :o LIMIT 1"""),
            {"o": officer.id})).scalar()
        active = (await db.execute(text("""
            SELECT count(*) FROM applications
            WHERE assigned_officer_id = :o
              AND current_status IN ('pending','in_progress','escalated')"""),
            {"o": officer.id})).scalar()
        print(f"fixtures: application={app_no}  survey={survey}  active={active}\n")

        subs = {"{APP}": app_no, "{SURVEY}": str(survey)}
        env = {"active": active, "subs": subs}
        session = str(uuid.uuid4())
        passed = failed = skipped = 0
        failures = []

        for question, needs_llm, assertion in CASES:
            q = question
            for k, v in subs.items():
                q = q.replace(k, v)
            if needs_llm and fast:
                print(f"  skip {q}")
                skipped += 1
                continue

            # each case gets a fresh session so context cannot leak between them
            try:
                res = await process_chat(q, str(uuid.uuid4()), ctx, db)
                answer = res.get("response") or res.get("answer") or ""
            except Exception as exc:                             # noqa: BLE001
                failed += 1
                failures.append((q, f"raised {type(exc).__name__}: {exc}"))
                print(f"  FAIL {q}\n       raised {type(exc).__name__}: {exc}")
                continue

            ok, why = assertion(answer, env)
            flat = " ".join(strip_html(answer).split())
            if ok:
                passed += 1
                print(f"  ok   {q}\n       {flat[:150]}")
            else:
                failed += 1
                failures.append((q, why))
                print(f"  FAIL {q}\n       {why}\n       {flat[:220]}")

        print(f"\npassed={passed} failed={failed} skipped={skipped}")
        if failures:
            print("\nfailures:")
            for q, why in failures:
                print(f"  - {q}\n      {why}")
            return 1
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main("--fast" in sys.argv)))
