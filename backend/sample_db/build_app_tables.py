"""
Build the application's ORM tables inside sis_chatbot_db from the sample tables.

The chatbot answers through backend/services/postgres.py and backend/services/
chatbot.py, which query the ORM models in backend/models.py (applications,
survey_numbers, owners, field_visits, ...). This script derives those tables
from the 16 CSV-shaped tables seeded by seed_sample_db.py, so the answers the
chatbot gives come from the sample data.

Both live in sis_chatbot_db: the CSV-shaped tables are the source of record,
the ORM tables are a derived projection. Re-running rebuilds the projection.

Run from the project root:
    python -m backend.sample_db.build_app_tables
"""
from __future__ import annotations

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
from backend.services.auth_service import get_password_hash

DB_URL = database_url().replace("+asyncpg", "+psycopg2")

DEFAULT_PASSWORD = "Test@1234"

# service_code -> application_type. The Application model only admits these
# three (ck_application_type), so other services stay in the CSV-shaped tables
# and are not projected.
SERVICE_TO_TYPE = {"0153": "NISD", "0154": "ISD", "0155": "MERGE"}

# Only the sub-division chain includes a mandatory field inspection. NISD is
# document verification at the SIS desk and then straight to the Zonal Level
# Tahsildar -- no visit is scheduled, so no visit date may be claimed for one.
FIELD_VISIT_TYPES = {"ISD", "MERGE"}

# What urban_application_log.application_status means. The transfer extracts
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

# land_type_code in the parcel register -> the model's free-text land_type.
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
    "MERGE": ["Sale Deed", "Survey Sketch", "Photo ID"],
}

# The ORM tables this script owns -- rebuilt from scratch on every run.
# `sis_officers` is deliberately NOT here (see `_OFFICER_NS` below): its rows
# are upserted in place, at a deterministic id, instead of being dropped and
# reinserted. That is what keeps a logged-in officer's JWT (which names their
# `sis_officers.id`) and `chat_sessions` row valid across a rebuild -- a
# `TRUNCATE sis_officers CASCADE` used to take chat_sessions / chat_messages /
# audit_logs / chat_attachments down with it every single time, which is what
# forced a re-login on every edit once rebuilds started firing automatically
# (see `watch_rebuild.py`). `notifications` still needs truncating: besides
# `officer_id` it also carries `application_id`, and `applications` itself has
# no stable identity across a rebuild, so its rows would dangle otherwise.
# `officer_jurisdictions` is still fully rebuilt too -- nothing references it,
# so doing so costs nothing and its ward assignments are meant to be
# recomputed. knowledge_embeddings is NOT touched: the document embeddings are
# expensive to rebuild and do not depend on any of this.
OWNED_TABLES = [
    "patta_transfers", "field_visits", "workflow_history",
    "application_sub_division_owners",
    "application_documents", "application_sub_divisions", "applications",
    "applicants", "survey_ownership", "owners", "sub_divisions",
    "survey_numbers", "officer_jurisdictions",
    "notifications",
    # districts and taluks are NOT here: they are now the TAMILNILAM master
    # tables (district_unicode / taluk), which this script reads and must
    # never truncate. See backend/sample_db/adopt_master_district_taluk.py.
    "blocks", "wards", "towns",
]

# A deterministic officer id (uuid5 of the workflow username, same trick as
# `district_unicode.app_uid` / `taluk.app_uid`) so a JWT issued before a
# rebuild still resolves after one -- `sis_officers` is upserted, never
# truncated, specifically so that identity survives.
_OFFICER_NS = uuid.UUID("6c1f9b2a-0000-4000-8000-000000000001")


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


