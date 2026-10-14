"""
The '_UNTRACKED_*' field family: owner/parcel/signature/appinfo/geo-master/transfer-record fields the register does not carry, answered as 'not recorded, here is what IS held' rather than falling through to a generic details dump.

Extracted from backend/services/chatbot.py (SIS_Chatbot_Modularity_Refactor,
chatbot.py >=15,000-line reduction pass) with no change in behavior -- every
function body below is copied verbatim from the original module. chatbot.py
re-exports the names it still needs so existing imports and monkeypatch
targets keep working unchanged.
"""
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Dict, Any, Optional, List, Tuple, Sequence
from datetime import datetime, timedelta, date, timezone
import asyncio
import contextvars
import time
import difflib
import re
import json
import dataclasses
from pathlib import Path
import functools
import uuid
import calendar
from backend.config import (
    DISTRICT_NAME_MAP, DISTRICT_TAMIL_NAME_MAP, DISTRICT_CODE_TO_TAMIL_NAME, settings,
)
from backend.schemas import OfficerContext
from backend.services import attachment_qa
from backend.services.agent import AgentUnavailable, run_agent, run_agent_stream
from backend.services.readonly_guard import chat_turn
from backend.services import neg_scope as _neg_scope
from backend.services import negq as _negq
from backend.services import calc_qa as _calc_qa
from backend.services import howto_qa as _howto_qa
from backend.services import card_html as _card_html
from backend.services import unheld_qa as _unheld_qa
from backend.services import recheck as _recheck
from backend.services import number_qa as _number_qa
from backend.services import stats_qa as _stats_qa
from backend.services import compare_followup as _compare_followup
from backend.services import qualifier_guard as _qualifier_guard
from backend.services import followup_context as fctx
from backend.services import semantic_intent
from backend.services import subdivision_qa as _subdivision_qa
from backend.services import oldowner_qa as _oldowner_qa
from backend.services import cmflow_qa as _cmflow_qa
from backend.services import appinfo_qa as _appinfo_qa
from backend.services import fline_qa as _fline_qa
from backend.services import merge_qa as _merge_qa
from backend.services import crosscheck_qa as _crosscheck_qa
from backend.services import igrs_owner_qa as _igrs_owner_qa
from backend.services import newowner_qa as _newowner_qa
from backend.services import fulltransfer_qa as _fulltransfer_qa
from backend.services import sdattach_qa as _sdattach_qa
from backend.services import tslr_qa as _tslr_qa
from backend.services import uamod_qa as _uamod_qa
from backend.services import natham_qa as _natham_qa
from backend.services import othercode_detail as _othercode_detail
from backend.services.rag import (
    detect_language,
    is_short_reply,
    get_prior_turn_language,
    detect_jurisdiction_focus,
    wants_details_of_listed,
    requested_listing_count,
    extract_sort_order,
    extract_row_selection,
    extract_result_limit,
    get_rag_context_async,
    build_prompt,
    build_html_response,
    _money,
    call_llama,
    call_llama_stream,
    paraphrase_facts_naturally,
    parse_intent,
    parse_last_application_query,
    parse_visit_plan_query,
    extract_survey_number,
    extract_application_number,
    extract_application_numbers,
    extract_ward_number,
    extract_block_number,
    extract_town_name,
    extract_taluk_name,
    extract_date_range,
    extract_month_scopes,
    format_month_scopes,
    extract_submission_channels,
    ambiguous_channel_clarification,
    _get_projected_application_columns,
    is_bare_date_scope,
    strip_date_scope_phrases,
    extract_date_scope_fragment,
    DATE_SCOPED_INTENTS,
)
from backend.services.postgres import (
    get_all_service_code_applications,
    get_can_details,
    get_last_application,
    get_visit_plan,
    get_applications_by_block,
    get_ward_directory,
    get_pending_applications,
    count_rejected_by_channel,
    get_overdue_applications,
    get_officer_workload,
    get_officer_workload_applications,
    get_application_detail,
    get_survey_detail,
    get_survey_owners,
    get_unscheduled_visits,
    get_applications_by_numbers,
    get_field_visits,
    get_next_subdivision_number,
    check_survey_application_lock,
    get_jurisdiction_filter,
    get_ward_surveys,
    get_all_surveys_in_jurisdiction,
    get_officer_applications,
    get_fee_summary,
    get_recent_fees,
    get_field_presence,
    _application_jurisdiction_subquery,
    _field_visit_jurisdiction_subquery,
)
from backend.models import (
    ChatSession, ChatMessage, Application, SurveyNumber, Block, Ward, Town, Taluk,
    FieldVisit, ApplicationDocument, WorkflowHistory, ApplicationSubDivision,
    OfficerJurisdiction, District, SISOfficer, messages_since_session_start
)
from backend.utils.helpers import service_code_one_liner
from backend.utils.logger import get_logger
from backend.utils import translit
from sqlalchemy import select, and_, or_, func
from sqlalchemy.orm import selectinload
from backend.utils.fuzzy import (
    extract_month_from_text,
    extract_tokens,
    is_token_typo_match,
    match_phrase,
    normalize_text,
    resolve_unique_entry,
)
from backend.services.chat_shared import (
    _ANY_APP_NUMBER_RE,
    _named_transfer_reason,
)
from backend.services.chat_smalltalk import _is_mutation_request
from backend.utils.logger import get_logger

logger = get_logger(__name__)




# ── Owner photo / image retrieval requests ──────────────────────────────────
# The SIS records projected from the TAMILNILAM urban extracts hold no owner
# photograph. The source `owner_photo` / `owner_image` columns
# (nisd_transfer_*_owner, full_field_patta_transfer_igrs_owner) are placeholders — every
# value is `-`, an empty string, or NULL — and the ORM `owners` table has no
# image column at all. So there is nothing to return as a PNG/JPG. Rather than
# let the request fall through to RAG (which answers vaguely), intercept it and
# say so plainly, then point the officer at what IS retrievable.
_OWNER_PHOTO_MEDIA_WORDS = [
    "photo", "photograph", "photos", "pic", "picture", "pictures", "headshot",
    "passport size", "passport-size", "png", ".png", "jpg", ".jpg", "jpeg",
    "image", "images", "snapshot", "mugshot",
    # Tamil / Tanglish
    "புகைப்படம்", "படம்", "போட்டோ", "போடடோ", "padam", "potto", "photo venum",
]
_OWNER_PHOTO_SUBJECT_WORDS = [
    "owner", "owner's", "owners", "applicant", "applicant's", "petitioner",
    "citizen", "patta holder", "pattadar",
    "உரிமையாளர்", "விண்ணப்பதாரர்", "urimaiyalar", "urimayalar", "vinnappadhaarar",
]


# A channel question that asks for the basis rather than the label: "why is it
# CSC", "how do you know", "on what basis", "explain in detail".
_CHANNEL_WHY_RE = re.compile(
    r"\bwhy\b|\bhow\s+(?:do|did|does|can)\s+(?:u|you|we)\b|\bhow\s+is\s+it\b"
    r"|\bhow\s+it\s+is\b|\bexplain\b|\bon\s+what\s+basis\b|\bbasis\b"
    r"|\breason(?:ing)?\b|\bjustif|\bin\s+detail|\bdetailed\b"
    r"|\bwhat\s+makes?\b|\b(?:proof|evidence|sure|certain)\b|\bhow\s+(?:did|do|does)\s+(?:u|you)\s+(?:say|know|decide|tell)"
    r"|\bhow\s+do\s+u\b|ஏன்|எப்படி|விளக்க",
    re.IGNORECASE)


# Columns of the source `application_workflow` log that are NOT projected
# into the officer's register (layer 2). `get_application_detail()` never returns
# them, so a pointed question about one used to fall through to the LLM/agent
# (an ungrounded guess) or -- for "received flag" -- get caught by the bare
# "received" -> submission_date field-map key and answered with the filing date.
# Answer deterministically instead: name the field and say where it lives.
# Order matters: the more specific "remarks" cue is tested before the "flag" one
# so "auto recommendation remarks" is not swallowed by "auto recommendation".
_UNTRACKED_WF_FIELDS = [
    ("auto_recommendation_remarks", "auto-recommendation remarks", [
        "auto recommendation remarks", "auto-recommendation remarks",
        "auto_recommendation_remarks", "system recommendation note",
        "தானியங்கி பரிந்துரை குறிப்பு"]),
    ("auto_recommendation_flag", "auto-recommendation flag", [
        "auto recommendation flag", "auto-recommendation flag",
        "auto_recommendation_flag", "auto recommendation", "auto-recommend",
        "auto recommend", "system auto-recommend", "தானியங்கி பரிந்துரை"]),
    ("recommendation_status", "recommendation status", [
        "recommendation status", "recommendation_status",
        "recommended for approval", "recommend for approval",
        "பரிந்துரை நிலை", "பரிந்துரைக்கப்பட்ட"]),
    ("action_status", "action status", [
        "action status", "action_status", "நடவடிக்கை நிலை"]),
    ("annual_income", "applicant's annual income", [
        "annual income", "annual_income", "yearly income",
        "ஆண்டு வருமானம்", "வருடாந்திர வருமானம்", "வருமானம்"]),
    ("review_flag", "review flag", [
        "review flag", "review_flag", "flagged for review", "flag for review",
        "second review", "மறுஆய்வு கொடி", "மறுஆய்வுக்கு குறி", "மறுஆய்வு தேவை"]),
    ("received_flag", "received flag", [
        "received flag", "received_flag", "receiving desk acknowledg",
        "receiving desk has acknowledg", "பெறப்பட்ட கொடி"]),
]

# ip_address is projected (applications.submission_ip) and answered by _ip_answer;
# these cues only route the question to it.
_IP_FIELD_CUES = ("ip address", "ip_address", "ip முகவரி", "client ip",
                  "submitting ip", "submitting client ip")


_BARE_IP_RE = re.compile(r"(?<![a-z0-9_])ip(?![a-z0-9_])")


def _asked_untracked_wf_field(message_lower: str) -> Optional[str]:
    """The `application_workflow` column a pointed question is about, when
    it is one the chatbot has no data for. None otherwise."""
    for key, _label, cues in _UNTRACKED_WF_FIELDS:
        if any(c in message_lower for c in cues):
            return key
    if any(c in message_lower for c in _IP_FIELD_CUES):
        return "ip_address"
    # "what is the ip?" / "ip enna" -- the bare word, token-bounded so it never
    # matches inside "relationship" or "description".
    if _BARE_IP_RE.search(message_lower):
        return "ip_address"
    return None


# IP address across MULTIPLE applications -- "do any of my applications
# share the same IP?", "which applications came from an internal IP
# address?", "what IP was used for applications submitted on <date>?". A
# single application's IP ("what is the IP address for <app>?") is already
# answered correctly by _asked_untracked_wf_field once an application number
# resolves -- ip_address is in _UNTRACKED_WF_FIELDS. This is the shape that
# names NO single application: `parse_intent` reads "which applications" /
# "show me applications" as a plain listing request, and the listing
# renderer -- which has no IP column at all, because ip_address is not
# projected into the ORM -- silently substituted the registration ADDRESS
# column instead, then returned a real, specific, filtered set of
# application numbers as if an IP comparison had actually been computed. No
# such comparison ever ran: those application numbers were never matched or
# filtered on IP by anything, so presenting them as "applications with
# matching IP addresses" is worse than a blank list -- it looks verified
# and is not.  Caught here, before the listing intent ever runs.
_IP_CROSS_APP_RE = re.compile(
    r"\bip[\s_]*(?:address|addr)?\b.{0,40}\b(match|matching|same|shared|"
    r"share|internal|external|which application|applications?\s+(?:came|"
    r"submitted|from|with)|source)\b"
    r"|"
    r"\bip[\s_]*(?:address|addr)?\b.{0,40}\b(they|them|those|these|all)\b"
    r"|"
    r"\b(match|matching|same|shared|share|which application|"
    r"applications?\s+(?:came|submitted|from|with))\b.{0,40}\bip[\s_]*"
    r"(?:address|addr)?\b",
    re.IGNORECASE)


_IP_SINGULAR_REF_RE = re.compile(r"\b(it|its|that|this|the\s+same\s+one)\b", re.I)
_IP_PLURAL_REF_RE = re.compile(r"\b(applications|they|them|those|these|all|any)\b", re.I)


def _asked_ip_across_applications(message: str) -> bool:
    m = message or ""
    if not _IP_CROSS_APP_RE.search(m):
        return False
    # "is that IP internal?" / "is its IP external?" is about the one file in
    # view, not a comparison -- the follow-up layer resolves that file.
    if _IP_SINGULAR_REF_RE.search(m) and not _IP_PLURAL_REF_RE.search(m):
        return False
    # A single named application is the shape _UNTRACKED_WF_FIELDS already
    # answers correctly (and more specifically, naming that application) --
    # leave it alone rather than pre-empting a better answer.
    return not _ANY_APP_NUMBER_RE.search(m)


def _ip_kind(ip: str, is_tamil: bool = False) -> str:
    import ipaddress
    try:
        private = ipaddress.ip_address(ip).is_private
    except ValueError:
        return ""
    if is_tamil:
        return "(உள் நெட்வொர்க்)" if private else "(பொது இணையம்)"
    return "(internal network)" if private else "(public internet)"


def _ip_answer(sd: dict, app_no: str, is_tamil: bool) -> str:
    """The IP one application was submitted from, and what kind of address it is."""
    ip = (sd.get("submission_ip") or "").strip()
    if not ip:
        return (f"விண்ணப்பம் {app_no}-க்கு IP முகவரி பதிவில் இல்லை." if is_tamil
                else f"No submitting IP address is on record for {app_no}.")
    kind = _ip_kind(ip, is_tamil)
    if is_tamil:
        return f"விண்ணப்பம் {app_no} சமர்ப்பிக்கப்பட்ட IP முகவரி: {ip} {kind}."
    return f"Application {app_no} was submitted from IP address {ip} {kind}."


def _ip_compare_numbers(message: str, ctx) -> list:
    """Applications an IP question is about: the ones named (two or more), else
    the listing just shown when the question says "they / them / those"."""
    named = []
    for n in _ANY_APP_NUMBER_RE.findall(message or ""):
        n = n.upper()
        if n not in named:
            named.append(n)
    if len(named) >= 2:
        return named
    if ctx is not None and not named and re.search(r"\b(they|them|those|these)\b", message or "", re.I):
        return list(getattr(ctx, "application_numbers", None) or [])
    return []


def _asked_ip_compare(message: str) -> bool:
    m = message or ""
    if _asked_ip_across_applications(m):
        return True
    return (len(_ip_compare_numbers(m, None)) >= 2
            and bool(_BARE_IP_RE.search(m.lower()) or "ip address" in m.lower()))


