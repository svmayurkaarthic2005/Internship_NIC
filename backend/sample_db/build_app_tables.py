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
from collections import defaultdict
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
    Application, ApplicationSubDivision, ApplicationDocument, WorkflowHistory,
    FieldVisit, PattaTransfer, Notification, AuditLog, ChatSession,
    ChatMessage, KnowledgeEmbedding,
)
from backend.sample_db.identifiers import aadhaar_for, can_channel, normalize_can
from backend.services.auth_service import get_password_hash

DB_URL = "postgresql+psycopg2://postgres:Mayur%402005@127.0.0.1:5432/sis_chatbot_db"

DEFAULT_PASSWORD = "Test@1234"

# service_code -> application_type. The Application model only admits these
# three (ck_application_type), so other services stay in the CSV-shaped tables
# and are not projected.
SERVICE_TO_TYPE = {"0153": "NISD", "0154": "ISD", "0155": "MERGE"}

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
# Read off the extracts rather than guessed. Role 1 is the CSC / e-Sevai
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
# sis_officers is included because officer UUIDs are regenerated each run, so
# chat_sessions / notifications / audit_logs that reference them are cleared by
# the CASCADE too. knowledge_embeddings is NOT touched: the document embeddings
# are expensive to rebuild and do not depend on any of this.
OWNED_TABLES = [
    "patta_transfers", "field_visits", "workflow_history",
    "application_documents", "application_sub_divisions", "applications",
    "applicants", "survey_ownership", "owners", "sub_divisions",
    "survey_numbers", "officer_jurisdictions", "sis_officers",
    "notifications", "audit_logs", "chat_messages", "chat_sessions",
    "blocks", "wards", "towns", "taluks", "districts",
]


