"""Completed vs not-completed field visits: does the answer match the register?

The question "show uncompleted field visits" is the sharp one. `chatbot.py`
selected the completed filter with an UNBOUNDED substring test --

    if any(w in msg_lower for w in ("completed", "complete", ...)):

-- and "unc|ompleted" contains "completed", "in|complete" contains "complete".
So a request for the visits still outstanding was answered with the visits
already finished: not an empty result or a vague one, but confidently the exact
opposite set. It is the same trap CLAUDE.md records for "how" inside "sHOW".

Every expectation here is computed from the database, never from another answer,
so the suite still means something after a reseed.

Run:
    python test_field_visit_status.py           # routing + answers (no LLM)
    python test_field_visit_status.py --routing # cue classification only, no DB
"""
from __future__ import annotations

import asyncio
import re
import sys
import uuid
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import select  # noqa: E402

from backend.database import AsyncSessionLocal  # noqa: E402
from backend.models import Application, FieldVisit, SISOfficer  # noqa: E402
from backend.schemas import OfficerContext  # noqa: E402
from backend.services import chatbot  # noqa: E402
from backend.services.auth_service import get_officer_jurisdiction_ids  # noqa: E402

FAILURES: list[str] = []

# The statuses a visit that still has to happen can carry, straight from
# `get_field_visits(to_be_visited_only=True)`.
OPEN_STATUSES = ("scheduled", "rescheduled", "pending", "overdue", "unscheduled")

# (question, which set it must return)
#   "completed" -> only finished visits
#   "open"      -> only visits still to be made
#   "all"       -> the officer's whole visit record
CASES = [
    ("show completed field visits", "completed"),
    ("show me the completed field visits", "completed"),
    ("how many field visits are completed", "completed"),
    ("which field visits are finished", "completed"),
    ("completed field visits", "completed"),

    # The reported case, and its family. Every one of these contains the
    # substring "complete", and every one means the opposite of it.
    ("show uncompleted field visits", "open"),
    ("show incomplete field visits", "open"),
    ("show un-completed field visits", "open"),
    ("which field visits are not completed", "open"),
    ("field visits not yet completed", "open"),
    ("show unfinished field visits", "open"),
    ("which field visits are still pending", "open"),
    ("show outstanding field visits", "open"),
    ("which field visits remain", "open"),

    # "unvisited" is the word an officer actually reaches for, and it was in
    # none of the cues -- the question fell through to the unfiltered summary
    # and returned every visit, finished or not. Note the app's own summary
    # line says "1 still to visit", so that phrasing has to work too: the
    # assistant must understand the words it chose itself.
    ("show unvisited field visits", "open"),
    ("show un-visited field visits", "open"),
    ("which field visits are not visited", "open"),
    ("field visits not yet visited", "open"),
    ("which field visits are still to visit", "open"),
    ("show field visits still to be visited", "open"),
    ("which are yet to visit", "open"),
    ("show never visited field visits", "open"),
    ("show pending visits", "open"),

    ("show my field visits", "all"),
    ("how many field visits do i have", "all"),
]

APP_NO_RE = re.compile(r"\b\d{4}/\d{3,4}/\d{1,3}/\d+\b")


def check(ok: bool, label: str, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}")
    if detail and not ok:
        print(f"        {detail}")
    if not ok:
        FAILURES.append(label)


def section(title: str) -> None:
    print(f"\n{title}\n" + "-" * len(title))


async def officer_context(db, officer) -> OfficerContext:
    j = await get_officer_jurisdiction_ids(officer.id, db)
    ids = (j["district_ids"] + j["taluk_ids"] + j["town_ids"]
           + j["ward_ids"] + j["block_ids"])
    return OfficerContext(
        officer_id=officer.id, employee_id=officer.employee_id,
        name=officer.name, email=officer.email, designation=officer.designation,
        jurisdiction_type=j["jurisdiction_type"],
        jurisdiction_name=j["jurisdiction_name"],
        jurisdiction_ids=[i for i in ids if i])


async def expected_sets(db, officer) -> dict:
    """The three sets, straight from the register.

    Rejected applications are excluded, the standing rule every listing follows
    and the one `get_field_visits` applies.
    """
    rows = (await db.execute(
        select(Application.application_number, FieldVisit.status)
        .join(Application, FieldVisit.application_id == Application.id)
        .where(FieldVisit.officer_id == officer.id,
               Application.current_status != "rejected")
    )).all()
    done = {n for n, s in rows if s == "completed"}
    open_ = {n for n, s in rows if s in OPEN_STATUSES}
    return {"completed": done, "open": open_, "all": done | open_}


async def run(check_answers: bool) -> int:
    section("1. The cue that picks the completed filter")
    # Checked without the database: "uncompleted" must not read as "completed".
    from backend.services.chatbot import _asked_completed_visits, _asked_open_visits
    for msg in ("show completed field visits", "how many are completed",
                "which field visits are finished"):
        check(_asked_completed_visits(msg) and not _asked_open_visits(msg),
              f"{msg!r} asks for COMPLETED visits")
    for msg in ("show uncompleted field visits", "show incomplete field visits",
                "which field visits are not completed", "show unfinished field visits",
                "field visits not yet completed", "show un-completed field visits",
                # the "unvisited" family
                "show unvisited field visits", "show un-visited field visits",
                "which field visits are not visited", "field visits not yet visited",
                "which field visits are still to visit",
                "show field visits still to be visited",
                "which are yet to visit", "show never visited field visits",
                "show pending visits"):
        check(_asked_open_visits(msg) and not _asked_completed_visits(msg),
              f"{msg!r} asks for the visits still OPEN")

    if not check_answers:
        print("\n(--routing: the database was not consulted)")
        return report()

    async with AsyncSessionLocal() as db:
        officers = (await db.execute(select(SISOfficer))).scalars().all()
        for officer in officers:
            ctx = await officer_context(db, officer)
            want = await expected_sets(db, officer)
            section(f"2. {officer.email} — "
                    f"{len(want['all'])} visits, {len(want['completed'])} completed, "
                    f"{len(want['open'])} still to make")
            if not want["all"]:
                print("        (no field visits on record; nothing to compare)")
                continue
            for question, which in CASES:
                res = await chatbot.process_chat(
                    question, str(uuid.uuid4()), ctx, db, [])
                html = res.get("response", "") or ""
                got = set(APP_NO_RE.findall(html))
                expect = want[which]
                # A count-shaped question ("how many ...") answers in words and
                # lists nothing, so compare the number it states instead.
                if not got and re.search(r"\bhow many\b", question, re.I):
                    m = re.search(r"\b(\d+)\b", re.sub(r"<[^>]+>", " ", html))
                    stated = int(m.group(1)) if m else -1
                    check(stated == len(expect),
                          f"{question!r} counts {len(expect)}",
                          f"answered {stated}: "
                          f"{re.sub(r'<[^>]+>', ' ', html)[:120]}")
                    continue
                check(got == expect,
                      f"{question!r} returns the {which} set ({len(expect)})",
                      f"missing={sorted(expect - got)[:4]} "
                      f"unexpected={sorted(got - expect)[:4]}")

    return report()


def report() -> int:
    print("\n" + "=" * 68)
    if FAILURES:
        print(f"{len(FAILURES)} FAILED:")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("ALL PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run(check_answers="--routing" not in sys.argv)))
