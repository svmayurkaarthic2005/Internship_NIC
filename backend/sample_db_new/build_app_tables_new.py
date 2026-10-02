"""
Build the application's ORM tables inside SISchatbot from the newer batch of
extracts in backend/sample_db_new/.

This is a sibling of backend/sample_db/build_app_tables.py, not a replacement
for it: that script projects backend/sample_table/'s `*_demo` tables into
sis_chatbot_db; this one projects backend/sample_db_new/'s tables (loaded by
load_sample_db_new.py under their own CSV-derived names, no `_demo` suffix,
no column renamed) into SISchatbot. The two extracts differ in two ways --
see backend/sample_db_new/column_map.py for the full comparison:

  * table names never carry `_demo` here;
  * 8 of the 16 shared tables were re-exported with shorter column names
    (application_date -> appl_dt, ...). Every query below that touches one of
    those tables goes through `newcol()` (a bare name substitution, for a
    query read positionally) or `sel()` (a `real AS old` alias, for a query
    read by name via `.mappings()`), rather than hand-translating each one --
    the same mapping `column_map.py` already validated against every real
    header is the only place that knows both names ever meant the same thing.

The chatbot answers through backend/services/postgres.py and backend/services/
chatbot.py, which query the ORM models in backend/models.py (applications,
survey_numbers, owners, field_visits, ...) -- identical regardless of which
database backs them, so this script builds the exact same ORM shape
build_app_tables.py does.

Run from the project root (after backend/sample_db_new/load_sample_db_new.py,
backend.sample_db.load_master_dumps and backend.sample_db.adopt_master_district_taluk
have loaded the geography masters -- .env's SYNC_DATABASE_URL / DATABASE_URL
must point at SISchatbot first, since every one of those reads it from there):
    python -m backend.sample_db_new.build_app_tables_new
"""
from __future__ import annotations

import itertools
import sys
import uuid
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# Names in the extracts are Tamil, and a Windows console defaults to cp1252,
# which cannot encode them. Same guard as backend/main.py.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Allow running as a script from anywhere in the repo.
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from sqlalchemy import create_engine, text

from backend.config import DISTRICT_CODE_MAP
from backend.database import Base
from backend.models import (  # noqa: F401 -- imported so create_all sees them
    District, Taluk, Town, Ward, Block, SurveyNumber, SubDivision,
    Owner, SurveyOwnership, SISOfficer, OfficerJurisdiction, Applicant,
    Application, ApplicationSubDivision, ApplicationSubDivisionOwner,
    ApplicationDocument, WorkflowHistory,
    FieldVisit, PattaTransfer, Notification, AuditLog, ChatSession,
    ChatMessage, KnowledgeEmbedding,
)
from backend.models import app_owned_tables, missing_master_tables
from backend.sample_db.identifiers import (CAN_LENGTHS, aadhaar_for, can_channel,
                                           normalize_can)
from backend.sample_db.dbconn import database_url
from backend.sample_db_new.column_map import newcol, sel
from backend.services.auth_service import get_password_hash

DB_URL = database_url().replace("+asyncpg", "+psycopg2")

DEFAULT_PASSWORD = "Test@1234"

# service_code -> application_type. The Application model only admits these
# three (ck_application_type), so other services stay in the CSV-shaped tables
# and are not projected.
SERVICE_TO_TYPE = {"0153": "NISD", "0154": "ISD"}

# Only the sub-division chain includes a mandatory field inspection. NISD is
# document verification at the SIS desk and then straight to the Zonal Level
# Tahsildar -- no visit is scheduled, so no visit date may be claimed for one.
FIELD_VISIT_TYPES = {"ISD"}

# What appl_log_urban_demo.application_status means. The transfer extracts
# spell the status out in words, so cross-tabulating the two settles it --
#   01/C -> "Approved By ZDT/HQDT" (128), "Order Generated" (20)
#   02/C -> "Rejected By ZDT/HQDT" (33), "Rejected" (13)
#   03/P -> "Send to SIS" (4), "Forward To ZDT" (2)
#   05/C -> "Rejected By ZDT/HQDT" (2), "Rejected" (2)
# -- and workflow_state agrees: C is a closed chain, P an open one. Checked a
# third way, against how each application's workflow chain actually ends, the
# code agrees on 176 of 180 closed applications and the wording on 173, so the
# code is what is used. The earlier map read 02 as in-progress and 03 as
# rejected, which inverted almost every status the chatbot reported and left
# the database with no pending applications at all.
STATUS_MAP = {"01": "approved", "02": "rejected", "03": "pending",
              "05": "rejected"}

# Within 03 -- the only open code -- the wording says which desk holds the file,
# and that is the pending / in-progress split: 'pending' is the SIS officer's
# own queue (workflow_guide.txt step 2), anything further along is in progress.
OPEN_TEXT_MAP = {"send to sis": "pending", "forward to zdt": "in_progress"}

# land_type_code (full_field_patta_transfer_urban) -> the model's free-text land_type.
LAND_TYPE_MAP = {"2": "residential", "3": "commercial", "5": "agricultural"}

# workflow role id -> stage name used by the model / chatbot.
#
# Read off the extracts rather than guessed. Role 1 is the CSC
# operator who submits (its actors are the source_name codes, not officers), so
# it is not a desk and the opening hop legitimately has no from_stage. The
# applications whose wording says "Send to SIS" are sitting at role 44 or role
# 41, and the two roles share their actors with role 42, so all three are the
# surveyor's office. "Forward To ZDT" / "Approved By ZDT/HQDT" is role 16, and
# role 8 has its own distinct set of actors -- the Senior Draughtsman (SD).
# Leaving 42 and 16 out of this map was what put 459 rows in workflow_history
# with no from_stage and marked 183 mid-chain hops as COMPLETED.
ROLE_TO_STAGE = {"44": "SIS", "42": "SIS", "41": "SIS", "8": "SD",
                 "16": "TAHSILDAR", "12": "DIS",
                 "59": "TAHSILDAR", "53": "TAHSILDAR"}

# transfer_reason in the extracts -> declared_reason in the model
# ("sale, inheritance, partition, gift_deed" per models.py).
TRANSFER_REASON_TO_DECLARED = {
    "sale deed": "sale",
    "விற்பனை ஆவணம்/ கிரைய ஆவணம்": "sale",
    "gift deed": "gift_deed",
    "தான ஆவணம்": "gift_deed",
    "settlement deed": "settlement",
    "ஏற்பாடு/ செட்டில்மெண்டு ஆவணம்": "settlement",
    "release deed": "release",
    "விடுதலை ஆவணம்": "release",
    "partition deed": "partition",
    "பாகப்பிரிவினை ஆவணம்": "partition",
    "legal heir": "inheritance",
    "வாரிசு உரிமை": "inheritance",
    "court order /judgement": "court_order",
    "நீதிமன்ற ஆணை": "court_order",
}

# Documents required for each type, from documents/workflow_guide.txt.
REQUIRED_DOCS = {
    "ISD": ["Sale Deed", "Encumbrance Certificate", "Survey Sketch",
            "Photo ID", "Photographs"],
    "NISD": ["Sale Deed", "Encumbrance Certificate", "Photo ID", "Patta Copy"],
}

# Every ORM table this script owns is upserted in place, at a deterministic
# id derived from the source extract's own natural key -- never TRUNCATEd and
# never given a random uuid4(). That is what makes a rerun idempotent against
# a production extract that grows over time (new applications filed, a status
# advancing, a field visit getting completed): a row whose natural key hasn't
# changed keeps its id and is merely updated in place; a genuinely new key
# inserts a new row; nothing already there is ever dropped and recreated.
#
# This is the same trick `_OFFICER_NS` already used for `sis_officers` (so a
# JWT issued before a rebuild still resolves after one, and a
# `TRUNCATE ... CASCADE` doesn't take `chat_sessions` / `chat_messages` /
# `audit_logs` / `chat_attachments` / `notifications` down with it), just
# applied to every table instead of one. `notifications` and
# `knowledge_embeddings` are still never touched by this script -- the former
# because it's written by the live app, not derived from the extracts; the
# latter because it holds expensive document embeddings unrelated to any of
# this.
_NS = uuid.UUID("6c1f9b2a-0000-4000-8000-000000000000")
_OFFICER_NS = uuid.UUID("6c1f9b2a-0000-4000-8000-000000000001")


def stable_id(*parts) -> uuid.UUID:
    """Deterministic id for one row, from its table name + natural key.

    The table name is always the first part, so two different tables whose
    natural keys happen to collide as strings (e.g. an application_id that
    equals some other table's key) never produce the same id.
    """
    key = "|".join("" if p is None else str(p) for p in parts)
    return uuid.uuid5(_NS, key)


def _utcnow():
    return datetime.now(timezone.utc)


# The TAMILNILAM master tables loaded by load_master_dumps.py. They are the
# department's own record of what each district / taluk / town / ward / block
# is called, so the projection takes its names from them instead of inventing
# them. English columns are used because these fields feed English labels in
# the UI; the Tamil ones (`*_name` / `district_tname`) are the same rows.
#
# Every value is CHAR-padded in the dumps ("Thoothukudi              "), so it
# is stripped -- trailing spaces are storage, not part of the name.
# District and taluk are absent: those two ARE the master tables now, so their
# names need no copying -- the projection reads them in place.
_MASTER_NAME_SOURCES = (
    ("town", "town", "town_ename",
     ("district_code", "taluk_code", "town_code")),
    ("ward", "ward", "ward_ename",
     ("district_code", "taluk_code", "town_code", "ward_code")),
    ("block", "block", "block_ename",
     ("district_code", "taluk_code", "town_code", "ward_code", "block_code")),
)


def load_master_names(cx) -> dict:
    """{level: {code tuple: official name}} from the master tables.

    A level whose master table is absent comes back empty, and the caller then
    falls back to the name it used to synthesise -- so this script still runs
    on a database where load_master_dumps.py has not been applied.
    """
    names: dict = {}
    for level, table, name_col, key_cols in _MASTER_NAME_SOURCES:
        if cx.execute(text("SELECT to_regclass(:t)"),
                      {"t": f"public.{table}"}).scalar() is None:
            names[level] = {}
            continue
        keys = ", ".join(key_cols)
        rows = cx.execute(text(
            f"SELECT {keys}, {name_col} FROM public.{table}")).all()
        names[level] = {
            tuple((c or "").strip() for c in row[:-1]): (row[-1] or "").strip()
            for row in rows if (row[-1] or "").strip()
        }
    return names


def humanise(username: str) -> str:
    """tut_kvenkatesan -> 'K. Venkatesan' (best effort, for display only)."""
    stem = username.replace("tut_", "")
    if len(stem) > 4 and stem[0] in "kabgjnprs" and stem[1] not in "aeiou":
        return f"{stem[0].upper()}. {stem[1:].capitalize()}"
    return stem.capitalize()


def _coerce_dt(v):
    """Coerce a DB value to a timezone-aware datetime, or return None."""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    if isinstance(v, date):
        return datetime.combine(v, datetime.min.time(), tzinfo=timezone.utc)
    try:
        s = str(v)[:19]
        dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
        return dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def performed_at(action) -> datetime:
    """When a workflow hop happened, to the second.

    A file often clears three desks in one day, so action_date alone makes the
    hops simultaneous and the history loses its order -- which then reads as
    the file bouncing backwards. last_updated_datetime carries the real time;
    where it is missing the serial number keeps the day's hops in sequence.
    """
    stamp, day, serial = _coerce_dt(action[7]), action[3], action[8]
    if stamp is not None:
        return stamp
    day_dt = _coerce_dt(day) or datetime.now(timezone.utc)
    return day_dt + timedelta(seconds=int(serial or 0))


def _display_code(code: str) -> str:
    """Strip leading zeros from a purely numeric code for a fallback display
    name ("Ward 001" -> "Ward 1"); a code that isn't purely numeric (a block
    code can be alphanumeric, e.g. "016A") is shown verbatim rather than
    crashing int() on it.
    """
    return str(int(code)) if code.isdigit() else code


def _to_date(v):
    if v is None:
        return date.today()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except (ValueError, TypeError):
        return date.today()


def working_days_between(start: date, end: date) -> int:
    days = 0
    cur = start
    while cur < end:
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            days += 1
    return days


