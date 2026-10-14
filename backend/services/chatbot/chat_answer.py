"""
Answer-building stage of the NON-STREAMING chat pipeline (process_chat).

Takes the structured data the dispatch stage fetched and decides how it
becomes an answer: whether RAG context is needed, whether the question asks
for one field (and which), negation scoping and result limits, and the
deterministic single-field answers. It produces the html_response / response
text / LLM prompt that the orchestrator then delivers; it never persists or
calls the LLM itself.
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
from . import attachment_qa
from .agent import AgentUnavailable, run_agent, run_agent_stream
from backend.services.readonly_guard import chat_turn
from . import neg_scope as _neg_scope
from . import negq as _negq
from . import calc_qa as _calc_qa
from . import howto_qa as _howto_qa
from . import card_html as _card_html
from . import unheld_qa as _unheld_qa
from . import recheck as _recheck
from . import number_qa as _number_qa
from . import stats_qa as _stats_qa
from . import compare_followup as _compare_followup
from . import qualifier_guard as _qualifier_guard
from backend.services import followup_context as fctx
from . import semantic_intent
from . import subdivision_qa as _subdivision_qa
from . import oldowner_qa as _oldowner_qa
from . import cmflow_qa as _cmflow_qa
from . import appinfo_qa as _appinfo_qa
from . import fline_qa as _fline_qa
from . import merge_qa as _merge_qa
from . import crosscheck_qa as _crosscheck_qa
from . import igrs_owner_qa as _igrs_owner_qa
from . import newowner_qa as _newowner_qa
from . import fulltransfer_qa as _fulltransfer_qa
from . import sdattach_qa as _sdattach_qa
from . import tslr_qa as _tslr_qa
from . import uamod_qa as _uamod_qa
from . import natham_qa as _natham_qa
from . import othercode_detail as _othercode_detail
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
from .chat_application_queries import (
    _DURATION_RE,
    _asked_day_of_week,
    _asked_decision_date,
    _asked_month_name,
    _asked_name_part,
    _asked_street_name,
    _asks_distinct_service_count,
    _day_of_week_answer,
    _decision_date_answer,
    _field_visit_answer,
    _fuzzy_match_all_fields,
    _fv_detail_answer,
    _month_name_answer,
    _name_part_answer,
    _street_name_answer,
    _subdivision_answer,
)
from .chat_corrections import (
    _jurisdiction_yn_answer,
    _payment_composite_answer,
    _status_yes_no,
)
from .chat_followups import (
    _extract_app_number_from_context,
)
from .chat_pipeline import (
    StageOutcome,
    _UNSET,
)
from .chat_response_formatting import (
    _apply_result_limit,
    _format_count_intro,
    _is_count_only_query,
)
from .chat_shared import (
    _render_all_service_code_answer,
)
from .chat_smalltalk import (
    _age_sla_answer,
    _workflow_history_answer,
)
from .chat_untracked_fields import (
    _area_unit_answer,
    _asked_area_in_unit,
    _asked_submission_channel_field,
    _asked_untracked_appinfo_field,
    _asked_untracked_source_field,
    _asked_untracked_wf_field,
    _ip_answer,
    _missing_field_answer,
    _render_submission_channel_answer,
    _sis_recommendation_text,
    _untracked_appinfo_field_answer,
    _untracked_source_field_answer,
    _untracked_wf_field_answer,
)

# Logged under chatbot's name so pipeline log records are unchanged.
logger = get_logger("backend.services.chatbot")


# Names still defined in chatbot.py (and the ones tests patch there) are
# looked up on the chatbot module at call time: chatbot.py imports this
# module, so importing them here at load time would be circular, and a
# frozen import would also miss a test's patch.
def _cb():
    from backend.services import chatbot
    return chatbot


async def build_answer(
        *,
        _gate_app_number,
        chat_history,
        db,
        intent,
        language,
        message,
        officer,
        response_text,
        structured_data,
        _field_keywords=_UNSET,
        _utf_key=_UNSET,
        is_tamil=_UNSET,
        today=_UNSET,
) -> StageOutcome:
    """Turn the dispatched data into an answer (HTML, text, or an LLM prompt).

    Extracted verbatim from chatbot._process_chat_impl (L3336-4460 before
    extraction). Reads: _field_keywords, _gate_app_number, _utf_key, chat_history, db, intent, is_tamil, language, message, officer, response_text, structured_data, today.
    Hands back (only if bound): _bypass_html, app_type, context_used, full_prompt, html_response, is_tamil, response_text, structured_data, survey_no.
    """
    if _field_keywords is _UNSET:
        del _field_keywords
    if _utf_key is _UNSET:
        del _utf_key
    if is_tamil is _UNSET:
        del is_tamil
    if today is _UNSET:
        del today
    _sd = structured_data or {}
    _meta_only = {"found", "message", "query_type", "searched_number",
                  "suggestions", "needs_confirmation"}
    has_db_results = bool(
        _sd
        and _sd.get("found", True)
        and (_sd.get("count", 0) > 0 if "count" in _sd
             else any(k not in _meta_only for k in _sd))
    )
    # 8, not 5: a section's detail can rank just below its overview, and at
    # k=5 the ISD timeline fell outside the window while the NISD one stayed
    # in -- the model then quoted NISD's 15-20 days for an ISD question.
    # 6 chunks of 2000 chars is already ~3k tokens of grounding; 8 mostly
    # added overlap-duplicated text and, on an 8b model, diluted focus
    # while eating the context room the answer needs.
    rag_context = await get_rag_context_async(message, language, n_results=6) if not has_db_results else ""
    context_used = len(rag_context) > 0

    # Step 5: Try to build HTML directly from structured data (no LLM needed).
    # Skip the HTML path when the user is asking a specific question about
    # the data (interrogative queries).
    _msg_lower = message.lower()
    _interrogative_keywords = [
        "which", "what", "how many", "how much", "why", "who",
        # "when did we receive this file" is as pointed a question as "what
        # is its status" -- leaving "when" out sent it down the summary path.
        "when", "எப்போது",
        "where", "where is", "which department", "currently",
        "give me", "tell me", "show me", "get me", "how long", "how long it is",
        "how long is", "how long has", "pending for", "how long pending",
        "எந்த", "என்ன", "எத்தனை", "ஏன்", "யார்", "எவ்வளவு", "எத்தனை நாள்", "எவ்வளவு நாள்", "ஆச்சு",
    ]
    # Specific field keywords that indicate the user wants one piece of data (English + Tamil)
    _field_keywords = [
        "address", "mobile", "phone", "name", "status", "type",
        # bare "and the ip?" / "is that ip internal?" (ip_address, untracked)
        " ip ", " ip?", " ip.",
        "position", "received", "receive", "ward", "block",
        # A per-application field-visit question ("is field visit
        # scheduled?") is a field lookup like any other. Without these the
        # gate below never opens and the answer falls through to the full
        # details card, which never says whether a visit is booked.
        "field visit", "field-visit", "fieldvisit", "site visit", "inspection",
        "கள ஆய்வு", "கள ஆய்வு தேதி", "kalam aaivu", "field visit date",
        "stage", "date", "year", "survey", "applicant", "priority", "aadhaar",
        # Bare "day"/"month" are too broad (substrings of "today",
        # "yesterday", "birthday") -- multi-word phrases and weekday names
        # only, so this stays as narrow as the rest of the list.
        "which day", "what day", "day of the week", "day of week",
        "weekday", "weekend", "which month", "what month",
        "monday", "tuesday", "wednesday", "thursday", "friday",
        "saturday", "sunday",
        "reason", "overdue", "nisd", "isd", "pending", "long", "duration",
        "days", "since", "how long", "serial", "serial number", "serial_number",
        # Bare "can" removed -- it is also the commonest English modal
        # verb ("Can you explain?"), and a substring match made every
        # such question a false-positive field query answered with the
        # CAN number instead of what was actually asked. "can number" /
        # "can_number" below already cover the genuine CAN-lookup cues.
        "can number", "can_number", "patta", "patta number", "patta_number",
        "subdivision", "subdivision number", "subdivision_number", "current subdivision",
        "current_subdivision_number", "role", "role id",
        "role_id", "user", "user id", "user_id",
        "handling", "handled by", "assigned officer", "assigned to",
        "which officer", "responsible officer", "in charge",
        "கையாள்", "அதிகாரி",
        "service", "service_code", "district_code", "taluk_code", "village_code",
        "urban_unit_code", "ward_code", "block_code", "ward", "block",
        "town code", "town_code", "relative name", "relative's name", "relatives name",
        "returned", "return status", "sent back", "owner status", "uds details",
        "undivided share", "relationship of the owner", "relationship type",
        "urban unit", "urban unit code", "district code", "taluk code",
        "village code", "ward code", "block code",
        "source", "source_code", "source_name", "workflow_state",
        # submission channel + its derivation columns (submission_channel /
        # submission_source_name / submission_camp_flag) -- without a gate
        # keyword the field dispatch never opened and "how was X submitted /
        # which channel / camp flag" fell to the generic "could not find".
        "channel", "submission channel", "submission_channel", "camp flag",
        "camp_flag", "revenue camp", "how was it submitted", "who submitted",
        "filed through", "submitted through", "csc name", "operator name",
        "சமர்ப்பித்த முறை", "முகாம் கொடி", "எந்த வழி",
        "declared reason", "declared_reason",
        # Untracked workflow-action fields whose only cue otherwise lived in
        # _UNTRACKED_WF_FIELDS -- a yes/no phrasing with no WH-word ("was there
        # a review flag on X?") never opened this gate, so it fell to the
        # generic details card instead of the "not held" answer.
        "review flag", "review_flag", "flag for review", "flagged for review",
        "annual income", "annual_income", "yearly income",
        "father", "father name", "mother", "mother name", "dob",
        "date of birth", "birth", "gender", "sex", "occupation",
        "permanent address", "fee", "fee amount", "challan", "challan number",
        "payment", "payment mode", "payment amount",
        "form 6", "form6", "form 6 number", "igrs form 6", "igrs number",
        "merged application", "merge parent", "parent application", "merged with", "merge group",
        "new patta", "generated patta", "issued patta", "new patta number",
        "signed by", "who signed", "signature date", "signed date", "when signed",
        # Registration / transfer-order detail (transfer_urban_detail)
        "transfer reason", "transfer type", "reason for transfer",
        "registration place", "registration date", "registered", "registration",
        "registration document", "old patta", "old patta number",
        "order number", "transfer order", "transfer order number",
        "order date", "order remarks", "order remark",
        "sis recommendation", "sis remarks", "sis remark",
        "recommendation", "recommended", "remarks", "remark",
        "பதிவு இடம்", "பதிவு தேதி", "பதிவு", "பரிந்துரை", "பழைய பட்டா", "ஆணை",
        "தந்தை", "தாய்", "பிறந்த தேதி", "பாலினம்", "கட்டணம்", "நிரந்தர முகவரி", "படிவம் 6",
        # Tamil field keywords
        "முகவரி", "தொலைபேசி", "பெயர்", "நாமாகும்", "நாமம்", "நிலை", "வகை",
        "கட்டம்", "தேதி", "ஆண்டு", "கணக்கெண்", "சர்வே எண்", "விண்ணப்பதாரர்", "முன்னுரிமை",
        "காரணம்", "காலதாமத", "நிலுவை", "நிலுவையில்", "எவ்வளவு", "எத்தனை", "நாட்கள்", "நாள்", "ஆச்சு",
        "வரிசை எண்", "பட்டா எண்", "உட்பிரிவு எண்", "பயனர் ஐடி",
        "பங்கு ஐடி", "ஆதாரம்",
        "niluvai", "evvalavu", "ethanai", "naal", "naatkal",
        # Stage/location keywords
        "sd", "dis", "tahsildar", "sis", "department", "office",
        "right now", "currently", "current stage",
        # Tamil stage/location
        "அலுவலகம்", "இப்போது", "எங்கே",
    ]
    _is_interrogative = any(kw in _msg_lower for kw in _interrogative_keywords)
    _is_interrogative = _is_interrogative or any(
        phrase in _msg_lower for phrase in
        ["included in", "part of", "belong to", "contains", "உள்ளது", "உள்ளன",
         "right now", "currently at", "currently with", "which department"]
    )
    _has_field_keyword = any(kw in _msg_lower for kw in _field_keywords)
    _has_interrogative_phrase = any(
        kw in _msg_lower for kw in ["give", "tell", "show", "get", "what", "provide",
                                     "where", "which", "currently", "right now", "is this",
                                     "how", "how many", "days", "overdue"]
    )
    _has_app_number = bool(extract_application_number(message))
    _has_context_app = bool((_extract_app_number_from_context(message, chat_history, allow_implicit_continuation=True) or _gate_app_number))
    _is_short_field_query = _has_field_keyword and len(_msg_lower.split()) <= 6
    _asking_specific_field = _has_field_keyword and (_has_interrogative_phrase or _has_app_number or _has_context_app or _is_short_field_query)
    _is_interrogative = _is_interrogative or _asking_specific_field
    _is_multi_app = bool(structured_data and "multi_applications" in structured_data)
    _bypass_html = _is_interrogative and intent in ("application_status",) and not _is_multi_app
    _asking_for_count = _is_count_only_query(message)

    # "show the first two applications" -- a cap on how much of the list is
    # wanted. Applied here, over the rows the officer would have been shown,
    # so no handler above has to know about it.
    structured_data = (await _neg_scope.apply(structured_data, language, db, officer))
    structured_data = _apply_result_limit(structured_data, message, intent)

    if intent == "all_service_code_applications" and structured_data and not structured_data.get("error"):
        # Rendered here, after the negation and "first N" steps above, so
        # the table and the totals describe the rows actually kept.
        html_response = _render_all_service_code_answer(
            structured_data, language in ("ta", "tanglish"), count_only=_asking_for_count,
            distinct_services=_asks_distinct_service_count(message))
    elif _asking_for_count and intent in ("pending_applications", "isd_applications", "nisd_applications", "both_applications", "overdue_applications"):
        html_response = _format_count_intro(structured_data, language, message)
    else:
        # last_application answers in one sentence naming the file it found;
        # an HTML card would repeat it and hide the yes/no the officer asked for.
        html_response = ("" if (_bypass_html or intent in ("last_application", "fv_visit_plan", "all_service_code_applications"))
                         else build_html_response(structured_data, language, query=message))

    if html_response:
        response_text = html_response
        logger.info("Responded with direct HTML (LLM bypassed)")

    # ── Hardcoded direct answer for interrogative queries ──────────────
    # Build the answer in Python from structured_data so we never rely on
    # the LLM to correctly extract and present specific fields.
    if not html_response and _bypass_html and structured_data and structured_data.get("found", True):
        sd = structured_data
        _direct_answer_text = None
        is_tamil = language in ("ta", "tanglish")
        app_no   = sd.get("application_number") or sd.get("application_id") or extract_application_number(message) or ""
        app_type = sd.get("type", "")
        survey_no = sd.get("survey_no", "")
        # ── Check if user is asking about application but didn't provide number ──
        if not response_text and _asking_specific_field and intent == "application_status" and not app_no:
            # User is asking a specific question about an application but didn't provide the number
            is_tamil = language in ("ta", "tanglish")
            if is_tamil:
                response_text = "தயவுசெய்து விண்ணப்ப எண்ணை குறிப்பிடவும். (எ.கா: 2026/0154/28/NNNNNN)"
            else:
                response_text = "Please provide the application number (e.g., 2026/0154/28/NNNNNN) so I can help you with that information."
            logger.info("User asked about application field without providing app number - prompted for app number")

        if not response_text and intent == "application_status" and app_no:
            response_text = (_jurisdiction_yn_answer(_msg_lower, app_no, sd, language in ("ta", "tanglish"))
                             or _payment_composite_answer(_msg_lower, app_no, sd, language in ("ta", "tanglish"))
                             or _fv_detail_answer(_msg_lower, sd, app_no, language in ("ta", "tanglish"))
                             or _status_yes_no(_msg_lower, app_no, sd.get("status"), sd.get("stage") or sd.get("current_stage"),
                                               language in ("ta", "tanglish")) or response_text)

        # Workflow-history questions (who/first/latest/count/transitions/remarks) checked
        # ahead of the pending/overdue field-extraction block below, since a phrase like
        # "latest workflow action" would otherwise trip that block's "late" cue.
        if not response_text and intent == "application_status" and app_no:
            response_text = await _workflow_history_answer(db, officer, message, app_no, language in ("ta", "tanglish")) or response_text

        # ── Specific field extraction for application_status queries ──
        if not response_text and intent == "application_status" and app_no:
            # Check for pending duration / how long pending / overdue questions (higher priority)
            # "the first overdue visit" names a ROW, not a second question
            # about overdue status -- see `_ORDINAL_VISIT_PHRASE_RE`.
            _msg_lower_no_row_ref = _cb()._ORDINAL_VISIT_PHRASE_RE.sub(" ", _msg_lower)
            _is_pending_or_overdue_q = any(w in _msg_lower_no_row_ref for w in [
                "overdue", "late", "delay", "tardiness", "தாமதம்", "காலதாமத",
                "how long", "how many days", "pending", "duration", "since",
                "நிலுவை", "நிலுவையில்", "எவ்வளவு நாள்", "எத்தனை நாள்", "எவ்வளவு நாட்கள்",
                "எத்தனை நாட்கள்", "நாள் ஆச்சு", "நாட்கள் ஆச்சு", "ஆச்சு",
                # "evvalavu" / "ethanai" on their own only mean "how much" --
                # "evvalavu fee?" is a fee lookup, not a pending-duration
                # question. The day words below carry that sense.
                "niluvai", "naal", "naatkal"
            ])
            # A money question ("evvalavu fee", "kattanam ethana") never
            # asks how long the file has been pending.
            if any(w in _msg_lower for w in [
                "fee", "fees", "charge", "challan", "payment", "paid",
                "amount", "cost", "price", "கட்டணம்", "கட்டண", "kattanam",
            ]) or (re.search(r"\blate(?:st|r)\b", _msg_lower) and not re.search(
                    r"\b(?:overdue|late|delay\w*|pending|how\s+long|how\s+many\s+days|since)\b", _msg_lower)):
                # "late" matched inside "latest": "when was the latest workflow action" is not an overdue question
                _is_pending_or_overdue_q = False
            # "How long" on a file that is already decided asks how long it
            # TOOK, not how long it has been waiting -- there is no waiting
            # left. Answered "it is NOT overdue" before this, which is true
            # and beside the point.
            if (_is_pending_or_overdue_q
                    and str(sd.get("status", "")).lower() in ("approved", "rejected")
                    and _DURATION_RE.search(_msg_lower)
                    and not any(w in _msg_lower for w in
                                ("overdue", "late", "delay", "pending", "since"))):
                _is_pending_or_overdue_q = False
            if _is_pending_or_overdue_q:
                fv_info = sd.get("field_visit") or {}
                fv_date_str = fv_info.get("scheduled_date") or sd.get("field_visit_date")
                sub_date_str = sd.get("submission_date")
                from datetime import date as _date_mod
                today = _date_mod.today()

                fv_days_overdue = None
                fv_days_until = None
                if fv_date_str:
                    try:
                        fv_d = _date_mod.fromisoformat(str(fv_date_str)[:10])
                        if today > fv_d:
                            fv_days_overdue = (today - fv_d).days
                        else:
                            fv_days_until = (fv_d - today).days
                    except Exception:
                        pass

                app_sub_days = None
                if sub_date_str:
                    try:
                        sub_d = _date_mod.fromisoformat(str(sub_date_str)[:10])
                        app_sub_days = (today - sub_d).days
                    except Exception:
                        pass

                is_tamil = language in ("ta", "tanglish")
                app_status_str = str(sd.get("status", "")).lower()

                # Explicit pending duration question ("how long pending", "days pending", "ethanai naal niluvai")
                _asked_pending_explicit = any(w in _msg_lower for w in [
                    "how long", "how long it is pending", "how long is it pending", "how long pending",
                    "pending for", "days pending", "how many days pending", "how long has", "since submission",
                    "நிலுவை", "நிலுவையில்", "எவ்வளவு நாள்", "எத்தனை நாள்", "எவ்வளவு நாட்கள்",
                    "எத்தனை நாட்கள்", "நாள் ஆச்சு", "நாட்கள் ஆச்சு", "ஆச்சு",
                    "niluvai", "naal"
                ]) and not any(w in _msg_lower for w in ["overdue", "late", "delay", "tardiness", "தாமதம்", "காலதாமத"])

                if _asked_pending_explicit and app_sub_days is not None:
                    if app_status_str == "rejected":
                        if is_tamil:
                            response_text = f"விண்ணப்பம் {app_no} நிலுவையில் இல்லை — இது நிராகரிக்கப்பட்டது."
                        else:
                            response_text = f"Application {app_no} is no longer pending — it was rejected."
                    elif app_status_str in ["completed", "approved", "closed"]:
                        if is_tamil:
                            response_text = f"விண்ணப்பம் {app_no} நிலுவையில் இல்லை — இது ஏற்கனவே முடிவடைந்தது/அங்கீகரிக்கப்பட்டது."
                        else:
                            response_text = f"Application {app_no} is no longer pending — it has been completed and approved."
                    else:
                        response_text = _age_sla_answer(app_no, sd, today, is_tamil)
                    logger.info(f"Responded pending duration ({app_sub_days} days) for {app_no}")

                elif (fv_days_overdue is not None and fv_days_overdue > 0
                      # a completed visit or a decided file is not overdue, whatever date it was booked for
                      and str(fv_info.get("status") or "").lower() != "completed" and not fv_info.get("actual_date")
                      and app_status_str not in ("completed", "approved", "closed", "rejected")):
                    if is_tamil:
                        response_text = f"விண்ணப்பம் {app_no}-ன் கள ஆய்வு ({str(fv_date_str)[:10]}) {fv_days_overdue} நாட்கள் தாமதமாக (overdue) உள்ளது."
                    else:
                        response_text = f"Application {app_no}: The field visit (scheduled for {str(fv_date_str)[:10]}) is **{fv_days_overdue} days overdue** (as of today, {today.isoformat()})."
                    if str(sd.get('status','')).lower() in ('pending','in_progress','escalated'):
                        response_text = (_age_sla_answer(app_no, sd, today, is_tamil) + (
                            f" கள ஆய்வு {str(fv_date_str)[:10]} அன்று திட்டமிடப்பட்டது ({fv_days_overdue} நாட்கள் முன்பு); முடிந்ததாகப் பதிவில்லை." if is_tamil else
                            f" The visit was scheduled for {str(fv_date_str)[:10]} ({fv_days_overdue} days ago) and is not recorded as completed."))
                    logger.info(f"Responded with {fv_days_overdue} days overdue for {app_no}")
                elif sd.get("is_overdue") and app_sub_days is not None:
                    response_text = _age_sla_answer(app_no, sd, today, is_tamil)
                elif app_status_str == "rejected":
                    # A rejected file is closed, not SLA-tracked, and never "overdue" --
                    # that word describes an open file still waiting on a deadline. Without
                    # this branch a rejected app fell through to _age_sla_answer(), which
                    # computes elapsed-time-vs-SLA regardless of status and reads as an
                    # overdue warning for a file that was decided long ago.
                    if is_tamil:
                        response_text = f"'தாமதம்' (Overdue) {app_no}-க்குப் பொருந்தாது — இது நிராகரிக்கப்பட்டு முடிவடைந்துவிட்டது."
                    else:
                        response_text = f"'Overdue' does not apply to {app_no} -- it was rejected and is closed, not an open file being tracked against a deadline."
                    logger.info(f"Responded rejected/not applicable for {app_no}")
                elif app_status_str in ["completed", "approved", "closed"]:
                    if is_tamil:
                        response_text = f"விண்ணப்பம் {app_no} தாமதமாக இல்லை — இது ஏற்கனவே முடிவடைந்தது/அங்கீகரிக்கப்பட்டது."
                    else:
                        response_text = f"Application {app_no} is NOT overdue. It has been completed and approved."
                    logger.info(f"Responded completed/not overdue for {app_no}")
                elif fv_days_until is not None:
                    if fv_days_until == 0:
                        if is_tamil:
                            response_text = f"விண்ணப்பம் {app_no} தாமதமாக இல்லை. கள ஆய்வு இன்று ({str(fv_date_str)[:10]}) திட்டமிடப்பட்டுள்ளது."
                        else:
                            response_text = f"Application {app_no} is NOT overdue. The field visit is scheduled for TODAY ({str(fv_date_str)[:10]})."
                    else:
                        if is_tamil:
                            response_text = f"விண்ணப்பம் {app_no} தாமதமாக இல்லை. கள ஆய்வு {str(fv_date_str)[:10]} அன்று திட்டமிடப்பட்டுள்ளது ({fv_days_until} நாட்களில்)."
                        else:
                            response_text = f"Application {app_no} is NOT overdue. The field visit is scheduled for {str(fv_date_str)[:10]} (in {fv_days_until} days)."
                    logger.info(f"Responded upcoming field visit in {fv_days_until} days for {app_no}")
                elif app_sub_days is not None:
                    response_text = _age_sla_answer(app_no, sd, today, is_tamil)
                else:
                    if is_tamil:
                        response_text = f"விண்ணப்பம் {app_no} தாமதமாக இல்லை (காலக்கெடுவிற்குள் உள்ளது)."
                    else:
                        response_text = f"Application {app_no} is currently on schedule and not overdue."
                    logger.info(f"Responded not overdue for {app_no}")

            # Check for NISD/ISD type questions next
            elif ("nisd" in _msg_lower or "isd" in _msg_lower):
                app_type_value = sd.get("type", "N/A")
                if is_tamil:
                    response_text = f"விண்ணப்பம் {app_no} வகை: {app_type_value}"
                else:
                    response_text = f"Application {app_no} is of type: {app_type_value}"
                logger.info(f"Responded with application type '{app_type_value}' for {app_no}")

        # PARITY: process_chat_stream seeds _direct_answer_text from the fetch
        # phase (_prefetch_text) and writes it throughout the chain above, so a
        # completed direct answer suppresses the field-map lookup. process_chat
        # writes response_text instead, so gate on it too — otherwise the field
        # map overwrites the richer SLA/pending answer just computed.
        if not _direct_answer_text and not response_text and app_no:
            # Map question field keyword to structured_data key
            _field_map = {
                # PARITY with process_chat_stream's _field_map
                "virivu": ("applicant_address", "Address"),
                # Serial Number
                "serial number": ("serial_number", "Serial Number"),
                "serial_number": ("serial_number", "Serial Number"),
                "serial": ("serial_number", "Serial Number"),
                "வரிசை எண்": ("serial_number", "Serial Number"),
                "வரிசை": ("serial_number", "Serial Number"),
                # CAN Number
                "can number": ("can_number", "CAN Number"),
                "can_number": ("can_number", "CAN Number"),
                "can": ("can_number", "CAN Number"),
                "can எண்": ("can_number", "CAN Number"),
                # Patta Number
                "patta number": ("patta_number", "Patta Number"),
                "patta_number": ("patta_number", "Patta Number"),
                "patta": ("patta_number", "Patta Number"),
                "பட்டா எண்": ("patta_number", "Patta Number"),
                "பட்டா": ("patta_number", "Patta Number"),
                # Subdivision Number
                "subdivision number": ("subdivision_number", "Subdivision Number"),
                "subdivision_number": ("subdivision_number", "Subdivision Number"),
                "subdivision": ("subdivision_number", "Subdivision Number"),
                "current subdivision": ("current_subdivision_number", "Current Subdivision Number"),
                "current_subdivision_number": ("current_subdivision_number", "Current Subdivision Number"),
                # Temporary ("3/T1") vs final ("4") sub-division numbers --
                # get_application_detail returns both; without these keys
                # "temporary subdivision number" fuzzy-matched the generic
                # "subdivision number" and returned the FINAL numbers.
                "temporary subdivision number": ("temporary_subdivision_number", "Temporary Sub-Division Number"),
                "temporary sub-division number": ("temporary_subdivision_number", "Temporary Sub-Division Number"),
                "temporary subdivision": ("temporary_subdivision_number", "Temporary Sub-Division Number"),
                "temporary sub-division": ("temporary_subdivision_number", "Temporary Sub-Division Number"),
                "provisional subdivision": ("temporary_subdivision_number", "Temporary Sub-Division Number"),
                "temp subdivision": ("temporary_subdivision_number", "Temporary Sub-Division Number"),
                "தற்காலிக உட்பிரிவு": ("temporary_subdivision_number", "Temporary Sub-Division Number"),
                "final subdivision number": ("final_subdivision_number", "Final Sub-Division Number"),
                "final sub-division number": ("final_subdivision_number", "Final Sub-Division Number"),
                "final subdivision": ("final_subdivision_number", "Final Sub-Division Number"),
                "new subdivision number": ("final_subdivision_number", "Final Sub-Division Number"),
                "new sub-division number": ("final_subdivision_number", "Final Sub-Division Number"),
                "assigned subdivision number": ("final_subdivision_number", "Final Sub-Division Number"),
                "இறுதி உட்பிரிவு": ("final_subdivision_number", "Final Sub-Division Number"),
                "உட்பிரிவு எண்": ("subdivision_number", "Subdivision Number"),
                "உட்பிரிவு": ("subdivision_number", "Subdivision Number"),
                # Area / SQM
                "area sq": ("area_sqm", "Area (sq.m)"),
                "area sqm": ("area_sqm", "Area (sq.m)"),
                "area in sq": ("area_sqm", "Area (sq.m)"),
                "area in sq m": ("area_sqm", "Area (sq.m)"),
                "total area": ("area_sqm", "Area (sq.m)"),
                "total area sq": ("area_sqm", "Area (sq.m)"),
                "survey area": ("area_sqm", "Area (sq.m)"),
                "area": ("area_sqm", "Area (sq.m)"),
                "sqm": ("area_sqm", "Area (sq.m)"),
                "sq m": ("area_sqm", "Area (sq.m)"),
                "square meter": ("area_sqm", "Area (sq.m)"),
                "square meters": ("area_sqm", "Area (sq.m)"),
                "பரப்பளவு": ("area_sqm", "Area (sq.m)"),
                "சதுர மீட்டர்": ("area_sqm", "Area (sq.m)"),
                # Who is handling the file -- see `assigned_officer` in
                # postgres.get_application_detail.
                "who is handling": ("assigned_officer", "Handled by"),
                "who handles": ("assigned_officer", "Handled by"),
                "who is dealing": ("assigned_officer", "Handled by"),
                "handling it": ("assigned_officer", "Handled by"),
                "handled by": ("assigned_officer", "Handled by"),
                "assigned officer": ("assigned_officer", "Handled by"),
                "assigned to": ("assigned_officer", "Handled by"),
                "which officer": ("assigned_officer", "Handled by"),
                "responsible officer": ("assigned_officer", "Handled by"),
                "officer in charge": ("assigned_officer", "Handled by"),
                "in charge of": ("assigned_officer", "Handled by"),
                "யார் கையாள்": ("assigned_officer", "Handled by"),
                "எந்த அதிகாரி": ("assigned_officer", "Handled by"),
                "பொறுப்பு அதிகாரி": ("assigned_officer", "Handled by"),
                # User ID & Role ID
                "user id": ("user_id", "User ID"),
                "user_id": ("user_id", "User ID"),
                "user": ("user_id", "User ID"),
                "பயனர் ஐடி": ("user_id", "User ID"),
                "பயனர்": ("user_id", "User ID"),
                "role id": ("role_id", "Role ID"),
                "role_id": ("role_id", "Role ID"),
                "role": ("role_id", "Role ID"),
                "பங்கு ஐடி": ("role_id", "Role ID"),
                # Service & Department
                "service code": ("service_code", "Service Code"),
                "service_code": ("service_code", "Service Code"),
                "workflow state": ("workflow_state", "Workflow State"),
                "workflow_state": ("workflow_state", "Workflow State"),
                # The sale deed number is stored on the application and was
                # absent from this map, so asking for it fell through to the
                # generic "here are the details" dump instead of answering.
                "sale deed number": ("sale_deed_number", "Sale Deed Number"),
                "sale deed no": ("sale_deed_number", "Sale Deed Number"),
                "saledeed number": ("sale_deed_number", "Sale Deed Number"),
                "deed number": ("sale_deed_number", "Sale Deed Number"),
                "கிரய பத்திர எண்": ("sale_deed_number", "Sale Deed Number"),
                "sale deed registered": ("sale_deed_registered", "Sale Deed Registered"),
                "source code": ("source_code", "Source Code"),
                "source_code": ("source_code", "Source Code"),
                "source name": ("source_name", "Source Name"),
                "source_name": ("source_name", "Source Name"),
                "source": ("source_name", "Source Name"),
                "ஆதாரம்": ("source_name", "Source Name"),
                # Address
                "address": ("applicant_address", "Address"),
                "முகவரி": ("applicant_address", "Address"),
                "mugavari": ("applicant_address", "Address"),
                "permanent address": ("applicant_permanent_address", "Permanent Address"),
                "நிரந்தர முகவரி": ("applicant_permanent_address", "Permanent Address"),
                # Applicant demographics (NISD/ISD application-info extract)
                "father name": ("applicant_father_name", "Father's Name"),
                "father's name": ("applicant_father_name", "Father's Name"),
                "fathers name": ("applicant_father_name", "Father's Name"),
                # The natham-chitta owner row's relative is projected into
                # applicants.father_name too; "relative name" alone used to
                # fall through to the LLM or the owner-list dump.
                "relative name": ("applicant_father_name", "Relative's Name"),
                "relative's name": ("applicant_father_name", "Relative's Name"),
                "relatives name": ("applicant_father_name", "Relative's Name"),
                "name of the relative": ("applicant_father_name", "Relative's Name"),
                "உறவினர் பெயர்": ("applicant_father_name", "Relative's Name"),
                "தந்தை பெயர்": ("applicant_father_name", "Father's Name"),
                "அப்பா பெயர்": ("applicant_father_name", "Father's Name"),
                "mother name": ("applicant_mother_name", "Mother's Name"),
                "mother's name": ("applicant_mother_name", "Mother's Name"),
                "mothers name": ("applicant_mother_name", "Mother's Name"),
                "தாய் பெயர்": ("applicant_mother_name", "Mother's Name"),
                "date of birth": ("applicant_dob", "Date of Birth"),
                "dob": ("applicant_dob", "Date of Birth"),
                "birth date": ("applicant_dob", "Date of Birth"),
                "பிறந்த தேதி": ("applicant_dob", "Date of Birth"),
                "gender": ("applicant_gender", "Gender"),
                "sex": ("applicant_gender", "Gender"),
                "பாலினம்": ("applicant_gender", "Gender"),
                "occupation": ("applicant_occupation", "Occupation"),
                "profession": ("applicant_occupation", "Occupation"),
                "தொழில்": ("applicant_occupation", "Occupation"),
                # Fee / payment
                "fee": ("fee_amount", "Fee Amount"),
                "fee amount": ("fee_amount", "Fee Amount"),
                "payment amount": ("fee_amount", "Fee Amount"),
                "amount paid": ("fee_amount", "Fee Amount"),
                "கட்டணம்": ("fee_amount", "Fee Amount"),
                "challan": ("challan_number", "Challan Number"),
                "challan number": ("challan_number", "Challan Number"),
                "challan no": ("challan_number", "Challan Number"),
                "payment mode": ("payment_mode", "Payment Mode"),
                "mode of payment": ("payment_mode", "Payment Mode"),
                # IGRS Form 6
                "form 6 number": ("igrs_form6_number", "IGRS Form 6 Number"),
                "form6 number": ("igrs_form6_number", "IGRS Form 6 Number"),
                "form 6": ("igrs_form6_number", "IGRS Form 6 Number"),
                "igrs form 6": ("igrs_form6_number", "IGRS Form 6 Number"),
                "igrs form6": ("igrs_form6_number", "IGRS Form 6 Number"),
                "igrs number": ("igrs_form6_number", "IGRS Form 6 Number"),
                # A bare "igrs" is needed for the Tamil and Tanglish
                # shapes: "IGRS எண் என்ன?" puts the Tamil word for
                # "number" after IGRS, so the English phrase "igrs number"
                # never matched and the answer was "I could not find that
                # particular detail" for a file that carries one.
                "igrs": ("igrs_form6_number", "IGRS Form 6 Number"),
                "igrs எண்": ("igrs_form6_number", "IGRS Form 6 Number"),
                "படிவம் 6": ("igrs_form6_number", "IGRS Form 6 Number"),
                # DSC signature on the patta order
                "signed by": ("patta_signed_by", "Signed By"),
                "who signed": ("patta_signed_by", "Signed By"),
                "dsc signed by": ("patta_signed_by", "Signed By"),
                "signature date": ("patta_signature_date", "Signature Date"),
                "signed date": ("patta_signature_date", "Signature Date"),
                "date signed": ("patta_signature_date", "Signature Date"),
                "when signed": ("patta_signature_date", "Signature Date"),
                # "what date was the TAHSILDAR SIGNATURE on X" puts "date"
                # before the phrase, so none of the cues above (which all
                # expect "signature"/"signed" immediately before "date")
                # matched, and the message fell through to the bare "date"
                # keyword elsewhere in this map, which answers with
                # submission_date instead -- a different, wrong field.
                "tahsildar signature": ("patta_signature_date", "Signature Date"),
                "தாசில்தார் கையொப்பம்": ("patta_signature_date", "Signature Date"),
                "கையொப்பமிட்டது": ("patta_signed_by", "Signed By"),
                # New patta issued by the transfer
                "new patta number": ("new_patta_number", "New Patta Number"),
                "generated patta": ("new_patta_number", "New Patta Number"),
                "issued patta": ("new_patta_number", "New Patta Number"),
                # Officers ask this the other way round just as often --
                # "was a patta issued?", "patta issued or not", "has a patta
                # been issued" -- and a bare bare "patta" cue on its own
                # answered with the survey's PRE-EXISTING patta number,
                # which reads as confirming one was issued even on a
                # REJECTED application that never reached a transfer order.
                "patta issued": ("new_patta_number", "New Patta Number"),
                "patta been issued": ("new_patta_number", "New Patta Number"),
                "patta given": ("new_patta_number", "New Patta Number"),
                "patta generated": ("new_patta_number", "New Patta Number"),
                "பட்டா வழங்கப்பட்டதா": ("new_patta_number", "New Patta Number"),
                "new patta": ("new_patta_number", "New Patta Number"),
                "புதிய பட்டா எண்": ("new_patta_number", "New Patta Number"),
                # Registration / transfer-order detail (transfer_urban_detail).
                # The deed number itself is "sale deed number" above.
                "registration document number": ("sale_deed_number", "Registration Document Number"),
                "registration document no": ("sale_deed_number", "Registration Document Number"),
                "registration_document_number": ("sale_deed_number", "Registration Document Number"),
                "registered document number": ("sale_deed_number", "Registration Document Number"),
                "பதிவு ஆவண எண்": ("sale_deed_number", "Registration Document Number"),
                "transfer reason": ("transfer_reason", "Transfer Reason"),
                "transfer_reason": ("transfer_reason", "Transfer Reason"),
                "reason for transfer": ("transfer_reason", "Transfer Reason"),
                "reason for the transfer": ("transfer_reason", "Transfer Reason"),
                "mutation reason": ("transfer_reason", "Transfer Reason"),
                "why transferred": ("transfer_reason", "Transfer Reason"),
                "why was it transferred": ("transfer_reason", "Transfer Reason"),
                "why was this transferred": ("transfer_reason", "Transfer Reason"),
                "மாற்றத்திற்கான காரணம்": ("transfer_reason", "Transfer Reason"),
                "பரிமாற்ற காரணம்": ("transfer_reason", "Transfer Reason"),
                "transfer type": ("transfer_type", "Transfer Type"),
                "transfer_type": ("transfer_type", "Transfer Type"),
                "type of transfer": ("transfer_type", "Transfer Type"),
                "kind of transfer": ("transfer_type", "Transfer Type"),
                "registration place": ("registration_place", "Registration Place"),
                "registration_place": ("registration_place", "Registration Place"),
                "registration office": ("registration_place", "Registration Place"),
                "registered at": ("registration_place", "Registration Place"),
                "where registered": ("registration_place", "Registration Place"),
                "where was the deed registered": ("registration_place", "Registration Place"),
                "where was it registered": ("registration_place", "Registration Place"),
                "sub registrar office": ("registration_place", "Registration Place"),
                "sro office": ("registration_place", "Registration Place"),
                "பதிவு இடம்": ("registration_place", "Registration Place"),
                "பதிவு அலுவலகம்": ("registration_place", "Registration Place"),
                "registration date": ("registration_date", "Registration Date"),
                "registration_date": ("registration_date", "Registration Date"),
                "date of registration": ("registration_date", "Registration Date"),
                "registered on": ("registration_date", "Registration Date"),
                "when was the deed registered": ("registration_date", "Registration Date"),
                "when registered": ("registration_date", "Registration Date"),
                "deed date": ("registration_date", "Registration Date"),
                "deed registration date": ("registration_date", "Registration Date"),
                "பதிவு தேதி": ("registration_date", "Registration Date"),
                # Patta transfer's OWN date -- distinct from submission date
                # (filing), registration date (the deed) and order date (the
                # ZDT order). Missing entirely used to fall through to the
                # generic bare "date" cue below, which answers with the
                # submission date -- the exact "which date field" confusion
                # this register's several date columns must not produce.
                "transfer date": ("transfer_date", "Transfer Date"),
                "transfer_date": ("transfer_date", "Transfer Date"),
                "when was the patta transferred": ("transfer_date", "Transfer Date"),
                "when was it transferred": ("transfer_date", "Transfer Date"),
                "பட்டா மாற்றப்பட்ட தேதி": ("transfer_date", "Transfer Date"),
                "tahsildar signature date": ("patta_signature_date", "Tahsildar Signature Date"),
                "tahsildar sign date": ("patta_signature_date", "Tahsildar Signature Date"),
                "signature date": ("patta_signature_date", "Tahsildar Signature Date"),
                "when did the tahsildar sign": ("patta_signature_date", "Tahsildar Signature Date"),
                "தாசில்தார் கையொப்ப தேதி": ("patta_signature_date", "Tahsildar Signature Date"),
                "old patta number": ("old_patta_number", "Old Patta Number"),
                "old patta": ("old_patta_number", "Old Patta Number"),
                "old_patta_number": ("old_patta_number", "Old Patta Number"),
                "previous patta": ("old_patta_number", "Old Patta Number"),
                "superseded patta": ("old_patta_number", "Old Patta Number"),
                "பழைய பட்டா எண்": ("old_patta_number", "Old Patta Number"),
                "பழைய பட்டா": ("old_patta_number", "Old Patta Number"),
                "transfer order number": ("transfer_order_number", "Transfer Order Number"),
                "transfer_order_number": ("transfer_order_number", "Transfer Order Number"),
                "order number": ("transfer_order_number", "Transfer Order Number"),
                "order id": ("transfer_order_number", "Transfer Order Number"),
                "ஆணை எண்": ("transfer_order_number", "Transfer Order Number"),
                "order date": ("order_date", "Order Date"),
                "order_date": ("order_date", "Order Date"),
                "date of order": ("order_date", "Order Date"),
                "order generated on": ("order_date", "Order Date"),
                "ஆணை தேதி": ("order_date", "Order Date"),
                "order remarks": ("order_remarks", "Order Remarks"),
                "order remark": ("order_remarks", "Order Remarks"),
                "order_remarks": ("order_remarks", "Order Remarks"),
                "remarks on the order": ("order_remarks", "Order Remarks"),
                "ஆணை குறிப்பு": ("order_remarks", "Order Remarks"),
                "sis recommendation": ("sis_recommendation", "SIS Recommendation"),
                "sis_recommendation": ("sis_recommendation", "SIS Recommendation"),
                "surveyor recommendation": ("sis_recommendation", "SIS Recommendation"),
                "my recommendation": ("sis_recommendation", "SIS Recommendation"),
                "recommendation": ("sis_recommendation", "SIS Recommendation"),
                "recommended": ("sis_recommendation", "SIS Recommendation"),
                "பரிந்துரை": ("sis_recommendation", "SIS Recommendation"),
                "sis remarks": ("sis_remarks", "SIS Remarks"),
                "sis remark": ("sis_remarks", "SIS Remarks"),
                "sis_remarks": ("sis_remarks", "SIS Remarks"),
                "surveyor remarks": ("sis_remarks", "SIS Remarks"),
                "my remarks": ("sis_remarks", "SIS Remarks"),
                "sis notes": ("sis_remarks", "SIS Remarks"),
                "sis குறிப்பு": ("sis_remarks", "SIS Remarks"),
                "recommendation reason": ("sis_recommendation_reason", "SIS Recommendation Reason"),
                "sis recommendation reason": ("sis_recommendation_reason", "SIS Recommendation Reason"),
                "reason for recommendation": ("sis_recommendation_reason", "SIS Recommendation Reason"),
                # lead application of a group of ISD files filed together
                "merged application": ("merged_application_id", "Merged Application"),
                "merge parent": ("merged_application_id", "Merged Application"),
                "parent application": ("merged_application_id", "Merged Application"),
                "merged with": ("merged_application_id", "Merged Application"),
                "merge group": ("merged_application_id", "Merged Application"),
                # Mobile/Phone
                "mobile number": ("applicant_mobile", "Mobile"),
                "phone number": ("applicant_mobile", "Phone"),
                "mobile": ("applicant_mobile", "Mobile"),
                "phone": ("applicant_mobile", "Phone"),
                "தொலைபேசி எண்": ("applicant_mobile", "Mobile"),
                "தொலைபேசி": ("applicant_mobile", "Mobile"),
                "கைபேசி": ("applicant_mobile", "Mobile"),
                "கைபேசி எண்": ("applicant_mobile", "Mobile"),
                "tholaipaesi": ("applicant_mobile", "Mobile"),
                "contact": ("applicant_mobile", "Mobile"),
                # Name variations (extensive for best matching)
                "name": ("applicant_name", "Applicant Name"),
                "applicant": ("applicant_name", "Applicant Name"),
                "பெயர்": ("applicant_name", "Applicant Name"),
                "நாமாகும்": ("applicant_name", "Applicant Name"),
                "நாமம்": ("applicant_name", "Applicant Name"),
                "விண்ணப்பதாரர்": ("applicant_name", "Applicant Name"),
                "விண்ணப்பதாரர் பெயர்": ("applicant_name", "Applicant Name"),
                "விண்ணப்பதாரரின் பெயர்": ("applicant_name", "Applicant Name"),
                "விண்ணப்பதாரரின் நாமாகும் பெயர்": ("applicant_name", "Applicant Name"),
                "நாமாகும் பெயர்": ("applicant_name", "Applicant Name"),
                "peyar": ("applicant_name", "Applicant Name"),
                "peiyar": ("applicant_name", "Applicant Name"),
                "namaagum": ("applicant_name", "Applicant Name"),
                "namam": ("applicant_name", "Applicant Name"),
                "vinnappatharar": ("applicant_name", "Applicant Name"),
                "vinnappathaarar": ("applicant_name", "Applicant Name"),
                # Status
                "status": ("status", "Status"),
                "நிலை": ("status", "Status"),
                "nilai": ("status", "Status"),
                "state": ("status", "Status"),
                # How officers actually ask: "what is the position of X",
                # "when did we receive X", "which ward is X in".
                "position": ("status", "Status"),
                "standing": ("status", "Status"),
                "received": ("submission_date", "Submission Date"),
                "receive": ("submission_date", "Submission Date"),
                "receipt date": ("submission_date", "Submission Date"),
                # Bare "ward"/"block" show the NAME, same as bare "district"/
                # "taluk" below -- "ward number" and "ward code" (mapped
                # further down) still give the raw code, for whoever
                # actually wants that.
                "ward": ("ward_name", "Ward"),
                "ward number": ("ward_number", "Ward"),
                "வார்டு": ("ward_name", "Ward"),
                "block": ("block_name", "Block"),
                "block number": ("block_number", "Block"),
                "தொகுதி": ("block_name", "Block"),
                # District / Taluk / Town names -- the jurisdiction chain
                # for this file. The "district code" / "taluk code" phrases
                # above still win for the numeric codes; these bare tokens
                # answer "which district / taluk / town is X in", which the
                # FAQ promises and which ward/block already answered.
                "district": ("district_name", "District"),
                "மாவட்டம்": ("district_name", "District"),
                "mavattam": ("district_name", "District"),
                "taluk": ("taluk_name", "Taluk"),
                "taluka": ("taluk_name", "Taluk"),
                "தாலுகா": ("taluk_name", "Taluk"),
                "town": ("town_name", "Town"),
                "நகரம்": ("town_name", "Town"),
                "nagaram": ("town_name", "Town"),
                # Stage
                "stage": ("stage", "Current Stage"),
                "கட்டம்": ("stage", "Current Stage"),
                "kattam": ("stage", "Current Stage"),
                "level": ("stage", "Current Stage"),
                # Type
                "type": ("type", "Application Type"),
                "வகை": ("type", "Application Type"),
                "vagai": ("type", "Application Type"),
                "kind": ("type", "Application Type"),
                # Land classification of the parcel (from the survey number).
                "land type": ("land_type", "Land Type"),
                "land_type": ("land_type", "Land Type"),
                "land classification": ("land_type", "Land Type"),
                "type of land": ("land_type", "Land Type"),
                "நில வகை": ("land_type", "Land Type"),
                "nila vagai": ("land_type", "Land Type"),
                # Survey
                "survey": ("survey_no", "Survey Number"),
                "survey number": ("survey_no", "Survey Number"),
                "survey no": ("survey_no", "Survey Number"),
                "survey_no": ("survey_no", "Survey Number"),
                "survey_number": ("survey_no", "Survey Number"),
                "கணக்கெண்": ("survey_no", "Survey Number"),
                "சர்வே": ("survey_no", "Survey Number"),
                "சர்வே எண்": ("survey_no", "Survey Number"),
                "ganakken": ("survey_no", "Survey Number"),
                "kanakken": ("survey_no", "Survey Number"),
                # Date / Year
                "date": ("submission_date", "Submission Date"),
                "தேதி": ("submission_date", "Submission Date"),
                "thethi": ("submission_date", "Submission Date"),
                "thedhi": ("submission_date", "Submission Date"),
                "submitted": ("submission_date", "Submission Date"),
                "year": ("submission_date", "Submission Date"),
                "ஆண்டு": ("submission_date", "Submission Date"),
                "aandu": ("submission_date", "Submission Date"),
                "annu": ("submission_date", "Submission Date"),
                "when": ("submission_date", "Submission Date"),
                "எப்போது": ("submission_date", "Submission Date"),
                "eppodhu": ("submission_date", "Submission Date"),
                # Priority
                "priority": ("priority_flag", "Priority"),
                "high priority": ("priority_flag", "Priority"),
                "priority flag": ("priority_flag", "Priority"),
                "முன்னுரிமை": ("priority_flag", "Priority"),
                "munnurimai": ("priority_flag", "Priority"),
                "urgent": ("priority_flag", "Priority"),
                # Overdue
                "overdue": ("is_overdue", "Overdue"),
                "is overdue": ("is_overdue", "Overdue"),
                "is it overdue": ("is_overdue", "Overdue"),
                "காலதாமத": ("is_overdue", "Overdue"),
                "kaalathamadha": ("is_overdue", "Overdue"),
                "delayed": ("is_overdue", "Overdue"),
                # Field visit
                "field visit": ("field_visit_scheduled", "Field Visit Scheduled"),
                "field visit scheduled": ("field_visit_scheduled", "Field Visit Scheduled"),
                "field visit date": ("field_visit_date", "Field Visit Date"),
                "visit date": ("field_visit_date", "Field Visit Date"),
                "inspection date": ("field_visit_date", "Field Visit Date"),
                "கள ஆய்வு": ("field_visit_scheduled", "Field Visit Scheduled"),
                "கள ஆய்வு தேதி": ("field_visit_date", "Field Visit Date"),
                # Location Codes
                "district code": ("district_code", "District Code"),
                "taluk code": ("taluk_code", "Taluk Code"),
                "ward code": ("ward_code", "Ward Code"),
                "block code": ("block_code", "Block Code"),
                "village code": ("village_code", "Village Code"),
                "urban unit code": ("urban_unit_code", "Urban Unit Code"),
                "town code": ("urban_unit_code", "Urban Unit Code"),
                "town_code": ("urban_unit_code", "Urban Unit Code"),
                # Aadhaar / CAN mapping (schema uses CAN number as citizen identifier)
                "aadhaar": ("can_number", "CAN Number"),
                "aadhar": ("can_number", "CAN Number"),
                "adhaar": ("can_number", "CAN Number"),
                # Reason
                "reason": ("declared_reason", "Declared Reason"),
                "declared reason": ("declared_reason", "Declared Reason"),
                "purpose": ("declared_reason", "Declared Reason"),
                "காரணம்": ("declared_reason", "Declared Reason"),
                "kaaranam": ("declared_reason", "Declared Reason"),
                "karanum": ("declared_reason", "Declared Reason"),
                # Location / stage keywords
                "where": ("stage", "Current Stage"),
                "where is it": ("stage", "Current Stage"),
                "where is": ("stage", "Current Stage"),
                "where is it currently": ("stage", "Current Stage"),
                "எங்கே": ("stage", "Current Stage"),
                "engae": ("stage", "Current Stage"),
                "enge": ("stage", "Current Stage"),
                "right now": ("stage", "Current Stage"),
                "currently": ("stage", "Current Stage"),
                "இப்போது": ("stage", "Current Stage"),
                "ippodhu": ("stage", "Current Stage"),
                "ippoathu": ("stage", "Current Stage"),
                # "which department is it at" means the desk/stage; a bare
                # "department code" is the org code (always 01 = Revenue).
                "department": ("department_code", "Department Code"),
                "department code": ("department_code", "Department Code"),
                "department_code": ("department_code", "Department Code"),
                "துறை": ("department_code", "Department Code"),
                "துறை குறியீடு": ("department_code", "Department Code"),
                "which department": ("stage", "Current Stage"),
                "office": ("stage", "Current Stage"),
                # Last updated (most recent workflow hop)
                "last updated": ("last_updated_datetime", "Last Updated Datetime"),
                "last update": ("last_updated_datetime", "Last Updated Datetime"),
                "last modified": ("last_updated_datetime", "Last Updated Datetime"),
                "updated on": ("last_updated_datetime", "Last Updated Datetime"),
                "last updated on": ("last_updated_datetime", "Last Updated Datetime"),
                "கடைசியாக புதுப்பிக்கப்பட்ட": ("last_updated_datetime", "Last Updated Datetime"),
                "புதுப்பிக்கப்பட்ட": ("last_updated_datetime", "Last Updated Datetime"),
                "அலுவலகம்": ("stage", "Current Stage"),
                "aluvalagam": ("stage", "Current Stage"),
                "aluvalakam": ("stage", "Current Stage"),
                "current stage": ("stage", "Current Stage"),
            }
            # Stage code → human-readable label (English)
            _stage_labels = {
                "SIS": "Sub Inspector Surveyor (SIS) — currently under field verification",
                "SD": "Senior Draughtsman (SD) — forwarded for sketch/approval",
                "DIS": "Deputy Inspector Surveyor (DIS) — under DIS review",
                "TAHSILDAR": "Zonal Deputy Tahsildar (ZDT) — holds the DSC; patta order pending sign-off",
                "COMPLETED": "Completed — patta order issued",
                "REJECTED": "Rejected",
            }
            # Tamil stage labels
            _stage_labels_ta = {
                "SIS": "துணை ஆய்வாளர் (SIS) — தற்போது கள சரிபார்ப்பில் உள்ளது",
                "SD": "மூத்த வரைவாளர் (SD) — வரைபட அங்கீகாரத்திற்கு அனுப்பப்பட்டது",
                "DIS": "மாவட்ட ஆய்வாளர் (DIS) — DIS மதிப்பாய்வில் உள்ளது",
                "TAHSILDAR": "மண்டல துணை வட்டாட்சியர் (ZDT) — DSC கையொப்பம், பட்டா ஆணை நிலுவையில்",
                "COMPLETED": "முடிந்தது — பட்டா ஆணை வழங்கப்பட்டது",
                "REJECTED": "நிராகரிக்கப்பட்டது",
            }

            # "When was this approved / rejected / closed?" -- answered from
            # the workflow hop that closed the file. The field map sends
            # every "when" and every "date" to submission_date, so without
            # this gate the answer is the filing date, which is not vague
            # but wrong. Runs first, so the field map never sees it.
            # The resolved application number, stripped out so a cue that
            # brackets it ("was <app> returned") still matches.
            _msg_lower_nonum = re.sub(r"\s+", " ", _msg_lower.replace((app_no or "").lower(), " "))
            _sdq_text = await _subdivision_answer(
                db, officer, message, sd, app_no, language in ("ta", "tanglish"))
            if _sdq_text:
                response_text = _sdq_text
                logger.info(f"Responded with sub-division record answer for {app_no}")
                all_matches = []
            elif _asked_decision_date(_msg_lower):
                sd["_asked"] = _msg_lower
                response_text = _decision_date_answer(
                    sd, app_no, language in ("ta", "tanglish"))
                logger.info(f"Responded with decision date for {app_no}")
                all_matches = []
            elif _asked_street_name(_msg_lower):
                response_text = _street_name_answer(
                    sd, app_no, language in ("ta", "tanglish"))
                logger.info(f"Responded with street-name-not-tracked for {app_no}")
                all_matches = []
            elif _asked_name_part(_msg_lower):
                response_text = _name_part_answer(
                    sd, app_no, language in ("ta", "tanglish"))
                logger.info(f"Responded with name-part answer for {app_no}")
                all_matches = []
            elif _asked_day_of_week(_msg_lower):
                response_text = _day_of_week_answer(
                    sd, app_no, _msg_lower, language in ("ta", "tanglish"))
                logger.info(f"Responded with day-of-week answer for {app_no}")
                all_matches = []
            elif _asked_month_name(_msg_lower):
                response_text = _month_name_answer(
                    sd, app_no, _msg_lower, language in ("ta", "tanglish"))
                logger.info(f"Responded with month-name answer for {app_no}")
                all_matches = []
            elif _asked_area_in_unit(_msg_lower) and _area_unit_answer(
                    sd, app_no, _asked_area_in_unit(_msg_lower),
                    language in ("ta", "tanglish")):
                response_text = _area_unit_answer(
                    sd, app_no, _asked_area_in_unit(_msg_lower),
                    language in ("ta", "tanglish"))
                logger.info(f"Converted area to {_asked_area_in_unit(_msg_lower)} for {app_no}")
                all_matches = []
            elif _asked_untracked_wf_field(_msg_lower_nonum):
                _utf_key = _asked_untracked_wf_field(_msg_lower_nonum)
                response_text = (_ip_answer(sd, app_no, language in ("ta", "tanglish"))
                                 if _utf_key == "ip_address" else
                                 _untracked_wf_field_answer(
                                     app_no, _utf_key, language in ("ta", "tanglish")))
                logger.info(f"Responded 'not in register' for {_utf_key} on {app_no}")
                all_matches = []
            elif _asked_untracked_source_field(_msg_lower_nonum):
                _usf_key = _asked_untracked_source_field(_msg_lower_nonum)
                response_text = _untracked_source_field_answer(
                    app_no, _usf_key, language in ("ta", "tanglish"))
                logger.info(f"Responded 'not in register' for source field {_usf_key} on {app_no}")
                all_matches = []
            elif _asked_untracked_appinfo_field(_msg_lower_nonum):
                _uaf_key = _asked_untracked_appinfo_field(_msg_lower_nonum)
                response_text = _untracked_appinfo_field_answer(
                    app_no, _uaf_key, language in ("ta", "tanglish"))
                logger.info(f"Responded 'not in register' for appinfo field {_uaf_key} on {app_no}")
                all_matches = []
            elif _asked_submission_channel_field(_msg_lower_nonum):
                response_text = _render_submission_channel_answer(
                    sd, language, _msg_lower)
                logger.info(f"Responded submission-channel field for {app_no}")
                all_matches = []
            else:
                # Use fuzzy matching for spelling error tolerance
                all_matches = _fuzzy_match_all_fields(_msg_lower, _field_map, threshold=0.75)

            # A field-visit question is answered in words, not as a value
            # list: "Field Visit Scheduled: No" beside an unrelated
            # "Submission Date" row reads as a missing field rather than an
            # answer. Submission date rides along because "date" matches it.
            _FV_FIELD_KEYS = {"field_visit_scheduled", "field_visit_date"}
            _asked_field_visit = any(w in _msg_lower for w in (
                "field visit", "field-visit", "fieldvisit", "site visit",
                "inspection", "கள ஆய்வு"))
            if (all_matches and _asked_field_visit
                    and any(k in _FV_FIELD_KEYS for k, _l, _kw in all_matches)
                    and all(k in _FV_FIELD_KEYS or k == "submission_date"
                            for k, _l, _kw in all_matches)):
                response_text = _field_visit_answer(
                    sd, app_no, language in ("ta", "tanglish"))
                logger.info(f"Responded with field-visit answer for {app_no}")
                all_matches = []

            if len(all_matches) > 1:
                is_tamil = language in ("ta", "tanglish")
                labels_to_use = _stage_labels_ta if is_tamil else _stage_labels
                ta_labels = {
                    "Address": "முகவரி", "Mobile": "தொலைபேசி",
                    "Applicant Name": "விண்ணப்பதாரர் பெயர்", "Status": "நிலை",
                    "Application Type": "விண்ணப்ப வகை", "Survey Number": "கணக்கெண்",
                    "Submission Date": "சமர்ப்பித்த தேதி", "Priority": "முன்னுரிமை",
                    "Overdue": "காலதாமதம்", "Declared Reason": "அறிவிக்கப்பட்ட காரணம்",
                    "Fee Amount": "கட்டணத் தொகை", "Challan Number": "சலான் எண்",
                    "Payment Mode": "கட்டண முறை",
                    "Serial Number": "வரிசை எண்", "CAN Number": "CAN எண்",
                    "Patta Number": "பட்டா எண்", "Subdivision Number": "உட்பிரிவு எண்",
                    "Current Subdivision Number": "தற்போதைய உட்பிரிவு எண்",
                    "User ID": "பயனர் ஐடி", "Role ID": "பங்கு ஐடி",
                    "Renewal Number": "புதுப்பித்தல் எண்", "Parent Application ID": "தாய் விண்ணப்ப எண்",
                    "CSC Service Charge": "CSC சேவை கட்டணம்", "Government Service Charge": "அரசு சேவை கட்டணம்",
                    "IP Address": "IP முகவரி", "Camp Flag": "முகாம் குறியீடு", "Camp Code": "முகாம் எண்",
                    "IGRS Form 6 Number": "IGRS படிவம் 6 எண்", "Dispatch Date": "அனுப்பிய தேதி",
                    "Received Date": "பெறப்பட்ட தேதி", "Last Updated Datetime": "கடைசியாக புதுப்பிக்கப்பட்ட தேதி",
                    "Workflow State": "பணிப்பாய்வு நிலை", "Return Status": "திரும்பிய நிலை",
                    "Ward": "வார்டு", "Block": "தொகுதி",
                    "District": "மாவட்டம்", "Taluk": "தாலுகா", "Town": "நகரம்",
                    "Source Name": "ஆதாரம்", "Current Stage": "தற்போதைய கட்டம்"
                }
                items = []
                for field_key, field_label, _ in all_matches:
                    value = sd.get(field_key)
                    # Name fields: annotate with a transliteration when the
                    # officer is working in the other script (parity with the
                    # single-field branch). See backend/utils/translit.py.
                    if field_key in _cb()._NAME_FIELD_KEYS and isinstance(value, str) and value:
                        value = translit.annotate_name(
                            value, translit.wants_script(_msg_lower, language))
                    if field_key == "fee_amount" and value not in (None, ""):
                        value = _money(value)
                    if field_key == "sis_recommendation" and isinstance(value, str) and value:
                        value = _sis_recommendation_text(value, is_tamil)
                    if value is None or value == "":
                        value = "N/A"
                    elif isinstance(value, bool):
                        value = ("ஆம்" if value else "இல்லை") if is_tamil else ("Yes" if value else "No")
                    elif field_key == "stage" and isinstance(value, str):
                        value = labels_to_use.get(value.upper(), value)
                    display_label = ta_labels.get(field_label, field_label) if is_tamil else field_label
                    items.append(f"• **{display_label}**: {value}")

                header = f"விண்ணப்பம் {app_no} விவரங்கள்:" if is_tamil else f"Details for application {app_no}:"
                response_text = header + "\n" + "\n".join(items)
                logger.info(f"Responded with multiple fields ({len(all_matches)}) for {app_no}")
            elif len(all_matches) == 1:
                field_key, field_label, matched_kw = all_matches[0]
                value = sd.get(field_key)
                # A fee reads as money, not as a bare float: "600.0" -> "₹600.00".
                if field_key == "fee_amount" and value not in (None, ""):
                    value = _money(value)
                # "Y" / "N" reads as a recommendation, not a bare letter.
                if field_key == "sis_recommendation" and isinstance(value, str) and value:
                    value = _sis_recommendation_text(value, language in ("ta", "tanglish"))
                if field_key in ("field_visit_scheduled", "field_visit_date"):
                    response_text = _field_visit_answer(
                        sd, app_no, language in ("ta", "tanglish"))
                    logger.info(f"Responded with field-visit answer for {app_no}")
                elif value is not None and value != "":
                    if isinstance(value, bool):
                        value = "Yes" if value else "No"
                    # A name is stored in one script only. If the officer is
                    # working in the other, carry a labelled transliteration
                    # beside the name of record (never instead of it).
                    if field_key in _cb()._NAME_FIELD_KEYS and isinstance(value, str):
                        value = translit.annotate_name(
                            value, translit.wants_script(_msg_lower, language))
                    # Expand stage codes to human-readable labels
                    if field_key == "stage" and isinstance(value, str):
                        # Use Tamil labels if query was in Tamil or Tanglish
                        is_tamil = language in ("ta", "tanglish")
                        labels_to_use = _stage_labels_ta if is_tamil else _stage_labels
                        readable = labels_to_use.get(value.upper(), value)
                        response_text = (
                            f"Application {app_no} is currently at: {readable}." if not is_tamil
                            else f"விண்ணப்பம் {app_no} தற்போது: {readable}."
                        )
                    # Extract year from date if user specifically asked for year
                    elif field_key == "submission_date" and any(kw in _msg_lower for kw in ["year", "ஆண்டு", "aandu", "annu"]):
                        # User asked for year specifically - extract year from date
                        try:
                            if isinstance(value, str) and len(value) >= 4:
                                year = value[:4]  # Extract YYYY from YYYY-MM-DD format
                                is_tamil = language in ("ta", "tanglish")
                                if is_tamil:
                                    response_text = f"{app_no} சமர்ப்பிக்கப்பட்ட ஆண்டு: {year}"
                                else:
                                    response_text = f"Application {app_no} was submitted in the year: {year}"
                                logger.info(f"Extracted year {year} from submission_date for {app_no}")
                            else:
                                response_text = f"The {field_label} for {app_no} is: {value}"
                                logger.info(f"Could not extract year, value type: {type(value)}, value: {value}")
                        except Exception as year_ex:
                            logger.error(f"Error extracting year: {year_ex}", exc_info=True)
                            response_text = f"The {field_label} for {app_no} is: {value}"
                    else:
                        # Provide response in Tamil if query was in Tamil or Tanglish
                        is_tamil = language in ("ta", "tanglish")
                        if is_tamil:
                            # Tamil field label mapping
                            ta_labels = {
                                "Address": "முகவரி", "Mobile": "தொலைபேசி",
                                "Applicant Name": "விண்ணப்பதாரர் பெயர்", "Status": "நிலை",
                                "Application Type": "விண்ணப்ப வகை", "Survey Number": "கணக்கெண்",
                                "Submission Date": "சமர்ப்பித்த தேதி", "Priority": "முன்னுரிமை",
                                "Overdue": "காலதாமதம்", "Declared Reason": "அறிவிக்கப்பட்ட காரணம்",
                                "Fee Amount": "கட்டணத் தொகை", "Challan Number": "சலான் எண்",
                                "Payment Mode": "கட்டண முறை",
                                "Serial Number": "வரிசை எண்", "CAN Number": "CAN எண்",
                                "Patta Number": "பட்டா எண்", "Subdivision Number": "உட்பிரிவு எண்",
                                "Current Subdivision Number": "தற்போதைய உட்பிரிவு எண்",
                                "User ID": "பயனர் ஐடி", "Role ID": "பங்கு ஐடி",
                                "Renewal Number": "புதுப்பித்தல் எண்", "Parent Application ID": "தாய் விண்ணப்ப எண்",
                                "CSC Service Charge": "CSC சேவை கட்டணம்", "Government Service Charge": "அரசு சேவை கட்டணம்",
                                "IP Address": "IP முகவரி", "Camp Flag": "முகாம் குறியீடு", "Camp Code": "முகாம் எண்",
                                "IGRS Form 6 Number": "IGRS படிவம் 6 எண்", "Dispatch Date": "அனுப்பிய தேதி",
                                "Received Date": "பெறப்பட்ட தேதி", "Last Updated Datetime": "கடைசியாக புதுப்பிக்கப்பட்ட தேதி",
                                "Workflow State": "பணிப்பாய்வு நிலை", "Return Status": "திரும்பிய நிலை",
                                "Ward": "வார்டு", "Block": "தொகுதி",
                    "District": "மாவட்டம்", "Taluk": "தாலுகா", "Town": "நகரம்",
                    "Source Name": "ஆதாரம்", "Area (sq.m)": "பரப்பளவு (ச.மீ)"
                            }
                            ta_field_label = ta_labels.get(field_label, field_label)
                            # More natural Tamil phrasing based on field type
                            if field_key == "applicant_name":
                                response_text = f"{app_no} விண்ணப்பதாரரின் பெயர்: {value}"
                            elif field_key == "status":
                                # The DB value is the English enum
                                # ("pending"/"approved"/...) -- rendered
                                # verbatim into an otherwise-Tamil sentence
                                # ("2026/... நிலை: pending") until now.
                                _status_ta_val = {
                                    "pending": "நிலுவையில் உள்ளது",
                                    "approved": "அங்கீகரிக்கப்பட்டது",
                                    "rejected": "நிராகரிக்கப்பட்டது",
                                    "in_progress": "செயல்பாட்டில் உள்ளது",
                                    "escalated": "மேல்நிலைப்படுத்தப்பட்டது",
                                }.get(str(value).lower(), value)
                                response_text = f"{app_no} நிலை: {_status_ta_val}"
                            elif field_key == "serial_number":
                                response_text = f"விண்ணப்பம் {app_no}-ன் வரிசை எண்: {value}"
                            else:
                                response_text = f"{app_no} {ta_field_label}: {value}"
                        else:
                            if field_key == "serial_number":
                                response_text = f"The serial number for application {app_no} is: {value}"
                            else:
                                response_text = f"The {field_label} for {app_no} is: {value}"
                else:
                    response_text = _missing_field_answer(
                        sd, app_no, field_key, field_label,
                        language in ("ta", "tanglish"))
                # A bare code answers nothing on its own: the officer asked
                # what the file is, so the code is stated with its meaning.
                if field_key == "service_code" and response_text:
                    _svc_line = service_code_one_liner(
                        value, language in ("ta", "tanglish"))
                    if _svc_line:
                        response_text = f"{response_text}\n{_svc_line}"
                logger.info(f"Responded with specific field '{field_label}' for {app_no} (matched: '{matched_kw}')")


    if not response_text and not html_response:
        # Step 6: Fall back to LLM for general / RAG queries or hardcoded intents
        full_prompt = build_prompt(message, rag_context, structured_data, language, chat_history,
                                   direct_answer=_bypass_html)
    _bound_now = locals()
    return StageOutcome(bound={k: _bound_now[k] for k in ('_bypass_html', 'app_type', 'context_used', 'full_prompt', 'html_response', 'is_tamil', 'response_text', 'structured_data', 'survey_no') if k in _bound_now})
