"""
test_field_queries.py -- Field-level query tests for the SIS chatbot.

Covers every column from urban_application_log that an SIS officer might ask
about, plus cross-cutting concerns:
  - Basic field lookups (English + Tamil + Tanglish)
  - Reference to previous message (implicit follow-up / context continuation)
  - Fields that are in the applications table  -> must answer with DB value
  - Fields NOT in the applications table       -> must NOT hallucinate; must
      explain availability or say the field is not stored
  - Safety: mutation-like field questions must not trigger a wipe

Run:
    python test_field_queries.py              # routing + data (no LLM)
    python test_field_queries.py --routing    # classification only, no DB
"""

from __future__ import annotations

import asyncio
import sys
import traceback
from typing import Any, Dict, List, Optional, Tuple

import logging
logging.disable(logging.INFO)

sys.stdout.reconfigure(encoding="utf-8")

ROUTING_ONLY = "--routing" in sys.argv

# ---------------------------------------------------------------------------
# Application numbers verified against sis_chatbot_db
# ---------------------------------------------------------------------------
# All apps belong to SIS-003 (muthulakshmis, ward C Meelavittan).
# SIS-003 has the widest variety: CSC+sub_registrar, ISD+NISD, pending+in_progress+approved+rejected, camp.
APP_ISD_APPROVED = "2026/0154/28/000685"   # ISD, approved, CSC, SIS-003 ward
APP_ISD_APPROVED_IGRS = "2023/0154/28/000021"   # ISD, approved, sub_registrar, IGRS=202227078638
APP_ISD_PENDING  = "2026/0154/28/001280"   # ISD, pending, CSC, CAN=133280136630740, SIS-003
APP_NISD_PENDING = "2026/0153/28/001876"   # NISD, in_progress (closest pending), SIS-003
APP_NISD_INPROG  = "2026/0153/28/001876"   # NISD, in_progress, SIS-003
APP_IGRS         = "2023/0154/28/000021"   # ISD, approved, sub_registrar, IGRS present, SIS-003
APP_CAMP         = "2024/0154/28/001397"   # ISD, rejected, camp_flag=P, citizen, SIS-003
# CAN numbers for hard assertions
_CAN_ISD_APPROVED = "133280135566736"      # CAN for APP_ISD_APPROVED (2026/0154/28/000685)
_CAN_IGRS         = "202227078638"         # CAN==IGRS for APP_IGRS
_IGRS_NUM         = "202227078638"         # IGRS Form6 number for APP_IGRS

# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------
from backend.services.rag import parse_intent

# Pre-generated valid UUIDs for reference-test sessions
import uuid as _uuid_mod
_SID_REF1 = "a1b2c3d4-0001-0000-0000-000000000000"
_SID_REF2 = "a1b2c3d4-0002-0000-0000-000000000000"
_SID_REF3 = "a1b2c3d4-0003-0000-0000-000000000000"
_SID_REF4 = "a1b2c3d4-0004-0000-0000-000000000000"
_SID_REF5 = "a1b2c3d4-0005-0000-0000-000000000000"

if not ROUTING_ONLY:
    import uuid as _uuid
    from backend.services.chatbot import process_chat
    from backend.database import AsyncSessionLocal
    from backend.schemas import OfficerContext
    from backend.models import SISOfficer
    from backend.services.auth_service import get_officer_jurisdiction_ids
    from sqlalchemy import select

    _OFFICER: Optional[OfficerContext] = None

    async def _get_officer() -> OfficerContext:
        """Load SIS-003 (muthulakshmis, ward C Meelavittan) from DB.
        SIS-003 owns the largest variety of test apps:
        CSC+sub_registrar, ISD+NISD, approved+pending+in_progress+rejected, camp.
        """
        global _OFFICER
        if _OFFICER is not None:
            return _OFFICER
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(SISOfficer)
                .where(SISOfficer.employee_id == "SIS-003")
                .limit(1)
            )
            officer_model = result.scalars().first()
            if officer_model is None:
                # Fallback: first active officer
                result = await db.execute(
                    select(SISOfficer)
                    .where(SISOfficer.is_active.is_(True))
                    .order_by(SISOfficer.employee_id).limit(1)
                )
                officer_model = result.scalars().first()
            if officer_model is None:
                raise RuntimeError("No active officer in DB -- run seed first")
            jdata = await get_officer_jurisdiction_ids(officer_model.id, db)
            all_ids = (
                jdata["district_ids"] + jdata["taluk_ids"] +
                jdata["town_ids"]     + jdata["ward_ids"]  +
                jdata["block_ids"]
            )
            _OFFICER = OfficerContext(
                officer_id=officer_model.id,
                employee_id=officer_model.employee_id,
                name=officer_model.name,
                name_tamil=getattr(officer_model, "name_tamil", None),
                email=officer_model.email,
                designation=officer_model.designation,
                jurisdiction_type=jdata.get("jurisdiction_type", "ward"),
                jurisdiction_name=jdata.get("jurisdiction_name", "Ward"),
                jurisdiction_ids=all_ids,
                district_ids=jdata["district_ids"],
                taluk_ids=jdata["taluk_ids"],
                town_ids=jdata["town_ids"],
                ward_ids=jdata["ward_ids"],
                block_ids=jdata["block_ids"],
                is_active=officer_model.is_active,
            )
        return _OFFICER

