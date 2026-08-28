"""
Run the generated question bank (688 questions) against the router, and the
behaviour-critical groups against the real chatbot.

Two passes, because they cost very different amounts:

  routing  -- all 688 through parse_intent. No DB, no LLM, runs in seconds.
              Catches crashes and questions that fall through to general_query
              when they should have hit a data handler.

  answers  -- the groups where a specific behaviour is required:
                * field visit date change -> must name the Tahsildar
                * out of scope            -> must decline
                * negation                -> must not answer the opposite
                * tamil                   -> must answer, in Tamil
              These go through process_chat, so they need the database and
              Ollama.

    python -m backend.sample_db.test_question_bank                # routing only
    python -m backend.sample_db.test_question_bank --answers      # + behaviour
    python -m backend.sample_db.test_question_bank --answers --group tamil
"""
from __future__ import annotations

import asyncio
import re
import sys
import uuid
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from sqlalchemy import select

from backend.database import AsyncSessionLocal
from backend.models import SISOfficer
from backend.sample_db.question_bank import build_bank
from backend.services.rag import parse_intent

TAMIL_RANGE = re.compile(r"[஀-௿]")


def strip_html(s: str) -> str:
    return re.sub(r"<[^>]+>", " ", s or "")


def flat(s: str) -> str:
    return " ".join(strip_html(s).split())


# ── pass 1: routing ──────────────────────────────────────────────────────

def run_routing() -> tuple[int, list[str]]:
    bank = build_bank()
    failures: list[str] = []
    intents = Counter()
    by_group_general = Counter()

    for q in bank:
        try:
            intent = parse_intent(q.text)
        except Exception as exc:                                 # noqa: BLE001
            failures.append(f"parse_intent raised on {q.text!r}: {exc}")
            print(f"  CRASH {q.text[:70]}: {type(exc).__name__}: {exc}")
            continue
        intents[intent] += 1
        if intent == "general_query":
            by_group_general[q.group] += 1
        if q.expect and intent != q.expect:
            failures.append(f"{q.text!r}: expected {q.expect}, got {intent}")

    print(f"routed {len(bank)} questions into {len(intents)} distinct intents")
    print("\ntop intents:")
    for intent, n in intents.most_common(12):
        print(f"  {intent:34s} {n}")
    print("\nfell through to general_query, by group:")
    for group, n in by_group_general.most_common():
        print(f"  {group:22s} {n}")
    return len(bank), failures


# ── pass 2: behaviour ────────────────────────────────────────────────────

def check_tahsildar(answer: str, _q) -> tuple[bool, str]:
    """workflow_guide.txt: only the Tahsildar approves a field visit date change."""
    low = flat(answer).lower()
    if "tahsildar" in low:
        return True, ""
    return False, "does not tell the officer to ask the Tahsildar"


def check_declined(answer: str, _q) -> tuple[bool, str]:
    """An out-of-scope question must be refused, not answered."""
    low = flat(answer).lower()
    decline_markers = [
        "not able to", "cannot help", "can't help", "outside", "out of scope",
        "don't have information", "do not have information", "not related",
        "i'm not", "i am not", "unable to", "only assist", "only help",
        "not a cooking", "sis", "survey", "application",
    ]
    if any(m in low for m in decline_markers):
        return True, ""
    return False, f"appears to answer it: {flat(answer)[:90]}"


def check_not_empty(answer: str, _q) -> tuple[bool, str]:
    return (bool(flat(answer)), "empty answer")


def check_tamil(answer: str, _q) -> tuple[bool, str]:
    text = flat(answer)
    if not text:
        return False, "empty answer"
    if not TAMIL_RANGE.search(text):
        return False, f"answered a Tamil question without Tamil: {text[:80]}"
    return True, ""


BEHAVIOUR_CHECKS = {
    "fv_date_change": check_tahsildar,
    "out_of_scope": check_declined,
    "negation": check_not_empty,
    "tamil": check_tamil,
}


async def run_answers(only_group: str | None) -> list[str]:
    from backend.sample_db.test_questions import build_context
    from backend.services.chatbot import process_chat

    bank = [q for q in build_bank() if q.group in BEHAVIOUR_CHECKS
            and (only_group is None or q.group == only_group)]
    failures: list[str] = []

    async with AsyncSessionLocal() as db:
        officer = (await db.execute(
            select(SISOfficer).order_by(SISOfficer.employee_id))).scalars().first()
        ctx = await build_context(db, officer)

        # The bank's application/survey literals are placeholders. Swap in rows
        # this officer actually owns, otherwise the answer is a (correct)
        # jurisdiction refusal and the behaviour under test never runs.
        from sqlalchemy import text as _text
        from backend.models import Application
        from backend.sample_db.question_bank import APP_ISD, APP_NISD, SURVEY
        real_isd = (await db.execute(
            select(Application.application_number)
            .where(Application.assigned_officer_id == officer.id)
            .where(Application.application_type == "ISD").limit(1))).scalar()
        real_nisd = (await db.execute(
            select(Application.application_number)
            .where(Application.assigned_officer_id == officer.id)
            .where(Application.application_type == "NISD").limit(1))).scalar()
        real_survey = (await db.execute(_text(
            "SELECT s.survey_no FROM survey_numbers s "
            "JOIN applications a ON a.survey_number_id = s.id "
            "WHERE a.assigned_officer_id = :o LIMIT 1"), {"o": officer.id})).scalar()

        def localise(t: str) -> str:
            if real_isd:
                t = t.replace(APP_ISD, real_isd)
            if real_nisd:
                t = t.replace(APP_NISD, real_nisd)
            if real_survey:
                t = t.replace(SURVEY, str(real_survey))
            return t

        for q in bank:
            check = BEHAVIOUR_CHECKS[q.group]
            text_q = localise(q.text)
            try:
                res = await process_chat(text_q, str(uuid.uuid4()), ctx, db)
                answer = res.get("response") or ""
            except Exception as exc:                             # noqa: BLE001
                failures.append(f"[{q.group}] {text_q!r} raised {type(exc).__name__}: {exc}")
                print(f"  CRASH [{q.group}] {text_q[:60]}")
                continue
            ok, why = check(answer, q)
            mark = "ok  " if ok else "FAIL"
            print(f"  {mark} [{q.group}] {text_q[:58]:58s} | {flat(answer)[:70]}")
            if not ok:
                failures.append(f"[{q.group}] {text_q!r}: {why}")
    return failures


def main() -> int:
    only_group = None
    if "--group" in sys.argv:
        only_group = sys.argv[sys.argv.index("--group") + 1]

    print("[1/2] routing\n")
    total, failures = run_routing()

    if "--answers" in sys.argv:
        print("\n[2/2] behaviour that must hold\n")
        failures += asyncio.run(run_answers(only_group))
    else:
        print("\n[2/2] behaviour checks skipped (pass --answers)")

    print()
    if failures:
        print(f"FAILED ({len(failures)}) of {total} questions:")
        for f in failures[:40]:
            print("  -", f)
        if len(failures) > 40:
            print(f"  ... and {len(failures) - 40} more")
        return 1
    print(f"all checks passed across {total} questions")
    return 0


if __name__ == "__main__":
    sys.exit(main())
