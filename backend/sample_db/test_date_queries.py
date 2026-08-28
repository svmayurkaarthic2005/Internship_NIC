"""
Check that date-scoped questions come back with the right applications.

A date question has two halves, and each can be wrong on its own:

  parsing  -- "between June 1st and June 30th", "last month", "since
              2026-01-01", "from January to March 2025" have to resolve to the
              range the officer meant. Compared against the range spelled out
              in each case below.

  answering -- the handler then has to return what the register holds for that
              period. A question that names a period is not a question about
              the officer's current desk, so it must not be pinned to their
              stage: every past month would answer 0, because an application
              from then has long since moved on.

The expected count is computed from the database independently of the handler,
so a filter that quietly widens or drops the ward shows up here.

Run from the project root:
    python -m backend.sample_db.test_date_queries
"""
from __future__ import annotations

import asyncio
import calendar
import re
import sys
from datetime import date, timedelta
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
from backend.models import SISOfficer
from backend.services import postgres
from backend.services.chatbot import extract_month_from_query
from backend.services.rag import extract_date_range

TODAY = date.today()


def _month(year: int, month: int) -> tuple[date, date]:
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def _prev_month() -> tuple[date, date]:
    first = TODAY.replace(day=1) - timedelta(days=1)
    return _month(first.year, first.month)


# (question, expected start, expected end, expected month, expected year)
# A None range with a month/year set means the month/year filter carries the
# query instead -- that is the path "applications in June 2026" takes.
CASES = [
    ("Show applications between 2026-01-01 and 2026-06-30",
     date(2026, 1, 1), date(2026, 6, 30), None, None),
    ("List applications submitted between June 1st and June 30th",
     date(TODAY.year, 6, 1), date(TODAY.year, 6, 30), None, None),
    ("Applications received between 2025-01-01 and 2025-12-31",
     date(2025, 1, 1), date(2025, 12, 31), None, None),
    ("applications from 01/06/2026 to 30/06/2026",
     date(2026, 6, 1), date(2026, 6, 30), None, None),
    ("applications between June 2026 and August 2026",
     date(2026, 6, 1), date(2026, 8, 31), None, None),
    ("applications from January to March 2025",
     date(2025, 1, 1), date(2025, 3, 31), None, None),
    ("list applications from 25th July to 31st July 2025",
     date(2025, 7, 25), date(2025, 7, 31), None, None),
    ("Show applications since 2026-01-01", date(2026, 1, 1), TODAY, None, None),
    ("Applications before 2023-01-01", None, date(2023, 1, 1), None, None),
    ("Show applications from this month", *_month(TODAY.year, TODAY.month), None, None),
    ("List last month's applications", *_prev_month(), None, None),
    ("Show applications from last 7 days", TODAY - timedelta(days=7), TODAY, None, None),
    ("Show applications submitted today", TODAY, TODAY, None, None),
    ("Show yesterday's applications",
     TODAY - timedelta(days=1), TODAY - timedelta(days=1), None, None),
    ("Show applications from June 2026", None, None, 6, 2026),
    ("Show me applications from jaunary 2026", None, None, 1, 2026),   # typo tolerated
    ("How many applications in 2024?", None, None, None, 2024),
    ("List applications in year 2025", None, None, None, 2025),
]

# What the register holds for the officer's ward over the period. Rejected
# applications stay out of lists unless they are asked for, matching the
# handler; the stage is deliberately NOT constrained -- that is the point.
COUNT_SQL = """
    SELECT count(*) FROM applications a
    JOIN survey_numbers s ON s.id = a.survey_number_id
    JOIN blocks b ON b.id = s.block_id
    JOIN officer_jurisdictions j ON j.officer_id = :officer
    WHERE b.ward_id = j.ward_id
      AND a.current_status <> 'rejected'
      {clause}
"""


def _sql_for(start, end, month, year) -> tuple[str, dict]:
    if start and end:
        return "AND a.submission_date BETWEEN :start AND :end", {"start": start, "end": end}
    if start:
        return "AND a.submission_date >= :start", {"start": start}
    if end:
        return "AND a.submission_date <= :end", {"end": end}
    clause, params = "", {}
    if year:
        clause += " AND extract(year FROM a.submission_date) = :year"
        params["year"] = year
    if month:
        clause += " AND extract(month FROM a.submission_date) = :month"
        params["month"] = month
    return clause, params


