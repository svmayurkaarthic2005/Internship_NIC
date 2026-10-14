"""
Clear/contentless-message detection, field-visit negation/aggregation, workflow-remarks, multi-question splitting (big_plan/prose_plan/multi_app_plan), and owner/record comparison parsing.

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
from backend.services.chat_followups import (
    _followup_is_self_contained,
    _fragment_typo_fix,
    _geo_scope,
)
from backend.services.chat_response_formatting import (
    _apply_followup_resolution,
    _project_field_answer,
    _resolve_prev_intent,
)
from backend.services.chat_shared import (
    _ADD_COLUMNS_RE,
    _ANY_APP_NUMBER_RE,
    _BARE_REF_RE,
    _COL_ADD_RE,
    _COL_DROP_RE,
    _EVIDENCE_SHAPES_RE,
    _FV_STATUS_FRAGMENT_RE,
    _LIST_FOLLOWUP_INTENTS,
    _NEG_LEAD,
    _SORT_OVERRIDE,
    _cm_strong,
    _fetch_visit_plan,
    _has_application_back_reference,
    _has_domain_evidence,
    _is_register_wide_listing,
    _parse_negation,
    _render_all_service_code_answer,
    _requested_service_code_filter,
    _requested_status_filter_for_register,
)
from backend.services.chat_smalltalk import (
    _fv_cols_merge,
    _fv_cols_message,
    _fv_cols_parse,
    _fv_cols_parse_loose,
    _is_capability_question,
)
from backend.utils.logger import get_logger

logger = get_logger(__name__)

# A few callees still live in chatbot.py (the processing pipelines, save_chat_messages,
# and functions kept there so tests can patch their collaborators). chatbot.py imports
# this module, so they are reached through the module object, imported at call time --
# importing it at module load (by name or as a module) would be a circular import.
def _cb():
    from backend.services import chatbot
    return chatbot




def _no_evidence_for_llm(original: str, intent, structured_data, resolved: str = "", ctx=None) -> bool:
    """True when this turn has nothing an LLM answer could be grounded in.

    Never true for a routed intent: the gate may stop an unsupported LLM answer,
    it must not stop a deterministically recognised SIS question.

    `original` is what the officer typed: the pipeline's typo correction turns
    "random words" into "random wards", which would read as evidence. `resolved`
    is the message after follow-up resolution; only an application / survey /
    number shape appended there counts.
    """
    if intent not in (None, "general_query") or structured_data:
        return False
    original = (_RAW_BY_FIXED.get() or {}).get(original, original)   # what was really typed
    # An explicit application or survey number named, with STILL no
    # structured_data at this point, means every deterministic handler in
    # this ~26000-line pipeline and the DB-backed agent already had a
    # chance at this exact entity and found nothing -- the question is
    # genuinely about one record, not a policy/definition question the
    # reference corpus can answer. Reaching the raw LLM prompt from here is
    # exactly how an invented status, date, owner name or survey fact
    # happens: the model is handed a concrete-sounding entity and no real
    # data, and nothing stops it writing a plausible answer anyway. Abstain
    # instead, before `_has_domain_evidence` even runs -- a number is
    # stronger evidence that THIS is the risk than any vocabulary match
    # could excuse.
    if _ANY_APP_NUMBER_RE.search(original or "") or re.search(
            r"\bsurvey\s*(?:no\.?|number)?\s*\d|\bsy\.?\s*\d", original or "", re.IGNORECASE):
        return True
    if _has_domain_evidence(original, ctx):
        return False
    return not (resolved and _EVIDENCE_SHAPES_RE.search(resolved))


def _clarification_reply(language: str) -> str:
    # Under the mandatory Tanglish-to-Tamil policy, both Tamil script and Tanglish
    # inputs receive answers in natural Tamil written in Tamil script.
    if language in ("ta", "tanglish"):
        return ("நீங்கள் என்ன கேட்கிறீர்கள் என்று புரியவில்லை. விண்ணப்பம், நில அளவை எண், சேவை, "
                "கள ஆய்வு அல்லது பணிப்பாய்வு பற்றி கேளுங்கள்.")
    return ("I'm not sure what you mean. Please ask about an application, survey number, "
            "service, field visit, or workflow.")


def _is_unresolved_survey_question(intent: str, message: str, followup_ctx) -> bool:
    """True when `intent` is survey_detail/survey_owners but nothing in
    `message` or `followup_ctx` actually names a survey or application --
    the exact shape "List every survey number mentioned." / "What
    subdivision number belongs to the first record?" matches (confirmed
    live, QA audit 2026-10-13, upload pass): the catch-all keyword rule in
    rag.py's parse_intent() assumes a number will be resolved downstream
    (via `_survey_ref` or an application already in view), so with neither,
    the deterministic DB handler always answered "No records found" -- even
    with an uploaded document in the same session that plainly contained
    the value being asked about.

    This is the pure half of the check only: the caller still has to decide
    whether redirecting the turn (to general_query, so the attachment layer
    or the LLM fallback gets a chance) is actually useful, which depends on
    whether anything is attached -- not testable here without a database.
    Narrow on purpose: any digit in the message, or an application/survey
    already in view, returns False, so a real numbered question or a
    resolvable follow-up is never touched.
    """
    if intent not in ("survey_detail", "survey_owners"):
        return False
    if re.search(r"\d", message or ""):
        return False
    if followup_ctx and (
            followup_ctx.single_application
            or followup_ctx.survey_numbers
            or (followup_ctx.entity == fctx.ENTITY_APPLICATION_LIST
                and len(followup_ctx.application_numbers) == 1)):
        return False
    return True


# A standalone message that states a language preference and nothing else
# ("No Tamil, English only", "Don't explain in Tamil", "Please respond in
# Tamil") carries no question of its own. Reaching the open-ended LLM prompt
# with nothing to answer is exactly what let the model, given rich chat
# history (a previous Tamil-script answer) but no data of its own for this
# turn, copy a stray example line out of that history instead of
# acknowledging the instruction -- in the wrong language, no less. Caught
# here, before any LLM call, in both the "tamil" and the "english" spelling,
# so neither the request nor its negation ever gets that chance.
_BARE_LANG_PREF_MENTION_RE = re.compile(r"\btamil\b|\benglish\b", re.IGNORECASE)
_BARE_LANG_PREF_FILLER_RE = re.compile(
    r"\b(?:please|kindly|just|only|now|instead|rather|ok|okay|i|want|wanted|need|"
    r"it|me|the|this|that|from|on|onwards?|don'?t|do|does|not|dont|never|no|"
    r"english|tamil|explain|answer|respond|reply|speak|say|translate|write|give|in|into|language)\b",
    re.IGNORECASE,
)


def _is_bare_language_instruction(message: str) -> bool:
    m = (message or "").strip()
    if not m or not _BARE_LANG_PREF_MENTION_RE.search(m):
        return False
    if _ANY_APP_NUMBER_RE.search(m) or re.search(
            r"\bsurvey\s*(?:no\.?|number)?\s*\d|\bsy\.?\s*\d", m, re.IGNORECASE):
        return False  # names a real subject -- a genuine question, not a bare instruction
    leftover = re.sub(r"[^\w\s]", " ", _BARE_LANG_PREF_FILLER_RE.sub(" ", m))
    return not leftover.strip()


def _language_preference_reply(language: str) -> str:
    if language in ("ta", "tanglish"):
        return "சரி, இனி தமிழில் பதிலளிக்கிறேன். எதைப் பற்றி தெரிந்துகொள்ள விரும்புகிறீர்கள்?"
    return "Understood — I'll reply in English from here. What would you like to know?"



# ── Field-visit table: follow-up scope, sort, exclusion, single-period ranges ──
# The rows a field-visit follow-up ("only completed", "sort by date descending",
# "show along district") must stay inside: the ones on screen, not every visit.
_FV_SCOPE = contextvars.ContextVar("fv_scope", default=None)
_MONTH_NUMBERS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7,
                  "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}


def _single_period_range(message: str):
    """(start, end) for "in 2025" / "in january 2025" -- a whole year or month."""
    import calendar
    from datetime import date as _date
    t = _ANY_APP_NUMBER_RE.sub(" ", (message or "").lower())
    t = re.sub(r"\d{4}-\d{2}-\d{2}|\d{1,2}[/\-.]\d{1,2}[/\-.]\d{4}", " ", t)
    m = re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s*(?:of\s+)?(20\d{2})\b", t)
    if m:
        mo, yr = _MONTH_NUMBERS[m.group(1)], int(m.group(2))
        return _date(yr, mo, 1), _date(yr, mo, calendar.monthrange(yr, mo)[1])
    m = re.search(r"\b(20\d{2})\b", t)
    if m:
        yr = int(m.group(1))
        return _date(yr, 1, 1), _date(yr, 12, 31)
    return None, None


def _recount_field_visits(sd: dict) -> None:
    rows = sd.get("field_visits") or []
    sd["count"] = len(rows)
    sd["completed_count"] = sum(1 for r in rows if r.get("status") == "completed")
    sd["to_be_visited_count"] = len(rows) - sd["completed_count"]
    sd["overdue_count"] = sum(1 for r in rows if r.get("is_overdue"))


def _refine_field_visits(sd: dict, message: str, single_app=None) -> None:
    """Scope to the rows on screen, drop "without <app>" rows, apply "sort by ...".
    Runs on the field-visit query result, so every figure still comes from the DB."""
    rows = sd.get("field_visits")
    scope = _FV_SCOPE.get()
    _FV_SCOPE.set(None)
    if not isinstance(rows, list) or single_app:
        return
    msg = (message or "").lower()
    if scope is not None:                # an EMPTY scope is "no rows", not "no scope"
        keep = {n.upper() for n in scope}
        rows = [r for r in rows if str(r.get("application_number", "")).upper() in keep]
    so = _SORT_OVERRIDE.get() or extract_sort_order(message) or _FV_PRIOR_SORT.get()   # the clause may have been set aside
    _FV_PRIOR_SORT.set(None)
    _keep = _FV_KEEP.get()
    _FV_KEEP.set(None)
    if _keep:
        rows = [r for r in rows if _fv_row_matches(r, _keep)]
    if so and len(rows) > 1:
        field, direction = so
        def _num(v):
            m = re.match(r"\d+", str(v or ""))
            return int(m.group()) if m else 0
        key = {"application_number": lambda r: str(r.get("application_number") or ""),
               "status": lambda r: str(r.get("status") or ""),
               "application_type": lambda r: str(r.get("application_type") or ""),
               "block_number": lambda r: str(r.get("block_number") or ""),
               "ward_number": lambda r: str(r.get("ward_number") or ""),
               "survey_no": lambda r: _num(r.get("survey_no")),
               "applicant_name": lambda r: str(r.get("applicant_name") or "").lower(),
               }.get(field, lambda r: str(r.get("field_visit_date") or ""))
        dated = field not in ("application_number", "status", "application_type", "block_number",
                              "ward_number", "survey_no", "applicant_name")
        if dated:   # a visit with no date sorts last whichever way the dates run
            have = sorted([r for r in rows if r.get("field_visit_date")], key=key, reverse=direction == "desc")
            rows = have + [r for r in rows if not r.get("field_visit_date")]
        else:
            rows = sorted(rows, key=key, reverse=direction == "desc")
    _st = _FV_COLS.get()
    _FV_COLS.set(None)
    if _st is None:                      # a fresh question ("field visits with applicant name") sets the columns too
        _a, _d = [], []
        for _m in (_COL_DROP_RE.finditer(msg), _COL_ADD_RE.finditer(msg)):
            pass
        _st = _fv_cols_merge(None, *_fv_cols_parse_loose(msg))
    sd["fv_columns"] = _st
    neg = _NEG_EXCLUDE.get()
    _NEG_EXCLUDE.set(None)
    if neg:
        rows = [r for r in rows
                if str(r.get("application_type", "")).upper() not in neg.get("types", set())
                and not any(_fv_row_matches(r, k) for k in neg.get("visit", set()))]
    if so:
        sd["sort_by"], sd["sort_dir"] = so
    if scope is not None or rows is not sd["field_visits"] or so or neg:
        sd["field_visits"] = rows
        _recount_field_visits(sd)


_INC_EXC_RE = re.compile(
    r"^(?:include|add|bring\s+back)\s+(?P<a>.+?)\s+(?:but|and)\s+(?:exclude|remove|drop)\s+(?P<b>.+)$"
    r"|^(?:exclude|remove|drop)\s+(?P<b2>.+?)\s+(?:but|and)\s+(?:include|add|bring\s+back)\s+(?P<a2>.+)$",
    re.IGNORECASE)


def _fv_include_exclude_answer(message: str, ctx):
    """"Include the first one but exclude the second one." over the
    field-visit list on screen -- a COMPOUND request naming two row
    positions in the SAME carried list, one to add back and one to drop.

    Both ordinals resolve against the CURRENT carried scope (after any
    earlier exclusion already narrowed it) -- never a wider set this layer
    has no record of, so "include" a row that turns out to already be in
    the list is a checked confirmation, never a guess at one that cannot
    actually be restored.

    Returns (explanation, new_scope), or None when this message is not that
    shape, there is no field-visit list in view, or an ordinal in it cannot
    be resolved against the current rows (left to the ordinary
    ambiguous-clarification path rather than acted on blindly).
    """
    if not (ctx and (ctx.filters or {}).get("about_visit") and ctx.application_numbers):
        return None
    clean = (message or "").strip().lower().rstrip("?.! ")
    m = _INC_EXC_RE.match(clean)
    if not m:
        return None
    scope = list(ctx.application_numbers)
    inc_txt = m.group("a") or m.group("a2")
    exc_txt = m.group("b") or m.group("b2")
    inc_num = fctx.ordinal_pick(inc_txt, scope)
    exc_num = fctx.ordinal_pick(exc_txt, scope)
    if not inc_num or not exc_num:
        return None
    parts = []
    if inc_num in scope:
        parts.append(f"{inc_num} is already in the list.")
    else:
        # Nothing wider than `scope` is tracked here, so a row not currently
        # in it cannot be restored -- said plainly rather than guessed at.
        parts.append(f"I don't have a wider list to bring {inc_num} back from "
                     f"-- ask for the full list again if you need it.")
    new_scope = [n for n in scope if n != exc_num]
    parts.append(f"{exc_num} has been excluded."
                if exc_num in scope else f"{exc_num} was already excluded.")
    return " ".join(parts), new_scope


# Labels for `postgres.get_field_presence`'s keys -- the one place that list
# and this module's parser both have to agree on, so a mismatch is a single
# KeyError at the call site rather than a silently wrong label.
_FIELD_PRESENCE_LABELS = {
    "subdivision": ("a subdivision recorded", "ஒரு துணைப்பிரிவு பதிவு"),
    "field_visit_scheduled": ("a scheduled field visit", "திட்டமிடப்பட்ட கள ஆய்வு"),
    "workflow_remarks": ("workflow remarks recorded", "பணிப்பாய்வு குறிப்புகள் பதிவு"),
    "payment": ("payment recorded", "கட்டணம் பதிவு"),
}


async def _render_field_presence(db, officer, scope: list, field_key: str, wants_present: bool,
                                  is_tamil: bool, scope_label_en: str = "those", scope_label_ta: str = "அந்த",
                                  scope_suffix_en: str = ""):
    """The shared rendering half of a field-presence answer: given the exact
    application numbers already decided to be in scope (by a carried-list
    filter, Mode A, or a freshly queried dataset, Mode B), check the field's
    presence on each (`postgres.get_field_presence`, the authoritative ORM
    relationship -- never the legacy raw extract) and render the matching
    subset as the standard listing table.

    A number the presence check cannot resolve at all (outside jurisdiction,
    or no `Application` row -- a register-only service code with no ISD/NISD
    record) is reported separately, never silently dropped from the count or
    guessed at. "Not applicable" (an NISD file has no subdivision or field
    visit concept at all) is reported separately from "not recorded" too --
    the whole point of this check. Returns (html, matched_numbers).

    `scope_label_en` is a DETERMINER before "N application(s)" ("those",
    "the") -- a full noun phrase there ("the applications in your
    jurisdiction") reads as "...of the applications in your jurisdiction 126
    application(s) have..." once the count and the literal word
    "application(s)" are spliced in after it. A trailing qualifier
    ("in your jurisdiction", "under 0158 (...)") belongs in
    `scope_suffix_en` instead, appended AFTER "application(s)".
    """
    from html import escape as _e
    scope = list(dict.fromkeys(n.upper() for n in scope))
    presence = await get_field_presence(db, officer, scope, field_key)
    label_en, label_ta = _FIELD_PRESENCE_LABELS[field_key]
    label = label_ta if is_tamil else label_en

    matched, not_applicable, unresolved = [], [], []
    for n in scope:
        if n not in presence:
            unresolved.append(n)
        elif presence[n] is None:
            not_applicable.append(n)
        elif presence[n] == wants_present:
            matched.append(n)

    def _caveat():
        bits = []
        if not_applicable:
            bits.append(f"{len(not_applicable)} not applicable for this field" if not is_tamil
                       else f"{len(not_applicable)} பொருந்தாது")
        if unresolved:
            bits.append(f"{len(unresolved)} could not be checked" if not is_tamil
                       else f"{len(unresolved)} சரிபார்க்க முடியவில்லை")
        return f" ({'; '.join(bits)}.)" if bits else ""

    if not matched:
        # "All / none of them have X" is an assertion about whether the
        # field is SET -- it must only be made about rows where that is a
        # knowable fact. A service code the field has no concept for at all
        # (e.g. "subdivision" on a register-only 0158 file) puts every row
        # in `not_applicable`, never in `matched`; the old wording still said
        # "All of the 20 ... have a subdivision recorded" in the very same
        # breath as "(20 not applicable for this field)" -- turning a NULL
        # into a false positive, exactly what this check exists to prevent.
        determinable = len(scope) - len(not_applicable) - len(unresolved)
        if determinable <= 0:
            if is_tamil:
                text = f"{scope_label_ta} {len(scope)} பட்டியலில் {label} பொருந்துமா என்று எதற்கும் தீர்மானிக்க முடியவில்லை."
            else:
                text = f"{label.capitalize()} does not apply to any of {scope_label_en} {len(scope)} application(s){scope_suffix_en}."
            return text + _caveat(), matched
        if is_tamil:
            text = f"{scope_label_ta} {determinable} பட்டியலில் {label} கொண்டது எதுவும் இல்லை." if wants_present \
                   else f"{scope_label_ta} {determinable} அனைத்திற்கும் {label} உள்ளது."
        else:
            text = (f"None of {scope_label_en} {determinable} application(s){scope_suffix_en} have {label}." if wants_present
                   else f"All of {scope_label_en} {determinable} application(s){scope_suffix_en} have {label}.")
        return text + _caveat(), matched

    rows_data = await get_applications_by_numbers(db, officer, matched, include_rejected=True)
    rows = rows_data.get("applications") or []
    if is_tamil:
        intro = f"{scope_label_ta} {len(scope)} இல் {len(matched)} {label} கொண்டவை:"
    else:
        verb = "have" if wants_present else "do not have"
        intro = f"{len(matched)} of {scope_label_en} {len(scope)} application(s){scope_suffix_en} {verb} {label}:"
    intro += _caveat()
    table = _project_field_answer(rows, list(fctx._STANDARD_LISTING_COLUMNS), len(matched), is_tamil) if rows else ""
    if table:
        # `_project_field_answer` renders its own "Found N application(s):"
        # intro line; this answer's intro already states the field-presence
        # count and the not-applicable/unresolved caveat, so the generic one
        # is dropped rather than shown twice.
        table = re.sub(r"^<div class='table-intro'>.*?</div>", "", table, count=1)
    return f"<div class='table-intro'>{_e(intro)}</div>{table}", matched


async def _field_presence_answer(db, officer, message: str, ctx, language: str):
    """"Show only those with a subdivision recorded." / "...without a
    scheduled field visit." -- filter the CARRIED list (never a fresh
    register-wide search) by a per-application field's presence. Mode A of
    two: the other, `_standalone_field_presence_answer`, is for a question
    that names its own population with no carried list in play.
    """
    if not (ctx and ctx.application_numbers):
        return None
    parsed = fctx.parse_field_presence_request(message)
    if not parsed:
        return None
    field_key, wants_present = parsed
    is_tamil = language in ("ta", "tanglish")
    return await _render_field_presence(db, officer, ctx.application_numbers, field_key, wants_present, is_tamil)


async def _standalone_field_presence_answer(db, officer, message: str, session_id, chat_history, language: str):
    """"Which applications awaiting a visit have no subdivision recorded?" /
    "Which overdue visits have a subdivision recorded?" / "Which
    applications in ward 001 do not have a subdivision recorded?" /
    "Which applications have payment information recorded?" -- Mode B: the
    officer names the population explicitly, with no carried list to filter
    (or regardless of one -- the population here is stated outright, not a
    pointer back at the screen). Never answered with the raw, unfiltered
    plan/listing the gap this closes used to fall back to: an unfiltered
    answer to a question that asked for a filter is a wrong answer, not an
    incomplete one.

    The dataset queried depends on the population named:
      * "awaiting a visit" / "awaiting visit(s)" -- the awaiting-schedule
        section of the field-visit plan (ISD only, by definition).
      * "overdue visit(s)" -- the overdue section of the same plan.
      * anything else ("applications", "applications in ward 001", ...) --
        the register-wide listing (every service code), ward/block-scoped
        the same way `_register_listing` reads geography from the message.
    Returns (html, matched_numbers, scope_kind) or None when this message is
    not this shape.
    """
    parsed = fctx.parse_standalone_field_presence_request(message)
    if not parsed:
        return None
    scope_kind, field_key, wants_present = parsed
    is_tamil = language in ("ta", "tanglish")

    if scope_kind in ("awaiting_visit", "overdue_visit"):
        ward_num, block_num = await _geo_scope(db, session_id, message, chat_history, officer)
        plan = await _fetch_visit_plan(db, message, officer, ward_number=ward_num, block_number=block_num)
        rows = plan.get("awaiting" if scope_kind == "awaiting_visit" else "overdue") or []
        numbers = list(dict.fromkeys(
            str(r.get("application_number")).upper() for r in rows if r.get("application_number")))
        # English: a DETERMINER ("the") + a trailing qualifier, never a full
        # noun phrase here -- see _render_field_presence's docstring for why
        # ("...of the applications awaiting a visit 20 application(s) have
        # X" was the bug). Tamil is untouched (its own templates already
        # read correctly with a full phrase in this slot).
        scope_en, scope_suffix = "the", (
            " awaiting a visit" if scope_kind == "awaiting_visit" else " with an overdue visit")
        scope_ta = "கள ஆய்வுக்காக காத்திருக்கும் விண்ணப்பங்களில்" if scope_kind == "awaiting_visit" else "தாமதமான கள ஆய்வுகளில்"
    else:
        ward_num, block_num = await _geo_scope(db, session_id, message, chat_history, officer)
        # "which service code 0158 applications have no subdivision
        # recorded?" names its own code -- dropped here before, same as a
        # register listing would lose it without `_requested_service_code_
        # filter`, so the answer silently widened to every code instead of
        # staying scoped to the one named.
        service_code = _requested_service_code_filter(message)
        # "which of my PENDING ISD applications have a field visit
        # scheduled" used to be answered over the WHOLE jurisdiction (every
        # status, every code) -- this branch read a service code but never a
        # status or an ISD/NISD type, the same scopes `_register_listing`
        # already reads for an ordinary listing. Status goes straight into
        # the query (the register's own column); ISD/NISD is excluded from
        # `_requested_service_code_filter` by design (it is the type
        # column there, not a service-code filter -- see that function's own
        # docstring), so it is read here with the same bare-type-word rule
        # `rag.py` uses elsewhere and applied as a post-filter on the
        # `application_type` field those two codes' rows already carry.
        reg_status = _requested_status_filter_for_register(message)
        _type_m = [w for w in ("nisd", "isd") if re.search(
            rf"(?<![a-z0-9]){w}(?![a-z0-9])", message, re.IGNORECASE)]
        named_type = _type_m[0].upper() if len(_type_m) == 1 else None
        sd = await get_all_service_code_applications(
            db, officer, service_code=service_code, ward_number=ward_num,
            block_number=block_num, status=reg_status, hide_rejected=True)
        apps = sd.get("applications") or []
        if named_type:
            apps = [r for r in apps if r.get("application_type") == named_type]
        numbers = list(dict.fromkeys(
            str(r.get("application_number")).upper()
            for r in apps if r.get("application_number")))
        _scope_bits = ([reg_status] if isinstance(reg_status, str) else []) + ([named_type] if named_type else [])
        _scope_qualifier = f" that are {' '.join(_scope_bits)}" if _scope_bits else ""
        if service_code:
            from backend.utils.helpers import SIS_URBAN_SERVICES
            _nm = (SIS_URBAN_SERVICES.get(service_code, {}) or {}).get("name") or service_code
            scope_en, scope_suffix = "the", f"{_scope_qualifier} under {service_code} ({_nm})"
            scope_ta = f"{service_code} ({_nm}) விண்ணப்பங்களில்"
        else:
            scope_en, scope_suffix = "the", f"{_scope_qualifier} in your jurisdiction"
            scope_ta = "உங்கள் அதிகார வரம்பிலுள்ள விண்ணப்பங்களில்"

    if not numbers:
        text = ("இந்த வரம்பில் விண்ணப்பங்கள் எதுவும் இல்லை." if is_tamil
                else "There are no applications in this scope to check.")
        return text, [], scope_kind

    text, matched = await _render_field_presence(
        db, officer, numbers, field_key, wants_present, is_tamil,
        scope_label_en=scope_en, scope_label_ta=scope_ta, scope_suffix_en=scope_suffix)
    return text, matched, scope_kind


def _fv_followup(original: str, ctx, resolution):
    """(self-contained field-visit question, rows to stay inside) for a refine / sort /
    projection / row-removal fragment said over a field-visit table -- or None.
    The columns the table has been given so far ride along on every answer."""
    if not (ctx and (ctx.filters or {}).get("about_visit")):
        return None
    scope = list(ctx.application_numbers)
    _fl = ctx.filters or {}
    if _fl.get("sort_by"):
        _FV_PRIOR_SORT.set((_fl["sort_by"], _fl.get("sort_dir") or "asc"))
    _prior_cols = _fl.get("fv_columns") or {}

    def _ret(msg, rows=None):
        # every answer keeps the columns already asked for ("along with district", "no type")
        if _prior_cols.get("add") or _prior_cols.get("drop"):
            _FV_COLS.set(_prior_cols)
            msg += _fv_cols_message(_prior_cols)[len("show field visits"):]
        return msg, (scope if rows is None else rows)

    frag = re.sub(r"^\s*(?:please\s+)?(?:show|display|give|list)\s+(?:me\s+)?", "",
                  (original or "").strip().rstrip("?.! "), flags=re.I)
    _clean = (original or "").strip().lower().rstrip("?.! ")
    _nt, _nv, _resid = _parse_negation(_clean)
    if _nt or _nv:
        _NEG_EXCLUDE.set({"types": _nt, "visit": _nv})
        return _ret(f"show field visits {_resid}".strip())
    if (re.search(r"\b(?:not|except|excluding|outside|other\s+than)\b", _clean)
            and (re.search(r"\bbetween\b|\b20\d{2}\b|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)", _clean))):
        return _ret(f"show field visits {_clean}")
    _adds, _drops = _fv_cols_parse(_clean)
    if _adds or _drops:
        _state = _fv_cols_merge(_prior_cols, _adds, _drops)
        _FV_COLS.set(_state)
        return _fv_cols_message(_state), scope
    if _FV_STATUS_FRAGMENT_RE.fullmatch(_clean):
        for _key, _pat in _FV_CRITERIA:
            if re.search(_pat, _clean):
                _FV_KEEP.set(_key)
                return _ret("show field visits")
        return _ret(f"show field visits {frag}")
    if not (resolution.resolved and resolution.entity == fctx.ENTITY_APPLICATION_LIST
            and not resolution.application_number):
        return None
    if getattr(resolution, "rows_dropped", 0):
        return _ret("show field visits", list(resolution.application_numbers or []))
    # "the first, second and third overdue visits" / "the first and fourth
    # pending visits" also lands on FOLLOWUP_LIST_REFINE (no field named) or
    # FOLLOWUP_LIST_FIELD (a field named alongside the positions) once
    # `multi_ordinal_pick` has picked the EXACT rows named -- but this
    # function's whole job below is re-deriving a FRESH, criteria-filtered
    # query from the fragment's own words ("show field visits <criteria>"),
    # which has no concept of "position 1 and 4" at all and would silently
    # answer with every row matching no criteria (the whole table) instead
    # of the two rows actually named. An explicit multi-ordinal pick already
    # carries its own `application_numbers`, computed by `resolve()` itself;
    # standing aside here lets the generic application-list renderer use
    # them directly instead of this rewrite discarding them.
    if fctx.mentions_multi_ordinal(original):
        return None
    if (resolution.kind in (fctx.FOLLOWUP_LIST_REFINE, fctx.FOLLOWUP_LIST_FIELD)
            or fctx.field_projections(original) is not None):
        return _ret(f"show field visits {frag}")
    return None



# ── Counting / superlatives over a FIELD-VISIT table ────────────────────────
# "how many of them are completed?" after a visit table is about the VISITS'
# status, not the applications'. Answered from the visit rows on screen, so a
# figure never comes from the previous answer's text or from application status.
_FV_COUNT_RE = re.compile(
    r"\bhow\s+many\b|\bhow\s+much\b|\bcount\b|\bnumber\s+of\b|\btotal\b|\bevlo\b|\bevvalavu\b"
    r"|எத்தனை|எவ்வளவு|எண்ணிக்கை", re.IGNORECASE)
_FV_SUPERLATIVE_RE = re.compile(r"\b(oldest|earliest|newest|latest|most\s+recent)\b|பழைய|புதிய|சமீபத்திய", re.IGNORECASE)
_FV_CRITERIA = (
    ("completed", r"complet|\bdone\b|finish|முடிந்த|முடிக்கப்பட்ட|mudinj|mudint"),
    ("unscheduled", r"unschedul|not\s+scheduled|திட்டமிடப்படாத|thittamidapadatha"),
    ("overdue", r"overdue|\blate\b|தாமத|thamadham"),
    # Checked ahead of "scheduled": `reschedul` would never match that
    # pattern's `\bschedul` anyway (no word boundary after "re"), but a
    # row whose status is literally "rescheduled" is still a DIFFERENT,
    # narrower thing than a plain "scheduled" one -- "excluding rescheduled
    # visits" must drop only those, not every visit with a booked date.
    ("rescheduled", r"reschedul|மாற்றியமைக்க"),
    ("scheduled", r"\bschedul|திட்டமிட"),
    ("open", r"pending|to\s+visit|to\s+be\s+visited|remaining|outstanding|\bopen\b|நிலுவை"),
)
_FV_LABELS = {
    "completed": ("completed", "முடிந்தவை"), "unscheduled": ("unscheduled", "திட்டமிடப்படாதவை"),
    "overdue": ("overdue", "தாமதமானவை"), "scheduled": ("scheduled", "திட்டமிடப்பட்டவை"),
    "rescheduled": ("rescheduled", "மாற்றியமைக்கப்பட்டவை"),
    "open": ("still to visit", "இன்னும் பார்வையிட வேண்டியவை"),
}
_FV_NEG_BEFORE_RE = re.compile(r"(?:\bnot|\bnon|n't|\bisn'?t|\baren'?t|illa\w*|இல்லாத)\s+(?:yet\s+|been\s+)*$", re.IGNORECASE)


def _fv_row_matches(row: dict, key: str) -> bool:
    st = row.get("status")
    return {"completed": st == "completed", "unscheduled": st == "unscheduled",
            "scheduled": st in ("scheduled", "rescheduled"), "overdue": bool(row.get("is_overdue")),
            "rescheduled": st == "rescheduled",
            "open": st != "completed"}[key]


async def _fv_aggregate_answer(db, officer, message: str, ctx, language: str):
    """A count or an oldest/newest question over the visit table on screen, or None."""
    if not (ctx and (ctx.filters or {}).get("about_visit")):
        return None
    msg = (message or "").strip()
    low = msg.lower()
    # a message that names its own subject ("how many field visits are completed") is a fresh question
    if re.search(r"\bvisits?\b|\bapplications?\b|கள\s*ஆய்வு|விண்ணப்ப|\d{4}/\d{4}/", fctx._correct_typos(low)):
        return None
    count_q, sup = bool(_FV_COUNT_RE.search(low)), _FV_SUPERLATIVE_RE.search(low)
    # "latest first" / "oldest first" ask for an ORDER, not for the one that is newest
    if sup and not count_q and (fctx.is_sort_fragment(msg) or not re.search(
            r"\b(which|what|who|when)\b|எது|எந்த", low)):
        return None
    if not (count_q or sup):
        return None
    is_ta = language in ("ta", "tanglish")
    scope = {n.upper() for n in ctx.application_numbers}
    if not scope:
        return ("அந்த அட்டவணையில் கள ஆய்வுகள் இல்லை; எண்ண எதுவும் இல்லை." if is_ta
                else "That table had no field visits, so there is nothing to count.")
    from backend.services.postgres import get_field_visits
    rows = [r for r in (await get_field_visits(db, officer))["field_visits"]
            if str(r.get("application_number", "")).upper() in scope]
    total = len(rows)
    if sup and not count_q:
        dated = sorted([r for r in rows if r.get("field_visit_date")], key=lambda r: r["field_visit_date"])
        if not dated:
            return ("அந்த ஆய்வுகளில் எதற்கும் தேதி இல்லை." if is_ta else "None of those field visits has a date.")
        oldest = sup.group(0).lower() in ("oldest", "earliest") or sup.group(0) == "பழைய"
        r = dated[0] if oldest else dated[-1]
        d = str(r["field_visit_date"])[:10]
        if is_ta:
            return (f"அந்த {total} கள ஆய்வுகளில் {'பழையது' if oldest else 'புதியது'} {r['application_number']}, "
                    f"{d} அன்று ({r.get('status')}).")
        return (f"The {'oldest' if oldest else 'newest'} of those {total} field visits is "
                f"{r['application_number']}, on {d} ({r.get('status')}).")
    crit, neg = None, False
    for key, pat in _FV_CRITERIA:
        m = re.search(pat, low)
        if m:
            crit = key
            neg = bool(_FV_NEG_BEFORE_RE.search(low[:m.start()]))
            break
    tm = re.search(r"\b(nisd|isd)\b", low)
    typ = tm.group(1).upper() if tm else None
    picked = rows
    if crit:
        picked = [r for r in picked if _fv_row_matches(r, crit) != neg]
    if typ:
        picked = [r for r in picked if str(r.get("application_type", "")).upper() == typ]
    n = len(picked)
    if not crit and not typ:
        return (f"அந்த அட்டவணையில் {total} கள ஆய்வுகள் உள்ளன." if is_ta
                else f"There {'is' if total == 1 else 'are'} {total} field visit(s) in that table.")
    if is_ta:
        lab = _FV_LABELS[crit][1] if crit and not neg else (f"{_FV_LABELS[crit][1]} அல்லாதவை" if crit else "")
        return f"அந்த {total} கள ஆய்வுகளில் {n} " + (f"{typ} வகை; " if typ else "") + lab
    lab = (("not " if neg else "") + _FV_LABELS[crit][0]) if crit else ""
    parts = " and ".join(x for x in (typ, lab) if x)
    return f"{n} of those {total} field visit(s) {'is' if n == 1 else 'are'} {parts}."
_NEG_CHANNEL_RE = re.compile(
    rf"\b{_NEG_LEAD}\s+(?:the\s+|any\s+|all\s+|of\s+)*(?:from\s+|via\s+|through\s+)?"
    r"(csc|citizen|sub[\s-]?registrar|sro)\b(?:\s+(?:ones?|applications?|apps?))?", re.IGNORECASE)
_NEG_EXCLUDE = contextvars.ContextVar("neg_exclude", default=None)


def _rewrite_negated_types(message: str) -> str:
    """Before intent parsing: a negated type over applications becomes the types that are
    left; over field visits it is remembered and applied to the rows."""
    if not message or _ANY_APP_NUMBER_RE.search(message):
        return message
    ch = _NEG_CHANNEL_RE.search(message)
    if ch and not re.search(r"\bvisits?\b|கள\s*ஆய்வு", message, re.IGNORECASE):
        gone = {"csc": "CSC", "citizen": "citizen", "sro": "Sub-Registrar"}[
            "sro" if ch.group(1).lower().startswith(("sub", "sro")) else ch.group(1).lower()]
        left = [c for c in ("CSC", "citizen", "Sub-Registrar") if c != gone]
        rest = _NEG_CHANNEL_RE.sub(" ", message)
        rest = re.sub(r"\b(?:applications?|apps?|show|list|display|give|me|please|pls|all|everything|my|from)\b", " ",
                      rest, flags=re.IGNORECASE)
        return re.sub(r"\s+", " ", f"show {rest} applications from {' and '.join(left)}").strip()
    types, visit, residual = _parse_negation(message)
    if not (types or visit):
        return message
    if re.search(r"\bvisits?\b|கள\s*ஆய்வு", message, re.IGNORECASE):
        _NEG_EXCLUDE.set({"types": types, "visit": visit})
        return residual if re.search(r"\bvisits?\b|கள\s*ஆய்வு", residual, re.IGNORECASE) else "show field visits"
    if visit or not types:
        return message
    left = [t for t in ("ISD", "NISD") if t not in types]
    if not left:
        return message
    rest = re.sub(r"\b(?:applications?|apps?|files|cases|show|list|display|give|me|please|pls|all|everything|anything|my)\b",
                  " ", residual, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", f"show {rest} {' and '.join(left)} applications").strip()



# ── Remarks written on an application's workflow steps ──────────────────────
# "is there any remark in them?", "remarks of <app>", "why were they rejected?" -- answered from the
# workflow history of exactly the applications named or on screen, never from the model.
_REMARK_RE = re.compile(r"\b(?:remarks?|comments?|notes?)\b|குறிப்பு|கருத்து", re.IGNORECASE)
_REMARK_OTHER_RE = re.compile(
    r"\b(?:order|sis|proposed|auto[\s-]?recommend\w*|system|sd|draughtsman|surveyor'?s?|rejection|return)\s+"
    r"(?:remarks?|comments?|notes?)\b|\b(?:remarks?|comments?)\s+(?:on|of|about)\s+the\s+order\b"
    # the visit row's own columns, answered by _fv_detail_answer
    r"|\b(?:vis\w*t|encro\w*)\s+(?:notes?|remarks?|observations?)\b", re.IGNORECASE)
_REJECT_WHY_RE = re.compile(r"\bwhy\b[^?.!]{0,40}\b(?:reject|declin|refus)\w*|\breasons?\b[^?.!]{0,30}\breject\w*"
                            r"|\breject\w*\b[^?.!]{0,30}\breasons?\b|ஏன்\s*நிராகரி", re.IGNORECASE)
_DESK_LABEL = {"SIS": ("Sub Inspector Surveyor", "நில அளவையர்"), "SD": ("Senior Draughtsman", "மூத்த வரைவாளர்"),
               "DIS": ("Deputy Inspector Surveyor", "துணை ஆய்வாளர்"),
               "TAHSILDAR": ("Zonal Tahsildar", "வலய தாசில்தார்")}


def _asked_workflow_remarks(message: str):
    """'remarks' | 'reason' | None -- what a remark / rejection-reason question wants."""
    m = message or ""
    if _REMARK_OTHER_RE.search(m):
        return None            # a specific remark field, answered by its own handler
    if _REJECT_WHY_RE.search(m):
        return "reason"
    if _REMARK_RE.search(m):
        return "remarks"
    return None


_YN_WORDS = {"pending": "pending", "approved": "approved", "approve": "approved", "rejected": "rejected", "reject": "rejected",
             "completed": "completed", "complete": "completed", "escalated": "escalated", "escalate": "escalated",
             "closed": "completed", "decided": "completed"}
_YN_SKIP = re.compile(r"\b(?:how\s+long|how\s+many|overdue|late|delay\w*|since|days?|sla|why|when|reason|remarks?|visit|"
                      r"documents?|fee|ward|block|survey|patta|owner|"
                      # A role/desk named in the question ("has the ZDT approved
                      # X?") has no decision-desk evidence to attribute from here
                      # -- this is the register-row path, with no workflow_history
                      # to check against. Declining sends it to
                      # `_render_status_role_answer` / `_stage_yes_no_question`
                      # instead, which DO have that evidence, rather than
                      # answering from the overall status alone and silently
                      # crediting a role that may never have touched the file.
                      r"zdt|dro|tahsildar\w*|tahsildhar\w*|zonal|draughtsman|draftsman|\bsd\b|\bdis\b|\bsis\b|"
                      r"deputy\s+inspect\w*|sub\s+inspect\w*|senior\s+draughtsman|district\s+revenue\s+officer)\b",
                      re.IGNORECASE)


def _status_yes_no(msg: str, app_no: str, status, stage, ta: bool) -> Optional[str]:
    """"is X pending or approved?", "has X been completed?", "is X rejected?" -- answered from the status.
    A status word in the question sent these to the age / overdue path, which answered an approved file's
    either/or question with "the field visit is 42 days overdue"."""
    m = (msg or "").lower()
    if not re.match(r"\s*(?:is|was|has|have|did|whether)\b", m) or _YN_SKIP.search(m):
        return None
    asked = []
    for tok in re.findall(r"[a-z]+", _ANY_APP_NUMBER_RE.sub(" ", m)):
        hit = _YN_WORDS.get(tok) or next((v for k, v in _YN_WORDS.items() if len(tok) >= 6 and is_token_typo_match(tok, k)), None)
        if hit and hit not in asked:
            asked.append(hit)
    st = str(status or "").lower()
    if not asked or not st:
        return None
    if asked == ["pending"] and st in ("pending", "in_progress"):
        return None                      # the age / SLA answer below says "yes" and how long, which is more use
    lab = {"in_progress": "in progress"}.get(st, st)
    lab_ta = {"pending": "நிலுவையில்", "in_progress": "செயலாக்கத்தில்", "approved": "அங்கீகரிக்கப்பட்டது",
              "rejected": "நிராகரிக்கப்பட்டது", "escalated": "மேல்முறையீட்டில்"}.get(st, st)
    stg = f" (stage: {stage})" if stage else ""
    if len(asked) > 1:
        return f"{app_no} {lab_ta}{stg}." if ta else f"{app_no} is {lab}{stg}."
    # "has it reached the COMPLETED STAGE?" names the stage literally -- COMPLETED and
    # REJECTED are different terminal stages (confirmed in the data: approved ->
    # COMPLETED, rejected -> REJECTED), so a rejected file must answer No here even
    # though the looser "has it been decided?" / "is it closed?" (no "stage" word)
    # is still Yes for either, by design.
    if asked[0] == "completed" and re.search(r"\bstage\b", m):
        hit = str(stage or "").strip().upper() == "COMPLETED"
    else:
        hit = asked[0] == st or (asked[0] == "completed" and st in ("approved", "rejected")) \
            or (asked[0] == "pending" and st in ("in_progress", "escalated"))
    if ta:
        return f"{'ஆம்' if hit else 'இல்லை'} — {app_no} {lab_ta}{stg}."
    return f"{'Yes' if hit else 'No'} — {app_no} is {lab}{stg}."


_JURISDICTION_YN_RE = re.compile(
    r"\b(?:fall|falls|come|comes|lie|lies)\s+(?:within|under|in)\s+(?:my|our)\s+jurisdiction\b|"
    r"\bwithin\s+my\s+jurisdiction\b|\bin\s+my\s+jurisdiction\b|\bis\s+it\s+(?:in\s+)?my\s+jurisdiction\b|"
    r"\b(?:my|the)\s+jurisdiction\b.{0,15}\?|என்\s*அதிகார\s*வரம்பில்", re.IGNORECASE)


_PAYMENT_COMPOSITE_RE = re.compile(
    r"\bpayment\s+(?:information|info|details?)\b|\bwhat\s+payment\b.{0,20}\bavailable\b", re.IGNORECASE)


def _payment_composite_answer(msg: str, app_no: str, sd: dict, ta: bool) -> Optional[str]:
    """"what payment information is available for X?" names no single field, so it matched
    neither "payment amount" nor "payment mode" in the field map (each a single-field lookup) and
    fell to "I could not find that particular detail" -- even though fee_amount, challan_number
    and payment_mode were all populated and each answers correctly on its OWN narrower question.
    A composite question reuses exactly those same fields rather than a separate implementation,
    and reports a field as not recorded (never silently dropped) when it genuinely is blank."""
    if not _PAYMENT_COMPOSITE_RE.search(msg or ""):
        return None
    nr = "பதிவு இல்லை" if ta else "not recorded"
    fee = sd.get("fee_amount")
    challan = sd.get("challan_number")
    mode = sd.get("payment_mode")
    if fee is None and not challan and not mode:
        return (f"{app_no}-க்கு கட்டணம், சலான் எண் அல்லது செலுத்தும் முறை எதுவும் பதிவு செய்யப்படவில்லை." if ta
                else f"No fee amount, challan number or payment mode is recorded for {app_no}.")
    if ta:
        return (f"{app_no}-ன் கட்டண விவரங்கள் — கட்டணம்: {fee if fee is not None else nr}; "
                f"சலான் எண்: {challan or nr}; செலுத்தும் முறை: {mode or nr}.")
    return (f"Payment details for {app_no} -- fee amount: {fee if fee is not None else nr}; "
            f"challan number: {challan or nr}; payment mode: {mode or nr}.")


def _jurisdiction_yn_answer(msg: str, app_no: str, sd: dict, ta: bool) -> Optional[str]:
    """"does X fall within my jurisdiction?" -- a direct Yes/No, never substituted with the
    application's type or any other field. By the time this runs `sd` already came from
    get_application_detail()/the register gate, BOTH of which filter through the officer's
    actual SQL jurisdiction subquery (get_jurisdiction_filter) before returning a row at all --
    so reaching this function with a populated `sd` already proves membership; authorization
    was enforced in SQL, not here. An inaccessible application never reaches this far: it is
    refused earlier, with the same wording any not-found number gets, so this never confirms or
    denies the existence of a file outside the officer's view."""
    if not re.match(r"\s*(?:is|does|do|was|has)\b", (msg or "")) or not _JURISDICTION_YN_RE.search(msg or ""):
        return None
    ward = sd.get("ward_name") or sd.get("ward_number") or (sd.get("ward") if isinstance(sd.get("ward"), str) else None)
    where = f" ({ward})" if ward else ""
    if ta:
        return f"ஆம் — {app_no} உங்கள் அதிகார வரம்பில் உள்ளது{(' (' + ward + ')') if ward else ''}."
    return f"Yes -- {app_no} is within your jurisdiction{where}."