async def _ip_cross_comparison_answer(db, officer, message: str, ctx, is_tamil: bool) -> str:
    """Same IP or not -- computed from the register, never by the model.

    Not to be confused with chat_application_queries._ip_comparison_answer
    ("same submission IP as <one application>"). Both used to share that
    name, and the later definition silently replaced this one, so every
    cross-application IP question crashed with a TypeError."""
    from backend.services.postgres import get_ip_comparison
    nums = _ip_compare_numbers(message, ctx)
    res = await get_ip_comparison(db, officer, nums or None)
    groups, singles, no_ip, dropped = res["groups"], res["singles"], res["no_ip"], res["dropped"]
    n = res["count"]
    if n == 0:
        return ("ஒப்பிட உங்கள் அதிகார எல்லைக்குள் விண்ணப்பங்கள் இல்லை." if is_tamil
                else "There are no applications in your jurisdiction to compare.")

    def _fmt(ip, apps):
        return f"{ip} {_ip_kind(ip, is_tamil)}: " + ", ".join(apps)

    lines = []
    if nums:
        if len(groups) == 1 and not singles and not no_ip:
            head = (f"ஆம் -- {n} விண்ணப்பங்களும் ஒரே IP முகவரியிலிருந்து சமர்ப்பிக்கப்பட்டன."
                    if is_tamil else f"Yes -- all {n} applications were submitted from the same IP address.")
        elif not groups:
            head = (f"இல்லை -- {n} விண்ணப்பங்களும் வெவ்வேறு IP முகவரிகளிலிருந்து வந்தவை."
                    if is_tamil else f"No -- these {n} applications were submitted from different IP addresses.")
        else:
            head = ("பகுதியளவு -- சில விண்ணப்பங்கள் மட்டுமே ஒரே IP-ஐப் பகிர்கின்றன."
                    if is_tamil else "Partly -- only some of these applications share an IP address.")
        lines.append(head)
    elif groups:
        shared = sum(len(a) for _, a in groups)
        lines.append(
            (f"உங்கள் {n} விண்ணப்பங்களில் {shared} விண்ணப்பங்கள் {len(groups)} IP முகவரிகளைப் பகிர்கின்றன "
             f"(நிராகரிக்கப்பட்டவை தவிர):" if is_tamil else
             f"Of your {n} applications (rejected ones excluded), {shared} share an IP address, "
             f"across {len(groups)} IP address(es):"))
    else:
        lines.append("உங்கள் விண்ணப்பங்களில் எதுவும் ஒரே IP-ஐப் பகிரவில்லை." if is_tamil
                     else f"No two of your {n} applications share an IP address (rejected ones excluded).")
    shown = groups if nums else groups[:10]
    for ip, apps in shown:
        lines.append("- " + _fmt(ip, apps))
    if nums:
        for app, ip in singles.items():
            lines.append(f"- {ip} {_ip_kind(ip, is_tamil)}: {app}")
    elif len(groups) > len(shown):
        lines.append(f"... +{len(groups) - len(shown)} more")
    if no_ip:
        lines.append(("IP பதிவில் இல்லாதவை: " if is_tamil else "No IP on record for: ") + ", ".join(no_ip))
    if dropped:
        lines.append((f"{dropped} விண்ணப்பம்(ங்கள்) உங்கள் அதிகார எல்லையில் இல்லை/கிடைக்கவில்லை."
                      if is_tamil else f"{dropped} named application(s) not found in your jurisdiction."))
    lines.append("ஒரே IP என்பது ஒரே நெட்வொர்க்/கவுண்டர் என்பதையே குறிக்கும்; ஒரே விண்ணப்பதாரர் என்று அர்த்தமல்ல."
                 if is_tamil else
                 "A shared IP means the same network or counter, not necessarily the same applicant.")
    return "\n".join(lines)


def _untracked_wf_field_answer(app_no: str, field_key: str, is_tamil: bool) -> str:
    label = next(l for k, l, _c in _UNTRACKED_WF_FIELDS if k == field_key)
    if is_tamil:
        return (
            f"விண்ணப்பம் {app_no}-ன் {label} தகவல் உங்கள் SIS பதிவேட்டில் "
            f"சேமிக்கப்படவில்லை. அது மூல பணிப்பாய்வு பதிவில் ({field_key}) "
            f"மட்டுமே உள்ளது — இந்த உதவியாளர் அந்த அடுக்கை வினவுவதில்லை. "
            f"கள ஆய்வு, பணிப்பாய்வு குறிப்புகள், தற்போதைய நிலை, ஒவ்வொரு "
            f"நகர்வின் அனுப்பிய/பெறும் மேசை ஆகியவற்றை என்னால் தர முடியும்.")
    return (
        f"Application {app_no}'s {label} is not held in your SIS register. It "
        f"exists only in the source workflow-action log ({field_key}), which "
        f"this assistant does not query. I can give you the field visit, the "
        f"workflow remarks, the current stage, and the from/to desk of each hop.")


# Columns of the source `appl_log_urban` extract that `build_app_tables.py`
# does NOT project into the officer's register (layer 2). `get_application_detail`
# never returns them, so a pointed question about one used to be fuzzy-matched to
# a semantically-near ORM field and answered WRONG with confidence:
#   "land type"           -> application_type  ("... Application Type ... is: ISD")
#   "adopted area"        -> area_sqm          ("... Area (sq.m) ... is: 40.0")
#   "plot type" / "plot number" -> application_type
#   "new patta waste"    -> new_patta_number dump
# Answer deterministically instead: name the field and say it is not in the
# register. Runs BEFORE the fuzzy field map so it pre-empts the wrong match.
# Order matters: the more specific cue is listed before the shorter one it
# contains ("land use description" before "land use", "group ... number" before
# "survey"/"patta").
# NOTE: patta-transfer / registration-detail extract columns
# (`nisd_/sub_div_patta_transfer_urban`) are handled separately by
# `_asked_untracked_transfer_field` / `_asked_registration_detail` below -- do
# not add them here.
_UNTRACKED_SOURCE_FIELDS = [
    ("land_use_description", "land-use description", [
        "land use description", "land-use description", "landuse description",
        "land use detail", "நில பயன்பாட்டு விவரம்"]),
    ("land_use", "land use", [
        "land use", "land-use", "landuse", "நில பயன்பாடு"]),
    ("primary_soil_type", "soil type", [
        "soil type", "soil_type", "primary soil", "secondary soil",
        "type of soil", "soil classification", "மண் வகை"]),
    ("tax_per_hectare", "tax per hectare", [
        "tax per hectare", "tax/hectare", "tax per ha", "வரி ஹெக்டேர்"]),
    ("tax_rate", "tax rate", [
        "tax rate", "tax_rate", "rate of tax", "வரி விகிதம்"]),
    ("government_private_flag", "government / private classification", [
        "government/private", "government / private", "govt/private",
        "govt / private", "government or private", "govt or private",
        "government private flag", "government land", "private land",
        "poramboke", "அரசு அல்லது தனியார்", "அரசு தனியார்"]),
    ("adopted_area", "adopted area", [
        "adopted area", "adopted_area", "surveyor adopted area",
        "district adopted area", "area adopted", "ஏற்கப்பட்ட பரப்பு",
        "ஏற்றுக்கொள்ளப்பட்ட பரப்பு"]),
    ("new_patta_waste", "new-patta waste value", [
        "new patta waste", "new_patta_waste", "patta waste", "புதிய பட்டா தரிசு"]),
    ("plot_number", "plot number", [
        "plot number", "plot_number", "plot no", "மனை எண்"]),
    ("plot_type", "plot type", [
        "plot type", "plot_type", "type of plot", "மனை வகை"]),
    ("group_survey_number", "group survey number", [
        "group survey number", "group_survey_number", "group survey no",
        "குழு கணக்கெண்"]),
    ("group_subdivision_number", "group sub-division number", [
        "group subdivision number", "group sub-division number",
        "group_subdivision_number", "group temporary subdivision",
        "group temporary sub-division"]),
    ("group_patta_number", "group patta number", [
        "group patta number", "group_patta_number", "group patta no",
        "குழு பட்டா எண்"]),
]


def _asked_untracked_source_field(message_lower: str) -> Optional[str]:
    """The `appl_log_urban` column a pointed question is about, when it is
    one the ORM projection drops. None otherwise."""
    for key, _label, cues in _UNTRACKED_SOURCE_FIELDS:
        if any(c in message_lower for c in cues):
            return key
    return None


def _untracked_source_field_answer(app_no: str, field_key: str, is_tamil: bool) -> str:
    label = next(l for k, l, _c in _UNTRACKED_SOURCE_FIELDS if k == field_key)
    if is_tamil:
        return (
            f"விண்ணப்பம் {app_no}-ன் {label} தகவல் உங்கள் SIS பதிவேட்டில் "
            f"இல்லை. அது மூல விண்ணப்பப் பதிவில் (appl_log_urban / "
            f"{field_key}) மட்டுமே உள்ளது — இந்த உதவியாளர் அந்த அடுக்கை "
            f"வினவுவதில்லை. சர்வே எண், பட்டா எண், பரப்பளவு (ச.மீ), நில வகை, "
            f"உட்பிரிவுகள், நிலை, கள ஆய்வு ஆகியவற்றை என்னால் தர முடியும்.")
    return (
        f"Application {app_no}'s {label} is not held in your SIS register. It "
        f"exists only in the source application extract (appl_log_urban / "
        f"{field_key}), which this assistant does not query. I can give you the "
        f"survey number, patta number, area (sq.m), land type, sub-divisions, "
        f"status and field visit.")


# Columns of `uareg` that `build_app_tables.py` reads for the
# geography / area / patta / land-type projection but does NOT carry into
# `survey_numbers` / `sub_divisions`. Unlike the owner extract these are largely
# POPULATED in the source (soil ~540 rows, tax_per_hectare / total_tax / the
# flags all 1033, remarks ~800, form6 ~490), so the honest answer is "held in
# the source parcel register, not in this assistant's projection" -- not "blank
# everywhere". Without this a question about one of them routes to
# `survey_detail` and gets the generic parcel card (area / land type / patta /
# sub-divisions), which silently omits the field asked -- a confident
# non-answer, the same failure `_UNTRACKED_OWNER_FIELDS` fixes for the owner
# extract. Cues are specific: bare "remarks" is left out (it belongs to five
# other handlers -- order/SIS/SD/auto/proposed remarks).
_UNTRACKED_PARCEL_FIELDS = [
    ("soil_type", "soil type / soil classification", [
        "soil type", "soil_type", "primary soil", "secondary soil",
        "soil class", "soil classification", "type of soil",
        "மண் வகை", "மண் வகைப்பாடு", "மண் இனம்"]),
    ("irrigation_source", "irrigation source", [
        "irrigation source", "irrigation_source", "source of irrigation",
        "irrigation type", "irrigation", "நீர்ப்பாசன ஆதாரம்", "பாசன ஆதாரம்",
        "நீர்ப்பாசனம்"]),
    ("double_crop", "double-crop flag", [
        "double crop", "double-crop", "double_crop", "second crop",
        "two crops", "இரட்டைப் பயிர்", "இருபயிர்"]),
    ("partition_indicator", "partition indicator", [
        "partition indicator", "partition_indicator", "partition flag",
        "பிரிவினை குறியீடு", "பாகப்பிரிவினை குறியீடு"]),
    ("government_priority", "government-priority flag", [
        "government priority", "government-priority", "government_priority",
        "govt priority", "priority parcel", "priority flag",
        "அரசு முன்னுரிமை"]),
    ("tax_rate", "tax rate / tax rate code", [
        "tax rate", "tax_rate", "tax rate code", "rate of tax",
        "வரி விகிதம்"]),
    ("tax_per_hectare", "tax per hectare", [
        "tax per hectare", "tax/hectare", "tax per ha", "வரி ஹெக்டேர்"]),
    ("total_tax", "total land tax", [
        "total tax", "total_tax", "total land tax", "land tax amount",
        "மொத்த வரி"]),
    ("municipal_tax", "municipal tax", [
        "municipal tax", "municipal_tax", "corporation tax", "நகராட்சி வரி"]),
    ("land_use", "land-use code / descriptive land use", [
        "land use code", "land_use_code", "land use", "land-use", "landuse",
        "descriptive land use", "நில பயன்பாடு", "நில பயன்பாட்டு குறியீடு"]),
    ("extent_breakdown", "extent unit and extent value 1 / 2", [
        "extent unit", "extent_unit", "extent value 1", "extent value 2",
        "extent_value_1", "extent_value_2", "acre-cent", "hectare-are",
        "பரப்பு அலகு"]),
    ("old_survey_number", "old / original survey number", [
        "old survey number", "old survey no", "old_survey_number",
        "original survey number", "previous survey number", "பழைய சர்வே எண்"]),
    ("old_subdivision_number", "old sub-division number", [
        "old subdivision number", "old sub-division number",
        "old_subdivision_number", "previous subdivision number",
        "பழைய உட்பிரிவு எண்"]),
    ("form_number", "Form 6 / 7 / 8 number", [
        "form 6 number", "form6 number", "form-6 number", "form6_number",
        "form 7 number", "form7 number", "form-7 number", "form7_number",
        "form 8 number", "form8 number", "form-8 number", "form8_number",
        "படிவம் 6", "படிவம் 7", "படிவம் 8"]),
    ("relinquishment_number", "relinquishment number", [
        "relinquishment number", "relinquishment_number", "relinquishment no"]),
    # "assignment number" also sits in _UNTRACKED_OWNER_FIELDS, which is checked
    # first but only fires with an application number in view; a survey-scoped
    # ask falls through to here.
    ("assignment_number", "assignment number", [
        "assignment number", "assignment_number", "assignment no"]),
    ("alienation_number", "alienation number", [
        "alienation number", "alienation_number", "alienation no"]),
    ("acquisition_number", "acquisition number", [
        "acquisition number", "acquisition_number", "acquisition no",
        "கையகப்படுத்தல் எண்"]),
    ("assessed_flag", "assessed flag", [
        "assessed flag", "assessed_flag", "assessment flag", "is it assessed",
        "assessed", "is the survey assessed", "is this parcel assessed"]),
    ("cultivable_flag", "cultivable flag", [
        "cultivable flag", "cultivable_flag", "cultivable", "cultivability",
        "is the land cultivable", "பயிரிடத்தக்க"]),
    ("door_number", "door number / new door number", [
        "door number", "door_number", "door no", "new door number",
        "new_door_number", "கதவு எண்"]),
    ("street_code", "street code", [
        "street code", "street_code", "street no", "தெரு குறியீடு"]),
    ("coss_block_code", "COSS block code", [
        "coss block code", "coss_block_code", "coss block"]),
    ("parcel_remarks", "parcel-register remarks", [
        "parcel remarks", "parcel register remarks", "register remarks",
        "remarks1", "remarks 1", "descriptive remark code",
        "descriptive_remark_code",
        "remarks on survey", "remarks for survey", "remarks of survey",
        "remarks on the survey", "remarks on this survey", "remarks on parcel",
        "remarks on the parcel"]),
]