async def build_context(db, officer):
    from backend.sample_db.test_questions import build_context as ctx
    return await ctx(db, officer)


async def main() -> int:
    failures: list[str] = []
    async with AsyncSessionLocal() as db:
        dbname = (await db.execute(text("SELECT current_database()"))).scalar()
        officer = (await db.execute(
            select(SISOfficer).order_by(SISOfficer.employee_id))).scalars().first()
        ctx = await build_context(db, officer)
        span = (await db.execute(text(
            "SELECT min(submission_date), max(submission_date) FROM applications"))).one()
        print(f"database: {dbname}   today: {TODAY}")
        print(f"officer:  {officer.email} ({ctx.jurisdiction_name})")
        print(f"register: {span[0]} .. {span[1]}\n")

        print("[1/2] date phrases resolve to the right range")
        for question, want_start, want_end, want_month, want_year in CASES:
            got_start, got_end = extract_date_range(question)
            got_month = extract_month_from_query(question)
            got_year = None
            if got_start is None and got_end is None:
                m = re.search(r"\b(20\d{2})\b", question)
                got_year = int(m.group(1)) if m else None
            # The chatbot only reads a month or a year out of the message when
            # no range was found (chatbot.py: "only used when NO full date
            # range is present"), so a stray month alongside a range is dead
            # and is not asserted on.
            ok = (got_start, got_end) == (want_start, want_end)
            if not (got_start or got_end):
                ok = ok and got_month == want_month and got_year == want_year
            shown = (f"{got_start}..{got_end}" if (got_start or got_end)
                     else f"month={got_month} year={got_year}")
            print(f"  {'ok  ' if ok else 'FAIL'} {question[:52]:54s} {shown}")
            if not ok:
                print(f"        wanted {want_start}..{want_end} "
                      f"month={want_month} year={want_year}")
                failures.append(f"parse: {question}")

        print("\n[2/2] the answer matches the register for that period")
        for question, start, end, month, year in CASES:
            result = await postgres.get_officer_applications(
                db, ctx, start_date=start, end_date=end,
                submission_month=month if not (start or end) else None,
                submission_year=year if not (start or end) else None)
            got = result.get("count", 0)
            clause, params = _sql_for(start, end, month, year)
            want = (await db.execute(text(COUNT_SQL.format(clause=clause)),
                                     {"officer": officer.id, **params})).scalar()
            ok = got == want
            print(f"  {'ok  ' if ok else 'FAIL'} {question[:52]:54s} {got:3d} vs {want:3d}")
            if not ok:
                failures.append(f"answer: {question}: {got} vs {want}")

        print("\n[3/2] negated ranges exclude exactly what the plain range includes")
        for question, start, end in [
            ("Show applications not between 2026-01-01 and 2026-06-30",
             date(2026, 1, 1), date(2026, 6, 30)),
            ("List applications outside 2025-01-01 to 2025-12-31",
             date(2025, 1, 1), date(2025, 12, 31)),
        ]:
            inside = (await postgres.get_officer_applications(
                db, ctx, start_date=start, end_date=end)).get("count", 0)
            outside = (await postgres.get_officer_applications(
                db, ctx, start_date=start, end_date=end,
                exclude_date_range=True)).get("count", 0)
            total = (await db.execute(text(COUNT_SQL.format(clause="")),
                                      {"officer": officer.id})).scalar()
            ok = inside + outside == total
            print(f"  {'ok  ' if ok else 'FAIL'} {question[:52]:54s} "
                  f"{inside} in + {outside} out = {total}")
            if not ok:
                failures.append(f"negation: {question}: {inside}+{outside} != {total}")

    print()
    if failures:
        print(f"FAILED ({len(failures)}):")
        for f in failures:
            print("  -", f)
        return 1
    print(f"all {len(CASES)} date questions parse and answer correctly, "
          f"negation included")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