def main():
    engine = create_engine(DB_URL, future=True)
    print(f"connected to {engine.url.database}")

    # knowledge_embeddings needs pgvector, same as the original database
    with engine.begin() as cx:
        cx.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    print("pgvector extension ready")

    # The masters are excluded: create_all() would build them from the few
    # columns models.py maps and leave a stub holding none of the department's
    # data. If they are absent, say so instead of quietly carrying on.
    with engine.connect() as cx:
        _absent = missing_master_tables(cx)
    if _absent:
        raise SystemExit(
            "master table(s) missing: " + ", ".join(_absent) + "\n"
            "Run first:\n"
            "  python -m backend.sample_db.load_master_dumps\n"
            "  python -m backend.sample_db.adopt_master_district_taluk")
    Base.metadata.create_all(engine, tables=app_owned_tables())
    # An earlier schema enforced one active application per survey number.
    # The extracts disprove that (a parcel can be under two live requests at
    # once), so models.py now declares a plain index; drop the superseded
    # unique one if this database still carries it.
    with engine.begin() as cx:
        cx.execute(text("DROP INDEX IF EXISTS idx_unique_active_app_per_survey"))
        # create_all() only adds missing tables, never columns. Bring existing
        # applicants/applications/application_sub_division* up to the current
        # model when this database predates these columns.
        for ddl in (
            "ALTER TABLE applicants ADD COLUMN IF NOT EXISTS permanent_address TEXT",
            "ALTER TABLE applicants ADD COLUMN IF NOT EXISTS father_name VARCHAR(200)",
            "ALTER TABLE applicants ADD COLUMN IF NOT EXISTS mother_name VARCHAR(200)",
            "ALTER TABLE applicants ADD COLUMN IF NOT EXISTS date_of_birth DATE",
            "ALTER TABLE applicants ADD COLUMN IF NOT EXISTS gender VARCHAR(10)",
            "ALTER TABLE applicants ADD COLUMN IF NOT EXISTS occupation VARCHAR(100)",
            "ALTER TABLE owners ADD COLUMN IF NOT EXISTS gender VARCHAR(10)",
            "ALTER TABLE owners ADD COLUMN IF NOT EXISTS address TEXT",
            "ALTER TABLE owners ADD COLUMN IF NOT EXISTS relationship_type VARCHAR(50)",
            "ALTER TABLE applications ADD COLUMN IF NOT EXISTS fee_amount NUMERIC(10,2)",
            "ALTER TABLE applications ADD COLUMN IF NOT EXISTS challan_number VARCHAR(50)",
            "ALTER TABLE applications ADD COLUMN IF NOT EXISTS payment_mode VARCHAR(20)",
            "ALTER TABLE applications ADD COLUMN IF NOT EXISTS igrs_form6_number VARCHAR(30)",
            "ALTER TABLE applications ADD COLUMN IF NOT EXISTS submission_source_name VARCHAR(100)",
            "ALTER TABLE applications ADD COLUMN IF NOT EXISTS submission_camp_flag VARCHAR(5)",
            "ALTER TABLE applications ADD COLUMN IF NOT EXISTS submission_ip VARCHAR(50)",
            "ALTER TABLE applications ADD COLUMN IF NOT EXISTS merged_application_id VARCHAR(30)",
            # MERGE (0155) was removed: no extract row carries it.
            "ALTER TABLE applications DROP CONSTRAINT IF EXISTS ck_application_type",
            "ALTER TABLE applications ADD CONSTRAINT ck_application_type "
            "CHECK (application_type IN ('ISD','NISD'))",
            "ALTER TABLE patta_transfers ADD COLUMN IF NOT EXISTS new_patta_number VARCHAR(50)",
            "ALTER TABLE patta_transfers ADD COLUMN IF NOT EXISTS signed_by VARCHAR(50)",
            "ALTER TABLE patta_transfers ADD COLUMN IF NOT EXISTS transfer_reason VARCHAR(120)",
            "ALTER TABLE patta_transfers ADD COLUMN IF NOT EXISTS transfer_type VARCHAR(60)",
            "ALTER TABLE patta_transfers ADD COLUMN IF NOT EXISTS registration_place VARCHAR(200)",
            "ALTER TABLE patta_transfers ADD COLUMN IF NOT EXISTS registration_date DATE",
            "ALTER TABLE patta_transfers ADD COLUMN IF NOT EXISTS old_patta_number VARCHAR(50)",
            "ALTER TABLE patta_transfers ADD COLUMN IF NOT EXISTS order_number VARCHAR(100)",
            "ALTER TABLE patta_transfers ADD COLUMN IF NOT EXISTS order_date DATE",
            "ALTER TABLE patta_transfers ADD COLUMN IF NOT EXISTS order_remarks TEXT",
            "ALTER TABLE patta_transfers ADD COLUMN IF NOT EXISTS sis_recommendation VARCHAR(10)",
            "ALTER TABLE patta_transfers ADD COLUMN IF NOT EXISTS sis_remarks TEXT",
            "ALTER TABLE patta_transfers ADD COLUMN IF NOT EXISTS sis_recommendation_reason TEXT",
            "ALTER TABLE application_sub_divisions ADD COLUMN IF NOT EXISTS "
            "temporary_sub_division_no VARCHAR(50)",
            "ALTER TABLE service_register_entries ADD COLUMN IF NOT EXISTS survey_no VARCHAR(50)",
            "ALTER TABLE service_register_entries ADD COLUMN IF NOT EXISTS subdivision_no VARCHAR(50)",
            "ALTER TABLE service_register_entries ADD COLUMN IF NOT EXISTS stage VARCHAR(30)",
            "ALTER TABLE service_register_entries ADD COLUMN IF NOT EXISTS submission_channel VARCHAR(20)",
        ):
            cx.execute(text(ddl))
        # town_code used to have a standalone unique constraint; it should be
        # (taluk_id, town_code) since the same code can appear in different taluks.
        cx.execute(text(
            "ALTER TABLE towns DROP CONSTRAINT IF EXISTS towns_town_code_key"))
        cx.execute(text("""
            DO $$ BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname='towns_taluk_id_town_code_key'
                ) THEN
                    ALTER TABLE towns ADD CONSTRAINT towns_taluk_id_town_code_key
                        UNIQUE (taluk_id, town_code);
                END IF;
            END $$"""))
    print("app tables created/verified")

    with engine.begin() as cx:
        # ---------- geography ----------
        # There is no parcel register for this extract (uareg / uchitta_natham
        # were Thoothukudi-only files that are not part of this district-02
        # batch), so geography is built entirely from appl_log_urban's own
        # district/taluk/town/ward/block columns -- the same source and the
        # same stable_id scheme a ward-mismatch fallback used to use only when
        # the parcel register didn't cover a ward. Covers EVERY service code
        # appl_log_urban carries, not just 0153/0154: other service types
        # (0159 Addition, 0160 Deletion, 0169 Govt-to-Private, ...) sit in
        # wards 0153/0154 never touch (002, 005, 007, 008, 009), and
        # service_register_entries below needs those wards to exist. Officer
        # assignment further down stays scoped to 0153/0154's own wards --
        # only ISD/NISD get a full workflow, so only their wards need a
        # dedicated caseload.
        rows = cx.execute(text(f"""
            SELECT DISTINCT {newcol('appl_log_urban', 'district_code')},
                             {newcol('appl_log_urban', 'taluk_code')},
                             {newcol('appl_log_urban', 'village_code')},
                             {newcol('appl_log_urban', 'ward_code')},
                             {newcol('appl_log_urban', 'block_code')}
            FROM appl_log_urban
            ORDER BY 1,2,3,4,5""")).all()

        district_id = {}
        taluk_id = {}
        town_id = {}
        ward_id = {}
        block_id = {}
        now = _utcnow()

        # Official names, from the department's master tables. Anything they do
        # not cover keeps the name this script used to make up for it.
        master = load_master_names(cx)
        from_master = Counter()

        def _name(level: str, key: tuple, fallback: str) -> str:
            official = master.get(level, {}).get(key)
            if official:
                from_master[level] += 1
                return official
            return fallback

        for dc, tk, tw, wd, bl in rows:
            # Districts and taluks are no longer built here -- they ARE the
            # master tables now, so this looks up the row that already exists
            # instead of inserting a duplicate of it. The UUID is the master's
            # own app_uid; a missing row is a hard error, because inventing one
            # would put the whole projection under a district that is not in
            # the department's record.
            if dc not in district_id:
                district_id[dc] = cx.execute(text(
                    "SELECT app_uid FROM district_unicode WHERE trim(district_code)=:c"),
                    dict(c=dc)).scalar()
                if district_id[dc] is None:
                    raise SystemExit(
                        f"district {dc} is not in `district_unicode`. Run:\n"
                        f"  python -m backend.sample_db.load_master_dumps\n"
                        f"  python -m backend.sample_db.adopt_master_district_taluk")
            if (dc, tk) not in taluk_id:
                taluk_id[(dc, tk)] = cx.execute(text(
                    "SELECT app_uid FROM taluk "
                    " WHERE trim(district_code)=:d AND trim(taluk_code)=:c"),
                    dict(d=dc, c=tk)).scalar()
                if taluk_id[(dc, tk)] is None:
                    raise SystemExit(
                        f"taluk {dc}/{tk} is not in the master `taluk` table. Run:\n"
                        f"  python -m backend.sample_db.load_master_dumps\n"
                        f"  python -m backend.sample_db.adopt_master_district_taluk")
            if (dc, tk, tw) not in town_id:
                town_id[(dc, tk, tw)] = stable_id("town", dc, tk, tw)
                cx.execute(text("""INSERT INTO towns (id,taluk_id,name,town_code,created_at,updated_at)
                    VALUES (:i,:k,:n,:c,:t,:t)
                    ON CONFLICT (id) DO UPDATE SET
                        taluk_id=EXCLUDED.taluk_id, name=EXCLUDED.name,
                        town_code=EXCLUDED.town_code, updated_at=EXCLUDED.updated_at"""),
                    dict(i=town_id[(dc, tk, tw)], k=taluk_id[(dc, tk)],
                         n=_name("town", (dc, tk, tw),
                                 DISTRICT_CODE_MAP.get(dc, "Town")),
                         c=tw, t=now))
            if (dc, tk, tw, wd) not in ward_id:
                ward_id[(dc, tk, tw, wd)] = stable_id("ward", dc, tk, tw, wd)
                cx.execute(text("""INSERT INTO wards (id,town_id,ward_number,ward_name,created_at,updated_at)
                    VALUES (:i,:o,:n,:w,:t,:t)
                    ON CONFLICT (id) DO UPDATE SET
                        town_id=EXCLUDED.town_id, ward_number=EXCLUDED.ward_number,
                        ward_name=EXCLUDED.ward_name, updated_at=EXCLUDED.updated_at"""),
                    dict(i=ward_id[(dc, tk, tw, wd)], o=town_id[(dc, tk, tw)],
                         n=wd,
                         w=_name("ward", (dc, tk, tw, wd), f"Ward {_display_code(wd)}"),
                         t=now))
            key = (dc, tk, tw, wd, bl)
            if key not in block_id:
                block_id[key] = stable_id("block", dc, tk, tw, wd, bl)
                cx.execute(text("""INSERT INTO blocks (id,ward_id,block_number,block_name,created_at,updated_at)
                    VALUES (:i,:w,:n,:b,:t,:t)
                    ON CONFLICT (id) DO UPDATE SET
                        ward_id=EXCLUDED.ward_id, block_number=EXCLUDED.block_number,
                        block_name=EXCLUDED.block_name, updated_at=EXCLUDED.updated_at"""),
                    dict(i=block_id[key], w=ward_id[(dc, tk, tw, wd)],
                         n=bl,
                         b=_name("block", key, f"Block {_display_code(bl)}"),
                         t=now))
        print(f"geography: {len(district_id)} district, {len(taluk_id)} taluk, "
              f"{len(town_id)} town, {len(ward_id)} ward, {len(block_id)} block "
              f"(from appl_log_urban -- no parcel register in this extract)")
        print(f"  district + taluk read from the master tables "
              f"(district_unicode, taluk) -- not rebuilt")
        _totals = {"town": len(town_id), "ward": len(ward_id), "block": len(block_id)}
        if any(master.get(lvl) for lvl in _totals):
            print("  names from the master tables: " + ", ".join(
                f"{lvl} {from_master[lvl]}/{total}" for lvl, total in _totals.items()))
        else:
            print("  master tables not loaded -- names synthesised as before "
                  "(run: python -m backend.sample_db.load_master_dumps)")

        # ---------- survey numbers, sub-divisions, owners ----------
        # No parcel register (uareg) or natham-chitta extract (uchitta_natham)
        # exists for this district -- both were Thoothukudi-only files not
        # part of this batch -- so there is no register to pre-populate these
        # from. Every application instead gets its own minimal placeholder
        # survey/sub-division from its own row, in the applications loop below
        # (the same fallback that used to fire only when a Thoothukudi ward
        # went unmatched now fires for every application, which is the honest
        # state of this data). Owners stay empty: there is no source for them.
        survey_id = {}          # (ward_code, block_code, survey_no) -> uuid
        survey_by_patta = {}    # patta -> (survey uuid, subdiv uuid) -- always empty here
        subdiv_id = {}          # (ward_code, block_code, survey_no, subdiv_no) -> subdiv uuid
        first_subdiv = {}       # (ward_code, block_code, survey_no) -> subdiv uuid
        print("survey_numbers: 0, sub_divisions: 0 (no parcel register -- built per-application below)")
        print("owners: 0, survey_ownership: 0 (no natham-chitta extract in this district)")

        # ---------- officers ----------
        # The SIS officers are the usernames that open the workflow chain
        # (role 41).
        sis_users = [r[0] for r in cx.execute(text(f"""
            SELECT DISTINCT {newcol('application_workflow', 'updated_by_user')}
            FROM application_workflow
            WHERE {newcol('application_workflow', 'action_from_role_id')} = '41'
            ORDER BY 1""")).all()]
        wards_sorted = sorted(ward_id.items(), key=lambda kv: kv[0][3])

        # An officer must hold the wards the applications actually sit in --
        # otherwise the file is assigned to someone whose jurisdiction filter
        # then hides it, and they cannot open their own ward's application.
        # ward_id was itself built from appl_log_urban's 0153/0154 rows above,
        # so this intersection is normally a no-op (every ward already carries
        # applications); it is kept as a safety net in case a future geography
        # source widens ward_id beyond what actually has applications. An
        # officer can hold more than one ward when there are fewer officers
        # than wards.
        app_wards = {r[0] for r in cx.execute(text(f"""
            SELECT DISTINCT {newcol('appl_log_urban', 'ward_code')} FROM appl_log_urban
            WHERE {newcol('appl_log_urban', 'service_code')} IN ('0153','0154')""")).all()}
        covered = [kv for kv in wards_sorted if kv[0][3] in app_wards] or wards_sorted
        wards_of = defaultdict(list)
        for n, kv in enumerate(covered):
            wards_of[n % len(sis_users)].append(kv)
        for n in range(len(sis_users)):                 # more officers than wards
            wards_of.setdefault(n, [covered[n % len(covered)]])

        officer_id = {}
        officer_for_ward = {}
        for n, user in enumerate(sis_users):
            oid = uuid.uuid5(_OFFICER_NS, user)             # stable across reruns -- see _OFFICER_NS
            officer_id[user] = oid
            name = humanise(user)
            # Upsert, not insert: the row must keep existing across a rebuild for
            # a live JWT / chat_sessions row to keep resolving. password_hash is
            # deliberately left out of the UPDATE clause -- the seeded password
            # never changes, and overwriting it on conflict would just be a
            # same-value rehash for no reason.
            cx.execute(text("""INSERT INTO sis_officers
                (id,employee_id,name,name_tamil,email,password_hash,mobile,
                 designation,is_active,created_at,updated_at)
                VALUES (:i,:e,:n,:nt,:m,:p,:mo,:d,:a,:t,:t)
                ON CONFLICT (id) DO UPDATE SET
                    employee_id=EXCLUDED.employee_id, name=EXCLUDED.name,
                    name_tamil=EXCLUDED.name_tamil, email=EXCLUDED.email,
                    designation=EXCLUDED.designation, is_active=EXCLUDED.is_active,
                    updated_at=EXCLUDED.updated_at"""),
                dict(i=oid, e=f"SIS-{n+1:03d}", n=name, nt=None,
                     m=f"{user.replace('tut_', '')}@sis.tn.gov.in",
                     p=get_password_hash(DEFAULT_PASSWORD), mo=None,
                     d="Sub Inspector Surveyor", a=True, t=now))
            for wkey, wuuid in wards_of[n]:
                officer_for_ward.setdefault(wkey[3], oid)
                # The parent ids must be filled in as well, not just ward_id:
                # auth_service.get_officer_jurisdiction_ids reads district/taluk/
                # town straight off this row for a ward-level officer.
                dc, tk, tw, wd = wkey
                cx.execute(text("""INSERT INTO officer_jurisdictions
                    (id,officer_id,jurisdiction_type,district_id,taluk_id,town_id,
                     ward_id,block_id,created_at,updated_at)
                    VALUES (:i,:o,'ward',:d,:k,:w2,:w,NULL,:t,:t)
                    ON CONFLICT (id) DO UPDATE SET
                        district_id=EXCLUDED.district_id, taluk_id=EXCLUDED.taluk_id,
                        town_id=EXCLUDED.town_id, ward_id=EXCLUDED.ward_id,
                        updated_at=EXCLUDED.updated_at"""),
                    dict(i=stable_id("officer_jurisdiction", oid, dc, tk, tw, wd),
                         o=oid, d=district_id[dc], k=taluk_id[(dc, tk)],
                         w2=town_id[(dc, tk, tw)], w=wuuid, t=now))
        # Every ward -- including one whose only applications are a service
        # code that never becomes an `Application` (0159, 0160, 0169, ...) --
        # still needs an officer with real DB jurisdiction over it, or its
        # service_register_entries rows are invisible to everybody. Wards
        # already granted above (the 0153/0154 caseload) are skipped; the
        # rest are handed out round-robin so they don't all pile onto one
        # officer the way officer_for_ward's own fallback below does (that
        # dict only steers ISD/NISD application assignment, so its single-
        # officer fallback doesn't create the same jurisdiction gap).
        _extra_ward_cycle = itertools.cycle(officer_id.values())
        for wkey, wuuid in wards_sorted:
            wd = wkey[3]
            officer_for_ward.setdefault(wd, next(iter(officer_id.values())))
            if wd in app_wards:
                continue
            dc, tk, tw, _wd = wkey
            grant_officer = next(_extra_ward_cycle)
            cx.execute(text("""INSERT INTO officer_jurisdictions
                (id,officer_id,jurisdiction_type,district_id,taluk_id,town_id,
                 ward_id,block_id,created_at,updated_at)
                VALUES (:i,:o,'ward',:d,:k,:w2,:w,NULL,:t,:t)
                ON CONFLICT (id) DO UPDATE SET
                    district_id=EXCLUDED.district_id, taluk_id=EXCLUDED.taluk_id,
                    town_id=EXCLUDED.town_id, ward_id=EXCLUDED.ward_id,
                    updated_at=EXCLUDED.updated_at"""),
                dict(i=stable_id("officer_jurisdiction", grant_officer, dc, tk, tw, wd),
                     o=grant_officer, d=district_id[dc], k=taluk_id[(dc, tk)],
                     w2=town_id[(dc, tk, tw)], w=wuuid, t=now))
        print(f"sis_officers: {len(officer_id)} (password '{DEFAULT_PASSWORD}'), "
              f"wards held: "
              + ", ".join(f"{w}" for w in sorted(officer_for_ward)))

        # ---------- service register (every service code, informational) ----------
        # "Show all applications" meaning only ISD/NISD is a real gap an
        # officer hits: 0158/0159/0160/0162/0164/0167/0169/0178 (and others)
        # are real applications sitting in appl_log_urban that `applications`
        # was never meant to carry (see ServiceRegisterEntry's own docstring).
        # This is the flat, informational register that closes that gap --
        # one row per application_id, every service code, no workflow.
        sre_rows = []
        for r in cx.execute(text(f"""
            SELECT * FROM (
                SELECT DISTINCT ON ({newcol('appl_log_urban', 'application_id')})
                       {newcol('appl_log_urban', 'application_id')},
                       {newcol('appl_log_urban', 'service_code')},
                       {newcol('appl_log_urban', 'district_code')},
                       {newcol('appl_log_urban', 'taluk_code')},
                       {newcol('appl_log_urban', 'village_code')},
                       {newcol('appl_log_urban', 'ward_code')},
                       {newcol('appl_log_urban', 'block_code')},
                       {newcol('appl_log_urban', 'application_date')},
                       {newcol('appl_log_urban', 'application_status')},
                       {newcol('appl_log_urban', 'survey_number')},
                       {newcol('appl_log_urban', 'subdivision_number')},
                       {newcol('appl_log_urban', 'workflow_state')},
                       {newcol('appl_log_urban', 'source_name')},
                       {newcol('appl_log_urban', 'camp_flag')}
                FROM appl_log_urban
                ORDER BY {newcol('appl_log_urban', 'application_id')},
                         {newcol('appl_log_urban', 'last_updated_datetime')} DESC NULLS LAST,
                         {newcol('appl_log_urban', 'application_date')} DESC
            ) latest""")).all():
            (app_id, svc, dc, tk, tw, wd, bl, sub_date, status,
             sno, subdiv_no, wf_state, src_name, camp_flag) = r
            wd_key = (dc, tk, tw, wd)
            if wd_key not in ward_id:
                continue  # geography above covers every ward in this table; defensive only
            bl_key = (dc, tk, tw, wd, bl)
            # workflow_state is the same C(losed)/P(ending) marker
            # STATUS_MAP's own comment reads for 0153/0154 -- C is a closed
            # chain (approved or rejected), P an open one. There is no
            # multi-desk chain to name for these other service codes, so
            # this is as far as "stage" can honestly go for them.
            sre_rows.append(dict(
                i=stable_id("service_register_entry", app_id),
                num=app_id, svc=svc, sno=sno, sub=subdiv_no,
                st=STATUS_MAP.get(status, "pending"),
                stg=("Completed" if (wf_state or "").strip().upper() == "C" else "Open"),
                ch=can_channel(src_name, camp_flag),
                d=_to_date(sub_date), w=ward_id[wd_key],
                b=block_id.get(bl_key), t=now))
        if sre_rows:
            cx.execute(text("""INSERT INTO service_register_entries
                (id,application_number,service_code,survey_no,subdivision_no,
                 status,stage,submission_channel,submission_date,ward_id,block_id,created_at,updated_at)
                VALUES (:i,:num,:svc,:sno,:sub,:st,:stg,:ch,:d,:w,:b,:t,:t)
                ON CONFLICT (id) DO UPDATE SET
                    service_code=EXCLUDED.service_code, survey_no=EXCLUDED.survey_no,
                    subdivision_no=EXCLUDED.subdivision_no, status=EXCLUDED.status,
                    stage=EXCLUDED.stage, submission_channel=EXCLUDED.submission_channel,
                    submission_date=EXCLUDED.submission_date,
                    ward_id=EXCLUDED.ward_id, block_id=EXCLUDED.block_id,
                    updated_at=EXCLUDED.updated_at"""), sre_rows)
        _by_svc = Counter(r["svc"] for r in sre_rows)
        print(f"service_register_entries: {len(sre_rows)} across {len(_by_svc)} service codes "
              "(" + ", ".join(f"{c}:{n}" for c, n in sorted(_by_svc.items())) + ")")

        # ---------- applicants + applications ----------
        # Common columns across both application-info extracts (isd_ lacks
        # `occupation`); selected by name so each row is a dict.
        info = {}
        _INFO_COLS = ["applicant_name", "mobile_number", "current_address",
                      "application_status", "permanent_address", "father_name",
                      "mother_name", "date_of_birth", "gender",
                      "challan_number", "payment_mode", "payment_amount",
                      "igrs_form6_number"]
        for table in ("full_field_patta_transfer_application_information", "sub_div_patta_transfer_application_information_urban"):
            is_isd = table == "sub_div_patta_transfer_application_information_urban"
            cols = list(_INFO_COLS)
            if not is_isd:
                cols.append("occupation")           # nisd_ only
            else:
                cols.append("merged_application_id")  # isd_ only
            for r in cx.execute(text(
                    f"SELECT {sel(table, 'application_id', *cols)} FROM {table}")).mappings():
                d = dict(r)
                d.setdefault("occupation", None)
                d.setdefault("merged_application_id", None)
                info[r["application_id"]] = d

        def _clean_info(v):
            v = (str(v).strip() if v is not None else "")
            return v if v and v not in ("-", "--", "N/A", "Not Stated", "NA") else None

        # IGRS Form 6 number also lives on the registration-side owner extract;
        # use it when the application-info row lacks one.
        igrs_f6 = {}
        _igrs_tbl = "full_field_patta_transfer_igrs_owner"
        for r in cx.execute(text(f"""
            SELECT {newcol(_igrs_tbl, 'application_id')}, {newcol(_igrs_tbl, 'igrs_form6_number')}
            FROM {_igrs_tbl} WHERE {newcol(_igrs_tbl, 'igrs_form6_number')} IS NOT NULL""")).all():
            igrs_f6.setdefault(r[0], r[1])

        # The registration document and the reason for transfer live in the
        # detail extracts; without them the model's sale_deed_number and
        # declared_reason stayed NULL and the bot answered "I don't have that
        # information" for sale-deed and sub-registrar questions. Only the
        # NISD-side source is read here -- sub_div_patta_transfer_urban (the
        # ISD-side one) is not part of this extract.
        deed = {}
        for table in ("full_field_patta_transfer_urban",):
            _c = ["application_id", "registration_document_number", "transfer_reason",
                  "registration_place", "registration_date"]
            for r in cx.execute(text(f"""
                SELECT {', '.join(newcol(table, c) for c in _c)}
                FROM {table}""")).all():
                if r[1]:
                    deed[r[0]] = r

        # An application that covers several parcels has one appl_log_urban_demo
        # row per parcel (1211 rows over 1139 application_ids in the extracts),
        # and the rows can disagree on status as the file moves. `applications`
        # holds one row per application, so keep the most recently updated one --
        # that is the application's current state. The other parcels are still
        # reachable through the sub-division and transfer-detail tables.
        _alu = "appl_log_urban"
        _alu_cols = ["application_id", "service_code", "district_code", "taluk_code",
                     "village_code", "ward_code", "block_code", "survey_number",
                     "application_date", "application_status", "can_number",
                     "source_code", "source_name", "igrs_form6_number",
                     "igrs_auto_mutation_flag", "camp_flag", "ip_address",
                     "subdivision_number"]
        apps = cx.execute(text(f"""
            SELECT * FROM (
                SELECT DISTINCT ON ({newcol(_alu, 'application_id')})
                       {', '.join(newcol(_alu, c) for c in _alu_cols)}
                FROM {_alu}
                WHERE {newcol(_alu, 'service_code')} IN ('0153','0154')
                ORDER BY {newcol(_alu, 'application_id')},
                         {newcol(_alu, 'last_updated_datetime')} DESC NULLS LAST,
                         {newcol(_alu, 'application_date')} DESC
            ) latest
            ORDER BY {newcol(_alu, 'application_date')}""")).all()

        # last workflow action per application -> stage, and field visit date
        last_action = {}
        last_hop = {}
        fv_date = {}
        _awf = "application_workflow"
        _awf_cols1 = ["application_id", "action_to_role_id", "action_date",
                     "field_visit_date", "serial_number"]
        for r in cx.execute(text(f"""
            SELECT {', '.join(newcol(_awf, c) for c in _awf_cols1)}
            FROM {_awf} ORDER BY {newcol(_awf, 'application_id')}, {newcol(_awf, 'serial_number')}""")).all():
            last_action[r[0]] = r
            # The chain's closing row routes to role "0" -- an end marker, not a
            # desk. The last row that names a real role is the one that says
            # where the file actually is.
            if r[1] and r[1] != "0":
                last_hop[r[0]] = r
            if r[3]:
                fv_date[r[0]] = r[3]

        applicant_rows, app_rows, doc_rows, appsub_rows = [], [], [], []
        status_of, reason_of = {}, {}
        active_taken = set()
        app_uuid = {}
        skipped_no_survey = 0
        synthetic_surveys = 0
        # Filled per application below, read after the areg_temp_subdivclub
        # loop to backfill ISD files that extract has no row for at all.
        type_of, app_survey_key, raw_subdiv_of = {}, {}, {}

        can_repaired = can_dropped = 0
        # A ward outside uareg (this district-02 extract's own wards) gets
        # handed out round-robin across the SAME officers, rather than every
        # such ward piling onto a single fixed officer -- each officer still
        # ends up with a real, distinct ward of their own to answer for.
        _synthetic_officer_cycle = itertools.cycle(officer_id.values())
        _synthetic_jurisdiction_granted = set()   # wd_key already granted to its officer
        for (app_id, svc, dc_app, tk_app, tw_app, wd, bl_app, sno, sub_date, status, can,
             _src, src_name, form6, _auto_mut, camp_flag, ip_addr, subdiv_no_raw) in apps:
            sub_date = _to_date(sub_date)
            key = (wd, bl_app, sno)
            if key not in survey_id:
                # The application's ward/block/survey isn't in uareg (different
                # district extract). Build minimal geography + survey entries
                # from the application's own geography columns so the FK
                # resolves and the application can still be projected.
                if dc_app not in district_id:
                    district_id[dc_app] = cx.execute(text(
                        "SELECT app_uid FROM district_unicode WHERE trim(district_code)=:c"),
                        dict(c=dc_app)).scalar()
                if district_id.get(dc_app) is None:
                    skipped_no_survey += 1
                    continue
                if (dc_app, tk_app) not in taluk_id:
                    taluk_id[(dc_app, tk_app)] = cx.execute(text(
                        "SELECT app_uid FROM taluk WHERE trim(district_code)=:d AND trim(taluk_code)=:c"),
                        dict(d=dc_app, c=tk_app)).scalar()
                if taluk_id.get((dc_app, tk_app)) is None:
                    skipped_no_survey += 1
                    continue
                tw_key = (dc_app, tk_app, tw_app)
                if tw_key not in town_id:
                    town_id[tw_key] = stable_id("town", dc_app, tk_app, tw_app)
                    cx.execute(text("""INSERT INTO towns (id,taluk_id,name,town_code,created_at,updated_at)
                        VALUES (:i,:k,:n,:c,:t,:t)
                        ON CONFLICT (id) DO UPDATE SET
                            taluk_id=EXCLUDED.taluk_id, name=EXCLUDED.name,
                            town_code=EXCLUDED.town_code, updated_at=EXCLUDED.updated_at"""),
                        dict(i=town_id[tw_key], k=taluk_id[(dc_app, tk_app)],
                             n=_name("town", tw_key, DISTRICT_CODE_MAP.get(dc_app, "Town")),
                             c=tw_app, t=now))
                wd_key = (dc_app, tk_app, tw_app, wd)
                if wd_key not in ward_id:
                    ward_id[wd_key] = stable_id("ward", dc_app, tk_app, tw_app, wd)
                    cx.execute(text("""INSERT INTO wards (id,town_id,ward_number,ward_name,created_at,updated_at)
                        VALUES (:i,:o,:n,:w,:t,:t)
                        ON CONFLICT (id) DO UPDATE SET
                            town_id=EXCLUDED.town_id, ward_number=EXCLUDED.ward_number,
                            ward_name=EXCLUDED.ward_name, updated_at=EXCLUDED.updated_at"""),
                        dict(i=ward_id[wd_key], o=town_id[tw_key],
                             n=wd, w=_name("ward", wd_key, f"Ward {_display_code(wd)}"), t=now))
                # This ward is outside uareg (it's from the district-02 extract,
                # which officer_for_ward was never built to cover), so nobody
                # holds it yet -- assign it to the next officer in rotation AND
                # actually grant them jurisdiction over it. Skipping the grant
                # was the bug: the application's assigned_officer_id named this
                # officer while officer_jurisdictions never did, so the
                # officer's own jurisdiction-filtered queries
                # (get_application_detail, ...) could not find their own
                # assigned application. Round-robin (rather than always the
                # same officer) is what keeps every district-02 ward from
                # piling onto one officer while the others hold none of it.
                if wd not in officer_for_ward:
                    officer_for_ward[wd] = next(_synthetic_officer_cycle)
                ward_officer = officer_for_ward[wd]
                if wd_key not in _synthetic_jurisdiction_granted:
                    _synthetic_jurisdiction_granted.add(wd_key)
                    cx.execute(text("""INSERT INTO officer_jurisdictions
                        (id,officer_id,jurisdiction_type,district_id,taluk_id,town_id,
                         ward_id,block_id,created_at,updated_at)
                        VALUES (:i,:o,'ward',:d,:k,:w2,:w,NULL,:t,:t)
                        ON CONFLICT (id) DO UPDATE SET
                            district_id=EXCLUDED.district_id, taluk_id=EXCLUDED.taluk_id,
                            town_id=EXCLUDED.town_id, ward_id=EXCLUDED.ward_id,
                            updated_at=EXCLUDED.updated_at"""),
                        dict(i=stable_id("officer_jurisdiction", ward_officer,
                                          dc_app, tk_app, tw_app, wd),
                             o=ward_officer, d=district_id[dc_app],
                             k=taluk_id[(dc_app, tk_app)], w2=town_id[tw_key],
                             w=ward_id[wd_key], t=now))
                bl_key = (dc_app, tk_app, tw_app, wd, bl_app)
                if bl_key not in block_id:
                    block_id[bl_key] = stable_id("block", dc_app, tk_app, tw_app, wd, bl_app)
                    cx.execute(text("""INSERT INTO blocks (id,ward_id,block_number,block_name,created_at,updated_at)
                        VALUES (:i,:w,:n,:b,:t,:t)
                        ON CONFLICT (id) DO UPDATE SET
                            ward_id=EXCLUDED.ward_id, block_number=EXCLUDED.block_number,
                            block_name=EXCLUDED.block_name, updated_at=EXCLUDED.updated_at"""),
                        dict(i=block_id[bl_key], w=ward_id[wd_key],
                             n=bl_app, b=_name("block", bl_key, f"Block {_display_code(bl_app)}"), t=now))
                if key not in survey_id:
                    sid = stable_id("survey_number", wd, bl_app, sno)
                    survey_id[key] = sid
                    cx.execute(text("""INSERT INTO survey_numbers
                        (id,block_id,survey_no,total_area_sqm,land_type,
                         patta_number,has_encroachment,has_litigation,litigation_reference,
                         created_at,updated_at)
                        VALUES (:i,:b,:s,:a,:l,:p,:e,:g,:r,:t,:t)
                        ON CONFLICT (id) DO UPDATE SET
                            block_id=EXCLUDED.block_id, survey_no=EXCLUDED.survey_no,
                            updated_at=EXCLUDED.updated_at"""),
                        dict(i=sid, b=block_id[bl_key], s=sno, a=0.0,
                             l="residential", p=None, e=False, g=False, r=None, t=now))
                    # Minimal sub_division so FK from applications resolves
                    sub_uuid = stable_id("sub_division", wd, bl_app, sno, "0")
                    cx.execute(text("""INSERT INTO sub_divisions
                        (id,survey_number_id,sub_division_no,area_sqm,status,
                         created_at,updated_at)
                        VALUES (:i,:s,:n,:a,:st,:t,:t)
                        ON CONFLICT (id) DO UPDATE SET
                            survey_number_id=EXCLUDED.survey_number_id,
                            updated_at=EXCLUDED.updated_at"""),
                        dict(i=sub_uuid, s=sid, n=f"{sno}/0", a=0.0,
                             st="active", t=now))
                    subdiv_id[(wd, bl_app, sno, "0")] = sub_uuid
                    first_subdiv[key] = sub_uuid
                    synthetic_surveys += 1
            atype = SERVICE_TO_TYPE[svc]
            type_of[app_id] = atype
            app_survey_key[app_id] = key
            raw_subdiv_of[app_id] = (str(subdiv_no_raw).strip()
                                      if subdiv_no_raw is not None
                                      and str(subdiv_no_raw).strip() not in ("", "-", "None")
                                      else None)
            rec = info.get(app_id) or {}
            status_text = (rec.get("application_status") or "").strip().lower()
            cur_status = STATUS_MAP.get(status, "pending")
            if cur_status == "pending":
                cur_status = OPEN_TEXT_MAP.get(status_text, "pending")

            # The extracts do carry several concurrently-active applications on
            # one survey number (a parcel can be under more than one request at
            # a time), so the status is projected exactly as the log records it.
            if cur_status in ("pending", "in_progress", "escalated"):
                active_taken.add(survey_id[key])

            aid = stable_id("applicant", app_id)
            name = rec.get("applicant_name") or None
            # The extracts strip the applicant's Aadhaar, so it is derived the
            # same way seed_sample_db.py derives an owner's: keyed on the name,
            # which means an applicant who also holds a natham chitta patta
            # carries the one number across both layers. Only the last four
            # digits are kept -- that is all the model stores.
            aadhaar = aadhaar_for(name or f"applicant|{app_id}")
            _dob = rec.get("date_of_birth")
            if isinstance(_dob, str):
                try:
                    _dob = datetime.strptime(_dob[:10], "%Y-%m-%d").date()
                except ValueError:
                    _dob = None
            applicant_rows.append(dict(
                i=aid,
                n=name or "Citizen Applicant",
                m=(rec.get("mobile_number") or None),
                a=aadhaar[-4:],
                ad=(rec.get("current_address") or None),
                pad=_clean_info(rec.get("permanent_address")),
                fn=_clean_info(rec.get("father_name")),
                mn=_clean_info(rec.get("mother_name")),
                dob=_dob,
                g=_clean_info(rec.get("gender")),
                occ=_clean_info(rec.get("occupation")),
                t=now))

            if cur_status == "rejected":
                stage = "REJECTED"
            elif cur_status == "approved":
                stage = "COMPLETED"
            elif cur_status == "in_progress" and status_text == "forward to zdt":
                # application_workflow's own hop log lags the application-info
                # extract's free-text status here -- "Forward To ZDT" is what
                # made cur_status "in_progress" in the first place (OPEN_TEXT_MAP
                # above), but no matching role-16 hop exists yet in the workflow
                # table, so falling through to `last_hop` below left stage
                # pinned at "SIS" -- a file the register itself says has moved
                # on, reported as still sitting on the surveyor's own desk.
                stage = "TAHSILDAR"
            else:
                la = last_hop.get(app_id)
                stage = ROLE_TO_STAGE.get(la[1], "SIS") if la else "SIS"

            # application_workflow_demo carries a field_visit_date on NISD rows
            # too, but NISD has no field visit -- projecting it made 167 of 168
            # NISD applications report a scheduled visit that no field_visits row
            # backs, and the answer quoted a date the workflow never produced.
            visit = fv_date.get(app_id) if atype in FIELD_VISIT_TYPES else None
            # visit may come back as a string if the column type is DATE but
            # psycopg2 returned it unparsed (e.g. on some configurations).
            if isinstance(visit, str):
                try:
                    visit = date.fromisoformat(visit[:10])
                except (ValueError, TypeError):
                    visit = None
            overdue = False
            if atype == "ISD" and cur_status in ("pending", "in_progress", "escalated"):
                # "field visit MUST be scheduled within 15 working days of
                # application submission ... if not completed within 15 days the
                # application is marked overdue" (workflow_guide.txt). A visit
                # dated in the future is only scheduled, not completed, so the
                # clock is still running against today.
                today = date.today()
                ref = visit if (visit and visit <= today) else today
                overdue = working_days_between(sub_date, ref) > 15

            auuid = stable_id("application", app_id)
            app_uuid[app_id] = auuid
            status_of[app_id] = cur_status
            reason_of[app_id] = (rec.get("application_status") or None)
            d_rec = deed.get(app_id)
            deed_no = d_rec[1] if d_rec else None
            reason = (TRANSFER_REASON_TO_DECLARED.get((d_rec[2] or "").strip().lower())
                      if d_rec else None)
            notes = (f"Registered at {d_rec[3]} on {d_rec[4]}"
                     if d_rec and d_rec[3] and d_rec[4] else None)
            # source_name says whether anyone keyed the file in: a placeholder
            # (`-`) is the unattended Sub-Registrar route, an operator code is a
            # counter. On an attended row camp_flag = 'P' marks a special camp,
            # where the operator keys the file in for the citizen present, so it
            # counts as the citizen's own submission; everything else attended is
            # CSC. The channel then fixes the lengths a CAN may have,
            # and a value that cannot be brought to one is not a CAN and is
            # dropped.
            channel = can_channel(src_name, camp_flag)
            can_no = normalize_can(can, channel)
            if can_no is None:
                can_dropped += can is not None
            elif can_no != (can or "").strip():
                can_repaired += 1

            try:
                _fee = float(rec.get("payment_amount")) if rec.get("payment_amount") not in (None, "", "-") else None
            except (TypeError, ValueError):
                _fee = None
            # officer_for_ward[wd] is always set by this point -- either from
            # the uareg-ward round robin above, or from the district-02
            # round robin in the synthetic branch this application's own
            # ward just went through. The `next(...)` is a defensive
            # fallback only, never expected to fire.
            app_rows.append(dict(
                i=auuid, num=app_id, ty=atype, ap=aid, s=survey_id[key],
                o=officer_for_ward.get(wd) or next(iter(officer_id.values())),
                ch=channel, d=sub_date,
                # The two columns the channel was derived from, carried
                # through so the answer can cite them. Kept verbatim, not
                # through _clean_info: '-' is the signal here, not a blank.
                src=(str(src_name).strip() if src_name is not None else None),
                ip=(str(ip_addr).strip() or None) if ip_addr is not None else None,
                camp=(str(camp_flag).strip() if camp_flag is not None else None),
                sd=deed_no, sr=deed_no is not None, dr=reason,
                can=can_no, st=stage, cs=cur_status,
                fv=visit, fs=visit is not None, ov=overdue,
                pr=cur_status == "escalated", no=notes,
                fee=_fee, chn=_clean_info(rec.get("challan_number")),
                pm=_clean_info(rec.get("payment_mode")),
                # the log carries a Form 6 for two applications the transfer
                # info tables miss, and never disagrees where both have one
                f6=(_clean_info(rec.get("igrs_form6_number"))
                    or igrs_f6.get(app_id) or _clean_info(form6)),
                mrg=_clean_info(rec.get("merged_application_id")),
                t=now))

            for doc in REQUIRED_DOCS[atype]:
                doc_rows.append(dict(
                    i=stable_id("application_document", app_id, doc),
                    a=auuid, ty=doc, n=f"{doc} - {app_id}",
                    u=cur_status != "pending", v=cur_status in ("approved", "in_progress"),
                    ua=now if cur_status != "pending" else None, t=now))

        # Unlike build_app_tables.py's own sis_chatbot_db, where the application
        # log and the parcel register always share a district, these three can
        # legitimately come back empty here: the ward/survey key an application
        # resolves through may simply not exist in this batch's register (see
        # the module docstring / column_map.py for what that means for THIS
        # extract pair). An empty list makes SQLAlchemy refuse the whole
        # INSERT ("a value is required for bind parameter") rather than
        # inserting nothing, so each is skipped rather than crashing the run.
        if applicant_rows:
            cx.execute(text("""INSERT INTO applicants
                (id,name,mobile,aadhaar_last4,address,permanent_address,father_name,
                 mother_name,date_of_birth,gender,occupation,created_at,updated_at)
                VALUES (:i,:n,:m,:a,:ad,:pad,:fn,:mn,:dob,:g,:occ,:t,:t)
                ON CONFLICT (id) DO UPDATE SET
                    name=EXCLUDED.name, mobile=EXCLUDED.mobile,
                    aadhaar_last4=EXCLUDED.aadhaar_last4, address=EXCLUDED.address,
                    permanent_address=EXCLUDED.permanent_address,
                    father_name=EXCLUDED.father_name, mother_name=EXCLUDED.mother_name,
                    date_of_birth=EXCLUDED.date_of_birth, gender=EXCLUDED.gender,
                    occupation=EXCLUDED.occupation, updated_at=EXCLUDED.updated_at"""),
                applicant_rows)
        if app_rows:
            cx.execute(text("""INSERT INTO applications
                (id,application_number,application_type,applicant_id,survey_number_id,
                 assigned_officer_id,submission_channel,submission_source_name,
                 submission_ip,submission_camp_flag,submission_date,sale_deed_number,
                 sale_deed_registered,declared_reason,can_number,current_stage,
                 current_status,field_visit_date,field_visit_scheduled,is_overdue,
                 priority_flag,notes,fee_amount,challan_number,payment_mode,
                 igrs_form6_number,merged_application_id,created_at,updated_at)
                VALUES (:i,:num,:ty,:ap,:s,:o,:ch,:src,:ip,:camp,:d,:sd,:sr,:dr,:can,:st,:cs,:fv,:fs,
                        :ov,:pr,:no,:fee,:chn,:pm,:f6,:mrg,:t,:t)
                ON CONFLICT (id) DO UPDATE SET
                    application_number=EXCLUDED.application_number,
                    application_type=EXCLUDED.application_type,
                    applicant_id=EXCLUDED.applicant_id,
                    survey_number_id=EXCLUDED.survey_number_id,
                    assigned_officer_id=EXCLUDED.assigned_officer_id,
                    submission_channel=EXCLUDED.submission_channel,
                    submission_source_name=EXCLUDED.submission_source_name,
                    submission_ip=EXCLUDED.submission_ip,
                    submission_camp_flag=EXCLUDED.submission_camp_flag,
                    submission_date=EXCLUDED.submission_date,
                    sale_deed_number=EXCLUDED.sale_deed_number,
                    sale_deed_registered=EXCLUDED.sale_deed_registered,
                    declared_reason=EXCLUDED.declared_reason,
                    can_number=EXCLUDED.can_number, current_stage=EXCLUDED.current_stage,
                    current_status=EXCLUDED.current_status,
                    field_visit_date=EXCLUDED.field_visit_date,
                    field_visit_scheduled=EXCLUDED.field_visit_scheduled,
                    is_overdue=EXCLUDED.is_overdue, priority_flag=EXCLUDED.priority_flag,
                    notes=EXCLUDED.notes, fee_amount=EXCLUDED.fee_amount,
                    challan_number=EXCLUDED.challan_number, payment_mode=EXCLUDED.payment_mode,
                    igrs_form6_number=EXCLUDED.igrs_form6_number,
                    merged_application_id=EXCLUDED.merged_application_id,
                    updated_at=EXCLUDED.updated_at"""), app_rows)
        if doc_rows:
            cx.execute(text("""INSERT INTO application_documents
                (id,application_id,document_type,document_name,is_uploaded,is_verified,
                 uploaded_at,created_at,updated_at)
                VALUES (:i,:a,:ty,:n,:u,:v,:ua,:t,:t)
                ON CONFLICT (id) DO UPDATE SET
                    application_id=EXCLUDED.application_id,
                    document_type=EXCLUDED.document_type,
                    document_name=EXCLUDED.document_name,
                    is_uploaded=EXCLUDED.is_uploaded, is_verified=EXCLUDED.is_verified,
                    uploaded_at=EXCLUDED.uploaded_at, updated_at=EXCLUDED.updated_at"""),
                doc_rows)
        print(f"applicants: {len(applicant_rows)}, applications: {len(app_rows)} "
              f"(skipped {skipped_no_survey} with no matching survey, "
              f"{synthetic_surveys} synthetic survey entries created), "
              f"documents: {len(doc_rows)}")
        by_channel = defaultdict(int)
        for r in app_rows:
            by_channel[r["ch"]] += 1
        print("  channel: " + ", ".join(
            f"{by_channel[c]} {c}" for c in CAN_LENGTHS if by_channel[c]))
        print("  CAN: " + ", ".join(
            f"{sum(1 for r in app_rows if r['ch'] == c and r['can'])} {c} "
            f"({'/'.join(str(n) for n in CAN_LENGTHS[c])} digits)"
            for c in CAN_LENGTHS if by_channel[c])
            + f", {can_repaired} repaired, {can_dropped} dropped as not a CAN")
        by_status = defaultdict(int)
        for r in app_rows:
            by_status[r["cs"]] += 1
        print("  status: " + ", ".join(f"{k} {v}" for k, v in sorted(by_status.items())))

        # ---------- application sub-divisions (ISD) ----------
        # An ISD request splits one parent parcel into several new sub-divisions.
        # Each row carries the temporary number the file runs under ("3/T1") and,
        # once assigned, the final one ("4"). Both are kept: an officer asking
        # "what temporary number was assigned on 2022/0154/28/000779?" must still
        # get an answer after the final numbers exist.
        #
        # Resolving the parent parcel takes four steps, because only the FIRST
        # row of an application carries a real existing_patta_number and the rest
        # hold "-" -- and a split large enough to be filed as several
        # applications (survey 35's 2A/T1..2A/T8 is five files) leaves the later
        # files with no patta at all:
        #   1. the row's own patta,
        #   2. the parent of the temporary number -- "2A/T7" -> sub-division 2A
        #      of that ward+block+survey,
        #   3. the parent already resolved for an earlier row of the same file,
        #   4. any sub-division of that ward+block+survey -- the parcel is at
        #      least identified that far, and dropping the row loses it entirely.
        _SUBDIV_STATUS = {"approved": "approved", "rejected": "rejected",
                          "in_progress": "in_progress"}
        parent_subdiv = {}   # application_id -> parent sub_division uuid
        appsub_by_tmp = {}   # (application_id, temporary_subdivision_number) -> appsub uuid
        unresolved = []
        _areg = "areg_temp_subdivclub"
        _areg_cols = ["application_id", "temporary_subdivision_number",
                     "new_subdivision_number", "area_square_meter", "existing_patta_number",
                     "ward_code", "block_code", "survey_number", "row_id"]
        for r in cx.execute(text(f"""
            SELECT {', '.join(newcol(_areg, c) for c in _areg_cols)}
            FROM {_areg}
            ORDER BY {newcol(_areg, 'application_id')}, {newcol(_areg, 'row_id')}""")).all():
            app_id, tmp_no, new_no, area, patta, ward, block, survey, _rid = r
            if app_id not in app_uuid:
                continue
            parent_no = (tmp_no or "").rsplit("/T", 1)[0]
            sub_uuid = None
            if patta and patta != "-" and patta in survey_by_patta:
                sub_uuid = survey_by_patta[patta][1]
            if sub_uuid is None:
                sub_uuid = subdiv_id.get((ward, block, survey, parent_no))
            if sub_uuid is None:
                sub_uuid = parent_subdiv.get(app_id)
            if sub_uuid is None:
                sub_uuid = first_subdiv.get((ward, block, survey))
            if sub_uuid is None:
                unresolved.append((app_id, tmp_no))
                continue
            parent_subdiv[app_id] = sub_uuid
            appsub_uuid = stable_id("application_sub_division", app_id, tmp_no)
            appsub_by_tmp[(app_id, tmp_no)] = appsub_uuid
            appsub_rows.append(dict(
                i=appsub_uuid, a=app_uuid[app_id], s=sub_uuid,
                ar=float(area or 0),
                tn=(tmp_no or None),
                # the number the parcel ends up with: the final one once the
                # sub-division is confirmed, the temporary one until then (a
                # rejected file never gets a final number).
                n=((new_no or "").strip() or tmp_no),
                st=_SUBDIV_STATUS.get(status_of.get(app_id, ""), "pending"),
                t=now))
        if unresolved:
            print(f"  WARNING: {len(unresolved)} temp sub-division parcels have no "
                  f"resolvable parent parcel: {unresolved[:5]}")
        if appsub_rows:
            cx.execute(text("""INSERT INTO application_sub_divisions
                (id,application_id,sub_division_id,proposed_area_sqm,
                 temporary_sub_division_no,proposed_sub_division_no,status,
                 created_at,updated_at)
                VALUES (:i,:a,:s,:ar,:tn,:n,:st,:t,:t)
                ON CONFLICT (id) DO UPDATE SET
                    application_id=EXCLUDED.application_id,
                    sub_division_id=EXCLUDED.sub_division_id,
                    proposed_area_sqm=EXCLUDED.proposed_area_sqm,
                    temporary_sub_division_no=EXCLUDED.temporary_sub_division_no,
                    proposed_sub_division_no=EXCLUDED.proposed_sub_division_no,
                    status=EXCLUDED.status, updated_at=EXCLUDED.updated_at"""),
                appsub_rows)
        print(f"application_sub_divisions: {len(appsub_rows)}")

        # An ISD file areg_temp_subdivclub has no row for at all -- typically
        # one still sitting at SIS, before SD has logged a temp/final split --
        # still carries a real subdivision number on appl_log_urban itself
        # (100% populated there for every service code, unlike this clubbing
        # extract). Without this, "Sub-Divisions" read "-" for a pending ISD
        # file the register already names a subdivision for.
        _covered = {a for (a, _t) in appsub_by_tmp}
        _fallback_rows = []
        for app_id, atype in type_of.items():
            if atype != "ISD" or app_id in _covered:
                continue
            no = raw_subdiv_of.get(app_id)
            if not no:
                continue
            wd, bl_app, sno = app_survey_key[app_id]
            sub_key = (wd, bl_app, sno, no)
            sub_uuid = subdiv_id.get(sub_key)
            if sub_uuid is None:
                sid = survey_id.get((wd, bl_app, sno))
                sub_uuid = stable_id("sub_division", wd, bl_app, sno, no)
                subdiv_id[sub_key] = sub_uuid
                cx.execute(text("""INSERT INTO sub_divisions
                    (id,survey_number_id,sub_division_no,area_sqm,status,
                     created_at,updated_at)
                    VALUES (:i,:s,:n,0.0,'active',:t,:t)
                    ON CONFLICT (id) DO UPDATE SET
                        survey_number_id=EXCLUDED.survey_number_id,
                        updated_at=EXCLUDED.updated_at"""),
                    dict(i=sub_uuid, s=sid, n=f"{sno}/{no}", t=now))
            appsub_uuid = stable_id("application_sub_division", app_id, no)
            _fallback_rows.append(dict(
                i=appsub_uuid, a=app_uuid[app_id], s=sub_uuid, ar=0.0,
                tn=no, n=no,
                st=_SUBDIV_STATUS.get(status_of.get(app_id, ""), "pending"),
                t=now))
        if _fallback_rows:
            cx.execute(text("""INSERT INTO application_sub_divisions
                (id,application_id,sub_division_id,proposed_area_sqm,
                 temporary_sub_division_no,proposed_sub_division_no,status,
                 created_at,updated_at)
                VALUES (:i,:a,:s,:ar,:tn,:n,:st,:t,:t)
                ON CONFLICT (id) DO UPDATE SET
                    application_id=EXCLUDED.application_id,
                    sub_division_id=EXCLUDED.sub_division_id,
                    proposed_area_sqm=EXCLUDED.proposed_area_sqm,
                    temporary_sub_division_no=EXCLUDED.temporary_sub_division_no,
                    proposed_sub_division_no=EXCLUDED.proposed_sub_division_no,
                    status=EXCLUDED.status, updated_at=EXCLUDED.updated_at"""),
                _fallback_rows)
        print(f"application_sub_divisions (fallback from appl_log_urban): {len(_fallback_rows)}")

        # ---------- proposed sub-division owners (ISD) ----------
        # chitta_temp_subdivclub_demo: the new owner(s) of each proposed
        # sub-division, linked by (application_id, temporary_subdivision_number).
        appsub_owner_rows = []
        appsub_owner_seq = defaultdict(int)
        _cts = "chitta_temp_subdivclub"
        _cts_cols = ["application_id", "temporary_subdivision_number", "owner_no",
                    "owner_name_english", "owner_name_tamil", "relationship",
                    "relative_name_english", "relative_name_tamil", "ownership_share",
                    "aadhaar_number", "gender"]
        for r in cx.execute(text(f"""
            SELECT {', '.join(newcol(_cts, c) for c in _cts_cols)}
            FROM {_cts}
            ORDER BY {newcol(_cts, 'application_id')}, {newcol(_cts, 'temporary_subdivision_number')}, {newcol(_cts, 'owner_no')}""")).all():
            (app_id, tmp_no, own_no, name_en, name_ta, rel, rel_en, rel_ta,
             share, aadhaar, gender) = r
            parent = appsub_by_tmp.get((app_id, tmp_no))
            if parent is None:
                continue  # sub-division row was not projected
            try:
                own_no_i = int(own_no) if own_no not in (None, "", "-") else None
            except (TypeError, ValueError):
                own_no_i = None
            appsub_owner_seq[(app_id, tmp_no)] += 1
            own_key = own_no_i if own_no_i is not None else f"seq{appsub_owner_seq[(app_id, tmp_no)]}"
            def _clean(v):
                v = (v or "").strip()
                return v if v and set(v) != {"."} and v not in ("-", "--", "N/A") else None
            appsub_owner_rows.append(dict(
                i=stable_id("application_sub_division_owner", app_id, tmp_no, own_key),
                p=parent, no=own_no_i,
                n=(_clean(name_en) or _clean(name_ta)),
                nt=_clean(name_ta),
                rt=_clean(rel),
                rn=(_clean(rel_en) or _clean(rel_ta)),
                sh=_clean(share),
                a4=(aadhaar or "")[-4:] or None,
                g=_clean(gender),
                t=now))
        if appsub_owner_rows:
            cx.execute(text("""INSERT INTO application_sub_division_owners
                (id,application_sub_division_id,owner_no,name,name_tamil,
                 relationship_type,relative_name,ownership_share,aadhaar_last4,
                 gender,created_at,updated_at)
                VALUES (:i,:p,:no,:n,:nt,:rt,:rn,:sh,:a4,:g,:t,:t)
                ON CONFLICT (id) DO UPDATE SET
                    application_sub_division_id=EXCLUDED.application_sub_division_id,
                    owner_no=EXCLUDED.owner_no, name=EXCLUDED.name,
                    name_tamil=EXCLUDED.name_tamil,
                    relationship_type=EXCLUDED.relationship_type,
                    relative_name=EXCLUDED.relative_name,
                    ownership_share=EXCLUDED.ownership_share,
                    aadhaar_last4=EXCLUDED.aadhaar_last4, gender=EXCLUDED.gender,
                    updated_at=EXCLUDED.updated_at"""),
                appsub_owner_rows)
        print(f"application_sub_division_owners: {len(appsub_owner_rows)}")

        # ---------- workflow history ----------
        chains = defaultdict(list)
        _awf_cols2 = ["application_id", "action_from_role_id", "action_to_role_id",
                     "action_date", "remarks", "updated_by_user", "recommendation_status",
                     "last_updated_datetime", "serial_number"]
        for r in cx.execute(text(f"""
            SELECT {', '.join(newcol(_awf, c) for c in _awf_cols2)}
            FROM {_awf} ORDER BY {newcol(_awf, 'application_id')}, {newcol(_awf, 'serial_number')}""")).all():
            if r[0] in app_uuid:
                chains[r[0]].append(r)

        wf_rows = []
        patched_rejections = 0
        for app_id, chain in chains.items():
            # recommendation_status "N" is carried forward down the chain once a
            # reviewer sets it -- every subsequent hop inherits the "N", so it is
            # NOT a per-hop rejection event. An application is rejected once, at
            # its closing hop. Marking every "N" hop REJECTED produced 3-4
            # duplicate "SIS -> REJECTED" rows per rejected file, some with
            # identical timestamps. So: the single rejecting hop is the last one
            # in the chain, and only when the projected status says rejected.
            rejecting = set()
            if status_of.get(app_id) == "rejected":
                rejecting = {len(chain) - 1}
                patched_rejections += 1

            prev_key = None  # collapse consecutive hops identical after the
                             # role->stage mapping (44->42 and 42->41 both = SIS->SIS)
            for n, r in enumerate(chain):
                rejected_hop = n in rejecting
                reason = r[4] if (r[4] and r[4] != "-") else reason_of.get(app_id)
                f_stage = ROLE_TO_STAGE.get(r[1])
                t_stage = "REJECTED" if rejected_hop else ROLE_TO_STAGE.get(r[2], "COMPLETED")
                action = "REJECTED" if rejected_hop else "Forwarded"
                key = (f_stage, t_stage, action, r[4])
                if key == prev_key and not rejected_hop:
                    continue
                prev_key = key
                wf_rows.append(dict(
                    i=stable_id("workflow_history", app_id, r[8]), a=app_uuid[app_id],
                    f=f_stage,
                    # A rejecting hop must land on the REJECTED stage: the
                    # rejection handlers select on to_stage == "REJECTED" (or an
                    # uppercase "REJECT" in action), so a role-derived stage here
                    # made the rejection invisible.
                    t2=t_stage,
                    ac=action,
                    o=officer_id.get(r[5]), rm=r[4],
                    rj=(reason or "Rejected") if rejected_hop else None,
                    p=performed_at(r),
                    t=now))
        if wf_rows:
            # Append-only history: a hop already recorded (same application +
            # serial_number) is never edited, only a genuinely new hop is
            # inserted -- so ON CONFLICT DO NOTHING rather than DO UPDATE.
            cx.execute(text("""INSERT INTO workflow_history
                (id,application_id,from_stage,to_stage,action,performed_by_officer_id,
                 remarks,rejection_reason,performed_at,created_at,updated_at)
                VALUES (:i,:a,:f,:t2,:ac,:o,:rm,:rj,:p,:t,:t)
                ON CONFLICT (id) DO NOTHING"""), wf_rows)
        print(f"workflow_history: {len(wf_rows)} "
              f"({patched_rejections} rejections read off the closing hop)")

        # ---------- real owners + patta transfers (from the extract's own owner files) ----------
        # Neither uareg nor uchitta_natham exist for this district, but three
        # OTHER files carry real owner/patta data for a subset of these
        # applications: chitta_temp_old_owner_demo.csv (the ISD parent
        # parcel's owner, before the split) and full_field_patta_transfer_
        # old_owner/new_owner_demo.csv (the NISD parcel's owner before/after
        # the transfer). Their own survey_no/block_code were cross-checked
        # against appl_log_urban's own columns for the same application --
        # zero mismatches across every application both sides cover -- so
        # they identify the SAME parcel the application's survey_number
        # already points at; only patta_number, owner name/relation/sex, and
        # (for NISD) the old/new owner pair get to be real here. extent/
        # share/aadhaar stay blank in every one of these three files -- a
        # genuine gap in the source, not a bug here -- so area and
        # ownership_share are still placeholders.
        _REL_CODE = {"4": "w/o", "5": "s/o", "6": "d/o"}

        def _clean_owner(v):
            v = (v or "").strip()
            return v if v and v not in ("-", "--", "N/A") else None

        def _survey_row_for(app_id):
            auuid = app_uuid.get(app_id)
            if auuid is None:
                return None
            return next((a for a in app_rows if a["i"] == auuid), None)

        real_owner_rows = []
        real_ownership_rows = []
        real_pt_rows = []
        patta_updates = []

        # ---- ISD: chitta_temp_old_owner (the parent parcel's owner) ----
        # Not in column_map.py's _OLD2NEW table -- it's a table this script
        # never read before, and its own CSV header already uses these exact
        # names (appl_id, owner_num, existing_patta_no, ...), so the columns
        # are read raw rather than through newcol()/sel().
        by_app_isd = defaultdict(list)
        for r in cx.execute(text("""
            SELECT appl_id, owner_name, owner_ename, relation_code,
                   existing_patta_no, owner_num, sex
            FROM chitta_temp_old_owner
            ORDER BY appl_id, owner_num""")).all():
            if r[0] in app_uuid:
                by_app_isd[r[0]].append(r)

        isd_owners_added = 0
        for app_id, rows_for_app in by_app_isd.items():
            row = _survey_row_for(app_id)
            if row is None:
                continue
            sid = row["s"]
            is_joint = len(rows_for_app) > 1
            first_patta = None
            for seq, (_aid, name_en, name_ta, rel_code, patta, own_no, sex) in enumerate(rows_for_app, 1):
                own_key = _clean_owner(own_no) or f"seq{seq}"
                oid = stable_id("owner", "isd_parent", app_id, own_key)
                _g = (sex or "").strip().upper()
                real_owner_rows.append(dict(
                    i=oid, n=(_clean_owner(name_en) or _clean_owner(name_ta)),
                    nt=_clean_owner(name_ta), f=None,
                    rt=_REL_CODE.get((rel_code or "").strip()),
                    a=None, m=None, ad=None,
                    g=(_g if _g in ("M", "F") else None), t=now))
                real_ownership_rows.append(dict(
                    i=stable_id("survey_ownership", "isd_parent", app_id, own_key),
                    s=sid, d=None, o=oid, p=(100.0 if not is_joint else None),
                    j=is_joint, ty="joint" if is_joint else "sole",
                    e=None, t=now))
                isd_owners_added += 1
                first_patta = first_patta or _clean_owner(patta)
            if first_patta:
                patta_updates.append((sid, first_patta))

        # ---- NISD: full_field_patta_transfer_old_owner / new_owner ----
        # Neither is in column_map.py's _OLD2NEW table either -- same reason
        # as chitta_temp_old_owner above -- so read raw column names.
        by_app_new = defaultdict(list)
        for r in cx.execute(text("""
            SELECT appl_id, owner_name, owner_ename, relation_code,
                   patta_no, owner_num, sex, mobile_number
            FROM full_field_patta_transfer_new_owner
            ORDER BY appl_id, owner_num""")).all():
            if r[0] in app_uuid:
                by_app_new[r[0]].append(r)
        by_app_old = defaultdict(list)
        for r in cx.execute(text("""
            SELECT appl_id, owner_name, owner_ename, relation_code,
                   patta_no, owner_num, sex
            FROM full_field_patta_transfer_old_owner
            ORDER BY appl_id, owner_num""")).all():
            if r[0] in app_uuid:
                by_app_old[r[0]].append(r)

        nisd_owners_added = 0
        first_new_oid, first_old_oid = {}, {}   # application_id -> owner uuid, for the transfer pairing below
        for app_id in sorted(set(by_app_new) | set(by_app_old)):
            row = _survey_row_for(app_id)
            if row is None:
                continue
            sid = row["s"]
            new_owners, old_owners = by_app_new.get(app_id, []), by_app_old.get(app_id, [])
            new_is_joint, old_is_joint = len(new_owners) > 1, len(old_owners) > 1
            # own_no is not always unique per application (one file's data has
            # the same application stamping two different owner rows both
            # owner_num=1), so the row's own position -- not own_no -- is the
            # disambiguator; own_no is still carried into the data, just not
            # used for identity.
            for seq, (_aid, name_en, name_ta, rel_code, patta, own_no, sex, mobile) in enumerate(new_owners, 1):
                oid = stable_id("owner", "nisd_new", app_id, seq)
                first_new_oid.setdefault(app_id, oid)
                _g = (sex or "").strip().upper()
                real_owner_rows.append(dict(
                    i=oid, n=(_clean_owner(name_en) or _clean_owner(name_ta)),
                    nt=_clean_owner(name_ta), f=None,
                    rt=_REL_CODE.get((rel_code or "").strip()),
                    a=None, m=_clean_owner(mobile), ad=None,
                    g=(_g if _g in ("M", "F") else None), t=now))
                real_ownership_rows.append(dict(
                    i=stable_id("survey_ownership", "nisd_new", app_id, seq),
                    s=sid, d=None, o=oid, p=(100.0 if not new_is_joint else None),
                    j=new_is_joint, ty="joint" if new_is_joint else "sole",
                    e=None, t=now))
                nisd_owners_added += 1
            for seq, (_aid, name_en, name_ta, rel_code, patta, own_no, sex) in enumerate(old_owners, 1):
                oid = stable_id("owner", "nisd_old", app_id, seq)
                first_old_oid.setdefault(app_id, oid)
                _g = (sex or "").strip().upper()
                real_owner_rows.append(dict(
                    i=oid, n=(_clean_owner(name_en) or _clean_owner(name_ta)),
                    nt=_clean_owner(name_ta), f=None,
                    rt=_REL_CODE.get((rel_code or "").strip()),
                    a=None, m=None, ad=None,
                    g=(_g if _g in ("M", "F") else None), t=now))
                nisd_owners_added += 1
            new_patta = _clean_owner(new_owners[0][4]) if new_owners else None
            if new_patta:
                patta_updates.append((sid, new_patta))

        # Applied now, before the detail file below -- that file's patta
        # number is the more authoritative one (it also knows the
        # transaction status, so it picks old vs. new correctly), so it must
        # be able to overwrite this cruder owner-file value, not the other
        # way around.
        for sid, patta in patta_updates:
            cx.execute(text("""UPDATE survey_numbers SET patta_number=:p, updated_at=:t
                WHERE id=:i"""), dict(p=patta, i=sid, t=now))

        # ---- NISD detail: full_field_patta_transfer_urban (real area, land
        # type, patta numbers, registration + order detail) ----
        # This is the SAME file `deed` above already reads for sale_deed_number/
        # declared_reason; here it also enriches the survey/parcel record and,
        # paired with the owner ids just built, produces a real patta_transfers
        # row wherever both an old and a new owner exist for that application.
        _ffu = "full_field_patta_transfer_urban"
        _ffu_cols = ["application_id", "survey_number", "subdivision_number",
                    "old_patta_number", "land_type_code", "extent_value_3",
                    "generated_patta_number", "transaction_status",
                    "transfer_reason", "registration_place", "registration_date",
                    "order_number", "order_date", "order_remarks",
                    "sis_recommendation", "sis_remarks", "sis_recommendation_reason",
                    "transfer_type", "direct_transfer_flag"]
        nisd_detail_enriched = 0
        for r in cx.execute(text(f"""
            SELECT {sel(_ffu, *_ffu_cols)}
            FROM {_ffu}""")).mappings().all():
            app_id = r["application_id"]
            if app_id not in app_uuid:
                continue
            row = _survey_row_for(app_id)
            if row is None:
                continue
            sid = row["s"]
            _extent = None
            try:
                _extent = float(r["extent_value_3"]) if r["extent_value_3"] not in (None, "") else None
            except (TypeError, ValueError):
                _extent = None
            _land = LAND_TYPE_MAP.get((r["land_type_code"] or "").strip())
            txn = (r["transaction_status"] or "").strip()
            cur_patta = (_clean_owner(r["generated_patta_number"]) if txn == "01"
                         else _clean_owner(r["old_patta_number"]))
            # cur_patta is populated in effectively every row (verified against
            # the extract), and this runs after the cruder owner-file pass
            # above, so COALESCE here means "prefer this authoritative value,
            # fall back to whatever that pass set" -- txn tells us whether the
            # old or new patta is the current one, which the owner files alone
            # cannot.
            cx.execute(text("""UPDATE survey_numbers SET
                    total_area_sqm=COALESCE(:a, total_area_sqm),
                    land_type=COALESCE(:l, land_type),
                    patta_number=COALESCE(:p, patta_number),
                    updated_at=:t
                WHERE id=:i"""),
                dict(a=_extent, l=_land, p=cur_patta, i=sid, t=now))
            nisd_detail_enriched += 1

            new_oid, old_oid = first_new_oid.get(app_id), first_old_oid.get(app_id)
            # previous_owner_id / new_owner_id are both NOT NULL on
            # patta_transfers -- a transfer needs both sides, so this row is
            # skipped (not fabricated) when only one side has an owner row.
            if not (new_oid and old_oid):
                continue
            if txn == "01":
                txn_status = "completed"
            elif txn.startswith("02"):
                txn_status = "rejected"
            else:
                txn_status = "pending"
            _dtf = (r["direct_transfer_flag"] or "").strip().upper()
            _tr_type = _clean_owner(r["transfer_type"])
            if not _tr_type and _dtf in ("Y", "1", "T"):
                _tr_type = "direct"
            real_pt_rows.append(dict(
                i=stable_id("patta_transfer", "real", app_id),
                a=app_uuid[app_id], s=sid, d=None,
                p=old_oid, n=new_oid, o=(_clean_owner(r["order_number"]) or f"{app_id}TR"),
                tr=r["order_date"] or r["registration_date"],
                np=_clean_owner(r["generated_patta_number"]),
                ts=None, sb=None, ds=(txn == "01"), st=txn_status, t=now,
                treason=_clean_owner(r["transfer_reason"]), ttype=_tr_type,
                rplace=_clean_owner(r["registration_place"]),
                rdate=r["registration_date"],
                oldp=_clean_owner(r["old_patta_number"]),
                ordno=_clean_owner(r["order_number"]) or f"{app_id}TR",
                orddt=r["order_date"],
                ordrem=_clean_owner(r["order_remarks"]),
                sisrec=_clean_owner(r["sis_recommendation"]),
                sisrem=_clean_owner(r["sis_remarks"]),
                sisreason=_clean_owner(r["sis_recommendation_reason"])))

        if real_owner_rows:
            cx.execute(text("""INSERT INTO owners
                (id,name,name_tamil,father_name,relationship_type,aadhaar_last4,
                 mobile,address,gender,created_at,updated_at)
                VALUES (:i,:n,:nt,:f,:rt,:a,:m,:ad,:g,:t,:t)
                ON CONFLICT (id) DO UPDATE SET
                    name=EXCLUDED.name, name_tamil=EXCLUDED.name_tamil,
                    relationship_type=EXCLUDED.relationship_type,
                    mobile=EXCLUDED.mobile, gender=EXCLUDED.gender,
                    updated_at=EXCLUDED.updated_at"""), real_owner_rows)
        if real_ownership_rows:
            cx.execute(text("""INSERT INTO survey_ownership
                (id,survey_number_id,sub_division_id,owner_id,ownership_share,
                 is_joint_owner,ownership_type,effective_from,created_at,updated_at)
                VALUES (:i,:s,:d,:o,:p,:j,:ty,:e,:t,:t)
                ON CONFLICT (id) DO UPDATE SET
                    ownership_share=EXCLUDED.ownership_share,
                    is_joint_owner=EXCLUDED.is_joint_owner,
                    ownership_type=EXCLUDED.ownership_type,
                    updated_at=EXCLUDED.updated_at"""), real_ownership_rows)
        if real_pt_rows:
            cx.execute(text("""INSERT INTO patta_transfers
                (id,application_id,survey_number_id,sub_division_id,previous_owner_id,
                 new_owner_id,transfer_order_number,new_patta_number,transfer_date,
                 tahsildar_signature_date,signed_by,dsc_applied,status,
                 transfer_reason,transfer_type,registration_place,registration_date,
                 old_patta_number,order_number,order_date,order_remarks,
                 sis_recommendation,sis_remarks,sis_recommendation_reason,
                 created_at,updated_at)
                VALUES (:i,:a,:s,:d,:p,:n,:o,:np,:tr,:ts,:sb,:ds,:st,
                 :treason,:ttype,:rplace,:rdate,:oldp,:ordno,:orddt,:ordrem,
                 :sisrec,:sisrem,:sisreason,:t,:t)
                ON CONFLICT (id) DO UPDATE SET
                    previous_owner_id=EXCLUDED.previous_owner_id,
                    new_owner_id=EXCLUDED.new_owner_id,
                    new_patta_number=EXCLUDED.new_patta_number,
                    old_patta_number=EXCLUDED.old_patta_number,
                    dsc_applied=EXCLUDED.dsc_applied, status=EXCLUDED.status,
                    updated_at=EXCLUDED.updated_at"""), real_pt_rows)
        print(f"real owners from the extract's own owner files: "
              f"{isd_owners_added} ISD (parent parcel), {nisd_owners_added} NISD "
              f"(old+new), survey_ownership: {len(real_ownership_rows)}, "
              f"patta numbers filled from owner files: {len(patta_updates)}, "
              f"NISD parcels enriched with real area/land_type/patta: {nisd_detail_enriched}, "
              f"patta_transfers (real): {len(real_pt_rows)}")

        # ---------- field visits ----------
        fv_rows = []
        for app_id, auuid in app_uuid.items():
            row = next((a for a in app_rows if a["i"] == auuid), None)
            if row is None or row["ty"] not in FIELD_VISIT_TYPES:
                continue
            visit = row["fv"]
            today = date.today()
            if visit is None:
                status_v = "unscheduled"
            elif visit > today:
                # Dated ahead of today, so it is booked, not done -- even when
                # the application itself has since been closed.
                status_v = "scheduled"
            elif row["cs"] in ("approved", "rejected"):
                status_v = "completed"
            elif row["ov"]:
                status_v = "overdue"
            else:
                status_v = "scheduled"
            fv_rows.append(dict(
                i=stable_id("field_visit", app_id), a=auuid, o=row["o"], s=visit,
                ac=visit if status_v == "completed" else None, st=status_v,
                n="Boundary and extent verified on site" if status_v == "completed" else None,
                e=False, en=None, av=status_v == "completed", t=now))
        if fv_rows:
            # One row per application (its current visit state), not an event
            # log -- unlike workflow_history this is expected to change as the
            # source data advances (unscheduled -> scheduled -> completed), so
            # it is kept in sync with DO UPDATE rather than frozen with DO
            # NOTHING. The deterministic id (application_id alone) already
            # rules out ever inserting a duplicate row for the same application.
            cx.execute(text("""INSERT INTO field_visits
                (id,application_id,officer_id,scheduled_date,actual_date,status,
                 visit_notes,encroachment_found,encroachment_notes,area_verified,
                 created_at,updated_at)
                VALUES (:i,:a,:o,:s,:ac,:st,:n,:e,:en,:av,:t,:t)
                ON CONFLICT (id) DO UPDATE SET
                    officer_id=EXCLUDED.officer_id,
                    scheduled_date=EXCLUDED.scheduled_date,
                    actual_date=EXCLUDED.actual_date, status=EXCLUDED.status,
                    visit_notes=EXCLUDED.visit_notes,
                    encroachment_found=EXCLUDED.encroachment_found,
                    encroachment_notes=EXCLUDED.encroachment_notes,
                    area_verified=EXCLUDED.area_verified,
                    updated_at=EXCLUDED.updated_at"""), fv_rows)
        print(f"field_visits: {len(fv_rows)}")

        # ---------- patta transfers ----------
        # There is no owner source in this district (owners/ownership_rows are
        # always empty here -- see the survey/owners block above), so
        # owner_by_survey stays empty and every candidate row below is
        # dropped by the `len(pool) < 2` check, same as it always was even
        # with Thoothukudi's owner data loaded. survey_by_patta is likewise
        # always empty, so `old_patta_number not in survey_by_patta` also
        # drops every row on its own -- both checks are kept rather than
        # short-circuited, so this section still runs (and still reports 0)
        # if a future extract ever adds an owner or patta-register source.
        pt_rows = []
        owner_by_survey = defaultdict(list)
        # No DSC signature source exists for this district either
        # (uchitta_nathammap_ds / uaregmap_ds were Thoothukudi-only).
        sig_by_app = {}

        # transfer_date is the NISD order date / the ISD registration date. The
        # ISD extract has no order_number/date/remarks or transfer_type column,
        # so those are selected as NULL for it.
        _clean = lambda v: (str(v).strip() or None) if v not in (None, "", "-") else None
        # sub_div_patta_transfer_urban (the ISD-side transfer source) is not
        # part of this extract, so only the NISD-side source is read.
        transfer_sources = [
            ("full_field_patta_transfer_urban", "order_date",
             ["order_number", "order_date", "order_remarks", "transfer_type",
              "direct_transfer_flag"]),
        ]
        _base_transfer_cols = ["application_id", "old_patta_number", "generated_patta_number",
                              "transaction_status", "transfer_reason", "registration_place",
                              "registration_date", "sis_recommendation", "sis_remarks",
                              "sis_recommendation_reason"]
        for table, date_col, extra_cols in transfer_sources:
            select_cols = sel(table, *_base_transfer_cols, date_col)
            if extra_cols:
                select_cols += ", " + sel(table, *extra_cols)
            else:
                select_cols += (", NULL AS order_number, NULL AS order_date, "
                               "NULL AS order_remarks, NULL AS transfer_type, "
                               "NULL AS direct_transfer_flag")
            for r in cx.execute(text(f"""
                SELECT {select_cols}
                FROM {table}""")).mappings().all():
                if r["application_id"] not in app_uuid or r["old_patta_number"] not in survey_by_patta:
                    continue
                sid, sub_uuid, _wd, _sno = survey_by_patta[r["old_patta_number"]]
                pool = owner_by_survey.get(sid) or []
                if len(pool) < 2:
                    continue
                # transaction_status is "01" when the transfer went through and
                # "02/NN" when it was refused, NN being the reason code (03, 15,
                # 06, ...). Reading everything that is not "01" as still pending
                # told the officer that 60 refused transfers were in flight.
                txn = (r["transaction_status"] or "").strip()
                if txn == "01":
                    txn_status = "completed"
                elif txn.startswith("02"):
                    txn_status = "rejected"
                else:
                    txn_status = "pending"      # no status yet -- still moving
                _newp = (str(r["generated_patta_number"]).strip()
                         if r["generated_patta_number"] is not None else "")
                _sig_dt, _sig_by = sig_by_app.get(r["application_id"], (None, None))
                _sig_date = _sig_dt.date() if hasattr(_sig_dt, "date") else _sig_dt
                _tr_type = _clean(r["transfer_type"])
                _dtf = (r["direct_transfer_flag"] or "").strip().upper()
                if not _tr_type and _dtf in ("Y", "1", "T"):
                    _tr_type = "direct"
                _tdate = r[date_col]      # NISD: order_date; ISD: registration_date
                pt_rows.append(dict(
                    i=stable_id("patta_transfer", table, r["application_id"],
                                r["old_patta_number"]),
                    a=app_uuid[r["application_id"]], s=sid, d=sub_uuid,
                    p=pool[0], n=pool[-1], o=f"{r['application_id']}TR",
                    tr=_tdate,
                    np=(_newp if _newp and _newp not in ("-", "0") else None),
                    ts=(_sig_date or _tdate),
                    sb=_sig_by,
                    ds=(txn == "01" or _sig_dt is not None),
                    st=txn_status, t=now,
                    treason=_clean(r["transfer_reason"]),
                    ttype=_tr_type,
                    rplace=_clean(r["registration_place"]),
                    rdate=r["registration_date"],
                    oldp=_clean(r["old_patta_number"]),
                    ordno=_clean(r["order_number"]) or f"{r['application_id']}TR",
                    orddt=r["order_date"],
                    ordrem=_clean(r["order_remarks"]),
                    sisrec=_clean(r["sis_recommendation"]),
                    sisrem=_clean(r["sis_remarks"]),
                    sisreason=_clean(r["sis_recommendation_reason"]),
                ))
        if pt_rows:
            cx.execute(text("""INSERT INTO patta_transfers
                (id,application_id,survey_number_id,sub_division_id,previous_owner_id,
                 new_owner_id,transfer_order_number,new_patta_number,transfer_date,
                 tahsildar_signature_date,signed_by,dsc_applied,status,
                 transfer_reason,transfer_type,registration_place,registration_date,
                 old_patta_number,order_number,order_date,order_remarks,
                 sis_recommendation,sis_remarks,sis_recommendation_reason,
                 created_at,updated_at)
                VALUES (:i,:a,:s,:d,:p,:n,:o,:np,:tr,:ts,:sb,:ds,:st,
                 :treason,:ttype,:rplace,:rdate,:oldp,:ordno,:orddt,:ordrem,
                 :sisrec,:sisrem,:sisreason,:t,:t)
                ON CONFLICT (id) DO UPDATE SET
                    application_id=EXCLUDED.application_id,
                    survey_number_id=EXCLUDED.survey_number_id,
                    sub_division_id=EXCLUDED.sub_division_id,
                    previous_owner_id=EXCLUDED.previous_owner_id,
                    new_owner_id=EXCLUDED.new_owner_id,
                    transfer_order_number=EXCLUDED.transfer_order_number,
                    new_patta_number=EXCLUDED.new_patta_number,
                    transfer_date=EXCLUDED.transfer_date,
                    tahsildar_signature_date=EXCLUDED.tahsildar_signature_date,
                    signed_by=EXCLUDED.signed_by, dsc_applied=EXCLUDED.dsc_applied,
                    status=EXCLUDED.status, transfer_reason=EXCLUDED.transfer_reason,
                    transfer_type=EXCLUDED.transfer_type,
                    registration_place=EXCLUDED.registration_place,
                    registration_date=EXCLUDED.registration_date,
                    old_patta_number=EXCLUDED.old_patta_number,
                    order_number=EXCLUDED.order_number, order_date=EXCLUDED.order_date,
                    order_remarks=EXCLUDED.order_remarks,
                    sis_recommendation=EXCLUDED.sis_recommendation,
                    sis_remarks=EXCLUDED.sis_remarks,
                    sis_recommendation_reason=EXCLUDED.sis_recommendation_reason,
                    updated_at=EXCLUDED.updated_at"""), pt_rows)
        print(f"patta_transfers: {len(pt_rows)}")

    print("\ndone -- app tables built inside SISchatbot")


if __name__ == "__main__":
    main()