def _asked_untracked_parcel_field(message_lower: str) -> Optional[str]:
    """The `uareg` column a pointed survey question is about,
    when it is one the ORM projection drops. None otherwise."""
    for key, _label, cues in _UNTRACKED_PARCEL_FIELDS:
        if any(c in message_lower for c in cues):
            return key
    return None


# Things officers ask about a survey number that the register does not hold at all
# (neither this layer nor the source parcel register carries them).
_ABSENT_PARCEL_FIELDS = [
    ("GPS coordinates / latitude and longitude", [
        "gps", "coordinate", "latitude", "longitude", "lat long", "geo location",
        "geolocation", "map location", "google map", "அட்சரேகை", "தீர்க்கரேகை"]),
    ("market value / guideline value / land price", [
        "market value", "guideline value", "land value", "land price",
        "price of survey", "price of the land", "price of this land",
        "cost of the land", "cost of survey", "சந்தை மதிப்பு", "வழிகாட்டி மதிப்பு"]),
]

_SQM_PER_UNIT = {
    "acre": (4046.8564224, "acres"), "cent": (40.468564224, "cents"),
    "hectare": (10000.0, "hectares"), "sq.ft": (0.09290304, "sq.ft"),
    "ground": (222.9, "grounds"),
}
_AREA_UNIT_RE = re.compile(
    r"\b(acres?|cents?|hectares?|sq\.?\s*ft|sqft|square\s+feet|grounds?)\b|ஏக்கர்|சென்ட்|ஹெக்டேர்")


def _asked_absent_parcel_field(message_lower: str):
    for label, cues in _ABSENT_PARCEL_FIELDS:
        if any(c in message_lower for c in cues):
            return label
    return None


def _asked_area_units(message_lower: str) -> list:
    out = []
    for m in _AREA_UNIT_RE.finditer(message_lower):
        w = m.group(0)
        key = ("acre" if "acre" in w or "ஏக்கர்" in w else
               "cent" if "cent" in w or "சென்ட்" in w else
               "hectare" if "hectare" in w or "ஹெக்டேர்" in w else
               "ground" if "ground" in w else "sq.ft")
        if key not in out:
            out.append(key)
    return out


async def _survey_attribute_answer(db, officer, message: str, survey_ref: str, is_tamil: bool):
    """Answers about one survey number that need a little arithmetic or a flag the
    generic survey card does not spell out: the area in acres / cents / hectares,
    and whether it is flagged encroached. None when the message asks neither."""
    low = message.lower()
    units = _asked_area_units(low)
    asks_enc = bool(re.search(r"encroach|ஆக்கிரமிப்பு", low))
    # one field of the parcel: "patta number of survey 120", "area of survey 120", "how many sub divisions does it have"
    one = None
    if not re.search(r"\b(?:details?|everything|all|full|complete|summary|about)\b|விவரம்", low):
        if re.search(r"\bpatta\b|பட்டா", low):
            one = "patta"
        elif re.search(r"\b(?:sub[\s-]?divisions?|subdivisions?)\b.{0,25}\b(?:how\s+many|count|number|have|has)\b|\bhow\s+many\b.{0,25}\bsub[\s-]?divisions?\b|எத்தனை\s*உட்பிரிவு", low):
            one = "subdiv"
        elif re.search(r"\bland\s*(?:type|use)\b|நில\s*வகை", low):
            one = "landtype"
        elif re.search(r"\blitigation\b|\bcourt\s+case\b|வழக்கு", low):
            one = "litigation"
        elif re.search(r"\b(?:area|extent|size)\b|பரப்", low) and not units:
            one = "area"
    if not units and not asks_enc and not one:
        return None
    sd = await get_survey_detail(db, survey_ref, officer)
    if not sd.get("found"):
        return (f"கணக்கெண் {survey_ref} உங்கள் அதிகார எல்லையில் பதிவில் இல்லை." if is_tamil
                else f"Survey {survey_ref} is not on record in your jurisdiction.")
    parts = []
    if one:
        sref = survey_ref
        if one == "patta":
            v = sd.get("patta_number")
            parts.append((f"கணக்கெண் {sref}-ன் பட்டா எண்: {v}." if is_tamil else f"The patta number of survey {sref} is {v}.") if v
                         else (f"கணக்கெண் {sref}-க்கு பட்டா எண் பதிவில் இல்லை." if is_tamil else f"No patta number is recorded for survey {sref}."))
        elif one == "landtype":
            v = sd.get("land_type")
            parts.append((f"நில வகை: {v}." if is_tamil else f"The land type of survey {sref} is {v}.") if v
                         else f"No land type is recorded for survey {sref}.")
        elif one == "litigation":
            parts.append(f"Survey {sref} " + ("is flagged for litigation in the register." if sd.get("has_litigation")
                                               else "has no litigation flagged in the register."))
        elif one == "subdiv":
            subs = sd.get("sub_divisions") or []
            nums = ", ".join(str(x.get("sub_division_no")) for x in subs)
            parts.append((f"கணக்கெண் {sref}-க்கு {len(subs)} உட்பிரிவு: {nums}." if is_tamil
                          else f"Survey {sref} has {len(subs)} sub-division(s): {nums}.") if subs else f"No sub-division is recorded for survey {sref}.")
        elif one == "area":
            sqm = float(sd.get("total_area_sqm") or 0)
            parts.append((f"Survey {sref} has a total area of {sqm:,.2f} sq.m." if sqm
                          else f"The total area of survey {sref} is not recorded in the register (it holds 0 sq.m)."))
    if units:
        sqm = float(sd.get("total_area_sqm") or 0)
        conv = []
        for u in units:
            per, name = _SQM_PER_UNIT[u]
            conv.append(f"{sqm / per:,.4f} {name}".replace(".0000 ", " "))
        if is_tamil:
            parts.append(f"கணக்கெண் {survey_ref}-ன் மொத்த பரப்பளவு {sqm:,.2f} ச.மீ ("
                         + ", ".join(conv) + ").")
        else:
            parts.append(f"Survey {survey_ref} has a total area of {sqm:,.2f} sq.m ("
                         + ", ".join(conv) + ").")
    if asks_enc:
        flagged = bool(sd.get("has_encroachment"))
        if is_tamil:
            parts.append(f"கணக்கெண் {survey_ref}-ல் ஆக்கிரமிப்பு "
                         + ("குறிக்கப்பட்டுள்ளது." if flagged else "எதுவும் குறிக்கப்படவில்லை."))
        else:
            parts.append(f"Survey {survey_ref} " + (
                "is flagged as encroached in the register." if flagged
                else "has no encroachment flagged in the register."))
    return " ".join(parts)


def _absent_parcel_field_answer(survey_ref: str, label: str, is_tamil: bool) -> str:
    if is_tamil:
        return (f"கணக்கெண் {survey_ref}-க்கான {label} எந்தப் பதிவேட்டிலும் இல்லை. "
                f"பட்டா எண், பரப்பளவு, நில வகை, உட்பிரிவுகள், ஆக்கிரமிப்பு / வழக்கு நிலை "
                f"ஆகியவற்றை என்னால் தர முடியும்.")
    return (f"No {label} is recorded for survey {survey_ref} in the register. "
            f"I can give the patta number, the area, the land type, the sub-divisions "
            f"and the encroachment / litigation flags.")


def _untracked_parcel_field_answer(survey_no: str, field_key: str,
                                   is_tamil: bool) -> str:
    label = next(l for k, l, _c in _UNTRACKED_PARCEL_FIELDS if k == field_key)
    ref = f" {survey_no}" if survey_no else ""
    if is_tamil:
        return (
            f"கணக்கெண்{ref}-ன் {label} உங்கள் SIS பதிவேட்டில் இல்லை. அது மூல "
            f"நகர்ப்புற நிலப் பதிவேட்டில் (uareg / {field_key}) "
            f"உள்ளது — இந்த உதவியாளர் அந்த அடுக்கை வினவுவதில்லை. கணக்கெண், "
            f"பட்டா எண், பரப்பளவு (ச.மீ), நில வகை, உட்பிரிவுகள், ஆக்கிரமிப்பு / "
            f"வழக்கு நிலை ஆகியவற்றை என்னால் தர முடியும்.")
    return (
        f"The {label} for survey{ref} is not held in your SIS register. It lives "
        f"in the source parcel register (uareg / {field_key}), "
        f"which this assistant's projection does not carry. What I can give you "
        f"for a survey number: the patta number, the area (sq.m), the land type, "
        f"the sub-divisions, and the encroachment / litigation flags.")


# Columns of the parcel / owner digital-signature extracts
# (`uaregmap_ds` = uaregmap_ds.csv,
#  `uchitta_nathammap_ds` = uchitta_nathammap_ds.csv). These hold
# the X.509 / PKCS#7 DSC blob applied to the cadastral parcel and natham-chitta
# records; `build_app_tables.py` projects NONE of them into layer 2, so
# `get_application_detail()` never returns them. A pointed question about one
# used to misroute badly:
#   "document hash"          -> check_documents  ("All required documents are present.")
#   "signed datetime"        -> _decision_date_answer  (the approval date -- wrong record)
#   "date of verify"         -> submission_date field-map key  (the filing date)
#   "nic dsign flag" / "digital signature content" / "form 8 number"
#                            -> the generic "I could not find that detail" dump
# Every one is a confident non-answer. Answer deterministically instead: name
# the field, say which layer it lives in (or that it is blank in the source),
# and point at the patta-order signature the register DOES carry
# (`decision_date` / `decision_by`). Two kinds:
#   "source"  -- the value exists in the layer-1 signature extract only
#   "blank"   -- the column is empty on every row of the source extract too
# `form8_number` is its own kind: no `applications` row carries a Form 8 number.
# Runs at the same early point as the owner / parcel / transfer families, before
# intent branching, so it also pre-empts the `check_documents` misroute. Needs
# an application or survey reference for the question to be about a record.
# Order matters: a more specific cue precedes a shorter one it contains.
_UNTRACKED_SIGNATURE_FIELDS = [
    ("digital_signature_content", "raw digital-signature (PKCS#7) content", "source", [
        "digital signature content", "digital_signature_content",
        "signature content", "signature_content", "signature blob",
        "raw signature", "pkcs7", "pkcs#7", "certificate content",
        "signature certificate", "கையொப்ப உள்ளடக்கம்"]),
    ("document_hash", "signed-document hash", "source", [
        "document hash", "document_hash", "doc hash", "hash of the document",
        "hash of the signed document", "signature hash", "signed hash",
        "ஆவண ஹாஷ்", "ஆவணக் குறியீட்டு எண்"]),
    ("username_verify", "digital-signature verifier username", "blank", [
        "username verify", "username_verify", "verifier username",
        "who verified the signature", "who verified the digital signature",
        "who verified the dsc", "who verified its signature",
        "who verified its digital signature", "verified its signature",
        "verified its digital signature", "signature verified by",
        "dsc verified by", "verified by whom", "கையொப்பம் சரிபார்த்தவர்"]),
    ("dateofverify", "digital-signature verification date", "blank", [
        "date of verify", "dateofverify", "date of verification",
        "verification date", "verified datetime", "verified_datetime",
        "verified date time", "when was the signature verified",
        "when was the digital signature verified", "when was the dsc verified",
        "signature verified on", "கையொப்ப சரிபார்ப்பு தேதி"]),
    ("nic_dsign_flag", "NIC digital-signature flag", "blank", [
        "nic dsign flag", "nic_dsign_flag", "nic dsign", "nic sign flag",
        "nic digital signature flag", "nic digital signature",
        "digital signature flag", "is the digital signature verified",
        "is the digital signature valid", "is the signature verified",
        "is the signature valid", "is the dsc valid", "signature validity",
        "என்ஐசி டிஜிட்டல் கையொப்பம்"]),
    ("signed_datetime", "digital-signature timestamp (when the DSC was applied)", "source", [
        "signed datetime", "signed_datetime", "signed date time",
        "signed date-time", "signing timestamp", "signature timestamp",
        "digitally signed on", "digital signature applied",
        "signature applied", "when was the dsc applied", "dsc applied",
        "dsc timestamp", "பதிவு கையொப்ப நேரம்"]),
    ("signer_username", "digital-signature signer username", "source", [
        "signature username", "signer username", "signed by username",
        "dsc username", "username of the signer", "username that signed",
        "which username signed", "கையொப்பமிட்ட பயனர்பெயர்"]),
    ("form8_number", "Form 8 number", "form8", [
        "form 8 number", "form8 number", "form-8 number", "form8_number",
        "form 8a number", "form8a number", "form-8a number", "form 8",
        "form-8", "படிவம் 8"]),
]


def _asked_untracked_signature_field(message_lower: str) -> Optional[str]:
    """The parcel / owner digital-signature column a pointed question is about,
    when it is one the ORM projection drops (or leaves blank). None otherwise."""
    for key, _label, _kind, cues in _UNTRACKED_SIGNATURE_FIELDS:
        if any(c in message_lower for c in cues):
            return key
    return None


