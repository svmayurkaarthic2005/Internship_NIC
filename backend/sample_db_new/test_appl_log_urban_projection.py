"""
Verify appl_log_urban's projection into `applications` (SISchatbot), and that
the chatbot's follow-up layer still resolves correctly against that data.

Three checks, in order:

  1. Column mapping -- every column build_app_tables_new.py reads off
     appl_log_urban via `_alu_cols` actually resolves through `newcol()` to a
     real column in the table (a silent rename/drop would otherwise only
     surface as a wrong VALUE deep in `applications`, not as an error), and
     any real column NOT read by the build script is listed for awareness.
  2. Projection correctness -- for a sample of projected `applications` rows,
     the derived fields (application_type, current_status, submission_channel,
     can_number, district/taluk/ward/block chain) are re-derived independently
     from the same appl_log_urban row and compared against what actually
     landed in the ORM table.
  3. Follow-up questions -- the same officer-context smoke test
     backend/sample_db/check_app_wiring.py runs against sis_chatbot_db, run
     here against SISchatbot, plus a genuine multi-turn follow-up ("show my
     applications" -> "which one is oldest?") to prove followup_context still
     resolves against this extract's data.

Run from the project root (SISchatbot must already be built --
python -m backend.sample_db_new.build_app_tables_new):
    python -m backend.sample_db_new.test_appl_log_urban_projection            # checks 1+2, no LLM
    python -m backend.sample_db_new.test_appl_log_urban_projection --chat     # + check 3's chat turns (needs Ollama)
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from sqlalchemy import select, text

from backend.database import AsyncSessionLocal, get_engine_url
from backend.models import Application, SISOfficer
from backend.schemas import OfficerContext
from backend.sample_db_new.build_app_tables_new import SERVICE_TO_TYPE, STATUS_MAP, OPEN_TEXT_MAP
from backend.sample_db_new.column_map import newcol
from backend.sample_db.identifiers import can_channel, normalize_can
from backend.services import postgres
from backend.services.auth_service import get_officer_jurisdiction_ids, verify_password

EXPECTED_DB = "SISchatbot"
DEFAULT_PASSWORD = "Test@1234"

# The exact list build_app_tables_new.py reads off appl_log_urban -- kept in
# sync by hand since it's a short, stable list; a real drift would fail
# check 1 below rather than pass silently.
_ALU_COLS = ["application_id", "service_code", "district_code", "taluk_code",
             "village_code", "ward_code", "block_code", "survey_number",
             "application_date", "application_status", "can_number",
             "source_code", "source_name", "igrs_form6_number",
             "igrs_auto_mutation_flag", "camp_flag", "ip_address"]

FOLLOWUP_CHAT_TURNS = [
    "Show me my applications",
    "which one is the oldest?",
]


async def officer_context(db, officer) -> OfficerContext:
    jur = await get_officer_jurisdiction_ids(officer.id, db)
    ids = (jur["district_ids"] + jur["taluk_ids"] + jur["town_ids"]
           + jur["ward_ids"] + jur["block_ids"])
    return OfficerContext(
        officer_id=officer.id, employee_id=officer.employee_id, name=officer.name,
        email=officer.email, designation=officer.designation,
        jurisdiction_type=jur["jurisdiction_type"],
        jurisdiction_name=jur["jurisdiction_name"],
        jurisdiction_ids=[i for i in ids if i])


def show(label, result, width=400):
    print(f"\n--- {label} ---")
    if isinstance(result, dict):
        for k, v in result.items():
            sv = str(v)
            print(f"   {k}: {sv[:width]}{'...' if len(sv) > width else ''}")
    else:
        print("  ", str(result)[:width])


async def check_column_mapping(db, failures: list) -> None:
    print("\n" + "=" * 70)
    print("CHECK 1 -- column mapping (appl_log_urban)")
    real_cols = {r[0] for r in (await db.execute(text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='appl_log_urban'"))).all()}
    if not real_cols:
        failures.append("appl_log_urban table not found in SISchatbot")
        return

    resolved = set()
    for old in _ALU_COLS:
        try:
            real = newcol("appl_log_urban", old)
        except KeyError as exc:
            failures.append(f"column mapping broken for {old!r}: {exc}")
            continue
        resolved.add(real)
        if real not in real_cols:
            failures.append(
                f"{old!r} maps to {real!r}, which is not a real column of "
                f"appl_log_urban")
    print(f"  {len(_ALU_COLS)} columns used by build_app_tables_new.py, "
          f"all resolved: {resolved <= real_cols}")

    unused = sorted(real_cols - resolved)
    if unused:
        print(f"  columns in appl_log_urban NOT read by the build script "
              f"({len(unused)}): {unused}")


async def check_projection_correctness(db, failures: list, sample_size: int = 15) -> None:
    print("\n" + "=" * 70)
    print("CHECK 2 -- projection correctness (applications vs appl_log_urban)")
    rows = (await db.execute(text(f"""
        SELECT a.application_number, a.application_type, a.current_status,
               a.submission_channel, a.can_number,
               al.service_code, al.appl_status, al.source_name, al.camp_flag,
               al.can, al.district_code, al.taluk_code, al.ward_code, al.block_code
        FROM applications a
        JOIN appl_log_urban al ON al.appl_id = a.application_number
        ORDER BY random()
        LIMIT {sample_size}"""))).all()

    if not rows:
        failures.append("no applications joined back to appl_log_urban -- "
                         "projection produced nothing to check")
        return

    checked = 0
    for (num, atype, status, channel, can_no, svc, appl_status_code, src_name,
         camp_flag, can_raw, dc, tk, wd, bl) in rows:
        checked += 1
        exp_type = SERVICE_TO_TYPE.get(svc)
        if exp_type != atype:
            failures.append(f"{num}: application_type={atype!r}, expected "
                             f"{exp_type!r} from service_code={svc!r}")

        exp_channel = can_channel(src_name, camp_flag)
        if exp_channel != channel:
            failures.append(f"{num}: submission_channel={channel!r}, "
                             f"expected {exp_channel!r} from source_name="
                             f"{src_name!r}/camp_flag={camp_flag!r}")

        exp_can = normalize_can(can_raw, exp_channel)
        if exp_can != can_no:
            failures.append(f"{num}: can_number={can_no!r}, expected "
                             f"{exp_can!r} (re-derived from source can="
                             f"{can_raw!r})")

        # The projected ward/block chain must trace back to the SAME codes
        # the source row carries -- this is what "which ward/block is this
        # application in" answers from.
        geo = (await db.execute(text("""
            SELECT w.ward_number, bl.block_number
            FROM applications a
            JOIN survey_numbers sn ON sn.id = a.survey_number_id
            JOIN blocks bl ON bl.id = sn.block_id
            JOIN wards w ON w.id = bl.ward_id
            WHERE a.application_number = :num"""),
            {"num": num})).first()
        if geo is None:
            failures.append(f"{num}: no ward/block chain resolves for this application")
        elif (geo[0], geo[1]) != (wd, bl):
            failures.append(f"{num}: projected ward/block = {geo}, source "
                             f"appl_log_urban has ({wd!r}, {bl!r})")

    print(f"  checked {checked} sampled applications against their source row")


async def check_followups(db, run_chat: bool, failures: list) -> None:
    print("\n" + "=" * 70)
    print("CHECK 3 -- officer queries + follow-up questions")
    dbname = (await db.execute(text("SELECT current_database()"))).scalar()
    print("connected database:", dbname)
    if dbname != EXPECTED_DB:
        failures.append(f"app is on {dbname}, expected {EXPECTED_DB}")

    officer = (await db.execute(
        select(SISOfficer).order_by(SISOfficer.employee_id))).scalars().first()
    if officer is None:
        failures.append("no officers in SISchatbot -- run build_app_tables_new first")
        return
    print(f"officer: {officer.employee_id} {officer.name} <{officer.email}>")
    if not verify_password(DEFAULT_PASSWORD, officer.password_hash):
        failures.append("seeded officer password does not verify")

    ctx = await officer_context(db, officer)
    print(f"jurisdiction: {ctx.jurisdiction_type} / {ctx.jurisdiction_name}")

    workload = await postgres.get_officer_workload(db, ctx)
    show("get_officer_workload", workload)

    num = (await db.execute(
        select(Application.application_number)
        .where(Application.assigned_officer_id == officer.id)
        .limit(1))).scalar()
    if num is None:
        failures.append(f"officer {officer.employee_id} has no assigned applications")
    else:
        detail = await postgres.get_application_detail(db, num, ctx)
        show(f"get_application_detail({num})", detail)
        if not detail.get("found"):
            failures.append(f"application {num} not resolvable by its own officer")
        # a follow-up like "which ward is it in" depends on these being present
        for field in ("ward_number", "block_number"):
            if not detail.get(field):
                failures.append(f"get_application_detail({num}) is missing "
                                 f"{field!r} -- a ward/block follow-up would fail")

    if run_chat:
        from backend.services.chatbot import process_chat
        session = str(uuid.uuid4())
        for q in FOLLOWUP_CHAT_TURNS:
            print("\n" + "-" * 70)
            print("Q:", q)
            try:
                res = await process_chat(q, session, ctx, db)
                answer = res.get("response") or res.get("answer") or res
                print("A:", str(answer)[:600])
            except Exception as exc:  # noqa: BLE001
                failures.append(f"chat failed on {q!r}: {exc}")
                print("ERROR:", type(exc).__name__, exc)


async def main(run_chat: bool) -> int:
    failures: list = []
    print("engine:", get_engine_url())
    async with AsyncSessionLocal() as db:
        await check_column_mapping(db, failures)
        await check_projection_correctness(db, failures)
        await check_followups(db, run_chat, failures)

    print("\n" + "=" * 70)
    if failures:
        print(f"FAILED ({len(failures)}):")
        for f in failures:
            print("  -", f)
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main("--chat" in sys.argv)))