async def _register_only_remarks(db, officer, numbers):
    """get_workflow_remarks' shape for files outside ISD / NISD, read from application_workflow.
    Access is the register's: a number this officer does not hold returns nothing."""
    from sqlalchemy import text as _sql
    from backend.services.othercode_detail import _hist
    reg = await get_applications_by_numbers(db, officer, numbers, include_rejected=True)
    out = []
    for a in (reg or {}).get("applications") or []:
        n = a.get("application_number")
        if not n or n.split("/")[1] in ("0153", "0154"):
            continue
        try:
            async with db.begin_nested():
                wf = (await db.execute(_sql("SELECT action_from, action_date, remarks FROM application_workflow "
                                            "WHERE appl_id = :a ORDER BY slno"), {"a": n})).mappings().all()
        except Exception:
            wf = []
        hops = [{"desk": _hist(str(w["action_from"]), False), "date": str(w["action_date"] or "") or None,
                 "remark": (w["remarks"] or "").strip().strip("-").strip() or None} for w in wf]
        status = a.get("status")
        last = next((h["remark"] for h in reversed(hops) if h["remark"]), None)
        out.append({"application_number": n, "status": status, "hops": hops,
                    "reason": last if status == "rejected" else None})
    return out


async def _workflow_remarks_answer(db, officer, message: str, ctx, language: str):
    """HTML answer about workflow remarks / rejection reasons, or None when this is not that question."""
    mode = _asked_workflow_remarks(message)
    if not mode:
        return None
    named = list(dict.fromkeys(n.upper() for n in _ANY_APP_NUMBER_RE.findall(message)))
    scope = named
    if not scope and ctx and ctx.application_numbers and (ctx.filters or {}).get("about_visit") is not True:
        nums = list(ctx.application_numbers)
        pick = fctx.ordinal_pick(message, nums) if (fctx.mentions_ordinal(message) or fctx.mentions_slice(message)) else None
        scope = [pick] if pick else nums
    if not scope:
        return None
    from backend.services.postgres import get_workflow_remarks
    from html import escape as _e
    data = await get_workflow_remarks(db, officer, scope)
    apps = data["apps"]
    is_ta = language in ("ta", "tanglish")
    if not apps:
        # `applications` holds only ISD / NISD: a file under any other code (F-Line, 0158 ...) has its
        # remarks in the raw workflow log -- reading only the former called the officer's own file
        # "not in your jurisdiction"
        apps = await _register_only_remarks(db, officer, scope)
        data = {**data, "dropped": max(0, len(scope) - len(apps))}
    if not apps and named:
        return None      # not this officer's file: the standard jurisdiction refusal answers it
    if not apps:
        return ("<div class='table-intro'>" + ("அந்த விண்ணப்பங்கள் உங்கள் அதிகார எல்லைக்குள் இல்லை." if is_ta
                else "None of those applications are in your jurisdiction.") + "</div>")

    def desk(d):
        return _DESK_LABEL.get(d, (d or "-", d or "-"))[1 if is_ta else 0]

    def cell_app(n):
        return (f"<a href='javascript:void(0)' class='app-table-link' onclick=\"window.handleAppClick('{_e(n)}')\" "
                f"style='color:#2563eb;text-decoration:underline;cursor:pointer;'>{_e(n)}</a>")

    rows, with_x = [], 0
    for a in apps:
        if mode == "reason":
            if a["status"] != "rejected":
                continue
            if a["reason"]:
                with_x += 1
            rows.append(f"<tr><td>{cell_app(a['application_number'])}</td>"
                        f"<td>{_e(a['reason'] or ('பதிவில் இல்லை' if is_ta else 'no reason recorded'))}</td></tr>")
            continue
        marks = [h for h in a["hops"] if h["remark"]]
        if marks:
            with_x += 1
        if len(apps) == 1:
            for h in marks:
                rows.append(f"<tr><td>{_e(desk(h['desk']))}</td><td>{_e(h['date'] or '-')}</td><td>{_e(h['remark'])}</td></tr>")
        else:
            last = marks[-1] if marks else None
            more = f" (+{len(marks) - 1} " + ("முந்தைய" if is_ta else "earlier") + ")" if len(marks) > 1 else ""
            rows.append(f"<tr><td>{cell_app(a['application_number'])}</td>"
                        f"<td>{_e(desk(last['desk'])) if last else '-'}</td><td>{_e(last['date'] or '-') if last else '-'}</td>"
                        f"<td>{_e(last['remark'] + more) if last else ('பதிவில் இல்லை' if is_ta else 'no remark recorded')}</td></tr>")
    n = len(apps) if mode == "remarks" else sum(1 for a in apps if a["status"] == "rejected")
    if mode == "reason" and not n:
        return ("<div class='table-intro'>" + ("இவற்றில் நிராகரிக்கப்பட்டவை இல்லை." if is_ta
                else "None of those applications was rejected, so there is no rejection reason.") + "</div>")
    if is_ta:
        intro = (f"{n} விண்ணப்பங்களில் {with_x}-க்கு நிராகரிப்புக் காரணம் பதிவாகியுள்ளது:" if mode == "reason" else
                 ((f"{apps[0]['application_number']}-இல் பதிவான குறிப்புகள்:" if len(apps) == 1 else
                   f"{n} விண்ணப்பங்களில் {with_x}-க்கு பணிப்பாய்வு குறிப்புகள் பதிவாகியுள்ளன:") if with_x else
                  f"இந்த {n} விண்ணப்பத்தின் பணிப்பாய்வில் குறிப்புகள் எதுவும் பதிவாகவில்லை."))
    elif mode == "reason":
        intro = f"Rejection reason recorded for {with_x} of {n} rejected application(s):"
    elif with_x and len(apps) == 1:
        intro = f"Remarks recorded on {apps[0]['application_number']}:"
    elif with_x:
        intro = f"Remarks are recorded on {with_x} of {n} application(s); the latest one for each is shown:"
    else:
        intro = (f"No remark is recorded on the workflow of {'this application' if n == 1 else f'any of these {n} applications'}"
                 f" (a step with no note, or just '-', is not a remark).")
    if mode == "remarks" and not with_x:
        return f"<div class='table-intro'>{_e(intro)}</div>"
    if mode == "reason":
        head = ("விண்ணப்ப எண்", "நிராகரிப்புக் காரணம்") if is_ta else ("Application No.", "Reason")
    elif len(apps) == 1:
        head = ("மேசை", "தேதி", "குறிப்பு") if is_ta else ("Desk", "Date", "Remark")
    else:
        head = ("விண்ணப்ப எண்", "மேசை", "தேதி", "குறிப்பு") if is_ta else ("Application No.", "Desk", "Date", "Remark")
    if data.get("dropped"):
        intro += (f" ({data['dropped']} விண்ணப்பம் உங்கள் அதிகார எல்லைக்கு வெளியே.)" if is_ta
                  else f" ({data['dropped']} not in your jurisdiction.)")
    return (f"<div class='table-intro'>{_e(intro)}</div><table class='data-table'><thead><tr>"
            + "".join(f"<th>{h}</th>" for h in head) + "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>")