def _untracked_signature_field_answer(ref: str, field_key: str, is_tamil: bool) -> str:
    label, kind = next((l, k) for key, l, k, _c in _UNTRACKED_SIGNATURE_FIELDS
                       if key == field_key)
    ref_txt = f" {ref}" if ref else ""
    if kind == "form8":
        if is_tamil:
            return (
                f"{ref_txt} — படிவம் 8 எண் உங்கள் SIS பதிவேட்டில் இல்லை. "
                f"பதிவேட்டில் உள்ள எந்த விண்ணப்பத்திற்கும் படிவம் 8 எண் "
                f"கிடையாது; மூல கையொப்ப அட்டவணையின் (uaregmap_ds) "
                f"form8_number நெடுவரிசை முழுவதும் காலியாக உள்ளது. அனுமதிக்கும் "
                f"போது வலய நிலை தாசில்தார் பட்டா மாற்ற ஆணையை உருவாக்குகிறார் — "
                f"முடிவு தேதி, கையொப்பமிட்டவர், பட்டா எண், நிலை ஆகியவற்றை "
                f"என்னால் தர முடியும்.")
        return (
            f"There is no Form 8 number for{ref_txt}. No application in your SIS "
            f"register carries one — the source signature extract's "
            f"`form8_number` column is empty on every row. The patta transfer "
            f"order is generated by the Zonal Level Tahsildar on approval; I can "
            f"give you the decision date, who signed it, the patta number and "
            f"the status.")
    if is_tamil:
        where_ta = (
            "மூல பட்டா / நத்தம்-சிட்டா கையொப்ப அட்டவணையில் "
            "(uaregmap_ds / uchitta_nathammap_ds) மட்டுமே "
            "உள்ளது — இந்த உதவியாளர் அந்த அடுக்கை வினவுவதில்லை"
            if kind == "source" else
            "மூல கையொப்ப அட்டவணையின் ஒவ்வொரு வரிசையிலும் காலியாக உள்ளது; "
            "SIS பதிவேடு DSC சரிபார்ப்பை பதிவு செய்வதில்லை")
        return (
            f"{ref_txt} — {label} உங்கள் SIS பதிவேட்டில் இல்லை. அது {where_ta}. "
            f"பட்டா ஆணையின் தாசில்தார் கையொப்ப தேதி மற்றும் கையொப்பமிட்டவர் "
            f"(முடிவு தேதி / முடிவு செய்தவர்), பட்டா எண், விண்ணப்ப நிலை "
            f"ஆகியவற்றை என்னால் தர முடியும்.")
    where_en = (
        "It exists only in the source parcel / natham-chitta digital-signature "
        "extract (uaregmap_ds / uchitta_nathammap_ds), which "
        "this assistant's projection does not carry"
        if kind == "source" else
        "That column is blank on every row of the source signature extract and "
        "is not projected — the SIS register does not track DSC verification")
    return (
        f"The {label} for{ref_txt} is not held in your SIS register. {where_en}. "
        f"For the patta order I can give you the Tahsildar signature date and "
        f"signer (the decision date / decision by), the patta number, and the "
        f"application status.")


# Columns of the NISD / ISD application-info extracts
# (full_field_patta_transfer_application_information / sub_div_patta_transfer_application_information_urban) that
# `build_app_tables.py` reads but does NOT project into the ORM `applications`
# / `applicants` rows. A pointed question about one used to fall through to the
# LLM/agent -- or, for "challan date" and "documents received", get fuzzy-matched
# to `challan_number` / `submission_date` and answered WRONG with confidence.
# Answer deterministically instead, ahead of the fuzzy field map.
# Order matters: a more specific cue is listed before a shorter one it contains.
_UNTRACKED_APPINFO_FIELDS = [
    ("document_received_date", "document received date", [
        "document received date", "document_received_date", "documents received on",
        "documents received date", "when were the documents received",
        "when the documents were received", "ஆவணம் பெறப்பட்ட தேதி"]),
    ("document_sent_date", "document sent / dispatch date", [
        "document sent date", "document_sent_date", "documents sent on",
        "documents sent date", "documents dispatched", "dispatch date",
        "when were the documents sent", "ஆவணம் அனுப்பிய தேதி"]),
    ("physical_verification_status", "physical verification status", [
        "physical verification status", "physical_verification_status",
        "physical verification", "physically verified",
        "நேரடி சரிபார்ப்பு நிலை", "நேரில் சரிபார்"]),
    ("challan_date", "challan date", [
        "challan date", "challan_date", "date of the challan", "date of challan",
        "சலான் தேதி"]),
    ("treasury_name", "treasury name", [
        "treasury name", "treasury_name", "which treasury", "treasury branch",
        "கருவூலம்", "கஜானா"]),
    ("bank_branch", "bank branch", [
        "bank branch", "bank_branch", "branch name", "which branch",
        "வங்கி கிளை"]),
    ("bank_name", "bank name", [
        "bank name", "bank_name", "which bank", "name of the bank",
        "வங்கி பெயர்"]),
    ("barcode_flag", "barcode flag", [
        "barcode flag", "barcode_flag", "barcode", "பார்கோடு"]),
    ("enclosure_certificate", "enclosure certificate", [
        "enclosure certificate", "enclosure_certificate", "இணைப்பு சான்றிதழ்"]),
    ("enclosure_details", "enclosure details", [
        "enclosure details", "enclosure_details", "list of enclosures",
        "what enclosures", "enclosures attached", "இணைப்பு விவரம்"]),
    ("document_page_scan", "scanned page images (first / reverse / last page)", [
        "first page document", "first_page_document", "reverse page document",
        "reverse_page_document", "last page document", "last_page_document",
        "scanned page", "page scan", "scanned image of the application",
        "application scan copy", "scan copy of the application",
        "ஸ்கேன் செய்யப்பட்ட பக்கம்"]),
    ("return_status", "return status", [
        "return status", "return_status", "returned status", "was it returned",
        "has it been returned", "was returned", "was this returned",
        "was the application returned", "been returned", "sent back",
        "was it sent back", "was sent back",
        "திரும்பிய நிலை", "திருப்பி அனுப்பப்பட்டதா", "திருப்பி அனுப்பப்பட்டதா?"]),
    ("owner_correction_reason", "owner-correction reason", [
        "owner correction reason", "owner_correction_reason",
        "name correction reason", "correction reason",
        "உரிமையாளர் திருத்த காரணம்"]),
    ("auto_mutated_flag", "auto-mutated flag", [
        "auto mutated flag", "auto_mutated_flag", "auto mutation flag",
        "auto-mutation flag", "igrs auto mutation", "auto mutated",
        "auto mutation", "தானியங்கி மாற்ற கொடி", "தானாக மாற்றப்பட்ட"]),
    ("proposed_remarks", "proposed remarks", [
        "proposed remarks", "proposed_remarks", "proposal remarks",
        "முன்மொழியப்பட்ட குறிப்பு"]),
    # The ORM stores `current_status` as the WORD ("approved"), never the source
    # numeric code (01/02/03/05). A question that asks for the CODE specifically
    # has no answer in the register -- say so rather than repeat the word.
    ("application_status_code", "numeric application-status code", [
        "application status code", "application_status_code", "status code",
        "status_code", "numeric status", "status number", "code of the status",
        "விண்ணப்ப நிலை குறியீடு", "நிலை குறியீடு"]),
    # The rejection REASON text is answerable (from the closing workflow hop);
    # the numeric `rejection_reason_code` is not projected.
    ("rejection_reason_code", "rejection-reason code", [
        "rejection reason code", "rejection_reason_code", "reason code",
        "reason_code", "rejection code", "code for the rejection",
        "நிராகரிப்பு காரண குறியீடு", "காரண குறியீடு"]),
    ("sro_code", "Sub-Registrar Office (SRO) code", [
        "sro code", "sro_code", "sub registrar office code",
        "sub-registrar office code", "sub registrar code", "registrar office code",
        "சார்பதிவாளர் அலுவலக குறியீடு", "எஸ்ஆர்ஓ குறியீடு"]),
    # `applicants` carries the applicant's own mobile only; the appinfo extract's
    # separate `relative_mobile_number` column is not projected.
    ("relative_mobile_number", "relative's / guardian's mobile number", [
        "relative mobile number", "relative_mobile_number", "relative's mobile",
        "relatives mobile", "relative mobile no", "relative phone number",
        "relative's phone", "father's mobile", "mother's mobile",
        "guardian mobile", "guardian's mobile", "உறவினர் மொபைல்",
        "உறவினர் தொலைபேசி"]),
]


def _asked_untracked_appinfo_field(message_lower: str) -> Optional[str]:
    """The NISD/ISD application-info column a pointed question is about, when it
    is one the ORM projection drops. None otherwise."""
    for key, _label, cues in _UNTRACKED_APPINFO_FIELDS:
        if any(c in message_lower for c in cues):
            return key
    return None


def _untracked_appinfo_field_answer(app_no: str, field_key: str, is_tamil: bool) -> str:
    label = next(l for k, l, _c in _UNTRACKED_APPINFO_FIELDS if k == field_key)
    if is_tamil:
        return (
            f"விண்ணப்பம் {app_no}-ன் {label} தகவல் உங்கள் SIS பதிவேட்டில் "
            f"சேமிக்கப்படவில்லை. அது மூல விண்ணப்பத் தகவல் பிரிவில் "
            f"(nisd_/sub_div_patta_transfer_application_information_urban / {field_key}) மட்டுமே உள்ளது "
            f"— இந்த உதவியாளர் அந்த அடுக்கை வினவுவதில்லை. விண்ணப்பதாரர் விவரம், "
            f"கட்டணம் மற்றும் சலான் எண், கட்டண முறை, ஆவண நிலை, கள ஆய்வு "
            f"ஆகியவற்றை என்னால் தர முடியும்.")
    return (
        f"Application {app_no}'s {label} is not held in your SIS register. It "
        f"exists only in the source application-info extract "
        f"(nisd_/sub_div_patta_transfer_application_information_urban / {field_key}), which this "
        f"assistant does not query. I can give you the applicant details, the "
        f"fee and challan number, the payment mode, the document status and the "
        f"field visit.")


# "how was X submitted?", "which channel?", "was it a revenue camp?", "camp
# flag?", "csc / operator name?" -- the submission channel and its two
# derivation columns (`applications.submission_channel` /
# `submission_source_name` / `submission_camp_flag`) ARE projected and
# `get_application_detail()` returns them, but the `application_status` field
# map carried no cue for them -- so a pointed question fell to "I could not find
# that particular detail" on a file that plainly has a channel. Route these to
# `_render_submission_channel_answer`, which already words CSC / citizen /
# Sub-Registrar and (on a "why / how do you know" phrasing) shows the basis.
_SUBMISSION_CHANNEL_FIELD_RE = re.compile(
    r"\bcamp[\s_]*flag\b|\brevenue\s+camp\b|\bat\s+a\s+camp\b|\bspecial\s+camp\b"
    r"|\bsubmission\s+channel\b|\bwhich\s+channel\b|\bwhat\s+channel\b"
    r"|\bchannel\s+(?:of|for|is|was|did|was\s+it)\b|\bfiling\s+channel\b"
    r"|\bhow\s+(?:was|were|did)\b[^?]{0,60}?\b(?:submitted|filed|lodged|received|"
    r"come\s+in|reach)\b"
    r"|\bsubmitted\s+(?:through|via|from|at)\b|\bfiled\s+(?:through|via|from|at)\b"
    r"|\bcsc\s+name\b|\boperator\s+(?:name|code)\b|\bvle\s+(?:name|code)\b"
    r"|\bcounter\s+(?:name|code)\b|\bwho\s+submitted\b"
    r"|சமர்ப்பித்த\s*முறை|எந்த\s*வழி(?:யாக)?|முகாம்\s*கொடி|சிஎஸ்சி\s*பெயர்"
    r"|எப்படி\s*சமர்ப்பிக்கப்பட்டது",
    re.IGNORECASE,
)


def _asked_submission_channel_field(message_lower: str) -> bool:
    return bool(_SUBMISSION_CHANNEL_FIELD_RE.search(message_lower or ""))


# Columns of the TAMILNILAM geography masters (`town`, `ward`, `block` in the
# TAMILNILAM dumps) that `build_app_tables.py` does NOT project into the ORM
# `towns` / `wards` / `blocks` rows -- those keep only the id chain, the code
# and the English name. A pointed question about one of the master's own
# housekeeping columns (`town_flag`, `town_status`, `igrs_mutation`,
# `igrs_am_date`, `nic_dsign`, `updn_dt`, `user_no`, the ward `freeze_*`
# columns, `settlement_status` / `settlement_flag`, `next_patta_no`, the block
# `old_*` codes) used to be swallowed by
# `jurisdiction_summary` -- which detects the bare word "town" and answers
# "Your town is Thoothukudi.", never acknowledging the attribute asked -- or
# misrouted to `application_status`, which then asks for an application number
# a town question does not have. Both are confident non-answers. Answer
# deterministically instead: name the field, say the SIS register does not
# carry it, and point at what IS held (the name and the code). This fires only
# when the message also names a geography level (`town` / `ward` / `block` /
# `taluk` / `village`), so a bare "what is a digital signature" is left alone.
# Order matters: a more specific cue precedes a shorter one it contains.
_UNTRACKED_GEO_MASTER_FIELDS = [
    ("town_flag", "town flag", [
        "town flag", "town_flag", "நகர கொடி", "நகரக் கொடி"]),
    ("town_status", "town status", [
        "town status", "town_status", "நகர நிலை", "நகரத்தின் நிலை"]),
    ("igrs_mutation", "IGRS mutation flag", [
        "igrs mutation", "igrs_mutation", "igrs mutation flag",
        "igrs mutation enabled", "ஐஜிஆர்எஸ் மாற்றம்"]),
    ("igrs_am_date", "IGRS auto-mutation date", [
        "igrs am date", "igrs_am_date", "igrs auto mutation date",
        "igrs auto-mutation date", "ஐஜிஆர்எஸ் தேதி"]),
    ("nic_dsign", "NIC digital-signature flag", [
        "nic digital signature", "nic dsign", "nic_dsign", "nic sign flag",
        "digital signature flag", "என்ஐசி டிஜிட்டல் கையொப்பம்"]),
    # `freeze_dates` is checked BEFORE `freeze_flag`: its cues ("freeze start",
    # "freeze end") contain the word "freeze", so a bare-"freeze" flag cue would
    # otherwise claim a dates question first.
    ("freeze_dates", "ward freeze start / end dates", [
        "freeze start", "freeze end", "freeze_start_date", "freeze_end_date",
        "freeze period", "freeze duration", "when does the freeze",
        "when the freeze", "how long is the freeze", "freeze start and end",
        "முடக்க தேதி", "முடக்க காலம்"]),
    ("freeze_flag", "ward freeze / transaction-freeze flag", [
        "freeze flag", "freeze_flag", "ward freeze", "freeze status",
        "transaction freeze", "txn freeze", "frozen", "is the freeze",
        "under freeze", "revocation", "block/revoke", "முடக்க கொடி",
        "முடக்கப்பட்ட", "முடக்கம்"]),
    ("settlement_status", "settlement status / settlement flag", [
        "settlement status", "settlement_status", "settlement flag",
        "settlement_flag", "under settlement", "in settlement",
        "settlement done", "settlement complete", "is settlement",
        "settled ward", "settled block", "தீர்வை நிலை", "தீர்வை கொடி"]),
    ("next_patta_no", "ward / block next-patta-number counter", [
        "next patta number", "next patta no", "next_patta_no", "next patta",
        "next available patta", "patta number counter", "patta counter",
        "அடுத்த பட்டா எண்"]),
    ("old_codes", "old (superseded) taluk / town / block code", [
        "old taluk code", "old town code", "old block code", "old_taluk_code",
        "old_town_code", "old_block_code", "previous block code",
        "superseded code", "பழைய குறியீடு"]),
    ("dept_codes", "treasury (TO) / drawing-and-disbursing-officer (DDO) code", [
        "ddo code", "ddo_code", "drawing and disbursing officer code",
        "drawing & disbursing officer code", "to_code", "treasury officer code",
        "treasury code", "கருவூல குறியீடு", "வரைவு மற்றும் வழங்கல் அலுவலர் குறியீடு"]),
    ("updn_dt", "master-record last-updated timestamp", [
        "record last updated", "record was last updated", "record updated",
        "master record updated", "record last modified", "updn_dt",
        "when was the town record", "when was the ward record",
        "when was the block record", "பதிவு புதுப்பிக்கப்பட்ட"]),
    ("user_no", "master-record user number", [
        "user number", "user_no", "user no", "which user created",
        "which user number created", "who created the record",
        "who last updated the", "who updated the", "last updated by",
        "updated by which user", "பயனர் எண்"]),
]