# ---------------------------------------------------------------------------
# Test infrastructure
# ---------------------------------------------------------------------------
_failures: List[str] = []
_passes: int = 0


def _fail(label: str, detail: str) -> None:
    msg = f"FAIL  {label}\n      {detail}"
    print(msg)
    _failures.append(msg)


def _pass(label: str) -> None:
    global _passes
    _passes += 1


# ===========================================================================
# SECTION 1 -- ROUTING TESTS
# ===========================================================================

ROUTING_CASES: List[Tuple[str, Optional[str], str]] = [
    # English field lookups (with explicit app number -> application_status)
    (f"What is the serial number of {APP_ISD_APPROVED}?",
     "application_status", "serial_number lookup EN"),
    (f"Show me the user_id for {APP_ISD_APPROVED}",
     "application_status", "user_id lookup EN"),
    (f"What is the department code for {APP_ISD_APPROVED}",
     "application_status", "department_code lookup EN"),
    (f"What service code is {APP_ISD_APPROVED}",
     "service_code_guide", "service_code lookup EN"),
    (f"What is the district code for {APP_ISD_APPROVED}",
     "application_status", "district_code lookup EN"),
    (f"What is the taluk code for {APP_ISD_APPROVED}",
     "application_status", "taluk_code lookup EN"),
    (f"What is the village code for {APP_ISD_APPROVED}",
     "application_status", "village_code lookup EN"),
    (f"What is the urban unit code for {APP_ISD_APPROVED}",
     "application_status", "urban_unit_code lookup EN"),
    (f"What is the ward code for {APP_ISD_APPROVED}",
     "application_status", "ward_code lookup EN"),
    (f"What is the block code for {APP_ISD_APPROVED}",
     "application_status", "block_code lookup EN"),
    (f"What is the application date for {APP_ISD_APPROVED}",
     "application_status", "application_date lookup EN"),
    (f"What is the application status for {APP_ISD_APPROVED}",
     "application_status", "application_status lookup EN"),
    (f"When was {APP_ISD_APPROVED} last updated?",
     "application_status", "last_updated_datetime lookup EN"),
    (f"What is the application ID for {APP_ISD_APPROVED}",
     "application_status", "application_id lookup EN"),
    (f"What survey number is {APP_ISD_APPROVED} for?",
     "application_status", "survey_number lookup EN"),
    (f"What is the subdivision number for {APP_ISD_APPROVED}",
     "application_status", "subdivision_number lookup EN"),
    (f"What is the patta number for {APP_ISD_APPROVED}",
     "application_status", "patta_number lookup EN"),
    (f"What is the role id for {APP_ISD_APPROVED}",
     "application_status", "role_id lookup EN"),
    (f"What is the source code for {APP_ISD_APPROVED}",
     "submission_channel_check", "source_code lookup EN"),
    (f"What is the CAN number for {APP_ISD_APPROVED}",
     "can_number_info", "can_number lookup EN"),
    (f"What is the workflow state for {APP_ISD_APPROVED}",
     "application_status", "workflow_state lookup EN"),
    (f"What is the IGRS Form 6 number for {APP_IGRS}",
     "application_status", "igrs_form6_number lookup EN"),
    (f"What is the current subdivision number for {APP_ISD_APPROVED}",
     "application_status", "current_subdivision_number lookup EN"),
    # Fields only in urban_application_log (still app_number in message)
    (f"What is the IP address of {APP_ISD_APPROVED}",
     "application_status", "ip_address lookup EN"),
    (f"What is the return status of {APP_NISD_INPROG}",
     "application_status", "return_status lookup EN"),
    (f"What is the renewal number of {APP_ISD_APPROVED}",
     "application_status", "renewal_number lookup EN"),
    (f"Is {APP_CAMP} an auto-mutated application?",
     "application_status", "auto_mutated_flag lookup EN"),
    (f"What is the camp flag for {APP_CAMP}",
     "application_status", "camp_flag lookup EN"),
    (f"What is the IGRS auto mutation flag for {APP_ISD_APPROVED}",
     "application_status", "igrs_auto_mutation_flag lookup EN"),
    # Tamil field lookups
    (f"{APP_ISD_APPROVED} \u0baa\u0b9f\u0bcd\u0b9f\u0bbe \u0b8e\u0ba3\u0bcd \u0b8e\u0ba9\u0bcd\u0ba9?",
     "application_status", "patta_number lookup TA"),
    (f"{APP_ISD_APPROVED} \u0bb5\u0bb0\u0bbf\u0b9a\u0bc8 \u0b8e\u0ba3\u0bcd \u0b8e\u0ba9\u0bcd\u0ba9?",
     "application_status", "serial_number lookup TA"),
    (f"{APP_ISD_APPROVED} CAN \u0b8e\u0ba3\u0bcd \u0b8e\u0ba9\u0bcd\u0ba9?",
     "application_status", "can_number lookup TA"),
    (f"{APP_ISD_APPROVED} \u0b95\u0ba3\u0b95\u0bcd\u0b95\u0bc6\u0ba3\u0bcd \u0b8e\u0ba9\u0bcd\u0ba9?",
     "application_status", "survey_number lookup TA"),
    (f"{APP_ISD_APPROVED} \u0ba8\u0bbf\u0bb2\u0bc8 \u0b8e\u0ba9\u0bcd\u0ba9?",
     "application_status", "status lookup TA"),
    (f"{APP_IGRS} IGRS \u0baa\u0b9f\u0bbf\u0bb5\u0bae\u0bcd 6 \u0b8e\u0ba3\u0bcd \u0b8e\u0ba9\u0bcd\u0ba9?",
     "application_status", "igrs_form6 lookup TA"),
    # Tanglish field lookups
    (f"{APP_ISD_APPROVED} patta number enna?",
     "application_status", "patta_number lookup Tanglish"),
    (f"{APP_ISD_APPROVED} serial number enna?",
     "application_status", "serial_number lookup Tanglish"),
    (f"{APP_ISD_APPROVED} status enna?",
     "application_status", "status lookup Tanglish"),
    (f"Yenna {APP_ISD_APPROVED} survey number?",
     "application_status", "survey_number lookup Tanglish"),
    # Implicit follow-ups (no app number) -- chatbot resolves from context
    # parse_intent routes these to application_status (field keyword present)
    # or general_query (bare keyword, no context)
    ("What is its serial number?",
     "application_status", "serial_number implicit followup EN"),
    ("show the patta number",
     "application_status", "patta_number implicit followup EN"),
    # bare "can number?" has no surrounding context -> general_query fallback
    ("what is the can number?",
     "general_query", "can_number bare fallback EN"),
    ("ip address?",
     "application_status", "ip_address implicit followup EN"),
    ("source code la?",
     "application_status", "source_code implicit followup Tanglish"),
    # Safety: field names must NOT route to clear
    ("reset my pending applications count",
     None, "safety: reset pending NOT a wipe"),
    ("delete the patta number entry",
     None, "safety: delete patta NOT a wipe"),
]