_FV_COLS = contextvars.ContextVar("fv_cols", default=None)
_FV_KEEP = contextvars.ContextVar("fv_keep", default=None)         # keep only visits of this status
_FV_PRIOR_SORT = contextvars.ContextVar("fv_prior_sort", default=None)   # (field, direction) the table was in


_RAW_BY_FIXED = contextvars.ContextVar("raw_by_fixed", default=None)


async def _agent_chunks(first, rest):
    """Re-join the agent's first chunk to the remainder of its stream.

    The first chunk has to be pulled before anything is written to the SSE
    stream -- that pull is what runs the tool loop, and therefore what raises
    if the agent cannot run. Once it has arrived the turn is committed to the
    agent, and this puts the stream back together in order.
    """
    yield first
    async for chunk in rest:
        yield chunk



# A bare follow-up typed with nothing to refer back to. CLAUDE.md's rule is
# that this layer "stands aside entirely rather than asking, because the
# handlers below already ask when they need a number" -- true for the
# deterministic handlers, and NOT true for the one that actually receives
# these: `general_query` hands the message to llama3.1:8b, which does not ask.
# Measured, with an empty session: "which is oldest?" -> 60 s and a confidently
# named application; the other officer got *"There are no overdue ISD
# applications."*; "எது பழையது?" -> 137 s and an invented date (2022-02-15).
#
# So the carve-out is narrow: ONLY when the message is list-shaped, has no
# context to resolve against, and would otherwise reach the model. Every
# deterministic handler keeps its own behaviour.
_NO_CONTEXT_FOLLOWUP_KINDS = (fctx.FOLLOWUP_LIST_AGGREGATE,
                              fctx.FOLLOWUP_LIST_REFINE)