_GEO_MASTER_LEVEL_CUES = (
    "town", "ward", "block", "taluk", "taluq", "village", "district",
    "நகர", "நகரம்", "வார்டு", "பிளாக்", "தாலுகா", "கிராம", "மாவட்ட",
)


# Cues specific enough on their own that requiring a geography-level word
# alongside them ("ward", "block", ...) only lost real questions instead of
# preventing false ones -- officers ask about these in APPLICATION terms
# ("does this application have a settlement status?", "what is the next
# patta number for it?"), not ward/block terms. A live sweep of ~500
# untracked-field questions found that without a geo-level word these fell
# through to the generic application field map, which answered with a
# WRONG but real-looking field instead: settlement_status got the
# application's own current_status ("approved"), next_patta_no got the
# survey's EXISTING patta number, igrs_mutation got igrs_form6_number, and
# updn_dt got the application's own last_updated_datetime -- four different
# confident wrong-field substitutions, not a refusal. The remaining fields
# in this family keep the geo-level requirement: their cues ("town flag",
# "town status", "user number") are short enough to plausibly collide with
# an ordinary question.
_GEO_MASTER_SELF_SUFFICIENT = {
    "igrs_mutation", "igrs_am_date", "settlement_status", "next_patta_no", "updn_dt",
}


def _asked_untracked_geo_master_field(message_lower: str) -> Optional[str]:
    """The geography-master column a pointed question is about, when it is one
    the ORM projection drops. Most of this family also requires the message to
    name a geography level, to stay clear of an ordinary application question
    that happens to share a short, generic cue word -- see
    `_GEO_MASTER_SELF_SUFFICIENT` for the cues specific enough not to need it.

    A message shaped like a command to CHANGE one of these ("freeze ward 102",
    "set the next patta number to 500") is left to the mutation-refusal path --
    the two chat paths check that path and this one in opposite order, so the
    bail here keeps them consistent."""
    if _is_mutation_request(message_lower):
        return None
    # "Block/Revoke Town Survey Number" (0163) opens with the exact phrase
    # the freeze_flag cue list uses for an imperative ("block/revoke [a
    # ward]") -- a message that names a real service code is asking about
    # that service, never the geography master's own housekeeping columns.
    from backend.utils.helpers import find_service_codes as _ugm_find_codes
    if _ugm_find_codes(message_lower):
        return None
    has_geo_level = any(c in message_lower for c in _GEO_MASTER_LEVEL_CUES)
    for key, _label, cues in _UNTRACKED_GEO_MASTER_FIELDS:
        if not any(c in message_lower for c in cues):
            continue
        if has_geo_level or key in _GEO_MASTER_SELF_SUFFICIENT:
            return key
    return None


def _untracked_geo_master_field_answer(field_key: str, is_tamil: bool) -> str:
    label = next(l for k, l, _c in _UNTRACKED_GEO_MASTER_FIELDS if k == field_key)
    if is_tamil:
        if field_key == "old_codes":
            label = "பழைய (மாற்றப்பட்ட) தாலுகா / நகர / பிளாக் குறியீடு"
        return (
            f"{label} தகவல் உங்கள் SIS பதிவேட்டில் இல்லை. நகரம் / வார்டு / "
            f"பிளாக் ஆகியவற்றின் TAMILNILAM மூல அட்டவணையில் மட்டுமே அது உள்ளது "
            f"— இந்த உதவியாளர் அந்த அடுக்கை வினவுவதில்லை. நகரத்தின் பெயர் "
            f"மற்றும் குறியீடு, வார்டு எண், பிளாக் எண் ஆகியவற்றை என்னால் தர "
            f"முடியும்.")
    return (
        f"The {label} is not held in your SIS register. It exists only in the "
        f"TAMILNILAM town / ward / block master, which this assistant does not "
        f"query — the projection keeps only the name and the code. I can give "
        f"you the town name and code, the ward number and the block number.")


# Columns of the natham-chitta / IGRS owner extracts
# (`uchitta_natham`, `full_field_patta_transfer_igrs_owner`) that
# `build_app_tables.py` does NOT project into the ORM `owners` /
# `survey_ownership` rows. Most are empty in the source too:
# `ration_card_number`, `epic_no`, `occupation_code`, `assignment_number`,
# `user_no` carry 0 non-null values across all 551 natham-chitta rows;
# `cin_no` / `pin_code` carry the literal '0' on every populated row;
# `door_number` carries 7. `own_num` (owner serial) and `rel_num` are
# populated in the source but still not projected -- the register exposes the
# owner's name, relative, relationship, Aadhaar last-4 and share, not these
# raw counters. A pointed question about any of them used to be swallowed by
# the `survey_owners` intent, which
# dumped every owner on the parcel and never acknowledged the field asked --
# a confident non-answer. Answer deterministically instead: name the field,
# say it is not in the register, and point at what IS held. Runs BEFORE the
# `survey_owners` dump and the fuzzy field map.
# Order matters: a more specific cue precedes a shorter one it contains
# ("ration card number" before "ration card").
_UNTRACKED_OWNER_FIELDS = [
    ("ration_card_number", "owner's ration card number", [
        "ration card number", "ration card no", "ration_card_number",
        "ration card", "ரேஷன் கார்டு எண்", "குடும்ப அட்டை எண்", "ரேஷன் கார்டு",
        "குடும்ப அட்டை"]),
    ("voter_id_number", "owner's voter ID / EPIC number", [
        "voter id number", "voter id no", "voter_id_number", "voter id",
        "voter identity", "epic number", "epic no", "epic_no", "epic",
        "election id", "வாக்காளர் அடையாள எண்", "வாக்காளர் எண்", "வாக்காளர் அடையாள",
        "வாக்காளர் அட்டை"]),
    ("pin_code", "owner's PIN code", [
        "pin code", "pincode", "pin_code", "postal code", "post code",
        "zip code", "அஞ்சல் குறியீடு", "பின் கோடு", "பின்கோடு"]),
    # `cin_no` -- the natham-chitta extract's citizen-identification-number
    # column. Present but pure garbage: the literal '0' on every populated row,
    # blank on the rest, and never projected. Without this cue a question like
    # "what is the CIN number of the owner of <app>?" carried the word "owner"
    # and was swallowed by the `survey_owners` dump. Cues are specific -- never
    # bare "cin", which is a substring of ordinary words.
    ("cin_no", "owner's citizen identification number (CIN)", [
        "cin number", "cin no", "cin_no", "citizen identification number",
        "citizen id number", "citizen identification", "cin of the owner",
        "owner's cin", "குடிமகன் அடையாள எண்", "சி.ஐ.என் எண்", "சிஐஎன் எண்"]),
    ("occupation_code", "owner's occupation code", [
        "occupation code", "occupation_code", "occupation of the owner",
        "owner's occupation", "owner occupation", "தொழில் குறியீடு",
        "உரிமையாளர் தொழில்"]),
    ("relation_code", "owner's relation / relationship code", [
        "relation code", "relationship code", "relation_code",
        "relationship_code", "உறவு குறியீடு", "உறவுமுறை குறியீடு"]),
    ("relation_no", "owner's relation number", [
        "relation number", "relation no", "relation_no", "rel num", "rel_num",
        "உறவு எண்"]),
    # `relationship` (s/o, w/o, d/o) IS now projected: build_app_tables.py maps
    # uchitta_natham.relationship_code -> owners.relationship_type
    # (5->s/o, 4->w/o, 6->d/o; code 0 stays NULL). get_survey_owners returns it
    # and _owner_detail_line renders it beside the relative name, so a plain
    # "owner's relationship" question is answered, not deflected. Only the raw
    # numeric `relation_code` / `relation_no` above remain unexposed.
    ("owner_status", "owner status", [
        "owner status", "owner_status", "owner's status", "owners status",
        "status of the owner", "உரிமையாளர் நிலை", "உரிமையாளரின் நிலை"]),
    ("uds_details", "undivided share (UDS) details", [
        "uds details", "uds_details", "uds detail", "undivided share details",
        "undivided share detail", "undivided share", "uds share",
        "பிரிக்கப்படாத பங்கு விவரம்", "பிரிக்கப்படாத பங்கு"]),
    ("extent", "extent held by the owner", [
        "extent of the owner", "owner's extent", "owners extent", "owner extent",
        "extent for the owner", "extent held by", "owner's holding extent",
        "உரிமையாளர் பரப்பு", "உரிமையாளரின் பரப்பளவு"]),
    ("owner_no", "owner serial number (owner_no)", [
        "owner number", "owner no", "owner_no", "owner serial number",
        "own num", "own_num", "உரிமையாளர் எண்", "உரிமையாளர் வரிசை எண்"]),
    ("door_number", "owner's door number", [
        "door number", "door no", "door_number", "house number", "house no",
        "கதவு எண்", "வீட்டு எண்"]),
    ("assignment_number", "owner's assignment number", [
        "assignment number", "assignment no", "assignment_number",
        "ஒதுக்கீட்டு எண்", "assignment எண்"]),
    ("user_number", "owner-row user number", [
        "user number", "user no", "user_no", "user_number",
        "user no of the owner", "user_no of the owner", "பயனர் எண்"]),
]


# Typo-tolerant fallback for _asked_untracked_owner_field. Hand-picked pairs of
# distinctive content words that co-occur ONLY in the question they name, so a
# misspelling of either ("raton card", "occuption code", "reltion code") still
# lands. Deliberately NOT auto-derived from the cue list: single weak tokens
# like "number" / "status" would false-match ordinary questions.
_UOF_FUZZY = [
    ("ration_card_number", ("ration", "card")),
    ("occupation_code", ("occupation", "code")),
    ("relation_code", ("relation", "code")),
    # ("relation_no", ("relation", "number")) removed: "relation" typo-matches
    # "relative", so "relative's mobile NUMBER" (an appinfo column) was being
    # claimed as the owner's relation number. The exact cues still cover
    # "relation number" / "relation no" / "rel num".
    ("cin_no", ("citizen", "identification")),
    ("assignment_number", ("assignment", "number")),
    ("uds_details", ("undivided", "share")),
]


def _asked_untracked_owner_field(message_lower: str) -> Optional[str]:
    """The natham-chitta / IGRS owner column a pointed question is about, when it
    is one the ORM projection drops (and the source usually leaves blank). None
    otherwise.

    Substring match first (fast, exact), then a narrow typo-tolerant fallback so
    "raton card", "occuption code", "reltion code" land where they should --
    the same edit-distance rule `parse_intent` uses elsewhere.
    """
    for key, _label, cues in _UNTRACKED_OWNER_FIELDS:
        if any(c in message_lower for c in cues):
            return key
    msg_tokens = extract_tokens(message_lower)
    if not msg_tokens:
        return None
    for key, sig in _UOF_FUZZY:
        if all(any(is_token_typo_match(mt, want) for mt in msg_tokens)
               for want in sig):
            return key
    return None


# Personal attributes an officer might ask for that are in NO land record --
# not a projection gap, just not the kind of thing the register holds. Without
# this, "owner's caste / religion / age / email for <app>" fell through to the
# `survey_owners` dump (every owner on the parcel) and never said so. Closed
# list, matched as substrings, so there are no false positives on real fields.
_UNKNOWN_OWNER_ATTRS = [
    ("caste", ["caste", "sub caste", "sub-caste", "community", "ஜாதி", "சாதி", "இனம்"]),
    ("religion", ["religion", "மதம்"]),
    ("age", ["owner's age", "owners age", "age of the owner", "owner age",
             "வயது"]),
    ("date of birth", ["owner's dob", "owner's date of birth", "dob of the owner",
                       "date of birth of the owner", "owner date of birth"]),
    ("email address", ["email", "e-mail", "e mail", "மின்னஞ்சல்"]),
    ("marital status", ["marital status", "married or", "is the owner married",
                        "திருமண நிலை"]),
    ("annual income", ["owner's income", "owners income", "income of the owner",
                       "annual income of the owner", "வருமானம்"]),
    ("educational qualification", ["owner's education", "owners education",
                                   "educational qualification of the owner",
                                   "owner's qualification", "கல்வித் தகுதி"]),
    ("blood group", ["blood group", "இரத்த வகை"]),
    ("nationality", ["nationality", "citizenship of the owner", "குடியுரிமை"]),
]


def _asked_unknown_owner_attr(message_lower: str) -> Optional[str]:
    """A personal attribute of the owner that no land record carries. None
    otherwise. Requires the word 'owner' so it never claims an applicant/general
    question."""
    if "owner" not in message_lower:
        return None
    for label, cues in _UNKNOWN_OWNER_ATTRS:
        if any(c in message_lower for c in cues):
            return label
    return None


def _unknown_owner_attr_answer(app_no: str, label: str, is_tamil: bool) -> str:
    ref = f" {app_no}" if app_no else ""
    if is_tamil:
        return (
            f"விண்ணப்பம்{ref}-ன் உரிமையாளரின் {label} SIS பதிவேட்டில் இல்லை — "
            f"இது நில பரிமாற்றப் பதிவேடு, தனிநபர் விவரக் கோப்பு அல்ல. உரிமையாளர் "
            f"பெயர் (தமிழ்/ஆங்கிலம்), தொடர்புடையவர் பெயர் மற்றும் உறவுமுறை, ஆதார் "
            f"கடைசி 4 இலக்கம், உரிமைப் பங்கு, பாலினம் (பதிவு இருந்தால்), பட்டா எண் "
            f"ஆகியவற்றை மட்டுமே அது வைத்திருக்கிறது.")
    return (
        f"The owner's {label} is not held for application{ref}. The SIS record is "
        f"a land-mutation register, not a personal profile — for an owner it "
        f"holds only the name (Tamil/English), the relative's name and "
        f"relationship, the Aadhaar last 4 digits, the ownership share, gender "
        f"where recorded, and the patta number.")