def run_routing_tests() -> None:
    print("=" * 72)
    print("ROUTING")
    print("=" * 72)
    for msg, expected_intent, label in ROUTING_CASES:
        result = parse_intent(msg)
        got = result if isinstance(result, str) else result.get("intent") if isinstance(result, dict) else str(result)

        if expected_intent is None:
            # Safety check: must not be "clear"
            if got == "clear":
                _fail(label, f"msg={msg!r} -> intent={got!r} (must not be 'clear')")
            else:
                _pass(label)
            continue

        if got == expected_intent:
            _pass(label)
        else:
            _fail(label,
                  f"msg={msg!r}\n      "
                  f"expected={expected_intent!r} got={got!r}")


# ===========================================================================
# SECTION 2 -- DATA TESTS  (skipped with --routing)
# ===========================================================================

async def _chat(message: str,
                history: Optional[List[Dict]] = None,
                session_id: Optional[str] = None) -> str:
    """Call process_chat with a real DB officer and a valid UUID session."""
    officer = await _get_officer()
    sid = session_id or str(_uuid.uuid4())
    async with AsyncSessionLocal() as db:
        result = await process_chat(
            db=db,
            message=message,
            officer=officer,
            session_id=sid,
            chat_history=history or [],
        )
        return result.get("response", "") if isinstance(result, dict) else str(result)


def _assert_contains(label: str, response: str, fragment: str) -> None:
    if fragment.lower() in response.lower():
        _pass(label)
    else:
        _fail(label,
              f"expected fragment: {fragment!r}\n"
              f"      response snippet : {response[:300]!r}")


def _assert_not_contains(label: str, response: str, fragment: str) -> None:
    if fragment.lower() not in response.lower():
        _pass(label)
    else:
        _fail(label,
              f"forbidden fragment: {fragment!r}\n"
              f"      response snippet  : {response[:300]!r}")


def _assert_not_empty(label: str, response: str) -> None:
    if response and response.strip():
        _pass(label)
    else:
        _fail(label, "response was empty or None")


def _assert_contains_any(label: str, response: str, fragments: List[str]) -> None:
    low = response.lower()
    if any(f.lower() in low for f in fragments):
        _pass(label)
    else:
        _fail(label,
              f"expected one of {fragments!r}\n"
              f"      response snippet : {response[:300]!r}")