_FRAGMENT_WORDS = frozenset("""what about it its this that those these them the one ones first second third last next more
    why when who where how long much many which is are was were and also then so approved rejected pending overdue completed
    row rows entry display show previous prev following
    today yesterday tomorrow week month year jan feb mar apr may june july aug sept oct nov dec january february march april
    august september october november december area patta number name date status fee amount mobile ward block details detail
    only just isd nisd csc citizen sro left right previous other another same""".split())


_VAGUE_OPENER_RE = re.compile(
    r"^(?:please\s+)?(?:tell\s+me\s+something|i\s+have\s+a\s+(?:problem|question|doubt|issue)|can\s+you\s+(?:check|help)(?:\s+me)?"
    r"|(?:check|open|see)\s+(?:my|the)\s+(?:file|application|case)|tell\s+me\s+about\s+(?:my|the)\s+(?:application|file)"
    r"|(?:my\s+)?application\s+(?:is\s+)?not\s+moving)\s*[?.!]*$", re.IGNORECASE)


def _is_bare_fragment_without_context(message: str, context, chat_history=None) -> bool:
    """"why", "what is the area", "approved ones", "last month" as the FIRST thing
    said: nothing is in view to refer to, and left to the model these were answered
    from imagination."""
    if context:
        return False
    # The workload summary is not an application list, so it correctly leaves
    # no FollowupContext. A bare "show" immediately after it is nevertheless a
    # valid request to open the all-service-code register, not an orphan
    # follow-up. Let parse_intent use the previous turn's intent for that one
    # narrow case; the same guard is mirrored in process_chat_stream below.
    _display_only = re.fullmatch(
        r"\s*(?:please\s+)?(?:show|list|display|view|open|காட்டு|காண்பி|பட்டியல்)\s*[?.!]*\s*",
        message or "", re.IGNORECASE)
    if _display_only and _resolve_prev_intent(chat_history) == "officer_workload":
        return False
    if _BARE_REF_RE.match((message or "").strip()) or _is_capability_question(message):
        return False
    if _VAGUE_OPENER_RE.match((message or "").strip()):
        return True
    toks = re.findall(r"[a-z0-9]+", (message or "").lower())
    if not toks or len(toks) > 5 or re.search(r"\d{4}/\d|\d{3,}", message or ""):
        return False
    if not all(t in _FRAGMENT_WORDS or re.fullmatch(r"\d{1,2}(?:st|nd|rd|th)|\d{1,2}", t) for t in toks):
        return False
    if len(toks) == 1 and toks[0] in ("isd", "nisd", "csc", "citizen", "sro"):
        return False
    return parse_intent(message) == "general_query" or toks[0] in ("only", "just")


