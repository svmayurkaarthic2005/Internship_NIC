"""Test the "temporary sub-division number assigned by the DIS" answers.

Three layers, none of which needs Ollama:

  1. CSV  -- what areg_temp_subdivclub_demo.csv actually carries, checked
            against the claims in backend/documents/.
  2. DB   -- layer 1 (urban_temp_subdivision_parcel) vs layer 2
            (application_sub_divisions): does the temp number survive the
            projection?
  3. Chat -- routing of temp-number questions, and whether the answer the
            officer gets can actually name the temporary number.

    python test_temp_subdivision_queries.py           # everything
    python test_temp_subdivision_queries.py --csv     # CSV + documents only
    python test_temp_subdivision_queries.py --routing # routing only, no database
"""
from __future__ import annotations

import asyncio
import csv
import re
import sys
from collections import defaultdict
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import logging


def _quiet_sql() -> None:
    """The app engine is built with echo=True in development, and the SQL log
    buries this suite's own findings."""
    from backend.database import engine
    engine.echo = False
    for name in ("sqlalchemy.engine", "sqlalchemy.engine.Engine"):
        logging.getLogger(name).setLevel(logging.WARNING)

CSV_PATH = _ROOT / "backend" / "sample_table" / "areg_temp_subdivclub_demo.csv"
OWNER_CSV_PATH = _ROOT / "backend" / "sample_table" / "chitta_temp_subdivclub_demo.csv"

TEMP_RE = re.compile(r"^(.+)/T(\d+)$")

# Questions an officer would actually type, and the intent they must reach.
# `None` means "anything but general_query is fine" -- what matters is that the
# question is not silently dropped into RAG.
ROUTING_CASES = [
    ("What is the temporary sub-division number for 2022/0154/28/000779?", None),
    ("what temporary number did DIS assign to 2023/0154/28/000376", None),
    ("show the temp subdivision numbers of 2025/0154/28/000466", None),
    ("list the proposed sub divisions for 2025/0154/28/000466", None),
    ("has DIS assigned a final subdivision number for 2023/0154/28/000376?", None),
    ("temporary and final subdivision numbers for my last ISD application", "last_application"),
    # Reference questions -- these belong in RAG, general_query is correct.
    ("Who assigns the temporary subdivision number?", "general_query"),
    ("What does 3/T1 mean?", "general_query"),
    ("What is the format of a temporary subdivision number?", "general_query"),
]

# (application, temporary number, final number or None) -- read off the CSV.
KNOWN = [
    ("2022/0154/28/000779", "3/T1", "3"),
    ("2022/0154/28/000779", "3/T2", "4"),
    ("2023/0154/28/000021", "0/T1", "1"),
    ("2023/0154/28/000376", "1/T1", None),   # rejected: no final number
    ("2023/0154/28/000395", "0/T3", None),   # rejected: no final number
    ("2025/0154/28/000466", "12/T2", "14"),
]