def _utcnow():
    return datetime.now(timezone.utc)


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

    Base.metadata.create_all(engine)
    # An earlier schema enforced one active application per survey number.
    # The extracts disprove that (a parcel can be under two live requests at
    # once), so models.py now declares a plain index; drop the superseded
    # unique one if this database still carries it.
    with engine.begin() as cx:
        cx.execute(text("DROP INDEX IF EXISTS idx_unique_active_app_per_survey"))
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

        for dc, tk, tw, wd, bl in rows:
            if dc not in district_id:
                district_id[dc] = uuid.uuid4()
                cx.execute(text("""INSERT INTO districts (id,name,district_code,created_at,updated_at)
                    VALUES (:i,:n,:c,:t,:t)"""),
                    dict(i=district_id[dc], n=DISTRICT_CODE_MAP.get(dc, f"District {dc}"),
                         c=dc, t=now))
            if (dc, tk) not in taluk_id:
                taluk_id[(dc, tk)] = uuid.uuid4()
                cx.execute(text("""INSERT INTO taluks (id,district_id,name,taluk_code,created_at,updated_at)
                    VALUES (:i,:d,:n,:c,:t,:t)"""),
                    dict(i=taluk_id[(dc, tk)], d=district_id[dc],
                         n=DISTRICT_CODE_MAP.get(dc, "Taluk"), c=tk, t=now))
            if (dc, tk, tw) not in town_id:
                town_id[(dc, tk, tw)] = uuid.uuid4()
                cx.execute(text("""INSERT INTO towns (id,taluk_id,name,town_code,created_at,updated_at)
                    VALUES (:i,:k,:n,:c,:t,:t)"""),
                    dict(i=town_id[(dc, tk, tw)], k=taluk_id[(dc, tk)],
                         n=DISTRICT_CODE_MAP.get(dc, "Town"), c=tw, t=now))
            if (dc, tk, tw, wd) not in ward_id:
                ward_id[(dc, tk, tw, wd)] = uuid.uuid4()
                cx.execute(text("""INSERT INTO wards (id,town_id,ward_number,ward_name,created_at,updated_at)
                    VALUES (:i,:o,:n,:w,:t,:t)"""),
                    dict(i=ward_id[(dc, tk, tw, wd)], o=town_id[(dc, tk, tw)],
                         n=wd, w=f"Ward {int(wd)}", t=now))
            key = (dc, tk, tw, wd, bl)
            if key not in block_id:
                block_id[key] = uuid.uuid4()
                cx.execute(text("""INSERT INTO blocks (id,ward_id,block_number,block_name,created_at,updated_at)
                    VALUES (:i,:w,:n,:b,:t,:t)"""),
                    dict(i=block_id[key], w=ward_id[(dc, tk, tw, wd)],
                         n=bl, b=f"Block {int(bl)}", t=now))
        print(f"geography: {len(district_id)} district, {len(taluk_id)} taluk, "
              f"{len(town_id)} town, {len(ward_id)} ward, {len(block_id)} block")

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
                   relative_name_english, aadhaar_number, ownership_share, own_num
            FROM urban_natham_chitta_owner""")).all()
        for patta, name_en, name_ta, rel, aadhaar, share, own_num in owners:
            if patta not in survey_by_patta:
                continue
            sid, sub_uuid, _wd, _sno = survey_by_patta[patta]
            oid = uuid.uuid4()
            owner_rows.append(dict(
                i=oid, n=name_en or name_ta, nt=name_ta, f=rel,
                a=(aadhaar or "")[-4:] or None, m=None, ad=None, t=now))
            # "1/2" -> 50.00
            pct = 100.0
            if share and "/" in share:
                try:
                    num, den = share.split("/")
                    pct = round(100.0 * int(num) / int(den), 2)
                except (ValueError, ZeroDivisionError):
                    pct = 100.0
            ownership_rows.append(dict(
                i=uuid.uuid4(), s=sid, d=sub_uuid, o=oid, p=pct,
                j=(own_num or 1) > 1, ty="joint" if (own_num or 1) > 1 else "sole",
                e=None, t=now))
        cx.execute(text("""INSERT INTO owners
            (id,name,name_tamil,father_name,aadhaar_last4,mobile,address,created_at,updated_at)
            VALUES (:i,:n,:nt,:f,:a,:m,:ad,:t,:t)"""), owner_rows)
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
            oid = uuid.uuid4()
            officer_id[user] = oid
            name = humanise(user)
            cx.execute(text("""INSERT INTO sis_officers
                (id,employee_id,name,name_tamil,email,password_hash,mobile,
                 designation,is_active,created_at,updated_at)
                VALUES (:i,:e,:n,:nt,:m,:p,:mo,:d,:a,:t,:t)"""),
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
        info = {}
        for table in ("nisd_transfer_application_info", "isd_transfer_application_info"):
            for r in cx.execute(text(f"""
                SELECT application_id, applicant_name, mobile_number, current_address,
                       application_status
                FROM {table}""")).all():
                info[r[0]] = r

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
                       source_code, source_name
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
        for app_id, svc, wd, sno, sub_date, status, can, _src, src_name in apps:
            key = (wd, sno)
            if key not in survey_id:
                skipped_no_survey += 1
                continue
            atype = SERVICE_TO_TYPE[svc]
            rec = info.get(app_id)
            status_text = (rec[4] or "").strip().lower() if rec else ""
            cur_status = STATUS_MAP.get(status, "pending")
            if cur_status == "pending":
                cur_status = OPEN_TEXT_MAP.get(status_text, "pending")

            # The extracts do carry several concurrently-active applications on
            # one survey number (a parcel can be under more than one request at
            # a time), so the status is projected exactly as the log records it.
            if cur_status in ("pending", "in_progress", "escalated"):
                active_taken.add(survey_id[key])

            aid = uuid.uuid4()
            name = rec[1] if rec and rec[1] else None
            # The extracts strip the applicant's Aadhaar, so it is derived the
            # same way seed_sample_db.py derives an owner's: keyed on the name,
            # which means an applicant who also holds a natham chitta patta
            # carries the one number across both layers. Only the last four
            # digits are kept -- that is all the model stores.
            aadhaar = aadhaar_for(name or f"applicant|{app_id}")
            applicant_rows.append(dict(
                i=aid,
                n=name or "Citizen Applicant",
                m=(rec[2] if rec else None),
                a=aadhaar[-4:],
                ad=(rec[3] if rec else None), t=now))

            if cur_status == "rejected":
                stage = "REJECTED"
            elif cur_status == "approved":
                stage = "COMPLETED"
            else:
                la = last_hop.get(app_id)
                stage = ROLE_TO_STAGE.get(la[1], "SIS") if la else "SIS"

            visit = fv_date.get(app_id)
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
            reason_of[app_id] = (rec[4] if rec and rec[4] else None)
            d_rec = deed.get(app_id)
            deed_no = d_rec[1] if d_rec else None
            reason = (TRANSFER_REASON_TO_DECLARED.get((d_rec[2] or "").strip().lower())
                      if d_rec else None)
            notes = (f"Registered at {d_rec[3]} on {d_rec[4]}"
                     if d_rec and d_rec[3] and d_rec[4] else None)
            # source_name says which channel filed it; the channel fixes the
            # CAN's length (CSC 15 digits, citizen 12) and a value that cannot
            # be brought to that length is not a CAN and is dropped.
            channel = can_channel(src_name)
            can_no = normalize_can(can, channel)
            if can_no is None:
                can_dropped += can is not None
            elif can_no != (can or "").strip():
                can_repaired += 1

            app_rows.append(dict(
                i=auuid, num=app_id, ty=atype, ap=aid, s=survey_id[key],
                o=officer_for_ward[wd], ch=channel, d=sub_date,
                sd=deed_no, sr=deed_no is not None, dr=reason,
                can=can_no, st=stage, cs=cur_status,
                fv=visit, fs=visit is not None, ov=overdue,
                pr=cur_status == "escalated", no=notes, t=now))

            for doc in REQUIRED_DOCS[atype]:
                doc_rows.append(dict(
                    i=uuid.uuid4(), a=auuid, ty=doc, n=f"{doc} - {app_id}",
                    u=cur_status != "pending", v=cur_status in ("approved", "in_progress"),
                    ua=now if cur_status != "pending" else None, t=now))

        cx.execute(text("""INSERT INTO applicants
            (id,name,mobile,aadhaar_last4,address,created_at,updated_at)
            VALUES (:i,:n,:m,:a,:ad,:t,:t)"""), applicant_rows)
        cx.execute(text("""INSERT INTO applications
            (id,application_number,application_type,applicant_id,survey_number_id,
             assigned_officer_id,submission_channel,submission_date,sale_deed_number,
             sale_deed_registered,declared_reason,can_number,current_stage,
             current_status,field_visit_date,field_visit_scheduled,is_overdue,
             priority_flag,notes,created_at,updated_at)
            VALUES (:i,:num,:ty,:ap,:s,:o,:ch,:d,:sd,:sr,:dr,:can,:st,:cs,:fv,:fs,
                    :ov,:pr,:no,:t,:t)"""), app_rows)
        cx.execute(text("""INSERT INTO application_documents
            (id,application_id,document_type,document_name,is_uploaded,is_verified,
             uploaded_at,created_at,updated_at)
            VALUES (:i,:a,:ty,:n,:u,:v,:ua,:t,:t)"""), doc_rows)
        print(f"applicants: {len(applicant_rows)}, applications: {len(app_rows)} "
              f"(skipped {skipped_no_survey} with no matching survey), "
              f"documents: {len(doc_rows)}")
        print(f"  CAN: {sum(1 for r in app_rows if r['ch'] == 'CSC' and r['can'])} CSC "
              f"(15 digits), {sum(1 for r in app_rows if r['ch'] == 'citizen' and r['can'])} "
              f"citizen (12 digits), {can_repaired} repaired, {can_dropped} dropped "
              f"as not a CAN")
        by_status = defaultdict(int)
        for r in app_rows:
            by_status[r["cs"]] += 1
        print("  status: " + ", ".join(f"{k} {v}" for k, v in sorted(by_status.items())))

        # ---------- application sub-divisions (ISD) ----------
        for r in cx.execute(text("""
            SELECT application_id, temporary_subdivision_number,
                   new_subdivision_number, area_square_meter, existing_patta_number
            FROM urban_temp_subdivision_parcel""")).all():
            if r[0] not in app_uuid or r[4] not in survey_by_patta:
                continue
            _sid, sub_uuid, _wd, _sno = survey_by_patta[r[4]]
            appsub_rows.append(dict(
                i=uuid.uuid4(), a=app_uuid[r[0]], s=sub_uuid,
                ar=float(r[3] or 0), n=r[1], st="pending", t=now))
        if appsub_rows:
            cx.execute(text("""INSERT INTO application_sub_divisions
                (id,application_id,sub_division_id,proposed_area_sqm,
                 proposed_sub_division_no,status,created_at,updated_at)
                VALUES (:i,:a,:s,:ar,:n,:st,:t,:t)"""), appsub_rows)
        print(f"application_sub_divisions: {len(appsub_rows)}")

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
            # A hop the officer marked "N" is the rejecting one. Some rejected
            # applications carry no such mark -- the chain simply stops -- and
            # without one the history never reaches the REJECTED stage and
            # "why was X rejected?" comes back empty. For those the closing hop
            # is the rejection, which is what it was.
            rejecting = {n for n, r in enumerate(chain) if r[6] == "N"}
            if not rejecting and status_of.get(app_id) == "rejected":
                rejecting = {len(chain) - 1}
                patched_rejections += 1
            for n, r in enumerate(chain):
                rejected_hop = n in rejecting
                reason = r[4] if (r[4] and r[4] != "-") else reason_of.get(app_id)
                wf_rows.append(dict(
                    i=uuid.uuid4(), a=app_uuid[app_id],
                    f=ROLE_TO_STAGE.get(r[1]),
                    # A rejecting hop must land on the REJECTED stage: the
                    # rejection handlers select on to_stage == "REJECTED" (or an
                    # uppercase "REJECT" in action), so a role-derived stage here
                    # made the rejection invisible.
                    t2="REJECTED" if rejected_hop else ROLE_TO_STAGE.get(r[2], "COMPLETED"),
                    ac="REJECTED" if rejected_hop else "Forwarded",
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
            if row is None or row["ty"] != "ISD":
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
        # the NISD extract carries the transfer order date, the ISD one does not
        transfer_sources = [
            ("nisd_transfer_urban_detail", "order_date"),
            ("isd_transfer_urban_detail", "registration_date"),
        ]
        for table, date_col in transfer_sources:
            for r in cx.execute(text(f"""
                SELECT application_id, old_patta_number, generated_patta_number,
                       {date_col}, transaction_status
                FROM {table}""")).all():
                if r[0] not in app_uuid or r[1] not in survey_by_patta:
                    continue
                sid, sub_uuid, _wd, _sno = survey_by_patta[r[1]]
                pool = owner_by_survey.get(sid) or []
                if len(pool) < 2:
                    continue
                # transaction_status is "01" when the transfer went through and
                # "02/NN" when it was refused, NN being the reason code (03, 15,
                # 06, ...). Reading everything that is not "01" as still pending
                # told the officer that 60 refused transfers were in flight.
                txn = (r[4] or "").strip()
                if txn == "01":
                    txn_status = "completed"
                elif txn.startswith("02"):
                    txn_status = "rejected"
                else:
                    txn_status = "pending"      # no status yet -- still moving
                pt_rows.append(dict(
                    i=uuid.uuid4(), a=app_uuid[r[0]], s=sid, d=sub_uuid,
                    p=pool[0], n=pool[-1], o=f"{r[0]}TR", tr=r[3],
                    ts=r[3], ds=txn == "01",
                    st=txn_status, t=now))
        if pt_rows:
            cx.execute(text("""INSERT INTO patta_transfers
                (id,application_id,survey_number_id,sub_division_id,previous_owner_id,
                 new_owner_id,transfer_order_number,transfer_date,
                 tahsildar_signature_date,dsc_applied,status,created_at,updated_at)
                VALUES (:i,:a,:s,:d,:p,:n,:o,:tr,:ts,:ds,:st,:t,:t)"""), pt_rows)
        print(f"patta_transfers: {len(pt_rows)}")

    print("\ndone -- app tables rebuilt inside sis_chatbot_db")


if __name__ == "__main__":
    main()