def _is_orphan_list_followup(message: str, context) -> bool:
    return (not context
            and fctx.classify(message) in _NO_CONTEXT_FOLLOWUP_KINDS
            and parse_intent(message) == "general_query"
            and not _followup_is_self_contained(message))


# "What is my workload?" -> "show" -- a bare request to see the records the
# workload summary just counted. `get_officer_workload()` returns aggregate
# counts only (no application numbers), so this never had a FollowupContext
# to resolve against and fell into `_orphan_followup_reply()`'s "nothing has
# been shown in this conversation yet" -- confidently wrong, since something
# WAS just shown, a summary of exactly these records. Deliberately narrow (no
# trailing words beyond "me"/"us"/"my"/"them"/"applications"/"files"), so a
# more specific follow-up ("show applications in ward 102") is untouched and
# reaches the normal pipeline instead.
_WORKLOAD_SHOW_RE = re.compile(
    r"^\s*(?:please\s+)?(?:show|list|display|give)\b"
    r"(?:\s+(?:me|us))?(?:\s+(?:my|the|them|those|it))?"
    r"(?:\s+(?:applications?|apps?|files?|list))?"
    r"\s*[.!?]*\s*$",
    re.IGNORECASE,
)


async def _workload_show_followup(db, officer, message: str, chat_history: list, language: str):
    """`(structured_data, response_text)` for a "show" that follows a
    workload summary, or None when the message doesn't fit that shape."""
    if not _WORKLOAD_SHOW_RE.match(message or ""):
        return None
    if _resolve_prev_intent(chat_history) != "officer_workload":
        return None
    data = await get_officer_workload_applications(db, officer)
    text = _render_all_service_code_answer(data, language in ("ta", "tanglish"))
    return data, text


def _list_was_asked(chat_history: list) -> bool:
    return any(parse_intent(h.get("content") or "") in _LIST_FOLLOWUP_INTENTS
               for h in (chat_history or []) if (h.get("role") or "user") == "user"
               and len((h.get("content") or "").split()) >= 2)


def _is_empty_sort_followup(message: str, context) -> bool:
    return ((fctx.is_sort_fragment(message)
             or (len((message or "").split()) <= 4 and fctx.field_projections(message) is not None
                 and not re.search(r"\bapplications?\b|\bapps?\b", message or "", re.IGNORECASE)))
            and not (context and getattr(context, "application_numbers", None)))


def _orphan_followup_reply(language: str, empty: bool = False) -> str:
    if empty:
        if language in ("ta", "tanglish"):
            return "மாற்றியமைக்க எதுவும் இல்லை — முந்தைய பட்டியலில் விண்ணப்பங்கள் எதுவும் இல்லை."
        return "There is nothing to change — the last list had no applications."
    if language in ("ta", "tanglish"):
        return ("எந்தப் பட்டியலைக் குறிப்பிடுகிறீர்கள் என்று தெரியவில்லை — "
                "இந்த உரையாடலில் இதுவரை எந்த விண்ணப்பப் பட்டியலும் காட்டப்படவில்லை.\n"
                "முதலில் ஒன்றைக் கேளுங்கள் — எ.கா. \"எனது நிலுவை விண்ணப்பங்கள்\", "
                "\"ஒப்புதல் பெற்ற விண்ணப்பங்கள்\", \"CSC விண்ணப்பங்கள்\" — "
                "பிறகு இதே கேள்வியைக் கேளுங்கள்.")
    return ("I do not know which application or list you mean — nothing has "
            "been shown in this conversation yet.\n"
            "Give an application number (for example 2026/0154/28/001197), or ask "
            "for a list first (\"my pending applications\", \"my approved "
            "applications\", \"applications from CSC\"), then ask this again.")


# ── Long messages and several requests in one message ────────────────────────
# An officer who types a paragraph ("...my supervisor asked me about it ... so please
# show me my pending applications") asked ONE thing, and one who types "show pending
# and how many ISD" asked two. Read whole, the paragraph was routed on stray words
# ("ward", "one" -> a ward comparison) and the pair answered with only its last half.
_REQ_CUE_EN = re.compile(
    r"^(?:show|list|display|give|tell|get|find|check|count|open|view|compare|explain|describe|calculate|remarks?|any\s+remarks?|"
    r"comments?|why|reason|is\s+there|are\s+there|"
    r"how\s+(?:many|much|long)|what|which|who|when|where|is|are|does|do|can\s+(?:you|i|we)|status|total)\b", re.IGNORECASE)
_REQ_CUE_TA = re.compile(
    r"காட்டு|சொல்லு|சொல்லுங்க|எத்தனை|என்ன|எவ்வளவு|எது|பட்டியல்|\b(?:kaattu\w*|kaatu\w*|sollu\w*|evlo|enna|edhu|eppo|yaar\w*|displat\w*)\b",
    re.IGNORECASE)
_REQ_LEAD = re.compile(
    r"^(?:(?:so|and|also|then|now|ok|okay|please|pls|kindly|sir|madam|first|next|finally|plus|but|anyway|actually|btw|"
    r"by\s+the\s+way|can\s+you|could\s+you|would\s+you|i\s+want\s+you\s+to|i\s+would\s+like\s+to|let\s+me\s+know|just|quickly|"
    r"i\s+need\s+to\s+know|i\s+want\s+to\s+know)\b[\s,]*)+", re.IGNORECASE)
_REQ_SPLIT = re.compile(
    r"\s*(?:[?.!;]+|,|\s&\s|\band\b|\bplus\b|\balso\b|\bthen\b|\bso\b|\bbut\b|\bbtw\b|by\s+the\s+way|"
    r"மற்றும்|பிறகு|அப்புறம்|\bmatrum\b|\bapram\b|\bapuram\b)\s+", re.IGNORECASE)
_NOT_A_REQUEST = frozenset({"general_query", "greeting", "small_talk", "unknown_message", "help"})