def read_csv():
    with CSV_PATH.open(encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


# ---------------------------------------------------------------- layer 1: CSV
def run_csv() -> int:
    failures = 0
    rows = read_csv()
    print("── CSV: backend/sample_table/areg_temp_subdivclub_demo.csv ──")
    print(f"  {len(rows)} parcel rows over "
          f"{len({r['application_id'] for r in rows})} applications")

    def check(label, ok, detail=""):
        nonlocal failures
        failures += 0 if ok else 1
        print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f" -- {detail}" if detail else ""))

    # The format the documents promise: {existing_subdivision}/T{sequence}.
    bad = [r["temporary_subdivision_number"] for r in rows
           if not TEMP_RE.match(r["temporary_subdivision_number"] or "")]
    check("every temporary number is {subdiv}/T{seq}", not bad, f"offenders: {bad[:5]}")

    # Only ISD (0154) / MERGE (0155) may carry one.
    codes = {r["application_id"].split("/")[1] for r in rows}
    check("only 0154 / 0155 applications carry temp numbers",
          codes <= {"0154", "0155"}, f"service codes seen: {sorted(codes)}")

    # A temp number is unique inside its application.
    dupes = [k for k, v in
             ((k, v) for k, v in _group(rows).items()) if len(v) != len({t for t, _ in v})]
    check("temporary numbers are unique within an application", not dupes, str(dupes))

    # Rejected/pending files keep the temp number and get no final number.
    blank_final = [(r["application_id"], r["temporary_subdivision_number"])
                   for r in rows if not (r["new_subdivision_number"] or "").strip()]
    print(f"  INFO  {len(blank_final)} parcels have no final number yet: "
          f"{sorted({a for a, _ in blank_final})}")

    # Doc claim (survey_manual.txt): T1 usually retains the existing subdivision.
    t1 = [(TEMP_RE.match(r["temporary_subdivision_number"]).group(1),
           r["new_subdivision_number"])
          for r in rows
          if TEMP_RE.match(r["temporary_subdivision_number"] or "")
          and TEMP_RE.match(r["temporary_subdivision_number"]).group(2) == "1"
          and (r["new_subdivision_number"] or "").strip()]
    kept = sum(1 for parent, final in t1 if parent == final or (parent == "0" and final == "1"))
    check("doc claim: T1 retains the existing sub-division number",
          t1 and kept == len(t1), f"{kept}/{len(t1)} kept it")

    # Doc claim (survey_manual.txt): the 2A/T1..2A/T8 series is filed as five
    # applications, 2025/0154/28/001633 to ...001637, on survey 35.
    two_a = {r["application_id"] for r in rows
             if (r["temporary_subdivision_number"] or "").startswith("2A/T")}
    check("doc claim: the 2A/T1..2A/T8 series spans applications 001633-001637",
          two_a == {f"2025/0154/28/00163{n}" for n in range(3, 8)},
          f"the series is on {sorted(two_a)}")

    # The owner extract must reference temp numbers that exist on the parcel side.
    with OWNER_CSV_PATH.open(encoding="utf-8-sig") as fh:
        owners = list(csv.DictReader(fh))
    parcel_keys = {(r["application_id"], r["temporary_subdivision_number"]) for r in rows}
    orphans = {(o["application_id"], o["temporary_subdivision_number"]) for o in owners
               if (o["application_id"], o["temporary_subdivision_number"]) not in parcel_keys}
    check("every owner row points at a parcel's temporary number",
          not orphans, f"{len(orphans)} orphans e.g. {sorted(orphans)[:3]}")
    return failures


def _group(rows):
    g = defaultdict(list)
    for r in rows:
        g[r["application_id"]].append(
            (r["temporary_subdivision_number"], r["new_subdivision_number"]))
    return g