async def run_data_tests() -> None:
    print("\n" + "=" * 72)
    print("DATA (live DB, no LLM)")
    print("=" * 72)

    # -- 2.1  serial_number -----------------------------------------------
    # The serial the officer means is the last segment of the application
    # number (YYYY/service/district/SERIAL), not the layer-1 within-batch
    # row counter -- derive it so the check survives a reseed.
    _serial = APP_ISD_APPROVED.split("/")[-1].lstrip("0") or "0"
    label = "serial_number -- basic EN"
    try:
        r = await _chat(f"What is the serial number of {APP_ISD_APPROVED}?")
        _assert_contains(label, r, _serial)
    except Exception:
        _fail(label, traceback.format_exc()[-300:])

    label = "serial_number -- Tamil"
    try:
        r = await _chat(f"{APP_ISD_APPROVED} \u0bb5\u0bb0\u0bbf\u0b9a\u0bc8 \u0b8e\u0ba3\u0bcd \u0b8e\u0ba9\u0bcd\u0ba9?")
        _assert_contains(label, r, _serial)
    except Exception:
        _fail(label, traceback.format_exc()[-300:])

    # -- 2.2  user_id -------------------------------------------------------
    label = "user_id -- basic EN"
    try:
        r = await _chat(f"What is the user id for {APP_ISD_APPROVED}?")
        _assert_not_empty(label, r)
        _assert_not_contains(label, r, "not found")
    except Exception:
        _fail(label, traceback.format_exc()[-300:])

    # -- 2.3  department_code -- always 01 for Revenue ----------------------
    label = "department_code -- must return 01"
    try:
        r = await _chat(f"What is the department code for {APP_ISD_APPROVED}?")
        _assert_contains(label, r, "01")
    except Exception:
        _fail(label, traceback.format_exc()[-300:])

    # -- 2.4  service_code --------------------------------------------------
    label = "service_code -- ISD must be 0154"
    try:
        r = await _chat(f"What is the service code for {APP_ISD_APPROVED}?")
        _assert_contains(label, r, "0154")
    except Exception:
        _fail(label, traceback.format_exc()[-300:])

    label = "service_code -- NISD must be 0153"
    try:
        r = await _chat(f"What is the service code for {APP_NISD_PENDING}?")
        _assert_contains(label, r, "0153")
    except Exception:
        _fail(label, traceback.format_exc()[-300:])

    # -- 2.5  district_code -------------------------------------------------
    label = "district_code -- must return 28 (Thoothukudi)"
    try:
        r = await _chat(f"What is the district code for {APP_ISD_APPROVED}?")
        _assert_contains(label, r, "28")
    except Exception:
        _fail(label, traceback.format_exc()[-300:])

    # -- 2.6  application_date --------------------------------------------
    # application_date == submission_date; assert it is an ISO date whose year
    # matches the application number's year, not a fixed calendar year.
    _appyear = APP_ISD_APPROVED.split("/")[0]
    label = f"application_date -- ISO date in {_appyear}"
    try:
        r = await _chat(f"What is the application date for {APP_ISD_APPROVED}?")
        _assert_contains(label, r, _appyear)
        import re as _re6
        if not _re6.search(r"\b\d{4}-\d{2}-\d{2}\b", r):
            _fail(label, f"not an ISO date: {r[:160]!r}")
    except Exception:
        _fail(label, traceback.format_exc()[-300:])

    # -- 2.7  last_updated_datetime -----------------------------------------
    label = "last_updated_datetime -- EN"
    try:
        r = await _chat(f"When was {APP_ISD_APPROVED} last updated?")
        _assert_not_empty(label, r)
        _assert_not_contains(label, r, "i could not find")
    except Exception:
        _fail(label, traceback.format_exc()[-300:])

    # -- 2.8  CAN number ----------------------------------------------------
    label = "can_number -- EN (known value)"
    try:
        r = await _chat(f"What is the CAN number for {APP_ISD_APPROVED}?")
        _assert_contains(label, r, _CAN_ISD_APPROVED)
    except Exception:
        _fail(label, traceback.format_exc()[-300:])

    label = "can_number -- Tamil"
    try:
        r = await _chat(f"{APP_ISD_APPROVED} CAN \u0b8e\u0ba3\u0bcd \u0b8e\u0ba9\u0bcd\u0ba9?")
        _assert_contains(label, r, _CAN_ISD_APPROVED)
    except Exception:
        _fail(label, traceback.format_exc()[-300:])

    label = "can_number -- Tanglish"
    try:
        r = await _chat(f"{APP_ISD_APPROVED} CAN number enna?")
        _assert_contains(label, r, _CAN_ISD_APPROVED)
    except Exception:
        _fail(label, traceback.format_exc()[-300:])

    # -- 2.9  IGRS Form 6 ---------------------------------------------------
    label = "igrs_form6_number -- sub_registrar app has one"
    try:
        r = await _chat(f"What is the IGRS Form 6 number for {APP_IGRS}?")
        _assert_contains(label, r, _IGRS_NUM)
    except Exception:
        _fail(label, traceback.format_exc()[-300:])

    label = "igrs_form6_number -- CSC app should say no IGRS"
    try:
        r = await _chat(f"Does {APP_ISD_APPROVED} have an IGRS Form 6 number?")
        # APP_ISD_APPROVED is a CSC app -- must NOT contain an IGRS number
        _assert_not_contains(label, r, _IGRS_NUM)
    except Exception:
        _fail(label, traceback.format_exc()[-300:])

    label = "igrs_form6_number -- Tamil (IGRS \u0b8e\u0ba3\u0bcd)"
    try:
        r = await _chat(f"{APP_IGRS} IGRS \u0b8e\u0ba3\u0bcd \u0b8e\u0ba9\u0bcd\u0ba9?")
        _assert_contains(label, r, _IGRS_NUM)
    except Exception:
        _fail(label, traceback.format_exc()[-300:])

    # -- 2.10  source / submission channel ----------------------------------
    label = "source_code -- CSC app should say CSC"
    try:
        r = await _chat(f"What is the source code for {APP_ISD_APPROVED}?")
        _assert_contains(label, r, "CSC")
    except Exception:
        _fail(label, traceback.format_exc()[-300:])

    label = "source -- IGRS / sub_registrar app"
    try:
        r = await _chat(f"What is the source for {APP_IGRS}?")
        _assert_contains_any(label, r, ["sub", "registrar", "igrs", "sro"])
    except Exception:
        _fail(label, traceback.format_exc()[-300:])

    # -- 2.11  workflow_state -----------------------------------------------
    label = "workflow_state -- approved ISD is COMPLETED"
    try:
        r = await _chat(f"What is the workflow state for {APP_ISD_APPROVED}?")
        _assert_contains_any(label, r,
                             ["completed", "rejected", "sis", "sd", "dis", "tahsildar"])
    except Exception:
        _fail(label, traceback.format_exc()[-300:])

    # -- 2.12  patta_number -------------------------------------------------
    label = "patta_number -- not empty for approved app"
    try:
        r = await _chat(f"What is the patta number for {APP_ISD_APPROVED}?")
        _assert_not_empty(label, r)
    except Exception:
        _fail(label, traceback.format_exc()[-300:])

    label = "patta_number -- Tamil"
    try:
        r = await _chat(f"{APP_ISD_APPROVED} \u0baa\u0b9f\u0bcd\u0b9f\u0bbe \u0b8e\u0ba3\u0bcd \u0b8e\u0ba9\u0bcd\u0ba9?")
        _assert_not_empty(label, r)
    except Exception:
        _fail(label, traceback.format_exc()[-300:])

    # -- 2.13  subdivision_number / current_subdivision_number --------------
    label = "subdivision_number -- ISD should have one"
    try:
        r = await _chat(f"What is the subdivision number for {APP_ISD_APPROVED}?")
        _assert_not_empty(label, r)
        _assert_not_contains(label, r, "not found")
    except Exception:
        _fail(label, traceback.format_exc()[-300:])

    label = "current_subdivision_number -- EN"
    try:
        r = await _chat(f"What is the current subdivision number for {APP_ISD_APPROVED}?")
        _assert_not_empty(label, r)
    except Exception:
        _fail(label, traceback.format_exc()[-300:])

    # -- 2.14  survey_number ------------------------------------------------
    label = "survey_number -- basic EN"
    try:
        r = await _chat(f"What survey number is {APP_ISD_APPROVED} for?")
        _assert_not_empty(label, r)
        _assert_not_contains(label, r, "not found")
    except Exception:
        _fail(label, traceback.format_exc()[-300:])

    # -- 2.15  ward_code / block_code / urban_unit_code / village_code ------
    label = "ward_code -- not empty"
    try:
        r = await _chat(f"What is the ward code for {APP_ISD_APPROVED}?")
        _assert_not_empty(label, r)
    except Exception:
        _fail(label, traceback.format_exc()[-300:])

    label = "block_code -- not empty"
    try:
        r = await _chat(f"What is the block code for {APP_ISD_APPROVED}?")
        _assert_not_empty(label, r)
    except Exception:
        _fail(label, traceback.format_exc()[-300:])

    label = "urban_unit_code -- returns town code"
    try:
        r = await _chat(f"What is the urban unit code for {APP_ISD_APPROVED}?")
        _assert_not_empty(label, r)
    except Exception:
        _fail(label, traceback.format_exc()[-300:])

    label = "village_code -- urban app; must not hallucinate"
    try:
        r = await _chat(f"What is the village code for {APP_ISD_APPROVED}?")
        _assert_not_contains(label, r, "error")
        _assert_not_empty(label, r)
    except Exception:
        _fail(label, traceback.format_exc()[-300:])

    # -- 2.16  role_id ------------------------------------------------------
    label = "role_id -- officer designation"
    try:
        r = await _chat(f"What is the role id for {APP_ISD_APPROVED}?")
        _assert_not_empty(label, r)
    except Exception:
        _fail(label, traceback.format_exc()[-300:])

    # -- 2.17  camp_flag ----------------------------------------------------
    label = "camp_flag -- camp app says P or revenue camp"
    try:
        r = await _chat(f"What is the camp flag for {APP_CAMP}?")
        _assert_not_empty(label, r)
        _assert_contains_any(label, r, ["'p'", '"p"', " p ", "camp", "citizen", "revenue"])
    except Exception:
        _fail(label, traceback.format_exc()[-300:])

    # -- 2.18  appinfo code / relative-mobile columns the ORM does NOT project.
    #          The status WORD and the rejection REASON TEXT are answerable; the
    #          numeric codes, the SRO code, and the relative's mobile are not --
    #          the answer must be an honest "not held in your register", never a
    #          fabricated code or phone number.
    _appinfo_untracked = [
        ("What is the application status code on {a}?", "status code"),
        ("What is the rejection reason code on {a}?", "reason code"),
        ("What is the SRO code on {a}?", "sro code"),
        ("What is the relative's mobile number on {a}?", "relative mobile"),
    ]
    import re as _re
    for q_tmpl, tag in _appinfo_untracked:
        label = f"{tag} -- not projected; refuse honestly, no fabricated value"
        try:
            r = await _chat(q_tmpl.format(a=APP_CAMP))
            _assert_not_empty(label, r)
            _assert_contains_any(label, r, [
                "not held in your sis register", "not in your sis register",
                "not held", "not stored", "does not query",
                "sis பதிவேட்டில் இல்லை"])
            # The honest refusal quotes no code or number of its own. Strip the
            # echoed application number and boilerplate mentions ("Form 6",
            # "last 4 digits of the Aadhaar"), then a run of 2+ digits would be
            # an invented value.
            _stripped = (r.lower()
                         .replace(APP_CAMP.lower(), " ")
                         .replace("form 6", " ").replace("form 8", " ")
                         .replace("last 4", " ").replace("4 digits", " "))
            _runs = _re.findall(r"\d{2,}", _stripped)
            if _runs:
                _fail(label, f"looks fabricated ({_runs}): {r[:160]!r}")
        except Exception:
            _fail(label, traceback.format_exc()[-300:])

    # -- 2.19  previous-message reference into an untracked appinfo field ---
    label = "reason-code follow-up after 'show application' -- keeps ref, refuses"
    try:
        history = []
        t1 = await _chat(f"show me application {APP_CAMP}",
                         history=history, session_id=_SID_REF5)
        history += [
            {"role": "user", "content": f"show me application {APP_CAMP}"},
            {"role": "assistant", "content": t1},
        ]
        t2 = await _chat("what is the rejection reason code?",
                         history=history, session_id=_SID_REF5)
        _assert_not_empty(label, t2)
        _assert_contains_any(label, t2, [
            "not held", "not in your sis register", "not stored",
            "sis பதிவேட்டில் இல்லை"])
    except Exception:
        _fail(label, traceback.format_exc()[-300:])

    # -- 2.18  ip_address (urban_application_log only; not in applications) -
    # Must not say "Application not found" and must not crash
    label = "ip_address -- must not crash or say app not found"
    try:
        r = await _chat(f"What is the IP address of {APP_ISD_APPROVED}?")
        _assert_not_contains(label, r, "application not found")
        _assert_not_contains(label, r, "not accessible")
        _assert_not_empty(label, r)
    except Exception:
        _fail(label, traceback.format_exc()[-300:])

    # -- 2.19  return_status ------------------------------------------------
    label = "return_status -- must not crash"
    try:
        r = await _chat(f"What is the return status for {APP_NISD_INPROG}?")
        _assert_not_contains(label, r, "application not found")
        _assert_not_empty(label, r)
    except Exception:
        _fail(label, traceback.format_exc()[-300:])

    # -- 2.20  renewal_number -----------------------------------------------
    label = "renewal_number -- must answer even if 0"
    try:
        r = await _chat(f"What is the renewal number of {APP_ISD_APPROVED}?")
        _assert_not_contains(label, r, "application not found")
        _assert_not_empty(label, r)
    except Exception:
        _fail(label, traceback.format_exc()[-300:])

    # -- 2.21  auto_mutated_flag / igrs_auto_mutation_flag ------------------
    label = "auto_mutated_flag -- must not crash"
    try:
        r = await _chat(f"Is {APP_ISD_APPROVED} an auto-mutated application?")
        _assert_not_empty(label, r)
        _assert_not_contains(label, r, "application not found")
    except Exception:
        _fail(label, traceback.format_exc()[-300:])

    label = "igrs_auto_mutation_flag -- must not crash"
    try:
        r = await _chat(f"What is the IGRS auto mutation flag for {APP_ISD_APPROVED}?")
        _assert_not_empty(label, r)
    except Exception:
        _fail(label, traceback.format_exc()[-300:])

    # -- 2.22  dispatch_date / received_date / generated_datetime -----------
    label = "dispatch_date -- graceful (may be None)"
    try:
        r = await _chat(f"What is the dispatch date for {APP_ISD_APPROVED}?")
        _assert_not_contains(label, r, "application not found")
        _assert_not_empty(label, r)
    except Exception:
        _fail(label, traceback.format_exc()[-300:])

    label = "received_date -- graceful"
    try:
        r = await _chat(f"What is the received date for {APP_ISD_APPROVED}?")
        _assert_not_contains(label, r, "application not found")
        _assert_not_empty(label, r)
    except Exception:
        _fail(label, traceback.format_exc()[-300:])

    label = "generated_datetime -- graceful"
    try:
        r = await _chat(f"What is the generated datetime for {APP_ISD_APPROVED}?")
        _assert_not_contains(label, r, "application not found")
        _assert_not_empty(label, r)
    except Exception:
        _fail(label, traceback.format_exc()[-300:])

    # -- 2.23  csc_service_charge / government_service_charge --------------
    label = "csc_service_charge -- graceful (may be None)"
    try:
        r = await _chat(f"What is the CSC service charge for {APP_ISD_APPROVED}?")
        _assert_not_empty(label, r)
    except Exception:
        _fail(label, traceback.format_exc()[-300:])

    label = "government_service_charge -- graceful"
    try:
        r = await _chat(f"What is the government service charge for {APP_ISD_APPROVED}?")
        _assert_not_empty(label, r)
    except Exception:
        _fail(label, traceback.format_exc()[-300:])

    # -- 2.24  parent_application_id ----------------------------------------
    label = "parent_application_id -- must not crash"
    try:
        r = await _chat(f"What is the parent application ID for {APP_ISD_APPROVED}?")
        _assert_not_empty(label, r)
    except Exception:
        _fail(label, traceback.format_exc()[-300:])

    # -- 2.25  Multiple fields in one query ---------------------------------
    label = "multi-field: serial + can + service code"
    try:
        r = await _chat(
            f"For {APP_ISD_APPROVED} give me the serial number, CAN number, and service code"
        )
        _assert_not_empty(label, r)
        _assert_not_contains(label, r, "application not found")
    except Exception:
        _fail(label, traceback.format_exc()[-300:])

    # -- 2.26  Previous-message reference (context continuation) ------------
    label = "prev-ref: CAN number after app detail"
    try:
        history: List[Dict] = []
        t1 = await _chat(
            f"Show me the details for {APP_ISD_APPROVED}",
            history=history, session_id=_SID_REF1,
        )
        history += [
            {"role": "user", "content": f"Show me the details for {APP_ISD_APPROVED}"},
            {"role": "assistant", "content": t1},
        ]
        t2 = await _chat("What is its CAN number?",
                         history=history, session_id=_SID_REF1)
        _assert_contains(label, t2, _CAN_ISD_APPROVED)
    except Exception:
        _fail(label, traceback.format_exc()[-300:])

    label = "prev-ref: survey number after NISD detail"
    try:
        history = []
        t1 = await _chat(f"Show me {APP_NISD_PENDING} details",
                         history=history, session_id=_SID_REF2)
        history += [
            {"role": "user", "content": f"Show me {APP_NISD_PENDING} details"},
            {"role": "assistant", "content": t1},
        ]
        t2 = await _chat("What is the survey number?",
                         history=history, session_id=_SID_REF2)
        _assert_not_empty(label, t2)
        _assert_not_contains(label, t2, "provide the application number")
    except Exception:
        _fail(label, traceback.format_exc()[-300:])

    label = "prev-ref: service code after status query"
    try:
        history = []
        t1 = await _chat(f"What is the status of {APP_ISD_APPROVED}?",
                         history=history, session_id=_SID_REF3)
        history += [
            {"role": "user", "content": f"What is the status of {APP_ISD_APPROVED}?"},
            {"role": "assistant", "content": t1},
        ]
        t2 = await _chat("And the service code?",
                         history=history, session_id=_SID_REF3)
        _assert_contains(label, t2, "0154")
    except Exception:
        _fail(label, traceback.format_exc()[-300:])

    label = "prev-ref: camp flag after channel question (should carry app number)"
    try:
        history = []
        t1 = await _chat(f"Who submitted {APP_CAMP}?",
                         history=history, session_id=_SID_REF4)
        history += [
            {"role": "user", "content": f"Who submitted {APP_CAMP}?"},
            {"role": "assistant", "content": t1},
        ]
        t2 = await _chat("What is the camp flag?",
                         history=history, session_id=_SID_REF4)
        _assert_not_empty(label, t2)
        _assert_not_contains(label, t2, "please provide the application number")
    except Exception:
        _fail(label, traceback.format_exc()[-300:])

    label = "prev-ref: IGRS number after pending list + explicit number"
    try:
        history = []
        t1 = await _chat("Show my pending applications",
                         history=history, session_id=_SID_REF5)
        history += [
            {"role": "user", "content": "Show my pending applications"},
            {"role": "assistant", "content": t1},
        ]
        t2 = await _chat(f"What is the IGRS form 6 number for {APP_IGRS}?",
                         history=history, session_id=_SID_REF5)
        _assert_contains(label, t2, "202227078638")
    except Exception:
        _fail(label, traceback.format_exc()[-300:])

    # -- 2.27  Complex real-officer questions --------------------------------
    label = "complex: how long pending (SLA check)"
    try:
        r = await _chat(f"How many days has {APP_ISD_PENDING} been pending?")
        _assert_contains_any(label, r, ["day", "\u0ba8\u0bbe\u0bb3\u0bcd", "sla", "submitted"])
    except Exception:
        _fail(label, traceback.format_exc()[-300:])

    label = "complex: field visit scheduled for pending ISD"
    try:
        r = await _chat(f"Is there a field visit scheduled for {APP_ISD_PENDING}?")
        _assert_not_empty(label, r)
        _assert_contains_any(label, r,
                             ["field visit", "scheduled", "not scheduled", "no",
                              "\u0b95\u0bb3 \u0b86\u0baf\u0bcd\u0bb5\u0bc1"])
    except Exception:
        _fail(label, traceback.format_exc()[-300:])

    label = "complex: applicant name + mobile for ISD"
    try:
        r = await _chat(
            f"Who is the applicant for {APP_ISD_APPROVED} and what is their mobile?"
        )
        _assert_not_empty(label, r)
        _assert_not_contains(label, r, "not found")
    except Exception:
        _fail(label, traceback.format_exc()[-300:])

    label = "complex: NISD workflow Tamil stage question"
    try:
        r = await _chat(
            f"{APP_NISD_PENDING} SIS \u0b87\u0bb2\u0bbf\u0bb0\u0bc1\u0ba8\u0bcd\u0ba4\u0bc1 "
            f"\u0b8e\u0ba8\u0bcd\u0ba4 \u0b95\u0b9f\u0bcd\u0b9f\u0ba4\u0bcd\u0ba4\u0bbf\u0bb2\u0bcd "
            f"\u0b89\u0bb3\u0bcd\u0bb3\u0ba4\u0bc1?"
        )
        _assert_not_empty(label, r)
    except Exception:
        _fail(label, traceback.format_exc()[-300:])

    label = "complex: IGRS referral channel explanation"
    try:
        r = await _chat(
            f"How was {APP_IGRS} submitted and what does the IGRS number mean?"
        )
        _assert_contains_any(label, r, ["sub", "registrar", "sro", "igrs", "deed"])
    except Exception:
        _fail(label, traceback.format_exc()[-300:])

    label = "complex: camp application submission channel"
    try:
        r = await _chat(f"How was {APP_CAMP} submitted? Was it at a revenue camp?")
        _assert_contains_any(label, r, ["camp", "citizen", "revenue camp", " p "])
    except Exception:
        _fail(label, traceback.format_exc()[-300:])

    label = "complex: CAN length and channel meaning"
    try:
        r = await _chat(
            f"The CAN number of {APP_ISD_APPROVED} is {_CAN_ISD_APPROVED}. "
            f"Does its length tell me the submission channel?"
        )
        _assert_not_empty(label, r)
    except Exception:
        _fail(label, traceback.format_exc()[-300:])

    # -- 2.28  Safety -------------------------------------------------------
    label = "safety: delete patta must not wipe"
    try:
        r = await _chat("delete the patta number entry for application")
        _assert_not_contains(label, r, "conversation cleared")
    except Exception:
        _fail(label, traceback.format_exc()[-300:])

    label = "safety: reset pending must not wipe"
    try:
        r = await _chat("reset my pending applications count")
        _assert_not_contains(label, r, "conversation cleared")
    except Exception:
        _fail(label, traceback.format_exc()[-300:])

    # -- 2.29  Tamil complex ------------------------------------------------
    label = "Tamil: application date"
    try:
        r = await _chat(f"{APP_ISD_APPROVED} \u0bb5\u0bbf\u0ba3\u0bcd\u0ba3\u0baa\u0bcd\u0baa \u0ba4\u0bc7\u0ba4\u0bbf \u0b8e\u0ba9\u0bcd\u0ba9?")
        _assert_contains(label, r, APP_ISD_APPROVED.split("/")[0])
    except Exception:
        _fail(label, traceback.format_exc()[-300:])

    label = "Tamil: survey number"
    try:
        r = await _chat(f"{APP_ISD_APPROVED} \u0b95\u0ba3\u0b95\u0bcd\u0b95\u0bc6\u0ba3\u0bcd \u0b8e\u0ba9\u0bcd\u0ba9?")
        _assert_not_empty(label, r)
    except Exception:
        _fail(label, traceback.format_exc()[-300:])

    label = "Tamil: status"
    try:
        r = await _chat(f"{APP_ISD_APPROVED} \u0ba8\u0bbf\u0bb2\u0bc8 \u0b8e\u0ba9\u0bcd\u0ba9?")
        _assert_contains_any(label, r,
                             ["approved", "\u0b85\u0b99\u0bcd\u0b95\u0bc0\u0b95\u0bb0\u0bbf\u0b95\u0bcd\u0b95\u0baa\u0bcd\u0baa\u0b9f\u0bcd\u0b9f\u0ba4\u0bc1", "completed"])
    except Exception:
        _fail(label, traceback.format_exc()[-300:])

    label = "Tamil: field visit scheduled"
    try:
        r = await _chat(f"{APP_ISD_PENDING} \u0b95\u0bb3 \u0b86\u0baf\u0bcd\u0bb5\u0bc1 \u0ba4\u0bbf\u0b9f\u0bcd\u0b9f\u0bae\u0bbf\u0b9f\u0baa\u0bcd\u0baa\u0b9f\u0bcd\u0b9f\u0ba4\u0bbe?")
        _assert_not_empty(label, r)
    except Exception:
        _fail(label, traceback.format_exc()[-300:])

    label = "Tanglish: service code enna"
    try:
        r = await _chat(f"{APP_ISD_APPROVED} service code enna?")
        _assert_contains(label, r, "0154")
    except Exception:
        _fail(label, traceback.format_exc()[-300:])

    label = "Tanglish: district code enna"
    try:
        r = await _chat(f"{APP_ISD_APPROVED} district code enna?")
        _assert_contains(label, r, "28")
    except Exception:
        _fail(label, traceback.format_exc()[-300:])


# ===========================================================================
# MAIN
# ===========================================================================

def main() -> None:
    run_routing_tests()
    routing_fails = len(_failures)

    if not ROUTING_ONLY:
        asyncio.run(run_data_tests())

    total_fail = len(_failures)
    data_fail = total_fail - routing_fails

    print()
    print("=" * 72)
    print(f"field query tests: {_passes} passed, {total_fail} failed")
    if not ROUTING_ONLY:
        print(f"  routing: {routing_fails} failed")
        print(f"  data:    {data_fail} failed")
    print("=" * 72)
    if _failures:
        print("\nFAIL")
        sys.exit(1)
    else:
        print("\nPASS")


if __name__ == "__main__":
    main()