# ── One large message that holds many questions ─────────────────────────────────────────────
# "For application X tell me the name, mobile, address, ... and when it was submitted", "1) ... 2) ... 3) ...",
# "Compare A and B : status, challan, owner ...", "Show full details of A, B, C and D including ...". Handing the
# whole message to one handler answered one part and dropped the rest, so such a message is split into its own
# questions, each is answered by the ordinary pipeline in order (every answer is part of the history the next one
# sees), and the answers are returned together under their question.
_APP_NO_RE = re.compile(r"\d{4}/\d{3,4}/\d{1,3}/\d+")
_BIG_MAX_CLAUSES = 30
_BIG_VERB = re.compile(r"^(?P<head>.*?\b(?:tell\s+me|give\s+me|show\s+me|let\s+me\s+know|i\s+need|need|what\s+(?:is|are)|pls\s+give\s+me|"
                       r"please\s+give\s+me|give|show|including|include)\b)(?P<rest>.+)$", re.IGNORECASE | re.DOTALL)
_BIG_SEP = re.compile(r"\s*(?:,\s*(?:and\s+(?:also\s+)?|then\s+)?|;|\s+மற்றும்\s+|\s+and\s+also\s+|\s+and\s+then\s+|\s+then\s+|"
                      r"\s+and\s+(?=(?:when|what|which|who|how|is|are|does|do|has|have|was|were|show|tell|give|its|their)\b))\s*", re.IGNORECASE)
_BIG_LANG_TAIL = re.compile(r",?\s*(?:answer|reply|respond)\s+in\s+(?:tamil|english|tanglish)\s*\.?\s*$", re.IGNORECASE)


_CQ_SPLIT = re.compile(
    r"\s*(?:[;]|,?\s+(?:and\s+also|and\s+then|and|also|then|plus|mattrum|matrum|aprm|apram)|\s+மற்றும்)\s+"
    r"(?=(?:whether|if|which|what|who|whom|when|where|why|how|is|are|was|were|does|do|did|has|have|any|show|tell|list|give|compare|check|"
    r"யார்|என்ன|எந்த|எப்போது|ஏன்|எத்தனை)(?:\s|$))", re.IGNORECASE)
_CQ_REC = re.compile(r"\b(tslr|igrs|natham|f[\s-]?line|sd\s+attachments?|new\s+owners?|old\s+owners?|returned\s+owners?|"
                           r"parcel\s+modification|full[\s-]?field)\b", re.IGNORECASE)


def _compound_split(text: str):
    """"which tslr fields are blank and which are the same as the application": two questions in one sentence.
    The second keeps the record the first one named. None unless both halves are real questions."""
    parts = [p.strip(" ,.;?") for p in _CQ_SPLIT.split(text) if p and p.strip(" ,.;?")]
    if len(parts) < 2 or len(parts) > 5 or any(len(p.split()) < 3 for p in parts):
        return None
    rec = _CQ_REC.search(parts[0])
    out = []
    for i, p in enumerate(parts):
        if i and rec and not _CQ_REC.search(p) and not _APP_NO_RE.search(p):
            p = f"{p} ({rec.group(1)})"
        out.append(p)
    return out


def _multi_app_plan(body: str, apps):
    """Two or more applications and one question about each of them:
    "compare A and B on status, applicant and survey no"  -> each field of each application
    "A, B and C: which has a new owner record, which has no old owner record" -> each question of each application."""
    if len(apps) < 2:
        return None
    out = []
    m = re.match(r"^\W*(?:compare|comparison\s+of|check|contrast)\b.*?\b(?:on|for|in\s+terms\s+of|regarding|with\s+respect\s+to|by)\s+(?P<f>.+)$",
                 body, re.IGNORECASE)
    if m:
        fields = [re.sub(r"^(?:also|and|then|the|whether)\s+", "", f.strip(" ,.;?"), flags=re.IGNORECASE)
                  for f in re.split(r"\s*(?:,|;|&|\band\b)\s*(?!either|both)", m.group("f")) if f and f.strip(" ,.;?")]
        fields = [x for f in fields for x in (_compound_split(f) or [f])]
        fields = [re.sub(r"^(?:whether|if)\s+(?:either|both|each|any)(?:\s+of\s+them)?\s+(?:has|have)\s+", "does it have ", f, flags=re.IGNORECASE)
                  for f in fields]
        for f in fields:
            for a in apps:
                out.append(f"{f} of {a}" if not re.match(r"^(?:does|do|is|are|did|has|have|any|either|which)\b", f, re.IGNORECASE)
                           else re.sub(r"^(?:either|which\s+of\s+them|both)\s+", "", f, flags=re.IGNORECASE) + f" {a}")
        return out[:_BIG_MAX_CLAUSES] if len(out) >= 4 else None
    qs = list(re.finditer(r"\bwhich\s+(?:one\s+|of\s+them\s+)?(?:has|have|had|is|are|does|do)\s+(?P<q>(?:no\s+|not\s+)?[^,]+?)(?=\s*(?:,|\band\b\s+which|$|\?))",
                           body, re.IGNORECASE))
    if len(qs) >= 2 or (len(qs) == 1 and len(apps) >= 2):
        for q in qs:
            what = q.group("q").strip(" ,.;?")
            neg = re.match(r"^(?:no|not)\s+(.*)$", what, re.IGNORECASE)
            for a in apps:
                out.append(f"does it have {neg.group(1) if neg else what} {a}")
        return out[:_BIG_MAX_CLAUSES] if len(out) >= 4 else None
    return None


_BARE_COLUMN_NOUN_RE = re.compile(
    r"^(?:application\s*(?:no\.?|number)|status|survey\s*(?:no\.?|number)?|"
    r"sub[\s-]?division\s*(?:no\.?|number)?s?|submission\s*date|date|ward|block|stage|type|"
    r"service\s*code|(?:submission\s*)?channel)\.?$",
    re.IGNORECASE)


def _is_bare_column_field(field: str) -> bool:
    """A single column name, OR a plain "A and B" conjunction of two of
    them -- the shared trailing-field case `_BIG_SEP` deliberately leaves
    joined ("...service code, submission date and status" splits into
    ["service code", "submission date and status"], not three pieces,
    because `_BIG_SEP`'s own "and" rule only fires before a comma or a
    question word, neither of which is here). Without this, "show my
    pending ISD applications, including application number, service code,
    submission date and status" -- an ordinary listing with a column list,
    the exact shape the register-wide no-split guard below exists for --
    still split on "service code" (missing from the allow-list this
    checks) and "submission date and status" (two columns, not one), so
    each fell through to the generic multi-request splitter and was
    answered as its own UNSCOPED field question over the whole
    jurisdiction instead of staying inside the one scoped listing.
    """
    field = field.strip()
    if _BARE_COLUMN_NOUN_RE.match(field):
        return True
    parts = re.split(r"\s+and\s+", field)
    return len(parts) > 1 and all(_BARE_COLUMN_NOUN_RE.match(p.strip()) for p in parts)