# ------------------------------------------------------- layer 1 vs layer 2: DB
async def run_db() -> int:
    from sqlalchemy import select
    from backend.database import AsyncSessionLocal
    from backend.models import Application, ApplicationSubDivision
    from sqlalchemy import text

    _quiet_sql()
    failures = 0
    print("\n── DB: layer 1 (urban_temp_subdivision_parcel) → layer 2 ──")
    async with AsyncSessionLocal() as db:
        def check(label, ok, detail=""):
            nonlocal failures
            failures += 0 if ok else 1
            print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f" -- {detail}" if detail else ""))

        src = (await db.execute(text(
            "SELECT application_id, temporary_subdivision_number, new_subdivision_number "
            "FROM urban_temp_subdivision_parcel ORDER BY application_id, row_id"))).all()
        csv_rows = read_csv()
        check("layer 1 carries every CSV parcel row",
              len(src) == len(csv_rows), f"db={len(src)} csv={len(csv_rows)}")

        proj = (await db.execute(
            select(Application.application_number,
                   ApplicationSubDivision.proposed_sub_division_no)
            .join(ApplicationSubDivision,
                  ApplicationSubDivision.application_id == Application.id))).all()
        check("every layer-1 parcel is projected into application_sub_divisions",
              len(proj) == len(src), f"layer1={len(src)} layer2={len(proj)}")

        src_apps = {a for a, _, _ in src}
        proj_apps = {a for a, _ in proj}
        missing = sorted(src_apps - proj_apps)
        check("every application with parcels is projected",
              not missing, f"{len(missing)} dropped: {missing}")

        # The question this file exists for: can the DB still name the
        # temporary number the DIS assigned?
        temp_by_app = defaultdict(set)
        for app, tmp, _ in src:
            temp_by_app[app].add(tmp)
        proj_by_app = defaultdict(set)
        for app, no in proj:
            proj_by_app[app].add(no)

        cols = {r[0] for r in (await db.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='application_sub_divisions'"))).all()}
        check("application_sub_divisions has a column for the temporary number",
              "temporary_sub_division_no" in cols, f"columns: {sorted(cols)}")

        tmp_col = (await db.execute(text(
            "SELECT a.application_number, s.temporary_sub_division_no, "
            "       s.proposed_sub_division_no "
            "FROM applications a JOIN application_sub_divisions s "
            "  ON s.application_id = a.id"))).all()
        by_app = defaultdict(set)
        for app, tmp, _fin in tmp_col:
            by_app[app].add(tmp)
        wrong = sorted(a for a, tmps in by_app.items()
                       if tmps != temp_by_app.get(a, set()))
        check("every projected parcel keeps its layer-1 temporary number",
              not wrong, f"mismatched: {wrong[:5]}")
        not_temp = [t for _a, t, _f in tmp_col if not TEMP_RE.match(t or "")]
        check("temporary_sub_division_no holds only {subdiv}/T{seq} values",
              not not_temp, f"offenders: {not_temp[:5]}")

        # Spot-checks against numbers read off the CSV by hand.
        print("  ── known parcels ──")
        for app_no, tmp, final in KNOWN:
            row = [(t, n) for a, t, n in src if a == app_no and t == tmp]
            ok = bool(row) and (row[0][1] or None) == (final or None) or (
                bool(row) and not final and not (row[0][1] or "").strip())
            failures += 0 if ok else 1
            print(f"    {'PASS' if ok else 'FAIL'}  {app_no} {tmp} -> "
                  f"final {row[0][1]!r} (expected {final!r})" if row
                  else f"    FAIL  {app_no} {tmp} not in layer 1")
    return failures


# ------------------------------------------------------------------- the answer
async def run_answers() -> int:
    from sqlalchemy import select
    from backend.database import AsyncSessionLocal
    from backend.models import Application, SISOfficer
    from backend.schemas import OfficerContext
    from backend.services.auth_service import get_officer_jurisdiction_ids
    from backend.services.postgres import get_application_detail

    _quiet_sql()
    failures = 0
    print("\n── Answer: what get_application_detail() can tell the officer ──")
    async with AsyncSessionLocal() as db:
        # get_application_detail() filters by jurisdiction, so ask as the officer
        # who actually holds these files rather than whichever row comes first.
        officer_row = (await db.execute(
            select(SISOfficer)
            .join(Application, Application.assigned_officer_id == SISOfficer.id)
            .where(Application.application_number == KNOWN[0][0])
        )).scalars().first()
        if not officer_row:
            print("  No officer holds "
                  f"{KNOWN[0][0]} -- rebuild the projection first.")
            return 1
        jur = await get_officer_jurisdiction_ids(officer_row.id, db)
        ids = (jur["district_ids"] + jur["taluk_ids"] + jur["town_ids"]
               + jur["ward_ids"] + jur["block_ids"])
        officer = OfficerContext(
            officer_id=officer_row.id, employee_id=officer_row.employee_id,
            name=officer_row.name, email=officer_row.email,
            designation=officer_row.designation,
            jurisdiction_type=jur["jurisdiction_type"],
            jurisdiction_name=jur["jurisdiction_name"],
            jurisdiction_ids=[i for i in ids if i])
        print(f"  asking as {officer.name} ({jur['jurisdiction_name']})")

        for app_no in ("2022/0154/28/000779", "2023/0154/28/000376"):
            detail = await get_application_detail(db, app_no, officer)
            subs = detail.get("proposed_sub_divisions") or []
            temps = [s.get("temporary_sub_division_no") for s in subs]
            finals = [s.get("final_sub_division_no") for s in subs]
            expected_temps = [t for a, t, _ in KNOWN if a == app_no]
            expected_finals = [f for a, _, f in KNOWN if a == app_no]
            ok = (bool(subs)
                  and all(t in temps for t in expected_temps)
                  and all(f in finals for f in expected_finals if f))
            failures += 0 if ok else 1
            print(f"  {'PASS' if ok else 'FAIL'}  {app_no}: temporary={temps} final={finals}"
                  f" (expected temps {expected_temps}, finals {expected_finals})")
            print(f"        temporary_subdivision_number = {detail.get('temporary_subdivision_number')!r}")
            print(f"        final_subdivision_number     = {detail.get('final_subdivision_number')!r}")
            print(f"        subdivision_number           = {detail.get('subdivision_number')!r}")
    return failures


def run_routing() -> int:
    from backend.services.rag import parse_intent
    failures = 0
    print("\n── Routing ──")
    for question, expected in ROUTING_CASES:
        intent = parse_intent(question)
        ok = (intent == expected) if expected else (intent != "general_query")
        failures += 0 if ok else 1
        print(f"  {'PASS' if ok else 'FAIL'}  {question}\n        -> {intent}"
              + (f" (expected {expected})" if expected and intent != expected else ""))
    return failures


def main() -> int:
    args = set(sys.argv[1:])
    if "--routing" in args:
        return run_routing()
    if "--csv" in args:
        return run_csv()
    failures = run_csv()
    failures += run_routing()

    async def _db_layers():
        return await run_db() + await run_answers()

    failures += asyncio.run(_db_layers())
    print(f"\n{'ALL PASS' if not failures else str(failures) + ' FAILURE(S)'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