def _untracked_owner_field_answer(app_no: str, field_key: str, is_tamil: bool) -> str:
    label = next(l for k, l, _c in _UNTRACKED_OWNER_FIELDS if k == field_key)
    ref = f" {app_no}" if app_no else ""
    if is_tamil:
        return (
            f"விண்ணப்பம்{ref}-ன் {label} உங்கள் SIS பதிவேட்டில் இல்லை. அது மூல "
            f"உரிமையாளர் பட்டியல்களில் (natham_chitta_owner / igrs_owner / "
            f"transfer_*_owner / {field_key}) மட்டுமே உள்ளது — இந்த உதவியாளர் அந்த அடுக்கை "
            f"வினவுவதில்லை, மேலும் அந்த நெடுவரிசை பெரும்பாலான வரிசைகளில் "
            f"காலியாகவே உள்ளது. உரிமையாளர் பெயர் (தமிழ்), தொடர்புடையவர் பெயர், "
            f"ஆதார் கடைசி 4 இலக்கம், உரிமைப் பங்கு, பட்டா எண் ஆகியவற்றை "
            f"என்னால் தர முடியும்.")
    return (
        f"The {label} for application{ref} is not held in your SIS register. It "
        f"exists only in the source owner extracts (natham_chitta_owner / "
        f"igrs_owner / transfer_*_owner / {field_key}), which this assistant "
        f"does not query — and that column is blank on most rows there too. What "
        f"I can give you for an owner: the name (Tamil), the relative's name and "
        f"relationship (s/o, w/o, d/o), gender where recorded, the last 4 digits "
        f"of the Aadhaar, the ownership share, and the patta number.")


# Columns of the patta-transfer / registration detail extracts
# (`nisd_/sub_div_patta_transfer_urban`) that `build_app_tables.py` does NOT
# project into `patta_transfers`. The reason, registration place / date, old
# patta, transfer order, and the SIS's own recommendation / remarks ARE
# projected now and answered from the field map -- these are the rest. A
# pointed question about one is answered here (named, and told what IS held)
# instead of falling to the LLM or a generic detail dump.
# Order matters: a longer cue precedes a shorter one it contains.
_UNTRACKED_TRANSFER_FIELDS = [
    ("succession_certificate_number", "succession certificate number / date / issuer", [
        "succession certificate number", "succession certificate date",
        "succession certificate issued by", "succession certificate",
        "வாரிசுச் சான்றிதழ்", "வாரிசு சான்றிதழ்"]),
    ("legal_heir_certificate_issue_place", "legal-heir certificate issue place", [
        "legal heir certificate", "legal-heir certificate", "legal heir cert",
        "சட்டப்பூர்வ வாரிசு சான்றிதழ்"]),
    # Sub-division sketch milestone dates. `sub_div_patta_transfer_urban` carries
    # `sketch_sent_date` (31/50) and `sketch_received_date` (0/50); neither is
    # projected. Without this the field map sends "when was the sketch sent" to
    # `submission_date` and answers the filing date.
    ("sketch_sent_date", "sub-division sketch sent / received-back date", [
        "sketch sent", "sketch was sent", "sent the sketch", "sketch dispatch",
        "sketch sent date", "sketch_sent_date", "sketch received", "sketch back",
        "sketch returned", "received the sketch", "sketch received date",
        "sketch_received_date", "when was the sketch", "sketch to the draughtsman",
        "sketch to sd", "வரைபடம் அனுப்பிய", "வரைபடம் திரும்ப",
        "வரைபடம் பெறப்பட்ட"]),
    # Only the SIS desk's recommendation / remarks / reason are projected. The
    # DIS (district) and Tahsildar desks each have their own columns in the ISD
    # transfer extract; unguarded, "district recommendation" fuzzy-matched
    # `district_name` and "tahsildar recommendation" answered the SIS one.
    ("district_recommendation", "DIS / district recommendation, remarks and reason", [
        "district recommendation reason", "district recommendation",
        "district remarks", "district remark", "dis recommendation",
        "dis remarks", "deputy inspector recommendation",
        "மாவட்ட பரிந்துரை", "மாவட்ட குறிப்பு"]),
    ("tahsildar_recommendation", "Tahsildar recommendation, remarks and reason", [
        "tahsildar recommendation reason", "tahsildar recommendation",
        "tahsildar remarks", "tahsildar remark", "zdt recommendation",
        "தாசில்தார் பரிந்துரை", "தாசில்தார் குறிப்பு"]),
    # Workflow milestone dates from the ISD transfer extract -- not projected,
    # and every one of them would otherwise fuzzy-match "when"/"date"/"received"
    # to the submission date.
    ("surveyor_completed_date", "surveyor received / completed date", [
        "surveyor received date", "surveyor received on", "surveyor completed date",
        "surveyor completion date", "when did the surveyor receive",
        "when did the surveyor complete", "surveyor_received_date",
        "surveyor_completed_date"]),
    ("tahsildar_receipt_date", "Tahsildar receipt date", [
        "tahsildar receipt date", "tahsildar received", "tahsildar received on",
        "when did the tahsildar receive", "tahsildar_receipt_date",
        "தாசில்தார் பெற்ற தேதி"]),
    ("incorporation_date", "incorporation / handover / commissioner-change date", [
        "incorporation date", "incorporated on", "handover received date",
        "handover date", "commissioner change date", "incorporation_date",
        "handover_received_date", "commissioner_change_date"]),
    ("form8a_number", "Form 8A number", [
        "form 8a", "form8a", "form 8 a", "form 8-a", "8a number", "படிவம் 8அ"]),
    ("court_order_number", "court order number / date / court name", [
        "court order number", "court order date", "court order no", "court name",
        "which court", "court_order", "நீதிமன்ற ஆணை எண்", "நீதிமன்ற ஆணை",
        "நீதிமன்றத்தின் பெயர்"]),
    ("extent_value", "transferred extent (ha / are / sq.m breakdown)", [
        "extent transferred", "transferred extent", "extent of transfer",
        "extent value", "extent_value", "extent in ares", "extent_in_ares",
        "purchased area", "purchased_area", "area transferred", "area purchased",
        "மாற்றப்பட்ட பரப்பளவு", "வாங்கிய பரப்பளவு"]),
    ("new_door_number", "new / old door number", [
        "new door number", "old door number", "door number", "door no",
        "புதிய கதவு எண்", "பழைய கதவு எண்", "கதவு எண்"]),
    ("new_total_tax", "new / old total tax", [
        "new total tax", "old total tax", "total tax", "house tax", "property tax",
        "tax amount", "வரி தொகை", "மொத்த வரி"]),
    ("tax_rate", "tax rate", [
        "tax rate", "tax_rate", "rate of tax", "வரி விகிதம்"]),
    ("land_category_code", "land category code", [
        "land category code", "land category", "land_category_code",
        "நில வகைப்பாடு குறியீடு", "நில வகைப்பாடு"]),
    ("land_type_code", "land type code (numeric)", [
        "land type code", "land_type_code", "land type number",
        "நில வகை குறியீடு"]),
    ("direct_transfer_flag", "direct-transfer flag", [
        "direct transfer flag", "direct transfer", "direct_transfer",
        "is it a direct transfer", "நேரடி மாற்றம்"]),
    ("transaction_code", "transaction code", [
        "transaction code", "transaction_code", "txn code", "பரிவர்த்தனை குறியீடு"]),
    ("group_survey_number", "group survey / sub-division / patta number", [
        "group survey number", "group subdivision number", "group patta number",
        "group survey", "குழு கணக்கெண்"]),
    ("owner_correction_reason", "owner-correction reason", [
        "owner correction reason", "owner_correction_reason",
        "correction reason", "உரிமையாளர் திருத்தக் காரணம்"]),
    ("street_code", "street code / old street code", [
        "street code", "street_code", "old street code", "தெரு குறியீடு"]),
    ("slno", "extract serial number (slno / sno)", [
        "slno", "sl no", "sno", "extract serial", "row serial",
        "பதிவேற்று வரிசை எண்"]),
]


def _asked_untracked_transfer_field(message_lower: str) -> Optional[str]:
    """The transfer-detail extract column a pointed question is about, when it
    is one the ORM projection drops. None otherwise."""
    for key, _label, cues in _UNTRACKED_TRANSFER_FIELDS:
        if any(c in message_lower for c in cues):
            return key
    return None


def _registration_detail_answer(detail: dict, kind: str, app_no: str,
                                is_tamil: bool, message: str = "") -> str:
    """Where / when the deed was registered, or why the parcel was transferred,
    from get_application_detail()'s projected patta-transfer fields."""
    place = detail.get("registration_place")
    date = detail.get("registration_date")
    reason = detail.get("transfer_reason") or detail.get("declared_reason")
    deed = detail.get("sale_deed_number")
    # A file with no completed transfer has not been "transferred" -- it was
    # FILED as a mutation of that kind. Only patta_transfers.transfer_reason
    # (not the application's declared_reason) means a transfer actually happened.
    _done = bool(detail.get("transfer_reason")) or (
        (detail.get("current_status") or detail.get("status")) in ("approved", "completed"))
    _v_en = "was transferred on" if _done else "was filed as a mutation of type"
    _v_ta = "மாற்றப்பட்டது —" if _done else "இந்த வகை மாற்றத்திற்காக தாக்கல் செய்யப்பட்டது —"
    if kind == "docnum":
        if not deed:
            return (f"விண்ணப்பம் {app_no}-க்கு பதிவு ஆவண எண் பதிவேட்டில் இல்லை."
                    if is_tamil else
                    f"No registration document number is recorded for application {app_no}.")
        return (f"விண்ணப்பம் {app_no}-ன் பதிவு ஆவண எண்: {deed}"
                f"{(' (' + place + ', ' + str(date) + ')') if (place and date) else ''}."
                if is_tamil else
                f"The registration document number for application {app_no} is {deed}"
                f"{(' (registered at ' + place + ' on ' + str(date) + ')') if (place and date) else ''}.")
    if kind == "place":
        if not place:
            return (f"விண்ணப்பம் {app_no}-க்கு பதிவு அலுவலகம் பதிவேட்டில் இல்லை."
                    if is_tamil else
                    f"No registration office is recorded for application {app_no}.")
        extra = (f", பதிவு ஆவணம் {deed}" if deed else "")
        return (f"விண்ணப்பம் {app_no}-ன் ஆவணம் {place}-இல் பதிவு செய்யப்பட்டது"
                f"{(' (' + str(date) + ')') if date else ''}{extra}." if is_tamil else
                f"The deed for application {app_no} was registered at {place}"
                f"{(' on ' + str(date)) if date else ''}"
                f"{(', document ' + deed) if deed else ''}.")
    if kind == "date":
        if not date:
            return (f"விண்ணப்பம் {app_no}-க்கு பதிவு தேதி பதிவேட்டில் இல்லை."
                    if is_tamil else
                    f"No registration date is recorded for application {app_no}.")
        return (f"விண்ணப்பம் {app_no}-ன் ஆவணம் {date} அன்று பதிவு செய்யப்பட்டது"
                f"{(' (' + place + ')') if place else ''}. "
                f"(இது சமர்ப்பித்த தேதி அல்ல.)" if is_tamil else
                f"The deed for application {app_no} was registered on {date}"
                f"{(' at ' + place) if place else ''}. "
                f"(This is the deed's registration date, not the filing date.)")
    # reason
    if not reason:
        return (f"விண்ணப்பம் {app_no}-க்கு மாற்றக் காரணம் பதிவேட்டில் இல்லை."
                if is_tamil else
                f"No transfer reason is recorded for application {app_no}.")
    named = _named_transfer_reason((message or "").lower())
    if named:
        # normalise Tamil transfer_reason values to their English label
        _rl = (reason or "").strip().lower()
        _matches = (named.lower() in _rl) or (
            _rl in ("விற்பனை ஆவணம்/ கிரைய ஆவணம்",) and named == "Sale deed") or (
            _rl in ("ஏற்பாடு/ செட்டில்மெண்டு ஆவணம்",) and named == "Settlement deed") or (
            _rl in ("விடுதலை ஆவணம்",) and named == "Release deed") or (
            _rl in ("தான ஆவணம்",) and named == "Gift deed") or (
            _rl in ("பாகப்பிரிவினை ஆவணம்",) and named == "Partition deed") or (
            _rl in ("வாரிசு உரிமை",) and named == "Legal Heir") or (
            _rl in ("நீதிமன்ற ஆணை",) and named == "Court Order /Judgement")
        if _matches:
            return (f"ஆம் — விண்ணப்பம் {app_no} {_v_ta} {reason}"
                    f"{(' (பதிவு ஆவணம் ' + deed + ')') if deed else ''}." if is_tamil else
                    f"Yes — application {app_no} {_v_en} {reason}"
                    f"{(' (registered deed ' + deed + ')') if deed else ''}.")
        return (f"இல்லை — விண்ணப்பம் {app_no} {_v_ta} {reason}, "
                f"நீங்கள் குறிப்பிட்டது அல்ல." if is_tamil else
                f"No — application {app_no} {_v_en} {reason}, "
                f"not what you named.")
    return (f"விண்ணப்பம் {app_no} {_v_ta} {reason}"
            f"{(' (பதிவு ஆவணம் ' + deed + ')') if deed else ''}." if is_tamil else
            f"Application {app_no} {_v_en} {reason}"
            f"{(' (registered deed ' + deed + ')') if deed else ''}.")


def _untracked_transfer_field_answer(app_no: str, field_key: str, is_tamil: bool) -> str:
    label = next(l for k, l, _c in _UNTRACKED_TRANSFER_FIELDS if k == field_key)
    ref = f" {app_no}" if app_no else ""
    if is_tamil:
        return (
            f"விண்ணப்பம்{ref}-ன் {label} உங்கள் SIS பதிவேட்டில் எடுக்கப்படவில்லை. அது "
            f"மூல பதிவு/மாற்ற விவர அடுக்கில் (nisd_/sub_div_patta_transfer_urban / "
            f"{field_key}) மட்டுமே உள்ளது — இந்த உதவியாளர் அதை வினவுவதில்லை, மேலும் "
            f"அந்த நெடுவரிசை பெரும்பாலான வரிசைகளில் காலியாகவே உள்ளது. மாற்றத்திற்கு "
            f"என்னால் தர முடிந்தவை: மாற்றக் காரணம், பதிவு இடம் மற்றும் தேதி, பதிவு "
            f"ஆவண எண், பழைய/புதிய பட்டா எண், மாற்ற ஆணை எண்/தேதி/குறிப்பு, SIS "
            f"பரிந்துரை மற்றும் குறிப்புகள்.")
    return (
        f"The {label} for application{ref} is not carried in your SIS register. "
        f"It exists only in the source registration/transfer detail extract "
        f"(nisd_/sub_div_patta_transfer_urban / {field_key}), which this assistant "
        f"does not query — and that column is blank on most rows there too. For "
        f"a transfer I can give you: the transfer reason, the registration place "
        f"and date, the registered deed number, the old / new patta number, the "
        f"transfer order number / date / remarks, and the SIS's own "
        f"recommendation and remarks.")