def performed_at(action) -> datetime:
    """When a workflow hop happened, to the second.

    A file often clears three desks in one day, so action_date alone makes the
    hops simultaneous and the history loses its order -- which then reads as
    the file bouncing backwards. last_updated_datetime carries the real time;
    where it is missing the serial number keeps the day's hops in sequence.
    """
    stamp, day, serial = action[7], action[3], action[8]
    if stamp is not None:
        return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)
    return (datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
            + timedelta(seconds=int(serial or 0)))


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
        ):
            cx.execute(text(ddl))
    print("app tables created/verified")

    with engine.begin() as cx:
        cx.execute(text("TRUNCATE " + ", ".join(OWNED_TABLES) + " CASCADE"))
        print(f"cleared {len(OWNED_TABLES)} app tables")

        # ---------- geography ----------
        rows = cx.execute(text("""
            SELECT DISTINCT district_code, taluk_code, town_code, ward_code, block_code
            FROM urban_parcel_register ORDER BY 1,2,3,4,5""")).all()

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
                town_id[(dc, tk, tw)] = uuid.uuid4()
                cx.execute(text("""INSERT INTO towns (id,taluk_id,name,town_code,created_at,updated_at)
                    VALUES (:i,:k,:n,:c,:t,:t)"""),
                    dict(i=town_id[(dc, tk, tw)], k=taluk_id[(dc, tk)],
                         n=_name("town", (dc, tk, tw),
                                 DISTRICT_CODE_MAP.get(dc, "Town")),
                         c=tw, t=now))
            if (dc, tk, tw, wd) not in ward_id:
                ward_id[(dc, tk, tw, wd)] = uuid.uuid4()
                cx.execute(text("""INSERT INTO wards (id,town_id,ward_number,ward_name,created_at,updated_at)
                    VALUES (:i,:o,:n,:w,:t,:t)"""),
                    dict(i=ward_id[(dc, tk, tw, wd)], o=town_id[(dc, tk, tw)],
                         n=wd,
                         w=_name("ward", (dc, tk, tw, wd), f"Ward {int(wd)}"),
                         t=now))
            key = (dc, tk, tw, wd, bl)
            if key not in block_id:
                block_id[key] = uuid.uuid4()
                cx.execute(text("""INSERT INTO blocks (id,ward_id,block_number,block_name,created_at,updated_at)
                    VALUES (:i,:w,:n,:b,:t,:t)"""),
                    dict(i=block_id[key], w=ward_id[(dc, tk, tw, wd)],
                         n=bl,
                         b=_name("block", key, f"Block {int(bl)}"),
                         t=now))
        print(f"geography: {len(district_id)} district, {len(taluk_id)} taluk, "
              f"{len(town_id)} town, {len(ward_id)} ward, {len(block_id)} block")
        print(f"  district + taluk read from the master tables "
              f"(district_unicode, taluk) -- not rebuilt")
        _totals = {"town": len(town_id), "ward": len(ward_id), "block": len(block_id)}
        if any(master.get(lvl) for lvl in _totals):
            print("  names from the master tables: " + ", ".join(
                f"{lvl} {from_master[lvl]}/{total}" for lvl, total in _totals.items()))
        else:
            print("  master tables not loaded -- names synthesised as before "
                  "(run: python -m backend.sample_db.load_master_dumps)")

        # ---------- survey numbers and sub-divisions ----------
        parcels = cx.execute(text("""
            SELECT district_code, taluk_code, town_code, ward_code, block_code,
                   survey_number, subdivision_number, patta_number,
                   land_type_code, extent_value_3, remarks
            FROM urban_parcel_register""")).all()

        by_survey = defaultdict(list)
        for p in parcels:
            by_survey[(p[0], p[1], p[2], p[3], p[4], p[5])].append(p)

        survey_id = {}          # (ward_code, survey_no) -> uuid
        survey_by_patta = {}    # patta -> (survey uuid, subdiv uuid)
        subdiv_id = {}          # (ward_code, survey_no, subdiv_no) -> subdiv uuid
        first_subdiv = {}       # (ward_code, survey_no) -> subdiv uuid
        subdiv_rows = []
        survey_rows = []
        for key, group in by_survey.items():
            dc, tk, tw, wd, bl, sno = key
            sid = uuid.uuid4()
            survey_id[(wd, sno)] = sid
            total = sum(float(g[9] or 0) for g in group)
            first = group[0]
            survey_rows.append(dict(
                i=sid, b=block_id[(dc, tk, tw, wd, bl)], s=sno, a=round(total, 2),
                l=LAND_TYPE_MAP.get(first[8], "residential"), p=first[7],
                e=False, g=False, r=None, t=now))
            for g in group:
                sub_uuid = uuid.uuid4()
                subdiv_rows.append(dict(
                    i=sub_uuid, s=sid, n=f"{sno}/{g[6]}",
                    a=round(float(g[9] or 0), 2), st="active", t=now))
                survey_by_patta[g[7]] = (sid, sub_uuid, wd, sno)
                subdiv_id[(wd, sno, g[6])] = sub_uuid
                first_subdiv.setdefault((wd, sno), sub_uuid)

        cx.execute(text("""INSERT INTO survey_numbers
            (id,block_id,survey_no,total_area_sqm,land_type,patta_number,
             has_encroachment,has_litigation,litigation_reference,created_at,updated_at)
            VALUES (:i,:b,:s,:a,:l,:p,:e,:g,:r,:t,:t)"""), survey_rows)
        cx.execute(text("""INSERT INTO sub_divisions
            (id,survey_number_id,sub_division_no,area_sqm,status,created_at,updated_at)
            VALUES (:i,:s,:n,:a,:st,:t,:t)"""), subdiv_rows)
        print(f"survey_numbers: {len(survey_rows)}, sub_divisions: {len(subdiv_rows)}")

        # ---------- owners ----------
        owner_rows = []
        ownership_rows = []
        owners = cx.execute(text("""
            SELECT patta_number, owner_name_english, owner_name_tamil,
                   relative_name_english, relative_name_tamil,
                   aadhaar_number, ownership_share, own_num, address, sex,
                   relationship_code
            FROM urban_natham_chitta_owner""")).all()
        # relationship_code -> how father_name relates to the owner. Read off
        # the sibling extract chitta_temp_subdivclub_demo, which carries BOTH
        # the code and the Tamil word: 5=மகன் (son), 4=மனைவி (wife), 6=மகள்
        # (daughter). Code 0 (the bulk) is left unspecified in the extract.
        _REL_CODE = {"4": "w/o", "5": "s/o", "6": "d/o"}
        # Joint ownership is a property of the patta, not of a single owner row:
        # if a patta carries more than one owner, every one of them is a joint
        # holder. own_num only sequences them (1, 2, 3 ...) -- keying the flag on
        # own_num > 1 wrongly labelled owner #1 of an N-way holding "sole".
        owners_per_patta = Counter(
            patta for (patta, *_rest) in owners if patta in survey_by_patta)
        for (patta, name_en, name_ta, rel_en, rel_ta, aadhaar, share, own_num,
             addr, sex, rel_code) in owners:
            if patta not in survey_by_patta:
                continue
            sid, sub_uuid, _wd, _sno = survey_by_patta[patta]
            oid = uuid.uuid4()
            # The urban natham-chitta extract for this jurisdiction carries only
            # the TAMIL name / relative name (the *_english columns are blank),
            # so fall back to the Tamil string rather than storing NULL and
            # sending "who is the owner / their father" to the LLM. `sex` ->
            # gender (only M/F kept; the extract's blank and '-' become NULL),
            # relationship_code -> relationship_type, `address` verbatim --
            # mostly blank in the extract, but shown where present.
            _g = (sex or "").strip().upper()
            owner_rows.append(dict(
                i=oid, n=(name_en or name_ta), nt=name_ta,
                f=((rel_en or rel_ta) or None),
                rt=_REL_CODE.get((rel_code or "").strip()),
                a=(aadhaar or "")[-4:] or None, m=None,
                ad=((addr or "").strip() or None),
                g=(_g if _g in ("M", "F") else None), t=now))
            # "1/2" -> 50.00
            pct = 100.0
            if share and "/" in share:
                try:
                    num, den = share.split("/")
                    pct = round(100.0 * int(num) / int(den), 2)
                except (ValueError, ZeroDivisionError):
                    pct = 100.0
            is_joint = owners_per_patta[patta] > 1
            ownership_rows.append(dict(
                i=uuid.uuid4(), s=sid, d=sub_uuid, o=oid, p=pct,
                j=is_joint, ty="joint" if is_joint else "sole",
                e=None, t=now))
        cx.execute(text("""INSERT INTO owners
            (id,name,name_tamil,father_name,relationship_type,aadhaar_last4,
             mobile,address,gender,created_at,updated_at)
            VALUES (:i,:n,:nt,:f,:rt,:a,:m,:ad,:g,:t,:t)"""), owner_rows)
        cx.execute(text("""INSERT INTO survey_ownership
            (id,survey_number_id,sub_division_id,owner_id,ownership_share,
             is_joint_owner,ownership_type,effective_from,created_at,updated_at)
            VALUES (:i,:s,:d,:o,:p,:j,:ty,:e,:t,:t)"""), ownership_rows)
        print(f"owners: {len(owner_rows)}, survey_ownership: {len(ownership_rows)}")

        # ---------- officers ----------
        # The SIS officers are the usernames that open the workflow chain
        # (role 41).
        sis_users = [r[0] for r in cx.execute(text("""
            SELECT DISTINCT updated_by_user FROM application_workflow_action
            WHERE action_from_role_id = '41' ORDER BY 1""")).all()]
        wards_sorted = sorted(ward_id.items(), key=lambda kv: kv[0][3])

        # An officer must hold the wards the applications actually sit in --
        # otherwise the file is assigned to someone whose jurisdiction filter
        # then hides it, and they cannot open their own ward's application.
        # The parcel register covers more wards than the application log does
        # (ward 004 has parcels but no applications), so only the wards that
        # carry applications are shared out, and an officer can hold more than
        # one when there are fewer officers than wards.
        app_wards = {r[0] for r in cx.execute(text("""
            SELECT DISTINCT ward_code FROM urban_application_log
            WHERE service_code IN ('0153','0154','0155')""")).all()}
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
                    VALUES (:i,:o,'ward',:d,:k,:w2,:w,NULL,:t,:t)"""),
                    dict(i=uuid.uuid4(), o=oid, d=district_id[dc], k=taluk_id[(dc, tk)],
                         w2=town_id[(dc, tk, tw)], w=wuuid, t=now))
        # a ward with no applications still has to resolve to somebody
        for wkey, _w in wards_sorted:
            officer_for_ward.setdefault(wkey[3], next(iter(officer_id.values())))
        print(f"sis_officers: {len(officer_id)} (password '{DEFAULT_PASSWORD}'), "
              f"wards held: "
              + ", ".join(f"{w}" for w in sorted(officer_for_ward)))

        # ---------- applicants + applications ----------
        # Common columns across both application-info extracts (isd_ lacks
        # `occupation`); selected by name so each row is a dict.
        info = {}
        _INFO_COLS = ["applicant_name", "mobile_number", "current_address",
                      "application_status", "permanent_address", "father_name",
                      "mother_name", "date_of_birth", "gender",
                      "challan_number", "payment_mode", "payment_amount",
                      "igrs_form6_number"]
        for table in ("nisd_transfer_application_info", "isd_transfer_application_info"):
            is_isd = table == "isd_transfer_application_info"
            cols = list(_INFO_COLS)
            if not is_isd:
                cols.append("occupation")           # nisd_ only
            else:
                cols.append("merged_application_id")  # isd_ only
            for r in cx.execute(text(
                    f"SELECT application_id, {', '.join(cols)} FROM {table}")).mappings():
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
        for r in cx.execute(text("""
            SELECT application_id, igrs_form6_number
            FROM nisd_transfer_igrs_owner WHERE igrs_form6_number IS NOT NULL""")).all():
            igrs_f6.setdefault(r[0], r[1])

        # The registration document and the reason for transfer live in the
        # detail extracts; without them the model's sale_deed_number and
        # declared_reason stayed NULL and the bot answered "I don't have that
        # information" for sale-deed and sub-registrar questions.
        deed = {}
        for table in ("nisd_transfer_urban_detail", "isd_transfer_urban_detail"):
            for r in cx.execute(text(f"""
                SELECT application_id, registration_document_number, transfer_reason,
                       registration_place, registration_date
                FROM {table}""")).all():
                if r[1]:
                    deed[r[0]] = r

        # An application that covers several parcels has one urban_application_log
        # row per parcel (1211 rows over 1139 application_ids in the extracts),
        # and the rows can disagree on status as the file moves. `applications`
        # holds one row per application, so keep the most recently updated one --
        # that is the application's current state. The other parcels are still
        # reachable through the sub-division and transfer-detail tables.
        apps = cx.execute(text("""
            SELECT * FROM (
                SELECT DISTINCT ON (application_id)
                       application_id, service_code, ward_code, survey_number,
                       application_date, application_status, can_number,
                       source_code, source_name, igrs_form6_number,
                       igrs_auto_mutation_flag, camp_flag, ip_address
                FROM urban_application_log
                WHERE service_code IN ('0153','0154','0155')
                ORDER BY application_id,
                         last_updated_datetime DESC NULLS LAST,
                         application_date DESC
            ) latest
            ORDER BY application_date""")).all()

        # last workflow action per application -> stage, and field visit date
        last_action = {}
        last_hop = {}
        fv_date = {}
        for r in cx.execute(text("""
            SELECT application_id, action_to_role_id, action_date, field_visit_date,
                   serial_number
            FROM application_workflow_action ORDER BY application_id, serial_number""")).all():
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

        can_repaired = can_dropped = 0
        for (app_id, svc, wd, sno, sub_date, status, can,
             _src, src_name, form6, _auto_mut, camp_flag, ip_addr) in apps:
            key = (wd, sno)
            if key not in survey_id:
                skipped_no_survey += 1
                continue
            atype = SERVICE_TO_TYPE[svc]
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

            aid = uuid.uuid4()
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
            else:
                la = last_hop.get(app_id)
                stage = ROLE_TO_STAGE.get(la[1], "SIS") if la else "SIS"

            # application_workflow_action carries a field_visit_date on NISD rows
            # too, but NISD has no field visit -- projecting it made 167 of 168
            # NISD applications report a scheduled visit that no field_visits row
            # backs, and the answer quoted a date the workflow never produced.
            visit = fv_date.get(app_id) if atype in FIELD_VISIT_TYPES else None
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

            auuid = uuid.uuid4()
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
            app_rows.append(dict(
                i=auuid, num=app_id, ty=atype, ap=aid, s=survey_id[key],
                o=officer_for_ward[wd], ch=channel, d=sub_date,
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
                    i=uuid.uuid4(), a=auuid, ty=doc, n=f"{doc} - {app_id}",
                    u=cur_status != "pending", v=cur_status in ("approved", "in_progress"),
                    ua=now if cur_status != "pending" else None, t=now))

        cx.execute(text("""INSERT INTO applicants
            (id,name,mobile,aadhaar_last4,address,permanent_address,father_name,
             mother_name,date_of_birth,gender,occupation,created_at,updated_at)
            VALUES (:i,:n,:m,:a,:ad,:pad,:fn,:mn,:dob,:g,:occ,:t,:t)"""), applicant_rows)
        cx.execute(text("""INSERT INTO applications
            (id,application_number,application_type,applicant_id,survey_number_id,
             assigned_officer_id,submission_channel,submission_source_name,
             submission_ip,submission_camp_flag,submission_date,sale_deed_number,
             sale_deed_registered,declared_reason,can_number,current_stage,
             current_status,field_visit_date,field_visit_scheduled,is_overdue,
             priority_flag,notes,fee_amount,challan_number,payment_mode,
             igrs_form6_number,merged_application_id,created_at,updated_at)
            VALUES (:i,:num,:ty,:ap,:s,:o,:ch,:src,:ip,:camp,:d,:sd,:sr,:dr,:can,:st,:cs,:fv,:fs,
                    :ov,:pr,:no,:fee,:chn,:pm,:f6,:mrg,:t,:t)"""), app_rows)
        cx.execute(text("""INSERT INTO application_documents
            (id,application_id,document_type,document_name,is_uploaded,is_verified,
             uploaded_at,created_at,updated_at)
            VALUES (:i,:a,:ty,:n,:u,:v,:ua,:t,:t)"""), doc_rows)
        print(f"applicants: {len(applicant_rows)}, applications: {len(app_rows)} "
              f"(skipped {skipped_no_survey} with no matching survey), "
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
        #      of that ward+survey,
        #   3. the parent already resolved for an earlier row of the same file,
        #   4. any sub-division of that ward+survey -- the parcel is at least
        #      identified that far, and dropping the row loses it entirely.
        _SUBDIV_STATUS = {"approved": "approved", "rejected": "rejected",
                          "in_progress": "in_progress"}
        parent_subdiv = {}   # application_id -> parent sub_division uuid
        appsub_by_tmp = {}   # (application_id, temporary_subdivision_number) -> appsub uuid
        unresolved = []
        for r in cx.execute(text("""
            SELECT application_id, temporary_subdivision_number,
                   new_subdivision_number, area_square_meter, existing_patta_number,
                   ward_code, survey_number, row_id
            FROM urban_temp_subdivision_parcel
            ORDER BY application_id, row_id""")).all():
            app_id, tmp_no, new_no, area, patta, ward, survey, _rid = r
            if app_id not in app_uuid:
                continue
            parent_no = (tmp_no or "").rsplit("/T", 1)[0]
            sub_uuid = None
            if patta and patta != "-" and patta in survey_by_patta:
                sub_uuid = survey_by_patta[patta][1]
            if sub_uuid is None:
                sub_uuid = subdiv_id.get((ward, survey, parent_no))
            if sub_uuid is None:
                sub_uuid = parent_subdiv.get(app_id)
            if sub_uuid is None:
                sub_uuid = first_subdiv.get((ward, survey))
            if sub_uuid is None:
                unresolved.append((app_id, tmp_no))
                continue
            parent_subdiv[app_id] = sub_uuid
            appsub_uuid = uuid.uuid4()
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
                  f"parent parcel in urban_parcel_register: {unresolved[:5]}")
        if appsub_rows:
            cx.execute(text("""INSERT INTO application_sub_divisions
                (id,application_id,sub_division_id,proposed_area_sqm,
                 temporary_sub_division_no,proposed_sub_division_no,status,
                 created_at,updated_at)
                VALUES (:i,:a,:s,:ar,:tn,:n,:st,:t,:t)"""), appsub_rows)
        print(f"application_sub_divisions: {len(appsub_rows)}")

        # ---------- proposed sub-division owners (ISD) ----------
        # urban_temp_subdivision_owner: the new owner(s) of each proposed
        # sub-division, linked by (application_id, temporary_subdivision_number).
        appsub_owner_rows = []
        for r in cx.execute(text("""
            SELECT application_id, temporary_subdivision_number, owner_no,
                   owner_name_english, owner_name_tamil, relationship,
                   relative_name_english, relative_name_tamil, ownership_share,
                   aadhaar_number, gender
            FROM urban_temp_subdivision_owner
            ORDER BY application_id, temporary_subdivision_number, owner_no""")).all():
            (app_id, tmp_no, own_no, name_en, name_ta, rel, rel_en, rel_ta,
             share, aadhaar, gender) = r
            parent = appsub_by_tmp.get((app_id, tmp_no))
            if parent is None:
                continue  # sub-division row was not projected
            try:
                own_no_i = int(own_no) if own_no not in (None, "", "-") else None
            except (TypeError, ValueError):
                own_no_i = None
            def _clean(v):
                v = (v or "").strip()
                return v if v and set(v) != {"."} and v not in ("-", "--", "N/A") else None
            appsub_owner_rows.append(dict(
                i=uuid.uuid4(), p=parent, no=own_no_i,
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
                VALUES (:i,:p,:no,:n,:nt,:rt,:rn,:sh,:a4,:g,:t,:t)"""),
                appsub_owner_rows)
        print(f"application_sub_division_owners: {len(appsub_owner_rows)}")

        # ---------- workflow history ----------
        chains = defaultdict(list)
        for r in cx.execute(text("""
            SELECT application_id, action_from_role_id, action_to_role_id,
                   action_date, remarks, updated_by_user, recommendation_status,
                   last_updated_datetime, serial_number
            FROM application_workflow_action ORDER BY application_id, serial_number""")).all():
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
                    i=uuid.uuid4(), a=app_uuid[app_id],
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
        cx.execute(text("""INSERT INTO workflow_history
            (id,application_id,from_stage,to_stage,action,performed_by_officer_id,
             remarks,rejection_reason,performed_at,created_at,updated_at)
            VALUES (:i,:a,:f,:t2,:ac,:o,:rm,:rj,:p,:t,:t)"""), wf_rows)
        print(f"workflow_history: {len(wf_rows)} "
              f"({patched_rejections} rejections read off the closing hop)")

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
                i=uuid.uuid4(), a=auuid, o=row["o"], s=visit,
                ac=visit if status_v == "completed" else None, st=status_v,
                n="Boundary and extent verified on site" if status_v == "completed" else None,
                e=False, en=None, av=status_v == "completed", t=now))
        if fv_rows:
            cx.execute(text("""INSERT INTO field_visits
                (id,application_id,officer_id,scheduled_date,actual_date,status,
                 visit_notes,encroachment_found,encroachment_notes,area_verified,
                 created_at,updated_at)
                VALUES (:i,:a,:o,:s,:ac,:st,:n,:e,:en,:av,:t,:t)"""), fv_rows)
        print(f"field_visits: {len(fv_rows)}")

        # ---------- patta transfers ----------
        pt_rows = []
        owner_by_survey = defaultdict(list)
        for o in ownership_rows:
            owner_by_survey[o["s"]].append(o["o"])
        # DSC signature: when the patta order was digitally signed and by whom.
        # Keyed on form6_number (an application id); the natham-chitta signature
        # table covers NISD, the parcel one ISD/settlement. verified_datetime /
        # username_verify / nic_dsign_flag are all NULL in the extracts.
        sig_by_app = {}
        for tbl, user_col in (("urban_natham_chitta_signature", "signed_by_username"),
                              ("urban_parcel_signature", "username")):
            for sr in cx.execute(text(
                    f"SELECT form6_number, signed_datetime, {user_col} "
                    f"FROM {tbl} WHERE form6_number IS NOT NULL "
                    f"ORDER BY signed_datetime")).all():
                sig_by_app[sr[0]] = (sr[1], sr[2])   # last (latest) wins

        # transfer_date is the NISD order date / the ISD registration date. The
        # ISD extract has no order_number/date/remarks or transfer_type column,
        # so those are selected as NULL for it.
        _clean = lambda v: (str(v).strip() or None) if v not in (None, "", "-") else None
        transfer_sources = [
            ("nisd_transfer_urban_detail", "order_date",
             "order_number, order_date, order_remarks, transfer_type, direct_transfer_flag"),
            ("isd_transfer_urban_detail", "registration_date",
             "NULL AS order_number, NULL AS order_date, NULL AS order_remarks, "
             "NULL AS transfer_type, NULL AS direct_transfer_flag"),
        ]
        for table, date_col, extra_cols in transfer_sources:
            for r in cx.execute(text(f"""
                SELECT application_id, old_patta_number, generated_patta_number,
                       {date_col}, transaction_status,
                       transfer_reason, registration_place, registration_date,
                       sis_recommendation, sis_remarks, sis_recommendation_reason,
                       {extra_cols}
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
                    i=uuid.uuid4(), a=app_uuid[r["application_id"]], s=sid, d=sub_uuid,
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
                 :sisrec,:sisrem,:sisreason,:t,:t)"""), pt_rows)
        print(f"patta_transfers: {len(pt_rows)}")

    print("\ndone -- app tables rebuilt inside sis_chatbot_db")


if __name__ == "__main__":
    main()