def _big_plan(message: str):
    """A list of single questions when `message` is one large message holding several, else None."""
    text = re.sub(r"\s+", " ", (message or "").strip())
    words = text.split()
    if len(words) < 8 or _howto_qa.match(text):
        return None
    # "Show my pending ISD applications and my pending NISD applications, and
    # tell me which has more." is ALREADY one fully-supported comparison
    # question -- `compare_applications`'s "type" count comparison (see
    # CLAUDE.md's comparison table) answers it in a single coherent sentence.
    # Splitting it first broke that: clause 1 became a bare combined listing
    # with no per-type breakdown, and clause 2 ("tell me which has more") lost
    # all memory of ISD/NISD and answered "I could not verify a count for
    # that from the register" -- a confident refusal to a question the
    # register could answer perfectly well as a whole. Stand aside whenever
    # the whole message already resolves to a real comparison, at >=14 words
    # too (the short-message path a few lines below only ever checked this
    # for itself).
    from backend.services.rag import parse_comparison_query as _big_plan_cmp
    if _big_plan_cmp(text):
        return None
    if len(words) < 14:
        _apps_s = list(dict.fromkeys(_APP_NO_RE.findall(text)))
        _m = _multi_app_plan(text, _apps_s) if len(_apps_s) >= 2 else None
        if _m:
            return _m
        return _compound_split(text) if not _neg_scope.parse(text)[0] and not _parse_negation(text)[1] and not re.search(
            r"\b(?:compare|difference|between|versus|vs)\b", text, re.IGNORECASE) else None
    apps = []
    for m in _APP_NO_RE.findall(text):
        if m not in apps:
            apps.append(m)
    tamil = bool(re.search(r"[\u0B80-\u0BFF]", text))
    clauses = []

    def with_app(clause: str, app: str) -> str:
        c = clause.strip(" ,.;:?")
        if not c or _APP_NO_RE.search(c):
            return c
        c = re.sub(r"\s+for\s+(?:it|them|this\s+\w+|the\s+same\s+\w+|that\s+\w+)$", "", c, flags=re.IGNORECASE)
        return f"{app} {c} என்ன" if tamil else f"{c} of {app}"

    # 1) numbered items: "1) ... 2) ... 3) ..."
    marks = list(re.finditer(r"(?:(?<=\s)|^)\(?(\d{1,2})[.)]\s+(?=\S)", text))
    if len(marks) >= 2 and [int(m.group(1)) for m in marks[:2]] == [1, 2]:
        tail = _BIG_LANG_TAIL.search(text)
        body = text[:tail.start()] if tail else text
        marks = list(re.finditer(r"(?:(?<=\s)|^)\(?(\d{1,2})[.)]\s+(?=\S)", body))
        last_app = apps[0] if apps else None
        for i, m in enumerate(marks):
            seg = body[m.end(): marks[i + 1].start() if i + 1 < len(marks) else len(body)].strip(" ,.;")
            found = _APP_NO_RE.findall(seg)
            if found:
                last_app = found[-1]
            elif last_app:
                seg = with_app(seg, last_app)
            clauses.append(seg)
        return clauses[:_BIG_MAX_CLAUSES] if len(clauses) >= 2 else None
    tail = _BIG_LANG_TAIL.search(text)
    body = text[:tail.start()] if tail else text
    multi = _multi_app_plan(body, apps)
    if multi:
        return multi
    if len(apps) >= 2 and ':' not in body and not re.match(r"^\W*(?:details?\s+of|show|compare)\b", body, re.IGNORECASE):
        prose = _prose_plan(body, apps)
        if prose:
            return prose
    # 2) "<head> : f1, f2, f3 ..."  /  "<head> tell me f1, f2, f3 ..."
    head, rest = None, None
    inc = re.search(r"\b(?:including|include|along\s+with|covering)\b", body, re.IGNORECASE)
    if inc and len(apps) >= 2 and not _APP_NO_RE.search(body[inc.end():]) and len(_BIG_SEP.split(body[inc.end():])) >= 2:
        head, rest = body[:inc.end()], body[inc.end():]           # "details of A, B, C including f1, f2 ..."
    elif ":" in body and len(_BIG_SEP.split(body.split(":", 1)[1])) >= 3:
        head, rest = body.split(":", 1)
    elif apps and re.match(r"^\W*\d{4}/\d{3,4}/\d{1,3}/\d+\s+\S", body) and len(_BIG_SEP.split(body)) >= 4:
        m0 = _APP_NO_RE.match(body.lstrip())
        head, rest = apps[0], body.lstrip()[len(apps[0]):]         # "<app> field1, field2, field3 ..."
    else:
        m = _BIG_VERB.match(body)
        if m:
            _vb_pieces = [p.strip(" ,.;?") for p in _BIG_SEP.split(m.group("rest")) if p and p.strip(" ,.;?")]
            # "What is the status of my pending applications? <three or more
            # comma-separated sentences of unrelated prose>" matched this
            # branch on the bare opener "What is" alone, with no check that
            # what followed looked like a column/field list at all -- ONE
            # real question, immediately followed by filler text that
            # happens to contain commas, was carved into several "fields"
            # and each unrelated fragment answered as its own question
            # (several avoidable LLM calls for text that named no request of
            # its own). A genuine field list is short per item ("status",
            # "survey number", "subdivision number"); real prose is not --
            # requiring most pieces to be <=6 words keeps "the application
            # number, survey number, and subdivision number" (3/3 short)
            # while rejecting a sentence that only happens to contain commas.
            _short_enough = sum(1 for p in _vb_pieces if len(p.split()) <= 6)
            if (len(_vb_pieces) >= 3 and _short_enough >= max(2, len(_vb_pieces) - 1)):
                head, rest = m.group("head"), m.group("rest")
    if rest is not None:
        fields = [f.strip(" ,.;?") for f in _BIG_SEP.split(rest) if f and f.strip(" ,.;?")]
        # "including"/"include" is a list LEAD-IN, not a field name of its own
        # ("...applications, including application number, service code,
        # ...") -- left unstripped, "including application number" failed
        # the bare-column-noun check below and the whole message was split
        # into unscoped sub-questions instead of staying one scoped listing.
        fields = [re.sub(r"^(?:also|and|then|the|me|including|include)\s+", "", f, flags=re.IGNORECASE) for f in fields]
        fields = [re.sub(r"\s*(?:என்ன|யார்)\s*$", "", f) for f in fields]
        # "IGRS owner name, IGRS form 6 no, patta no, share, relative": the short fields after a qualified one keep its qualifier
        qual_rx = re.compile(r"^(IGRS|TSLR|F-?line|SD|new\s+owner|old\s+owner|returned\s+owner|ZDT|SIS|DIS|RDO)\b", re.IGNORECASE)
        qual = None
        qualified = []
        for f in fields:
            qm = qual_rx.match(f)
            if qm:
                qual = qm.group(1)
            elif re.match(r"^(?:does|do|is|are|did|what|which|who|when|how|show|tell|give|why|where|any|also)\b", f, re.IGNORECASE):
                qual = None
            elif qual and len(f.split()) <= 3:
                f = f"{qual} {f}"
            qualified.append(f)
        fields = qualified
        merged_fields = []
        for f in fields:
            # "does it match the applicant, the chitta record and the new owner record": one comparison, not three questions
            if (merged_fields and re.match(r"^(?:does|do|is|are|did)\b.*\b(?:match|same|differ\w*|consistent|compare\w*)\b", merged_fields[-1], re.IGNORECASE)
                    and not re.match(r"^(?:does|do|is|are|did|what|which|who|when|how|show|tell|give|why|where|any)\b", f, re.IGNORECASE)):
                merged_fields[-1] = merged_fields[-1] + " and " + f
            else:
                merged_fields.append(f)
        fields = [x for f in merged_fields for x in (_compound_split(f) or [f])]
        if len(fields) < 3:
            return None
        if not apps:
            # "show applications in ward 001 as a table with application
            # number, status, survey number, subdivision number, and
            # submission date" names no application at all -- the "with ..."
            # list is the COLUMNS of one listing request, not several
            # independent questions. Split anyway, it answered "1.
            # applications in ward 001 as a table with application number"
            # as its own nonsense clause. This no-application fallback
            # exists for real jurisdiction-wide asks bundled together
            # ("my pending count, my workload and my overdue visits"); a
            # message that is itself one ordinary listing/table request is
            # left whole so the register-listing handler renders it (with
            # its own fixed column set -- the column list on a FIRST turn
            # is not yet honoured, only on a follow-up; see
            # `followup_context.field_projections`). Narrowed to the shape
            # where every field AFTER the first is a bare column noun ("status",
            # "survey number", "subdivision number", "submission date") and
            # none of them --- unlike a genuine bundle of separate asks
            # ("how many are CSC", "the oldest one") -- is itself a question;
            # the listing's own request sentence (which also carries the
            # FIRST column name) stays in `fields[0]` and is dropped along
            # with the rest. Without this narrowing the same no-application
            # fallback below (for real bundled jurisdiction-wide asks) was
            # defeated for every one of them too.
            if (_is_register_wide_listing(body) and len(fields) >= 2
                    and all(_is_bare_column_field(f) for f in fields[1:])):
                return None
            # "application number, survey number, and subdivision number of
            # the second overdue visit" -- ordinary English drops a shared
            # trailing reference ("of X") from every item in a list but the
            # last one. Without reattaching it, only the LAST split clause
            # (whichever field happened to end the sentence) kept the
            # ordinal/row/visit reference, and every field before it was
            # asked bare -- "application number" alone, with no idea which
            # record it meant, answered from the officer's whole desk queue
            # instead of the one row the sentence actually named. Scoped to
            # when no OTHER field already carries its own "of"/"for" clause,
            # so a genuine bundle of independent asks ("the oldest one, and
            # the fee total for CSC applications") is not rewritten.
            if len(fields) >= 2:
                _tail = re.search(r"\s+(?:of|for)\s+(?:the\s+)?\S.*$", fields[-1], re.IGNORECASE)
                if _tail and not any(re.search(r"\s+(?:of|for)\s+\S", f, re.IGNORECASE) for f in fields[:-1]):
                    _suffix = fields[-1][_tail.start():].strip()
                    _head_field = fields[-1][:_tail.start()].strip()
                    if _head_field:
                        fields = [f"{f} {_suffix}" for f in fields[:-1]] + [f"{_head_field} {_suffix}"]
            return fields[:_BIG_MAX_CLAUSES]
        details_head = bool(re.search(r"\b(?:full\s+)?details?\s+of\b", head, re.IGNORECASE))
        if details_head and len(apps) * (len(fields) + 1) > _BIG_MAX_CLAUSES:
            return [f"show details of {a}" for a in apps][:_BIG_MAX_CLAUSES]
        if len(apps) * len(fields) > _BIG_MAX_CLAUSES:
            fields = fields[: max(1, _BIG_MAX_CLAUSES // len(apps))]
        out = []
        if details_head:
            out += [f"show details of {a}" for a in apps]
        for f in fields:
            if re.search(r"\b(?:which|who)\b.*\b(?:first|last|older|oldest|newest|more|most|less)\b", f, re.IGNORECASE) and len(apps) >= 2:
                out.append(f"which is older, {apps[0]} or {apps[1]}" if re.search(r"first|older|oldest", f, re.IGNORECASE) else f"{f} {' '.join(apps)}")
                continue
            for a in apps:
                out.append(with_app(f, a))
        return out[:_BIG_MAX_CLAUSES]
    # 3) several questions in a row
    qs = [re.sub(r"^(?:then|and)\s+", "", q.strip(), flags=re.IGNORECASE) for q in re.split(r"(?<=\?)\s+", body) if q.strip()]
    if len(qs) >= 3:
        return qs[:_BIG_MAX_CLAUSES]
    if not _neg_scope.parse(text)[0] and not _parse_negation(text)[1]:
        return _prose_plan(body, apps) or _compound_split(body)
    return None


_PROSE_SEP = re.compile(r"\s*(?:,|;|\s-\s|\bthen\b|\bafter\s+that\b|\baprm\b|\bapram\b|\bappuram\b)\s*", re.IGNORECASE)
_PROSE_NOISE = re.compile(
    r"^(?:for|about|regarding|hi|hello|hey|sir|madam|please|pls|kindly|nan|naan|can\s+you|could\s+you|i\s+(?:want|need)\s+to\s+know|i\s+am\s+checking\s+file|"
    r"tell\s+me|give\s+me|also|and|so|ok|okay)\b\s*", re.IGNORECASE)
_PROSE_TAIL = re.compile(r"\s*(?:\bthanks?\b|\bthank\s+you\b|because\s+the\s+party\s+is\s+asking|pathi\s+kekkanum|kekkanum|today)\W*$", re.IGNORECASE)
_PROSE_ASK = re.compile(r"\b(?:what|who|which|when|where|why|how|is|are|does|do|did|has|have|whether|if|status|owner|share|patta|number|no|name|details?|"
                        r"record|recommend\w*|match\w*|sketch|challan|sro|merge|mobile|address|date|enna|yaar|irukka|irukkaa|aagutha)\b|என்ன|யார்",
                        re.IGNORECASE)


def _prose_plan(body: str, apps):
    """A long sentence of comma-joined questions about one or more applications: each piece is asked of the application
    named most recently before it ("... for B especially challan, sro" asks B, not A)."""
    if not apps or len(_PROSE_SEP.split(body)) < 3:
        return None
    cur, out = None, []
    for part in _PROSE_SEP.split(body):
        if not part or not part.strip():
            continue
        found = _APP_NO_RE.findall(part)
        if found:
            cur = found[-1]
            part = _APP_NO_RE.sub(" ", part)
        part = re.sub(r"\b(?:give\s+the\s+same\s+for|the\s+same\s+for|same\s+for|especially|particularly|for\s+file|file)\b", " ", part, flags=re.IGNORECASE)
        part = re.sub(r"\s+", " ", part).strip(" ,.;:-?")
        for _ in range(6):
            part = _PROSE_NOISE.sub("", part).strip(" ,.;:-?")
        part = _PROSE_TAIL.sub("", part).strip(" ,.;:-?")
        if len(part.split()) < 1 or not _PROSE_ASK.search(part):
            continue
        for sub in (_compound_split(part) or [part]):
            out.append(f"{sub} of {cur}" if cur and not re.search(r"[\u0B80-\u0BFF]", sub) else (f"{cur} {sub} என்ன" if cur else sub))
    return out[:_BIG_MAX_CLAUSES] if len(out) >= 3 else None


async def _answer_big(clauses, message, session_id, officer, db, chat_history):
    import html as _html
    hist = list(chat_history or [])
    results = []
    for clause in clauses:
        try:
            res = await _cb()._single_turn(clause, session_id, officer, db, hist)
        except Exception as exc:  # one failing question must not lose the others
            logger.warning(f"big message: clause failed ({type(exc).__name__}): {clause[:60]}")
            res = {"response": "(could not answer this part)", "language": detect_language(clause)}
        results.append((clause, res))
        hist.append({"role": "user", "content": clause})
        hist.append({"role": "assistant", "content": res.get("response") or ""})
    body = []
    for n, (clause, res) in enumerate(results, 1):
        body.append(f"<div><strong>{n}. {_html.escape(clause)}</strong></div>\n{res.get('response') or ''}")
    out = dict(results[0][1])
    out.update({"response": "\n\n".join(body), "intent": "multi_request", "table_data": None})
    return out


def _plan_requests(message: str):
    """None (answer the message as it is), ("focus", clause) or ("multi", [clauses])."""
    text = (message or "").strip()
    words = text.split()
    if _cm_strong(text) or _oldowner_qa.attributes(text) == [_oldowner_qa.COMPARE]:  # one comparison, not several questions
        return None
    if len(words) < 6 or re.search(r"\d{4}/\d{3,4}/\d{1,3}/\d+.*\band\b.*\d{4}/\d{3,4}/", text):
        return None
    # "show only application number and subdivision number" is ONE per-row
    # column-selection request over the carried list -- `field_projections()`
    # already reads the whole message as such and `_process_chat_impl`'s own
    # follow-up layer answers it correctly in one table. Splitting it here on
    # the "and" first (this function's own `_REQ_SPLIT`, with no concept of a
    # column list) turned it into two separate "requests" -- "application
    # number" (answered as its own one-column table) and a bare "subdivision
    # number." with no row left to be a fragment of, which then asked "which
    # one do you mean?" instead of adding the column to the first table.
    if fctx.field_projections(text) is not None:
        return None
    if _neg_scope.parse(text)[0] or _parse_negation(text)[1]:
        return None      # "pending but not NISD" / "excluding rescheduled visits" is one request, not two
    # "applications from the same month BUT different channels" is one compound-filter
    # question, not two: _REQ_SPLIT's bare `\bbut\b` split point turned it into "...from the
    # same month" + "different submission channels?" and answered each as an unrelated
    # fresh request, producing a garbled, non-responsive reply. "same ... but different ..."
    # (either order) names one condition with two clauses, not two separate asks.
    if re.search(r"\bsame\b.{0,40}\bbut\s+different\b|\bdifferent\b.{0,40}\bbut\s+same\b", text, re.IGNORECASE):
        return None
    # "compare ward 102 and ward 103", "difference between A and B" are ONE request with two sides
    if len(words) <= 25 and re.search(r"\b(?:compare|compared|comparison|versus|vs\.?|difference\s+between|between)\b", text, re.IGNORECASE):
        return None
    # clauses; a fragment of three words or fewer that is not itself a request ("nisd" in
    # "compare isd and nisd") is joined back onto the request before it
    parts = _REQ_SPLIT.split(text)
    clauses = [p.strip() for p in parts if p and p.strip()]
    merged = []
    for c in clauses:
        led = _REQ_LEAD.sub("", c).strip()
        is_cue = bool(_REQ_CUE_EN.match(led)) or bool(_REQ_CUE_TA.search(c)) or bool(_howto_qa.match(c))
        if (not is_cue and len(c.split()) >= 2 and parse_intent(c) not in _NOT_A_REQUEST
                and not re.search(r"\d{4}/\d{3,4}/\d{1,3}/\d+", text)
                and not re.search(r"\b(?:for|of|to|in|with|about|from|on)\b", c, re.IGNORECASE)
                # A clause that STARTS with a bare number ("45 days overdue?")
                # is a trailing detail of whatever the previous clause named,
                # never a request of its own -- "Who gets notified at Level 3
                # escalation, 45 days overdue?" split on the comma into
                # exactly this shape and the fallback below (meant for a
                # verb-less noun request like "isd count") wrongly promoted
                # the second half to its own request, so one question was
                # answered twice, each half wrong: the first half alone
                # (no "45 days overdue" context) misrouted to a bare
                # applications listing, and the second half, with no subject
                # of its own, asked "which application do you mean?".
                and not re.match(r"^\d", c)):
            is_cue = True  # "isd count", "my workload" -- a request without a verb
        if merged and not is_cue and len(c.split()) <= 4 and merged[-1][1]:
            merged[-1] = (merged[-1][0] + " " + c, True)
        else:
            merged.append((led if is_cue and _REQ_CUE_EN.match(led) else c, is_cue))
    cued = [c for c, is_cue in merged if is_cue]
    _NOT_REQUEST_INTENTS = frozenset({"greeting", "small_talk", "unknown_message", "help"})
    real = []
    for c in cued:
        intent = parse_intent(c)
        if intent in _NOT_REQUEST_INTENTS:
            continue
        if intent == "general_query":
            led_c = _REQ_LEAD.sub("", c).strip()
            if _REQ_CUE_EN.match(led_c) or _REQ_CUE_TA.search(c) or _howto_qa.match(c) or "?" in c:
                real.append(c)
        else:
            real.append(c)
    if len(real) >= 2 and not any(
            re.search(r"\b(?:it|its|this|that|them|their|those|these|above)\b", c, re.IGNORECASE)
            or (parse_intent(c) == "application_status" and not re.search(r"\d{4}/\d", c)) for c in real):
        return ("multi", real[:5])
    if len(real) >= 2:
        real = real[-1:]
    if len(words) > 25:
        if len(real) == 1:
            return ("focus", real[0])
        if cued:
            return ("focus", cued[-1])
        return ("focus", "")
    return None


# ── Several follow-up operations in one message ─────────────────────────────
# "along with district and sort by date descending", "only NISD, sort newest first and add ward",
# "remove row 2 and sort by survey number", "how many are approved and how many are ISD".
# Each clause is a follow-up operation on what is on screen; they are applied IN ORDER, each one
# against the result of the one before (every answer records its own context), and the officer gets
# the final table plus every count / remark answer.
_OP_START = (r"(?:sort|order|arrange|reverse|latest|oldest|newest|earliest|only|just|show|list|display|give|add|along|"
             r"with|without|remove|exclude|not|except|hide|drop|skip|keep|filter|dont|don't|do\s+not|no|how\s+many|"
             r"count|which|what|is\s+there|are\s+there|any|remarks?|comments?|why|reason|first|last|next|previous)")
_COMPOUND_SPLIT = re.compile(rf"\s*(?:,|;|&|\band\b|\bthen\b|\balso\b|\bplus\b)\s+(?={_OP_START}\b)", re.IGNORECASE)


def _add_as_along(text: str) -> str:
    """"add taluk" / "also include ward and status" -> "along with taluk": the same request, said the way
    the column rules (which build on the columns already shown) read it."""
    low = (text or "").strip().lower().rstrip("?.! ")
    if _ADD_COLUMNS_RE.fullmatch(low):
        cols = re.sub(r"^(?:also\s+)?(?:add|include)\s+(?:the\s+)?|\s+(?:also|too)$", "", low)
        return f"along with {cols}"
    return text


def _split_compound(message: str) -> list:
    parts = [p.strip(" ,;.") for p in _COMPOUND_SPLIT.split(message or "") if p and p.strip(" ,;.")]
    merged = []
    for p in parts:      # "not A and not B" is ONE negation, not two operations
        if merged and _neg_scope.parse(merged[-1])[0] and _neg_scope.parse(p)[0] \
                and not re.search(r"\b(?:sort|order|arrange|along|add|latest|oldest|newest|reverse)\b", p, re.IGNORECASE):
            merged[-1] += " and " + p
        else:
            merged.append(p)
    return merged


def _is_followup_op(clause: str) -> bool:
    c = _fragment_typo_fix(clause) or clause
    low = c.strip().lower().rstrip("?.! ")
    return bool(
        fctx.classify(c) != fctx.FOLLOWUP_NONE
        or fctx.is_sort_fragment(c) or fctx.normalise_sort_negation(c)[0] != c
        or fctx.negation_normalise(c) != c or _neg_scope.parse(c)[0] or _parse_negation(c)[0] or _parse_negation(c)[1]
        or _FV_STATUS_FRAGMENT_RE.fullmatch(low) or _ADD_COLUMNS_RE.fullmatch(low)
        or fctx.field_projections(c) is not None
        or _asked_workflow_remarks(c)
        or _number_qa._COUNT.search(c) or _number_qa._PCT.search(c) or _number_qa.bare_scope_terms(c)
        or _FV_COUNT_RE.search(c))


_OO_EXISTS_RE = re.compile(
    r"\bdoes\s+(?:it|this|that)\s+exist\b|\bis\s+there\s+(?:a|any)\s+record\b|\bdoes\s+(?:a|an)\s+"
    r"(?:old[\s-]?owner|chitta)\s+record\s+exist\b", re.I)
_OO_FULL_RE = re.compile(r"\b(?:full|complete|all)\s+details?\b|\beverything\b", re.I)


def _oo_exists_parse(message, chat_history=None):
    """"does it exist?" / "is there a record?" / "full details?" right after an
    old-owner answer -- the EXISTS / FULL continuations. Neither question carries an
    "owner"/"chitta"/"relative" word for oldowner_qa.attributes()'s gate to recognise
    on its own, so without this: "does it exist?" fell all the way to the LLM
    fallback, which answered a DATABASE existence question as a document-corpus
    search and claimed the record "does not exist in the documents" -- flatly wrong,
    right after the officer had been shown 4 owner rows; and "full details?" was
    claimed by `subdivision_qa` instead (it has no such gate at all), answering with
    the plot/subdivision card instead of the owner record just being discussed.
    Scoped to only fire when the PREVIOUS answer was specifically an old-owner card,
    so a bare "full details?" after a survey or subdivision answer still goes there."""
    if not chat_history:
        return None
    is_exists = bool(_OO_EXISTS_RE.search(message or ""))
    is_full = bool(_OO_FULL_RE.search(message or ""))
    if not is_exists and not is_full:
        return None
    for m in reversed((chat_history or [])[-4:]):
        if isinstance(m, dict) and str(m.get("role", "")).lower() in ("assistant", "ai", "bot"):
            if "Old-owner record for" in (m.get("content") or "")[:300]:
                return [_oldowner_qa.EXISTS] if is_exists else [_oldowner_qa.FULL]
            break
    return None


_VISIT_WORD_RE = re.compile(r"\bvisits?\b|\binspections?\b", re.IGNORECASE)


def _is_ordinal_visit_reference(message: str) -> bool:
    """"the second overdue visit" / "the last one" naming a FIELD-VISIT list
    position -- a third shape alongside `_is_bare_concept_definition` and
    `_is_register_wide_listing` that must not resolve through
    `_extract_app_number_from_context`'s stale chat_history scan.

    The gap this closes: "What is the survey number and subdivision number of
    the second overdue visit?" carries no application number, so the scan
    picked whichever application chat_history mentioned first -- the top row
    of a prior, UNFILTERED field-visit summary (a completed visit from
    months ago) -- and `subdivision_qa` answered about that file, ignoring
    "second" and "overdue" entirely. Both words point at a position in the
    officer's field-visit list, which `rag.parse_intent` already resolves
    correctly on a fresh question (routed to `fv_visit_plan`, which re-queries
    and sorts overdue-first) and which `followup_context.resolve()` already
    resolves correctly against a carried visit list (also sorted
    overdue-first) -- neither of which this early hook ever consults.
    Standing aside here lets whichever of those two actually applies run.

    Scoped to messages that carry no application number of their own, the
    same precondition `_is_register_wide_listing` documents.
    """
    msg = message or ""
    if extract_application_number(msg) or _has_application_back_reference(msg):
        return False
    if not _VISIT_WORD_RE.search(msg):
        return False
    return bool(fctx.mentions_ordinal(msg) or fctx.mentions_slice(msg))


async def _maybe_resolve_fresh_visit_ordinal(db, officer, message: str, intent: str,
                                             chat_history, session_id, language: str) -> str:
    """"Show the survey number and subdivision number of the second overdue
    visit." asked as the FIRST field-visit question of the conversation --
    `_is_ordinal_visit_reference` is true, but there is no prior answer for
    the pre-intent follow-up pipeline to resolve it against (that pipeline
    only ever sees a `FollowupContext` built from the PREVIOUS turn's
    structured data), so `intent` parses fresh to `fv_visit_plan` and its
    handler renders the whole unscoped plan -- a question that named one row
    answered with all of them.

    Mirrors exactly what a genuine SECOND turn already does, one step early:
    compute the same plan `fv_visit_plan`'s own handler would (same
    `_fetch_visit_plan`, same overdue-first ordering `get_visit_plan` already
    returns), resolve the ordinal against it with the same
    `followup_context.resolve()` a real follow-up uses, and if it resolves,
    rewrite the message to NAME that application -- the identical rewrite
    `_apply_followup_resolution` performs for a real follow-up. The rest of
    the pipeline (`_early_subdivision_answer`, `application_status`, the
    field map) then answers it exactly as it would on turn two, from the
    officer-scoped, authoritative database query -- nothing here renders an
    answer itself or invents a value.

    Stands aside -- returning `message` unchanged -- whenever there is no
    ordinal/slice reference, the plan has no overdue/awaiting rows to point
    at, or the position named does not exist: in each of those cases
    `fv_visit_plan`'s own full-listing answer is the correct, nothing-guessed
    response, exactly as it already is for an explicit "show all overdue
    visits".
    """
    # An ordinal+field visit-queue question can parse fresh to either
    # `fv_visit_plan` (via `parse_visit_plan_query`'s own phrasings, "which
    # application should I visit next"), `fv_overdue_inspections` (a bare
    # "overdue visit(s)" phrasing, see `_has_fv_terms` in rag.py), or the
    # generic `field_visits` -- the last one is what a single FIELD split off
    # by `_big_plan()` lands on ("application number of the second overdue
    # visit" carries no list verb of its own once "give me ... and ..." is
    # split away from it, and is too long to count as the bare-noun-phrase
    # shape `rag.py` also accepts). `_is_ordinal_visit_reference` below is
    # the real gate (it already requires both a visit word and an ordinal/
    # slice reference), so admitting `field_visits` here does not widen what
    # this function acts on -- only which parses of the SAME shape reach it.
    if intent not in ("fv_visit_plan", "fv_overdue_inspections", "field_visits") \
            or not _is_ordinal_visit_reference(message):
        return message
    _vp_ward, _vp_block = await _geo_scope(db, session_id, message, chat_history, officer)
    plan = await _fetch_visit_plan(db, message, officer, ward_number=_vp_ward, block_number=_vp_block)
    _vp_ctx = fctx.build_context("fv_visit_plan", plan)
    if not _vp_ctx:
        return message
    _vp_res = fctx.resolve(message, _vp_ctx, language)
    if not (_vp_res.resolved and _vp_res.application_number):
        return message
    logger.info(
        f"First-turn visit-plan ordinal resolved: '{message}' -> application "
        f"{_vp_res.application_number}")
    return _apply_followup_resolution(message, _vp_res, None)


_BARE_APP_STATUS = re.compile(
    r"^\W*(?:(?:what|whats)(?:\s+is|\s+are)?\s+)?(?:the\s+)?(?:current\s+|present\s+|latest\s+)?"
    r"(?:applcation|aplication|application)\s+(?:status|stauts|satus)\W*$", re.IGNORECASE)


def _app_evidence_in_scope(message: str, chat_history: list = None) -> bool:
    """Whether there is any real reason to read this turn as being about one
    application: an explicit number in the message, a back-reference pronoun
    ("this application", "it"), or a number named in the last 6 turns.

    A second, independent check at the `_cm_strong()` / `_bare_app_status()`
    call site -- belt and suspenders alongside the guards already inside
    those functions. `_cm_strong()` delegates to ten different claimant
    parsers (`_tslr_parse`, `_fline_qa.claims`, `_appinfo_qa.claims`, ...),
    and auditing every one of them for a bare-definition false positive the
    way `_cm_strong()`'s own DEFN_SHAPE_RE guard was added for is a standing
    risk, not a one-time fix. This stops ANY of them from forcing a fresh,
    evidence-free question into `application_status` -- which only ever asks
    the officer for a number they were never going to give, since the
    question was never about one file.
    """
    if extract_application_number(message) or _has_application_back_reference(message):
        return True
    for m in (chat_history or [])[-6:]:
        content = (m.get("content") if isinstance(m, dict) else str(m)) or ""
        if _ANY_APP_NUMBER_RE.search(content):
            return True
    return False


def _bare_app_status(message, chat_history=None) -> bool:
    """"what is the current application status?" right after an answer about ONE application
    asks about that application, not the officer's pending list."""
    if not _BARE_APP_STATUS.match(message or ""):
        return False
    for m in reversed((chat_history or [])[-4:]):
        found = set(_cmflow_qa._APP_NO.findall((m.get("content") if isinstance(m, dict) else str(m)) or ""))
        if found:
            return len(found) == 1
    return False