# The register stores parcel area only in square metres. When the officer asks
# for hectares / ares / acres (all three are columns of the source extract),
# the field map matched the bare word "area" and answered the sq.m figure with
# an "Area (sq.m)" label -- the wrong unit, presented as if it were the answer.
# Convert deterministically instead and show the working.
_AREA_UNIT_CUES = (
    ("hectare", ("hectare", "hectares", " ha ", " ha?", "hect", "ஹெக்டேர்", "எக்டேர்")),
    ("acre", ("acre", "acres", "ஏக்கர்")),
    ("ares", ("ares", "in are", "in ares", "ஆர்")),
)
_AREA_UNIT_DIVISOR = {"hectare": 10000.0, "ares": 100.0, "acre": 4046.856}
_AREA_UNIT_NAME = {"hectare": ("hectares", "ஹெக்டேர்"),
                   "ares": ("ares", "ஆர்"), "acre": ("acres", "ஏக்கர்")}


def _asked_area_in_unit(message_lower: str) -> Optional[str]:
    if not any(w in message_lower for w in (
            "area", "extent", "பரப்பு", "பரப்பளவு", "விஸ்தீரண")):
        return None
    for unit, cues in _AREA_UNIT_CUES:
        if any(c in message_lower for c in cues):
            return unit
    return None


def _area_unit_answer(sd: dict, app_no: str, unit: str, is_tamil: bool) -> Optional[str]:
    sqm = sd.get("area_sqm") or sd.get("survey_total_area_sqm")
    if not sqm:
        return None
    try:
        sqm = float(sqm)
    except (TypeError, ValueError):
        return None
    val = sqm / _AREA_UNIT_DIVISOR[unit]
    en, ta = _AREA_UNIT_NAME[unit]
    if is_tamil:
        return (f"விண்ணப்பம் {app_no}-ன் பதிவு செய்யப்பட்ட பரப்பளவு {sqm:g} ச.மீ, "
                f"அதாவது ≈ {val:.4g} {ta}. (பதிவேட்டில் ச.மீ மட்டுமே உள்ளது; "
                f"{ta} மதிப்பு கணக்கிடப்பட்டது.)")
    return (f"{app_no}'s recorded area is {sqm:g} sq.m, i.e. ≈ {val:.4g} {en}. "
            f"(Only sq.m is stored in your register; the {en} value is derived.)")


def _missing_field_answer(sd: dict, app_no: str, field_key: str,
                          field_label: str, is_tamil: bool) -> str:
    """Why a field is empty, when the emptiness is a fact rather than a gap.

    "No igrs form 6 number information found" reads as a hole in the record.
    An IGRS Form 6 number is raised only where the Sub-Registrar's IGRS builds
    the mutation off a registered deed, so a CSC or citizen file has none by
    definition, and saying so answers the question instead of dodging it.
    """
    if field_key == "igrs_form6_number":
        channel = sd.get("submission_channel")
        route = {"CSC": "a CSC counter",
                 "citizen": "the citizen (a revenue camp)"}.get(channel)
        if is_tamil:
            base = (f"{app_no}-க்கு IGRS படிவம் 6 எண் இல்லை. "
                    f"சார்பதிவாளர் (IGRS) மூலம் வரும் விண்ணப்பங்களுக்கு மட்டுமே "
                    f"அந்த எண் உண்டு")
            return base + (f"; இந்த கோப்பு {route} வழியாக வந்தது." if route else ".")
        base = (f"{app_no} has no IGRS Form 6 number. Only an application "
                f"referred by the Sub-Registrar carries one -- it names the "
                f"registered deed the mutation was raised from")
        return base + (f", and this file came in through {route}." if route else ".")
    if is_tamil:
        return f"{app_no} க்கு {field_label} தகவல் இல்லை."
    return f"No {field_label.lower()} information found for {app_no}."


def _sub_division_numbers(app) -> tuple:
    """The (temporary, final) sub-division numbers of an application, rendered.

    The temporary number ("3/T1") is assigned while the file is in the workflow;
    the final one ("4") replaces it only when the sub-division is confirmed, so a
    still-open or rejected file has no final number to show.
    """
    temps, finals = [], []
    for sd in (getattr(app, "application_sub_divisions", None) or []):
        if sd.temporary_sub_division_no:
            temps.append(sd.temporary_sub_division_no)
        if sd.proposed_sub_division_no and sd.proposed_sub_division_no != sd.temporary_sub_division_no:
            finals.append(sd.proposed_sub_division_no)
    return (", ".join(temps) or "N/A", ", ".join(finals) or "Not assigned")


def _render_submission_channel_basis(sd: dict, is_tamil: bool) -> str:
    """Show the working behind `applications.submission_channel`.

    The channel is derived once, when `appl_log_urban` is projected, by
    `can_channel()` in backend/sample_db/identifiers.py, from two columns:
    `source_name` (a placeholder `-` means no operator account touched the file,
    so it is the Sub-Registrar's unattended IGRS referral; an operator / VLE code
    means someone keyed it in at a counter) and `camp_flag` (`P` on an attended
    row marks a special revenue camp, where the operator files for the citizen
    standing in front of them). Everything else attended is a CSC
    counter. The CAN's length names the counter that ISSUED the number, not the
    channel, so it is reported as a corroborating detail and never as the reason.
    """
    channel = sd.get("submission_channel")
    if channel not in ("CSC", "citizen", "sub_registrar"):
        return ""
    app_no = sd.get("application_number") or "N/A"
    src = sd.get("submission_source_name")
    can = sd.get("can_number")
    form6 = sd.get("igrs_form6_number")

    who = (src or "").strip()
    if is_tamil:
        head = {
            "CSC": f"விண்ணப்பம் {app_no} -- பொது சேவை மையம் (CSC) கோப்பு.",
            "citizen": f"விண்ணப்பம் {app_no} -- குடிமகனின் சொந்த சமர்ப்பிப்பு.",
            "sub_registrar": f"விண்ணப்பம் {app_no} -- சார்-பதிவாளர் (SRO) கோப்பு.",
        }[channel]
        if channel == "sub_registrar":
            body = ("இதை யாரும் உள்ளிடவில்லை; பதிவு செய்யப்பட்ட விற்பனைப் பத்திரத்திலிருந்து பதிவு அமைப்பே "
                    "அனுப்பியது."
                    + (f" அதன் பதிவு குறிப்பு எண் (IGRS படிவம் 6): {form6}." if form6 else ""))
        elif channel == "citizen":
            body = ("சிறப்பு வருவாய் முகாமில் தாக்கல் செய்யப்பட்டது: குடிமகன் முன்னால் நிற்க, ஆபரேட்டர்"
                    + (f" (குறியீடு {who})" if who else "") + " உள்ளிட்டார்; எனவே இது குடிமகனின் சொந்த சமர்ப்பிப்பு.")
        else:
            body = ("CSC கவுண்டரில் ஆபரேட்டர்" + (f" (குறியீடு {who})" if who else "")
                    + " குடிமகன் சார்பாக உள்ளிட்டார்; இது சிறப்பு வருவாய் முகாம் அல்ல.")
        out = [head, body]
        if can:
            out.append(f"CAN எண் {can} ({len(str(can))} இலக்கங்கள்) இதற்கு காரணம் அல்ல -- அது எண்ணை வழங்கிய "
                       "கவுண்டரை மட்டுமே காட்டும், கோப்பு வந்த வழியை அல்ல.")
        return "\n".join(out)

    head = {
        "CSC": f"CSC -- application {app_no} was entered at a Common Service Centre counter.",
        "citizen": f"Citizen -- application {app_no} is the citizen's own submission.",
        "sub_registrar": f"Sub-Registrar (SRO) -- application {app_no} came from the Sub-Registrar.",
    }[channel]
    if channel == "sub_registrar":
        body = ("Nobody typed it in: when the sale deed was registered, the registration system sent "
                "the transfer request to the survey office by itself."
                + (f" Its registration reference number (IGRS Form 6) is {form6}." if form6 else ""))
    elif channel == "citizen":
        body = ("It was filed at a special revenue camp: an operator" + (f" (code {who})" if who else "")
                + " keyed it in while the citizen stood in front of them, so it counts as the citizen's own "
                  "submission rather than a CSC counter's.")
    else:
        body = ("An operator" + (f" (code {who})" if who else "")
                + " at a CSC counter entered it on the citizen's behalf; it was not entered at a special "
                  "revenue camp.")
    out = [head, body]
    if can:
        out.append(f"Its CAN number ({can}, {len(str(can))} digits) is not the reason: the length only shows "
                   "which counter issued the number, not how the file arrived.")
    return "\n".join(out)


def _transfer_party_answer(detail: dict, side: str, message: str,
                           is_tamil: bool) -> str:
    """Render the transferor or transferee of a patta-transfer application."""
    app_no = detail.get("application_id") or detail.get("application_number") or ""
    parties = detail.get("transfer_parties") or []
    label = ("முந்தைய உரிமையாளர் (வழங்குநர்)" if is_tamil else "previous owner (transferor)") \
        if side == "previous" else \
        ("புதிய உரிமையாளர் (பெறுநர்)" if is_tamil else "new owner (transferee)")
    key = "previous_owner" if side == "previous" else "new_owner"
    picked = [(p.get("sub_division_no"), p.get(key)) for p in parties if p.get(key)]
    if not picked:
        if is_tamil:
            return (f"விண்ணப்பம் {app_no}-க்கு பட்டா மாற்ற {label} பதிவு இல்லை. "
                    "இது பட்டா மாற்ற விண்ணப்பமாக இருந்தால் மட்டுமே இந்த விவரம் இருக்கும்.")
        return (f"No {label} is recorded for application {app_no}. "
                "This detail exists only once the file is projected as a patta transfer.")
    ml = (message or "").lower()
    want_aadhaar = any(w in ml for w in ("aadhaar", "aadhar", "adhaar", "ஆதார"))
    want_gender = any(w in ml for w in ("gender", "sex", "male", "female", "பாலின"))
    want_relative = any(w in ml for w in (
        "father", "relative", "husband", "wife", "spouse", "relationship",
        "தந்தை", "கணவ", "மனைவி", "உறவ"))
    lines = []
    for sd_no, o in picked:
        seg = o.get("name") or "N/A"
        extras = []
        if sd_no:
            extras.append((f"உட்பிரிவு {sd_no}" if is_tamil else f"sub-division {sd_no}"))
        if want_relative:
            rn = o.get("relative_name")
            extras.append((f"உறவினர்: {rn}" if is_tamil else f"relative: {rn}") if rn
                          else ("உறவினர் பெயர் SIS உரிமையாளர் பதிவில் இல்லை" if is_tamil
                                else "relative name not in the SIS owner register"))
        if want_aadhaar:
            a4 = o.get("aadhaar_last4")
            extras.append((f"ஆதார் முடிவு {a4}" if is_tamil else f"Aadhaar ending {a4}") if a4
                          else ("ஆதார் எண் பதிவில் இல்லை" if is_tamil
                                else "Aadhaar not on record"))
        if want_gender:
            g = o.get("gender")
            extras.append((f"பாலினம்: {g}" if is_tamil else f"gender: {g}") if g
                          else ("பாலினம் SIS உரிமையாளர் பதிவில் இல்லை" if is_tamil
                                else "gender not recorded in the SIS owner register"))
        if extras:
            seg += " (" + ", ".join(extras) + ")"
        lines.append(seg)
    body = "; ".join(lines)
    if is_tamil:
        ans = f"விண்ணப்பம் {app_no} — {label}: {body}."
    else:
        ans = f"Application {app_no} — {label}: {body}."
    # relationship / ownership share / extent / UDS / photo are not projected.
    if any(w in ml for w in ("ownership share", "share", "extent", "uds",
                             "undivided", "photo", "image", "பங்கு", "பரப்ப")):
        ans += ("\n\nஉரிமைப் பங்கு, பரப்பளவு, UDS விவரம், புகைப்படம் — இவை "
                "நகர்ப்புற natham-chitta / transfer-owner பிரிவில் மட்டுமே உள்ளன, "
                "SIS செயலியின் பதிவில் எடுக்கப்படவில்லை." if is_tamil else
                "\n\nOwnership share, extent, UDS details and photo are not carried "
                "in the projected SIS record for a transfer party -- they live only "
                "in the un-projected transfer-owner extract.")
    return ans


def _sis_recommendation_text(value: str, is_tamil: bool) -> str:
    """The SIS's own recommendation flag on a patta transfer -- "Y"/"N" in the
    extract -- rendered as words."""
    v = (value or "").strip().upper()
    if v in ("Y", "YES", "1", "T"):
        return "பரிந்துரைக்கப்பட்டது (Y)" if is_tamil else "Recommended (Y)"
    if v in ("N", "NO", "0", "F"):
        return "பரிந்துரைக்கப்படவில்லை (N)" if is_tamil else "Not recommended (N)"
    return value


def _render_submission_channel_answer(structured_data: dict, language: str,
                                      message: str = "") -> str:
    """Direct answer to "did this application come from a citizen or a CSC?".

    The channel is a stored fact, derived from appl_log_urban:
    `source_name = '-'` is the Sub-Registrar's unattended IGRS referral, an
    operator code with `camp_flag = 'P'` is a citizen filing at a special camp,
    and any other operator code is a CSC counter.
    """
    is_tamil = language in ("ta", "tanglish")
    if not structured_data or not structured_data.get("found", True):
        return (structured_data or {}).get(
            "message",
            "தயவுசெய்து விண்ணப்ப எண்ணை குறிப்பிடவும்." if is_tamil else
            "Please specify which application you're asking about. "
            "For example: 2026/0154/28/NNNNNN")

    app_no = structured_data.get("application_number") or "N/A"
    channel = structured_data.get("submission_channel")
    can = structured_data.get("can_number")
    form6 = structured_data.get("igrs_form6_number")

    # "why is it CSC?", "how do you know?", "explain in detail" -- the officer
    # is asking for the basis, not the label. Show the two columns the channel
    # was derived from instead of repeating the one-line verdict.
    if _CHANNEL_WHY_RE.search(message or ""):
        detailed = _render_submission_channel_basis(structured_data, is_tamil)
        if detailed:
            return detailed
    can_note = ""
    if can:
        can_note = (f" (CAN {can} — {len(str(can))} இலக்கங்கள்)" if is_tamil
                    else f" (CAN {can} — {len(str(can))} digits)")
    elif channel == "sub_registrar" and form6:
        # a referral with no CAN of its own -- the Form 6 number identifies it
        can_note = (f" (IGRS படிவம் 6 {form6})" if is_tamil
                    else f" (IGRS Form 6 {form6})")

    # "submitted through Sub-Registrar?" / "is this a CSC application?" name the
    # ONE channel being asked about -- a yes/no question, even though it carries
    # no "yes"/"no" word of its own. Answering with the true channel alone (what
    # the three branches below already did) reads as a description, not an
    # answer to the question asked -- the officer has to work out for themself
    # whether a channel that differs from the one named means "no". A bare
    # "how was this submitted?" / "which channel?" names no channel of its own
    # (extract_submission_channel returns None) and is left exactly as before.
    _asked_channels = extract_submission_channels(message or "")
    _asked_channel = _asked_channels[0] if _asked_channels else None
    _yn = ""
    if _asked_channel and not _CHANNEL_WHY_RE.search(message or ""):
        _match = _asked_channel == channel
        _yn = ("ஆம், " if _match else "இல்லை, ") if is_tamil else ("Yes — " if _match else "No — ")

    if channel == "CSC":
        return (f"{_yn}விண்ணப்பம் {app_no} பொது சேவை மையம் (CSC) மூலம் "
                f"சமர்ப்பிக்கப்பட்டது{can_note}." if is_tamil else
                f"{_yn}CSC — application {app_no} was submitted through a Common Service Centre{can_note}.")
    if channel == "citizen":
        return (f"{_yn}விண்ணப்பம் {app_no} குடிமகனால் நேரடியாக இணையதளத்தில் "
                f"சமர்ப்பிக்கப்பட்டது{can_note}." if is_tamil else
                f"{_yn}Citizen — application {app_no} was filed directly by the citizen "
                f"on the portal{can_note}.")
    if channel == "sub_registrar":
        return (f"{_yn}விண்ணப்பம் {app_no} துணை பதிவாளர் அலுவலகத்தின் பரிந்துரை மூலம் "
                f"வந்தது{can_note}." if is_tamil else
                f"{_yn}Sub-Registrar referral — application {app_no} reached the office "
                f"through the Sub-Registrar / IGRS Form 6 route{can_note}.")
    source = structured_data.get("source_name") or structured_data.get("source_code")
    if source:
        return (f"{_yn}விண்ணப்பம் {app_no}-இன் சமர்ப்பிப்பு மூலம்: {source}." if is_tamil
                else f"{_yn}Application {app_no} was submitted via: {source}.")
    return (f"விண்ணப்பம் {app_no}-க்கு சமர்ப்பிப்பு மூலம் பதிவு செய்யப்படவில்லை."
            if is_tamil else
            f"No submission channel is recorded against application {app_no}.")


_OWNER_FIELD_CUES = {
    "relative": ("father", "relative", "husband", "wife", "spouse", "parent",
                 "relationship", "தந்தை", "கணவ", "மனைவி", "பெற்றோர்", "உறவு",
                 "thanthai", "kanavan", "manaivi", "uravu"),
    "aadhaar": ("aadhaar", "aadhar", "ஆதார்", "ஆதார", "aathaar"),
    "gender": ("gender", "sex", "male", "female", "பாலின", "ஆண்", "பெண்",
               "paalinam", "aan", "pen"),
    "mobile": ("mobile", "phone", "contact number", "தொலைபேசி", "கைபேசி",
               "mobile number", "tholaipaesi"),
    "address": ("address", "residence", "door number", "முகவரி", "வீட்டு முகவரி",
                "mugavari"),
    "tamilname": ("in tamil", "tamil name", "தமிழில்", "தமிழ் பெயர்",
                  "tamil la", "tamil peru"),
    "englishname": ("in english", "english name", "name in english",
                    "in roman", "roman script", "romanized", "romanised",
                    "transliterate", "ஆங்கிலத்தில்", "ஆங்கிலத்தில",
                    "english la", "english peru"),
}


def _owner_wants(ml: str, key: str) -> bool:
    return any(w in ml for w in _OWNER_FIELD_CUES[key])


def _owner_detail_line(o: dict, message: str) -> str:
    """One `survey_owners` list line.

    The relative name / Aadhaar / gender / mobile / address are appended ONLY
    when the officer's question asked for that field -- every one is a column on
    the natham-chitta owner row (get_survey_owners._row). A bare "who is the
    father of the owner?" used to fall through to the LLM; a question about a
    field the extract leaves blank (mobile, address, and gender on most rows)
    now gets an explicit "not recorded" rather than a line that ignores it.
    """
    ml = (message or "").lower()
    name = o.get("name", "N/A")
    if _owner_wants(ml, "tamilname") and o.get("name_tamil"):
        name = o["name_tamil"]
    # Owner names are stored in Tamil script. Romanise inline only when the
    # officer explicitly asked for the English form -- a whole list annotated
    # with "(transliteration ~ ...)" on every line would be unreadable.
    elif _owner_wants(ml, "englishname") and isinstance(name, str):
        _rom = translit.tamil_to_latin(name)
        if _rom and _rom != name and translit.script_of(_rom) == "latin":
            name = _rom
    sub_div = o.get("sub_division", "Survey Level")
    share = o.get("ownership_share", "N/A")
    o_type = o.get("ownership_type", "Primary")
    line = f"  • {name} — Sub-division: {sub_div}, Share: {share}, Type: {o_type}"

    extras = []
    if _owner_wants(ml, "relative"):
        if o.get("relative_name"):
            _rel = o.get("relationship")
            extras.append(f"Relative: {_rel + ' ' if _rel else ''}{o['relative_name']}")
        else:
            extras.append("Relative: not recorded in the SIS owner register")
    if _owner_wants(ml, "aadhaar"):
        extras.append(f"Aadhaar ending {o['aadhaar_last4']}" if o.get("aadhaar_last4")
                      else "Aadhaar: not recorded in the SIS owner register")
    if _owner_wants(ml, "gender"):
        g = {"m": "Male", "f": "Female"}.get(str(o.get("gender") or "").lower(),
                                             o.get("gender"))
        extras.append(f"Gender: {g}" if g
                      else "Gender: not recorded in the SIS owner register")
    if _owner_wants(ml, "mobile"):
        extras.append(f"Mobile: {o['mobile']}" if o.get("mobile")
                      else "Mobile: not held for owners in the SIS register")
    if _owner_wants(ml, "address"):
        extras.append(f"Address: {o['address']}" if o.get("address")
                      else "Address: not held for owners in the SIS register")
    if extras:
        line += " — " + ", ".join(extras)
    return line


def _render_joint_owner_answer(sd: dict, language: str) -> str:
    """Human answer for the joint_owner_check intent from summarized data."""
    is_tamil = language in ("ta", "tanglish")
    survey_no = sd.get("survey_no", "N/A")
    app_no = sd.get("application_number")
    if is_tamil:
        prefix = (f"விண்ணப்பம் {app_no} (கணக்கெண் {survey_no})" if app_no
                  else f"கணக்கெண் {survey_no}")
    else:
        prefix = (f"For application {app_no} (Survey {survey_no})" if app_no
                  else f"For Survey {survey_no}")

    names = sd.get("joint_owner_names", [])
    groups = sd.get("joint_subdivisions", {})
    total_owners = sd.get("total_owners", 0)

    # Ambiguity note: a bare survey number that matched parcels in >1 ward.
    wards = sd.get("matched_wards", [])
    amb = ""
    if not app_no and sd.get("matched_parcels", 1) > 1 and len(wards) > 1:
        if is_tamil:
            amb = f" (கணக்கெண் {survey_no} பல வார்டுகளில் உள்ளது: {', '.join(wards)})"
        else:
            amb = f" (survey {survey_no} exists in multiple wards: {', '.join(wards)})"

    # The user named a sub-division that carries no ownership record; the list
    # below is the whole parcel's, so do not pass it off as that sub-division's.
    if sd.get("requested_sub_division") and sd.get("sub_division_found") is False:
        _req = sd.get("requested_sub_division")
        if is_tamil:
            amb += f" (உட்பிரிவு {_req}-க்கு தனி உரிமையாளர் பதிவு இல்லை; கணக்கெண் முழுவதும் காட்டப்படுகிறது)"
        else:
            amb += (f" (no ownership record is held against sub-division {_req}; "
                    f"the whole survey number is shown)")

    if total_owners == 0:
        return (f"{prefix}: உரிமையாளர் பதிவுகள் இல்லை.{amb}" if is_tamil
                else f"{prefix}: No ownership records found.{amb}")
    if not names:
        return (f"{prefix}: விண்ணப்பதாரர் ஒரே உரிமையாளர். கூட்டு உரிமையாளர்கள் இல்லை.{amb}"
                if is_tamil
                else f"{prefix}: The applicant is the sole owner. No joint owners are listed.{amb}")

    if is_tamil:
        head = (f"{prefix}: {len(names)} தனிப்பட்ட கூட்டு உரிமையாளர்கள், "
                f"{len(groups)} உட்பிரிவில்{amb}:")
    else:
        head = (f"{prefix}: {len(names)} distinct joint owner(s) across "
                f"{len(groups)} sub-division(s){amb}:")
    lines = [head]
    for sd_no, sd_names in groups.items():
        lines.append(f"  • {sd_no}: {', '.join(sd_names)}")
    return "\n".join(lines)


_WF_STAGE_LABEL = {
    "SIS": "Sub Inspector Surveyor",
    "SD": "Senior Draughtsman",
    "DIS": "Deputy Inspector Surveyor",
    "TAHSILDAR": "Zonal Level Tahsildar",
    "COMPLETED": "Completed",
    "REJECTED": "Rejected",
}


def _render_workflow_history(structured_data: dict, language: str) -> str:
    """Format the stage-by-stage workflow timeline (not just a count).

    Consumes the `history` list built for the application_status intent:
    each entry has from_stage / to_stage / changed_at / note / changed_by_name.
    """
    hist = structured_data.get("history", []) or []
    app_no = structured_data.get("application_number", "")
    is_tamil = language in ("ta", "tanglish")
    if not hist:
        return (f"விண்ணப்பம் {app_no}-க்கு பணிப்பாய்வு வரலாறு எதுவும் இல்லை."
                if is_tamil else
                f"No workflow history recorded for application {app_no}.")

    header = (f"விண்ணப்பம் {app_no} பணிப்பாய்வு வரலாறு ({len(hist)} படிகள்):"
              if is_tamil else
              f"Workflow history for {app_no} ({len(hist)} step(s)):")
    lines = [header]
    # Collapse consecutive hops that carry the same visible content (role
    # granularity is finer than stage granularity, so 44->42 and 42->41 both
    # read as SIS -> SIS).
    prev_key = None
    step = 0
    for h in hist:
        frm = h.get("from_stage")
        to = h.get("to_stage")
        when = (h.get("changed_at") or "")[:10]
        note = (h.get("note") or "").strip().strip("-").strip()
        who = h.get("changed_by_name") or ""
        key = (frm, to, note)
        if key == prev_key:
            continue
        prev_key = key
        step += 1
        frm_l = _WF_STAGE_LABEL.get(frm, frm) if frm else ("சமர்ப்பிப்பு" if is_tamil else "Submission")
        to_l = _WF_STAGE_LABEL.get(to, to) if to else "?"
        seg = f"  {step}. {when} — {frm_l} → {to_l}"
        if who and who != "System":
            seg += f" · {who}"
        if note:
            seg += f' · "{note[:80]}"'
        lines.append(seg)
    return "\n".join(lines)


def _is_owner_photo_request(message: str) -> bool:
    if _igrs_owner_qa.IMAGE in _igrs_owner_qa.parse(message)["keys"] and re.search(
            r"\b(?:is|are)\s+there\b|\bavai?lable\b|\battached\b", message or "", re.IGNORECASE):
        return False  # "is an owner image available for this record": a field of the IGRS owner row
    m = (message or "").lower()
    # Word-boundary, not bare substring: "pic" (short for "picture") matched
    # inside "EPIC number" -- the voter-ID field -- and "owner" in the same
    # breath ("epic number OF THE OWNER") made every EPIC-number question
    # answer with the owner-photo refusal instead of the field it asked for.
    if not any(re.search(r'(?<![a-z0-9])' + re.escape(w) + r'(?![a-z0-9])', m)
               for w in _OWNER_PHOTO_MEDIA_WORDS):
        return False
    if any(p in m for p in ["owner photo", "owner image", "owner_photo", "owner_image",
                            "owner's photo", "owners photo", "photo of the owner",
                            "photo of owner", "image of the owner"]):
        return True
    return any(w in m for w in _OWNER_PHOTO_SUBJECT_WORDS)


def _owner_photo_reply(language: str, message: str) -> str:
    is_ta = language in ("ta", "tanglish") or any(w in (message or "") for w in ["புகைப்படம்", "படம்", "உரிமையாளர்"])
    if is_ta:
        return (
            "உரிமையாளரின் புகைப்படத்தை (PNG அல்லது வேறு எந்த வடிவத்திலும்) இந்த உதவியாளரால் "
            "வழங்க முடியாது. TAMILNILAM நகர்ப்புற தரவிலிருந்து உருவாக்கப்பட்ட SIS பதிவுகளில் "
            "உரிமையாளர் புகைப்படம் / படம் சேமிக்கப்படவில்லை — மூல `owner_photo` / `owner_image` "
            "புலங்கள் அனைத்தும் `-` அல்லது காலியாக உள்ளன; எந்த PNG/JPG/base64 படமும் இல்லை.\n\n"
            "உரிமையாளர் குறித்து நான் தர முடிந்தவை: பெயர் (தமிழ்/ஆங்கிலம்), ஆதார் எண், CAN, "
            "பட்டா எண், உரிமைப் பங்கு, கூட்டு உரிமையாளர் நிலை, இணைக்கப்பட்ட சர்வே/விண்ணப்பம். "
            "ஆவணங்களுக்கு — பதிவேற்ற நிலை மற்றும் வகையை (எ.கா. Sale Deed, ஆதார்) மட்டுமே தர முடியும், "
            "கோப்புகளை அல்ல."
        )
    return (
        "I can't return an owner's photo as a PNG (or in any other format). The SIS "
        "records built from the TAMILNILAM urban extracts don't store any owner "
        "photograph — the source `owner_photo` / `owner_image` fields are placeholders "
        "(`-` or empty) and no PNG, JPG, or base64 image is held for any owner.\n\n"
        "What I *can* pull up for an owner: name (English & Tamil), Aadhaar number, "
        "CAN, patta number, ownership share, joint-owner status, and the linked "
        "survey number / application. For documents I can report the upload status "
        "and type (e.g. Sale Deed, Aadhaar scan) — but not the files themselves."
    )
