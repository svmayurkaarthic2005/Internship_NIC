"""
Main chatbot service - RAG orchestration
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
# Same substring trap for the Tanglish cues beside it: "oda" is a plain
# substring of "today", so "what's today's district code" read as Tanglish
# and answered in Tamil script to an English question.
_DC_TANGLISH_RE = re.compile(r"\b(enapa|enna|oda|sollo|kudunga)\b", re.IGNORECASE)
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

# Field-map keys whose value is a person's name. These are stored in a single
# script (whatever the extract carried); when the officer is working in the
# other script the answer carries a labelled transliteration alongside the
# name of record. See backend/utils/translit.py.
_NAME_FIELD_KEYS = frozenset({
    "applicant_name", "applicant_father_name", "applicant_mother_name",
})
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

# ── Compatibility facade: re-exports from the extracted chat_*.py modules ──
# (SIS_Chatbot_Modularity_Refactor) -- every name below used to be defined
# directly in this file; it now lives in one of the chat_*.py modules and is
# re-exported here so this module's own remaining code (the two giant
# processing functions, and the handful of functions kept resident for
# monkeypatch-safety reasons) can keep calling it by its original bare name,
# and so `from backend.services.chatbot import X` keeps working for anything
# still imported that way elsewhere in the codebase.
from backend.services.chat_application_queries import (
    ASK_FOR_APP_NUMBER,
    _DURATION_RE,
    _FOLLOWUP_CONTEXT_WINDOW,
    _HOOK_APP,
    _RAW_MESSAGE,
    _RESTORE_FILTER_RE,
    _WF_CUE,
    _asked_day_of_week,
    _asked_decision_date,
    _asked_month_name,
    _asked_name_part,
    _asked_street_name,
    _asks_distinct_service_count,
    _build_comparison,
    _day_of_week_answer,
    _decision_date_answer,
    _early_subdivision_answer,
    _fetch_last_application,
    _field_visit_answer,
    _fuzzy_match_all_fields,
    _fv_detail_answer,
    _ip_comparison_answer,
    _month_name_answer,
    _name_part_answer,
    _not_found_or_register,
    _register_completion,
    _register_listing,
    _render_applications_by_block,
    _render_can_apply_answer,
    _render_last_application_answer,
    _render_visit_plan_answer,
    _render_ward_directory,
    _render_workload_answer,
    _restore_original_filter_answer,
    _single_app_workflow_answer,
    _street_name_answer,
    _subdivision_answer,
    _summarize_joint_owners,
    _visit_row_html,
    build_ambiguous_reference_message,
    build_app_confirm_message,
    build_app_forbidden_message,
    build_app_not_found_message,
    build_application_gate_message,
    build_invalid_app_number_message,
    build_isd_processing_answer,
    build_sale_deed_direct_answer,
    resolve_application_reference,
)
from backend.services.chat_corrections import (
    _FV_COLS,
    _FV_KEEP,
    _FV_PRIOR_SORT,
    _FV_SCOPE,
    _NEG_EXCLUDE,
    _PAYMENT_COMPOSITE_RE,
    _RAW_BY_FIXED,
    _VISIT_WORD_RE,
    _WORKLOAD_SHOW_RE,
    _add_as_along,
    _agent_chunks,
    _answer_big,
    _app_evidence_in_scope,
    _bare_app_status,
    _big_plan,
    _clarification_reply,
    _field_presence_answer,
    _fv_aggregate_answer,
    _fv_followup,
    _fv_include_exclude_answer,
    _fv_row_matches,
    _is_bare_column_field,
    _is_bare_fragment_without_context,
    _is_bare_language_instruction,
    _is_empty_sort_followup,
    _is_followup_op,
    _is_ordinal_visit_reference,
    _is_orphan_list_followup,
    _is_unresolved_survey_question,
    _jurisdiction_yn_answer,
    _language_preference_reply,
    _list_was_asked,
    _maybe_resolve_fresh_visit_ordinal,
    _no_evidence_for_llm,
    _orphan_followup_reply,
    _payment_composite_answer,
    _plan_requests,
    _refine_field_visits,
    _rewrite_negated_types,
    _single_period_range,
    _split_compound,
    _standalone_field_presence_answer,
    _status_yes_no,
    _workflow_remarks_answer,
    _workload_show_followup,
)
from backend.services.chat_followups import (
    _CURRENT_CLOCK_OVERRIDE,
    _all_word_to_english,
    _applicant_card_text,
    _asks_applicant_focus,
    _asks_for_specific_detail,
    _attachment_turn_is_not_about_a_file,
    _carried_details_message,
    _context_from_history,
    _conversation_recall_reply,
    _expand_listed_details,
    _extract_app_number_from_context,
    _followup_is_self_contained,
    _followup_out_context,
    _foreign_geo_denial,
    _fragment_typo_fix,
    _fullform_detail_reply,
    _fullform_detail_term,
    _fullform_followup,
    _geo_scope,
    _identity_facet,
    _is_conversation_recall,
    _is_officer_identity_question,
    _is_sensitive_query,
    _is_today_date_question,
    _mark_session_cleared,
    _officer_identity_reply,
    _only_type_after_empty_list,
    _recent_answers,
    _reconcile_count_claims,
    _rescope_list_followup,
    _scrub_unverified_amounts,
    _scrub_unverified_counts,
    _scrub_unverified_specifics,
    _survey_number_for_application,
    _survey_ref,
    _take_sort_clause,
    _today_date_answer,
    _type_swap_followup,
    _verify_answer_numbers,
    _wants_applicant_card,
    _wants_carried_list_details,
    _wants_workflow_history,
)
from backend.services.chat_response_formatting import (
    _SORT_RESET,
    _apply_followup_resolution,
    _apply_result_limit,
    _asked_completed_visits,
    _asked_open_visits,
    _asked_unscheduled,
    _asks_missing_can_number,
    _cannot_reformat_reply,
    _channel_breakdown_note,
    _channel_scope_label,
    _detect_reformat_request,
    _extract_app_types,
    _format_count_intro,
    _is_count_only_query,
    _is_stage_ever_question,
    _last_user_message,
    _missing_can_number_answer,
    _multi_details_message,
    _reformat_previous_answer,
    _render_stage_ever_answer,
    _render_stage_yes_no_answer,
    _render_status_role_answer,
    _render_status_yes_no_answer,
    _rescope_followup_message,
    _resolve_prev_intent,
    _scoped_list_answer,
    _stage_yes_no_question,
    _status_yes_no_question,
)
from backend.services.chat_routing import (
    _ALL_STATUSES_LIST,
    _SAVE_AS,
    _answer_multi,
    _answer_turn,
    _asked_ownership_field,
    _asked_record_timestamp,
    _calc_turn,
    _howto_turn,
    _is_incomplete_answer,
    _is_tamil_turn,
    _merge_turn,
    _old_codes_turn,
    _ownership_field_answer,
    _recheck_turn,
    _register_gap_turn,
    _stage_def_turn,
    _stats_turn,
    _timestamp_reply,
)
from backend.services.chat_service_codes import (
    _all_service_code_comparison_answer,
    _build_general_negative_correction_response,
    _build_service_code_comparison_response,
    _comparison_answer,
    _is_detail_request,
    _is_negative_sentence_format_request,
    _named_unsupported_service_code,
    _negative_sentence_format_context_code,
    _recent_nonattachment_claim,
    _route_negative_correction,
    _service_code_lookup_answer,
    _service_code_lookup_answer_natural,
    _unsupported_service_code_answer,
)
from backend.services.chat_shared import (
    _ADD_COLUMNS_RE,
    _ANY_APP_NUMBER_RE,
    _DISTRICT_NAME_RE,
    _DISTRICT_NAME_TA_RE,
    _DOMAIN_TERMS,
    _MONTH_NAMES,
    _SORT_OVERRIDE,
    _STATUS_LABELS,
    _asked_registration_detail,
    _asks_transfer_party,
    _build_jurisdiction_summary,
    _cm_strong,
    _contentless_message,
    _domain_evidence_tier,
    _excluded_service_code_filter,
    _explicit_status_request,
    _fetch_visit_plan,
    _fullform_term,
    _is_bare_concept_definition,
    _is_explain_request,
    _parse_negation,
    _period_from_message,
    _render_all_service_code_answer,
    _requested_service_code_filter,
    _resolve_month_year,
    _sd_parse,
    _um_parse,
    _whole_month_label,
    _year_match,
    extract_month_from_query,
)
from backend.services.chat_smalltalk import (
    _CHANNEL_BASIS_RE,
    _CHANNEL_NAMED_RE,
    _GLOSSARY,
    _GLOSSARY_ALIAS,
    _GLOSSARY_RE,
    _age_sla_answer,
    _channel_clarify_reply,
    _contentless_reply,
    _dc_special,
    _fee_lookup_data,
    _fee_lookup_text,
    _field_visit_rule_topic,
    _greeting_is_farewell,
    _greeting_is_thanks,
    _greeting_kind,
    _igrs_can_rule_answer,
    _igrs_can_rule_topic,
    _igrs_over_list_note,
    _is_capability_question,
    _is_merge_question,
    _is_mutation_request,
    _is_out_of_scope,
    _last_assistant_message,
    _llm_greeting,
    _mutation_refusal,
    _no_merge_type_answer,
    _recent_app_number_in_history,
    _resolve_rejected_widen,
    _resolve_unknown_from_confirmation,
    _scope_reply,
    _special_scope_kind,
    _special_scope_reply,
    _unidentified_number_answer,
    _unknown_message_kind,
    _unknown_message_reply,
    _workflow_history_answer,
    _workflow_steps_answer,
    _workflow_steps_answer_natural,
    _workflow_steps_topic,
)
from backend.services.chat_untracked_fields import (
    _absent_parcel_field_answer,
    _area_unit_answer,
    _asked_absent_parcel_field,
    _asked_area_in_unit,
    _asked_ip_compare,
    _asked_submission_channel_field,
    _asked_unknown_owner_attr,
    _asked_untracked_appinfo_field,
    _asked_untracked_geo_master_field,
    _asked_untracked_owner_field,
    _asked_untracked_parcel_field,
    _asked_untracked_signature_field,
    _asked_untracked_source_field,
    _asked_untracked_transfer_field,
    _asked_untracked_wf_field,
    _ip_answer,
    _ip_cross_comparison_answer,
    _is_owner_photo_request,
    _missing_field_answer,
    _owner_detail_line,
    _owner_photo_reply,
    _registration_detail_answer,
    _render_joint_owner_answer,
    _render_submission_channel_answer,
    _render_workflow_history,
    _sis_recommendation_text,
    _sub_division_numbers,
    _survey_attribute_answer,
    _transfer_party_answer,
    _unknown_owner_attr_answer,
    _untracked_appinfo_field_answer,
    _untracked_geo_master_field_answer,
    _untracked_owner_field_answer,
    _untracked_parcel_field_answer,
    _untracked_signature_field_answer,
    _untracked_source_field_answer,
    _untracked_transfer_field_answer,
    _untracked_wf_field_answer,
)
from backend.services.chat_pipeline import early_guard_reply
from backend.services.chat_answer import build_answer
from backend.services.chat_dispatch import dispatch_intent
from backend.services.chat_dispatch_stream import dispatch_intent_stream
from backend.services.chat_pipeline import _bound

# This module's own logger (pipeline log records are named backend.services.chatbot).
logger = get_logger(__name__)



# The six document types application_documents actually carries (verified
# against the seeded DB). check_documents named these in English regardless
# of language -- "காணாத ஆவணங்கள்: Sale Deed, Encumbrance Certificate" read as
# two languages in one sentence.
_DOCUMENT_TYPE_TA = {
    "Photographs": "புகைப்படங்கள்",
    "Photo ID": "புகைப்பட அடையாள அட்டை",
    "Patta Copy": "பட்டா நகல்",
    "Survey Sketch": "சர்வே வரைபடம்",
    "Sale Deed": "விற்பனை பத்திரம்",
    "Encumbrance Certificate": "பொறுப்புத் தடையில்லா சான்றிதழ்",
}



async def _channel_basis_evidence(db, officer, session_id, chat_history, message, language) -> str:
    """"How do you say THESE are from SRO?" -- after the rule, what the register says about the
    applications actually on screen (their recorded channels and IGRS Form 6 numbers)."""
    try:
        ctx = await _load_followup_context(db, session_id, chat_history, message,
                                           officer_id=officer.officer_id if officer else None)
        if not ctx or not ctx.application_numbers:
            return ""
        sd = await get_applications_by_numbers(db, officer, ctx.application_numbers, include_rejected=True)
        rows = sd.get("applications") or []
        if not rows:
            return ""
        by = {}
        for r in rows:
            by[r.get("submission_channel")] = by.get(r.get("submission_channel"), 0) + 1
        igrs = sum(1 for r in rows if r.get("igrs_form6_number"))
        n = len(rows)
        is_ta = language in ("ta", "tanglish")
        label = {"sub_registrar": ("Sub-Registrar", "சார்-பதிவாளர்"), "CSC": ("CSC", "CSC"),
                 "citizen": ("citizen", "குடிமகன்")}
        lab = lambda k: label.get(k, (str(k), str(k)))[1 if is_ta else 0]
        if len(by) == 1:
            k = next(iter(by))
            chan_en = f"all {n} are recorded as {lab(k)} files"
            chan_ta = f"{n} விண்ணப்பங்களும் {lab(k)} கோப்புகளாகப் பதிவாகியுள்ளன"
        else:
            parts = ", ".join(f"{c} {lab(k)}" for k, c in sorted(by.items(), key=lambda x: -x[1]))
            chan_en, chan_ta = f"they are recorded as {parts}", f"அவை பதிவில்: {parts}"
        ref_en = ("all of them carry" if igrs == n else "none of them carry" if igrs == 0 else f"{igrs} of them carry") + " a registration reference number (IGRS Form 6)"
        ref_ta = ("அனைத்திலும்" if igrs == n else f"{igrs} விண்ணப்பங்களில்") + (" பதிவு குறிப்பு எண் (IGRS படிவம் 6) இல்லை" if igrs == 0 else " பதிவு குறிப்பு எண் (IGRS படிவம் 6) உள்ளது")
        if is_ta:
            return f"\n\nநீங்கள் பார்க்கும் {n} விண்ணப்பங்களைப் பற்றி பதிவேடு: {chan_ta}; {ref_ta}."
        return f"\n\nAbout the {n} application(s) on your screen: {chan_en}, and {ref_en}."
    except Exception:  # evidence is a bonus, never a reason to fail the rule answer
        return ""


# Asked when an ownership/survey question arrives with no survey number and no
# confirmed application to take one from.
ASK_FOR_SURVEY_NUMBER = {
    "en": "Which survey number or application should I look up the ownership for?",
    "ta": "எந்த கணக்கெண் அல்லது விண்ணப்பத்திற்கான உரிமை விவரங்கள் வேண்டும்?",
    "tanglish": "எந்த கணக்கெண் அல்லது விண்ணப்பத்திற்கான உரிமை விவரங்கள் வேண்டும்?",
}

# "continue from where we left off" / "pick up where we left off" name no
# subject noun at all -- there is no "message" or "conversation" in the
# sentence, just an idiom for "resume the earlier session" -- so the
# qualifier+subject pair above never fires for it. Left unhandled, this fell
# to the LLM fallback with an EMPTY chat_history and it fabricated a prior
# conversation wholesale ("we were discussing survey numbers ... 1234, 5678,
# 9012, 3456") instead of saying it had nothing to resume. These phrases are
# self-sufficient recall signals and skip the qualifier/subject check, but
# still go through the same _RECALL_DATA_WORDS guard below.
# Continuation idioms ("where we left off", "let's continue", "resume our conversation")
# are handled by the contextual continuation dispatcher, NOT conversation recall.
_CONTINUATION_IDIOM_PHRASES = [
    "where we left off", "left off", "pick up where", "picking up where",
    "carry on from", "resume our conversation", "resume from before",
    "resume where", "continue our conversation", "continue from before",
    "continue where we", "let\'s continue", "lets continue",
    "தொடர்ந்து பேசுவோம்", "நிறுத்திய இடத்திலிருந்து",
]

_TANGLISH_WEEKDAYS = {
    0: "Thingatkilamai", 1: "Sevvaikkilamai", 2: "Budhankilamai", 3: "Vyazhakkilamai",
    4: "Vellikkilamai", 5: "Sanikkilamai", 6: "Gnayitrukkilamai",
}
# "list" and "points" also start real queries ("list pending applications"),
# so with no previous answer to reshape they must fall through to the normal
# pipeline instead of answering "nothing to reformat".
_AMBIGUOUS_REFORMATS = {"bullets", "short"}
# "where are they from" / "where they r from" / "where did they come from" --
# asks the CHANNEL of the carried list. Blindly concatenating this onto the
# previous question ("show my pending applications where they r from") read
# "r" as an unfiltered word nothing recognises and refused the whole turn
# ("I don't know how to filter applications by 'r'") instead of answering.
# Rewritten to the shape `_number_qa.answer()` already renders a channel
# breakdown for, ahead of the generic concatenation below.
_WHERE_FROM_RE = re.compile(
    r"\bwhere\b.{0,15}\b(?:they|them|those|these)\b.{0,15}\bfrom\b|எங்கிருந்து", re.IGNORECASE)


_STAGE_LABELS = {
    "SIS": "Sub Inspector Surveyor (SIS)", "SD": "Senior Draughtsman (SD)",
    "DIS": "Deputy Inspector Surveyor (DIS)", "TAHSILDAR": "the Zonal Level Tahsildar",
    "COMPLETED": "completed", "REJECTED": "rejected",
}


# "has this case been referred/forwarded/sent to the Tahsildar?" asks about
# the file's HISTORY -- did it ever reach that desk -- not where it sits right
# now. Answered with `_render_stage_yes_no_answer` (current `stage` only),
# a file the Tahsildar already decided (current_stage REJECTED/COMPLETED)
# got a confident "No", because "referred to" was read the same as "is it
# currently with". Officers ask it this way often: CLAUDE.md's own workflow
# chain is built from exactly these verbs ("Forward To ZDT", "Send to SIS").
_STAGE_EVER_PHRASE_RE = re.compile(
    r"\b(?:referred|forwarded|sent|escalated)\b[^.?!]{0,20}\bto\b|"
    r"\breached\b|\bpassed\s+through\b|\bmoved\s+to\b", re.IGNORECASE)


_PLURAL_REF_RE = re.compile(r"\b(?:they|their|them|those|these|all|each|every|ellam|ellaam|anaithu|avarrin|avatrin)\b"
                            r"|அவற்றின்|இவற்றின்|அனைத்து|எல்லா", re.IGNORECASE)


async def _load_followup_context(db, session_id, chat_history=None, message=None, officer_id=None):
    """The reference context left by the previous deterministic answer.

    Read from `chat_messages.structured_data` for this session -- the officer's
    own session, so nothing crosses between officers. Returns None when there
    is no usable record, which sends the turn down the ordinary path.

    Normally the most recent parseable record wins. The one exception is a
    message that picks a row by position ("what about the 2nd one?"): after
    "show details for <A>" the newest record is that single file, but "2nd one"
    means the second row of the list the officer saw before it, so the newest
    non-empty list context in the window is preferred for those.
    """
    if db is None or not session_id:
        return None
    try:
        if officer_id is not None:
            # A session id is a lookup key, never a grant: a context left by another
            # officer's session is not this officer's to continue.
            owner_row = (await db.execute(
                select(ChatSession.officer_id, ChatSession.started_at).where(ChatSession.id == session_id))).first()
            owner = owner_row[0] if owner_row else None
            if owner is not None and str(owner) != str(officer_id):
                return None
            _started = owner_row[1] if owner_row else None
        else:
            _started = None
        _cutoff = datetime.now(timezone.utc) - timedelta(minutes=settings.FOLLOWUP_CONTEXT_TTL_MINUTES)
        if _started is not None and _started > _cutoff:
            _cutoff = _started
        rows = (await db.execute(
            select(ChatMessage.structured_data)
            .where(and_(ChatMessage.session_id == session_id,
                        ChatMessage.role == "assistant",
                        ChatMessage.created_at >= _cutoff,
                        messages_since_session_start(session_id)))
            .order_by(ChatMessage.created_at.desc())
            .limit(_FOLLOWUP_CONTEXT_WINDOW)
        )).scalars().all()
    except Exception:  # pragma: no cover - context is a convenience, never fatal
        logger.warning("could not read the stored follow-up context")
        return None
    # The newest turn was about an uploaded file, not an application -- reaching
    # past it to an older application context would resolve "it" to a file the
    # officer has moved on from. Mirrors the rule `unheld_qa.context_app()`
    # already applies for a listing on screen: the newest answer decides, and a
    # kind this layer does not track is a wall, not something to see through.
    if rows and isinstance(rows[0], dict) and rows[0].get("entity") == "attachment":
        return None
    parsed = [c for c in (fctx.FollowupContext.from_json(raw) for raw in rows) if c]
    if not parsed:
        return None
    _nav = bool(message and fctx.nav_direction(message))
    if message and (_nav or fctx.mentions_ordinal(message) or fctx.mentions_slice(message)
                    # a field cue reaches back to the older list only when it is plural
                    # ("their wards"); "when was it submitted" after a single application
                    # is about THAT application
                    or (fctx.field_projection(message)
                        and (parsed[0].entity != fctx.ENTITY_APPLICATION
                             or _PLURAL_REF_RE.search(message.lower())))
                    or re.search(r"\b(?:last|first)\s+one\b", message.lower())):
        head = parsed[0]
        if head.entity == fctx.ENTITY_APPLICATION and (head.filters or {}).get("list_numbers"):
            # the row last picked carries the list it came from
            return fctx.FollowupContext(
                entity=fctx.ENTITY_APPLICATION_LIST,
                application_numbers=list(head.filters["list_numbers"]),
                filters={"cursor": (head.application_numbers or [None])[0]},
                query_type=head.query_type, intent=head.intent)
        # "the second OVERDUE VISIT" / "the first awaiting visit" names a
        # position in the field-visit QUEUE, a different list from whatever
        # application listing happens to sit in the lookback window --
        # unlike a bare "the 2nd one", this ordinal phrase carries its own
        # domain (`_VISIT_WORD_RE`) and must only match a context that is
        # ITSELF about visits (`about_visit` in its filters). Without this,
        # "show the survey and subdivision numbers of the second overdue
        # visit" right after an unrelated register listing picked the 2nd
        # row of THAT listing instead -- a file that is not even ISD, so it
        # can never have had a field visit at all -- rather than falling
        # through to `_maybe_resolve_fresh_visit_ordinal`, which queries the
        # actual visit plan and resolves the ordinal against it correctly.
        _wants_visit_ordinal = bool(_VISIT_WORD_RE.search(message))
        for ctx in parsed:
            if ctx.entity == fctx.ENTITY_APPLICATION_LIST and not ctx.application_numbers:
                # an EMPTY listing is what the officer was last shown: a position in it
                # is not a position in the older, non-empty list behind it
                if _wants_visit_ordinal and not (ctx.filters or {}).get("about_visit"):
                    continue
                return ctx
            if ctx.entity == fctx.ENTITY_APPLICATION_LIST and ctx.application_numbers:
                if _wants_visit_ordinal and not (ctx.filters or {}).get("about_visit"):
                    continue
                if _nav and head.entity == fctx.ENTITY_APPLICATION and head.application_numbers:
                    ctx = fctx.FollowupContext(
                        entity=ctx.entity, application_numbers=list(ctx.application_numbers),
                        filters={**(ctx.filters or {}), "cursor": head.application_numbers[0]},
                        query_type=ctx.query_type, intent=ctx.intent)
                return ctx
        if _wants_visit_ordinal:
            # Every list in the window was a non-visit application listing --
            # none of them is what "the second overdue visit" means, so stand
            # aside (as if no usable context existed) rather than falling
            # back to `parsed[0]` below, which could still be one of those
            # same unrelated lists.
            return None
    return parsed[0]


# "oldest one out of these" / "which is newest" -- a follow-up that PICKS one
# row out of the carried list. `_scoped_list_answer` below already names that
# one application in the text; re-attaching a table of every carried row
# underneath contradicts the sentence just given ("the oldest is X" next to a
# table still showing X and Y both) and reads as the assistant not standing
# behind its own answer. Shared with the two `_scoped_list_answer` callers so
# they can suppress the table the same way they already do for a per-row
# field projection ("show their IGRS number").
_SINGLE_PICK_FOLLOWUP_RE = re.compile(
    r"\boldest\b|\bearliest\b|\bolder\b|\bpazhusu\b|\bpazha(?:i)?y(?:a|adhu|athu)\b|பழைய"
    r"|\bnewest\b|\blatest\b|\bnewer\b|\bmore\s+recent\b|\bmost\s+recent\b"
    r"|\bputhusu\b|\bpudhusu\b|\bpu(?:th|dh)iy(?:a|adhu|athu)\b|சமீபத்திய|புதிய")


async def _single_turn(message, session_id, officer, db, chat_history):
    """One question through the whole pipeline (the same order `process_chat` uses), without re-splitting it."""
    for fn in (_merge_turn, _old_codes_turn, _record_field_turn, _recheck_turn, _compound_followup, _register_gap_turn,
               _howto_turn, _stage_def_turn, _calc_turn, _stats_turn, _number_turn):
        rc = await fn(message, session_id, officer, db, chat_history)
        if rc is not None:
            return rc
    return await _process_chat_impl(message, session_id, officer, db, chat_history)
_LIST_INTENTS = {"followup_list", "field_visits", "pending_applications", "all_service_code_applications", "fv_between_dates"}


async def _negation_as_exclusion(db, officer, session_id, chat_history, clause):
    """"not ISD" over the list on screen, as an operation: `exclude <A> <B> ...` (the applications that
    match). None when nothing matches; the clause unchanged when it is not a status/type/channel negation."""
    ex = {k: v for k, v in _neg_scope.parse(clause)[0].items() if k in ("status", "type", "channel", "ward")}
    if not ex:
        return clause
    ctx = await _load_followup_context(db, session_id, chat_history, clause,
                                       officer_id=officer.officer_id if officer else None)
    if not ctx or not ctx.application_numbers or (ctx.filters or {}).get("about_visit"):
        return clause
    from backend.services.postgres import get_applications_by_numbers
    rows = (await get_applications_by_numbers(db, officer, ctx.application_numbers, include_rejected=True)).get("applications") or []
    gone = [r["application_number"] for r in rows if _neg_scope._row_excluded(r, ex)]
    return ("exclude " + " ".join(gone)) if gone else None


async def _drop_rows_direct(db, officer, session_id, chat_history, exclude_message, message, language):
    """"exclude <many application numbers>" over the list on screen. The follow-up layer reads a message
    of that length as a request for the details of every number in it, so the rows are dropped here."""
    drop = {n.upper() for n in re.findall(r"\d{4}/\d{3,4}/\d{1,3}/\d+", exclude_message)}
    ctx = await _load_followup_context(db, session_id, chat_history, message,
                                       officer_id=officer.officer_id if officer else None)
    keep = [n for n in (ctx.application_numbers if ctx else []) if n.upper() not in drop]
    from backend.services.postgres import get_applications_by_numbers
    rows = ((await get_applications_by_numbers(db, officer, keep, include_rejected=True)).get("applications") or []) if keep else []
    ta = language in ("ta", "tanglish")
    sd = {"applications": rows, "count": len(rows), "jurisdiction_type": "ward", "query_type": "Applications",
          "empty_note": None, "sort_by": None, "sort_dir": None}
    body = build_html_response(sd, language, query=message) if rows else ""
    head = (f"{len(drop)} நீக்கப்பட்டது, {len(rows)} மீதம்:" if ta else f"Removed {len(drop)}, {len(rows)} left:")
    if not rows:
        head = "Nothing is left in the list." if not ta else "பட்டியலில் எதுவும் மீதமில்லை."
    text = f"<div class='table-intro'>{head}</div>{body}"
    out_ctx = fctx.build_context("followup_list", sd)
    await save_chat_messages(db=db, session_id=session_id, user_message=message, assistant_message=text, language=language,
                             response_time_ms=0, officer_id=officer.officer_id if officer else None,
                             structured_context=out_ctx.to_json() if out_ctx else None)
    return {"response": text, "language": language, "intent": "followup_list", "sources": [],
            "timestamp": datetime.now(timezone.utc).isoformat(), "context_used": True, "response_time_ms": 0,
            "table_data": None}

# "the first OVERDUE visit" / "the second overdue visit" names which ROW the
# question is about, resolved into an explicit application number before the
# message ever reaches the field-extraction block below (see
# `_maybe_resolve_fresh_visit_ordinal` / `_apply_followup_resolution`'s
# `about_visit` rewrite). That "overdue" is not a second question about
# overdue STATUS -- but the pending/overdue field-extraction gate matched it
# as a bare substring regardless, so "What is the survey number of the first
# overdue visit?" (now carrying an explicit application number) answered the
# SLA/overdue narrative instead of the survey number asked for. Stripped out
# before that gate is tested; a genuine "is 2026/.../015465 overdue?" with no
# row-ordinal still carries "overdue" on its own and is unaffected.
_ORDINAL_VISIT_PHRASE_RE = re.compile(
    r"\b(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|"
    r"last|next|\d+(?:st|nd|rd|th))\s+(?:overdue|awaiting|scheduled)\s+visits?\b",
    re.IGNORECASE)


async def _compound_followup(message, session_id, officer, db, chat_history):
    """Apply several follow-up operations said in one message, in order. None when the message is not one."""
    text = (message or "").strip()
    if _cm_strong(text, chat_history):
        return None
    if not (4 <= len(text.split()) <= 40) or re.search(r"\d{4}/\d{3,4}/\d{1,3}/\d+.*\band\b.*\d{4}/\d{3,4}/", text):
        return None
    clauses = _split_compound(_fragment_typo_fix(text) or text)
    _neg_ex, _neg_rest = _neg_scope.parse(_fragment_typo_fix(text) or text)
    if len(clauses) == 1 and sum(len(v) for v in _neg_ex.values()) >= 2 and not re.search(
            r"[A-Za-z]{3,}", re.sub(r"\b(?:and|or|nor|not|no|the|ones?|applications?|from|via|show|list|me|please|pls|only)\b", " ",
                                    _neg_rest, flags=re.IGNORECASE)):
        clauses = [text]                  # "not ISD and not from csc" over the rows on screen
        _neg_only = True
    else:
        _neg_only = False
    if not _neg_only and not (2 <= len(clauses) <= 4):
        return None
    if not await _load_followup_context(db, session_id, chat_history, message,
                                        officer_id=officer.officer_id if officer else None):
        return None                       # nothing on screen: a fresh multi-request, planned elsewhere
    if not all(_is_followup_op(c) for c in clauses):
        return None
    # Column operations describe how the FINAL table looks, so they go last (a sort answered after
    # "along with district" would otherwise come back in the default columns).
    def _is_column_op(c):
        low = (_fragment_typo_fix(c) or c).strip().lower().rstrip("?.! ")
        return bool(_ADD_COLUMNS_RE.fullmatch(low) or fctx.field_projections(c) is not None
                    or re.match(r"(?:no|not|without|hide|exclude|remove|drop|skip)\s+(?:the\s+)?(?:along\s+)?"
                                r"(?:ward|block|district|taluk|town|status|stage|type|survey|fee|name|applicant|mobile|"
                                r"date|channel|can|patta|sub[\s-]?divisions?)s?\b", low))
    clauses = [c for c in clauses if not _is_column_op(c)] + [c for c in clauses if _is_column_op(c)]
    results = []
    for c in clauses:
        # "not ISD" said on its own is a count; as one step of several it must NARROW the list the next
        # step works on, so it becomes "exclude <those applications>"
        c_run = await _negation_as_exclusion(db, officer, session_id, chat_history, c)
        if c_run is None:
            results.append((c, {"response": "<div class='table-intro'>Nothing to remove -- none of them match.</div>",
                                "intent": "compound_noop", "table_data": None}))
            continue
        try:
            if c_run.startswith("exclude "):
                r = await _drop_rows_direct(db, officer, session_id, chat_history, c_run, c,
                                            detect_language(_fragment_typo_fix(c) or c))
            else:
                r = await _number_turn(c_run, session_id, officer, db, chat_history)
                if r is None:
                    r = await _process_chat_impl(c_run, session_id, officer, db, chat_history)
        except Exception as exc:          # pragma: no cover - one step failing is said, not hidden
            logger.warning(f"Compound follow-up step failed: {c!r}: {exc}")
            r = {"response": "<div class='table-intro'>I could not apply this step.</div>", "intent": "compound_noop",
                 "table_data": None}
        results.append((c, r))
    def _has_table(r):
        return "<table" in (r.get("response") or "") or bool(r.get("table_data"))
    last_table = max((i for i, (_, r) in enumerate(results)
                      if r.get("intent") in _LIST_INTENTS and _has_table(r)), default=-1)
    if len(results) == 1:                 # one narrowing step: its own answer, with no numbered wrapper
        return results[0][1]
    from html import escape as _e
    body = []
    for i, (c, r) in enumerate(results):
        resp = r.get("response") or ""
        superseded = (r.get("intent") in _LIST_INTENTS and _has_table(r) and last_table > i
                      and not re.match(r"\s*(?:which|what|how\s+many|why|is\s+there|are\s+there|any\b|count|total)", c, re.IGNORECASE))
        shown = ("<div class='table-intro'>&#10003; applied</div>" if superseded else resp)
        body.append(f"<div><strong>{i + 1}. {_e(c)}</strong></div>\n{shown}")
    out = dict(results[-1][1])
    out.update({"response": "\n\n".join(body), "intent": "compound_followup",
                "table_data": (results[last_table][1].get("table_data") if last_table >= 0 else None)})
    return out
_SURVEY_COUNT_RE = re.compile(
    r"^\s*(?:please\s+)?(?:how\s+many|count(?:\s+of)?|number\s+of|no\.?\s+of|total(?:\s+number\s+of)?|evlo|எத்தனை)\s+"
    r"(?:my\s+|all\s+|the\s+)?(?:survey\s+numbers?|surveys?|சர்வே\s+எண்கள்)(?![A-Za-z])[^.?!]{0,30}[?.!]*\s*$", re.IGNORECASE)


async def _record_field_turn(message, session_id, officer, db, chat_history):
    """Ownership share / type and created / updated questions. Runs before the generic field
    map, which would read "ownership type" as the application type."""
    low = (message or "").lower()
    if _appinfo_qa.claims(message):
        return None
    if _uamod_qa.parse(message):
        return None  # "update date of the modification": a column of the parcel-modification row
    if _natham_qa.parse(message):
        return None  # "update date of the Natham record": a column of the Natham-chitta modification row
    _fk = _fulltransfer_qa.parse(message)["keys"]
    if _fulltransfer_qa.SUBMIT in _fk and _fulltransfer_qa.UPDATE in _fk:
        return None  # "submit date and last update date": both are columns of the transfer record
    if _newowner_qa.claims(message):
        _na = extract_application_number(message)
        if not _na:
            for _m in reversed((chat_history or [])[-14:]):
                _mm = _cmflow_qa._APP_NO.search((_m.get("content") if isinstance(_m, dict) else str(_m)) or "")
                if _mm:
                    _na = _mm.group(0)
                    break
        if _na and await _newowner_qa.load_new(db, _na):
            return None
    if _igrs_owner_qa.claims(message):
        # an owner question about an application that has IGRS owner rows is about those rows
        _ia = extract_application_number(message)
        if not _ia:
            for _m in reversed((chat_history or [])[-14:]):
                _mm = _cmflow_qa._APP_NO.search((_m.get("content") if isinstance(_m, dict) else str(_m)) or "")
                if _mm:
                    _ia = _mm.group(0)
                    break
        if _ia and await _igrs_owner_qa.load(db, _ia):
            return None
    if (not low.strip() or any(w in low for w in attachment_qa._DOC_WORDS)
            or attachment_qa._PAGE_OR_FILETYPE_RE.search(low)):
        return None
    try:
        ctx = await _load_followup_context(db, session_id, chat_history, message,
                                           officer_id=officer.officer_id if officer else None)
    except Exception:
        ctx = None
    field = _asked_ownership_field(message, bool(ctx and ctx.intent in ("survey_owners", "owner_lookup")))
    stamp = None if field else _asked_record_timestamp(message)
    if not (field or stamp):
        return None
    if stamp and _asked_untracked_geo_master_field(low):
        return None  # a master-table column ("when was the ward record updated")

    language = detect_language(message)
    ta = _is_tamil_turn(message, language)

    def reply_with(reply, out_ctx=None):
        return _answer_turn(message, reply, "record_field", session_id, officer, db, language,
                            out_ctx, context_used=bool(ctx))

    if stamp == "owner_ts":
        return await reply_with(
            "உரிமையாளர் பதிவுகளுக்கு உருவாக்க / புதுப்பித்த தேதி SIS பதிவேட்டில் இல்லை." if ta else
            "The register keeps no created / updated date for ownership records; it holds the "
            "owner, share and type only.")

    app_no = extract_application_number(message)
    if not app_no and ctx and ctx.application_numbers and ctx.entity in (fctx.ENTITY_APPLICATION,
                                                                        fctx.ENTITY_FIELD_VISIT):
        app_no = ctx.application_numbers[0]
    if not app_no:
        app_no = _extract_app_number_from_context(message, chat_history, allow_implicit_continuation=True)
    survey_no = None
    if not app_no and field:
        survey_no = await _survey_ref(db, session_id, message, chat_history)
        if not survey_no and ctx and ctx.survey_numbers:
            survey_no = ctx.survey_numbers[0]  # the owners list just shown was about this survey
    if not app_no and not survey_no:
        return None

    # get_application_detail() below reads only `applications` (ISD/NISD). A
    # register-only file (0178, F-Line, ...) always reports "not found" there
    # even when it plainly exists and was just discussed -- stand aside and
    # let the application gate answer it instead (resolve_application_reference
    # / _register_only_field_answer, which read the register and now cover the
    # "last updated" / "submitted" timestamp questions this function is for).
    if app_no and app_no.count("/") >= 3 and app_no.split("/")[1] not in ("0153", "0154"):
        return None

    detail = {}
    if app_no:
        detail = await get_application_detail(db, app_no, officer=officer)
        if not detail.get("found"):
            return await reply_with(detail.get("message") or "I could not find that application in your jurisdiction.")
        app_no = detail.get("application_id") or app_no

    if field:
        survey_no = survey_no or detail.get("survey_no")
        data = await get_survey_owners(db, survey_no, officer=officer) if survey_no else {}
        if not data.get("found", True) or not data.get("owners"):
            reply = data.get("message") or "No ownership records were found for that survey number."
        else:
            reply = _ownership_field_answer(field, survey_no, app_no, data["owners"], ta)
    else:
        reply = None
        if stamp == "updated" and app_no:
            # "updated" is matched here by a deliberately broad "workflow action" cue (see
            # _asked_record_timestamp) that also catches "first workflow action", "who
            # performed it", "latest remark", "immediately before/after X" and "final
            # transition" -- all of which need their OWN answer, not the generic
            # last-updated timestamp. Try the richer, question-specific handler first and
            # only fall through to the generic one below when this really is just
            # "when was it (last) updated".
            reply = await _workflow_history_answer(db, officer, message, app_no, ta)
        if not reply:
            reply = _timestamp_reply(stamp, app_no, detail, ta)

    out_ctx = None
    if app_no:
        out_ctx = fctx.FollowupContext(
            entity=fctx.ENTITY_APPLICATION, application_numbers=[str(app_no).upper()],
            filters={"owner_field": field} if field else {}, query_type="Record field",
            intent="survey_owners" if field else "application_status")
    return await reply_with(reply, out_ctx)


async def _number_turn(message, session_id, officer, db, chat_history):
    """Totals, percentages, ratios, breakdowns and "how many X and how many Y", counted from the
    officer's rows (and, for a follow-up, exactly the rows on screen). None for any other message."""
    text = (message or "").strip()
    if not text or len(text.split()) > 14:
        return None
    text = _fragment_typo_fix(text) or text          # "how mny are aproved" counts like "how many are approved"
    if _WHERE_FROM_RE.search(text):
        text = "count by channel"                     # "where are they from" / "where they r from" -> channel breakdown
    lang = detect_language(text)
    ta = lang in ("ta", "tanglish")
    reply, ctx_json = None, None
    _swapped = _compare_followup.swap_status(
        text, [m.get("content") or "" for m in (chat_history or []) if m.get("role") == "user"])
    if _swapped:                              # "and rejected" after "how many are approved"
        text = _swapped
    if _SURVEY_COUNT_RE.match(text):
        jur = (await _build_jurisdiction_summary(db, officer)).get("jurisdiction") or {}
        n = jur.get("survey_count")
        if isinstance(n, int):
            reply = (f"உங்கள் அதிகார வரம்பில் {n} சர்வே எண்கள் உள்ளன." if ta
                     else f"There {'is' if n == 1 else 'are'} {n} survey number{'s' if n != 1 else ''} in your jurisdiction.")
    elif (_number_qa._COUNT.search(text) or _number_qa._PCT.search(text) or _number_qa._RATIO.search(text)
          or _number_qa._BREAK.search(text) or _number_qa.bare_scope_terms(text)):
        # A question that names the field-visit domain itself ("how many field visits are
        # pending?") is not a count over the application list on screen: the field-visit
        # handler owns it, so stale list context must not answer it.
        if (re.search(r"\bfield\s+visits\b|\bvisits\b|\binspections\b|கள\s*ஆய்வுகள்", text, re.IGNORECASE)
                and not re.search(r"\bapplications?\b|விண்ணப்ப|\d{4}/|\bof\s+(?:them|those|these)\b", text, re.IGNORECASE)):
            return None
        ctx = await _load_followup_context(db, session_id, chat_history, message,
                                           officer_id=officer.officer_id if officer else None)
        # a field-visit table on screen: "how many of them are completed?" counts the VISITS, which the
        # field-visit follow-up layer does from the rows shown -- not the applications in the register
        if (ctx and (ctx.filters or {}).get("about_visit")
                and not re.search(r"\bapplications?\b|விண்ணப்ப|\d{4}/", text, re.IGNORECASE)):
            return None
        # `is not None`, not truthiness: a register listing scoped to one
        # service code that matched zero rows (e.g. "code 0172 applications")
        # carries ctx.application_numbers == [] on purpose -- a deliberate
        # "you're looking at an empty list" record, not "no list at all". A
        # plain truthiness check could not tell that apart from having no
        # carried list, so "how many of them are approved?" after a zero-row
        # listing fell through to `numbers = None` and answered from the
        # officer's whole unscoped jurisdiction instead of "0 of 0".
        numbers = ([n.upper() for n in ctx.application_numbers]
                   if (ctx and ctx.entity == fctx.ENTITY_APPLICATION_LIST
                       and ctx.application_numbers is not None
                       and not (ctx.filters or {}).get("about_visit")) else None)
        from backend.services.postgres import get_officer_applications
        rows_all = (await get_officer_applications(db, officer, status=_ALL_STATUSES_LIST)).get("applications") or []
        # The register holds every service code; `applications` holds only the
        # ISD/NISD ones. Counting over the latter alone answered "of those 19
        # applications" about a list of 47 -- a wrong denominator stated as fact.
        # Rows for the other service codes are added (ISD/NISD keep their fuller
        # `applications` rows), so a total, a percentage or a breakdown covers
        # every type the officer was shown.
        from backend.services.postgres import get_all_service_code_applications
        for _r in rows_all:
            _r.setdefault("service_code", {"ISD": "0154", "NISD": "0153"}.get(str(_r.get("type") or "").upper()))
        _have_nums = {str(r.get("application_number", "")).upper() for r in rows_all}
        for _r in (await get_all_service_code_applications(db, officer)).get("applications") or []:
            if str(_r.get("application_number", "")).upper() not in _have_nums:
                rows_all.append({**_r, "type": _r.get("service_name") or _r.get("service_code")})
        view = [r for r in rows_all if str(r.get("application_number", "")).upper() in set(numbers)] if numbers is not None else None
        _bare = _number_qa.bare_scope_terms(text)
        _bare_table = None
        _bare_matched = None
        if _bare:      # "rejected ?" after an answer: yes / no about the one file, a count over the list
            _one = ([ctx.application_numbers[0].upper()] if (ctx and ctx.entity == fctx.ENTITY_APPLICATION
                                                             and ctx.application_numbers) else None)
            _bview = view if view else ([r for r in rows_all if str(r.get("application_number", "")).upper() in set(_one)]
                                        if _one else None)
            reply = _number_qa.bare_scope_answer(_bare, _bview, ta) if _bview else None
            # A bare status/type/channel word over MULTIPLE carried rows answers
            # with a count and a "say show only X" nudge -- but "not pending"
            # right beside it already renders the table directly (it skips this
            # branch entirely and reaches the followup_context REFINE path
            # instead, since `bare_scope_terms` deliberately excludes negation).
            # That asymmetry read as broken: an officer typing "pending" got a
            # hint to ask again instead of the table "not pending" gave for
            # free. Reuses the same matching bare_scope_answer just did rather
            # than re-deriving a filter.
            if reply and _bview and len(_bview) > 1:
                _matched = [r for r in _bview if _number_qa._match(r, _bare)]
                if _matched:
                    _bare_matched = _matched
                    _bare_table = _build_table_data(
                        "pending_applications", message, str(officer.officer_id) if officer else "",
                        {"applications": _matched})
        else:
            reply = _number_qa.answer(text, rows_all, view, ta, has_list=(numbers is not None))
        # "not nisd" narrowed 70 rows to 6, but the saved context used to
        # carry all 70 forward unchanged -- so the NEXT follow-up ("which is
        # oldest?") silently re-expanded to the unfiltered 70 and could name a
        # NISD file as the answer to a question that had just excluded NISD.
        # A bare-scope match with an actual filtered subset narrows the saved
        # context the same way `_scoped_list_answer`'s REFINE path already
        # does, via the same shared helper.
        if _bare_matched:
            ctx_json = _followup_out_context({"applications": _bare_matched}, ctx) if reply else None
        else:
            ctx_json = ctx.to_json() if (ctx and reply) else None
    if not reply:
        return None
    await save_chat_messages(db=db, session_id=session_id, user_message=message, assistant_message=reply, language=lang,
                             response_time_ms=0, officer_id=officer.officer_id if officer else None,
                             structured_context=ctx_json)
    return {"response": reply, "language": lang, "intent": "number_answer", "sources": [],
            "timestamp": datetime.now(timezone.utc).isoformat(), "context_used": bool(ctx_json),
            "response_time_ms": 0, "table_data": _bare_table}


_CONTINUATION_COMMAND_RE = re.compile(
    r"^(?:please\s+)?(?:continue|resume|proceed|go\s+on|keep\s+going|carry\s+on|"
    r"show\s+(?:the\s+)?(?:rest|remaining\s+part)|the\s+rest|finish\s+the\s+remaining\s+(?:part|steps)|"
    r"continue\s+(?:the\s+)?remaining\s+part|continue\s+from\s+(?:where\s+you\s+stopped|before)|"
    r"continue\s+where\s+we\s+left\s+off|let'?s\s+continue|continue\s+our\s+conversation|"
    r"resume\s+our\s+conversation|pick\s+up\s+where\s+we\s+left\s+off|continue\s+the\s+previous\s+answer|"
    r"meethi\s*(?:sollu|pesu)?|thodaru|thodarunga|"
    r"தொடரவும்|தொடர்ந்து\s*பேசுவோம்|நிறுத்திய\s*இடத்திலிருந்து(?:\s*தொடரவும்)?|மீதி)\s*[.!?]*$",
    re.IGNORECASE
)


async def process_chat(
    message: str,
    session_id: str,
    officer: OfficerContext,
    db: AsyncSession,
    chat_history: list = None
) -> Dict[str, Any]:
    """Answer one message, with the read-only guard armed for the whole turn.

    The guard is applied here rather than inside the handlers because it must
    cover everything a turn touches -- every intent handler, the agent's tool
    loop and the attachment pipeline alike. See `readonly_guard`.
    """
    with chat_turn():
        _RAW_MESSAGE.set(message)
        _bp = _big_plan(message)
        if _bp:
            return await _answer_big(_bp, message, session_id, officer, db, chat_history)
        plan = _plan_requests(message)
        if plan and plan[0] == "multi" and len(plan[1]) >= 2:
            return await _answer_multi(plan, message, session_id, officer, db, chat_history)
        _rc = await _merge_turn(message, session_id, officer, db, chat_history)
        if _rc is None:
            _rc = await _old_codes_turn(message, session_id, officer, db, chat_history)
        if _rc is None:
            _rc = await _record_field_turn(message, session_id, officer, db, chat_history)
        if _rc is None:
            _rc = await _recheck_turn(message, session_id, officer, db, chat_history)
        if _rc is None:
            _rc = await _compound_followup(message, session_id, officer, db, chat_history)
        if _rc is None:
            _rc = await _register_gap_turn(message, session_id, officer, db, chat_history)
        if _rc is None:
            _rc = await _howto_turn(message, session_id, officer, db, chat_history)
            if _rc is None:
                _rc = await _stage_def_turn(message, session_id, officer, db, chat_history)
            if _rc is None:
                _rc = await _calc_turn(message, session_id, officer, db, chat_history)
            if _rc is None:
                _rc = await _stats_turn(message, session_id, officer, db, chat_history)
        if _rc is None:
            _rc = await _number_turn(message, session_id, officer, db, chat_history)
        if _rc is not None:
            return _rc
        plan = _plan_requests(message)
        if plan:
            return await _answer_multi(plan, message, session_id, officer, db, chat_history)
        return await _process_chat_impl(message, session_id, officer, db, chat_history)


async def _process_chat_impl(
    message: str,
    session_id: str,
    officer: OfficerContext,
    db: AsyncSession,
    chat_history: list = None
) -> Dict[str, Any]:
    """
    Main RAG orchestration function for processing chat messages
    
    Args:
        message: User's input message
        session_id: Chat session UUID
        officer: Officer context with jurisdiction info
        db: Database session
        chat_history: Optional chat history from client (sessionStorage)
        
    Returns:
        Dictionary with response, language, and metadata
    """
    start_time = time.time()
    
    try:
        # Step 0: Use provided chat history from client
        if not chat_history:
            chat_history = []
        logger.info("=== CHAT CONTEXT DEBUG ===")
        logger.info(f"Received {len(chat_history)} previous messages from client")
        logger.info(f"Current message: '{message}'")
        if chat_history:
            for i, msg in enumerate(chat_history[-3:]):  # Show last 3
                logger.info(f"  History[{i}]: {msg.get('role')} said: {(msg.get('content') or '')[:50]}...")

        # Step 1: Detect language (on the corrected text, so a slipped Tanglish word
        # does not flip the reply to English)
        language = detect_language(_fragment_typo_fix(message))
        if is_short_reply(message) and chat_history:
            prior_lang = get_prior_turn_language(chat_history)
            if prior_lang in ("ta", "tanglish"):
                language = prior_lang
        logger.info(f"Detected language: {language}")

        # Step 1b: A bare period ("last month") re-scopes the previous question.
        # The merged text drives intent detection and every handler below; the
        # transcript still records what the officer actually typed.
        _original_message = message
        _FV_SCOPE.set(None)
        _FV_COLS.set(None)
        _FV_KEEP.set(None)
        _FV_PRIOR_SORT.set(None)
        _RAW_BY_FIXED.set(None)      # the typo map is per turn: a stale entry would rewrite a later message
        _NEG_EXCLUDE.set(None)
        _neg_scope.EXCLUDE.set(None)
        _SORT_RESET.set(False)
        # Slips in a follow-up-shaped fragment are corrected for every rule below;
        # the transcript still records what was typed.
        _typo_fixed = _add_as_along(fctx.negation_normalise(_fragment_typo_fix(message)))
        if _typo_fixed != message:
            logger.info(f"Follow-up spelling corrected: '{message}' -> '{_typo_fixed}'")
            _RAW_BY_FIXED.set({_typo_fixed: message})
            _original_message = _typo_fixed
        message = _typo_fixed
        # A bare type word ("nisd", "what about isd") continues the previous question
        # with the other type; that is decided before the date / list re-scope, which
        # would otherwise claim it as a list follow-up.
        _swapped = _only_type_after_empty_list(message, chat_history)
        if _swapped == message:
            _swapped = await _type_swap_followup(db, session_id, chat_history, message)
        if _swapped != message:
            logger.info(f"Bare type follow-up: '{_original_message}' -> '{_swapped}'")
            _raw_typed = _RAW_BY_FIXED.get().get(_typo_fixed, _typo_fixed) if _RAW_BY_FIXED.get() else _typo_fixed
            _RAW_BY_FIXED.set({_swapped: _raw_typed})
            message = _swapped
            _original_message = _swapped
            _typo_fixed = _swapped
        else:
            message = _rescope_followup_message(message, chat_history)
            if message != _typo_fixed:
                logger.info(f"Date follow-up re-scoped: '{_original_message}' -> '{message}'")
            else:
                message = _rescope_list_followup(message, chat_history)
                if message != _typo_fixed:
                    logger.info(f"List follow-up re-scoped: '{_original_message}' -> '{message}'")
        # A message the date/list re-scope above already made self-contained
        # must not be further rewritten by the single-application follow-up
        # below -- "how many of those are completed?" right after a field-visit
        # summary was correctly re-scoped here, then the single-app resolver ran
        # on the UNSCOPED _original_message anyway, found a stale application
        # number from further back in the history, and appended it, silently
        # discarding the already-correct re-scope and answering about the wrong
        # record entirely.
        _already_rescoped = message != _typo_fixed

        # Step 1b2: IGRS / CAN asked as a RULE ("if the IGRS number is absent,
        # what does it mean -- so it's not from SRO?"). These name no
        # application, and answering them with a table of applications -- which
        # is what the Sub-Registrar channel filter did -- answers a question
        # nobody asked. The rule is a documented invariant, so it is stated in
        # Python rather than left to the model.
        _rule_ctx_app = _extract_app_number_from_context(message, chat_history, allow_implicit_continuation=True)
        _rule_topic = None if _um_parse(message, chat_history) else _igrs_can_rule_topic(message, allow_bare_tamil_what=not _rule_ctx_app)
        # "what is SRO" is answered by the LLM (grounded on the SRO article in
        # land_rules.txt / faq_english.txt via RAG), not this deterministic
        # table -- explicit product decision, see the chat with the user.
        # Forced to general_query below (not just left to fall through) so it
        # cannot be picked up by an unrelated handler like sale_deed_check,
        # which also matches on "registrar" -- CLAUDE.md already documents
        # that exact regression from before sro_what existed.
        _force_general_query = _rule_topic == "sro_what"
        if _force_general_query:
            _rule_topic = None
        if _rule_topic:
            _rule_text = _igrs_can_rule_answer(_rule_topic, language)
            if _rule_text and _rule_topic.startswith("channel_basis"):
                _rule_text += await _channel_basis_evidence(
                    db, officer, session_id, chat_history, message, language)
            if _rule_text:
                logger.info(f"Answered the IGRS/CAN rule question '{_rule_topic}'")
                await save_chat_messages(
                    db=db, session_id=session_id, user_message=_original_message,
                    assistant_message=_rule_text, language=language,
                    response_time_ms=int((time.time() - start_time) * 1000),
                    officer_id=officer.officer_id if officer else None,
                    structured_context=None)
                return {
                    "response": _rule_text,
                    "language": language,
                    "intent": "igrs_can_rule",
                    "sources": ["land_rules.txt"],
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "context_used": True,
                    "response_time_ms": int((time.time() - start_time) * 1000),
                    "table_data": None,
                }

        # Step 1b3: "what are the ISD/NISD workflow steps" -- a documented,
        # fixed fact (see CLAUDE.md's workflow chains), not something to
        # generate. Left to the LLM it invented a workflow that skipped the
        # SD step entirely and added an unstated "24 hour" SLA.
        _wf_topic = _workflow_steps_topic(message, chat_history)
        # "ward"/"block" are glossary terms AND application fields -- "ward
        # enna" right after naming an application meant "what ward is IT in",
        # not "define a ward", but this check runs before the follow-up
        # context is loaded below, so it answered the glossary definition
        # every time, context or not. Stand aside for just these two terms
        # when an application was named recently, and let the field-lookup
        # path (reached once _followup_ctx is resolved) answer instead.
        if _wf_topic in ("GLOSS:ward", "GLOSS:block", "GLOSS:wards", "GLOSS:blocks"):
            _stand_aside = _recent_app_number_in_history(chat_history)
            # "blocks enna?" (plural, Tanglish/Tamil) after a listing asks for that column;
            # "what is a block?" is still the definition
            if (not _stand_aside and _wf_topic in ("GLOSS:wards", "GLOSS:blocks")
                    and re.search(r"enna|என்ன|edhu", message.lower())):
                _stand_aside = bool(await _load_followup_context(
                    db, session_id, chat_history, message,
                    officer_id=officer.officer_id if officer else None))
            if _stand_aside:
                _wf_topic = None
        if _wf_topic:
            _wf_text = await _workflow_steps_answer_natural(
                _wf_topic, language in ("ta", "tanglish"), not _is_detail_request(message),
                language, chat_history)
            logger.info(f"Answered the {_wf_topic} workflow-steps question deterministically")
            await save_chat_messages(
                db=db, session_id=session_id, user_message=_original_message,
                assistant_message=_wf_text, language=language,
                response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None,
                structured_context=None)
            return {
                "response": _wf_text,
                "language": language,
                "intent": "workflow_steps",
                "sources": ["workflow_guide.txt"],
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "context_used": True,
                "response_time_ms": int((time.time() - start_time) * 1000),
                "table_data": None,
            }

        # Step 1b4: MERGE is not a separate application type
        # here -- see _is_merge_question for why this must never reach
        # general_query. Checked on the raw message, so a bare follow-up
        # fragment ("what about merge", "only merge") is caught the same way
        # a fresh question is, before the follow-up layer or the qualifier
        # guard can turn it into something else.
        if _is_merge_question(_original_message) and not _appinfo_qa.claims(_original_message):
            _merge_text = _no_merge_type_answer(language in ("ta", "tanglish"))
            logger.info("Answered a merge question deterministically -- merge is a field, not a service")
            await save_chat_messages(
                db=db, session_id=session_id, user_message=_original_message,
                assistant_message=_merge_text, language=language,
                response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None,
                structured_context=None)
            return {
                "response": _merge_text,
                "language": language,
                "intent": "no_merge_type",
                "sources": [],
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "context_used": True,
                "response_time_ms": int((time.time() - start_time) * 1000),
                "table_data": None,
            }

        # Direct Handler for "what is today's date / day?" — the server's own
        # clock in Asia/Kolkata (IST), checked ahead of follow-up context resolution so
        # an officer asking the current date/day right after an application is
        # never told that application's submission or inspection date.
        if _is_today_date_question(message):
            res_txt = _today_date_answer(language, message)
            await save_chat_messages(
                db=db, session_id=session_id, user_message=_original_message,
                assistant_message=res_txt, language=language,
                response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None
            )
            return {
                "response": res_txt,
                "language": language,
                "intent": "today_date",
                "sources": [],
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "context_used": False,
                "response_time_ms": int((time.time() - start_time) * 1000),
                "table_data": None
            }

        # Step 1c: Resolve an implicit follow-up against the stored reference
        # context -- "what is the applicant name?", "which one is oldest?",
        # "when was it scheduled?". These name no application, no list and no
        # scope, and until the context record existed they were answered by
        # scraping the previous rendered answer, or not at all. Runs BEFORE
        # intent routing so every handler below sees a self-contained question.
        _followup_ctx = (await _load_followup_context(db, session_id, chat_history, _original_message,
                                                      officer_id=officer.officer_id if officer else None)
                         or _context_from_history(chat_history))

        # Continuation handler (context-aware resumption, pagination, and elaboration)
        if _CONTINUATION_COMMAND_RE.match(_original_message.strip()):
            _is_att_active = (await attachment_qa.attachment_topic_active(db, session_id) or
                              await attachment_qa.any_active_documents(db, officer.officer_id if officer else None, session_id))
            _is_list_active = bool(_followup_ctx and _followup_ctx.entity == fctx.ENTITY_APPLICATION_LIST)
            if not _is_att_active and not _is_list_active:
                _prev_user_q = ""
                _prev_asst_a = ""
                if chat_history:
                    for _m in reversed(chat_history):
                        if not _prev_asst_a and _m.get("role") == "assistant":
                            _prev_asst_a = _m.get("content") or ""
                        elif not _prev_user_q and _m.get("role") == "user":
                            _prev_user_q = _m.get("content") or ""
                        if _prev_asst_a and _prev_user_q:
                            break

                # Case 1: Empty session / No prior context
                if not _prev_asst_a and not _followup_ctx:
                    _empty_text = (
                        "தொடர்வதற்கு முந்தைய தலைப்போ பணியோ எதுவும் இல்லை. விண்ணப்பம், நில அளவை எண், சேவை குறியீடு அல்லது ஆவணம் பற்றி கேளுங்கள்."
                        if language in ("ta", "tanglish") else
                        "There is no previous topic or task to continue from. Please ask about an application, survey number, service code, or upload a document."
                    )
                    logger.info("Continuation requested with no context; asked for domain topic")
                    await save_chat_messages(
                        db=db, session_id=session_id, user_message=_original_message,
                        assistant_message=_empty_text, language=language,
                        response_time_ms=int((time.time() - start_time) * 1000),
                        officer_id=officer.officer_id if officer else None,
                        structured_context=None
                    )
                    return {
                        "response": _empty_text, "language": language, "intent": "continuation_clarification",
                        "sources": [], "timestamp": datetime.now(timezone.utc).isoformat(),
                        "context_used": False, "response_time_ms": int((time.time() - start_time) * 1000),
                        "table_data": None
                    }

                # Case 2: Incomplete / Truncated previous answer (Type A resumption)
                if _is_incomplete_answer(_prev_asst_a, _original_message):
                    logger.info("Resuming truncated/incomplete previous answer via LLM")
                    if language in ("ta", "tanglish"):
                        _resump_prompt = (
                            f"முந்தைய பதில் முழுமையடையாமல் நின்றது.\n\n"
                            f"[கேள்வி]:\n{_prev_user_q}\n\n"
                            f"[இதுவரை வழங்கப்பட்ட பகுதி பதில்]:\n{_prev_asst_a}\n\n"
                            f"ஏற்கனவே கூறியவற்றை மீண்டும் கூறாமல், நிறுத்திய இடத்திலிருந்து தடையின்றி விடையைத் தொடரவும்:"
                        )
                    else:
                        _resump_prompt = (
                            f"The previous answer to the question was cut off or incomplete.\n\n"
                            f"[Original Question]:\n{_prev_user_q}\n\n"
                            f"[Partial Answer Provided So Far]:\n{_prev_asst_a}\n\n"
                            f"Please continue the answer seamlessly from where it was cut off without repeating what has already been said:"
                        )
                    _res_text = await call_llama(_resump_prompt)
                    await save_chat_messages(
                        db=db, session_id=session_id, user_message=_original_message,
                        assistant_message=_res_text, language=language,
                        response_time_ms=int((time.time() - start_time) * 1000),
                        officer_id=officer.officer_id if officer else None,
                        structured_context=_followup_ctx.to_json() if _followup_ctx else None
                    )
                    return {
                        "response": _res_text, "language": language, "intent": "resumed_answer",
                        "sources": [], "timestamp": datetime.now(timezone.utc).isoformat(),
                        "context_used": True, "response_time_ms": int((time.time() - start_time) * 1000),
                        "table_data": None
                    }

                # Case 3: Service code in context (Type B service code expansion)
                _sc_code = None
                if _followup_ctx and _followup_ctx.entity == fctx.ENTITY_SERVICE_CODE:
                    _sc_code = (_followup_ctx.filters or {}).get("service_code") or (_followup_ctx.application_numbers[0] if _followup_ctx.application_numbers else None)
                if not _sc_code and _prev_asst_a:
                    _m_code = re.search(r"\b(015[1-9]|isd|nisd)\b", (_prev_user_q + " " + _prev_asst_a).lower())
                    if _m_code:
                        _sc_code = _m_code.group(1).upper()
                if _sc_code:
                    logger.info(f"Continuation expanding service code {_sc_code}")
                    _sc_text = await _service_code_lookup_answer_natural(
                        f"explain in detail {_sc_code}", language in ("ta", "tanglish"), brief=False,
                        language=language, chat_history=chat_history
                    )
                    _sc_ctx = fctx.FollowupContext(entity=fctx.ENTITY_SERVICE_CODE, application_numbers=[_sc_code],
                                                  filters={"service_code": _sc_code}, query_type="Service Code")
                    await save_chat_messages(
                        db=db, session_id=session_id, user_message=_original_message,
                        assistant_message=_sc_text, language=language,
                        response_time_ms=int((time.time() - start_time) * 1000),
                        officer_id=officer.officer_id if officer else None,
                        structured_context=_sc_ctx.to_json()
                    )
                    return {
                        "response": _sc_text, "language": language, "intent": "service_code_lookup",
                        "sources": ["workflow_guide.txt"], "timestamp": datetime.now(timezone.utc).isoformat(),
                        "context_used": True, "response_time_ms": int((time.time() - start_time) * 1000),
                        "table_data": None
                    }

                # Case 4: Single application in context (Type B application expansion)
                if _followup_ctx and _followup_ctx.entity == fctx.ENTITY_APPLICATION and _followup_ctx.application_numbers:
                    _app_num = _followup_ctx.application_numbers[0]
                    logger.info(f"Continuation expanding application details for {_app_num}")
                    _original_message = message = f"more details for {_app_num}"

                # Case 5: Completed general answer in context (Type B topic continuation)
                elif _prev_user_q:
                    logger.info("Continuation on completed general turn; offering elaboration")
                    _topic_prompt = (
                        f"முந்தைய தலைப்பு: \"{_prev_user_q}\".\n\nஇது குறித்து கூடுதல் விவரங்கள் தேவையா, அல்லது குறிப்பிட்ட விண்ணப்பம், நில அளவை எண், சேவை குறியீடு அல்லது பணிப்பாய்வு பற்றி அறிய விரும்புகிறீர்களா?"
                        if language in ("ta", "tanglish") else
                        f"The previous topic was about: \"{_prev_user_q}\".\n\nWould you like more details on this topic, or would you like to inquire about a specific application, survey number, service code, or workflow step?"
                    )
                    await save_chat_messages(
                        db=db, session_id=session_id, user_message=_original_message,
                        assistant_message=_topic_prompt, language=language,
                        response_time_ms=int((time.time() - start_time) * 1000),
                        officer_id=officer.officer_id if officer else None,
                        structured_context=_followup_ctx.to_json() if _followup_ctx else None
                    )
                    return {
                        "response": _topic_prompt, "language": language, "intent": "topic_continuation",
                        "sources": [], "timestamp": datetime.now(timezone.utc).isoformat(),
                        "context_used": True, "response_time_ms": int((time.time() - start_time) * 1000),
                        "table_data": None
                    }
        _sort_msg, _sort_reset = fctx.normalise_sort_negation(_original_message, _followup_ctx)
        if _sort_msg != _original_message:
            logger.info(f"Sort negation resolved: '{_original_message}' -> '{_sort_msg}'")
            _original_message = message = _sort_msg
            _SORT_RESET.set(_sort_reset)
        _ff_msg = _fullform_followup(_original_message, chat_history)
        if _ff_msg != _original_message:
            logger.info(f"Full-form follow-up: '{_original_message}' -> '{_ff_msg}'")
            _original_message = message = _ff_msg
        _ff_detail_term = _fullform_detail_term(_original_message, chat_history)
        if _ff_detail_term:
            _ff_detail_text = _fullform_detail_reply(_ff_detail_term, language)
            logger.info(f"Answered a fullform detail follow-up for '{_ff_detail_term}'")
            await save_chat_messages(
                db=db, session_id=session_id,
                user_message=_original_message, assistant_message=_ff_detail_text,
                language=language,
                response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None
            )
            return {
                "response": _ff_detail_text,
                "language": language,
                "intent": "fullform_detail",
                "action": None,
                "sources": [],
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "context_used": True,
                "response_time_ms": int((time.time() - start_time) * 1000),
                "table_data": None
            }
        _yr_msg = _qualifier_guard.share_year(_original_message)
        if _yr_msg != _original_message:
            logger.info(f"Shared year: '{_original_message}' -> '{_yr_msg}'")
            _original_message = message = _yr_msg
        _all_msg = _qualifier_guard.whole_register(
            _original_message, any(m.get("role") == "user" for m in (chat_history or [])))
        if _all_msg != _original_message:
            logger.info(f"Whole register: '{_original_message}' -> '{_all_msg}'")
            _original_message = message = _all_msg
        _noun_msg = _qualifier_guard.add_noun(
            _original_message, any(m.get("role") == "user" for m in (chat_history or [])))
        if _noun_msg != _original_message:
            logger.info(f"Scope with no noun: '{_original_message}' -> '{_noun_msg}'")
            _original_message = message = _noun_msg
        _lit_msg = _unheld_qa.litigation(_original_message, chat_history) or _unheld_qa.where_is(_original_message, chat_history) or _unheld_qa.intent_alias(_original_message)
        if _lit_msg:
            _original_message = message = _lit_msg
        # "Show only those with/without a subdivision recorded" is a LIST
        # filter over the carried queue, not a negated question about ONE
        # record -- but `_negq.rewrite()` finds an application number
        # anywhere in the last 8 turns and claims it regardless, stripping
        # "without" and reducing the message to "those a recorded
        # subdivision" (an existence check on whichever stale application it
        # found), before `_field_presence_answer` ever got a chance to see
        # the original wording. Checked first, same precedence `_neg_scope`
        # below already gives an `about_visit` queue over its own rewrite.
        # The STANDALONE shape ("which applications ... have no subdivision
        # recorded") gets the same precedence for the same reason -- it
        # names no application at all, but a stale number sitting in recent
        # chat_history is exactly what `_negq.rewrite()` goes looking for
        # regardless, and it would mangle this message the same way.
        _nq_msg = None if (_followup_ctx and _followup_ctx.application_numbers
                           and fctx.parse_field_presence_request(_original_message)) \
            or fctx.parse_standalone_field_presence_request(_original_message) \
            else _negq.rewrite(_original_message, chat_history)
        if _nq_msg is not None:
            logger.info(f"Negated record question: '{_original_message}' -> '{_nq_msg}' ({_negq.KIND.get()})")
            _original_message = message = _nq_msg
        _neg_prior = dict(_followup_ctx.filters or {}) if _followup_ctx else None
        # a field-visit table on screen: its negations ("not completed", "not ISD", "not in 2025") are
        # answered over the visit rows by the field-visit follow-up layer, not as an application list
        #
        # ONE application in view ("Show full application details for X" ->
        # "Is it not rejected?") is a yes/no STATUS QUESTION about that file,
        # not a request to exclude "rejected" from a list -- `neg_scope`
        # has no concept of a singular referent at all, so it read "not
        # rejected" as the status-list exclusion phrase it recognises
        # everywhere else and rewrote the whole message into a register-
        # wide "excluding rejected" listing, discarding "it" (and the one
        # application it pointed at) entirely. Guarded the same way the
        # field-visit case just above already is: by entity, not by intent,
        # since that is what actually distinguishes "a list is on screen"
        # from "one record is".
        _neg_singular = bool(_followup_ctx and _followup_ctx.entity == fctx.ENTITY_APPLICATION)
        # A negation right after an attachment answer ("don't show those,
        # just the one in our database") is a continuation of THAT answer,
        # not the application register -- `neg_scope` knows only the
        # register, so unguarded it read "show" + "those" as its own
        # listing-exclusion phrase and rewrote the message into "all
        # applications excluding ...", discarding the file context entirely
        # and answering with the officer's whole open queue. Guarded the
        # same way `_neg_singular` above is: by what the conversation is
        # actually about, so the rewritten message still reaches
        # `attachment_qa.plan_answer()` below unmolested and can resolve the
        # negation (or decline) against the file itself.
        _neg_attachment_topic = await attachment_qa.attachment_topic_active(db, session_id)
        _neg_contra = _neg_scope.detect_contradiction(_RAW_MESSAGE.get() or _original_message)
        if _neg_contra and not (_neg_singular or _neg_attachment_topic):
            await save_chat_messages(
                db=db, session_id=session_id, user_message=_original_message,
                assistant_message=_neg_contra, language=language,
                response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None, structured_context=None)
            return {
                'response': _neg_contra, 'language': language, 'intent': 'clarification_needed',
                'sources': [], 'timestamp': datetime.now(timezone.utc).isoformat(),
                'context_used': True, 'response_time_ms': int((time.time() - start_time) * 1000),
                'table_data': None, 'document_ids': None,
            }
        _neg_msg2 = None if (_neg_singular or _neg_attachment_topic
                              or (_followup_ctx and (_followup_ctx.filters or {}).get("about_visit"))) \
            else _neg_scope.rewrite(
            _original_message, _neg_prior,
            bool(_followup_ctx and _followup_ctx.application_numbers
                 and not (_followup_ctx.filters or {}).get("about_visit")))
        if _neg_msg2 is not None:
            logger.info(f"Exclusions taken out: '{_original_message}' -> '{_neg_msg2}' {_neg_scope.EXCLUDE.get()}")
            _original_message = message = _neg_msg2
        _swap_msg = _compare_followup.swap_status(
            _original_message, [m.get("content") or "" for m in (chat_history or []) if m.get("role") == "user"])
        if _swap_msg is not None:
            logger.info(f"Status swap: '{_original_message}' -> '{_swap_msg}'")
            _original_message = message = _swap_msg
        _cmp_msg = _compare_followup.rewrite(
            _original_message,
            list(_followup_ctx.application_numbers)
            if (_followup_ctx and not (_followup_ctx.filters or {}).get("about_visit")) else [],
            [m.get("content") or "" for m in (chat_history or []) if m.get("role") == "user"])
        if _cmp_msg is not None:
            logger.info(f"Comparative follow-up re-asked: '{_original_message}' -> '{_cmp_msg}'")
            _original_message = message = _cmp_msg
        if _followup_is_self_contained(_original_message):
            # The message names its own subject, so it is a question rather
            # than a continuation however short it reads.
            _followup = fctx.Resolution()
        else:
            _followup = fctx.resolve(_original_message, _followup_ctx, language)
        _scoped_sd = None
        # "only completed" / "sort by date descending" / "show along district" after a
        # field-visit table is about the VISITS: re-ask it as a visit question,
        # inside the rows on screen, instead of turning the table into applications.
        _wf_remarks = None if _cm_strong(_original_message, chat_history) else await _workflow_remarks_answer(db, officer, _original_message, _followup_ctx, language)
        if _wf_remarks:
            logger.info("Answered a workflow-remarks question from the workflow history")
            await save_chat_messages(
                db=db, session_id=session_id, user_message=_original_message,
                assistant_message=_wf_remarks, language=language,
                response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None,
                structured_context=_followup_ctx.to_json() if _followup_ctx else None)
            return {
                "response": _wf_remarks, "language": language, "intent": "workflow_remarks",
                "sources": [], "timestamp": datetime.now(timezone.utc).isoformat(),
                "context_used": True, "response_time_ms": int((time.time() - start_time) * 1000),
                "table_data": None,
            }
        _fv_agg = await _fv_aggregate_answer(db, officer, _original_message, _followup_ctx, language)
        if _fv_agg:
            logger.info("Counted over the field-visit table on screen")
            await save_chat_messages(
                db=db, session_id=session_id, user_message=_original_message,
                assistant_message=_fv_agg, language=language,
                response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None,
                structured_context=_followup_ctx.to_json())
            return {
                "response": _fv_agg, "language": language, "intent": "fv_followup_count",
                "sources": [], "timestamp": datetime.now(timezone.utc).isoformat(),
                "context_used": True, "response_time_ms": int((time.time() - start_time) * 1000),
                "table_data": None,
            }
        _fv_inc_exc = _fv_include_exclude_answer(_original_message, _followup_ctx)
        if _fv_inc_exc:
            _fie_text, _fie_scope = _fv_inc_exc
            logger.info(f"Include/exclude resolved over the field-visit table on screen -> {_fie_scope}")
            _fie_ctx = dataclasses.replace(_followup_ctx, application_numbers=_fie_scope)
            await save_chat_messages(
                db=db, session_id=session_id, user_message=_original_message,
                assistant_message=_fie_text, language=language,
                response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None,
                structured_context=_fie_ctx.to_json())
            return {
                "response": _fie_text, "language": language, "intent": "fv_followup_include_exclude",
                "sources": [], "timestamp": datetime.now(timezone.utc).isoformat(),
                "context_used": True, "response_time_ms": int((time.time() - start_time) * 1000),
                "table_data": None,
            }
        # "Show only those with a subdivision recorded." -- a field-PRESENCE
        # filter over the carried list. Checked here, ahead of
        # `_early_subdivision_answer` further down (which otherwise claims
        # any message naming "subdivision" as a per-application field
        # question, regardless of "those"/"only"/"without" asking for a
        # LIST filter instead).
        _fp = await _field_presence_answer(db, officer, _original_message, _followup_ctx, language)
        if _fp:
            _fp_text, _fp_scope = _fp
            logger.info(f"Field-presence filter resolved over the carried list -> {_fp_scope}")
            _fp_ctx = dataclasses.replace(_followup_ctx, application_numbers=_fp_scope)
            await save_chat_messages(
                db=db, session_id=session_id, user_message=_original_message,
                assistant_message=_fp_text, language=language,
                response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None,
                structured_context=_fp_ctx.to_json())
            return {
                "response": _fp_text, "language": language, "intent": "field_presence_filter",
                "sources": [], "timestamp": datetime.now(timezone.utc).isoformat(),
                "context_used": True, "response_time_ms": int((time.time() - start_time) * 1000),
                "table_data": None,
            }
        # "Which applications awaiting a visit have no subdivision
        # recorded?" -- the STANDALONE counterpart of the filter just above:
        # the officer names the population outright, so this runs regardless
        # of whether a list is even carried (and never re-filters one that
        # is -- the named population always wins over whatever was on
        # screen). Checked right after Mode A specifically so a message that
        # happens to match both reads ("those applications... have no
        # subdivision") is claimed by the narrower, carried-list-filtering
        # one first.
        _sfp = await _standalone_field_presence_answer(db, officer, _original_message, session_id, chat_history, language)
        if _sfp:
            _sfp_text, _sfp_matched, _sfp_scope_kind = _sfp
            logger.info(f"Standalone field-presence query ({_sfp_scope_kind}) -> {len(_sfp_matched)} matched")
            _sfp_ctx = fctx.FollowupContext(
                entity=fctx.ENTITY_APPLICATION_LIST, application_numbers=_sfp_matched,
                filters={"about_visit": _sfp_scope_kind in ("awaiting_visit", "overdue_visit")},
                query_type="Field Presence Filter", intent="field_presence_filter")
            await save_chat_messages(
                db=db, session_id=session_id, user_message=_original_message,
                assistant_message=_sfp_text, language=language,
                response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None,
                structured_context=_sfp_ctx.to_json() if _sfp_matched else None)
            return {
                "response": _sfp_text, "language": language, "intent": "field_presence_filter",
                "sources": [], "timestamp": datetime.now(timezone.utc).isoformat(),
                "context_used": True, "response_time_ms": int((time.time() - start_time) * 1000),
                "table_data": None,
            }
        # "Restore the original filter." -- back to the register-listing
        # scope this chain started from, or a clarification when nothing was
        # ever recorded. Checked ahead of the field-visit/field-presence
        # layers above only in the sense that it needs none of their state;
        # its own `_RESTORE_FILTER_RE` is narrow enough (the literal phrase
        # "restore ... filter") that it never competes with them in practice.
        _rf = await _restore_original_filter_answer(db, officer, _original_message, _followup_ctx, language, session_id=session_id, officer_id=officer.officer_id if officer else None)
        if _rf:
            _rf_text, _rf_sd = _rf
            _rf_ctx = (fctx.build_context("all_service_code_applications", _rf_sd)
                       if _rf_sd else _followup_ctx)
            logger.info(f"Restore-original-filter: {'restored' if _rf_sd else 'no snapshot on record'}")
            await save_chat_messages(
                db=db, session_id=session_id, user_message=_original_message,
                assistant_message=_rf_text, language=language,
                response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None,
                structured_context=_rf_ctx.to_json() if _rf_ctx else None)
            return {
                "response": _rf_text, "language": language, "intent": "restore_original_filter",
                "sources": [], "timestamp": datetime.now(timezone.utc).isoformat(),
                "context_used": True, "response_time_ms": int((time.time() - start_time) * 1000),
                "table_data": _build_table_data("all_service_code_applications", _original_message,
                                                str(officer.officer_id), _rf_sd) if _rf_sd else None,
            }
        _fv_re = _fv_followup(_original_message, _followup_ctx, _followup)
        if _fv_re:
            message, _fv_scope = _fv_re
            _FV_SCOPE.set(_fv_scope)
            _already_rescoped = True
            _followup = fctx.Resolution()
            logger.info(f"Field-visit table follow-up re-asked as: '{message}' over {len(_fv_scope)} row(s)")
        # "can i get the full details" / "i need details of both" -- name the
        # rows the officer is looking at and let the ordinary multi-application
        # detail path answer. Runs ahead of both the aggregate and the ambiguous
        # branches, which answered it with a count or with a question.
        if _wants_carried_list_details(_original_message, _followup_ctx) and not _cm_strong(_original_message, chat_history):
            message = _carried_details_message(_followup_ctx, _original_message)
            _already_rescoped = True
            _followup = fctx.Resolution()
            logger.info(
                f"Details follow-up scoped to the "
                f"{len(_followup_ctx.application_numbers)} listed application(s)")
        # "include rejected too" right after a channel listing: `fctx.classify()` reads
        # "include ... too" as the same generic singular/confirmation fragment shape as
        # "is that all?", so without this it never reached the register re-query below at
        # all -- `_followup.ambiguous` was True and the turn was answered "Which one do you
        # mean?" to a question that named no row. Must run ahead of the ambiguous branch for
        # exactly that reason.
        _rej_widen_early = _resolve_rejected_widen(_original_message, chat_history)
        if _rej_widen_early:
            message = _rej_widen_early
            _already_rescoped = True
            _followup = fctx.Resolution()
            logger.info(f"Rejected-widen follow-up resolved: '{_original_message}' -> '{_rej_widen_early}'")
        if _followup.ambiguous:
            # The older resolution path can still name a record for this turn.
            # Asking would be a regression, not a safeguard, so stand aside and
            # let the existing handlers answer as they always did -- but only
            # when the record it names is one of the rows in view. Unchecked, it
            # reached back over the stored context and answered about an
            # application from turns earlier (85 seconds in the agent layer,
            # about a file the officer had moved on from).
            _legacy_num = _extract_app_number_from_context(
                _original_message, chat_history, allow_implicit_continuation=True)
            _carried_nums = {n.upper() for n in
                             ((_followup_ctx.application_numbers or []) if _followup_ctx else [])}
            if (_legacy_num and (not _carried_nums or _legacy_num.upper() in _carried_nums)
                    and not fctx.is_bare_deictic(_original_message)):
                logger.info("Follow-up left to the existing resolution path")
                _followup = fctx.Resolution()
        # "What is my workload?" -> "show" / "show applications" / "list" /
        # "display" -- the workload answer is aggregate counts only, so there
        # is no FollowupContext for it to resolve against; checked ahead of
        # the orphan-fragment check just below, which would otherwise answer
        # "nothing has been shown in this conversation yet" to a message that
        # follows directly on from a summary of exactly these records.
        #
        # Merge note: origin/main independently fixed the same bug with a
        # narrower `_workload_display_followup` flag that only stood ASIDE
        # from the orphan check and let the generic pending-applications path
        # answer. That path is never stage-pinned for any code, while the
        # workload's own ISD/NISD count IS pinned to the officer's current
        # desk -- so it would show more ISD/NISD files than the workload just
        # counted, a scope mismatch the officer has no way to notice.
        # `_workload_show_followup()` (this branch) reruns the SAME two
        # business rules `get_officer_workload()` counts by and answers
        # directly, so the shown total always equals the workload's own --
        # kept over the narrower fix for that reason.
        _wl_show = await _workload_show_followup(db, officer, _original_message, chat_history, language)
        if _wl_show:
            _wl_data, _wl_text = _wl_show
            _wl_ctx = fctx.build_context("all_service_code_applications", _wl_data)
            logger.info("Workload follow-up: showed the %d application(s) behind the workload count",
                        _wl_data.get("count", 0))
            await save_chat_messages(
                db=db, session_id=session_id, user_message=_original_message,
                assistant_message=_wl_text, language=language,
                response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None,
                structured_context=_wl_ctx.to_json() if _wl_ctx else None)
            return {
                "response": _wl_text, "language": language, "intent": "all_service_code_applications",
                "sources": [], "timestamp": datetime.now(timezone.utc).isoformat(),
                "context_used": True, "response_time_ms": int((time.time() - start_time) * 1000),
                "table_data": _build_table_data("all_service_code_applications", _original_message,
                                                str(officer.officer_id), _wl_data),
            }
        # "show only the invalid ones" / "hide the ones not in our database"
        # right after a cross-check answer is a fragment with no
        # `_followup_ctx` of its own -- `_load_followup_context` deliberately
        # returns None the turn after an attachment answer (the same "a kind
        # this layer does not track is a wall" rule documented there), so
        # unguarded this fell straight into the orphan-fragment check just
        # below and was told "nothing has been shown in this conversation
        # yet" about an answer that was sitting right above it. Same
        # precedence fix as `_wl_show` just above, for the same reason.
        _cc_filtered = await attachment_qa._crosscheck_filter_followup(
            db, session_id, _original_message, language)
        if _cc_filtered is not None:
            res_txt = _cc_filtered.text
            logger.info("Cross-check follow-up filtered to %d of the previous result",
                        len((_cc_filtered.extra or {}).get("crosscheck") or []))
            await save_chat_messages(
                db=db, session_id=session_id, user_message=_original_message,
                assistant_message=res_txt, language=language,
                response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None,
                structured_context=({"entity": "attachment", "document_ids": _cc_filtered.document_ids,
                                    **(_cc_filtered.extra or {})}
                                   if len(_cc_filtered.document_ids or []) == 1 else None))
            return {
                "response": res_txt, "language": language, "intent": _cc_filtered.intent,
                "sources": _cc_filtered.sources, "timestamp": datetime.now(timezone.utc).isoformat(),
                "context_used": True, "response_time_ms": int((time.time() - start_time) * 1000),
                "table_data": None, "document_ids": _cc_filtered.document_ids,
            }
        if (not _followup.resolved and not _followup.ambiguous
                and not _neg_attachment_topic
                and not attachment_qa.is_negative_correction(_original_message)
                and not attachment_qa.is_project_relevance_request(_original_message)
                and not attachment_qa.location_reference(_original_message)
                and not await attachment_qa.attachment_topic_active(db, session_id)
                and not await attachment_qa.any_active_documents(db, officer.officer_id if officer else None, session_id)
                and (_is_orphan_list_followup(_original_message, _followup_ctx)
                     or _is_empty_sort_followup(_original_message, _followup_ctx)
                     or _is_bare_fragment_without_context(_original_message, _followup_ctx, chat_history))):
            _orphan_text = _orphan_followup_reply(
                language, empty=_is_empty_sort_followup(_original_message, _followup_ctx)
                and _list_was_asked(chat_history))
            logger.info("List follow-up with no context — asking rather than "
                        "letting the model answer")
            await save_chat_messages(
                db=db, session_id=session_id, user_message=_original_message,
                assistant_message=_orphan_text, language=language,
                response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None,
                structured_context=None)
            return {
                "response": _orphan_text,
                "language": language,
                "intent": "followup_clarification",
                "sources": [],
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "context_used": False,
                "response_time_ms": int((time.time() - start_time) * 1000),
                "table_data": None,
            }
        if _followup.ambiguous:
            logger.info(f"Ambiguous follow-up '{message}' — asking rather than guessing")
            # The ambiguity is over WHICH row the officer means ("the 50th" of
            # 47, "not that one -- which application?"), not whether the list
            # itself is still valid -- it is, Resolution carries it through as
            # `context`. Re-saving `_followup_ctx` (instead of None) keeps that
            # list in view, so "the 2nd one" right after this clarification
            # still resolves instead of "nothing has been shown yet".
            await save_chat_messages(
                db=db, session_id=session_id, user_message=_original_message,
                assistant_message=_followup.clarification, language=language,
                response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None,
                structured_context=_followup_ctx.to_json() if _followup_ctx else None)
            return {
                "response": _followup.clarification,
                "language": language,
                "intent": "followup_clarification",
                "sources": [],
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "context_used": True,
                "response_time_ms": int((time.time() - start_time) * 1000),
                "table_data": None,
            }
        if (_followup.resolved and _followup.full_details
                and _followup.application_numbers):
            # "details of both" -- the records, not a count of them. Name the
            # files and let the ordinary application_status path answer.
            logger.info(f"Follow-up asks for the details of "
                        f"{len(_followup.application_numbers)} listed application(s)")
            message = _multi_details_message(_followup.application_numbers)
            _followup = fctx.Resolution()
        if _followup.resolved and _followup.entity == fctx.ENTITY_APPLICATION_LIST:
            # A question about the list already on screen is answered from
            # exactly those applications, re-read under the officer's
            # jurisdiction -- never by re-running the original search.
            # The carried set may itself have come from an unscoped listing
            # ("show all applications") that legitimately included rejected
            # rows; only re-apply the default rejected-exclusion when the
            # ORIGINAL context was already scoped by status, so a plain
            # "add a column" follow-up cannot silently shrink the set.
            _include_rejected = not (
                _followup.status or (_followup_ctx and _followup_ctx.filters.get("status")))
            _sort_asked = (extract_sort_order(fctx.correct_spelling(_original_message))
                           if fctx.is_sort_fragment(_original_message) else None)
            _scoped_sd = await get_applications_by_numbers(
                db, officer, _followup.application_numbers,
                status=_followup.status, application_type=_followup.application_type,
                include_rejected=_include_rejected,
                sort_by=_sort_asked[0] if _sort_asked else None,
                sort_dir=_sort_asked[1] if _sort_asked else None)
            _scoped_text = None
            if len(_followup.application_numbers) == 1 and _WF_CUE.search(_original_message or ""):
                _scoped_text = await _single_app_workflow_answer(
                    db, officer, _original_message, _followup.application_numbers[0],
                    chat_history, language in ("ta", "tanglish"))
            if _scoped_text is None:
                _scoped_text = _scoped_list_answer(
                    _scoped_sd, _followup, _original_message, language)
            logger.info(
                f"Answered a list follow-up over {len(_followup.application_numbers)} "
                f"carried application(s)")
            # "show their IGRS number" is already answered in full -- one
            # line per application, every row named -- by _project_field_answer
            # above. Attaching the generic applications table beneath it
            # repeats nothing the officer asked for and everything they
            # didn't (survey, sub-divisions, status, stage...), the same
            # redundant-table failure CLAUDE.md already documents for
            # submission_channel_check ("wants one word back"). A genuine
            # listing/aggregate follow-up still gets the table.
            # "that applicant" / "their names" pivots the conversation to the
            # people: remember it so a bare "both" next stays on the applicants.
            _pk = fctx.field_projections(_original_message) or []
            if (any(k in ("applicant_name", "applicant_mobile") for k in _pk)
                    or _asks_applicant_focus(_original_message)) and isinstance(_scoped_sd, dict):
                _scoped_sd["detail_focus"] = "applicant"
            _is_field_projection = bool(
                getattr(_followup, "per_row_field", None)
                or fctx.field_projection(_original_message)
                or fctx.field_projections(_original_message) is not None
                or _SINGLE_PICK_FOLLOWUP_RE.search(_original_message.lower()))
            await save_chat_messages(
                db=db, session_id=session_id, user_message=_original_message,
                assistant_message=_scoped_text, language=language,
                response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None,
                structured_context=(_followup_out_context(
                    _scoped_sd, _followup_ctx, row_peek=getattr(_followup, "row_peek", False),
                    last_peeked=getattr(_followup, "application_number", None))))
            return {
                "response": _scoped_text,
                "language": language,
                "intent": "followup_list",
                "sources": [],
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "context_used": True,
                "response_time_ms": int((time.time() - start_time) * 1000),
                "table_data": _build_table_data("pending_applications", message,
                                                str(officer.officer_id), _scoped_sd)
                if _scoped_sd.get("applications") and not _is_field_projection else None,
            }
        # The legacy list re-scope turned "what about the third" (after a field-visit
        # summary) into "...third field visits", which re-ran the whole summary. A
        # pick resolved against the rows on screen, about their visits, wins.
        if _already_rescoped and _followup.resolved and _followup.about_visit and _followup.application_number:
            message = _original_message
            _already_rescoped = False
        if (_followup.resolved and not _already_rescoped
                and (_followup.application_number or _followup.entity == fctx.ENTITY_SURVEY)):
            _resolved_message = _apply_followup_resolution(
                _original_message, _followup,
                _last_user_message(chat_history))
            if _resolved_message != message:
                logger.info(f"Follow-up resolved: '{message}' -> '{_resolved_message}'")
                message = _resolved_message

        # Direct Handler for "what does an application number look like" —
        # asked as a general/meta question ("what does an application number
        # format look like?") this fell to the LLM, which fabricated a format
        # ("APP-2024-000001") that doesn't match the real system at all
        # (YYYY/ServiceCode/DistrictCode/SerialNumber). Application numbers
        # are exactly the kind of fact CLAUDE.md says must never be left to
        # the LLM, so this is answered deterministically instead.
        _msg_lower_fmt = message.lower()
        if any(p in _msg_lower_fmt for p in [
            "application number format", "format of application number", "format of an application number",
            "application number structure", "structure of application number", "structure of an application number",
            "application number look like", "application number pattern",
            "how are application numbers structured", "how is an application number structured",
            "விண்ணப்ப எண் வடிவம்", "விண்ணப்ப எண்ணின் அமைப்பு",
        ]) or (
            any(w in _msg_lower_fmt for w in ["application number", "app number", "விண்ணப்ப எண்"])
            and any(w in _msg_lower_fmt for w in ["format", "look like", "structure", "structured", "pattern", "வடிவம்", "அமைப்பு"])
        ):
            is_ta_fmt = language in ("ta", "tanglish")
            if is_ta_fmt:
                res_txt = (
                    "விண்ணப்ப எண் இந்த அமைப்பில் இருக்கும்: "
                    "**ஆண்டு/சேவைக்குறியீடு/மாவட்டக்குறியீடு/வரிசை எண்** "
                    "(எ.கா. 2026/0154/28/001167). "
                    "சேவைக்குறியீடு: 0154 = ISD, 0153 = NISD. "
                    "மாவட்டக்குறியீடு 2 இலக்கங்கள் (எ.கா. தூத்துக்குடிக்கு 28), "
                    "வரிசை எண் 6 இலக்கங்கள்."
                )
            else:
                res_txt = (
                    "An application number follows the format "
                    "**YEAR/SERVICE_CODE/DISTRICT_CODE/SERIAL_NUMBER** "
                    "(e.g. 2026/0154/28/001167). The service code is 0154 for ISD, "
                    "or 0153 for NISD; the district code is 2 digits "
                    "(28 for Thoothukudi); the serial number is 6 digits."
                )
            await save_chat_messages(
                db=db, session_id=session_id, user_message=_original_message,
                assistant_message=res_txt, language=language,
                response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None
            )
            return {
                "response": res_txt,
                "language": language,
                "intent": "app_number_format_info",
                "sources": [],
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "context_used": True,
                "response_time_ms": int((time.time() - start_time) * 1000),
                "table_data": None
            }

        # Direct Handler for District Code reference queries (bypasses jurisdiction checks)
        _msg_lower_dc = message.lower()
        _dc_name_match = _DISTRICT_NAME_RE.search(_msg_lower_dc)
        _dc_name_match_ta = _DISTRICT_NAME_TA_RE.search(message)
        if (any(w in _msg_lower_dc for w in ["district code", "taluk code", "code of", "district_code", "குறியீடு", "மாவட்டம் கோடு"])
                or ("code" in _msg_lower_dc and _dc_name_match) or _dc_name_match_ta):
            matched_dist = None
            if _dc_name_match:
                _d_name = _dc_name_match.group(1).lower()
                matched_dist = (_d_name.title(), DISTRICT_NAME_MAP[_d_name])
            elif _dc_name_match_ta:
                _d_code = DISTRICT_TAMIL_NAME_MAP[_dc_name_match_ta.group(1)]
                matched_dist = (DISTRICT_CODE_TO_TAMIL_NAME[_d_code], _d_code)

            _dc_sp = await _dc_special(db, officer, message, matched_dist, language)
            if matched_dist or _dc_sp:
                d_title, d_code = matched_dist or ("", "")
                is_ta = (language in ("ta", "tanglish") or bool(_DC_TANGLISH_RE.search(_msg_lower_dc))
                         or "குறியீடு" in _msg_lower_dc or bool(_dc_name_match_ta))
                if _dc_sp:
                    res_txt = _dc_sp
                elif is_ta:
                    res_txt = f"{d_title} மாவட்டத்தின் அதிகாரப்பூர்வ குறியீடு (District Code): **{d_code}**."
                else:
                    res_txt = f"The official district code for **{d_title}** is **{d_code}**."

                await save_chat_messages(
                    db=db, session_id=session_id, user_message=_original_message,
                    assistant_message=res_txt, language=language,
                    response_time_ms=int((time.time() - start_time) * 1000),
                    officer_id=officer.officer_id if officer else None
                )
                return {
                    "response": res_txt,
                    "language": language,
                    "intent": "district_code",
                    "sources": [],
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "context_used": True,
                    "response_time_ms": int((time.time() - start_time) * 1000),
                    "table_data": None
                }

        # Submission channel (CSC/Citizen/Sub-Registrar) questions are answered
        # by the submission_channel_check intent below, via
        # _render_submission_channel_answer() -- which now also leads with
        # Yes/No when the officer named the one channel they're asking about
        # ("submitted through Sub-Registrar?"). An earlier, duplicate handler
        # used to live here and short-circuit before that intent was ever
        # reached: its "names this/it/application" gate was dead code (the
        # substring check for "it" matches inside "submitted" itself), so it
        # fired on every "submitted through/by ..." phrasing and always
        # answered with the bare channel label, never a Yes or No. Removed
        # rather than patched a second time -- the real intent handler already
        # does the same app-number-from-context resolution.

        # Security guard: password / secret queries are NEVER answered.
        if _is_sensitive_query(message):
            _refusal = (
                "கடவுச்சொல் தகவல்களை வழங்க அனுமதி இல்லை. உங்கள் கடவுச்சொல்லை மீட்டமைக்க "
                "உங்கள் நிர்வாகியை தொடர்பு கொள்ளவும்."
                if language in ("ta", "tanglish") else
                "I am not authorised to provide password information. "
                "Please contact your system administrator to reset your password."
            )
            await save_chat_messages(
                db=db, session_id=session_id, user_message=_original_message,
                assistant_message=_refusal, language=language,
                response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None
            )
            return {
                "response": _refusal, "language": language,
                "intent": "sensitive_refused", "sources": [],
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "context_used": False,
                "response_time_ms": int((time.time() - start_time) * 1000),
                "table_data": None
            }

        # Direct Handler for "who am I?" — answered from the officer's own
        # record. The LLM only knows the role, so it replied with the role.
        if _is_officer_identity_question(message):
            res_txt = await _officer_identity_reply(
                db, officer, language, _identity_facet(message))
            await save_chat_messages(
                db=db, session_id=session_id, user_message=_original_message,
                assistant_message=res_txt, language=language,
                response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None
            )
            return {
                "response": res_txt,
                "language": language,
                "intent": "officer_identity",
                "sources": [],
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "context_used": True,
                "response_time_ms": int((time.time() - start_time) * 1000),
                "table_data": None
            }

        # Direct Handler for a bare format follow-up ("in table", "as bullets",
        # "in short"). It names no subject, so the LLM cannot answer it; the
        # answer already on screen is what the officer wants reshaped.
        _reformat_to = _detect_reformat_request(message)
        if _reformat_to:
            res_txt = _reformat_previous_answer(chat_history, _reformat_to, language)
            if not res_txt:
                _had_prev = bool(_last_assistant_message(chat_history))
                # "list"/"short" also open real queries — with nothing to
                # reshape, let the normal pipeline answer them instead.
                if not _had_prev and _reformat_to in _AMBIGUOUS_REFORMATS:
                    _reformat_to = None
                else:
                    res_txt = _cannot_reformat_reply(_reformat_to, language, _had_prev)
        if _reformat_to:
            await save_chat_messages(
                db=db, session_id=session_id, user_message=_original_message,
                assistant_message=res_txt, language=language,
                response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None
            )
            return {
                "response": res_txt,
                "language": language,
                "intent": f"reformat_as_{_reformat_to}",
                "sources": [],
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "context_used": True,
                "response_time_ms": int((time.time() - start_time) * 1000),
                "table_data": None
            }

        # Direct Handler for "what was my previous message?" — answered from the
        # chat history, never from the LLM (it echoes the current turn instead).
        if _is_conversation_recall(message):
            res_txt = _conversation_recall_reply(message, chat_history, language)
            await save_chat_messages(
                db=db, session_id=session_id, user_message=_original_message,
                assistant_message=res_txt, language=language,
                response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None
            )
            return {
                "response": res_txt,
                "language": language,
                "intent": "conversation_recall",
                "sources": [],
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "context_used": True,
                "response_time_ms": int((time.time() - start_time) * 1000),
                "table_data": None
            }

        # Direct Handler for owner photo / image retrieval requests
        if _is_owner_photo_request(message):
            res_txt = _owner_photo_reply(language, message)
            await save_chat_messages(
                db=db, session_id=session_id, user_message=_original_message,
                assistant_message=res_txt, language=language,
                response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None
            )
            return {
                "response": res_txt,
                "language": language,
                "intent": "owner_photo_unavailable",
                "sources": [],
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "context_used": True,
                "response_time_ms": int((time.time() - start_time) * 1000),
                "table_data": None
            }

        # Step 2: Parse intent to determine which DB query to run
        # The client sends no intent field, so this re-parses the previous user
        # message rather than looking for a marker that is never written.
        _prev_intent = _resolve_prev_intent(chat_history)
        if _FV_SCOPE.get() is not None:      # re-asked as a visit-table question: the "previous intent" is the table's
            _prev_intent = "field_visits"
        _unk_from_confirm = _resolve_unknown_from_confirmation(message, chat_history)
        if _unk_from_confirm:
            logger.info(f"Unknown-source confirmation resolved: '{message}' -> '{_unk_from_confirm}'")
            message = _unk_from_confirm
        _rej_widen = _resolve_rejected_widen(message, chat_history)
        if _rej_widen:
            logger.info(f"Rejected-widen follow-up resolved: '{message}' -> '{_rej_widen}'")
            message = _rej_widen
        _neg_msg = _rewrite_negated_types(message)
        if _neg_msg != message:
            logger.info(f"Negated type rewritten: '{message}' -> '{_neg_msg}'")
            message = _neg_msg
        message = _all_word_to_english(_take_sort_clause(message))
        intent = parse_intent(message, prev_intent=_prev_intent)
        _cm_strong_hit = _cm_strong(message, chat_history)
        _bare_status_hit = _bare_app_status(message, chat_history)
        _app_evidence = _app_evidence_in_scope(message, chat_history)
        _cm_override_blocked = (
            attachment_qa.is_negative_correction(message)
            or attachment_qa.is_project_relevance_request(message)
            or attachment_qa.location_reference(message)
        )
        if (_cm_strong_hit or _bare_status_hit) and _app_evidence and not _cm_override_blocked:
            logger.debug(
                "ROUTING_DEBUG path=non-stream msg=%r parsed_intent=%r cm_strong=%r "
                "bare_status=%r app_evidence=%r app=%r -> application_status",
                message, intent, _cm_strong_hit, _bare_status_hit, _app_evidence, extract_application_number(message))
            intent = "application_status"
        elif _cm_strong_hit or _bare_status_hit:
            logger.debug(
                "ROUTING_DEBUG path=non-stream msg=%r parsed_intent=%r cm_strong=%r "
                "bare_status=%r app_evidence=%r app=%r -> override SUPPRESSED (no evidence)",
                message, intent, _cm_strong_hit, _bare_status_hit, _app_evidence, extract_application_number(message))
        # "what is the payment information?" has no app number and no "this
        # application" phrase, so rag.py's fee-word gate reads it as a
        # register-wide fee_lookup -- correct with no active file, but wrong
        # right after discussing one: the officer meant that file's fee /
        # challan / payment mode, which _payment_composite_answer() already
        # answers from, and never gets reached because intent never becomes
        # application_status. Same evidence gate as the cm_strong override
        # above, so this never fires on a genuinely fresh register question.
        if intent == "fee_lookup" and _PAYMENT_COMPOSITE_RE.search(message or "") and _app_evidence_in_scope(message, chat_history):
            intent = "application_status"
        # "List every survey number mentioned." / "What subdivision number
        # belongs to the first record?" -- a bare, numberless survey/owner
        # question with nothing in the officer's register to resolve. The
        # survey_detail/survey_owners catch-all in rag.py matches on the
        # words alone (it assumes a number will be resolved downstream, via
        # _survey_ref or an application already in view), so with neither a
        # digit in the message nor an application/survey in view, the
        # deterministic handler always answered "No records found" -- even
        # when the officer had just uploaded a document that plainly
        # contains the survey number being asked about. Confirmed live (QA
        # audit 2026-10-13, upload pass): rephrasing the identical question
        # as "in the uploaded file, what is the synthetic survey number?"
        # already worked, proving the attachment layer itself is fine -- only
        # the intent gate never gave it a chance. Only reached with an active
        # attachment, so a session with nothing uploaded is unaffected.
        if (_is_unresolved_survey_question(intent, message, _followup_ctx)
                and await attachment_qa.any_active_documents(
                    db, officer.officer_id if officer else None, session_id)):
            logger.info(f"Bare {intent} question with an active attachment; "
                       "routing to general_query instead of the empty register lookup")
            intent = "general_query"
        if _force_general_query:
            intent = "general_query"
        # "detailed" / "explain" as a bare follow-up after a service_code_lookup
        # → re-route to service_code_lookup so the handler gives the full answer.
        _sentence_format_revert_code = _negative_sentence_format_context_code(message, _followup_ctx)
        _force_detailed_service_code_answer = bool(_sentence_format_revert_code)
        if _sentence_format_revert_code:
            # This is a FORMAT CORRECTION, not another request for a one-line
            # explanation. Convert it into a deterministic detailed lookup and
            # bypass the paraphraser below so the single-sentence answer cannot
            # be repeated by the model.
            intent = "service_code_lookup"
            message = f"explain in detail {_sentence_format_revert_code}"
            logger.info("Negative sentence-format follow-up: restoring detailed service-code answer")
        elif (intent == "general_query" and _is_explain_request(message)
                and _prev_intent == "service_code_lookup"):
            intent = "service_code_lookup"
            # The follow-up itself names no code ("explain in detail",
            # "explain briefly") -- it means the one from the previous turn.
            # Without this, _service_code_lookup_answer saw an empty code and
            # matched it as a "" prefix against all 29 codes.
            if not re.search(r"\d{3,4}", message) and not re.search(r"\b(?:isd|nisd)\b", message.lower()):
                _prev_q = _last_user_message(chat_history)
                if _prev_q:
                    message = f"{message} {_prev_q}"
        elif (intent == "general_query" and _is_explain_request(message)
                and not await attachment_qa.attachment_topic_active(db, session_id)
                and not await attachment_qa.any_active_documents(
                    db, officer.officer_id if officer else None, session_id)):
            # "explain" / "explain more" after a LISTING or a single record
            # card -- the same gap the service_code_lookup re-route above
            # closes for a definitional answer, but general enough that it
            # was falling to the generic "I'm not sure what you mean"
            # fallback for every other kind of previous answer (sometimes
            # several seconds late: "explain" alone carries no domain
            # evidence, so general_query's agent/LLM path had nothing to
            # ground a tool call in and took seconds to give up before
            # reaching that same fallback text) -- or, after a field-visit
            # listing, was answered with the wrong noun ("applications" for
            # what is actually a list of visits). A listing is ambiguous
            # (the standing rule: never infer over more than one referent),
            # so the officer is asked which record; a single one already in
            # view is described in full instead; and with nothing to
            # re-explain at all (a self-contained prior answer like a
            # comparison builds no context), the decline is immediate
            # rather than routed through the agent.
            _svc_code_ctx = (_followup_ctx.filters or {}).get("service_code") \
                if _followup_ctx and _followup_ctx.entity == fctx.ENTITY_SERVICE_CODE else None
            _single = _followup_ctx.single_application if _followup_ctx else None
            if not _single and _followup_ctx and _followup_ctx.entity == fctx.ENTITY_APPLICATION_LIST \
                    and len(_followup_ctx.application_numbers) == 1:
                _single = _followup_ctx.application_numbers[0]
            _single_survey = (_followup_ctx.survey_numbers[0]
                              if _followup_ctx and _followup_ctx.entity == fctx.ENTITY_SURVEY
                              and _followup_ctx.survey_numbers else None)
            if _svc_code_ctx:
                intent = "service_code_lookup"
                message = f"{message} {_svc_code_ctx}"
            elif _single:
                intent = "application_status"
                message = f"{message} {_single}"
            elif _single_survey:
                intent = "survey_detail"
                message = f"{message} {_single_survey}"
            elif (_followup_ctx and _followup_ctx.entity in (fctx.ENTITY_APPLICATION_LIST, fctx.ENTITY_FIELD_VISIT)
                    and len(_followup_ctx.application_numbers) > 1):
                _n = len(_followup_ctx.application_numbers)
                _is_visit_list = bool((_followup_ctx.filters or {}).get("about_visit")
                                       or _followup_ctx.entity == fctx.ENTITY_FIELD_VISIT
                                       or (_followup_ctx.intent or "").startswith("fv_")
                                       or _followup_ctx.intent == "field_visits")
                _noun_en = "field visit" if _is_visit_list else "application"
                _noun_ta = "கள ஆய்வு" if _is_visit_list else "விண்ணப்பம்"
                _clar = (f"நீங்கள் பார்த்த {_n} {_noun_ta} பதிவுகளில் எதை விளக்கமாக அறிய விரும்புகிறீர்கள்? எண்ணைச் சொல்லுங்கள்."
                         if language in ("ta", "tanglish") else
                         f"You're looking at {_n} {_noun_en}s -- which one would you like "
                         "explained? Reply with its number.")
                await save_chat_messages(
                    db=db, session_id=session_id, user_message=_original_message,
                    assistant_message=_clar, language=language,
                    response_time_ms=int((time.time() - start_time) * 1000),
                    officer_id=officer.officer_id if officer else None
                )
                return {
                    "response": _clar, "language": language, "intent": "explain_clarify",
                    "sources": [], "timestamp": datetime.now(timezone.utc).isoformat(),
                    "context_used": True, "response_time_ms": int((time.time() - start_time) * 1000),
                    "table_data": None
                }
            else:
                res_txt = _clarification_reply(language)
                await save_chat_messages(
                    db=db, session_id=session_id, user_message=_original_message,
                    assistant_message=res_txt, language=language,
                    response_time_ms=int((time.time() - start_time) * 1000),
                    officer_id=officer.officer_id if officer else None
                )
                return {
                    "response": res_txt, "language": language, "intent": "explain_clarify",
                    "sources": [], "timestamp": datetime.now(timezone.utc).isoformat(),
                    "context_used": True, "response_time_ms": int((time.time() - start_time) * 1000),
                    "table_data": None
                }
        # "how do you know <application> is SRO?" is about that file's channel, whichever verb was used
        if (intent in ("application_status", "general_query") and _ANY_APP_NUMBER_RE.search(message)
                and _CHANNEL_BASIS_RE.search(message.lower()) and _CHANNEL_NAMED_RE.search(message.lower())):
            intent = "submission_channel_check"
        logger.info(f"Parsed intent: {intent} (prev_intent={_prev_intent})")
        # Semantic check for conversational messages the keyword rules did not
        # claim ("hey buddy whats up"). Only messages no deterministic SIS rule
        # took, and only short ones with no SIS vocabulary, are ever asked.
        # "good job" / "im not satisfied with your performance" and the rest
        # of `_special_scope_kind` (capability, out-of-scope, sentiment,
        # identity, time) are ALSO answered deterministically, but only much
        # further down -- so without this guard the embedding model was
        # called anyway for every one of them (3-16s the first time, since it
        # also lazily builds the prototype embedding cache), its answer
        # thrown away the moment the deterministic check ran and overrode it.
        # Skipping the call here is the whole fix: nothing downstream changes.
        _sem_kind = None
        if (intent == "general_query" and not _is_capability_question(message)
                and not _is_out_of_scope(message) and not _special_scope_kind(message)):
            _sem_kind = await semantic_intent.classify(
                message, any(t in message.lower() for t in _DOMAIN_TERMS))
            if _sem_kind:
                intent = "greeting"

        # Direct Handler: answer from a file the officer attached this session.
        # `plan_answer` returns None unless this turn really is about an
        # attachment, so ordinary SIS questions keep going to the deterministic
        # PostgreSQL handlers below even with a file open. When it does claim
        # the turn it has already resolved which file, computed any CSV figure
        # from the stored rows, and decided whether there is evidence at all —
        # a plan with no prompt is answered without calling the LLM.
        _cm_blocked = _cm_strong(message, chat_history) and not (
            attachment_qa.is_negative_correction(message)
            or attachment_qa.is_project_relevance_request(message)
            or attachment_qa.location_reference(message)
        )
        # Which antecedent a negative correction ("I don't think so", "not
        # that") disputes -- decided BEFORE the attachment layer gets a
        # chance to claim it just because the topic happens to be active.
        # See `_route_negative_correction()` (FLINE-NEGATION-001).
        _neg_route = await _route_negative_correction(message, chat_history, db, session_id, language)
        if _neg_route and _neg_route["route"] == "ambiguous":
            _neg_resp = _neg_route["response"]["response"]
            await save_chat_messages(
                db=db, session_id=session_id, user_message=_original_message,
                assistant_message=_neg_resp, language=language,
                response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None,
                structured_context=_neg_route.get("carry_context")
            )
            return {
                "response": _neg_resp, "language": language,
                "intent": _neg_route["response"].get("intent", "general_negative_correction"),
                "sources": [], "timestamp": datetime.now(timezone.utc).isoformat(),
                "context_used": True, "response_time_ms": int((time.time() - start_time) * 1000),
                "table_data": None
            }
        if _neg_route and _neg_route["route"] == "attachment":
            # The router already established -- from the message itself, not
            # from `attachment_topic_active()`'s single-last-turn read -- that
            # this correction is about the file(s). Calling the handler
            # directly here (rather than letting `plan_answer()` re-derive
            # topic-active state and possibly decline) is what makes an
            # EXPLICIT file reference work even when an intervening turn (for
            # instance this router's own "ambiguous" clarification, saved
            # with no attachment context) would otherwise have reset that
            # flag. No DB write happens here -- only a read of which
            # documents are active.
            _neg_docs = await attachment_qa.attachment_store.active_documents(
                db, officer.officer_id if officer else None, session_id)
            _att_plan = await attachment_qa._handle_negative_correction(
                db, officer, session_id, message, language, _neg_docs) if _neg_docs else None
        else:
            _force_general_negcorr = bool(_neg_route and _neg_route["route"] == "general")
            _att_plan = None if (_attachment_turn_is_not_about_a_file(message) or _cm_blocked or _force_general_negcorr) else await attachment_qa.plan_answer(
                db=db, officer=officer, session_id=session_id,
                message=attachment_qa.drop_appended_numbers(message, _RAW_MESSAGE.get()), intent=intent, language=language)
        if _att_plan is not None:
            if _att_plan.prompt:
                res_txt = await call_llama(_att_plan.prompt)
                if _att_plan.retry_prompt and attachment_qa.is_refusal(res_txt):
                    res_txt = await call_llama(_att_plan.retry_prompt)
            else:
                res_txt = _att_plan.text
            # Leave a focus behind only when this turn resolved to exactly one
            # document -- the same "never infer when more than one referent
            # fits" rule the application follow-ups already use. A pronoun
            # follow-up with several files active reads this back in
            # attachment_qa._last_focused_document instead of re-asking which
            # file every single turn.
            _att_ctx = {
                "entity": "attachment",
                "document_ids": _att_plan.document_ids or [],
                "sources": _att_plan.sources or [],
                "attachment_kind": _att_plan.kind,
                "is_refusal": (_att_plan.kind == attachment_qa.KIND_REFUSAL),
                "prior_question": message,
                **(_att_plan.extra or {}),
            }
            await save_chat_messages(
                db=db, session_id=session_id, user_message=_original_message,
                assistant_message=res_txt, language=language,
                response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None,
                structured_context=_att_ctx
            )
            return {
                "response": res_txt,
                "language": language,
                "intent": _att_plan.intent,
                "sources": _att_plan.sources,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "context_used": True,
                "response_time_ms": int((time.time() - start_time) * 1000),
                "table_data": None,
                "attachment_kind": _att_plan.kind,
                "citations": _att_plan.citations,
                "document_ids": _att_plan.document_ids,
            }

        if _att_plan is None and not _contentless_message(_original_message) and attachment_qa.is_negative_correction(_original_message):
            _neg_data = _build_general_negative_correction_response(_original_message, chat_history, language, officer)
            if _neg_data:
                _res_text = _neg_data["response"]
                await save_chat_messages(
                    db=db, session_id=session_id, user_message=_original_message,
                    assistant_message=_res_text, language=language,
                    response_time_ms=int((time.time() - start_time) * 1000),
                    officer_id=officer.officer_id if officer else None,
                    structured_context=None
                )
                return {
                    "response": _res_text, "language": language, "intent": _neg_data.get("intent", "general_negative_correction"),
                    "sources": [], "timestamp": datetime.now(timezone.utc).isoformat(),
                    "context_used": True, "response_time_ms": int((time.time() - start_time) * 1000),
                    "table_data": None
                }



        # ── Step 2b: Jurisdiction access check ──────────────────────────────
        # Block-level officers cannot query ward/taluk/district level data.
        # Ward-level officers cannot query taluk/district level data. Etc.
        _jur_type = getattr(officer, "jurisdiction_type", "block")
        _jur_name = getattr(officer, "jurisdiction_name", "your jurisdiction")

        # Hierarchy levels (higher index = broader access needed)
        _JUR_LEVELS = ["block", "ward", "town", "taluk", "district"]
        _officer_level = _JUR_LEVELS.index(_jur_type) if _jur_type in _JUR_LEVELS else 0

        # Map each intent to the MINIMUM level the officer needs
        _INTENT_MIN_LEVEL = {
            "ward_surveys":            1,   # needs ward
            "block_surveys":           0,   # block is fine
            "jurisdiction_summary":    0,   # always OK (shows own level)
            "active_applications_taluks": 2, # needs town+
            "taluk_summary":           3,   # needs taluk
            "all_surveys_in_jurisdiction": 0, # always OK
        }

        # Also check message keywords for ward/taluk/district references
        _msg_lower_jur = message.lower()
        _requested_broader = False
        _broader_reason = ""

        # These intents use "ward"/"taluk" as geographic context within the officer's
        # own data — they are NOT requests for broader jurisdiction data.
        _FIELD_VISIT_INTENTS = {
            "fv_scheduled_this_week", "fv_date_select", "fv_nearby_pending",
            "fv_reschedule_availability", "fv_change_date", "fv_deadline_check", "fv_overdue_inspections",
            "fv_unassigned_awaiting", "fv_recently_rescheduled", "fv_scheduling_conflicts",
            "sd_additional_info", "sd_encroachment_check", "sd_sketch_readiness",
            "sd_forward_check", "sd_remarks", "application_status", "isd_processing",
            "officer_workload", "field_visits", "general_query", "rag", "greeting",
            "help", "district_code"
        }
        _is_code_reference_query = any(w in _msg_lower_jur for w in ["code", "கோடு", "service code", "district code"])
        # "Which taluk do I belong to?" names a broader level but asks only for
        # the officer's OWN posting — the answer is the hierarchy they sit in,
        # not other officers' data. These intents describe the officer, so the
        # keyword check must not turn them into an access denial.
        # "active applications by taluk" groups the officer's OWN jurisdiction-
        # scoped rows by taluk NAME as an output column -- the same "taluk" word
        # the broader-access check below reads as a request to see another
        # taluk's data. The query itself (see get_jurisdiction_summary) never
        # leaves the officer's own jurisdiction filter, so it belongs with the
        # other self-describing intents, not behind the access denial.
        _SELF_JUR_INTENTS = {"jurisdiction_summary", "officer_directory", "active_applications_taluks"}
        # "show applications along district" / "their taluk" -- a request to
        # ADD district/taluk/ward as a COLUMN on the officer's own already-
        # authorized rows, not a request to see another jurisdiction's data.
        # Read by keyword alone this refused with "you cannot retrieve
        # district-level data" for a ward officer asking to see their own
        # district's NAME printed in the table they already had on screen.
        # "list applications in ward 999" names a ward; "ward" is also a column
        # word, so it read as a projection and skipped the jurisdiction check.
        _is_column_projection_request = fctx.field_projections(message) is not None and not re.search(r"\b(ward|block)s?\s*(no\.?|number)?\s*[:#-]?\s*\d+", message.lower()) and not re.search(r"\b(?:in|of|at|under|within|for)\s+(?:my|the|this|our)\s+(?:taluk|town|district|ward|block)\b", message.lower())
        # A service code's own official name can carry a geography word --
        # "Block/Revoke TOWN Survey Number" (0163), "ULC TALUK ..." -- and
        # "show <that name> applications" is always scoped to the officer's
        # own jurisdiction (same as any other service-code listing), never a
        # request to see another town's or taluk's data. Read by keyword alone
        # this refused "block/revoke town survey number applications" with
        # "you cannot retrieve town-level data" for a ward officer asking
        # about their OWN 0163 files.
        from backend.utils.helpers import find_service_codes as _jur_find_codes
        _names_service_code = bool(_jur_find_codes(message))
        _skip_keyword_check = (intent in _FIELD_VISIT_INTENTS) or (intent in _SELF_JUR_INTENTS) \
            or _is_code_reference_query or intent == "service_code_lookup" \
            or _is_column_projection_request or _names_service_code \
            or bool(_oldowner_qa.attributes(message)) or _cm_strong(message)
        # The field-visit/self-jurisdiction exemption above is for the
        # "requested a BROADER level" check just below -- a ward officer
        # saying "ward" while asking about their OWN overdue visits is not
        # asking to see a wider jurisdiction. It must not also exempt the
        # FOREIGN-ward check further down: "Show my overdue field visits in
        # Ward 12" named a ward this officer does not hold at all, which is
        # unauthorized access whatever the intent is. Without this narrower
        # flag, `fv_overdue_inspections` (and every other field-visit /
        # self-jurisdiction intent) silently ignored a named ward/block
        # entirely instead of either scoping to it or refusing it by name.
        _skip_foreign_check = (
            _is_code_reference_query or intent == "service_code_lookup"
            or _is_column_projection_request or _names_service_code
            or bool(_oldowner_qa.attributes(message)) or _cm_strong(message)
        )

        if _officer_level == 0 and not _skip_keyword_check:  # block officer
            if any(w in _msg_lower_jur for w in ["ward", "வார்டு"]):
                _requested_broader = True
                _broader_reason = "ward-level"
            elif any(w in _msg_lower_jur for w in ["taluk", "தாலுகா"]):
                _requested_broader = True
                _broader_reason = "taluk-level"
            elif any(w in _msg_lower_jur for w in ["district", "மாவட்டம்"]):
                _requested_broader = True
                _broader_reason = "district-level"
        elif _officer_level == 1 and not _skip_keyword_check:  # ward officer
            if any(w in _msg_lower_jur for w in ["town", "நகரம்"]):
                _requested_broader = True
                _broader_reason = "town-level"
            elif any(w in _msg_lower_jur for w in ["taluk", "தாலுகா"]):
                _requested_broader = True
                _broader_reason = "taluk-level"
            elif any(w in _msg_lower_jur for w in ["district", "மாவட்டம்"]):
                _requested_broader = True
                _broader_reason = "district-level"

        # Also check intent minimum level
        if intent in _INTENT_MIN_LEVEL and _officer_level < _INTENT_MIN_LEVEL[intent]:
            _requested_broader = True
            _required_level = _JUR_LEVELS[_INTENT_MIN_LEVEL[intent]]
            _broader_reason = f"{_required_level}-level"

        # A ward/block named in the message that the officer does not hold is
        # another officer's data, whatever the intent asks for.
        if not _skip_foreign_check:
            _named_ward, _named_block = await _geo_scope(
                db, session_id, message, chat_history, officer=None)
            _foreign_msg = await _foreign_geo_denial(
                db, officer, _named_ward, _named_block, language)
            if _foreign_msg:
                logger.info(
                    f"Jurisdiction access denied for officer {officer.officer_id}: "
                    f"named ward={_named_ward} block={_named_block}, intent={intent}"
                )
                await save_chat_messages(
                    db=db, session_id=session_id,
                    user_message=_original_message, assistant_message=_foreign_msg,
                    language=language, response_time_ms=int((time.time() - start_time) * 1000),
                    officer_id=officer.officer_id if officer else None
                )
                return {
                    "response": _foreign_msg,
                    "language": language,
                    "intent": intent,
                    "sources": [],
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "context_used": False,
                    "response_time_ms": int((time.time() - start_time) * 1000),
                    "table_data": None
                }

        if _requested_broader:
            _jur_level_name = _JUR_LEVELS[_officer_level]
            response_text = (
                f"You are assigned as a **{_jur_level_name.capitalize()}-level** SIS officer "
                f"({_jur_name}). Your access is limited to data within your assigned {_jur_level_name}.\n\n"
                f"You cannot retrieve {_broader_reason} data. "
                f"Only officers with {_broader_reason.replace('-level', '')} or higher access can view that information.\n\n"
                f"If you need {_broader_reason} data, please contact your supervising officer."
            )
            logger.info(
                f"Jurisdiction access denied for officer {officer.officer_id} "
                f"(level={_jur_level_name}): requested {_broader_reason} data, intent={intent}"
            )
            # Save and return immediately — skip all DB queries
            await save_chat_messages(
                db=db, session_id=session_id,
                user_message=_original_message, assistant_message=response_text,
                language=language, response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None
            )
            return {
                "response": response_text,
                "language": language,
                "intent": intent,
                "sources": [],
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "context_used": False,
                "response_time_ms": int((time.time() - start_time) * 1000),
                "table_data": None
            }

        # Steps 2b-bis .. 2b-quater: the deterministic guard chain (contentless
        # message, mutation request, bare language instruction, same-message
        # filter contradiction, unsupported service code, unanswerable message,
        # capability / out-of-scope). The decision is shared with the streaming
        # pipeline -- see chat_pipeline.early_guard_reply(); delivery (save,
        # then return) stays here.
        _early, _ssk = early_guard_reply(
            message, language, chat_history, _RAW_MESSAGE.get(),
            bool(locals().get("_followup_ctx")))
        if _early:
            if _early.clear_session:
                await _mark_session_cleared(db, session_id, officer)
            if _early.log:
                logger.info(_early.log, *_early.log_args)
            # A clear is about to wipe this transcript and open a new session,
            # so writing the turn to it would only leave a "clear" / "cleared"
            # pair in a conversation nobody will read again.
            if _early.save:
                await save_chat_messages(
                    db=db, session_id=session_id,
                    user_message=_original_message, assistant_message=_early.text,
                    language=language,
                    response_time_ms=int((time.time() - start_time) * 1000),
                    officer_id=officer.officer_id if officer else None
                )
            return {
                "response": _early.text,
                "language": language,
                "intent": _early.intent,
                "action": _early.action,
                "sources": [],
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "context_used": False,
                "response_time_ms": int((time.time() - start_time) * 1000),
                "table_data": None
            }

        # Praise / complaint calls the LLM directly, with a small self-
        # contained prompt -- NOT the general_query agent, which was tried
        # first and reproduced the original bug: routed there, the agent's
        # RAG retrieval matched "performance" against the escalation passage
        # in workflow_guide.txt and confidently explained Level 1 escalation
        # rules, and "good job" (no document match at all) got the agent's
        # generic no-evidence fallback ("I'm not sure what you mean"). Since
        # a review of the assistant is not a question the register can answer,
        # no document retrieval or tool call is relevant to it -- this calls
        # the model with only the officer's own message and an instruction
        # to acknowledge it and invite the next real question, so it can
        # genuinely generate the reply without anything to hallucinate from.
        if _ssk in ("praise", "complaint"):
            _sent_prompt = (
                "You are the SIS assistant for the Tamil Nadu Survey Department. "
                f"An officer just sent this feedback about you, not a question about "
                f"the register: \"{message}\"\n"
                "Reply in 1-2 short sentences: acknowledge what they said, and invite "
                "them to ask a real question about an application, survey number, "
                "field visit or workflow. Do not mention escalation, performance "
                "reviews, or any SIS procedure -- nothing in the register is relevant "
                "to this message. "
                + ("Reply in Tamil." if language in ("ta", "tanglish") else "Reply in English.")
            )
            response_text = await call_llama(_sent_prompt)
            logger.info("Answered %s feedback with a direct LLM reply (no RAG/tools)", _ssk)
            await save_chat_messages(
                db=db, session_id=session_id,
                user_message=_original_message, assistant_message=response_text,
                language=language,
                response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None
            )
            return {
                "response": response_text,
                "language": language,
                "intent": "sentiment",
                "action": None,
                "sources": [],
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "context_used": False,
                "response_time_ms": int((time.time() - start_time) * 1000),
                "table_data": None
            }

        # Step 2b-quinquies: the message reads as a channel request but names
        # a near-miss spelling of CSC/SRO, not either one cleanly. Answered
        # here so a garbled "seo"/"scs" never reaches the agent/LLM layer,
        # which has nothing to look a channel up from and either hallucinates
        # one or hangs for the full request timeout with no answer at all.
        if ambiguous_channel_clarification(message) and not _cm_strong(message, chat_history):
            response_text = _channel_clarify_reply(language)
            logger.info("Asked which channel was meant instead of guessing or falling to the LLM")
            # A barrier, not a null: leaving structured_context empty let the
            # NEXT turn's follow-up resolution walk back past this one to
            # whatever real listing preceded it in the conversation, so a
            # bare "Sub-Registrar" reply to "which channel did you mean?" was
            # read as a singular follow-up against a stale 24-row list from
            # several turns earlier ("which one do you mean?") instead of
            # being treated as the fresh, self-contained channel request it
            # is. An explicit empty-applications record makes `resolve()`
            # stand aside on the next turn instead of reading through it.
            _channel_clarify_ctx = fctx.build_context(
                "channel_clarification", {"applications": []})
            await save_chat_messages(
                db=db, session_id=session_id,
                user_message=_original_message, assistant_message=response_text,
                language=language,
                response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None,
                structured_context=(_channel_clarify_ctx.to_json()
                                     if _channel_clarify_ctx else None)
            )
            return {
                "response": response_text,
                "language": language,
                "intent": "channel_clarification",
                "action": None,
                "sources": [],
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "context_used": False,
                "response_time_ms": int((time.time() - start_time) * 1000),
                "table_data": None
            }

        # Step 2b-sexies: "display application without CAN number" -- every
        # application in the register carries one (verified: 0 of 209 are
        # NULL/empty), so this always has the same answer. Unrecognised, the
        # column-projection machinery read "CAN number" as a requested
        # COLUMN and silently dropped "without", returning a table of
        # applications that all HAD a CAN number -- the opposite of the
        # question.
        if _asks_missing_can_number(message):
            response_text = _missing_can_number_answer(language)
            logger.info("Answered 'applications without a CAN number' -- always none, by design")
            await save_chat_messages(
                db=db, session_id=session_id,
                user_message=_original_message, assistant_message=response_text,
                language=language,
                response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None
            )
            return {
                "response": response_text,
                "language": language,
                "intent": "missing_can_number",
                "action": None,
                "sources": [],
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "context_used": False,
                "response_time_ms": int((time.time() - start_time) * 1000),
                "table_data": None
            }

        # Step 2c: Direct greeting response handling
        if intent == "greeting":
            msg_lower = message.lower().strip()
            # `language in ("ta", "tanglish")`, not just "ta" -- the convention
            # every other Tamil/English branch in this file follows (see
            # _is_tamil_turn). A bare `== "ta"` check missed every romanized
            # greeting with no Tamil-script word in it ("vanakkam", "nandri"),
            # so a Tanglish "vanakkam" got answered in English.
            is_tamil = language in ("ta", "tanglish") or any(w in message for w in ["வணக்கம்", "காலை", "மாலை", "நன்றி", "ஹாய்"])

            if "காலை" in msg_lower or "morning" in msg_lower:
                if is_tamil:
                    response_text = "காலை வணக்கம்! 👋 நான் உங்கள் Sub Inspector Surveyor (SIS) AI உதவியாளர். நில அளவை எண்கள், விண்ணப்பங்கள் (ISD/NISD), கள ஆய்வுகள் தொடர்பாக இன்று உங்களுக்கு எவ்வாறு உதவ முடியும்?"
                else:
                    response_text = "Good morning! 👋 I am your Sub Inspector Surveyor (SIS) AI assistant. How can I help you today with survey numbers, applications (ISD/NISD), or field visits?"
            elif "மாலை" in msg_lower or "evening" in msg_lower:
                if is_tamil:
                    response_text = "மாலை வணக்கம்! 👋 நான் உங்கள் Sub Inspector Surveyor (SIS) AI உதவியாளர். நில அளவை மற்றும் விண்ணப்பங்கள் தொடர்பான தகவல்களுக்கு இன்று உங்களுக்கு எவ்வாறு உதவ முடியும்?"
                else:
                    response_text = "Good evening! 👋 I am your Sub Inspector Surveyor (SIS) AI assistant. How can I help you with your survey work today?"
            elif _greeting_is_farewell(msg_lower):
                # A sign-off answered with "Hello! ... What can I assist you
                # with today?" reads as though nothing was heard. `greeting`
                # carries both halves of the conversation, so the handler has
                # to tell them apart even though the intent does not.
                if is_tamil:
                    response_text = "நன்றி! 👋 வணக்கம். மீண்டும் ஏதேனும் தேவைப்பட்டால் கேளுங்கள் — நல்ல நாளாக அமையட்டும்."
                else:
                    response_text = "Goodbye! 👋 Thank you — come back any time you need an application, a survey number or a field visit looked up. Have a good day."
            elif _greeting_is_thanks(msg_lower):
                if is_tamil:
                    response_text = "நல்வரவு! 😊 உங்களுக்கு மேலும் ஏதேனும் உதவி தேவைப்பட்டால் தயங்காமல் கேளுங்கள்."
                else:
                    response_text = "You're very welcome! 😊 Feel free to ask if you need anything else regarding your survey work."
            else:
                if is_tamil:
                    response_text = "வணக்கம்! 👋 நான் உங்கள் Sub Inspector Surveyor (SIS) AI உதவியாளர். நில அளவை எண்கள், விண்ணப்பங்களின் நிலை, கள ஆய்வுகள் மற்றும் பட்டா பரிமாற்றங்கள் பற்றிய கேள்விகளுக்கு உதவ தயாராக உள்ளேன். இன்று உங்களுக்கு என்ன உதவி தேவை?"
                else:
                    response_text = "Hello! 👋 I am your Sub Inspector Surveyor (SIS) AI assistant. I am here to help you manage survey applications, check document statuses, track field visits, and navigate workflow procedures. What can I assist you with today?"

            _gen = await _llm_greeting(
                message, _greeting_kind(msg_lower, _sem_kind), language in ("ta", "tanglish"))
            if _gen:
                response_text = _gen

            await save_chat_messages(
                db=db, session_id=session_id,
                user_message=_original_message, assistant_message=response_text,
                language=language, response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None
            )
            return {
                "response": response_text,
                "language": language,
                "intent": "greeting",
                "sources": [],
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "context_used": False,
                "response_time_ms": int((time.time() - start_time) * 1000),
                "table_data": None
            }

        # Step 2c-bis: a pointed question about a geography-master housekeeping
        # column the ORM projection drops (town flag / status, IGRS mutation,
        # NIC digital-signature flag, ward freeze, the masters' own
        # user/timestamp columns). Answered here, before the application-number
        # gate turns a town question into "please give an application number"
        # and before `jurisdiction_summary` collapses it to "Your town is
        # <name>." -- both confident non-answers. See `_UNTRACKED_GEO_MASTER_FIELDS`.
        _ugm_key = _asked_untracked_geo_master_field(message.lower())
        if _ugm_key:
            response_text = _untracked_geo_master_field_answer(
                _ugm_key, language in ("ta", "tanglish"))
            logger.info(f"Answered 'not in register' for geo-master field {_ugm_key}")
            await save_chat_messages(
                db=db, session_id=session_id,
                user_message=_original_message, assistant_message=response_text,
                language=language, response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None
            )
            return {
                "response": response_text,
                "language": language,
                "intent": "untracked_field",
                "sources": [],
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "context_used": False,
                "response_time_ms": int((time.time() - start_time) * 1000),
                "table_data": None
            }
        # IP address compared/matched ACROSS applications, naming no single
        # one -- see _asked_ip_across_applications: the listing intent this
        # otherwise falls to has no IP column at all and silently substitutes
        # the registration address, then returns a real filtered application
        # list as though an IP comparison had actually been run.
        if _asked_ip_compare(message):
            response_text = await _ip_cross_comparison_answer(
                db, officer, message, _followup_ctx, language in ("ta", "tanglish"))
            logger.info("Answered a cross-application IP comparison")
            await save_chat_messages(
                db=db, session_id=session_id,
                user_message=_original_message, assistant_message=response_text,
                language=language, response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None
            )
            return {
                "response": response_text,
                "language": language,
                "intent": "untracked_field",
                "sources": [],
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "context_used": False,
                "response_time_ms": int((time.time() - start_time) * 1000),
                "table_data": None
            }

        # ── Step 2d: Application reference validation ──────────────────────
        # Format, existence and jurisdiction are all settled here, before any
        # handler, RAG lookup or LLM call runs. An application number that is
        # malformed, nonexistent, out of jurisdiction or merely a near match
        # must never be treated as valid context downstream.
        _app_resolution = await resolve_application_reference(
            db, message, chat_history, officer, intent, session_id=session_id
        )
        if _app_resolution["verdict"] == "ask_number":
            # Dead end for the DB pipeline -- but if the officer has been
            # talking about an uploaded file all session (no number ever
            # given, one document open), that is who "it"/"approved"/"the
            # extent" refers to, not a nonexistent application. Retried here
            # rather than earlier because only now do we know the SIS intent
            # truly has nothing to resolve against.
            _att_fallback = await attachment_qa.try_single_document_fallback(
                db=db, officer=officer, session_id=session_id,
                message=message, language=language)
            if _att_fallback is not None:
                res_txt = (await call_llama(_att_fallback.prompt)
                          if _att_fallback.prompt else _att_fallback.text)
                logger.info("Application gate deferred to the one open attachment")
                _att_ctx = ({"entity": "attachment", "document_ids": _att_fallback.document_ids,
                            **(_att_fallback.extra or {})}
                           if len(_att_fallback.document_ids or []) == 1 else None)
                await save_chat_messages(
                    db=db, session_id=session_id, user_message=_original_message,
                    assistant_message=res_txt, language=language,
                    response_time_ms=int((time.time() - start_time) * 1000),
                    officer_id=officer.officer_id if officer else None,
                    structured_context=_att_ctx
                )
                return {
                    "response": res_txt,
                    "language": language,
                    "intent": _att_fallback.intent,
                    "sources": _att_fallback.sources,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "context_used": True,
                    "response_time_ms": int((time.time() - start_time) * 1000),
                    "table_data": None,
                    "attachment_kind": _att_fallback.kind,
                    "citations": _att_fallback.citations,
                    "document_ids": _att_fallback.document_ids,
                }
        if _app_resolution["verdict"] not in ("ok", "none"):
            response_text = build_application_gate_message(_app_resolution, officer, language)
            logger.info(
                f"Application gate refused request: verdict={_app_resolution['verdict']} "
                f"number={_app_resolution.get('app_number') or _app_resolution.get('token')} "
                f"intent={intent} officer={officer.officer_id if officer else None}"
            )
            _gate_ctx = (fctx.build_context("application_status", {}, fallback_app_number=_app_resolution.get("app_number"))
                         if _app_resolution["verdict"] == "register_only" else None)
            await save_chat_messages(
                db=db, session_id=session_id,
                user_message=_original_message, assistant_message=response_text,
                language=language, response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None,
                structured_context=_gate_ctx.to_json() if _gate_ctx else None
            )
            return {
                "response": response_text,
                "language": language,
                "intent": intent,
                "sources": [],
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "context_used": False,
                "response_time_ms": int((time.time() - start_time) * 1000),
                "table_data": None
            }

        # The one application number already proven valid and inside the
        # officer's jurisdiction. Handlers fall back to it so a follow-up reuses
        # exactly what the gate confirmed, never something re-derived.
        _gate_app_number = (
            _app_resolution.get("app_number") if _app_resolution["verdict"] == "ok" else None
        )

        # Step 3: Execute structured database queries based on intent
        structured_data = {}
        response_text = ""
        _isd_app_no = ""
        # (app_number, "new"|"previous") when this turn answered about a patta
        # transfer party -- recorded as the follow-up context so "what is their
        # gender?" next turn stays on that party.
        _tp_marker = None
        # (app_number, "place"|"date"|"reason"|"docnum") when this turn answered
        # a registration-detail question, so a bare "when?" / "where?" next turn
        # stays on the deed rather than the filing date.
        _reg_marker = None

        # "Show the survey number and subdivision number of the second
        # overdue visit." asked as the very FIRST field-visit question --
        # resolve the ordinal against the plan before anything else runs, so
        # the rest of this turn sees a question that names its own
        # application, same as a genuine second-turn follow-up would.
        _vp_resolved_message = await _maybe_resolve_fresh_visit_ordinal(
            db, officer, message, intent, chat_history, session_id, language)
        if _vp_resolved_message != message:
            message = _vp_resolved_message
            intent = parse_intent(message, prev_intent=intent)

        # A sub-division plot field (tax rate, plot, soil, group numbers, land
        # use, area, new sub-division / patta number, owners) -- read from the
        # plot rows, ahead of the guards below that call those fields absent.
        _hook_done = False
        if not response_text:
            response_text = await _early_subdivision_answer(
                db, officer, message, intent, _gate_app_number, chat_history, language) or ""
            if response_text and intent != "application_status":
                intent = "application_status"
        _hook_done = bool(response_text)
        if _hook_done and _HOOK_APP.get():
            app_number = _HOOK_APP.get()  # recorded as the follow-up context of this answer

        # An owner-record field the SIS register does not carry (ration card,
        # voter ID, PIN, occupation / relation code, ...). Answer it here,
        # naming the field, before the `survey_owners` branch dumps every owner
        # on the parcel or the fuzzy field map guesses. Needs an application /
        # survey reference for the question to be about a record at all.
        _uof_key = _asked_untracked_owner_field(message.lower())
        _uoa_lbl = None if _uof_key else _asked_unknown_owner_attr(message.lower())
        _uof_app = (extract_application_number(message) or _gate_app_number
                    or _extract_app_number_from_context(
                        message, chat_history, allow_implicit_continuation=True))
        if _uof_key and _uof_app and not response_text:
            response_text = _untracked_owner_field_answer(
                _uof_app, _uof_key, language in ("ta", "tanglish"))
            logger.info(f"Responded 'not in register' for owner field {_uof_key}")
        elif _uoa_lbl and _uof_app and not response_text:
            response_text = _unknown_owner_attr_answer(
                _uof_app, _uoa_lbl, language in ("ta", "tanglish"))
            logger.info(f"Responded 'not a register field' for owner attr {_uoa_lbl!r}")

        # A `uareg` column the survey projection drops (soil,
        # irrigation, the tax variants, double-crop / partition / priority
        # flags, old survey number, Form 6/7/8, door / street code, ...). Name
        # it as not held, before `survey_detail` answers with the generic parcel
        # card that omits it. `_UNTRACKED_SOURCE_FIELDS` is checked first when an
        # application number is in view -- it is the narrower, more specific
        # list for `appl_log_urban` columns and must win where the two
        # overlap (soil type, tax rate, tax/hectare, land use). Only the fields
        # neither list shares (municipal tax, total tax, old survey/subdivision
        # number, Form 6/7/8, the assignment/relinquishment/alienation/
        # acquisition numbers, the assessed/cultivable/double-crop/partition/
        # government-priority flags, ...) fall through to here even with an
        # application number present -- without this, "what is the municipal
        # tax for 2022/0154/28/000156?" answered a completely generic "could
        # not find that particular detail" instead of naming the field and
        # where it actually lives.
        # "IGRS form 6 number" is excluded outright: the parcel register's OWN
        # (untracked) form6_number cue is a bare substring of it, so an
        # application-scoped question about the IGRS number -- a real,
        # TRACKED column (applications.igrs_form6_number) -- was being
        # answered "not held" instead of by the handler that already carries
        # it correctly.
        if not response_text and "igrs" not in message.lower():
            _upf_key = _asked_untracked_parcel_field(message.lower())
            if _upf_key and (not _uof_app or not _asked_untracked_source_field(message.lower())):
                if _uof_app:
                    _upf_ref = await _survey_number_for_application(db, officer, _uof_app)
                else:
                    _upf_ref = (extract_survey_number(message)
                                or await _survey_ref(db, session_id, message, chat_history))
                if _upf_ref:
                    response_text = _untracked_parcel_field_answer(
                        _upf_ref, _upf_key, language in ("ta", "tanglish"))
                    logger.info(
                        f"Responded 'not in register' for parcel field {_upf_key}")

        if not response_text and not extract_application_number(message):
            _sattr_ref = extract_survey_number(message)
            if _sattr_ref:
                _abs_label = _asked_absent_parcel_field(message.lower())
                if _abs_label:
                    response_text = _absent_parcel_field_answer(
                        _sattr_ref, _abs_label, language in ("ta", "tanglish"))
                else:
                    response_text = await _survey_attribute_answer(
                        db, officer, message, _sattr_ref, language in ("ta", "tanglish"))

        # A parcel / owner digital-signature column (document hash, PKCS#7
        # content, signed / verified datetime, NIC dsign flag, Form 8 number).
        # None is projected into layer 2, so name it as not held before
        # `check_documents` answers "all documents present", the decision-date
        # handler answers with the approval date, or the field map guesses.
        if not response_text:
            _usig_key = _asked_untracked_signature_field(message.lower())
            if _usig_key:
                _usig_app = (extract_application_number(message) or _gate_app_number
                             or _extract_app_number_from_context(
                                 message, chat_history, allow_implicit_continuation=True))
                _usig_surv = None if _usig_app else (
                    extract_survey_number(message)
                    or await _survey_ref(db, session_id, message, chat_history))
                _usig_ref = _usig_app or (f"survey {_usig_surv}" if _usig_surv else None)
                if _usig_ref:
                    response_text = _untracked_signature_field_answer(
                        _usig_ref, _usig_key, language in ("ta", "tanglish"))
                    logger.info(
                        f"Responded 'not in register' for signature field {_usig_key}")

        # A patta-transfer / registration-detail column the projection carries
        # ("where/when was the deed registered", transfer reason) -> answered
        # from patta_transfers; or one it drops (succession cert, court order,
        # transferred extent, door / tax, land category) -> named as not held.
        # Both run before the field map sends "registered" to sale_deed_check
        # or "when" to the submission date.
        if not response_text:
            _trd_app = _uof_app or _gate_app_number or extract_application_number(message)
            _rdet = _asked_registration_detail(message.lower())
            _utf_key = _asked_untracked_transfer_field(message.lower())
            if _rdet and _trd_app:
                _rd_detail = await get_application_detail(db, _trd_app, officer=officer)
                if _rd_detail.get("found"):
                    response_text = _registration_detail_answer(
                        _rd_detail, _rdet, _trd_app, language in ("ta", "tanglish"), message)
                    _reg_marker = (_rd_detail.get("application_id") or _trd_app, _rdet)
                    logger.info(f"Answered registration detail '{_rdet}' for {_trd_app}")
            if not response_text and _utf_key and _trd_app:
                response_text = _untracked_transfer_field_answer(
                    _trd_app, _utf_key, language in ("ta", "tanglish"))
                logger.info(f"Responded 'not in register' for transfer field {_utf_key}")

        # "who is the new / previous owner of <app>", and pointed fields on them
        # ("the previous owner's relative name", "the new owner's aadhaar").
        # These name a patta-transfer party, not the applicant -- so answer from
        # the transfer parties before the field map sends "aadhaar" to the
        # applicant's CAN or "name" to the applicant. Needs a real application
        # number for the question to be about a file at all.
        if not response_text:
            _tp_side0 = _asks_transfer_party(message)
            _tp_app0 = _gate_app_number or extract_application_number(message)
            if _tp_side0 and _tp_app0:
                _tp_detail = await get_application_detail(db, _tp_app0, officer=officer)
                if _tp_detail.get("found"):
                    response_text = _transfer_party_answer(
                        _tp_detail, _tp_side0, message,
                        language in ("ta", "tanglish"))
                    _tp_marker = (_tp_detail.get("application_id") or _tp_app0, _tp_side0)
                    logger.info(f"Answered transfer {_tp_side0}-owner for {_tp_app0}")

        # Stage: intent dispatch -- see chat_dispatch.dispatch_intent().
        _stage = await dispatch_intent(
            _followup_ctx=_followup_ctx, _gate_app_number=_gate_app_number,
            _hook_done=_hook_done, _original_message=_original_message,
            chat_history=chat_history, db=db, intent=intent, language=language,
            message=message, officer=officer, response_text=response_text,
            session_id=session_id, start_time=start_time,
            structured_data=structured_data,
            **_bound(locals(), ('app_number', 'i', 'is_tamil', 'msg_lower', 'res_txt')))
        if _stage.early:
            return _stage.response
        # Rebind exactly the locals the stage assigned (an unassigned one keeps its state).
        if '_an' in _stage.bound: _an = _stage.bound['_an']
        if '_app_no' in _stage.bound: _app_no = _stage.bound['_app_no']
        if '_field_keywords' in _stage.bound: _field_keywords = _stage.bound['_field_keywords']
        if '_isd_app_no' in _stage.bound: _isd_app_no = _stage.bound['_isd_app_no']
        if '_missing' in _stage.bound: _missing = _stage.bound['_missing']
        if '_tp_marker' in _stage.bound: _tp_marker = _stage.bound['_tp_marker']
        if 'app_type' in _stage.bound: app_type = _stage.bound['app_type']
        if 'apps' in _stage.bound: apps = _stage.bound['apps']
        if 'completed' in _stage.bound: completed = _stage.bound['completed']
        if 'days' in _stage.bound: days = _stage.bound['days']
        if 'e' in _stage.bound: e = _stage.bound['e']
        if 'is_tamil' in _stage.bound: is_tamil = _stage.bound['is_tamil']
        if 'overlap_date' in _stage.bound: overlap_date = _stage.bound['overlap_date']
        if 'response_text' in _stage.bound: response_text = _stage.bound['response_text']
        if 'structured_data' in _stage.bound: structured_data = _stage.bound['structured_data']
        if 'sub_date' in _stage.bound: sub_date = _stage.bound['sub_date']
        if 'survey_no' in _stage.bound: survey_no = _stage.bound['survey_no']
        if 'taluk' in _stage.bound: taluk = _stage.bound['taluk']
        if 'today' in _stage.bound: today = _stage.bound['today']
        if 'total' in _stage.bound: total = _stage.bound['total']
        if 'working_days' in _stage.bound: working_days = _stage.bound['working_days']
        
        # Step 4: Get RAG context from pgvector — skip if DB data was actually found
        # (avoids FAQ docs contaminating DB answers)
        # A single-record lookup (one application, one survey, one owner set) carries
        # no "count" key, so requiring count > 0 classified every such answer as
        # "no database results" and pulled FAQ chunks into the prompt alongside
        # authoritative values. Count is honoured when present; otherwise any real
        # payload beyond the bookkeeping keys counts as a database result.
        # Stage: answer building -- see chat_answer.build_answer().
        _stage = await build_answer(
            _gate_app_number=_gate_app_number, chat_history=chat_history, db=db,
            intent=intent, language=language, message=message, officer=officer,
            response_text=response_text, structured_data=structured_data,
            **_bound(locals(), ('_field_keywords', '_utf_key', 'is_tamil', 'today')))
        # Rebind exactly the locals the stage assigned (an unassigned one keeps its state).
        if '_bypass_html' in _stage.bound: _bypass_html = _stage.bound['_bypass_html']
        if 'app_type' in _stage.bound: app_type = _stage.bound['app_type']
        if 'context_used' in _stage.bound: context_used = _stage.bound['context_used']
        if 'full_prompt' in _stage.bound: full_prompt = _stage.bound['full_prompt']
        if 'html_response' in _stage.bound: html_response = _stage.bound['html_response']
        if 'is_tamil' in _stage.bound: is_tamil = _stage.bound['is_tamil']
        if 'response_text' in _stage.bound: response_text = _stage.bound['response_text']
        if 'structured_data' in _stage.bound: structured_data = _stage.bound['structured_data']
        if 'survey_no' in _stage.bound: survey_no = _stage.bound['survey_no']

        if html_response or response_text:
            pass  # already set above
        elif intent == "active_applications_taluks":
            total = structured_data.get("total_active", 0)
            counts = structured_data.get("taluk_counts", {})
            if total > 0:
                counts_str = ", ".join(f"{count} in {taluk}" for taluk, count in counts.items())
                response_text = f"{total} active applications: {counts_str}."
            else:
                response_text = "0 active applications."
        elif intent == "highest_priority_applications":
            apps = structured_data.get("applications", [])
            count = len(apps)
            if count > 0:
                app_numbers = [a.get("application_number") for a in apps[:5]]  # Show first 5
                preview = ", ".join(app_numbers)
                if count > 5:
                    preview += f" and {count - 5} more"
                response_text = f"Found {count} high priority application(s): {preview}. Priority is based on overdue status or manual flagging."
            else:
                response_text = "No high priority applications found. All applications are within normal processing timeframes."
        elif intent == "escalation_check":
            approaching = structured_data.get("applications", [])
            total = structured_data.get("total_approaching", 0)
            overdue = structured_data.get("overdue_count", 0)
            if total == 0:
                response_text = "No applications are currently approaching the escalation threshold."
            else:
                critical = [a for a in approaching if "Critical" in a.get("urgency", "")]
                warning = [a for a in approaching if "Warning" in a.get("urgency", "")]
                ov_apps = [a for a in approaching if a.get("is_overdue")]
                parts = []
                if overdue:
                    parts.append(f"{overdue} already overdue")
                if critical:
                    parts.append(f"{len(critical)} critical (1–2 days remaining)")
                if warning:
                    parts.append(f"{len(warning)} warning (3–5 days remaining)")
                summary = ", ".join(parts) if parts else f"{total} total"
                response_text = (
                    f"Found {total} application(s) approaching or past the 15-working-day escalation threshold: {summary}. "
                    f"See the table below for details."
                )
        elif intent == "assigned_today":
            count = structured_data.get("count", 0)
            response_text = f"{count} applications were assigned today."
        elif intent == "immediate_action":
            apps = structured_data.get("apps", [])
            if apps:
                response_text = f"{', '.join(apps)} require immediate action based on pending deadlines."
            else:
                response_text = "No applications require immediate action today."
        elif intent == "awaiting_field_visit":
            count = structured_data.get("count", 0)
            response_text = f"{count} applications are awaiting field inspection."
        elif intent in ("isd_applications", "nisd_applications"):
            count = structured_data.get("count", 0) if structured_data else 0
            qtype = structured_data.get("query_type", "Applications") if structured_data else "Applications"
            if count == 0:
                response_text = f"No {qtype} found in your jurisdiction."
            elif count == 1:
                response_text = f"There is 1 {qtype.rstrip('s')} in your jurisdiction."
            else:
                response_text = f"There are {count} {qtype} in your jurisdiction."
        elif intent == "both_applications":
            total = structured_data.get("count", 0) if structured_data else 0
            qtype = structured_data.get("query_type", "Applications") if structured_data else "Applications"
            if total == 0:
                response_text = f"No {qtype} found in your jurisdiction."
            else:
                response_text = f"Found {total} application(s) — see the table below."
        elif intent == "fee_lookup":
            response_text = _fee_lookup_text(structured_data, language in ("ta", "tanglish"))
        elif intent == "fee_summary":
            fs = (structured_data or {}).get("fee_summary", {})
            scope = fs.get("period_label") or ""
            scope = f" {scope}" if scope else ""
            a_type = f" {fs.get('application_type')}" if fs.get("application_type") else ""
            if not fs.get("total_applications"):
                response_text = (f"No{a_type} applications{scope} in your jurisdiction, "
                                 "so there is no fee to total.")
            else:
                response_text = (
                    f"\u20b9{fs.get('total_fee', 0):,.2f} was collected on{a_type} "
                    f"applications{scope} in your jurisdiction \u2014 "
                    f"{fs.get('with_fee', 0)} of {fs.get('total_applications', 0)} "
                    f"application(s) carry a fee record, {fs.get('without_fee', 0)} carry none."
                    + (f" Fees are recorded for ISD/NISD files only; "
                       f"{fs.get('other_service_code_applications')} application(s) under other "
                       f"service codes have no fee record and are not included."
                       if fs.get('other_service_code_applications') else "")
                )
        elif intent == "workload_by_type":
            isd = structured_data.get("ISD", 0)
            nisd = structured_data.get("NISD", 0)
            response_text = f"ISD – {isd} applications, NISD – {nisd} applications."
        elif intent == "applications_by_block":
            response_text = _render_applications_by_block(structured_data, language)
        elif intent == "officer_directory":
            response_text = _render_ward_directory(structured_data, language)
        elif intent == "completion_rate":
            completed = structured_data.get("completed", 0)
            total = structured_data.get("total", 0)
            rate = structured_data.get("rate", 0)
            scope = structured_data.get("scope", "overall")
            if total == 0:
                response_text = f"No applications found for {scope}."
            else:
                response_text = (
                    f"Your application completion percentage {scope}: "
                    f"{rate}% — {completed} out of {total} applications "
                    f"have been completed (approved or rejected), across every service code."
                )
        elif intent == "pending_longest":
            apps = structured_data.get("apps", [])
            days = structured_data.get("days", 0)
            if apps:
                response_text = f"Application Nos. {', '.join(apps)} have been pending for more than {days} days."
            else:
                response_text = "No pending applications."
        elif intent == "is_nisd_or_isd":
            if not structured_data or not structured_data.get("found", True):
                response_text = structured_data.get("message", "Please specify an application number (e.g., 2026/0154/28/NNNNNN) to check if it is NISD or ISD.") if structured_data else "Please specify an application number (e.g., 2026/0154/28/NNNNNN) to check if it is NISD or ISD."
            else:
                app_type = structured_data.get("type", "ISD")
                survey_no = structured_data.get("survey_no") or "not recorded"
                subdivs = structured_data.get("included_subdivisions", "")
                subdiv_count = len(subdivs.split(",")) if subdivs and subdivs != "None" else None
                if app_type == "ISD":
                    response_text = (f"ISD — application declares sub-division into {subdiv_count} plots under survey no. {survey_no}."
                                     if subdiv_count else f"ISD — a sub-division of survey no. {survey_no}; the plots are not recorded yet.")
                elif app_type == "NISD":
                    response_text = f"NISD — application is for transfer of entire survey/patta without subdivision under survey no. {survey_no}."
        elif intent == "submission_channel_check":
            response_text = _render_submission_channel_answer(structured_data, language, message)
        elif intent == "can_apply_check":
            response_text = _render_can_apply_answer(structured_data, language, message)
        elif intent == "check_documents":
            _is_ta_docs = language in ("ta", "tanglish")
            if not structured_data or not structured_data.get("found", True):
                response_text = "விண்ணப்பம் கிடைக்கவில்லை." if _is_ta_docs else "Application not found."
            else:
                # the attachment extract, not application_documents: that table is synthesised at build
                # time and called every pending file's sale deed missing ("upload before the field visit",
                # even for NISD, which has no field visit)
                _dapp = (structured_data.get("application_number") or extract_application_number(message)
                         or _gate_app_number)
                response_text = (await _othercode_detail.attachments(db, _dapp, _is_ta_docs) if _dapp else
                                 ("எந்த விண்ணப்பம் என்று குறிப்பிடவும்." if _is_ta_docs else "Which application? Give its number."))
        elif intent == "check_sale_deed":
            if not structured_data or not structured_data.get("found", True):
                response_text = "Application not found."
            else:
                deed_no = structured_data.get("sale_deed_number") or "N/A"
                sub_date = structured_data.get("submission_date") or "the submission date (not recorded)"
                if structured_data.get("sale_deed_registered"):
                    response_text = f"Yes, deed no. {deed_no} matches Sub-Registrar's registered index as of {sub_date}."
                else:
                    response_text = "No match found — flag to Sub-Registrar's office before proceeding."
        elif intent == "joint_owner_check":
            if not structured_data or not structured_data.get("found", True):
                response_text = (structured_data or {}).get("message", "Please provide an application number or survey number")
            else:
                response_text = _render_joint_owner_answer(structured_data, language)

        elif intent == "fv_visit_plan":
            response_text = _render_visit_plan_answer(structured_data, language)

        elif intent == "last_application":
            response_text = _render_last_application_answer(structured_data, language)

        elif intent == "application_status" and not _hook_done:
            # Deterministic answer for application lookups — in particular the
            # "you gave me an application number that does not exist" case, which
            # must never be handed to the LLM.
            if not structured_data:
                response_text = build_app_not_found_message({}, language)
            elif structured_data.get("no_listing_to_expand"):
                # "details of all of them" with nothing listed before it.
                response_text = ASK_FOR_APP_NUMBER[language if language in ASK_FOR_APP_NUMBER else "en"]
            elif "multi_applications" in structured_data:
                _details_list = structured_data["multi_applications"]
                _found = [d for d in _details_list if d.get("found", True)]
                _missing = [d for d in _details_list if not d.get("found", True)]
                if not _found:
                    response_text = build_app_not_found_message(_missing[0] if _missing else {}, language)
                elif structured_data.get("detail_focus") == "applicant":
                    # The officer asked about the applicants — the table already
                    # renders name/mobile/address for each application, so we
                    # only emit a short intro line here instead of a verbose
                    # text dump that duplicates the table contents.
                    response_text = _applicant_card_text(
                        _found, language in ("ta", "tanglish"))
                else:
                    _summaries = []
                    for _d in _found:
                        _an = _d.get("application_number", "N/A")
                        _st = (_d.get("status") or "N/A").capitalize()
                        _sg = _d.get("stage", "N/A")
                        _summaries.append(f"{_an} (Status: {_st}, Stage: {_sg})")
                    response_text = f"Here are the details for {len(_found)} application(s): {'; '.join(_summaries)}."
                    _listed_total = structured_data.get("listed_total") or 0
                    if _listed_total > len(_details_list):
                        response_text += (
                            f" That is the first {len(_details_list)} of {_listed_total} —"
                            f" ask for the rest by application number.")
                    if _missing:
                        _missing_nos = ", ".join(d.get("searched_number", "N/A") for d in _missing)
                        response_text += f" Not found: {_missing_nos}."
            elif not structured_data.get("found", True):
                response_text = await _not_found_or_register(db, officer, structured_data, language)
            elif "history" in structured_data:
                response_text = _render_workflow_history(structured_data, language)
            # A role AND a status both named ("has the ZDT approved X?") is
            # attributed from `decision_desk`, tried before either single-axis
            # answer below -- the status alone would credit a role that may
            # never have touched the file.
            elif _status_yes_no_question(message) and _stage_yes_no_question(message):
                response_text = _render_status_role_answer(
                    structured_data, _status_yes_no_question(message),
                    _stage_yes_no_question(message), language)
            elif _status_yes_no_question(message):
                response_text = _render_status_yes_no_answer(
                    structured_data, _status_yes_no_question(message), language)
            elif _stage_yes_no_question(message) and _is_stage_ever_question(message):
                from backend.services.postgres import get_workflow_history
                _asked_stage = _stage_yes_no_question(message)
                _app_no = structured_data.get("application_number", "N/A")
                _hops = await get_workflow_history(db, officer, _app_no)
                response_text = _render_stage_ever_answer(
                    _hops, _app_no, _asked_stage, structured_data.get("stage"), language)
            elif _stage_yes_no_question(message):
                response_text = _render_stage_yes_no_answer(
                    structured_data, _stage_yes_no_question(message), language)
            else:
                app_no_d = structured_data.get("application_number", "N/A")
                app_type_d = structured_data.get("type", "N/A")
                status_d = (structured_data.get("status") or "N/A").capitalize()
                stage_d = structured_data.get("stage", "N/A")
                applicant_d = structured_data.get("applicant_name") or "N/A"
                survey_d = structured_data.get("survey_no", "N/A")
                if language in ("ta", "tanglish"):
                    # This generic single-application summary is the fallback
                    # for any application_status question nothing more specific
                    # matched -- including a plain Tamil yes/no like "X
                    # அங்கீகரிக்கப்பட்டதா?" ("was X approved?"). It was 100%
                    # hardcoded English regardless of `language`, so a Tamil
                    # question landed here and came back as
                    # "Here are the details for X. Type: NISD, Status:
                    # Pending, ..." -- not one word of it Tamil.
                    _status_ta_d = {
                        "pending": "நிலுவையில்", "approved": "அங்கீகரிக்கப்பட்டது",
                        "rejected": "நிராகரிக்கப்பட்டது", "in_progress": "செயல்பாட்டில்",
                        "escalated": "மேல்நிலைப்படுத்தப்பட்டது",
                    }.get((structured_data.get("status") or "").lower(), status_d)
                    _summary_ta = (
                        f"வகை: {app_type_d}, நிலை: {_status_ta_d}, கட்டம்: {stage_d}, "
                        f"விண்ணப்பதாரர்: {applicant_d}, கணக்கெண்: {survey_d}."
                    )
                    if _asks_for_specific_detail(message):
                        response_text = (
                            f"கேட்ட குறிப்பிட்ட தகவலை {app_no_d}-க்கு என்னால் "
                            f"கண்டுபிடிக்க முடியவில்லை. பதிவில் உள்ளது — {_summary_ta}"
                        )
                    else:
                        response_text = f"{app_no_d}-ன் விவரங்கள் — {_summary_ta}"
                elif _asks_for_specific_detail(message):
                    # Nothing in the record matched what was asked. Say that
                    # plainly instead of presenting a summary as if it were the
                    # answer, which reads as though the question was addressed.
                    _summary_d = (
                        f"Type: {app_type_d}, Status: {status_d}, Stage: {stage_d}, "
                        f"Applicant: {applicant_d}, Survey No: {survey_d}."
                    )
                    response_text = (
                        f"I could not find that particular detail for {app_no_d} in the record. "
                        f"Here is what it does hold — {_summary_d}"
                    )
                else:
                    _summary_d = (
                        f"Type: {app_type_d}, Status: {status_d}, Stage: {stage_d}, "
                        f"Applicant: {applicant_d}, Survey No: {survey_d}."
                    )
                    response_text = f"Here are the details for {app_no_d}. {_summary_d}"

        elif intent == "officer_workload":
            response_text = _render_workload_answer(structured_data, language)

        elif intent == "isd_processing":
            response_text = build_isd_processing_answer(message, structured_data, _isd_app_no)

        elif intent == "sd_additional_info":
            if not structured_data or not structured_data.get("found", True):
                response_text = "Application not found."
            else:
                missing = structured_data.get("missing_documents", [])
                clarification = structured_data.get("sd_clarification")
                req_parts = []
                if missing:
                    req_parts.append(f"missing documents ({', '.join(missing)})")
                if clarification:
                    req_parts.append(f"clarification: {clarification}")
                req_str = " and ".join(req_parts) if req_parts else "None"
                response_text = f"SD has requested: {req_str}."
                
        elif intent == "sd_encroachment_check":
            if not structured_data or not structured_data.get("found", True):
                response_text = "Application not found."
            else:
                if structured_data.get("encroachment_found"):
                    response_text = "Yes, flag visible in SD's view of the application file."
                else:
                    response_text = "No encroachment flag has been noted on this application."
                    
        elif intent == "sd_sketch_readiness":
            if not structured_data or not structured_data.get("found", True):
                response_text = "Application not found."
            else:
                missing_fields = []
                if not structured_data.get("field_visit_present"):
                    missing_fields.append("Field Visit Details")
                else:
                    if structured_data.get("area_verified") is None:
                        missing_fields.append("Area Verified")
                    if not structured_data.get("visit_notes_present"):
                        missing_fields.append("Visit Notes")
                if missing_fields:
                    response_text = f"Missing: {', '.join(missing_fields)}. Recommend completing before submission."
                else:
                    response_text = "All required fields are filled."
                    
        elif intent == "sd_forward_check":
            if not structured_data or not structured_data.get("found", True):
                response_text = "Application not found."
            else:
                if structured_data.get("current_stage") == "SIS":
                    response_text = "No. The application is pending SIS verification."
                else:
                    forward_date = structured_data.get("forwarded_to_sd_date") or structured_data.get("submission_date")
                    response_text = f"Yes. Forwarded on {forward_date}."
                    
        elif intent == "sd_remarks":
            if not structured_data or not structured_data.get("found", True):
                response_text = "Application not found."
            else:
                remarks = structured_data.get("sd_remarks")
                if remarks:
                    response_text = f"SD Remarks: {remarks}."
                else:
                    response_text = "No remarks recorded by SD."
                    
        elif intent == "fv_date_select":
            if not structured_data or not structured_data.get("found", True):
                response_text = "Application not found."
            else:
                fv_date = structured_data.get("field_visit_date")
                if fv_date:
                    response_text = f"{fv_date} confirmed for this application."
                else:
                    response_text = "No field visit scheduled for this application."
                    
        elif intent == "fv_nearby_pending":
            if not structured_data or not structured_data.get("found", True):
                response_text = "Application not found."
            else:
                count = structured_data.get("nearby_count", 0)
                ward = structured_data.get("ward_number", "N/A")
                block = structured_data.get("block_number", "N/A")
                response_text = f"{count} applications are located within the same Ward {ward} and Block {block}."
                
        elif intent == "fv_between_dates":
            count = structured_data.get("count", 0)
            to_visit = structured_data.get("to_be_visited_count", count)
            s_date = structured_data.get("start_date")
            e_date = structured_data.get("end_date")
            date_range = f" ({s_date} to {e_date})" if (s_date and e_date) else ""
            if to_visit == 0:
                response_text = f"There are no field visits needed to be visited{date_range}."
            elif to_visit == 1:
                response_text = f"There is 1 field visit needed to be visited{date_range}."
            else:
                response_text = f"There are {to_visit} field visits needed to be visited{date_range}."

        elif intent == "fv_scheduled_this_week":
            count = structured_data.get("taluk_scheduled_count", 0)
            taluk = structured_data.get("taluk_name", "N/A")
            cases = structured_data.get("taluk_cases", [])
            week_start = structured_data.get("week_start", "")
            week_end = structured_data.get("week_end", "")
            cases_str = ", ".join(cases) if cases else "None"
            date_range = f" ({week_start} to {week_end})" if week_start else ""
            if count == 0:
                response_text = f"You have no field visits scheduled in {taluk} this week{date_range}."
            elif count == 1:
                response_text = f"You have 1 field visit scheduled in {taluk} this week{date_range}: {cases_str}."
            else:
                response_text = f"You have {count} field visits scheduled in {taluk} this week{date_range}: {cases_str}."
            
        elif intent == "fv_change_date":
            if language in ("ta", "tanglish"):
                response_text = "கள ஆய்வு தேதியை மாற்றுவதற்கு தாசில்தாரிடம் (Tahsildar) கேட்க வேண்டும். தாசில்தாரின் அனுமதியுடன் மட்டுமே கள ஆய்வு தேதியை மாற்ற இயலும்."
            else:
                response_text = "To change the field visit date, you should ask the Tahsildar. The Tahsildar has the authority to approve field visit date changes."

        elif intent == "fv_reschedule_availability":
            res_date = structured_data.get("reschedule_date", "the next available working day") if structured_data else "the next available working day"
            response_text = f"Schedule available on {res_date}. Note: You should ask the Tahsildar about field visit date change."
            
        elif intent == "fv_deadline_check":
            if not structured_data or not structured_data.get("found", True):
                response_text = structured_data.get("message", "Please specify an application number to check the deadline.") if structured_data else "Please specify an application number."
            else:
                app_no_dl = structured_data.get("application_number", "")
                working_days = structured_data.get("working_days", 0)
                sub_date = structured_data.get("submission_date", "")
                if structured_data.get("is_overdue", False):
                    overdue = structured_data.get("days_overdue", max(0, working_days - 15))
                    response_text = (
                        f"Yes — {app_no_dl} is past the 15-working-day deadline. "
                        f"It has been {working_days} working days since submission ({sub_date}), "
                        f"{overdue} day(s) overdue. Recommend escalating or scheduling immediately."
                    )
                else:
                    remaining = structured_data.get("days_remaining", max(0, 15 - working_days))
                    response_text = (
                        f"No — {app_no_dl} is on working day {working_days} of 15 "
                        f"(submitted {sub_date}). {remaining} working day(s) remaining within the window."
                    )
                    
        elif intent == "fv_overdue_inspections":
            count = structured_data.get("overdue_visits_count", 0) if structured_data else 0
            _awaiting = (structured_data or {}).get("awaiting_scheduling_count", 0)
            if count == 0:
                response_text = "No field visit is past its scheduled date."
                if _awaiting:
                    response_text += (
                        f" {_awaiting} application(s) are still awaiting a visit "
                        f"that has never been booked, so they carry no scheduled "
                        f"date to be late against — ask \"which application "
                        f"should I field visit next?\" to see them.")
                else:
                    response_text += " Every booked visit is on schedule."
            else:
                response_text = f"Found {count} overdue field visit(s). See the table below for details."

        elif intent == "fv_unassigned_awaiting":
            count = structured_data.get("unassigned_visits_count", 0)
            apps_list = structured_data.get("unassigned_applications", [])
            # A bare count answers "how many", never "which" -- an officer
            # asking a follow-up ("which application number is that?") had
            # nothing to resolve against, since the number was never said.
            _nums = ", ".join(a.get("application_number", "") for a in apps_list)
            if language in ("ta", "tanglish"):
                if count == 0:
                    response_text = "திட்டமிடல் காத்திருக்கும் நிறைவேற்றப்படாத கள ஆய்வுகள் எதுவும் இல்லை."
                elif count == 1:
                    response_text = f"திட்டமிடல் காத்திருக்கும் 1 கள ஆய்வு விண்ணப்பம் உள்ளது: {_nums}."
                else:
                    response_text = f"திட்டமிடல் காத்திருக்கும் {count} கள ஆய்வு விண்ணப்பங்கள் உள்ளன: {_nums}."
            else:
                if count == 0:
                    response_text = "There are no unassigned field visits awaiting scheduling."
                elif count == 1:
                    response_text = f"There is 1 application with an unassigned field visit awaiting scheduling: {_nums}."
                else:
                    response_text = f"There are {count} applications with unassigned field visits awaiting scheduling: {_nums}."
            
        elif intent == "fv_recently_rescheduled":
            count = structured_data.get("recently_rescheduled_count", 0) if structured_data else 0
            if count == 0:
                response_text = "No field visits were rescheduled during the last 7 days."
            elif count == 1:
                response_text = "1 field visit was rescheduled during the last 7 days."
            else:
                response_text = f"{count} field visits were rescheduled during the last 7 days."
            
        elif intent == "fv_scheduling_conflicts":
            overlap_date = structured_data.get("overlap_date") if structured_data else None
            if overlap_date:
                response_text = (
                    f"Scheduling conflict detected: two or more field visits are scheduled on "
                    f"{overlap_date}. Please reschedule one of them — note that a field visit date "
                    f"change must be approved by the Tahsildar."
                )
            else:
                response_text = "No scheduling conflicts identified in the current inspection calendar."
        
        elif intent == "highest_priority_applications":
            count = len(structured_data.get("applications", []))
            stage_filter = structured_data.get("query_type", "").split("—")[-1].strip().replace(" Stage", "") if "—" in structured_data.get("query_type", "") else None
            is_tamil = language in ("ta", "tanglish")
            
            stage_text = f" in {stage_filter} stage" if stage_filter and stage_filter != "High Priority Applications" else ""
            
            if count == 0:
                response_text = (
                    f"உயர் முன்னுரிமை விண்ணப்பங்கள் எதுவும் இல்லை{stage_text}." if is_tamil
                    else f"There are no high priority applications{stage_text} at this time."
                )
            elif count == 1:
                response_text = (
                    f"1 உயர் முன்னுரிமை விண்ணப்பம் உள்ளது{stage_text} (⚠️ warning அல்லது overdue)." if is_tamil
                    else f"There is 1 high priority application{stage_text} (⚠️ warning or overdue)."
                )
            else:
                response_text = (
                    f"{count} உயர் முன்னுரிமை விண்ணப்பங்கள் உள்ளன{stage_text} (⚠️ warning அல்லது overdue)." if is_tamil
                    else f"There are {count} high priority applications{stage_text} (⚠️ warning or overdue)."
                )
        
        elif intent == "survey_owners" and not response_text:
            if structured_data and structured_data.get("direct_answer"):
                response_text = structured_data["direct_answer"]
            elif not structured_data or not structured_data.get("found", True):
                response_text = structured_data.get("message", "Survey not found or not accessible.")
            else:
                owners = structured_data.get("owners", [])
                survey_no = structured_data.get("survey_no", "")
                if not owners:
                    response_text = f"No ownership records found for Survey No. {survey_no}."
                else:
                    owner_lines = [_owner_detail_line(o, message) for o in owners]
                    response_text = f"Owners for Survey No. {survey_no} ({len(owners)} record(s)):\n" + "\n".join(owner_lines)
        elif intent == "service_code_lookup":
            _f_codes = (_followup_ctx.filters or {}).get("service_codes") if _followup_ctx else None
            if not _f_codes and _followup_ctx and (_followup_ctx.filters or {}).get("service_code"):
                _f_codes = [_followup_ctx.filters["service_code"]]
            if not _f_codes and _followup_ctx and _followup_ctx.application_numbers:
                _f_codes = _followup_ctx.application_numbers
            from backend.services.postgres import get_all_service_fees
            _fee_overrides = await get_all_service_fees(db) if db else None
            if _force_detailed_service_code_answer:
                response_text = _service_code_lookup_answer(message, language in ("ta", "tanglish"), brief=False, followup_codes=_f_codes, chat_history=chat_history, fee_overrides=_fee_overrides)
            else:
                response_text = await _service_code_lookup_answer_natural(
                    message, language in ("ta", "tanglish"),
                    not _is_detail_request(message), language, followup_codes=_f_codes, chat_history=chat_history, fee_overrides=_fee_overrides)
            # Recorded so a SECOND "explain" in a row ("what is ISD?" ->
            # "explain briefly" -> "explain in detail") still means ISD --
            # without this, the second hop found no referent of its own
            # (service_code_lookup names no application/survey) and the
            # stale-context fallback below picked up whatever unrelated
            # application the officer had looked at earlier in the session.
            # The code actually explained -- read off the answer itself rather
            # than re-extracted from the question, since "isd"/"nisd" resolve
            # via a word-level fallback local to _service_code_lookup_answer
            # that find_service_codes() (the general helper) does not know
            # about, and would otherwise find nothing for exactly those two.
            _sc_in_answer = sorted(list(set(re.findall(r'\b01[5-9]\d\b', response_text))))
            if len(_sc_in_answer) == 1:
                structured_data = {"service_code_filter": _sc_in_answer[0],
                                    "query_type": "Service Code Lookup"}
            elif len(_sc_in_answer) >= 2:
                structured_data = {"service_codes": _sc_in_answer,
                                    "service_code_filter": _sc_in_answer[0],
                                    "query_type": "Service Code Comparison"}
        elif intent == "all_service_code_comparison":
            from backend.services.postgres import get_service_register_counts_by_code, get_all_service_fees
            _dynamic_counts = await get_service_register_counts_by_code(db) if db else None
            _fee_map = await get_all_service_fees(db) if db else None
            response_text = _all_service_code_comparison_answer(
                message, language in ("ta", "tanglish"),
                dynamic_counts=_dynamic_counts, fee_map=_fee_map
            )
            structured_data = {
                "query_type": "All Service Code Comparison",
                "service_codes_count": 29
            }
        elif intent == "unidentified_number":
            response_text = _unidentified_number_answer(message, language in ("ta", "tanglish"))
        elif any(ph in message.lower() for ph in [
            "uploaded", "word document", "pdf document", "question bank",
            "answer all", "answer for all", "from the document", "in the document",
            "the file", "attached file", "from this file",
        ]):
            # User is asking about an uploaded document but no content was extracted.
            response_text = (
                "I don't see a file attached in this chat (uploaded files are kept for "
                f"{settings.UPLOAD_RETENTION_MINUTES} minutes only). "
                "Upload a PDF, Word (.docx), CSV or text file using the attach "
                "button and I'll answer your questions from it, with citations."
            )
        # Deterministic table intro — mirrors process_chat_stream so both
        # paths introduce a rendered table with the same wording.
        elif not response_text and intent in ("pending_applications", "field_visits", "fv_scheduled_this_week",
                        "fv_overdue_inspections", "fv_unassigned_awaiting", "fv_recently_rescheduled",
                        "ward_surveys", "block_surveys", "survey_detail", "next_subdivision",
                        "jurisdiction_summary", "rejection_info", "taluk_summary",
                        "litigation_check", "highest_priority_applications",
                        "town_applications", "block_applications",
                        "isd_applications", "nisd_applications",
                        "both_applications"):
            # Table is rendered on the frontend. Just emit a short natural intro.
            found = structured_data.get("found", True) if structured_data else False
            if not found:
                response_text = structured_data.get("message", "No records found.")
            else:
                asking_for_count = _is_count_only_query(message)
                
                if asking_for_count and intent in ("pending_applications", "isd_applications", "nisd_applications", "both_applications"):
                    response_text = _format_count_intro(structured_data, language, message)
                # Special message for priority applications
                elif intent == "highest_priority_applications":
                    count = len(structured_data.get("applications", []))
                    stage_filter = structured_data.get("query_type", "").split("—")[-1].strip().replace(" Stage", "") if "—" in structured_data.get("query_type", "") else None
                    is_tamil = language in ("ta", "tanglish")
                    
                    stage_text = f" in {stage_filter} stage" if stage_filter and stage_filter != "High Priority Applications" else ""
                    
                    if count == 0:
                        response_text = (
                            f"உயர் முன்னுரிமை விண்ணப்பங்கள் எதுவும் இல்லை{stage_text}." if is_tamil
                            else f"There are no high priority applications{stage_text} at this time."
                        )
                    elif count == 1:
                        response_text = (
                            f"1 உயர் முன்னுரிமை விண்ணப்பம் உள்ளது{stage_text} (⚠️ warning அல்லது overdue)." if is_tamil
                            else f"Found 1 high priority application{stage_text} (⚠️ warning or overdue)."
                        )
                    else:
                        response_text = (
                            f"{count} உயர் முன்னுரிமை விண்ணப்பங்கள் உள்ளன{stage_text} (⚠️ warning அல்லது overdue)." if is_tamil
                            else f"Found {count} high priority applications{stage_text} (⚠️ warning or overdue)."
                        )
                elif intent in ("field_visits", "fv_between_dates"):
                    count = structured_data.get("count", len(structured_data.get("field_visits", [])))
                    qtype = structured_data.get("query_type", "Field Visits")
                    start_date = structured_data.get("start_date")
                    end_date = structured_data.get("end_date")
                    date_range = f" ({start_date} to {end_date})" if (start_date and end_date) else ""
                    if count == 0:
                        response_text = f"No field visits found{date_range}."
                    elif count == 1:
                        response_text = f"Found 1 field visit{date_range}."
                    else:
                        response_text = f"Found {count} field visit(s){date_range}."
                else:
                    qtype = structured_data.get("query_type", "") if structured_data else ""
                    if qtype:
                        response_text = f"Here are the {qtype.lower()} results."
                    else:
                        response_text = "Results are shown in the table below."

        elif not response_text and intent in ("check_sale_deed", "sale_deed_check") and (
            _sd_answer := build_sale_deed_direct_answer(structured_data, message, language)
        ):
            response_text = _sd_answer
            logger.info("Responded with direct Python answer (sale deed field query)")

        elif not response_text and structured_data and "applications" in structured_data:
            count = structured_data.get("count", len(structured_data.get("applications", [])))
            qtype = structured_data.get("query_type", "Pending Applications")
            response_text = f"Found {count} application(s) ({qtype})."
        elif not response_text and _no_evidence_for_llm(_original_message, intent, structured_data, message, _followup_ctx):
            response_text = _clarification_reply(language)
            logger.info("No domain evidence for an LLM answer; asked for clarification")

        elif not response_text:
            # Last resort. Every deterministic handler has passed on this
            # message, so the alternative here is a free-text prompt that can
            # look nothing up. Give the LLM authorized tools instead, under the
            # signed-in officer, and keep the old prompt as the fallback for
            # when the agent layer cannot run at all.
            response_text = ""
            if settings.AGENT_ENABLED:
                try:
                    _agent_res = await run_agent(
                        message, officer, db, chat_history, language)
                    response_text = _agent_res.answer
                    # `intent` is deliberately left as parsed: it still drives
                    # _build_table_data below, and overwriting it here would
                    # suppress a table the deterministic side had earned.
                    logger.info(
                        f"Answered via the agent layer "
                        f"(tools={_agent_res.used_tools or 'none'})")
                except asyncio.TimeoutError:
                    # The tool loop already used its whole budget; a second long LLM
                    # call would double the wait for an answer that is just as slow.
                    logger.warning("Agent layer hit its time budget; answering that it is slow")
                    response_text = (
                        "பதிலளிக்க வழக்கத்தை விட அதிக நேரம் ஆகிறது. கேள்வியைச் சுருக்கி மீண்டும் முயற்சிக்கவும்."
                        if language in ("ta", "tanglish") else
                        "This is taking longer than expected to answer. Please try again, or narrow "
                        "the question (an application number, a survey number, a ward or a service code).")
                except AgentUnavailable as _agent_exc:
                    logger.warning(f"Agent layer unavailable, using the plain prompt: {_agent_exc}")
                except Exception as _agent_exc:  # never lose the turn to the agent
                    logger.error(f"Agent layer errored, using the plain prompt: {_agent_exc}",
                                 exc_info=True)
            if not response_text:
                response_text = await call_llama(full_prompt)

            # The model has written; check what it wrote against the data it was
            # written from. Applied only on this branch -- every other answer in
            # this function is rendered from the query result directly and has
            # nothing to disagree with.
            response_text, _verify_note = _verify_answer_numbers(
                response_text, structured_data, intent)
            if _verify_note:
                response_text += _verify_note

        # Step 7: Calculate response time
        response_time_ms = int((time.time() - start_time) * 1000)
        
        # What this answer was about, recorded for the next turn to resolve a
        # bare follow-up against. Built only from the structured data a query
        # returned -- never from the wording of the answer.
        _out_ctx = fctx.build_context(intent, structured_data,
                                      fallback_app_number=locals().get("app_number"))
        # A patta-transfer-party answer is text set during the pre-empt, so
        # structured_data may have been overwritten by a later fetch branch
        # (e.g. application_status). Record the party explicitly so the next
        # turn's "what is their gender?" stays on it.
        try:
            _pick_ctx = getattr(locals().get("_followup"), "context", None)
            if (_out_ctx and _out_ctx.entity == fctx.ENTITY_APPLICATION and _pick_ctx
                    and _pick_ctx.entity == fctx.ENTITY_APPLICATION_LIST
                    and len(_pick_ctx.application_numbers or []) > 1):
                _out_ctx.filters = {**(_out_ctx.filters or {}),
                                    "list_numbers": list(_pick_ctx.application_numbers)}
        except Exception:
            pass
        if _tp_marker:
            _out_ctx = fctx.FollowupContext(
                entity=fctx.ENTITY_APPLICATION,
                application_numbers=[str(_tp_marker[0]).upper()],
                filters={"transfer_party": _tp_marker[1]},
                query_type="Patta Transfer Parties",
                intent=intent)
        # A registration-detail answer ("registered at Melur on …") -- so a bare
        # "when?" / "where?" / "and the reason?" next turn stays on the deed,
        # not the filing date.
        elif _reg_marker:
            _out_ctx = fctx.FollowupContext(
                entity=fctx.ENTITY_APPLICATION,
                application_numbers=[str(_reg_marker[0]).upper()],
                filters={"reg_detail": _reg_marker[1]},
                query_type="Registration Detail",
                intent=intent)
        # Step 8: Save chat messages to database
        await save_chat_messages(
            db=db,
            session_id=session_id,
            user_message=_original_message,
            assistant_message=response_text,
            language=language,
            response_time_ms=response_time_ms,
            officer_id=officer.officer_id if officer else None,
            structured_context=(_out_ctx.to_json() if _out_ctx else None)
        )
        
        logger.info(f"Chat processed successfully in {response_time_ms}ms")
        
        # Prepare response with structured data for frontend rendering.
        # Suppress table_data when html_response is set OR when the HTML path
        # was bypassed for an interrogative query (_bypass_html) — in that case
        # the LLM answered conversationally and we don't want a table appended.
        # officer_workload is excluded too: the frontend's prepareWorkloadTable()
        # reads `data.workload.total_applications` (a nested object, different
        # field names) but `_build_table_data` sends a flat dict with different
        # keys entirely -- `w = data.workload || {}` always lands on `{}`, so
        # the table card rendered an unconditional empty "No records found."
        # right under the (correct, already complete) text answer.
        _td = None if (html_response or _bypass_html or intent in ("last_application", "fv_visit_plan", "officer_workload")) else _build_table_data(intent, message, str(officer.officer_id), structured_data)
        if _td:
            _td['language'] = language
        response = {
            "response": response_text,
            "language": language,
            "intent": intent,
            "sources": [],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "context_used": context_used,
            "response_time_ms": response_time_ms,
            "table_data": _td
        }
        
        # Keep structured_data for backward compatibility if needed
        if structured_data and structured_data.get("found", True):
            response["structured_data"] = structured_data
        
        return response
        
    except Exception as e:
        # Roll back BEFORE logging. The traceback renderer walks the frame
        # locals, and touching an ORM object there triggers a lazy load outside
        # the async context (MissingGreenlet) that leaves the session unusable
        # for every later message in the conversation.
        try:
            await db.rollback()
        except Exception as rb_err:
            logger.warning(f"Rollback after process_chat failure also failed: {rb_err}")

        logger.error(f"Error in process_chat: {e}", exc_info=True)

        # Return error message in appropriate language
        error_messages = {
            "en": "I apologize, but I encountered an error processing your request. Please try again.",
            "ta": "மன்னிக்கவும், உங்கள் கோரிக்கையைச் செயல்படுத்துவதில் பிழை ஏற்பட்டது. மீண்டும் முயற்சிக்கவும்.",
            "tanglish": "மன்னிக்கவும், உங்கள் கோரிக்கையைச் செயல்படுத்துவதில் பிழை ஏற்பட்டது. மீண்டும் முயற்சிக்கவும்."
        }
        
        language = detect_language(message)
        error_msg = error_messages.get(language, error_messages["en"])
        
        response = {
            "response": error_msg,
            "language": language,
            "context_used": False,
            "response_time_ms": int((time.time() - start_time) * 1000)
        }
        # Raw exception text can carry SQL, table names and connection details —
        # only expose it outside production.
        if settings.ENVIRONMENT != "production":
            response["error"] = str(e)
        return response


async def process_chat_stream(
    message: str,
    session_id: str,
    officer: OfficerContext,
    db: AsyncSession,
    chat_history: list = None
):
    """Stream one answer, with the read-only guard armed for the whole turn.

    The guard wraps the generator body, so it stays armed across every yield
    -- the SSE stream is produced lazily and the tool loop, the DB reads and
    the transcript write all happen between chunks. See `readonly_guard`.
    """
    with chat_turn():
        _RAW_MESSAGE.set(message)
        plan = _plan_requests(message)
        import json as _json_plan
        _bp = _big_plan(message)
        if _bp:
            res = await _answer_big(_bp, message, session_id, officer, db, chat_history)
        elif plan and plan[0] == "multi" and len(plan[1]) >= 2:
            res = await _answer_multi(plan, message, session_id, officer, db, chat_history)
        else:
            res = await _merge_turn(message, session_id, officer, db, chat_history)
        if res is None:
            res = await _old_codes_turn(message, session_id, officer, db, chat_history)
        if res is None:
            res = await _record_field_turn(message, session_id, officer, db, chat_history)
        if res is None:
            res = await _recheck_turn(message, session_id, officer, db, chat_history)
        if res is None:
            res = await _compound_followup(message, session_id, officer, db, chat_history)
        if res is None:
            res = await _register_gap_turn(message, session_id, officer, db, chat_history)
        if res is None:
            res = await _howto_turn(message, session_id, officer, db, chat_history)
        if res is None:
            res = await _stage_def_turn(message, session_id, officer, db, chat_history)
        if res is None:
            res = await _calc_turn(message, session_id, officer, db, chat_history)
        if res is None:
            res = await _stats_turn(message, session_id, officer, db, chat_history)
        if res is None:
            res = await _number_turn(message, session_id, officer, db, chat_history)
        if res is None and plan:
            res = await _answer_multi(plan, message, session_id, officer, db, chat_history)
        if res is not None:
            yield f"data: {_json_plan.dumps({'content': res.get('response') or ''})}\n\n".encode('utf-8')
            if res.get("table_data"):
                yield f"data: {_json_plan.dumps({'table_data': res['table_data']})}\n\n".encode('utf-8')
            return
        async for chunk in _process_chat_stream_impl(
                message, session_id, officer, db, chat_history):
            yield chunk


async def _process_chat_stream_impl(
    message: str,
    session_id: str,
    officer: OfficerContext,
    db: AsyncSession,
    chat_history: list = None
):
    """
    Process chat message and stream the response back.
    Yields chunks of the response as they are generated.
    """
    start_time = time.time()
    
    try:
        # Step 0: Use provided chat history from client
        if not chat_history:
            chat_history = []
        logger.info("=== CHAT STREAM CONTEXT DEBUG ===")
        logger.info(f"Received {len(chat_history)} previous messages from client")
        logger.info(f"Current message: '{message}'")
        if chat_history:
            for i, msg in enumerate(chat_history[-3:]):  # Show last 3
                logger.info(f"  History[{i}]: {msg.get('role')} said: {(msg.get('content') or '')[:50]}...")

        # Step 1: Detect language (on the corrected text, so a slipped Tanglish word
        # does not flip the reply to English)
        language = detect_language(_fragment_typo_fix(message))
        if is_short_reply(message) and chat_history:
            prior_lang = get_prior_turn_language(chat_history)
            if prior_lang in ("ta", "tanglish"):
                language = prior_lang
        logger.info(f"Detected language: {language}")

        # Step 1b: A bare period ("last month") re-scopes the previous question.
        # The merged text drives intent detection and every handler below; the
        # transcript still records what the officer actually typed.
        _original_message = message
        _FV_SCOPE.set(None)
        _FV_COLS.set(None)
        _FV_KEEP.set(None)
        _FV_PRIOR_SORT.set(None)
        _RAW_BY_FIXED.set(None)      # the typo map is per turn: a stale entry would rewrite a later message
        _NEG_EXCLUDE.set(None)
        _neg_scope.EXCLUDE.set(None)
        _SORT_RESET.set(False)
        # Slips in a follow-up-shaped fragment are corrected for every rule below;
        # the transcript still records what was typed.
        _typo_fixed = _add_as_along(fctx.negation_normalise(_fragment_typo_fix(message)))
        if _typo_fixed != message:
            logger.info(f"Follow-up spelling corrected: '{message}' -> '{_typo_fixed}'")
            _RAW_BY_FIXED.set({_typo_fixed: message})
            _original_message = _typo_fixed
        message = _typo_fixed
        # A bare type word ("nisd", "what about isd") continues the previous question
        # with the other type; that is decided before the date / list re-scope, which
        # would otherwise claim it as a list follow-up.
        _swapped = _only_type_after_empty_list(message, chat_history)
        if _swapped == message:
            _swapped = await _type_swap_followup(db, session_id, chat_history, message)
        if _swapped != message:
            logger.info(f"Bare type follow-up: '{_original_message}' -> '{_swapped}'")
            _raw_typed = _RAW_BY_FIXED.get().get(_typo_fixed, _typo_fixed) if _RAW_BY_FIXED.get() else _typo_fixed
            _RAW_BY_FIXED.set({_swapped: _raw_typed})
            message = _swapped
            _original_message = _swapped
            _typo_fixed = _swapped
        else:
            message = _rescope_followup_message(message, chat_history)
            if message != _typo_fixed:
                logger.info(f"Date follow-up re-scoped: '{_original_message}' -> '{message}'")
            else:
                message = _rescope_list_followup(message, chat_history)
                if message != _typo_fixed:
                    logger.info(f"List follow-up re-scoped: '{_original_message}' -> '{message}'")
        # A message the date/list re-scope above already made self-contained
        # must not be further rewritten by the single-application follow-up
        # below -- "how many of those are completed?" right after a field-visit
        # summary was correctly re-scoped here, then the single-app resolver ran
        # on the UNSCOPED _original_message anyway, found a stale application
        # number from further back in the history, and appended it, silently
        # discarding the already-correct re-scope and answering about the wrong
        # record entirely.
        _already_rescoped = message != _typo_fixed

        # Step 1b2: IGRS / CAN asked as a RULE -- see the same step in
        # process_chat for why this runs before the channel filter.
        _rule_ctx_app = _extract_app_number_from_context(message, chat_history, allow_implicit_continuation=True)
        _rule_topic = None if _um_parse(message, chat_history) else _igrs_can_rule_topic(message, allow_bare_tamil_what=not _rule_ctx_app)
        # "what is SRO" is answered by the LLM (grounded on the SRO article in
        # land_rules.txt / faq_english.txt via RAG), not this deterministic
        # table -- explicit product decision, see the chat with the user.
        # Forced to general_query below (not just left to fall through) so it
        # cannot be picked up by an unrelated handler like sale_deed_check,
        # which also matches on "registrar" -- CLAUDE.md already documents
        # that exact regression from before sro_what existed.
        _force_general_query = _rule_topic == "sro_what"
        if _force_general_query:
            _rule_topic = None
        if _rule_topic:
            _rule_text = _igrs_can_rule_answer(_rule_topic, language)
            if _rule_text and _rule_topic.startswith("channel_basis"):
                _rule_text += await _channel_basis_evidence(
                    db, officer, session_id, chat_history, message, language)
            if _rule_text:
                import json as _json
                logger.info(f"Answered the IGRS/CAN rule question '{_rule_topic}'")
                yield f"data: {_json.dumps({'content': _rule_text})}\n\n".encode('utf-8')
                await save_chat_messages(
                    db=db, session_id=session_id, user_message=_original_message,
                    assistant_message=_rule_text, language=language,
                    response_time_ms=int((time.time() - start_time) * 1000),
                    officer_id=officer.officer_id if officer else None,
                    structured_context=None)
                yield b"data: [DONE]\n\n"
                return

        # Step 1b3: ISD/NISD workflow-steps -- see the same step in
        # process_chat for why this is answered deterministically.
        _wf_topic = _workflow_steps_topic(message, chat_history)
        if _wf_topic in ("GLOSS:ward", "GLOSS:block", "GLOSS:wards", "GLOSS:blocks"):
            _stand_aside = _recent_app_number_in_history(chat_history)
            # "blocks enna?" (plural, Tanglish/Tamil) after a listing asks for that column;
            # "what is a block?" is still the definition
            if (not _stand_aside and _wf_topic in ("GLOSS:wards", "GLOSS:blocks")
                    and re.search(r"enna|என்ன|edhu", message.lower())):
                _stand_aside = bool(await _load_followup_context(
                    db, session_id, chat_history, message,
                    officer_id=officer.officer_id if officer else None))
            if _stand_aside:
                _wf_topic = None
        if _wf_topic:
            import json as _json
            _wf_text = await _workflow_steps_answer_natural(
                _wf_topic, language in ("ta", "tanglish"), not _is_detail_request(message),
                language, chat_history)
            logger.info(f"Answered the {_wf_topic} workflow-steps question deterministically")
            yield f"data: {_json.dumps({'content': _wf_text})}\n\n".encode('utf-8')
            await save_chat_messages(
                db=db, session_id=session_id, user_message=_original_message,
                assistant_message=_wf_text, language=language,
                response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None,
                structured_context=None)
            yield b"data: [DONE]\n\n"
            return

        # Step 1b4: MERGE is not a separate application type
        # here -- see _is_merge_question / process_chat for why this must
        # never reach general_query, on this path or the non-streaming one.
        if _is_merge_question(_original_message) and not _appinfo_qa.claims(_original_message):
            import json as _json
            _merge_text = _no_merge_type_answer(language in ("ta", "tanglish"))
            logger.info("Answered a merge question deterministically -- merge is a field, not a service")
            yield f"data: {_json.dumps({'content': _merge_text})}\n\n".encode('utf-8')
            await save_chat_messages(
                db=db, session_id=session_id, user_message=_original_message,
                assistant_message=_merge_text, language=language,
                response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None,
                structured_context=None)
            yield b"data: [DONE]\n\n"
            return

        # Direct Handler for "what is today's date / day?" (STREAMING)
        # Checked ahead of follow-up context resolution to prevent stale app hijacking.
        if _is_today_date_question(message):
            import json as _json_td
            res_txt = _today_date_answer(language, message)
            yield f"data: {_json_td.dumps({'content': res_txt}, ensure_ascii=False)}\n\n".encode('utf-8')
            await save_chat_messages(
                db=db, session_id=session_id, user_message=_original_message,
                assistant_message=res_txt, language=language,
                response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None
            )
            yield b"data: [DONE]\n\n"
            return

        # Step 1c: Resolve an implicit follow-up against the stored reference
        # context -- "what is the applicant name?", "which one is oldest?",
        # "when was it scheduled?". These name no application, no list and no
        # scope, and until the context record existed they were answered by
        # scraping the previous rendered answer, or not at all. Runs BEFORE
        # intent routing so every handler below sees a self-contained question.
        _followup_ctx = (await _load_followup_context(db, session_id, chat_history, _original_message,
                                                      officer_id=officer.officer_id if officer else None)
                         or _context_from_history(chat_history))

        # Continuation handler (context-aware resumption, pagination, and elaboration) (STREAMING)
        if _CONTINUATION_COMMAND_RE.match(_original_message.strip()):
            _is_att_active = (await attachment_qa.attachment_topic_active(db, session_id) or
                              await attachment_qa.any_active_documents(db, officer.officer_id if officer else None, session_id))
            _is_list_active = bool(_followup_ctx and _followup_ctx.entity == fctx.ENTITY_APPLICATION_LIST)
            if not _is_att_active and not _is_list_active:
                import json as _json_cont
                _prev_user_q = ""
                _prev_asst_a = ""
                if chat_history:
                    for _m in reversed(chat_history):
                        if not _prev_asst_a and _m.get("role") == "assistant":
                            _prev_asst_a = _m.get("content") or ""
                        elif not _prev_user_q and _m.get("role") == "user":
                            _prev_user_q = _m.get("content") or ""
                        if _prev_asst_a and _prev_user_q:
                            break

                # Case 1: Empty session / No prior context
                if not _prev_asst_a and not _followup_ctx:
                    _empty_text = (
                        "தொடர்வதற்கு முந்தைய தலைப்போ பணியோ எதுவும் இல்லை. விண்ணப்பம், நில அளவை எண், சேவை குறியீடு அல்லது ஆவணம் பற்றி கேளுங்கள்."
                        if language in ("ta", "tanglish") else
                        "There is no previous topic or task to continue from. Please ask about an application, survey number, service code, or upload a document."
                    )
                    logger.info("Continuation requested with no context; asked for domain topic (stream)")
                    yield f"data: {_json_cont.dumps({'content': _empty_text}, ensure_ascii=False)}\n\n".encode('utf-8')
                    await save_chat_messages(
                        db=db, session_id=session_id, user_message=_original_message,
                        assistant_message=_empty_text, language=language,
                        response_time_ms=int((time.time() - start_time) * 1000),
                        officer_id=officer.officer_id if officer else None,
                        structured_context=None
                    )
                    yield b"data: [DONE]\n\n"
                    return

                # Case 2: Incomplete / Truncated previous answer (Type A resumption)
                if _is_incomplete_answer(_prev_asst_a, _original_message):
                    logger.info("Resuming truncated/incomplete previous answer via LLM (stream)")
                    if language in ("ta", "tanglish"):
                        _resump_prompt = (
                            f"முந்தைய பதில் முழுமையடையாமல் நின்றது.\n\n"
                            f"[கேள்வி]:\n{_prev_user_q}\n\n"
                            f"[இதுவரை வழங்கப்பட்ட பகுதி பதில்]:\n{_prev_asst_a}\n\n"
                            f"ஏற்கனவே கூறியவற்றை மீண்டும் கூறாமல், நிறுத்திய இடத்திலிருந்து தடையின்றி விடையைத் தொடரவும்:"
                        )
                    else:
                        _resump_prompt = (
                            f"The previous answer to the question was cut off or incomplete.\n\n"
                            f"[Original Question]:\n{_prev_user_q}\n\n"
                            f"[Partial Answer Provided So Far]:\n{_prev_asst_a}\n\n"
                            f"Please continue the answer seamlessly from where it was cut off without repeating what has already been said:"
                        )
                    _full_res_text = ""
                    async for _chunk in call_llama_stream(_resump_prompt):
                        _full_res_text += _chunk
                        yield f"data: {_json_cont.dumps({'content': _chunk}, ensure_ascii=False)}\n\n".encode('utf-8')
                    await save_chat_messages(
                        db=db, session_id=session_id, user_message=_original_message,
                        assistant_message=_full_res_text, language=language,
                        response_time_ms=int((time.time() - start_time) * 1000),
                        officer_id=officer.officer_id if officer else None,
                        structured_context=_followup_ctx.to_json() if _followup_ctx else None
                    )
                    yield b"data: [DONE]\n\n"
                    return

                # Case 3: Service code in context (Type B service code expansion)
                _sc_code = None
                if _followup_ctx and _followup_ctx.entity == fctx.ENTITY_SERVICE_CODE:
                    _sc_code = (_followup_ctx.filters or {}).get("service_code") or (_followup_ctx.application_numbers[0] if _followup_ctx.application_numbers else None)
                if not _sc_code and _prev_asst_a:
                    _m_code = re.search(r"\b(015[1-9]|isd|nisd)\b", (_prev_user_q + " " + _prev_asst_a).lower())
                    if _m_code:
                        _sc_code = _m_code.group(1).upper()
                if _sc_code:
                    logger.info(f"Continuation expanding service code {_sc_code} (stream)")
                    _sc_text = await _service_code_lookup_answer_natural(
                        f"explain in detail {_sc_code}", language in ("ta", "tanglish"), brief=False,
                        language=language, chat_history=chat_history
                    )
                    _sc_ctx = fctx.FollowupContext(entity=fctx.ENTITY_SERVICE_CODE, application_numbers=[_sc_code],
                                                  filters={"service_code": _sc_code}, query_type="Service Code")
                    yield f"data: {_json_cont.dumps({'content': _sc_text}, ensure_ascii=False)}\n\n".encode('utf-8')
                    await save_chat_messages(
                        db=db, session_id=session_id, user_message=_original_message,
                        assistant_message=_sc_text, language=language,
                        response_time_ms=int((time.time() - start_time) * 1000),
                        officer_id=officer.officer_id if officer else None,
                        structured_context=_sc_ctx.to_json()
                    )
                    yield b"data: [DONE]\n\n"
                    return

                # Case 4: Single application in context (Type B application expansion)
                if _followup_ctx and _followup_ctx.entity == fctx.ENTITY_APPLICATION and _followup_ctx.application_numbers:
                    _app_num = _followup_ctx.application_numbers[0]
                    logger.info(f"Continuation expanding application details for {_app_num} (stream)")
                    _original_message = message = f"more details for {_app_num}"

                # Case 5: Completed general answer in context (Type B topic continuation)
                elif _prev_user_q:
                    logger.info("Continuation on completed general turn; offering elaboration (stream)")
                    _topic_prompt = (
                        f"முந்தைய தலைப்பு: \"{_prev_user_q}\".\n\nஇது குறித்து கூடுதல் விவரங்கள் தேவையா, அல்லது குறிப்பிட்ட விண்ணப்பம், நில அளவை எண், சேவை குறியீடு அல்லது பணிப்பாய்வு பற்றி அறிய விரும்புகிறீர்களா?"
                        if language in ("ta", "tanglish") else
                        f"The previous topic was about: \"{_prev_user_q}\".\n\nWould you like more details on this topic, or would you like to inquire about a specific application, survey number, service code, or workflow step?"
                    )
                    yield f"data: {_json_cont.dumps({'content': _topic_prompt}, ensure_ascii=False)}\n\n".encode('utf-8')
                    await save_chat_messages(
                        db=db, session_id=session_id, user_message=_original_message,
                        assistant_message=_topic_prompt, language=language,
                        response_time_ms=int((time.time() - start_time) * 1000),
                        officer_id=officer.officer_id if officer else None,
                        structured_context=_followup_ctx.to_json() if _followup_ctx else None
                    )
                    yield b"data: [DONE]\n\n"
                    return
        _sort_msg, _sort_reset = fctx.normalise_sort_negation(_original_message, _followup_ctx)
        if _sort_msg != _original_message:
            logger.info(f"Sort negation resolved: '{_original_message}' -> '{_sort_msg}'")
            _original_message = message = _sort_msg
            _SORT_RESET.set(_sort_reset)
        _ff_msg = _fullform_followup(_original_message, chat_history)
        if _ff_msg != _original_message:
            logger.info(f"Full-form follow-up: '{_original_message}' -> '{_ff_msg}'")
            _original_message = message = _ff_msg
        _ff_detail_term = _fullform_detail_term(_original_message, chat_history)
        if _ff_detail_term:
            import json as _json_ffd
            _ff_detail_text = _fullform_detail_reply(_ff_detail_term, language)
            logger.info(f"Answered a fullform detail follow-up for '{_ff_detail_term}' (stream)")
            yield f"data: {_json_ffd.dumps({'content': _ff_detail_text})}\n\n".encode('utf-8')
            await save_chat_messages(
                db=db, session_id=session_id,
                user_message=_original_message, assistant_message=_ff_detail_text,
                language=language,
                response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None
            )
            return
        _yr_msg = _qualifier_guard.share_year(_original_message)
        if _yr_msg != _original_message:
            logger.info(f"Shared year: '{_original_message}' -> '{_yr_msg}'")
            _original_message = message = _yr_msg
        _all_msg = _qualifier_guard.whole_register(
            _original_message, any(m.get("role") == "user" for m in (chat_history or [])))
        if _all_msg != _original_message:
            logger.info(f"Whole register: '{_original_message}' -> '{_all_msg}'")
            _original_message = message = _all_msg
        _noun_msg = _qualifier_guard.add_noun(
            _original_message, any(m.get("role") == "user" for m in (chat_history or [])))
        if _noun_msg != _original_message:
            logger.info(f"Scope with no noun: '{_original_message}' -> '{_noun_msg}'")
            _original_message = message = _noun_msg
        _lit_msg = _unheld_qa.litigation(_original_message, chat_history) or _unheld_qa.where_is(_original_message, chat_history) or _unheld_qa.intent_alias(_original_message)
        if _lit_msg:
            _original_message = message = _lit_msg
        # "Show only those with/without a subdivision recorded" is a LIST
        # filter over the carried queue, not a negated question about ONE
        # record -- but `_negq.rewrite()` finds an application number
        # anywhere in the last 8 turns and claims it regardless, stripping
        # "without" and reducing the message to "those a recorded
        # subdivision" (an existence check on whichever stale application it
        # found), before `_field_presence_answer` ever got a chance to see
        # the original wording. Checked first, same precedence `_neg_scope`
        # below already gives an `about_visit` queue over its own rewrite.
        # The STANDALONE shape ("which applications ... have no subdivision
        # recorded") gets the same precedence for the same reason -- it
        # names no application at all, but a stale number sitting in recent
        # chat_history is exactly what `_negq.rewrite()` goes looking for
        # regardless, and it would mangle this message the same way.
        _nq_msg = None if (_followup_ctx and _followup_ctx.application_numbers
                           and fctx.parse_field_presence_request(_original_message)) \
            or fctx.parse_standalone_field_presence_request(_original_message) \
            else _negq.rewrite(_original_message, chat_history)
        if _nq_msg is not None:
            logger.info(f"Negated record question: '{_original_message}' -> '{_nq_msg}' ({_negq.KIND.get()})")
            _original_message = message = _nq_msg
        _neg_prior = dict(_followup_ctx.filters or {}) if _followup_ctx else None
        # a field-visit table on screen: its negations ("not completed", "not ISD", "not in 2025") are
        # answered over the visit rows by the field-visit follow-up layer, not as an application list
        #
        # ONE application in view ("Show full application details for X" ->
        # "Is it not rejected?") is a yes/no STATUS QUESTION about that file,
        # not a request to exclude "rejected" from a list -- `neg_scope`
        # has no concept of a singular referent at all, so it read "not
        # rejected" as the status-list exclusion phrase it recognises
        # everywhere else and rewrote the whole message into a register-
        # wide "excluding rejected" listing, discarding "it" (and the one
        # application it pointed at) entirely. Guarded the same way the
        # field-visit case just above already is: by entity, not by intent,
        # since that is what actually distinguishes "a list is on screen"
        # from "one record is".
        _neg_singular = bool(_followup_ctx and _followup_ctx.entity == fctx.ENTITY_APPLICATION)
        # A negation right after an attachment answer ("don't show those,
        # just the one in our database") is a continuation of THAT answer,
        # not the application register -- `neg_scope` knows only the
        # register, so unguarded it read "show" + "those" as its own
        # listing-exclusion phrase and rewrote the message into "all
        # applications excluding ...", discarding the file context entirely
        # and answering with the officer's whole open queue. Guarded the
        # same way `_neg_singular` above is: by what the conversation is
        # actually about, so the rewritten message still reaches
        # `attachment_qa.plan_answer()` below unmolested and can resolve the
        # negation (or decline) against the file itself.
        _neg_attachment_topic = await attachment_qa.attachment_topic_active(db, session_id)
        _neg_contra = _neg_scope.detect_contradiction(_RAW_MESSAGE.get() or _original_message)
        if _neg_contra and not (_neg_singular or _neg_attachment_topic):
            import json as _json_contra
            yield f"data: {_json_contra.dumps({'content': _neg_contra})}\n\n".encode('utf-8')
            await save_chat_messages(
                db=db, session_id=session_id, user_message=_original_message,
                assistant_message=_neg_contra, language=language,
                response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None, structured_context=None)
            yield b"data: [DONE]\n\n"
            return
        _neg_msg2 = None if (_neg_singular or _neg_attachment_topic
                              or (_followup_ctx and (_followup_ctx.filters or {}).get("about_visit"))) \
            else _neg_scope.rewrite(
            _original_message, _neg_prior,
            bool(_followup_ctx and _followup_ctx.application_numbers
                 and not (_followup_ctx.filters or {}).get("about_visit")))
        if _neg_msg2 is not None:
            logger.info(f"Exclusions taken out: '{_original_message}' -> '{_neg_msg2}' {_neg_scope.EXCLUDE.get()}")
            _original_message = message = _neg_msg2
        _swap_msg = _compare_followup.swap_status(
            _original_message, [m.get("content") or "" for m in (chat_history or []) if m.get("role") == "user"])
        if _swap_msg is not None:
            logger.info(f"Status swap: '{_original_message}' -> '{_swap_msg}'")
            _original_message = message = _swap_msg
        _cmp_msg = _compare_followup.rewrite(
            _original_message,
            list(_followup_ctx.application_numbers)
            if (_followup_ctx and not (_followup_ctx.filters or {}).get("about_visit")) else [],
            [m.get("content") or "" for m in (chat_history or []) if m.get("role") == "user"])
        if _cmp_msg is not None:
            logger.info(f"Comparative follow-up re-asked: '{_original_message}' -> '{_cmp_msg}'")
            _original_message = message = _cmp_msg
        if _followup_is_self_contained(_original_message):
            # The message names its own subject, so it is a question rather
            # than a continuation however short it reads.
            _followup = fctx.Resolution()
        else:
            _followup = fctx.resolve(_original_message, _followup_ctx, language)
        _scoped_sd = None
        # "only completed" / "sort by date descending" / "show along district" after a
        # field-visit table is about the VISITS: re-ask it as a visit question,
        # inside the rows on screen, instead of turning the table into applications.
        _wf_remarks = None if _cm_strong(_original_message, chat_history) else await _workflow_remarks_answer(db, officer, _original_message, _followup_ctx, language)
        if _wf_remarks:
            import json as _json_wfr
            logger.info("Answered a workflow-remarks question from the workflow history (stream)")
            yield f"data: {_json_wfr.dumps({'content': _wf_remarks})}\n\n".encode('utf-8')
            await save_chat_messages(
                db=db, session_id=session_id, user_message=_original_message,
                assistant_message=_wf_remarks, language=language,
                response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None,
                structured_context=_followup_ctx.to_json() if _followup_ctx else None)
            yield b"data: [DONE]\n\n"
            return
        _fv_agg = await _fv_aggregate_answer(db, officer, _original_message, _followup_ctx, language)
        if _fv_agg:
            import json as _json_fva
            logger.info("Counted over the field-visit table on screen (stream)")
            yield f"data: {_json_fva.dumps({'content': _fv_agg})}\n\n".encode('utf-8')
            await save_chat_messages(
                db=db, session_id=session_id, user_message=_original_message,
                assistant_message=_fv_agg, language=language,
                response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None,
                structured_context=_followup_ctx.to_json())
            yield b"data: [DONE]\n\n"
            return
        _fv_inc_exc = _fv_include_exclude_answer(_original_message, _followup_ctx)
        if _fv_inc_exc:
            import json as _json_fie
            _fie_text, _fie_scope = _fv_inc_exc
            logger.info(f"Include/exclude resolved over the field-visit table on screen (stream) -> {_fie_scope}")
            yield f"data: {_json_fie.dumps({'content': _fie_text})}\n\n".encode('utf-8')
            _fie_ctx = dataclasses.replace(_followup_ctx, application_numbers=_fie_scope)
            await save_chat_messages(
                db=db, session_id=session_id, user_message=_original_message,
                assistant_message=_fie_text, language=language,
                response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None,
                structured_context=_fie_ctx.to_json())
            yield b"data: [DONE]\n\n"
            return
        _fp = await _field_presence_answer(db, officer, _original_message, _followup_ctx, language)
        if _fp:
            import json as _json_fp
            _fp_text, _fp_scope = _fp
            logger.info(f"Field-presence filter resolved over the carried list (stream) -> {_fp_scope}")
            yield f"data: {_json_fp.dumps({'content': _fp_text})}\n\n".encode('utf-8')
            _fp_ctx = dataclasses.replace(_followup_ctx, application_numbers=_fp_scope)
            await save_chat_messages(
                db=db, session_id=session_id, user_message=_original_message,
                assistant_message=_fp_text, language=language,
                response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None,
                structured_context=_fp_ctx.to_json())
            yield b"data: [DONE]\n\n"
            return
        _sfp = await _standalone_field_presence_answer(db, officer, _original_message, session_id, chat_history, language)
        if _sfp:
            import json as _json_sfp
            _sfp_text, _sfp_matched, _sfp_scope_kind = _sfp
            logger.info(f"Standalone field-presence query ({_sfp_scope_kind}, stream) -> {len(_sfp_matched)} matched")
            yield f"data: {_json_sfp.dumps({'content': _sfp_text})}\n\n".encode('utf-8')
            _sfp_ctx = fctx.FollowupContext(
                entity=fctx.ENTITY_APPLICATION_LIST, application_numbers=_sfp_matched,
                filters={"about_visit": _sfp_scope_kind in ("awaiting_visit", "overdue_visit")},
                query_type="Field Presence Filter", intent="field_presence_filter")
            await save_chat_messages(
                db=db, session_id=session_id, user_message=_original_message,
                assistant_message=_sfp_text, language=language,
                response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None,
                structured_context=_sfp_ctx.to_json() if _sfp_matched else None)
            yield b"data: [DONE]\n\n"
            return
        _rf = await _restore_original_filter_answer(db, officer, _original_message, _followup_ctx, language, session_id=session_id, officer_id=officer.officer_id if officer else None)
        if _rf:
            import json as _json_rf
            _rf_text, _rf_sd = _rf
            _rf_ctx = (fctx.build_context("all_service_code_applications", _rf_sd)
                       if _rf_sd else _followup_ctx)
            logger.info(f"Restore-original-filter (stream): {'restored' if _rf_sd else 'no snapshot on record'}")
            yield f"data: {_json_rf.dumps({'content': _rf_text})}\n\n".encode('utf-8')
            await save_chat_messages(
                db=db, session_id=session_id, user_message=_original_message,
                assistant_message=_rf_text, language=language,
                response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None,
                structured_context=_rf_ctx.to_json() if _rf_ctx else None)
            yield b"data: [DONE]\n\n"
            return
        _fv_re = _fv_followup(_original_message, _followup_ctx, _followup)
        if _fv_re:
            message, _fv_scope = _fv_re
            _FV_SCOPE.set(_fv_scope)
            _already_rescoped = True
            _followup = fctx.Resolution()
            logger.info(f"Field-visit table follow-up re-asked as: '{message}' over {len(_fv_scope)} row(s)")
        # "can i get the full details" / "i need details of both" -- name the
        # rows the officer is looking at and let the ordinary multi-application
        # detail path answer. Runs ahead of both the aggregate and the ambiguous
        # branches, which answered it with a count or with a question.
        if _wants_carried_list_details(_original_message, _followup_ctx) and not _cm_strong(_original_message, chat_history):
            message = _carried_details_message(_followup_ctx, _original_message)
            _already_rescoped = True
            _followup = fctx.Resolution()
            logger.info(
                f"Details follow-up scoped to the "
                f"{len(_followup_ctx.application_numbers)} listed application(s)")
        # "include rejected too" right after a channel listing: `fctx.classify()` reads
        # "include ... too" as the same generic singular/confirmation fragment shape as
        # "is that all?", so without this it never reached the register re-query below at
        # all -- `_followup.ambiguous` was True and the turn was answered "Which one do you
        # mean?" to a question that named no row. Must run ahead of the ambiguous branch for
        # exactly that reason.
        _rej_widen_early = _resolve_rejected_widen(_original_message, chat_history)
        if _rej_widen_early:
            message = _rej_widen_early
            _already_rescoped = True
            _followup = fctx.Resolution()
            logger.info(f"Rejected-widen follow-up resolved: '{_original_message}' -> '{_rej_widen_early}'")
        if _followup.ambiguous:
            # The older resolution path can still name a record for this turn.
            # Asking would be a regression, not a safeguard, so stand aside and
            # let the existing handlers answer as they always did -- but only
            # when the record it names is one of the rows in view. Unchecked, it
            # reached back over the stored context and answered about an
            # application from turns earlier (85 seconds in the agent layer,
            # about a file the officer had moved on from).
            _legacy_num = _extract_app_number_from_context(
                _original_message, chat_history, allow_implicit_continuation=True)
            _carried_nums = {n.upper() for n in
                             ((_followup_ctx.application_numbers or []) if _followup_ctx else [])}
            if (_legacy_num and (not _carried_nums or _legacy_num.upper() in _carried_nums)
                    and not fctx.is_bare_deictic(_original_message)):
                logger.info("Follow-up left to the existing resolution path")
                _followup = fctx.Resolution()
        # "What is my workload?" -> "show" / "show applications" / "list" /
        # "display" -- see the matching comment in process_chat(). Merge
        # note: see that comment for why this is kept over origin/main's
        # narrower `_workload_display_followup` fix for the same bug.
        _wl_show = await _workload_show_followup(db, officer, _original_message, chat_history, language)
        if _wl_show:
            import json as _json_wl
            _wl_data, _wl_text = _wl_show
            _wl_ctx = fctx.build_context("all_service_code_applications", _wl_data)
            logger.info("Workload follow-up (stream): showed the %d application(s) behind the workload count",
                        _wl_data.get("count", 0))
            yield f"data: {_json_wl.dumps({'content': _wl_text})}\n\n".encode('utf-8')
            await save_chat_messages(
                db=db, session_id=session_id, user_message=_original_message,
                assistant_message=_wl_text, language=language,
                response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None,
                structured_context=_wl_ctx.to_json() if _wl_ctx else None)
            yield b"data: [DONE]\n\n"
            return
        # See the matching comment in process_chat(): a cross-check filter
        # fragment has no `_followup_ctx` of its own and must be checked
        # ahead of the orphan-fragment block just below, for the same reason
        # `_wl_show` is checked ahead of it too.
        _cc_filtered = await attachment_qa._crosscheck_filter_followup(
            db, session_id, _original_message, language)
        if _cc_filtered is not None:
            import json as _json_cc
            res_txt = _cc_filtered.text
            logger.info("Cross-check follow-up filtered (stream) to %d of the previous result",
                        len((_cc_filtered.extra or {}).get("crosscheck") or []))
            yield f"data: {_json_cc.dumps({'content': res_txt})}\n\n".encode('utf-8')
            await save_chat_messages(
                db=db, session_id=session_id, user_message=_original_message,
                assistant_message=res_txt, language=language,
                response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None,
                structured_context=({"entity": "attachment", "document_ids": _cc_filtered.document_ids,
                                    **(_cc_filtered.extra or {})}
                                   if len(_cc_filtered.document_ids or []) == 1 else None))
            yield b"data: [DONE]\n\n"
            return
        if (not _followup.resolved and not _followup.ambiguous
                and not _neg_attachment_topic
                and not attachment_qa.is_negative_correction(_original_message)
                and not attachment_qa.is_project_relevance_request(_original_message)
                and not attachment_qa.location_reference(_original_message)
                and not await attachment_qa.attachment_topic_active(db, session_id)
                and not await attachment_qa.any_active_documents(db, officer.officer_id if officer else None, session_id)
                and (_is_orphan_list_followup(_original_message, _followup_ctx)
                     or _is_empty_sort_followup(_original_message, _followup_ctx)
                     or _is_bare_fragment_without_context(_original_message, _followup_ctx, chat_history))):
            import json as _json
            _orphan_text = _orphan_followup_reply(
                language, empty=_is_empty_sort_followup(_original_message, _followup_ctx)
                and _list_was_asked(chat_history))
            logger.info("List follow-up with no context — asking rather than "
                        "letting the model answer")
            yield f"data: {_json.dumps({'content': _orphan_text})}\n\n".encode('utf-8')
            await save_chat_messages(
                db=db, session_id=session_id, user_message=_original_message,
                assistant_message=_orphan_text, language=language,
                response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None,
                structured_context=None)
            yield b"data: [DONE]\n\n"
            return
        if _followup.ambiguous:
            logger.info(f"Ambiguous follow-up '{message}' — asking rather than guessing")
            import json as _json
            yield f"data: {_json.dumps({'content': _followup.clarification})}\n\n".encode('utf-8')
            # See the matching comment in process_chat(): the list is still
            # valid here, only the specific row named was not, so the context
            # is re-saved rather than cleared.
            await save_chat_messages(
                db=db, session_id=session_id, user_message=_original_message,
                assistant_message=_followup.clarification, language=language,
                response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None,
                structured_context=_followup_ctx.to_json() if _followup_ctx else None)
            yield b"data: [DONE]\n\n"
            return
        if (_followup.resolved and _followup.full_details
                and _followup.application_numbers):
            # "details of both" -- the records, not a count of them. Name the
            # files and let the ordinary application_status path answer.
            logger.info(f"Follow-up asks for the details of "
                        f"{len(_followup.application_numbers)} listed application(s)")
            message = _multi_details_message(_followup.application_numbers)
            _followup = fctx.Resolution()
        if _followup.resolved and _followup.entity == fctx.ENTITY_APPLICATION_LIST:
            import json as _json
            # Same carve-out as the non-streaming path: don't re-apply the
            # rejected-exclusion default when the carried set's ORIGINAL
            # context was itself unscoped by status.
            _include_rejected = not (
                _followup.status or (_followup_ctx and _followup_ctx.filters.get("status")))
            _sort_asked = (extract_sort_order(fctx.correct_spelling(_original_message))
                           if fctx.is_sort_fragment(_original_message) else None)
            _scoped_sd = await get_applications_by_numbers(
                db, officer, _followup.application_numbers,
                status=_followup.status, application_type=_followup.application_type,
                include_rejected=_include_rejected,
                sort_by=_sort_asked[0] if _sort_asked else None,
                sort_dir=_sort_asked[1] if _sort_asked else None)
            _scoped_text = None
            if len(_followup.application_numbers) == 1 and _WF_CUE.search(_original_message or ""):
                _scoped_text = await _single_app_workflow_answer(
                    db, officer, _original_message, _followup.application_numbers[0],
                    chat_history, language in ("ta", "tanglish"))
            if _scoped_text is None:
                _scoped_text = _scoped_list_answer(
                    _scoped_sd, _followup, _original_message, language)
            logger.info(
                f"Answered a list follow-up over {len(_followup.application_numbers)} "
                f"carried application(s)")
            yield f"data: {_json.dumps({'content': _scoped_text})}\n\n".encode('utf-8')
            # See the non-streaming twin above: a per-row field answer
            # ("show their IGRS number") already names every row in the
            # text itself, so the generic applications table beneath it is
            # pure redundancy.
            # "that applicant" / "their names" pivots the conversation to the
            # people: remember it so a bare "both" next stays on the applicants.
            _pk = fctx.field_projections(_original_message) or []
            if (any(k in ("applicant_name", "applicant_mobile") for k in _pk)
                    or _asks_applicant_focus(_original_message)) and isinstance(_scoped_sd, dict):
                _scoped_sd["detail_focus"] = "applicant"
            _is_field_projection = bool(
                getattr(_followup, "per_row_field", None)
                or fctx.field_projection(_original_message)
                or fctx.field_projections(_original_message) is not None
                or _SINGLE_PICK_FOLLOWUP_RE.search(_original_message.lower()))
            if _scoped_sd.get("applications") and not _is_field_projection:
                _td = _build_table_data("pending_applications", message,
                                        str(officer.officer_id), _scoped_sd)
                if _td:
                    yield f"data: {_json.dumps({'table_data': _td})}\n\n".encode('utf-8')
            await save_chat_messages(
                db=db, session_id=session_id, user_message=_original_message,
                assistant_message=_scoped_text, language=language,
                response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None,
                structured_context=(_followup_out_context(
                    _scoped_sd, _followup_ctx, row_peek=getattr(_followup, "row_peek", False),
                    last_peeked=getattr(_followup, "application_number", None))))
            yield b"data: [DONE]\n\n"
            return
        # The legacy list re-scope turned "what about the third" (after a field-visit
        # summary) into "...third field visits", which re-ran the whole summary. A
        # pick resolved against the rows on screen, about their visits, wins.
        if _already_rescoped and _followup.resolved and _followup.about_visit and _followup.application_number:
            message = _original_message
            _already_rescoped = False
        if (_followup.resolved and not _already_rescoped
                and (_followup.application_number or _followup.entity == fctx.ENTITY_SURVEY)):
            _resolved_message = _apply_followup_resolution(
                _original_message, _followup,
                _last_user_message(chat_history))
            if _resolved_message != message:
                logger.info(f"Follow-up resolved: '{message}' -> '{_resolved_message}'")
                message = _resolved_message

        # Direct Handler for "what does an application number look like" —
        # mirrors the non-streaming path (see there for why): application
        # numbers must never be left to the LLM to describe or invent.
        _msg_lower_fmt = message.lower()
        if any(p in _msg_lower_fmt for p in [
            "application number format", "format of application number", "format of an application number",
            "application number structure", "structure of application number", "structure of an application number",
            "application number look like", "application number pattern",
            "how are application numbers structured", "how is an application number structured",
            "விண்ணப்ப எண் வடிவம்", "விண்ணப்ப எண்ணின் அமைப்பு",
        ]) or (
            any(w in _msg_lower_fmt for w in ["application number", "app number", "விண்ணப்ப எண்"])
            and any(w in _msg_lower_fmt for w in ["format", "look like", "structure", "structured", "pattern", "வடிவம்", "அமைப்பு"])
        ):
            import json as _json_fmt
            is_ta_fmt = language == "ta"
            if is_ta_fmt:
                res_txt = (
                    "விண்ணப்ப எண் இந்த அமைப்பில் இருக்கும்: "
                    "**ஆண்டு/சேவைக்குறியீடு/மாவட்டக்குறியீடு/வரிசை எண்** "
                    "(எ.கா. 2026/0154/28/001167). "
                    "சேவைக்குறியீடு: 0154 = ISD, 0153 = NISD. "
                    "மாவட்டக்குறியீடு 2 இலக்கங்கள் (எ.கா. தூத்துக்குடிக்கு 28), "
                    "வரிசை எண் 6 இலக்கங்கள்."
                )
            else:
                res_txt = (
                    "An application number follows the format "
                    "**YEAR/SERVICE_CODE/DISTRICT_CODE/SERIAL_NUMBER** "
                    "(e.g. 2026/0154/28/001167). The service code is 0154 for ISD, "
                    "or 0153 for NISD; the district code is 2 digits "
                    "(28 for Thoothukudi); the serial number is 6 digits."
                )
            yield f"data: {_json_fmt.dumps({'content': res_txt})}\n\n".encode('utf-8')
            await save_chat_messages(
                db=db, session_id=session_id, user_message=_original_message,
                assistant_message=res_txt, language=language,
                response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None
            )
            return

        # Direct Handler for District Code reference queries (bypasses jurisdiction checks)
        # Mirrors the non-streaming path — answered from DISTRICT_NAME_MAP so the
        # LLM never invents a district code.
        _msg_lower_dc = message.lower()
        _dc_name_match = _DISTRICT_NAME_RE.search(_msg_lower_dc)
        _dc_name_match_ta = _DISTRICT_NAME_TA_RE.search(message)
        if (any(w in _msg_lower_dc for w in ["district code", "taluk code", "code of", "district_code", "குறியீடு", "மாவட்டம் கோடு"])
                or ("code" in _msg_lower_dc and _dc_name_match) or _dc_name_match_ta):
            matched_dist = None
            if _dc_name_match:
                _d_name = _dc_name_match.group(1).lower()
                matched_dist = (_d_name.title(), DISTRICT_NAME_MAP[_d_name])
            elif _dc_name_match_ta:
                _d_code = DISTRICT_TAMIL_NAME_MAP[_dc_name_match_ta.group(1)]
                matched_dist = (DISTRICT_CODE_TO_TAMIL_NAME[_d_code], _d_code)

            _dc_sp = await _dc_special(db, officer, message, matched_dist, language)
            if matched_dist or _dc_sp:
                import json as _json_dc
                d_title, d_code = matched_dist or ("", "")
                is_ta = (language in ("ta", "tanglish") or bool(_DC_TANGLISH_RE.search(_msg_lower_dc))
                         or "குறியீடு" in _msg_lower_dc or bool(_dc_name_match_ta))
                if _dc_sp:
                    res_txt = _dc_sp
                elif is_ta:
                    res_txt = f"{d_title} மாவட்டத்தின் அதிகாரப்பூர்வ குறியீடு (District Code): **{d_code}**."
                else:
                    res_txt = f"The official district code for **{d_title}** is **{d_code}**."

                yield f"data: {_json_dc.dumps({'content': res_txt})}\n\n".encode('utf-8')
                await save_chat_messages(
                    db=db, session_id=session_id, user_message=_original_message,
                    assistant_message=res_txt, language=language,
                    response_time_ms=int((time.time() - start_time) * 1000),
                    officer_id=officer.officer_id if officer else None
                )
                return

        # Security guard: password / secret queries are NEVER answered.
        if _is_sensitive_query(message):
            import json as _json_sec
            _refusal = (
                "கடவுச்சொல் தகவல்களை வழங்க அனுமதி இல்லை. உங்கள் கடவுச்சொல்லை மீட்டமைக்க "
                "உங்கள் நிர்வாகியை தொடர்பு கொள்ளவும்."
                if language in ("ta", "tanglish") else
                "I am not authorised to provide password information. "
                "Please contact your system administrator to reset your password."
            )
            yield f"data: {_json_sec.dumps({'content': _refusal})}\n\n".encode('utf-8')
            await save_chat_messages(
                db=db, session_id=session_id, user_message=_original_message,
                assistant_message=_refusal, language=language,
                response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None
            )
            return

        # Direct Handler for "who am I?" (STREAMING)
        if _is_officer_identity_question(message):
            import json as _json_id
            res_txt = await _officer_identity_reply(
                db, officer, language, _identity_facet(message))
            yield f"data: {_json_id.dumps({'content': res_txt})}\n\n".encode('utf-8')
            await save_chat_messages(
                db=db, session_id=session_id, user_message=_original_message,
                assistant_message=res_txt, language=language,
                response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None
            )
            return

        # Direct Handler for a bare format follow-up (STREAMING)
        _reformat_to = _detect_reformat_request(message)
        if _reformat_to:
            res_txt = _reformat_previous_answer(chat_history, _reformat_to, language)
            if not res_txt:
                _had_prev = bool(_last_assistant_message(chat_history))
                if not _had_prev and _reformat_to in _AMBIGUOUS_REFORMATS:
                    _reformat_to = None
                else:
                    res_txt = _cannot_reformat_reply(_reformat_to, language, _had_prev)
        if _reformat_to:
            import json as _json_tbl
            yield f"data: {_json_tbl.dumps({'content': res_txt})}\n\n".encode('utf-8')
            await save_chat_messages(
                db=db, session_id=session_id, user_message=_original_message,
                assistant_message=res_txt, language=language,
                response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None
            )
            return

        # Direct Handler for "what was my previous message?" (STREAMING)
        if _is_conversation_recall(message):
            import json as _json_recall
            res_txt = _conversation_recall_reply(message, chat_history, language)
            yield f"data: {_json_recall.dumps({'content': res_txt})}\n\n".encode('utf-8')
            await save_chat_messages(
                db=db, session_id=session_id, user_message=_original_message,
                assistant_message=res_txt, language=language,
                response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None
            )
            return

        # Direct Handler for owner photo / image retrieval requests (STREAMING)
        if _is_owner_photo_request(message):
            import json as _json_photo
            res_txt = _owner_photo_reply(language, message)
            yield f"data: {_json_photo.dumps({'content': res_txt})}\n\n".encode('utf-8')
            await save_chat_messages(
                db=db, session_id=session_id, user_message=_original_message,
                assistant_message=res_txt, language=language,
                response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None
            )
            return

        # Step 2: Parse intent to determine which DB query to run
        # The client sends no intent field, so this re-parses the previous user
        # message rather than looking for a marker that is never written.
        _prev_intent = _resolve_prev_intent(chat_history)
        if _FV_SCOPE.get() is not None:      # re-asked as a visit-table question: the "previous intent" is the table's
            _prev_intent = "field_visits"
        _unk_from_confirm = _resolve_unknown_from_confirmation(message, chat_history)
        if _unk_from_confirm:
            logger.info(f"Unknown-source confirmation resolved: '{message}' -> '{_unk_from_confirm}'")
            message = _unk_from_confirm
        _rej_widen = _resolve_rejected_widen(message, chat_history)
        if _rej_widen:
            logger.info(f"Rejected-widen follow-up resolved: '{message}' -> '{_rej_widen}'")
            message = _rej_widen
        _neg_msg = _rewrite_negated_types(message)
        if _neg_msg != message:
            logger.info(f"Negated type rewritten: '{message}' -> '{_neg_msg}'")
            message = _neg_msg
        message = _all_word_to_english(_take_sort_clause(message))
        intent = parse_intent(message, prev_intent=_prev_intent)
        _cm_strong_hit = _cm_strong(message, chat_history)
        _bare_status_hit = _bare_app_status(message, chat_history)
        _app_evidence = _app_evidence_in_scope(message, chat_history)
        _cm_override_blocked = (
            attachment_qa.is_negative_correction(message)
            or attachment_qa.is_project_relevance_request(message)
            or attachment_qa.location_reference(message)
        )
        if (_cm_strong_hit or _bare_status_hit) and _app_evidence and not _cm_override_blocked:
            logger.debug(
                "ROUTING_DEBUG path=stream msg=%r parsed_intent=%r cm_strong=%r "
                "bare_status=%r app_evidence=%r app=%r -> application_status",
                message, intent, _cm_strong_hit, _bare_status_hit, _app_evidence, extract_application_number(message))
            intent = "application_status"
        elif _cm_strong_hit or _bare_status_hit:
            logger.debug(
                "ROUTING_DEBUG path=stream msg=%r parsed_intent=%r cm_strong=%r "
                "bare_status=%r app_evidence=%r app=%r -> override SUPPRESSED (no evidence)",
                message, intent, _cm_strong_hit, _bare_status_hit, _app_evidence, extract_application_number(message))
        # See the matching non-streaming block above for why this is needed:
        # "payment information" with an active file in scope must reach
        # application_status (and _payment_composite_answer), not the
        # register-wide fee_lookup intent.
        if intent == "fee_lookup" and _PAYMENT_COMPOSITE_RE.search(message or "") and _app_evidence_in_scope(message, chat_history):
            intent = "application_status"
        # Same guard as the non-streaming path: a bare, numberless
        # survey_detail/survey_owners question with an active attachment and
        # nothing in the register to resolve is routed to general_query
        # instead of an empty "No records found". See the non-streaming
        # twin's comment for the full rationale.
        if (_is_unresolved_survey_question(intent, message, _followup_ctx)
                and await attachment_qa.any_active_documents(
                    db, officer.officer_id if officer else None, session_id)):
            logger.info(f"Bare {intent} question with an active attachment; "
                       "routing to general_query instead of the empty register lookup (stream)")
            intent = "general_query"
        if _force_general_query:
            intent = "general_query"
        # "detailed" / "explain" as a bare follow-up after a service_code_lookup
        # → re-route to service_code_lookup so the handler gives the full answer.
        _sentence_format_revert_code = _negative_sentence_format_context_code(message, _followup_ctx)
        _force_detailed_service_code_answer = bool(_sentence_format_revert_code)
        if _sentence_format_revert_code:
            # This is a FORMAT CORRECTION, not another request for a one-line
            # explanation. Convert it into a deterministic detailed lookup and
            # bypass the paraphraser below so the single-sentence answer cannot
            # be repeated by the model.
            intent = "service_code_lookup"
            message = f"explain in detail {_sentence_format_revert_code}"
            logger.info("Negative sentence-format follow-up: restoring detailed service-code answer")
        elif (intent == "general_query" and _is_explain_request(message)
                and _prev_intent == "service_code_lookup"):
            intent = "service_code_lookup"
            # The follow-up itself names no code ("explain in detail",
            # "explain briefly") -- it means the one from the previous turn.
            # Without this, _service_code_lookup_answer saw an empty code and
            # matched it as a "" prefix against all 29 codes.
            if not re.search(r"\d{3,4}", message) and not re.search(r"\b(?:isd|nisd)\b", message.lower()):
                _prev_q = _last_user_message(chat_history)
                if _prev_q:
                    message = f"{message} {_prev_q}"
        elif (intent == "general_query" and _is_explain_request(message)
                and not await attachment_qa.attachment_topic_active(db, session_id)
                and not await attachment_qa.any_active_documents(
                    db, officer.officer_id if officer else None, session_id)):
            # "explain" / "explain more" after a LISTING or a single record
            # card -- the same gap the service_code_lookup re-route above
            # closes for a definitional answer, but general enough that it
            # was falling to the generic "I'm not sure what you mean"
            # fallback for every other kind of previous answer (sometimes
            # several seconds late: "explain" alone carries no domain
            # evidence, so general_query's agent/LLM path had nothing to
            # ground a tool call in and took seconds to give up before
            # reaching that same fallback text) -- or, after a field-visit
            # listing, was answered with the wrong noun ("applications" for
            # what is actually a list of visits). A listing is ambiguous
            # (the standing rule: never infer over more than one referent),
            # so the officer is asked which record; a single one already in
            # view is described in full instead; and with nothing to
            # re-explain at all (a self-contained prior answer like a
            # comparison builds no context), the decline is immediate
            # rather than routed through the agent.
            _svc_code_ctx = (_followup_ctx.filters or {}).get("service_code") \
                if _followup_ctx and _followup_ctx.entity == fctx.ENTITY_SERVICE_CODE else None
            _single = _followup_ctx.single_application if _followup_ctx else None
            if not _single and _followup_ctx and _followup_ctx.entity == fctx.ENTITY_APPLICATION_LIST \
                    and len(_followup_ctx.application_numbers) == 1:
                _single = _followup_ctx.application_numbers[0]
            _single_survey = (_followup_ctx.survey_numbers[0]
                              if _followup_ctx and _followup_ctx.entity == fctx.ENTITY_SURVEY
                              and _followup_ctx.survey_numbers else None)
            if _svc_code_ctx:
                intent = "service_code_lookup"
                message = f"{message} {_svc_code_ctx}"
            elif _single:
                intent = "application_status"
                message = f"{message} {_single}"
            elif _single_survey:
                intent = "survey_detail"
                message = f"{message} {_single_survey}"
            elif (_followup_ctx and _followup_ctx.entity in (fctx.ENTITY_APPLICATION_LIST, fctx.ENTITY_FIELD_VISIT)
                    and len(_followup_ctx.application_numbers) > 1):
                _n = len(_followup_ctx.application_numbers)
                _is_visit_list = bool((_followup_ctx.filters or {}).get("about_visit")
                                       or _followup_ctx.entity == fctx.ENTITY_FIELD_VISIT
                                       or (_followup_ctx.intent or "").startswith("fv_")
                                       or _followup_ctx.intent == "field_visits")
                _noun_en = "field visit" if _is_visit_list else "application"
                _noun_ta = "கள ஆய்வு" if _is_visit_list else "விண்ணப்பம்"
                _clar = (f"நீங்கள் பார்த்த {_n} {_noun_ta} பதிவுகளில் எதை விளக்கமாக அறிய விரும்புகிறீர்கள்? எண்ணைச் சொல்லுங்கள்."
                         if language in ("ta", "tanglish") else
                         f"You're looking at {_n} {_noun_en}s -- which one would you like "
                         "explained? Reply with its number.")
                import json as _json_explain
                yield f"data: {_json_explain.dumps({'content': _clar})}\n\n".encode('utf-8')
                await save_chat_messages(
                    db=db, session_id=session_id, user_message=_original_message,
                    assistant_message=_clar, language=language,
                    response_time_ms=int((time.time() - start_time) * 1000),
                    officer_id=officer.officer_id if officer else None
                )
                return
            else:
                res_txt = _clarification_reply(language)
                import json as _json_explain
                yield f"data: {_json_explain.dumps({'content': res_txt})}\n\n".encode('utf-8')
                await save_chat_messages(
                    db=db, session_id=session_id, user_message=_original_message,
                    assistant_message=res_txt, language=language,
                    response_time_ms=int((time.time() - start_time) * 1000),
                    officer_id=officer.officer_id if officer else None
                )
                return
        # "how do you know <application> is SRO?" is about that file's channel, whichever verb was used
        if (intent in ("application_status", "general_query") and _ANY_APP_NUMBER_RE.search(message)
                and _CHANNEL_BASIS_RE.search(message.lower()) and _CHANNEL_NAMED_RE.search(message.lower())):
            intent = "submission_channel_check"
        logger.info(f"Parsed intent: {intent} (prev_intent={_prev_intent})")
        # Semantic check for conversational messages the keyword rules did not
        # claim ("hey buddy whats up"). Only messages no deterministic SIS rule
        # took, and only short ones with no SIS vocabulary, are ever asked.
        # "good job" / "im not satisfied with your performance" and the rest
        # of `_special_scope_kind` (capability, out-of-scope, sentiment,
        # identity, time) are ALSO answered deterministically, but only much
        # further down -- so without this guard the embedding model was
        # called anyway for every one of them (3-16s the first time, since it
        # also lazily builds the prototype embedding cache), its answer
        # thrown away the moment the deterministic check ran and overrode it.
        # Skipping the call here is the whole fix: nothing downstream changes.
        _sem_kind = None
        if (intent == "general_query" and not _is_capability_question(message)
                and not _is_out_of_scope(message) and not _special_scope_kind(message)):
            _sem_kind = await semantic_intent.classify(
                message, any(t in message.lower() for t in _DOMAIN_TERMS))
            if _sem_kind:
                intent = "greeting"

        # Direct Handler (STREAMING): answer from a file the officer attached
        # this session. Same decision as the non-streaming path, taken by the
        # same function — a refusal, a clarification and a computed CSV figure
        # are identical text in both, because only the delivery differs.
        _cm_blocked = _cm_strong(message, chat_history) and not (
            attachment_qa.is_negative_correction(message)
            or attachment_qa.is_project_relevance_request(message)
            or attachment_qa.location_reference(message)
        )
        # Same antecedent-routing decision as the non-streaming path, just
        # delivered as a single SSE chunk instead of a plain return value.
        # See `_route_negative_correction()` (FLINE-NEGATION-001).
        _neg_route = await _route_negative_correction(message, chat_history, db, session_id, language)
        if _neg_route and _neg_route["route"] == "ambiguous":
            import json as _json_negroute
            _neg_resp = _neg_route["response"]["response"]
            yield f"data: {_json_negroute.dumps({'content': _neg_resp})}\n\n".encode('utf-8')
            await save_chat_messages(
                db=db, session_id=session_id, user_message=_original_message,
                assistant_message=_neg_resp, language=language,
                response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None,
                structured_context=_neg_route.get("carry_context")
            )
            return
        if _neg_route and _neg_route["route"] == "attachment":
            # Same direct-call reasoning as the non-streaming path -- see
            # that site's comment (FLINE-NEGATION-001).
            _neg_docs = await attachment_qa.attachment_store.active_documents(
                db, officer.officer_id if officer else None, session_id)
            _att_plan = await attachment_qa._handle_negative_correction(
                db, officer, session_id, message, language, _neg_docs) if _neg_docs else None
        else:
            _force_general_negcorr = bool(_neg_route and _neg_route["route"] == "general")
            _att_plan = None if (_attachment_turn_is_not_about_a_file(message) or _cm_blocked or _force_general_negcorr) else await attachment_qa.plan_answer(
                db=db, officer=officer, session_id=session_id,
                message=attachment_qa.drop_appended_numbers(message, _RAW_MESSAGE.get()), intent=intent, language=language)
        if _att_plan is not None:
            import json as _json_upl
            _acc = ""
            if _att_plan.prompt:
                _sent = 0
                async for _chunk in call_llama_stream(_att_plan.prompt):
                    _acc += _chunk
                    # Hold back while the text could still be the refusal, so a
                    # recovery retry can replace it before the officer sees it.
                    if (_att_plan.retry_prompt and _sent == 0
                            and attachment_qa.REFUSAL_EN.startswith(_acc.strip())):
                        continue
                    yield f"data: {_json_upl.dumps({'content': _acc[_sent:]})}\n\n".encode('utf-8')
                    _sent = len(_acc)
                if _att_plan.retry_prompt and attachment_qa.is_refusal(_acc):
                    _acc = await call_llama(_att_plan.retry_prompt)
                    _sent = 0
                if _sent < len(_acc):
                    yield f"data: {_json_upl.dumps({'content': _acc[_sent:]})}\n\n".encode('utf-8')
            else:
                _acc = _att_plan.text
                yield f"data: {_json_upl.dumps({'content': _acc})}\n\n".encode('utf-8')
            _att_ctx = {
                "entity": "attachment",
                "document_ids": _att_plan.document_ids or [],
                "sources": _att_plan.sources or [],
                "attachment_kind": _att_plan.kind,
                "is_refusal": (_att_plan.kind == attachment_qa.KIND_REFUSAL),
                "prior_question": message,
                **(_att_plan.extra or {}),
            }
            await save_chat_messages(
                db=db, session_id=session_id, user_message=_original_message,
                assistant_message=_acc, language=language,
                response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None,
                structured_context=_att_ctx
            )
            return

        if _att_plan is None and not _contentless_message(_original_message) and attachment_qa.is_negative_correction(_original_message):
            _neg_data = _build_general_negative_correction_response(_original_message, chat_history, language, officer)
            if _neg_data:
                import json as _json_neg
                _res_text = _neg_data["response"]
                yield f"data: {_json_neg.dumps({'content': _res_text})}\n\n".encode('utf-8')
                await save_chat_messages(
                    db=db, session_id=session_id, user_message=_original_message,
                    assistant_message=_res_text, language=language,
                    response_time_ms=int((time.time() - start_time) * 1000),
                    officer_id=officer.officer_id if officer else None,
                    structured_context=None
                )
                return



        # Steps 2c-bis .. 2c-ter-b (STREAMING): the same deterministic guard
        # chain as the non-streaming pipeline, decided by
        # chat_pipeline.early_guard_reply(). Delivery stays here: the frame is
        # emitted first, the turn saved after.
        _early, _ssk = early_guard_reply(
            message, language, chat_history, _RAW_MESSAGE.get(),
            bool(locals().get("_followup_ctx")))
        if _early:
            import json as _json_early
            if _early.clear_session:
                await _mark_session_cleared(db, session_id, officer)
            if _early.log:
                logger.info(_early.log + (" (stream)" if _early.stream_log_suffix else ""),
                            *_early.log_args)
            _early_frame = {"content": _early.text}
            if _early.action:
                # The frontend acts on this: transcript wiped, storage cleared,
                # a fresh session opened. Sent in the same frame as the text so
                # a client that does not know the action still shows something
                # sensible.
                _early_frame["action"] = _early.action
            yield f"data: {_json_early.dumps(_early_frame)}\n\n".encode('utf-8')
            if _early.save:
                await save_chat_messages(
                    db=db, session_id=session_id,
                    user_message=_original_message, assistant_message=_early.text,
                    language=language,
                    response_time_ms=int((time.time() - start_time) * 1000),
                    officer_id=officer.officer_id if officer else None
                )
            return

        # Praise / complaint (STREAMING). See the non-streaming twin above
        # for why this calls the LLM directly rather than the general_query
        # agent: a review of the assistant has no document or tool to ground
        # an answer in, and routing it through the agent reproduced the
        # original bug (an unrelated escalation-rules explanation for a
        # complaint, a generic non-answer for praise).
        if _ssk in ("praise", "complaint"):
            import json as _json_sent
            _sent_prompt = (
                "You are the SIS assistant for the Tamil Nadu Survey Department. "
                f"An officer just sent this feedback about you, not a question about "
                f"the register: \"{message}\"\n"
                "Reply in 1-2 short sentences: acknowledge what they said, and invite "
                "them to ask a real question about an application, survey number, "
                "field visit or workflow. Do not mention escalation, performance "
                "reviews, or any SIS procedure -- nothing in the register is relevant "
                "to this message. "
                + ("Reply in Tamil." if language in ("ta", "tanglish") else "Reply in English.")
            )
            _acc = ""
            async for _chunk in call_llama_stream(_sent_prompt):
                _acc += _chunk
                yield f"data: {_json_sent.dumps({'content': _chunk})}\n\n".encode('utf-8')
            logger.info("Answered %s feedback with a direct LLM reply (no RAG/tools, stream)", _ssk)
            await save_chat_messages(
                db=db, session_id=session_id,
                user_message=_original_message, assistant_message=_acc,
                language=language,
                response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None
            )
            return

        # Step 2c-ter-c: a near-miss channel spelling (STREAMING). See the
        # non-streaming twin above.
        if ambiguous_channel_clarification(message) and not _cm_strong(message, chat_history):
            import json as _json_cch
            response_text = _channel_clarify_reply(language)
            logger.info("Asked which channel was meant instead of guessing or falling to the LLM (stream)")
            yield f"data: {_json_cch.dumps({'content': response_text})}\n\n".encode('utf-8')
            # A barrier, not a null -- see the non-streaming twin above.
            _channel_clarify_ctx = fctx.build_context(
                "channel_clarification", {"applications": []})
            await save_chat_messages(
                db=db, session_id=session_id,
                user_message=_original_message, assistant_message=response_text,
                language=language,
                response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None,
                structured_context=(_channel_clarify_ctx.to_json()
                                     if _channel_clarify_ctx else None)
            )
            return

        # Step 2b-sexies: "without a CAN number" (STREAMING). See the
        # non-streaming twin above -- always none, by design.
        if _asks_missing_can_number(message):
            import json as _json_mcn
            response_text = _missing_can_number_answer(language)
            logger.info("Answered 'applications without a CAN number' -- always none, by design (stream)")
            yield f"data: {_json_mcn.dumps({'content': response_text})}\n\n".encode('utf-8')
            await save_chat_messages(
                db=db, session_id=session_id,
                user_message=_original_message, assistant_message=response_text,
                language=language,
                response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None
            )
            return

        # Step 2c-quater: geography-master housekeeping column the projection
        # drops (STREAMING). See the non-streaming twin above.
        _ugm_key = _asked_untracked_geo_master_field(message.lower())
        if _ugm_key:
            import json as _json_ugm
            response_text = _untracked_geo_master_field_answer(
                _ugm_key, language in ("ta", "tanglish"))
            logger.info(f"Answered 'not in register' for geo-master field {_ugm_key} (stream)")
            yield f"data: {_json_ugm.dumps({'content': response_text})}\n\n".encode('utf-8')
            await save_chat_messages(
                db=db, session_id=session_id,
                user_message=_original_message, assistant_message=response_text,
                language=language,
                response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None
            )
            return
        if _asked_ip_compare(message):
            import json as _json_ipx
            response_text = await _ip_cross_comparison_answer(
                db, officer, message, _followup_ctx, language in ("ta", "tanglish"))
            logger.info("Answered a cross-application IP comparison (stream)")
            yield f"data: {_json_ipx.dumps({'content': response_text})}\n\n".encode('utf-8')
            await save_chat_messages(
                db=db, session_id=session_id,
                user_message=_original_message, assistant_message=response_text,
                language=language,
                response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None
            )
            return

        # Step 2d: Direct greeting response handling (STREAMING)
        if intent == "greeting":
            import json as _json_greeting
            msg_lower = message.lower().strip()
            # `language in ("ta", "tanglish")`, not just "ta" -- the convention
            # every other Tamil/English branch in this file follows (see
            # _is_tamil_turn). A bare `== "ta"` check missed every romanized
            # greeting with no Tamil-script word in it ("vanakkam", "nandri"),
            # so a Tanglish "vanakkam" got answered in English.
            is_tamil = language in ("ta", "tanglish") or any(w in message for w in ["வணக்கம்", "காலை", "மாலை", "நன்றி", "ஹாய்"])

            if "காலை" in msg_lower or "morning" in msg_lower:
                if is_tamil:
                    response_text = "காலை வணக்கம்! 👋 நான் உங்கள் Sub Inspector Surveyor (SIS) AI உதவியாளர். நில அளவை எண்கள், விண்ணப்பங்கள் (ISD/NISD), கள ஆய்வுகள் தொடர்பாக இன்று உங்களுக்கு எவ்வாறு உதவ முடியும்?"
                else:
                    response_text = "Good morning! 👋 I am your Sub Inspector Surveyor (SIS) AI assistant. How can I help you today with survey numbers, applications (ISD/NISD), or field visits?"
            elif "மாலை" in msg_lower or "evening" in msg_lower:
                if is_tamil:
                    response_text = "மாலை வணக்கம்! 👋 நான் உங்கள் Sub Inspector Surveyor (SIS) AI உதவியாளர். நில அளவை மற்றும் விண்ணப்பங்கள் தொடர்பான தகவல்களுக்கு இன்று உங்களுக்கு எவ்வாறு உதவ முடியும்?"
                else:
                    response_text = "Good evening! 👋 I am your Sub Inspector Surveyor (SIS) AI assistant. How can I help you with your survey work today?"
            elif _greeting_is_farewell(msg_lower):
                # A sign-off answered with "Hello! ... What can I assist you
                # with today?" reads as though nothing was heard. `greeting`
                # carries both halves of the conversation, so the handler has
                # to tell them apart even though the intent does not.
                if is_tamil:
                    response_text = "நன்றி! 👋 வணக்கம். மீண்டும் ஏதேனும் தேவைப்பட்டால் கேளுங்கள் — நல்ல நாளாக அமையட்டும்."
                else:
                    response_text = "Goodbye! 👋 Thank you — come back any time you need an application, a survey number or a field visit looked up. Have a good day."
            elif _greeting_is_thanks(msg_lower):
                if is_tamil:
                    response_text = "நல்வரவு! 😊 உங்களுக்கு மேலும் ஏதேனும் உதவி தேவைப்பட்டால் தயங்காமல் கேளுங்கள்."
                else:
                    response_text = "You're very welcome! 😊 Feel free to ask if you need anything else regarding your survey work."
            else:
                if is_tamil:
                    response_text = "வணக்கம்! 👋 நான் உங்கள் Sub Inspector Surveyor (SIS) AI உதவியாளர். நில அளவை எண்கள், விண்ணப்பங்களின் நிலை, கள ஆய்வுகள் மற்றும் பட்டா பரிமாற்றங்கள் பற்றிய கேள்விகளுக்கு உதவ தயாராக உள்ளேன். இன்று உங்களுக்கு என்ன உதவி தேவை?"
                else:
                    response_text = "Hello! 👋 I am your Sub Inspector Surveyor (SIS) AI assistant. I am here to help you manage survey applications, check document statuses, track field visits, and navigate workflow procedures. What can I assist you with today?"

            _gen = await _llm_greeting(
                message, _greeting_kind(msg_lower, _sem_kind), language in ("ta", "tanglish"))
            if _gen:
                response_text = _gen

            # Stream the greeting response
            yield f"data: {_json_greeting.dumps({'content': response_text})}\n\n".encode('utf-8')
            
            await save_chat_messages(
                db=db, session_id=session_id,
                user_message=_original_message, assistant_message=response_text,
                language=language, response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None
            )
            return

        # ── Step 2b: Jurisdiction access check (streaming) ─────────────────
        _jur_type = getattr(officer, "jurisdiction_type", "block")
        _jur_name = getattr(officer, "jurisdiction_name", "your jurisdiction")
        _JUR_LEVELS = ["block", "ward", "town", "taluk", "district"]
        _officer_level = _JUR_LEVELS.index(_jur_type) if _jur_type in _JUR_LEVELS else 0
        _INTENT_MIN_LEVEL = {
            "ward_surveys": 1, "block_surveys": 0, "jurisdiction_summary": 0,
            "active_applications_taluks": 2, "taluk_summary": 3,
            "all_surveys_in_jurisdiction": 0,
        }
        _FIELD_VISIT_INTENTS_S = {
            "fv_scheduled_this_week", "fv_date_select", "fv_nearby_pending",
            "fv_reschedule_availability", "fv_change_date", "fv_deadline_check", "fv_overdue_inspections",
            "fv_unassigned_awaiting", "fv_recently_rescheduled", "fv_scheduling_conflicts",
            "sd_additional_info", "sd_encroachment_check", "sd_sketch_readiness",
            "sd_forward_check", "sd_remarks", "application_status", "isd_processing",
            "officer_workload", "field_visits",
            "general_query", "rag", "greeting", "help", "district_code"
        }
        _msg_lower_jur = message.lower()
        _is_code_reference_query_s = any(w in _msg_lower_jur for w in ["code", "கோடு", "service code", "district code"])
        # Same carve-out as the non-streaming path: a question about the
        # officer's own jurisdiction is not a request for broader data.
        _SELF_JUR_INTENTS_S = {"jurisdiction_summary", "officer_directory", "active_applications_taluks"}
        # Same carve-out as the non-streaming path: a request to add
        # district/taluk/ward as a COLUMN on the officer's own rows, not a
        # request for another jurisdiction's data.
        # "list applications in ward 999" names a ward; "ward" is also a column
        # word, so it read as a projection and skipped the jurisdiction check.
        _is_column_projection_request_s = fctx.field_projections(message) is not None and not re.search(r"\b(ward|block)s?\s*(no\.?|number)?\s*[:#-]?\s*\d+", message.lower()) and not re.search(r"\b(?:in|of|at|under|within|for)\s+(?:my|the|this|our)\s+(?:taluk|town|district|ward|block)\b", message.lower())
        # Same carve-out as the non-streaming path: a service code's own
        # official name can carry a geography word.
        from backend.utils.helpers import find_service_codes as _jur_find_codes_s
        _names_service_code_s = bool(_jur_find_codes_s(message))
        _skip_keyword_check_s = (intent in _FIELD_VISIT_INTENTS_S) or (intent in _SELF_JUR_INTENTS_S) \
            or _is_code_reference_query_s or intent == "service_code_lookup" \
            or _is_column_projection_request_s or _names_service_code_s \
            or bool(_oldowner_qa.attributes(message)) or _cm_strong(message)
        # Same narrower flag as the non-streaming path: the field-visit /
        # self-jurisdiction exemption must not also skip the FOREIGN-ward
        # check below, or a named ward the officer does not hold is silently
        # ignored instead of refused.
        _skip_foreign_check_s = (
            _is_code_reference_query_s or intent == "service_code_lookup"
            or _is_column_projection_request_s or _names_service_code_s
            or bool(_oldowner_qa.attributes(message)) or _cm_strong(message)
        )
        _requested_broader = False
        _broader_reason = ""
        if _officer_level == 0 and not _skip_keyword_check_s:
            if any(w in _msg_lower_jur for w in ["ward", "வார்டு"]):
                _requested_broader = True; _broader_reason = "ward-level"
            elif any(w in _msg_lower_jur for w in ["taluk", "தாலுகா"]):
                _requested_broader = True; _broader_reason = "taluk-level"
            elif any(w in _msg_lower_jur for w in ["district", "மாவட்டம்"]):
                _requested_broader = True; _broader_reason = "district-level"
        elif _officer_level == 1 and not _skip_keyword_check_s:
            if any(w in _msg_lower_jur for w in ["town", "நகரம்"]):
                _requested_broader = True; _broader_reason = "town-level"
            elif any(w in _msg_lower_jur for w in ["taluk", "தாலுகா"]):
                _requested_broader = True; _broader_reason = "taluk-level"
            elif any(w in _msg_lower_jur for w in ["district", "மாவட்டம்"]):
                _requested_broader = True; _broader_reason = "district-level"
        if intent in _INTENT_MIN_LEVEL and _officer_level < _INTENT_MIN_LEVEL[intent]:
            _requested_broader = True
            _broader_reason = f"{_JUR_LEVELS[_INTENT_MIN_LEVEL[intent]]}-level"
        if not _skip_foreign_check_s:
            import json as _json_geo
            _named_ward, _named_block = await _geo_scope(
                db, session_id, message, chat_history, officer=None)
            _foreign_msg = await _foreign_geo_denial(
                db, officer, _named_ward, _named_block, language)
            if _foreign_msg:
                logger.info(
                    f"Jurisdiction access denied for officer {officer.officer_id}: "
                    f"named ward={_named_ward} block={_named_block}, intent={intent}"
                )
                yield f"data: {_json_geo.dumps({'content': _foreign_msg})}\n\n".encode('utf-8')
                await save_chat_messages(
                    db=db, session_id=session_id,
                    user_message=_original_message, assistant_message=_foreign_msg,
                    language=language, response_time_ms=int((time.time() - start_time) * 1000),
                    officer_id=officer.officer_id if officer else None
                )
                return

        if _requested_broader:
            import json as _json_mod
            _jur_level_name = _JUR_LEVELS[_officer_level]
            _access_msg = (
                f"You are assigned as a **{_jur_level_name.capitalize()}-level** SIS officer "
                f"({_jur_name}). Your access is limited to data within your assigned {_jur_level_name}.\n\n"
                f"You cannot retrieve {_broader_reason} data. "
                f"Only officers with {_broader_reason.replace('-level', '')} or higher access can view that information.\n\n"
                f"If you need {_broader_reason} data, please contact your supervising officer."
            )
            logger.info(
                f"Jurisdiction access denied for officer {officer.officer_id} "
                f"(level={_jur_level_name}): requested {_broader_reason} data, intent={intent}"
            )
            yield f"data: {_json_mod.dumps({'content': _access_msg})}\n\n".encode('utf-8')
            await save_chat_messages(
                db=db, session_id=session_id,
                user_message=_original_message, assistant_message=_access_msg,
                language=language, response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None
            )
            return

        # ── Step 2d: Application reference validation (streaming) ──────────
        # Exactly the same gate as the non-streaming path, applied before any
        # handler, RAG lookup or LLM call, so both endpoints refuse and accept
        # the same application references for the same reasons.
        _app_resolution = await resolve_application_reference(
            db, message, chat_history, officer, intent, session_id=session_id
        )
        if _app_resolution["verdict"] == "ask_number":
            # See process_chat Step 2d for why: a dead-end SIS intent with
            # exactly one attachment open and no number ever given defers to
            # that document instead of asking for a number nobody has.
            _att_fallback = await attachment_qa.try_single_document_fallback(
                db=db, officer=officer, session_id=session_id,
                message=message, language=language)
            if _att_fallback is not None:
                import json as _json_attfb
                res_txt = (await call_llama(_att_fallback.prompt)
                          if _att_fallback.prompt else _att_fallback.text)
                logger.info("Application gate deferred to the one open attachment (stream)")
                _att_ctx = ({"entity": "attachment", "document_ids": _att_fallback.document_ids,
                            **(_att_fallback.extra or {})}
                           if len(_att_fallback.document_ids or []) == 1 else None)
                yield f"data: {_json_attfb.dumps({'content': res_txt})}\n\n".encode('utf-8')
                await save_chat_messages(
                    db=db, session_id=session_id, user_message=_original_message,
                    assistant_message=res_txt, language=language,
                    response_time_ms=int((time.time() - start_time) * 1000),
                    officer_id=officer.officer_id if officer else None,
                    structured_context=_att_ctx
                )
                yield b"data: [DONE]\n\n"
                return
        if _app_resolution["verdict"] not in ("ok", "none"):
            import json as _json_badapp
            _bad_msg = build_application_gate_message(_app_resolution, officer, language)
            logger.info(
                f"Application gate refused stream: verdict={_app_resolution['verdict']} "
                f"number={_app_resolution.get('app_number') or _app_resolution.get('token')} "
                f"intent={intent} officer={officer.officer_id if officer else None}"
            )
            yield f"data: {_json_badapp.dumps({'content': _bad_msg})}\n\n".encode('utf-8')
            _gate_ctx = (fctx.build_context("application_status", {}, fallback_app_number=_app_resolution.get("app_number"))
                         if _app_resolution["verdict"] == "register_only" else None)
            await save_chat_messages(
                db=db, session_id=session_id,
                user_message=_original_message, assistant_message=_bad_msg,
                language=language, response_time_ms=int((time.time() - start_time) * 1000),
                officer_id=officer.officer_id if officer else None,
                structured_context=_gate_ctx.to_json() if _gate_ctx else None
            )
            return

        # See process_chat: the gate-confirmed application number, reused by handlers.
        _gate_app_number = (
            _app_resolution.get("app_number") if _app_resolution["verdict"] == "ok" else None
        )

        # Step 3: Execute structured database queries based on intent
        structured_data = {}
        _isd_app_no = ""
        # Answer text a fetch handler may set directly (mirrors how process_chat
        # pre-sets response_text). Picked up by _direct_answer_text below.
        _prefetch_text = ""
        _tp_marker = None  # (app_number, "new"|"previous") -- see process_chat
        _reg_marker = None  # (app_number, "place"|"date"|"reason"|"docnum")

        # First-turn visit-plan ordinal -- see process_chat.
        _vp_resolved_message = await _maybe_resolve_fresh_visit_ordinal(
            db, officer, message, intent, chat_history, session_id, language)
        if _vp_resolved_message != message:
            message = _vp_resolved_message
            intent = parse_intent(message, prev_intent=intent)

        # A sub-division plot field -- see process_chat.
        if not _prefetch_text:
            _prefetch_text = await _early_subdivision_answer(
                db, officer, message, intent, _gate_app_number, chat_history, language) or ""
            if _prefetch_text and intent != "application_status":
                intent = "application_status"
            if _prefetch_text and _HOOK_APP.get():
                app_number = _HOOK_APP.get()

        # Owner-record field the SIS register does not carry -- answer it here,
        # naming the field, before `survey_owners` dumps the parcel. Mirrors
        # process_chat's guard.
        _uof_key = _asked_untracked_owner_field(message.lower())
        _uoa_lbl = None if _uof_key else _asked_unknown_owner_attr(message.lower())
        _uof_app = (extract_application_number(message) or _gate_app_number
                    or _extract_app_number_from_context(
                        message, chat_history, allow_implicit_continuation=True))
        if _uof_key and _uof_app and not _prefetch_text:
            _prefetch_text = _untracked_owner_field_answer(
                _uof_app, _uof_key, language in ("ta", "tanglish"))
            logger.info(f"Responded 'not in register' for owner field {_uof_key} (stream)")
        elif _uoa_lbl and _uof_app and not _prefetch_text:
            _prefetch_text = _unknown_owner_attr_answer(
                _uof_app, _uoa_lbl, language in ("ta", "tanglish"))
            logger.info(f"Responded 'not a register field' for owner attr {_uoa_lbl!r} (stream)")

        # `uareg` column the survey projection drops -- name it
        # as not held before `survey_detail` answers with the generic card.
        # Mirrors process_chat, including the application-scoped fallback --
        # see the comment there for why `_UNTRACKED_SOURCE_FIELDS` is checked
        # first when an application number is in view. "igrs" is excluded
        # outright -- see the comment in process_chat for why.
        if not _prefetch_text and "igrs" not in message.lower():
            _upf_key = _asked_untracked_parcel_field(message.lower())
            if _upf_key and (not _uof_app or not _asked_untracked_source_field(message.lower())):
                if _uof_app:
                    _upf_ref = await _survey_number_for_application(db, officer, _uof_app)
                else:
                    _upf_ref = (extract_survey_number(message)
                                or await _survey_ref(db, session_id, message, chat_history))
                if _upf_ref:
                    _prefetch_text = _untracked_parcel_field_answer(
                        _upf_ref, _upf_key, language in ("ta", "tanglish"))
                    logger.info(
                        f"Responded 'not in register' for parcel field {_upf_key} (stream)")

        if not _prefetch_text and not extract_application_number(message):
            _sattr_ref = extract_survey_number(message)
            if _sattr_ref:
                _abs_label = _asked_absent_parcel_field(message.lower())
                if _abs_label:
                    _prefetch_text = _absent_parcel_field_answer(
                        _sattr_ref, _abs_label, language in ("ta", "tanglish"))
                else:
                    _prefetch_text = await _survey_attribute_answer(
                        db, officer, message, _sattr_ref, language in ("ta", "tanglish"))

        # Parcel / owner digital-signature column (document hash, PKCS#7 content,
        # signed / verified datetime, NIC dsign flag, Form 8 number) -- none
        # projected into layer 2. Name it as not held before `check_documents`
        # answers "all documents present" or the field map guesses. Mirrors
        # process_chat.
        if not _prefetch_text:
            _usig_key = _asked_untracked_signature_field(message.lower())
            if _usig_key:
                _usig_app = (extract_application_number(message) or _gate_app_number
                             or _extract_app_number_from_context(
                                 message, chat_history, allow_implicit_continuation=True))
                _usig_surv = None if _usig_app else (
                    extract_survey_number(message)
                    or await _survey_ref(db, session_id, message, chat_history))
                _usig_ref = _usig_app or (f"survey {_usig_surv}" if _usig_surv else None)
                if _usig_ref:
                    _prefetch_text = _untracked_signature_field_answer(
                        _usig_ref, _usig_key, language in ("ta", "tanglish"))
                    logger.info(
                        f"Responded 'not in register' for signature field {_usig_key} (stream)")

        # Patta-transfer / registration-detail column -- answered from
        # patta_transfers, or named as not held. Mirrors process_chat; runs
        # before "registered" routes to sale_deed_check.
        if not _prefetch_text:
            _trd_app = _uof_app or _gate_app_number or extract_application_number(message)
            _rdet = _asked_registration_detail(message.lower())
            _utf_key = _asked_untracked_transfer_field(message.lower())
            if _rdet and _trd_app:
                _rd_detail = await get_application_detail(db, _trd_app, officer=officer)
                if _rd_detail.get("found"):
                    _prefetch_text = _registration_detail_answer(
                        _rd_detail, _rdet, _trd_app, language in ("ta", "tanglish"), message)
                    _reg_marker = (_rd_detail.get("application_id") or _trd_app, _rdet)
                    logger.info(f"Answered registration detail '{_rdet}' for {_trd_app} (stream)")
            if not _prefetch_text and _utf_key and _trd_app:
                _prefetch_text = _untracked_transfer_field_answer(
                    _trd_app, _utf_key, language in ("ta", "tanglish"))
                logger.info(f"Responded 'not in register' for transfer field {_utf_key} (stream)")

        # Patta-transfer party ("who is the new / previous owner of <app>",
        # "the previous owner's relative name"). Answer before the field map
        # sends "aadhaar" to the applicant's CAN. Mirrors process_chat.
        if not _prefetch_text:
            _tp_side0 = _asks_transfer_party(message)
            _tp_app0 = _gate_app_number or extract_application_number(message)
            if _tp_side0 and _tp_app0:
                _tp_detail = await get_application_detail(db, _tp_app0, officer=officer)
                if _tp_detail.get("found"):
                    _prefetch_text = _transfer_party_answer(
                        _tp_detail, _tp_side0, message,
                        language in ("ta", "tanglish"))
                    _tp_marker = (_tp_detail.get("application_id") or _tp_app0, _tp_side0)
                    logger.info(f"Answered transfer {_tp_side0}-owner for {_tp_app0} (stream)")

        # Stage: intent dispatch -- see chat_dispatch_stream.dispatch_intent_stream().
        _stage = await dispatch_intent_stream(
            _followup_ctx=_followup_ctx, _gate_app_number=_gate_app_number,
            _isd_app_no=_isd_app_no, _prefetch_text=_prefetch_text,
            chat_history=chat_history, db=db, intent=intent, language=language,
            message=message, officer=officer, session_id=session_id,
            structured_data=structured_data,
            **_bound(locals(), ('app_number', 'i', 'is_tamil', 'msg_lower')))
        # Rebind exactly the locals the stage assigned (an unassigned one keeps its state).
        if '_an' in _stage.bound: _an = _stage.bound['_an']
        if '_field_keywords' in _stage.bound: _field_keywords = _stage.bound['_field_keywords']
        if '_isd_app_no' in _stage.bound: _isd_app_no = _stage.bound['_isd_app_no']
        if '_missing' in _stage.bound: _missing = _stage.bound['_missing']
        if '_msg_lower' in _stage.bound: _msg_lower = _stage.bound['_msg_lower']
        if '_prefetch_text' in _stage.bound: _prefetch_text = _stage.bound['_prefetch_text']
        if '_tp_marker' in _stage.bound: _tp_marker = _stage.bound['_tp_marker']
        if 'app_type' in _stage.bound: app_type = _stage.bound['app_type']
        if 'apps' in _stage.bound: apps = _stage.bound['apps']
        if 'completed' in _stage.bound: completed = _stage.bound['completed']
        if 'days' in _stage.bound: days = _stage.bound['days']
        if 'e' in _stage.bound: e = _stage.bound['e']
        if 'is_tamil' in _stage.bound: is_tamil = _stage.bound['is_tamil']
        if 'overlap_date' in _stage.bound: overlap_date = _stage.bound['overlap_date']
        if 'structured_data' in _stage.bound: structured_data = _stage.bound['structured_data']
        if 'sub_date' in _stage.bound: sub_date = _stage.bound['sub_date']
        if 'survey_no' in _stage.bound: survey_no = _stage.bound['survey_no']
        if 'taluk' in _stage.bound: taluk = _stage.bound['taluk']
        if 'today' in _stage.bound: today = _stage.bound['today']
        if 'total' in _stage.bound: total = _stage.bound['total']
        if 'working_days' in _stage.bound: working_days = _stage.bound['working_days']

        # Step 4: Get RAG context from pgvector — skip if DB data was actually found
        # A single-record lookup (one application, one survey, one owner set) carries
        # no "count" key, so requiring count > 0 classified every such answer as
        # "no database results" and pulled FAQ chunks into the prompt alongside
        # authoritative values. Count is honoured when present; otherwise any real
        # payload beyond the bookkeeping keys counts as a database result.
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
        # Skip the HTML path for interrogative questions so the LLM answers
        # conversationally — same logic as the non-streaming path above.
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
        _field_keywords = [
            "address", "mobile", "phone", "name", "status", "type",
            # bare "and the ip?" / "is that ip internal?" (ip_address, untracked)
            " ip ", " ip?", " ip.",
            "position", "received", "receive", "ward", "block",
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
        
            # PARITY with process_chat: these were missing here, so a field-visit / code question
            # in the stream got the details card
            'block code', 'district code', 'field visit', 'field visit date', 'field-visit', 'fieldvisit', 'inspection', 'kalam aaivu', 'site visit', 'taluk code', 'urban unit', 'urban unit code', 'village code', 'ward code', 'கள ஆய்வு', 'கள ஆய்வு தேதி',
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
        import json

        # Only emit table_data when there's no direct HTML response AND we are
        # not in interrogative-bypass mode (where LLM answers conversationally).
        # officer_workload excluded: its table_data never matched what
        # prepareWorkloadTable() on the frontend reads (data.workload.* vs the
        # flat keys this sends), so it rendered as an unconditional empty
        # "No records found." card under the already-complete text answer.
        if not html_response and not _bypass_html and intent not in ("last_application", "fv_visit_plan", "officer_workload"):
            table_data = _build_table_data(intent, message, str(officer.officer_id), structured_data)
            if table_data:
                table_data['language'] = language
                yield f"data: {json.dumps({'table_data': table_data})}\n\n".encode('utf-8')

        # ── Hardcoded direct answer for interrogative queries (streaming) ──
        _direct_answer_text = _prefetch_text  # text set during the fetch phase, if any
        if not html_response and _bypass_html and structured_data and structured_data.get("found", True):
            sd = structured_data
            app_no    = sd.get("application_number") or sd.get("application_id") or extract_application_number(message) or ""
            app_type  = sd.get("type", "")
            survey_no = sd.get("survey_no", "")
            # ── Check if user is asking about application but didn't provide number ──
            if not _direct_answer_text and _asking_specific_field and intent == "application_status" and not app_no:
                # User is asking a specific question about an application but didn't provide the number
                is_tamil_check = language in ("ta", "tanglish")
                if is_tamil_check:
                    _direct_answer_text = "தயவுசெய்து விண்ணப்ப எண்ணை குறிப்பிடவும். (எ.கா: 2026/0154/28/NNNNNN)"
                else:
                    _direct_answer_text = "Please provide the application number (e.g., 2026/0154/28/NNNNNN) so I can help you with that information."
                logger.info("User asked about application field without providing app number - prompted for app number")

            if not _direct_answer_text and intent == "application_status" and app_no:
                _direct_answer_text = (_jurisdiction_yn_answer(_msg_lower, app_no, sd, language in ("ta", "tanglish"))
                                       or _payment_composite_answer(_msg_lower, app_no, sd, language in ("ta", "tanglish"))
                                       or _fv_detail_answer(_msg_lower, sd, app_no, language in ("ta", "tanglish"))
                                       or _status_yes_no(_msg_lower, app_no, sd.get("status"), sd.get("stage") or sd.get("current_stage"),
                                                         language in ("ta", "tanglish")))

            # Workflow-history questions, same priority/reasoning as the non-stream path above.
            if not _direct_answer_text and intent == "application_status" and app_no:
                _direct_answer_text = await _workflow_history_answer(db, officer, message, app_no, language in ("ta", "tanglish"))

            # ── Specific field extraction for application_status queries (stream) ──
            if not _direct_answer_text and intent == "application_status" and app_no:
                # Defined block-level: the NISD/ISD branch below is a sibling of
                # the pending/overdue branch and read is_tamil before it was set
                # on that path (UnboundLocalError). process_chat sets it here too.
                is_tamil = language in ("ta", "tanglish")
                # Check for pending duration / how long pending / overdue questions (higher priority)
                # "the first overdue visit" names a ROW, not a second question
                # about overdue status -- see `_ORDINAL_VISIT_PHRASE_RE`.
                _msg_lower_no_row_ref = _ORDINAL_VISIT_PHRASE_RE.sub(" ", _msg_lower)
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
                                _direct_answer_text = f"விண்ணப்பம் {app_no} நிலுவையில் இல்லை — இது நிராகரிக்கப்பட்டது."
                            else:
                                _direct_answer_text = f"Application {app_no} is no longer pending — it was rejected."
                        elif app_status_str in ["completed", "approved", "closed"]:
                            if is_tamil:
                                _direct_answer_text = f"விண்ணப்பம் {app_no} நிலுவையில் இல்லை — இது ஏற்கனவே முடிவடைந்தது/அங்கீகரிக்கப்பட்டது."
                            else:
                                _direct_answer_text = f"Application {app_no} is no longer pending — it has been completed and approved."
                        else:
                            _direct_answer_text = _age_sla_answer(app_no, sd, today, is_tamil)
                        logger.info(f"Responded pending duration ({app_sub_days} days) for {app_no}")

                    # Overdue questions or default overdue calculations
                    elif (fv_days_overdue is not None and fv_days_overdue > 0
                          and str(fv_info.get("status") or "").lower() != "completed" and not fv_info.get("actual_date")
                          and app_status_str not in ("completed", "approved", "closed", "rejected")):
                        if is_tamil:
                            _direct_answer_text = f"விண்ணப்பம் {app_no}-ன் கள ஆய்வு ({str(fv_date_str)[:10]}) {fv_days_overdue} நாட்கள் தாமதமாக (overdue) உள்ளது."
                        else:
                            _direct_answer_text = f"Application {app_no}: The field visit (scheduled for {str(fv_date_str)[:10]}) is **{fv_days_overdue} days overdue** (as of today, {today.isoformat()})."
                        if str(sd.get('status','')).lower() in ('pending','in_progress','escalated'):
                            _direct_answer_text = (_age_sla_answer(app_no, sd, today, is_tamil) + (
                                f" கள ஆய்வு {str(fv_date_str)[:10]} அன்று திட்டமிடப்பட்டது ({fv_days_overdue} நாட்கள் முன்பு); முடிந்ததாகப் பதிவில்லை." if is_tamil else
                                f" The visit was scheduled for {str(fv_date_str)[:10]} ({fv_days_overdue} days ago) and is not recorded as completed."))
                        logger.info(f"Responded with {fv_days_overdue} days overdue for {app_no}")
                    elif sd.get("is_overdue") and app_sub_days is not None:
                        _direct_answer_text = _age_sla_answer(app_no, sd, today, is_tamil)
                    elif app_status_str == "rejected":
                        if is_tamil:
                            _direct_answer_text = f"'தாமதம்' (Overdue) {app_no}-க்குப் பொருந்தாது — இது நிராகரிக்கப்பட்டு முடிவடைந்துவிட்டது."
                        else:
                            _direct_answer_text = f"'Overdue' does not apply to {app_no} -- it was rejected and is closed, not an open file being tracked against a deadline."
                        logger.info(f"Responded rejected/not applicable for {app_no}")
                    elif app_status_str in ["completed", "approved", "closed"]:
                        if is_tamil:
                            _direct_answer_text = f"விண்ணப்பம் {app_no} தாமதமாக இல்லை — இது ஏற்கனவே முடிவடைந்தது/அங்கீகரிக்கப்பட்டது."
                        else:
                            _direct_answer_text = f"Application {app_no} is NOT overdue. It has been completed and approved."
                        logger.info(f"Responded completed/not overdue for {app_no}")
                    elif fv_days_until is not None:
                        if fv_days_until == 0:
                            if is_tamil:
                                _direct_answer_text = f"விண்ணப்பம் {app_no} தாமதமாக இல்லை. கள ஆய்வு இன்று ({str(fv_date_str)[:10]}) திட்டமிடப்பட்டுள்ளது."
                            else:
                                _direct_answer_text = f"Application {app_no} is NOT overdue. The field visit is scheduled for TODAY ({str(fv_date_str)[:10]})."
                        else:
                            if is_tamil:
                                _direct_answer_text = f"விண்ணப்பம் {app_no} தாமதமாக இல்லை. கள ஆய்வு {str(fv_date_str)[:10]} அன்று திட்டமிடப்பட்டுள்ளது ({fv_days_until} நாட்களில்)."
                            else:
                                _direct_answer_text = f"Application {app_no} is NOT overdue. The field visit is scheduled for {str(fv_date_str)[:10]} (in {fv_days_until} days)."
                        logger.info(f"Responded upcoming field visit in {fv_days_until} days for {app_no}")
                    elif app_sub_days is not None:
                        _direct_answer_text = _age_sla_answer(app_no, sd, today, is_tamil)
                    else:
                        if is_tamil:
                            _direct_answer_text = f"விண்ணப்பம் {app_no} தாமதமாக இல்லை (காலக்கெடுவிற்குள் உள்ளது)."
                        else:
                            _direct_answer_text = f"Application {app_no} is currently on schedule and not overdue."
                        logger.info(f"Responded not overdue for {app_no}")

                # Check for NISD/ISD type questions next
                elif ("nisd" in _msg_lower or "isd" in _msg_lower):
                    app_type_value = sd.get("type", "N/A")
                    if is_tamil:
                        _direct_answer_text = f"விண்ணப்பம் {app_no} வகை: {app_type_value}"
                    else:
                        _direct_answer_text = f"Application {app_no} is of type: {app_type_value}"
                    logger.info(f"Responded with application type '{app_type_value}' for {app_no}")
                
            # Map user keywords to structured_data fields (English + Tamil)
            # PARITY: do NOT gate on _asking_specific_field here. That list
            # (_field_keywords, 150 entries) is narrower than _field_map (230
            # entries) — "area sq", "பரப்பளவு", "csc charge" and friends are in the
            # map but not the keyword list, so gating on it made the streaming path
            # skip the lookup and fall through to the generic application summary
            # while /chat answered from the database. process_chat gates only on
            # "no answer yet", and _bypass_html already restricts this block to
            # interrogative application_status queries.
            if not _direct_answer_text and app_no:
                _field_map = {
                    # PARITY with process_chat's _field_map — these keys existed only
                    # on the non-streaming path, so area / charge / source / camp /
                    # workflow questions fell through to the LLM when streaming.
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
                    # Source / workflow
                    "source": ("source_name", "Source Name"),
                    "source_code": ("source_code", "Source Code"),
                    "source_name": ("source_name", "Source Name"),
                    "ஆதாரம்": ("source_name", "Source Name"),
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
                    "பயனர்": ("user_id", "User ID"),
                    # Address
                    "address": ("applicant_address", "Address"),
                    "முகவரி": ("applicant_address", "Address"),
                    "virivu": ("applicant_address", "Address"),
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
                    "முன்னுரிமை": ("priority_flag", "Priority"),
                    "munnurimai": ("priority_flag", "Priority"),
                    "urgent": ("priority_flag", "Priority"),
                    # Overdue
                    "overdue": ("is_overdue", "Overdue"),
                    "காலதாமத": ("is_overdue", "Overdue"),
                    "kaalathamadha": ("is_overdue", "Overdue"),
                    "delayed": ("is_overdue", "Overdue"),
                    # Aadhaar / CAN mapping (schema uses CAN number as citizen identifier)
                    "aadhaar": ("can_number", "CAN Number"),
                    "aadhar": ("can_number", "CAN Number"),
                    "adhaar": ("can_number", "CAN Number"),
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
                    "role id": ("role_id", "Role ID"),
                    "role_id": ("role_id", "Role ID"),
                    "role": ("role_id", "Role ID"),
                    "பங்கு ஐடி": ("role_id", "Role ID"),
                    # Service & Department
                    "service code": ("service_code", "Service Code"),
                    "service_code": ("service_code", "Service Code"),
                    "workflow state": ("workflow_state", "Workflow State"),
                    "source code": ("source_code", "Source Code"),
                    "source name": ("source_name", "Source Name"),
                    # Reason
                    "reason": ("declared_reason", "Declared Reason"),
                    "declared reason": ("declared_reason", "Declared Reason"),
                    "purpose": ("declared_reason", "Declared Reason"),
                    "காரணம்": ("declared_reason", "Declared Reason"),
                    "kaaranam": ("declared_reason", "Declared Reason"),
                    "karanum": ("declared_reason", "Declared Reason"),
                    # Priority & Overdue
                    "high priority": ("priority_flag", "Priority"),
                    "priority flag": ("priority_flag", "Priority"),
                    "is overdue": ("is_overdue", "Overdue"),
                    "is it overdue": ("is_overdue", "Overdue"),
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
                _stage_labels_s = {
                    "SIS": "Sub Inspector Surveyor (SIS) — currently under field verification",
                    "SD": "Senior Draughtsman (SD) — forwarded for sketch/approval",
                    "DIS": "Deputy Inspector Surveyor (DIS) — under DIS review",
                    "TAHSILDAR": "Zonal Level Tahsildar (ZDT) — holds the DSC; patta order pending sign-off",
                    "COMPLETED": "Completed — patta order issued",
                    "REJECTED": "Rejected",
                }
                # Tamil stage labels (streaming)
                _stage_labels_ta_s = {
                    "SIS": "துணை ஆய்வாளர் (SIS) — தற்போது கள சரிபார்ப்பில் உள்ளது",
                    "SD": "மூத்த வரைவாளர் (SD) — வரைபட அங்கீகாரத்திற்கு அனுப்பப்பட்டது",
                    "DIS": "மாவட்ட ஆய்வாளர் (DIS) — DIS மதிப்பாய்வில் உள்ளது",
                    "TAHSILDAR": "வலய நிலை தாசில்தார் (ZDT) — DSC கையொப்பம், பட்டா ஆணை நிலுவையில்",
                    "COMPLETED": "முடிந்தது — பட்டா ஆணை வழங்கப்பட்டது",
                    "REJECTED": "நிராகரிக்கப்பட்டது",
                }
                
                # "When was this approved / rejected / closed?" -- answered from
                # the workflow hop that closed the file. The field map sends
                # every "when" and every "date" to submission_date, so without
                # this gate the answer is the filing date, which is not vague
                # but wrong. Runs first, so the field map never sees it.
                # PARITY with process_chat.
                _msg_lower_nonum = re.sub(r"\s+", " ", _msg_lower.replace((app_no or "").lower(), " "))
                _sdq_text = await _subdivision_answer(
                    db, officer, message, sd, app_no, language in ("ta", "tanglish"))
                if _sdq_text:
                    response_text = _sdq_text
                    logger.info(f"Responded with sub-division record answer for {app_no}")
                    all_matches = []
                elif _asked_decision_date(_msg_lower):
                    sd["_asked"] = _msg_lower
                    _direct_answer_text = _decision_date_answer(
                        sd, app_no, language in ("ta", "tanglish"))
                    logger.info(f"Responded with decision date for {app_no}")
                    all_matches = []
                elif _asked_street_name(_msg_lower):
                    _direct_answer_text = _street_name_answer(
                        sd, app_no, language in ("ta", "tanglish"))
                    logger.info(f"Responded with street-name-not-tracked for {app_no}")
                    all_matches = []
                elif _asked_name_part(_msg_lower):
                    _direct_answer_text = _name_part_answer(
                        sd, app_no, language in ("ta", "tanglish"))
                    logger.info(f"Responded with name-part answer for {app_no}")
                    all_matches = []
                elif _asked_day_of_week(_msg_lower):
                    _direct_answer_text = _day_of_week_answer(
                        sd, app_no, _msg_lower, language in ("ta", "tanglish"))
                    logger.info(f"Responded with day-of-week answer for {app_no}")
                    all_matches = []
                elif _asked_month_name(_msg_lower):
                    _direct_answer_text = _month_name_answer(
                        sd, app_no, _msg_lower, language in ("ta", "tanglish"))
                    logger.info(f"Responded with month-name answer for {app_no}")
                    all_matches = []
                elif _asked_area_in_unit(_msg_lower) and _area_unit_answer(
                        sd, app_no, _asked_area_in_unit(_msg_lower),
                        language in ("ta", "tanglish")):
                    _direct_answer_text = _area_unit_answer(
                        sd, app_no, _asked_area_in_unit(_msg_lower),
                        language in ("ta", "tanglish"))
                    logger.info(f"Converted area to {_asked_area_in_unit(_msg_lower)} for {app_no}")
                    all_matches = []
                elif _asked_untracked_wf_field(_msg_lower_nonum):
                    _utf_key = _asked_untracked_wf_field(_msg_lower_nonum)
                    _direct_answer_text = (_ip_answer(sd, app_no, language in ("ta", "tanglish"))
                                           if _utf_key == "ip_address" else
                                           _untracked_wf_field_answer(
                                               app_no, _utf_key, language in ("ta", "tanglish")))
                    logger.info(f"Responded 'not in register' for {_utf_key} on {app_no}")
                    all_matches = []
                elif _asked_untracked_source_field(_msg_lower_nonum):
                    _usf_key = _asked_untracked_source_field(_msg_lower_nonum)
                    _direct_answer_text = _untracked_source_field_answer(
                        app_no, _usf_key, language in ("ta", "tanglish"))
                    logger.info(f"Responded 'not in register' for source field {_usf_key} on {app_no}")
                    all_matches = []
                elif _asked_untracked_appinfo_field(_msg_lower_nonum):
                    _uaf_key = _asked_untracked_appinfo_field(_msg_lower_nonum)
                    _direct_answer_text = _untracked_appinfo_field_answer(
                        app_no, _uaf_key, language in ("ta", "tanglish"))
                    logger.info(f"Responded 'not in register' for appinfo field {_uaf_key} on {app_no}")
                    all_matches = []
                elif _asked_submission_channel_field(_msg_lower_nonum):
                    _direct_answer_text = _render_submission_channel_answer(
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
                # PARITY with process_chat.
                _FV_FIELD_KEYS = {"field_visit_scheduled", "field_visit_date"}
                _asked_field_visit = any(w in _msg_lower for w in (
                    "field visit", "field-visit", "fieldvisit", "site visit",
                    "inspection", "கள ஆய்வு"))
                if (all_matches and _asked_field_visit
                        and any(k in _FV_FIELD_KEYS for k, _l, _kw in all_matches)
                        and all(k in _FV_FIELD_KEYS or k == "submission_date"
                                for k, _l, _kw in all_matches)):
                    _direct_answer_text = _field_visit_answer(
                        sd, app_no, language in ("ta", "tanglish"))
                    logger.info(f"Responded with field-visit answer for {app_no}")
                    all_matches = []

                if len(all_matches) > 1:
                    is_tamil_s = language in ("ta", "tanglish")
                    labels_to_use = _stage_labels_ta_s if is_tamil_s else _stage_labels_s
                    ta_labels_s = {
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
                        if field_key in _NAME_FIELD_KEYS and isinstance(value, str) and value:
                            value = translit.annotate_name(
                                value, translit.wants_script(_msg_lower, language))
                        if field_key == "fee_amount" and value not in (None, ""):
                            value = _money(value)
                        if field_key == "sis_recommendation" and isinstance(value, str) and value:
                            value = _sis_recommendation_text(value, is_tamil)
                        if value is None or value == "":
                            value = "N/A"
                        elif isinstance(value, bool):
                            value = ("ஆம்" if value else "இல்லை") if is_tamil_s else ("Yes" if value else "No")
                        elif field_key == "stage" and isinstance(value, str):
                            value = labels_to_use.get(value.upper(), value)
                        display_label = ta_labels_s.get(field_label, field_label) if is_tamil_s else field_label
                        items.append(f"• **{display_label}**: {value}")
                    
                    header = f"விண்ணப்பம் {app_no} விவரங்கள்:" if is_tamil_s else f"Details for application {app_no}:"
                    _direct_answer_text = header + "\n" + "\n".join(items)
                    logger.info(f"Responded with multiple fields ({len(all_matches)}) for {app_no} (streaming)")
                elif len(all_matches) == 1:
                    field_key, field_label, matched_kw = all_matches[0]
                    value = sd.get(field_key)
                    # A fee reads as money, not as a bare float: "600.0" -> "₹600.00".
                    if field_key == "fee_amount" and value not in (None, ""):
                        value = _money(value)
                    # "Y" / "N" reads as a recommendation, not a bare letter.
                    if field_key == "sis_recommendation" and isinstance(value, str) and value:
                        value = _sis_recommendation_text(value, language in ("ta", "tanglish"))
                    # PARITY with process_chat: a field-visit field is answered
                    # in words, not as "Field Visit Scheduled: No".
                    if field_key in ("field_visit_scheduled", "field_visit_date"):
                        _direct_answer_text = _field_visit_answer(
                            sd, app_no, language in ("ta", "tanglish"))
                        logger.info(f"Responded with field-visit answer for {app_no}")
                    elif value is not None and value != "":
                        if isinstance(value, bool):
                            value = "Yes" if value else "No"
                        # A name is stored in one script only. If the officer is
                        # working in the other, carry a labelled transliteration
                        # beside the name of record (never instead of it).
                        if field_key in _NAME_FIELD_KEYS and isinstance(value, str):
                            value = translit.annotate_name(
                                value, translit.wants_script(_msg_lower, language))
                        # Expand stage codes to human-readable labels
                        if field_key == "stage" and isinstance(value, str):
                            # Use Tamil labels if query was in Tamil or Tanglish
                            is_tamil_s = language in ("ta", "tanglish")
                            labels_to_use = _stage_labels_ta_s if is_tamil_s else _stage_labels_s
                            readable = labels_to_use.get(value.upper(), value)
                            _direct_answer_text = (
                                f"Application {app_no} is currently at: {readable}." if not is_tamil_s
                                else f"விண்ணப்பம் {app_no} தற்போது: {readable}."
                            )
                        # Extract year from date if user specifically asked for year
                        elif field_key == "submission_date" and any(kw in _msg_lower for kw in ["year", "ஆண்டு", "aandu", "annu"]):
                            # User asked for year specifically - extract year from date
                            try:
                                if isinstance(value, str) and len(value) >= 4:
                                    year = value[:4]  # Extract YYYY from YYYY-MM-DD format
                                    is_tamil_s = language in ("ta", "tanglish")
                                    if is_tamil_s:
                                        _direct_answer_text = f"{app_no} சமர்ப்பிக்கப்பட்ட ஆண்டு: {year}"
                                    else:
                                        _direct_answer_text = f"Application {app_no} was submitted in the year: {year}"
                                    logger.info(f"Extracted year {year} from submission_date for {app_no} (streaming)")
                                else:
                                    _direct_answer_text = f"The {field_label} for {app_no} is: {value}"
                                    logger.info(f"Could not extract year (streaming), value type: {type(value)}, value: {value}")
                            except Exception as year_ex_s:
                                logger.error(f"Error extracting year (streaming): {year_ex_s}", exc_info=True)
                                _direct_answer_text = f"The {field_label} for {app_no} is: {value}"
                        else:
                            # Provide response in Tamil if query was in Tamil or Tanglish
                            is_tamil_s = language in ("ta", "tanglish")
                            if is_tamil_s:
                                # Tamil field label mapping
                                ta_labels_s = {
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
                                ta_field_label_s = ta_labels_s.get(field_label, field_label)
                                # More natural Tamil phrasing based on field type
                                if field_key == "applicant_name":
                                    _direct_answer_text = f"{app_no} விண்ணப்பதாரரின் பெயர்: {value}"
                                elif field_key == "status":
                                    _direct_answer_text = f"{app_no} நிலை: {value}"
                                elif field_key == "serial_number":
                                    _direct_answer_text = f"விண்ணப்பம் {app_no}-ன் வரிசை எண்: {value}"
                                else:
                                    _direct_answer_text = f"{app_no} {ta_field_label_s}: {value}"
                            else:
                                if field_key == "serial_number":
                                    _direct_answer_text = f"The serial number for application {app_no} is: {value}"
                                else:
                                    _direct_answer_text = f"The {field_label} for {app_no} is: {value}"
                    else:
                        _direct_answer_text = _missing_field_answer(
                            sd, app_no, field_key, field_label,
                            language in ("ta", "tanglish"))
                    # A bare code answers nothing on its own: the officer asked
                    # what the file is, so the code is stated with its meaning.
                    if field_key == "service_code" and _direct_answer_text:
                        _svc_line = service_code_one_liner(
                            value, language in ("ta", "tanglish"))
                        if _svc_line:
                            _direct_answer_text = f"{_direct_answer_text}\n{_svc_line}"
                    logger.info(f"Responded with specific field '{field_label}' for {app_no} (matched: '{matched_kw}')")

        if html_response:
            # Send the whole HTML in one SSE chunk — no LLM latency
            logger.info("Responding with direct HTML (LLM bypassed for stream)")
            yield f"data: {json.dumps({'content': html_response})}\n\n".encode('utf-8')
            full_response_text = html_response
        elif _direct_answer_text:
            logger.info("Responding with direct Python answer (stream)")
            yield f"data: {json.dumps({'content': _direct_answer_text})}\n\n".encode('utf-8')
            full_response_text = _direct_answer_text
        else:
            # Step 6: Build prompt and stream LLM response or use hardcoded responses
            full_prompt = build_prompt(message, rag_context, structured_data, language, chat_history,
                                       direct_answer=_bypass_html)

        # Step 6: Stream LLM Response / hardcoded intent responses
        # Preserve full_response_text if already set by HTML or direct-answer path
        if not html_response and not _direct_answer_text:
            full_response_text = ""
        import json
        
        logger.info("Starting LLM stream...")
        chunk_count = 0
        
        if html_response or _direct_answer_text:
            # Content and full_response_text were already set above (the
            # html_response / _direct_answer_text branch) -- this used to
            # also do `full_response_text = chunk` and re-yield using a
            # `chunk` variable that is never assigned on this path, which
            # crashed every deterministic HTML/direct-answer stream with
            # UnboundLocalError right after the real content had already
            # gone out. That crash was caught by the outer except, which
            # both appended a visible "I apologize..." error after an
            # otherwise-correct answer and skipped whatever ran afterward to
            # persist this turn's follow-up context -- so the very next
            # follow-up question saw no context at all.
            pass
        elif intent == "active_applications_taluks":
            total = structured_data.get("total_active", 0)
            counts = structured_data.get("taluk_counts", {})
            if total > 0:
                counts_str = ", ".join(f"{count} in {taluk}" for taluk, count in counts.items())
                chunk = f"{total} active applications: {counts_str}."
            else:
                chunk = "0 active applications."
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')
        elif intent == "highest_priority_applications":
            apps = structured_data.get("applications", []) if structured_data else []
            count = len(apps)
            if count > 0:
                app_numbers = [a.get("application_number") for a in apps[:5]]  # Show first 5
                preview = ", ".join(app_numbers)
                if count > 5:
                    preview += f" and {count - 5} more"
                chunk = f"Found {count} high priority application(s): {preview}. Priority is based on overdue status or manual flagging."
            else:
                chunk = "No high priority applications found. All applications are within normal processing timeframes."
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')
        elif intent == "escalation_check":
            approaching = structured_data.get("applications", []) if structured_data else []
            total = structured_data.get("total_approaching", 0) if structured_data else 0
            overdue = structured_data.get("overdue_count", 0) if structured_data else 0
            if total == 0:
                chunk = "No applications are currently approaching the escalation threshold."
            else:
                critical = [a for a in approaching if "Critical" in a.get("urgency", "")]
                warning = [a for a in approaching if "Warning" in a.get("urgency", "")]
                parts = []
                if overdue:
                    parts.append(f"{overdue} already overdue")
                if critical:
                    parts.append(f"{len(critical)} critical (1–2 days remaining)")
                if warning:
                    parts.append(f"{len(warning)} warning (3–5 days remaining)")
                summary = ", ".join(parts) if parts else f"{total} total"
                chunk = (
                    f"Found {total} application(s) approaching or past the 15-working-day escalation threshold: "
                    f"{summary}. See the table below for details."
                )
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')
        elif intent == "assigned_today":
            count = structured_data.get("count", 0)
            chunk = f"{count} applications were assigned today."
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')
        elif intent == "immediate_action":
            apps = structured_data.get("apps", [])
            if apps:
                chunk = f"{', '.join(apps)} require immediate action based on pending deadlines."
            else:
                chunk = "No applications require immediate action today."
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')
        elif intent == "awaiting_field_visit":
            count = structured_data.get("count", 0)
            chunk = f"{count} applications are awaiting field inspection."
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')
        elif intent == "fee_lookup":
            chunk = _fee_lookup_text(structured_data, language in ("ta", "tanglish"))
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')
        elif intent == "fee_summary":
            fs = (structured_data or {}).get("fee_summary", {})
            scope = fs.get("period_label") or ""
            scope = f" {scope}" if scope else ""
            a_type = f" {fs.get('application_type')}" if fs.get("application_type") else ""
            if not fs.get("total_applications"):
                chunk = (f"No{a_type} applications{scope} in your jurisdiction, "
                         "so there is no fee to total.")
            else:
                chunk = (
                    f"\u20b9{fs.get('total_fee', 0):,.2f} was collected on{a_type} "
                    f"applications{scope} in your jurisdiction \u2014 "
                    f"{fs.get('with_fee', 0)} of {fs.get('total_applications', 0)} "
                    f"application(s) carry a fee record, {fs.get('without_fee', 0)} carry none."
                    + (f" Fees are recorded for ISD/NISD files only; "
                       f"{fs.get('other_service_code_applications')} application(s) under other "
                       f"service codes have no fee record and are not included."
                       if fs.get('other_service_code_applications') else "")
                )
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')
        elif intent == "workload_by_type":
            isd = structured_data.get("ISD", 0)
            nisd = structured_data.get("NISD", 0)
            chunk = f"ISD – {isd} applications, NISD – {nisd} applications."
        elif intent == "applications_by_block":
            chunk = _render_applications_by_block(structured_data, language)
        elif intent == "officer_directory":
            chunk = _render_ward_directory(structured_data, language)
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')
        elif intent == "completion_rate":
            completed = structured_data.get("completed", 0) if structured_data else 0
            total = structured_data.get("total", 0) if structured_data else 0
            rate = structured_data.get("rate", 0) if structured_data else 0
            scope = structured_data.get("scope", "overall") if structured_data else "overall"
            if total == 0:
                chunk = f"No applications found for {scope}."
            else:
                chunk = (
                    f"Your application completion percentage {scope}: "
                    f"{rate}% — {completed} out of {total} applications "
                    f"have been completed (approved or rejected), across every service code."
                )
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')
        elif intent == "pending_longest":
            apps = structured_data.get("apps", [])
            days = structured_data.get("days", 0)
            if apps:
                chunk = f"Application Nos. {', '.join(apps)} have been pending for more than {days} days."
            else:
                chunk = "No pending applications."
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')
        elif intent == "is_nisd_or_isd":
            if not structured_data or not structured_data.get("found", True):
                chunk = structured_data.get("message", "Please specify an application number (e.g., 2026/0154/28/NNNNNN) to check if it is NISD or ISD.") if structured_data else "Please specify an application number (e.g., 2026/0154/28/NNNNNN) to check if it is NISD or ISD."
            else:
                app_type = structured_data.get("type", "ISD")
                survey_no = structured_data.get("survey_no") or "not recorded"
                subdivs = structured_data.get("included_subdivisions", "")
                subdiv_count = len(subdivs.split(",")) if subdivs and subdivs != "None" else None
                if app_type == "ISD":
                    chunk = (f"ISD — application declares sub-division into {subdiv_count} plots under survey no. {survey_no}."
                             if subdiv_count else f"ISD — a sub-division of survey no. {survey_no}; the plots are not recorded yet.")
                elif app_type == "NISD":
                    chunk = f"NISD — application is for transfer of entire survey/patta without subdivision under survey no. {survey_no}."
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')
        elif intent == "submission_channel_check":
            chunk = _render_submission_channel_answer(structured_data, language, message)
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')
        elif intent == "can_apply_check":
            chunk = _render_can_apply_answer(structured_data, language, message)
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')
        elif intent == "check_documents":
            _is_ta_docs = language in ("ta", "tanglish")
            if not structured_data or not structured_data.get("found", True):
                chunk = "விண்ணப்பம் கிடைக்கவில்லை." if _is_ta_docs else "Application not found."
            else:
                # the attachment extract, not the synthesised application_documents (see process_chat)
                _dapp = (structured_data.get("application_number") or extract_application_number(message)
                         or _gate_app_number)
                chunk = (await _othercode_detail.attachments(db, _dapp, _is_ta_docs) if _dapp else
                         ("எந்த விண்ணப்பம் என்று குறிப்பிடவும்." if _is_ta_docs else "Which application? Give its number."))
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')
        elif intent == "check_sale_deed":
            if not structured_data or not structured_data.get("found", True):
                chunk = "Application not found."
            else:
                deed_no = structured_data.get("sale_deed_number") or "N/A"
                sub_date = structured_data.get("submission_date") or "the submission date (not recorded)"
                if structured_data.get("sale_deed_registered"):
                    chunk = f"Yes, deed no. {deed_no} matches Sub-Registrar's registered index as of {sub_date}."
                else:
                    chunk = "No match found — flag to Sub-Registrar's office before proceeding."
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')
        
        elif intent == "joint_owner_check":
            if not structured_data or not structured_data.get("found", True):
                chunk = (structured_data or {}).get("message", "Please provide an application number or survey number")
            else:
                chunk = _render_joint_owner_answer(structured_data, language)
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')

        elif intent == "fv_visit_plan":
            chunk = _render_visit_plan_answer(structured_data, language)
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')

        elif intent == "last_application":
            chunk = _render_last_application_answer(structured_data, language)
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')

        elif intent == "application_status":
            if not structured_data:
                chunk = build_app_not_found_message({}, language)
            elif structured_data.get("no_listing_to_expand"):
                # "details of all of them" with nothing listed before it.
                chunk = ASK_FOR_APP_NUMBER[language if language in ASK_FOR_APP_NUMBER else "en"]
            elif "multi_applications" in structured_data:
                # Multiple apps were requested — build a summary line listing all of them
                _details_list = structured_data["multi_applications"]
                _found = [d for d in _details_list if d.get("found", True)]
                _missing = [d for d in _details_list if not d.get("found", True)]
                if not _found:
                    chunk = build_app_not_found_message(_missing[0] if _missing else {}, language)
                elif structured_data.get("detail_focus") == "applicant":
                    # The officer asked about the applicants — the table already
                    # renders name/mobile/address for each application, so we
                    # only emit a short intro line here instead of a verbose
                    # text dump that duplicates the table contents.
                    chunk = _applicant_card_text(
                        _found, language in ("ta", "tanglish"))
                else:
                    _summaries = []
                    for _d in _found:
                        _an = _d.get("application_number", "N/A")
                        _st = (_d.get("status") or "N/A").capitalize()
                        _sg = _d.get("stage", "N/A")
                        _summaries.append(f"{_an} (Status: {_st}, Stage: {_sg})")
                    chunk = f"Here are the details for {len(_found)} application(s): {'; '.join(_summaries)}."
                    _listed_total = structured_data.get("listed_total") or 0
                    if _listed_total > len(_details_list):
                        chunk += (
                            f" That is the first {len(_details_list)} of {_listed_total} —"
                            f" ask for the rest by application number.")
                    if _missing:
                        _missing_nos = ", ".join(d.get("searched_number", "N/A") for d in _missing)
                        chunk += f" Not found: {_missing_nos}."
            elif not structured_data.get("found", True):
                chunk = await _not_found_or_register(db, officer, structured_data, language)
            elif "history" in structured_data:
                chunk = _render_workflow_history(structured_data, language)
            elif _status_yes_no_question(message) and _stage_yes_no_question(message):
                chunk = _render_status_role_answer(
                    structured_data, _status_yes_no_question(message),
                    _stage_yes_no_question(message), language)
            elif _status_yes_no_question(message):
                chunk = _render_status_yes_no_answer(
                    structured_data, _status_yes_no_question(message), language)
            elif _stage_yes_no_question(message) and _is_stage_ever_question(message):
                from backend.services.postgres import get_workflow_history
                _asked_stage = _stage_yes_no_question(message)
                _app_no = structured_data.get("application_number", "N/A")
                _hops = await get_workflow_history(db, officer, _app_no)
                chunk = _render_stage_ever_answer(
                    _hops, _app_no, _asked_stage, structured_data.get("stage"), language)
            elif _stage_yes_no_question(message):
                chunk = _render_stage_yes_no_answer(
                    structured_data, _stage_yes_no_question(message), language)
            else:
                app_no = structured_data.get("application_number", "N/A")
                app_type = structured_data.get("type", "N/A")
                status = structured_data.get("status", "N/A").capitalize()
                stage = structured_data.get("stage", "N/A")
                applicant = structured_data.get("applicant_name") or "N/A"
                survey = structured_data.get("survey_no", "N/A")
                if language in ("ta", "tanglish"):
                    # Mirrors the process_chat fix -- this generic summary is
                    # the fallback for a Tamil yes/no too ("X அங்கீகரிக்கப்பட்டதா?"),
                    # and was 100% hardcoded English regardless of `language`.
                    _status_ta = {
                        "pending": "நிலுவையில்", "approved": "அங்கீகரிக்கப்பட்டது",
                        "rejected": "நிராகரிக்கப்பட்டது", "in_progress": "செயல்பாட்டில்",
                        "escalated": "மேல்நிலைப்படுத்தப்பட்டது",
                    }.get((structured_data.get("status") or "").lower(), status)
                    _summary_ta = (
                        f"வகை: {app_type}, நிலை: {_status_ta}, கட்டம்: {stage}, "
                        f"விண்ணப்பதாரர்: {applicant}, கணக்கெண்: {survey}."
                    )
                    if _asks_for_specific_detail(message):
                        chunk = (
                            f"கேட்ட குறிப்பிட்ட தகவலை {app_no}-க்கு என்னால் "
                            f"கண்டுபிடிக்க முடியவில்லை. பதிவில் உள்ளது — {_summary_ta}"
                        )
                    else:
                        chunk = f"{app_no}-ன் விவரங்கள் — {_summary_ta}"
                else:
                    _summary = (
                        f"Type: {app_type}, Status: {status}, Stage: {stage}, "
                        f"Applicant: {applicant}, Survey No: {survey}."
                    )
                    if _asks_for_specific_detail(message):
                        # Mirrors process_chat: a pointed question that matched no
                        # field is answered honestly, not with a stand-in summary.
                        chunk = (
                            f"I could not find that particular detail for {app_no} in the record. "
                            f"Here is what it does hold — {_summary}"
                        )
                    else:
                        chunk = f"Here are the details for {app_no}. {_summary}"
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')

        elif intent in ("pending_applications", "overdue_applications"):
            count = structured_data.get("count", 0) if structured_data else 0
            qtype = structured_data.get("query_type", "applications") if structured_data else "applications"
            if count == 0:
                chunk = f"No {qtype.lower()} found in your jurisdiction."
            elif count == 1:
                chunk = f"Found 1 {qtype.lower()} in your jurisdiction:"
            else:
                chunk = f"Found {count} {qtype.lower()} in your jurisdiction:"
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')

        elif intent == "officer_workload":
            chunk = _render_workload_answer(structured_data, language)
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')

        elif intent == "fv_scheduled_this_week":
            count = structured_data.get("taluk_scheduled_count", 0) if structured_data else 0
            taluk = structured_data.get("taluk_name", "N/A") if structured_data else "N/A"
            cases = structured_data.get("taluk_cases", []) if structured_data else []
            week_start = structured_data.get("week_start", "") if structured_data else ""
            week_end = structured_data.get("week_end", "") if structured_data else ""
            cases_str = ", ".join(cases) if cases else "None"
            date_range = f" ({week_start} to {week_end})" if week_start else ""
            if count == 0:
                chunk = f"You have no field visits scheduled in {taluk} this week{date_range}."
            elif count == 1:
                chunk = f"You have 1 field visit scheduled in {taluk} this week{date_range}: {cases_str}."
            else:
                chunk = f"You have {count} field visits scheduled in {taluk} this week{date_range}: {cases_str}."
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')

        elif intent == "fv_unassigned_awaiting":
            count = structured_data.get("unassigned_visits_count", 0) if structured_data else 0
            apps_list = structured_data.get("unassigned_applications", []) if structured_data else []
            _nums = ", ".join(a.get("application_number", "") for a in apps_list)
            if language == "ta":
                if count == 0:
                    chunk = "திட்டமிடல் காத்திருக்கும் நிறைவேற்றப்படாத கள ஆய்வுகள் எதுவும் இல்லை."
                elif count == 1:
                    chunk = f"திட்டமிடல் காத்திருக்கும் 1 கள ஆய்வு விண்ணப்பம் உள்ளது: {_nums}."
                else:
                    chunk = f"திட்டமிடல் காத்திருக்கும் {count} கள ஆய்வு விண்ணப்பங்கள் உள்ளன: {_nums}."
            else:
                if count == 0:
                    chunk = "There are no unassigned field visits awaiting scheduling."
                elif count == 1:
                    chunk = f"There is 1 application with an unassigned field visit awaiting scheduling: {_nums}."
                else:
                    chunk = f"There are {count} applications with unassigned field visits awaiting scheduling: {_nums}."
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')

        elif intent == "fv_recently_rescheduled":
            count = structured_data.get("recently_rescheduled_count", 0) if structured_data else 0
            if count == 0:
                chunk = "No field visits were rescheduled during the last 7 days."
            elif count == 1:
                chunk = "1 field visit was rescheduled during the last 7 days."
            else:
                chunk = f"{count} field visits were rescheduled during the last 7 days."
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')

        elif intent == "fv_scheduling_conflicts":
            overlap_date = structured_data.get("overlap_date") if structured_data else None
            if overlap_date:
                chunk = (
                    f"Scheduling conflict detected: two or more field visits are scheduled on "
                    f"{overlap_date}. Please reschedule one of them — note that a field visit date "
                    f"change must be approved by the Tahsildar."
                )
            else:
                chunk = "No scheduling conflicts identified in the current inspection calendar."
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')

        elif intent == "fv_change_date":
            if language in ("ta", "tanglish"):
                chunk = "கள ஆய்வு தேதியை மாற்றுவதற்கு தாசில்தாரிடம் (Tahsildar) கேட்க வேண்டும். தாசில்தாரின் அனுமதியுடன் மட்டுமே கள ஆய்வு தேதியை மாற்ற இயலும்."
            else:
                chunk = "To change the field visit date, you should ask the Tahsildar. The Tahsildar has the authority to approve field visit date changes."
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')

        elif intent == "fv_reschedule_availability":
            res_date = structured_data.get("reschedule_date", "the next available working day") if structured_data else "the next available working day"
            chunk = f"Schedule available on {res_date}. Note: You should ask the Tahsildar about field visit date change."
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')

        elif intent == "fv_deadline_check":
            if not structured_data or not structured_data.get("found", True):
                chunk = structured_data.get("message", "Please specify an application number to check the deadline.") if structured_data else "Please specify an application number."
            else:
                app_no_dl = structured_data.get("application_number", "")
                working_days = structured_data.get("working_days", 0)
                sub_date_str = structured_data.get("submission_date", "")
                if structured_data.get("is_overdue", False):
                    overdue = structured_data.get("days_overdue", max(0, working_days - 15))
                    chunk = (
                        f"Yes — {app_no_dl} is past the 15-working-day deadline. "
                        f"It has been {working_days} working days since submission ({sub_date_str}), "
                        f"{overdue} day(s) overdue. Recommend escalating or scheduling immediately."
                    )
                else:
                    remaining = structured_data.get("days_remaining", max(0, 15 - working_days))
                    chunk = (
                        f"No — {app_no_dl} is on working day {working_days} of 15 "
                        f"(submitted {sub_date_str}). {remaining} working day(s) remaining within the window."
                    )
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')

        elif intent == "fv_overdue_inspections":
            count = structured_data.get("overdue_visits_count", 0) if structured_data else 0
            _awaiting = (structured_data or {}).get("awaiting_scheduling_count", 0)
            if count == 0:
                chunk = "No field visit is past its scheduled date."
                if _awaiting:
                    chunk += (
                        f" {_awaiting} application(s) are still awaiting a visit "
                        f"that has never been booked, so they carry no scheduled "
                        f"date to be late against — ask \"which application "
                        f"should I field visit next?\" to see them.")
                else:
                    chunk += " Every booked visit is on schedule."
            else:
                chunk = f"Found {count} overdue field visit(s). See the table below for details."
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')

        elif intent == "isd_processing":
            chunk = build_isd_processing_answer(message, structured_data, _isd_app_no)
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')

        # NOTE: fv_between_dates and survey_owners are deliberately NOT listed here —
        # they have dedicated branches further down that match process_chat's wording.
        elif intent in ("pending_applications", "field_visits", "fv_scheduled_this_week",
                        "fv_overdue_inspections", "fv_unassigned_awaiting", "fv_recently_rescheduled",
                        "ward_surveys", "block_surveys", "survey_detail", "next_subdivision",
                        "jurisdiction_summary", "rejection_info", "taluk_summary",
                        "litigation_check", "highest_priority_applications",
                        "town_applications", "block_applications",
                        "isd_applications", "nisd_applications",
                        "both_applications"):
            # Table is rendered on the frontend. Just emit a short natural intro.
            found = structured_data.get("found", True) if structured_data else False
            if not found:
                chunk = structured_data.get("message", "No records found.")
            else:
                asking_for_count = _is_count_only_query(message)
                
                if asking_for_count and intent in ("pending_applications", "isd_applications", "nisd_applications", "both_applications"):
                    chunk = _format_count_intro(structured_data, language, message)
                # Special message for priority applications
                elif intent == "highest_priority_applications":
                    count = len(structured_data.get("applications", []))
                    stage_filter = structured_data.get("query_type", "").split("—")[-1].strip().replace(" Stage", "") if "—" in structured_data.get("query_type", "") else None
                    is_tamil = language in ("ta", "tanglish")
                    
                    stage_text = f" in {stage_filter} stage" if stage_filter and stage_filter != "High Priority Applications" else ""
                    
                    if count == 0:
                        chunk = (
                            f"உயர் முன்னுரிமை விண்ணப்பங்கள் எதுவும் இல்லை{stage_text}." if is_tamil
                            else f"There are no high priority applications{stage_text} at this time."
                        )
                    elif count == 1:
                        chunk = (
                            f"1 உயர் முன்னுரிமை விண்ணப்பம் உள்ளது{stage_text} (⚠️ warning அல்லது overdue)." if is_tamil
                            else f"Found 1 high priority application{stage_text} (⚠️ warning or overdue)."
                        )
                    else:
                        chunk = (
                            f"{count} உயர் முன்னுரிமை விண்ணப்பங்கள் உள்ளன{stage_text} (⚠️ warning அல்லது overdue)." if is_tamil
                            else f"Found {count} high priority applications{stage_text} (⚠️ warning or overdue)."
                        )
                elif intent in ("field_visits", "fv_between_dates"):
                    count = structured_data.get("count", len(structured_data.get("field_visits", [])))
                    qtype = structured_data.get("query_type", "Field Visits")
                    start_date = structured_data.get("start_date")
                    end_date = structured_data.get("end_date")
                    date_range = f" ({start_date} to {end_date})" if (start_date and end_date) else ""
                    if count == 0:
                        chunk = f"No field visits found{date_range}."
                    elif count == 1:
                        chunk = f"Found 1 field visit{date_range}."
                    else:
                        chunk = f"Found {count} field visit(s){date_range}."
                else:
                    qtype = structured_data.get("query_type", "") if structured_data else ""
                    if qtype:
                        chunk = f"Here are the {qtype.lower()} results."
                    else:
                        chunk = "Results are shown in the table below."

            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')

        elif intent == "sd_additional_info":
            if not structured_data or not structured_data.get("found", True):
                chunk = "Application not found."
            else:
                missing = structured_data.get("missing_documents", [])
                clarification = structured_data.get("sd_clarification")
                req_parts = []
                if missing:
                    req_parts.append(f"missing documents ({', '.join(missing)})")
                if clarification:
                    req_parts.append(f"clarification: {clarification}")
                req_str = " and ".join(req_parts) if req_parts else "None"
                chunk = f"SD has requested: {req_str}."
                
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')
        elif intent == "sd_encroachment_check":
            if not structured_data or not structured_data.get("found", True):
                chunk = "Application not found."
            else:
                if structured_data.get("encroachment_found"):
                    chunk = "Yes, flag visible in SD's view of the application file."
                else:
                    chunk = "No encroachment flag has been noted on this application."
                    
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')
        elif intent == "sd_sketch_readiness":
            if not structured_data or not structured_data.get("found", True):
                chunk = "Application not found."
            else:
                missing_fields = []
                if not structured_data.get("field_visit_present"):
                    missing_fields.append("Field Visit Details")
                else:
                    if structured_data.get("area_verified") is None:
                        missing_fields.append("Area Verified")
                    if not structured_data.get("visit_notes_present"):
                        missing_fields.append("Visit Notes")
                if missing_fields:
                    chunk = f"Missing: {', '.join(missing_fields)}. Recommend completing before submission."
                else:
                    chunk = "All required fields are filled."
                    
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')
        elif intent == "sd_forward_check":
            if not structured_data or not structured_data.get("found", True):
                chunk = "Application not found."
            else:
                if structured_data.get("current_stage") == "SIS":
                    chunk = "No. The application is pending SIS verification."
                else:
                    forward_date = structured_data.get("forwarded_to_sd_date") or structured_data.get("submission_date")
                    chunk = f"Yes. Forwarded on {forward_date}."
                    
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')
        elif intent == "sd_remarks":
            if not structured_data or not structured_data.get("found", True):
                chunk = "Application not found."
            else:
                remarks = structured_data.get("sd_remarks")
                if remarks:
                    chunk = f"SD Remarks: {remarks}."
                else:
                    chunk = "No remarks recorded by SD."
                    
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')
        elif intent == "fv_date_select":
            if not structured_data or not structured_data.get("found", True):
                chunk = "Application not found."
            else:
                fv_date = structured_data.get("field_visit_date")
                if fv_date:
                    chunk = f"{fv_date} confirmed for this application."
                else:
                    chunk = "No field visit scheduled for this application."
                    
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')
        elif intent == "fv_nearby_pending":
            if not structured_data or not structured_data.get("found", True):
                chunk = "Application not found."
            else:
                count = structured_data.get("nearby_count", 0)
                ward = structured_data.get("ward_number", "N/A")
                block = structured_data.get("block_number", "N/A")
                chunk = f"{count} applications are located within the same Ward {ward} and Block {block}."
                
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')
        elif intent == "fv_between_dates":
            count = structured_data.get("count", 0)
            to_visit = structured_data.get("to_be_visited_count", count)
            s_date = structured_data.get("start_date")
            e_date = structured_data.get("end_date")
            date_range = f" ({s_date} to {e_date})" if (s_date and e_date) else ""
            if to_visit == 0:
                chunk = f"There are no field visits needed to be visited{date_range}."
            elif to_visit == 1:
                chunk = f"There is 1 field visit needed to be visited{date_range}."
            else:
                chunk = f"There are {to_visit} field visits needed to be visited{date_range}."

            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')
        elif intent == "survey_owners":
            if structured_data and structured_data.get("direct_answer"):
                chunk = structured_data["direct_answer"]
            elif not structured_data or not structured_data.get("found", True):
                chunk = structured_data.get("message", "Survey not found or not accessible.")
            else:
                owners = structured_data.get("owners", [])
                survey_no = structured_data.get("survey_no", "")
                if not owners:
                    chunk = f"No ownership records found for Survey No. {survey_no}."
                else:
                    owner_lines = [_owner_detail_line(o, message) for o in owners]
                    chunk = f"Owners for Survey No. {survey_no} ({len(owners)} record(s)):\n" + "\n".join(owner_lines)
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')
        elif intent == "service_code_lookup":
            _f_codes = (_followup_ctx.filters or {}).get("service_codes") if _followup_ctx else None
            if not _f_codes and _followup_ctx and (_followup_ctx.filters or {}).get("service_code"):
                _f_codes = [_followup_ctx.filters["service_code"]]
            if not _f_codes and _followup_ctx and _followup_ctx.application_numbers:
                _f_codes = _followup_ctx.application_numbers
            from backend.services.postgres import get_all_service_fees
            _fee_overrides = await get_all_service_fees(db) if db else None
            if _force_detailed_service_code_answer:
                chunk = _service_code_lookup_answer(message, language in ("ta", "tanglish"), brief=False, followup_codes=_f_codes, chat_history=chat_history, fee_overrides=_fee_overrides)
            else:
                chunk = await _service_code_lookup_answer_natural(
                    message, language in ("ta", "tanglish"),
                    not _is_detail_request(message), language, followup_codes=_f_codes, chat_history=chat_history, fee_overrides=_fee_overrides)
            full_response_text = chunk
            # Recorded so a SECOND "explain" in a row still means the same
            # code -- see the non-streaming twin's comment for why the code
            # is read off the answer text, not re-extracted from the question.
            _sc_in_answer = sorted(list(set(re.findall(r'\b01[5-9]\d\b', chunk))))
            if len(_sc_in_answer) == 1:
                structured_data = {"service_code_filter": _sc_in_answer[0],
                                    "query_type": "Service Code Lookup"}
            elif len(_sc_in_answer) >= 2:
                structured_data = {"service_codes": _sc_in_answer,
                                    "service_code_filter": _sc_in_answer[0],
                                    "query_type": "Service Code Comparison"}
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')
        elif intent == "all_service_code_comparison":
            from backend.services.postgres import get_service_register_counts_by_code, get_all_service_fees
            _dynamic_counts = await get_service_register_counts_by_code(db) if db else None
            _fee_map = await get_all_service_fees(db) if db else None
            chunk = _all_service_code_comparison_answer(
                message, language in ("ta", "tanglish"),
                dynamic_counts=_dynamic_counts, fee_map=_fee_map
            )
            full_response_text = chunk
            structured_data = {
                "query_type": "All Service Code Comparison",
                "service_codes_count": 29
            }
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')
        elif intent == "unidentified_number":
            chunk = _unidentified_number_answer(message, language in ("ta", "tanglish"))
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')
        elif any(ph in message.lower() for ph in [
            "uploaded", "word document", "pdf document", "question bank",
            "answer all", "answer for all", "from the document", "in the document",
            "the file", "attached file", "from this file",
        ]):
            chunk = (
                "I don't see a file attached in this chat (uploaded files are kept for "
                f"{settings.UPLOAD_RETENTION_MINUTES} minutes only). "
                "Upload a PDF, Word (.docx), CSV or text file using the attach "
                "button and I'll answer your questions from it, with citations."
            )
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')

        elif intent in ("check_sale_deed", "sale_deed_check") and (
            _sd_answer := build_sale_deed_direct_answer(structured_data, message, language)
        ):
            # Same fix as process_chat: never let the LLM pick which field is
            # "the deed number" out of the full application record.
            full_response_text = _sd_answer
            sse_data = f"data: {json.dumps({'content': _sd_answer})}\n\n"
            yield sse_data.encode('utf-8')

        elif structured_data and "applications" in structured_data:
            # Deterministic intro for any remaining list result — same fallback as
            # process_chat, so a row count is never left to the LLM.
            count = structured_data.get("count", len(structured_data.get("applications", [])))
            qtype = structured_data.get("query_type", "Pending Applications")
            chunk = f"Found {count} application(s) ({qtype})."
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')

        elif _no_evidence_for_llm(_original_message, intent, structured_data, message, _followup_ctx):
            chunk = _clarification_reply(language)
            logger.info("No domain evidence for an LLM answer; asked for clarification (stream)")
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')

        else:
            # Same last-resort point as process_chat: prefer the tool-calling
            # agent, fall back to the plain prompt. The agent's tool loop runs
            # before its first chunk, so a failure still lands here with
            # nothing written to the SSE stream yet.
            _stream_source = None
            if settings.AGENT_ENABLED:
                _agen = run_agent_stream(message, officer, db, chat_history, language)
                try:
                    _first_chunk = await _agen.__anext__()
                    _stream_source = (_agen, _first_chunk)
                except StopAsyncIteration:
                    logger.warning("Agent layer produced nothing, using the plain prompt")
                except (AgentUnavailable, asyncio.TimeoutError) as _agent_exc:
                    logger.warning(f"Agent layer unavailable, using the plain prompt: {_agent_exc}")
                except Exception as _agent_exc:
                    logger.error(f"Agent layer errored, using the plain prompt: {_agent_exc}",
                                 exc_info=True)
                if _stream_source is None:
                    await _agen.aclose()

            if _stream_source is not None:
                _agen, _first_chunk = _stream_source
                async for chunk in _agent_chunks(_first_chunk, _agen):
                    chunk_count += 1
                    full_response_text += chunk
                    sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
                    yield sse_data.encode('utf-8')
                    if chunk_count % 10 == 0:
                        logger.debug(f"Streamed {chunk_count} chunks, total length: {len(full_response_text)}")
                logger.info(f"Agent stream complete: {chunk_count} chunks")
            else:
                async for chunk in call_llama_stream(full_prompt):
                    chunk_count += 1
                    full_response_text += chunk

                    # Format as Server-Sent Event
                    sse_data = f"data: {json.dumps({'content': chunk})}\n\n"

                    # Encode to bytes for streaming
                    yield sse_data.encode('utf-8')

                    if chunk_count % 10 == 0:
                        logger.debug(f"Streamed {chunk_count} chunks, total length: {len(full_response_text)}")
        
        logger.info(f"Stream complete: {chunk_count} chunks, {len(full_response_text)} chars")

        # The same check as in process_chat, on what the model actually streamed.
        # The chunks are already on the wire and cannot be recalled, so a
        # correction is streamed after them and the corrected text is what gets
        # stored -- the officer is told, and the next turn's follow-up context
        # is built from a transcript with no invented number in it.
        if chunk_count:
            _verified_text, _verify_note = _verify_answer_numbers(
                full_response_text, structured_data, intent)
            if _verified_text != full_response_text or _verify_note:
                _correction = (_verify_note or "").strip()
                # Only a stated total that was really rewritten earns this note: text that
                # merely differs after clean-up (spacing, table formatting) corrected no figure.
                if (_verified_text != full_response_text and not _correction
                        and _reconcile_count_claims(full_response_text, structured_data)[1]):
                    _correction = ("Note: a figure in that answer disagreed with your "
                                   "register and has been corrected to "
                                   f"{structured_data.get('count')}.")
                full_response_text = _verified_text + (" " + _correction if _correction else "")
                if _correction:
                    _sse = f"data: {json.dumps({'content': ' ' + _correction})}\n\n"
                    yield _sse.encode('utf-8')
            
        # Step 7: Calculate response time
        response_time_ms = int((time.time() - start_time) * 1000)
        
        # What this answer was about, recorded for the next turn to resolve a
        # bare follow-up against. Built only from the structured data a query
        # returned -- never from the wording of the answer.
        _out_ctx = fctx.build_context(intent, structured_data,
                                      fallback_app_number=locals().get("app_number"))
        # A patta-transfer-party answer is text set during the pre-empt, so
        # structured_data may have been overwritten by a later fetch branch
        # (e.g. application_status). Record the party explicitly so the next
        # turn's "what is their gender?" stays on it.
        try:
            _pick_ctx = getattr(locals().get("_followup"), "context", None)
            if (_out_ctx and _out_ctx.entity == fctx.ENTITY_APPLICATION and _pick_ctx
                    and _pick_ctx.entity == fctx.ENTITY_APPLICATION_LIST
                    and len(_pick_ctx.application_numbers or []) > 1):
                _out_ctx.filters = {**(_out_ctx.filters or {}),
                                    "list_numbers": list(_pick_ctx.application_numbers)}
        except Exception:
            pass
        if _tp_marker:
            _out_ctx = fctx.FollowupContext(
                entity=fctx.ENTITY_APPLICATION,
                application_numbers=[str(_tp_marker[0]).upper()],
                filters={"transfer_party": _tp_marker[1]},
                query_type="Patta Transfer Parties",
                intent=intent)
        # A registration-detail answer ("registered at Melur on …") -- so a bare
        # "when?" / "where?" / "and the reason?" next turn stays on the deed,
        # not the filing date.
        elif _reg_marker:
            _out_ctx = fctx.FollowupContext(
                entity=fctx.ENTITY_APPLICATION,
                application_numbers=[str(_reg_marker[0]).upper()],
                filters={"reg_detail": _reg_marker[1]},
                query_type="Registration Detail",
                intent=intent)
        # Step 8: Save chat messages to database
        await save_chat_messages(
            db=db,
            session_id=session_id,
            user_message=_original_message,
            assistant_message=full_response_text,
            language=language,
            response_time_ms=response_time_ms,
            officer_id=officer.officer_id if officer else None,
            structured_context=(_out_ctx.to_json() if _out_ctx else None)
        )
        
        logger.info(f"Chat processed and streamed successfully in {response_time_ms}ms")
        
    except Exception as e:
        import json
        # Roll back BEFORE logging: rendering the traceback walks frame locals,
        # and touching an ORM object there lazy-loads outside the async context,
        # which poisons the session for the rest of the conversation.
        try:
            await db.rollback()
        except Exception as rb_err:
            logger.warning(f"Rollback after process_chat_stream failure also failed: {rb_err}")

        logger.error(f"Error in process_chat_stream: {e}", exc_info=True)
        error_messages = {
            "en": "I apologize, but I encountered an error processing your request. Please try again.",
            "ta": "மன்னிக்கவும், உங்கள் கோரிக்கையைச் செயல்படுத்துவதில் பிழை ஏற்பட்டது. மீண்டும் முயற்சிக்கவும்.",
            "tanglish": "மன்னிக்கவும், உங்கள் கோரிக்கையைச் செயல்படுத்துவதில் பிழை ஏற்பட்டது. மீண்டும் முயற்சிக்கவும்."
        }
        language = detect_language(message)
        error_msg = error_messages.get(language, error_messages["en"])
        yield f"data: {json.dumps({'content': error_msg})}\n\n".encode('utf-8')


async def save_chat_messages(
    db: AsyncSession,
    session_id: str,
    user_message: str,
    assistant_message: str,
    language: str,
    response_time_ms: int,
    officer_id: Optional[uuid.UUID] = None,
    structured_context: Optional[Dict[str, Any]] = None
) -> None:
    """
    Save user and assistant messages to database.

    `structured_context` is the `FollowupContext` this answer leaves behind for
    the next turn -- see `backend/services/followup_context.py`. It is None for
    answers with no referent worth carrying (greetings, RAG answers, and access
    denials, which must not leave a number behind).
    """
    # The transcript records what the officer typed, not the spelling-corrected
    # text the rules were run on.
    _raw_map = _RAW_BY_FIXED.get()
    if _raw_map and user_message in _raw_map:
        user_message = _raw_map[user_message]
    if _SAVE_AS.get():                       # a re-check turn: the transcript keeps what was typed
        user_message = _SAVE_AS.get()
    try:
        # Get session
        session_query = select(ChatSession).where(
            ChatSession.id == session_id
        )
        result = await db.execute(session_query)
        session = result.scalar_one_or_none()
        
        if session and officer_id and str(session.officer_id) != str(officer_id):
            # Someone else's session: never write into it.
            logger.warning("chat turn not stored: session belongs to another officer")
            return

        if not session:
            try:
                import uuid as _uuid_m
                sess_uuid = _uuid_m.UUID(str(session_id))
                if not officer_id:
                    # Never fall back to "whichever officer happens to be first in
                    # the table" — that files one officer's conversation under
                    # another officer's account. Drop the transcript instead.
                    logger.warning(
                        f"Cannot auto-create session {session_id}: no officer_id supplied; "
                        f"messages not saved."
                    )
                    return
                session = ChatSession(
                    id=sess_uuid,
                    officer_id=officer_id,
                    session_token=str(session_id),
                    is_active=True,
                    started_at=datetime.now(timezone.utc),
                    last_activity=datetime.now(timezone.utc)
                )
                db.add(session)
                await db.flush()
            except Exception as sess_err:
                logger.warning(f"Could not auto-create session {session_id}: {sess_err}")
                return
        
        # Save user message
        user_msg = ChatMessage(
            session_id=session_id,
            role="user",
            content=user_message,
            detected_language=language,
            created_at=datetime.now(timezone.utc)
        )
        db.add(user_msg)
        
        # Save assistant message. `structured_data` carries the reference
        # context for the next turn -- what this answer was about, as data.
        # Follow-ups used to recover that by scraping the rendered HTML back
        # out of the transcript; the column existed for this from the start
        # and was simply never written.
        assistant_msg = ChatMessage(
            session_id=session_id,
            role="assistant",
            content=assistant_message,
            detected_language=language,
            response_time_ms=response_time_ms,
            structured_data=structured_context,
            created_at=datetime.now(timezone.utc)
        )
        db.add(assistant_msg)
        
        # Update session last activity
        session.last_activity = datetime.now(timezone.utc)
        
        await db.commit()
        logger.info(f"Saved chat messages for session {session_id}")
        
    except Exception as e:
        logger.error(f"Error saving chat messages: {e}")
        await db.rollback()


async def create_chat_session(
    db: AsyncSession,
    officer_id: str
) -> ChatSession:
    """
    Create a new chat session for an officer
    
    Args:
        db: Database session
        officer_id: Officer UUID
        
    Returns:
        Created ChatSession object
    """
    try:
        import uuid
        
        session = ChatSession(
            officer_id=officer_id,
            session_token=str(uuid.uuid4()),
            started_at=datetime.now(timezone.utc),
            last_activity=datetime.now(timezone.utc),
            is_active=True
        )
        
        db.add(session)
        await db.commit()
        await db.refresh(session)
        
        logger.info(f"Created new chat session: {session.id}")
        return session
        
    except Exception as e:
        logger.error(f"Error creating chat session: {e}")
        await db.rollback()
        raise


async def get_session_history(
    db: AsyncSession,
    session_id: str,
    limit: int = 50
) -> list:
    """
    Get chat history for a session
    
    Args:
        db: Database session
        session_id: Chat session UUID
        limit: Maximum number of messages to return
        
    Returns:
        List of chat messages
    """
    try:
        # Only the current conversation: after a typed "clear" the older rows are
        # kept for audit but must not be restored into the officer's transcript.
        query = select(ChatMessage).where(
            ChatMessage.session_id == session_id,
            messages_since_session_start(session_id)
        ).order_by(ChatMessage.created_at.desc()).limit(limit)
        
        result = await db.execute(query)
        messages = result.scalars().all()
        
        # Reverse to get chronological order
        messages = list(reversed(messages))
        
        return [
            {
                "role": msg.role,
                "content": msg.content,
                "language": msg.detected_language,
                "timestamp": msg.created_at.isoformat()
            }
            for msg in messages
        ]
        
    except Exception as e:
        logger.error(f"Error getting session history: {e}")
        return []


async def get_officer_sessions(
    db: AsyncSession,
    officer_id: str
) -> list:
    """
    Get all chat sessions for an officer
    
    Args:
        db: Database session
        officer_id: Officer UUID
        
    Returns:
        List of chat sessions
    """
    try:
        query = select(ChatSession).where(
            ChatSession.officer_id == officer_id
        ).order_by(ChatSession.last_activity.desc())
        
        result = await db.execute(query)
        sessions = result.scalars().all()
        
        return [
            {
                "session_id": str(session.id),
                "session_token": session.session_token,
                "started_at": session.started_at.isoformat(),
                "last_activity": session.last_activity.isoformat() if session.last_activity else None,
                "is_active": session.is_active
            }
            for session in sessions
        ]
        
    except Exception as e:
        logger.error(f"Error getting officer sessions: {e}")
        return []


# The 5 geography columns a listing table can show, narrowest to broadest --
# the fixed default `prepareApplicationsTable` uses in table_renderer.js.
# Named here as the officer would say them, including common misspellings/
# Tanglish, so "order columns as district, taluk, ward, block" or "show
# district first" can be read back into the same column keys the frontend
# expects ('Block', 'Ward', 'Town', 'Taluk', 'District').
_LOC_COLUMN_WORDS = (
    ("block", "Block", r"\bblo?ck\b"),
    ("ward", "Ward", r"\bward\b"),
    ("town", "Town", r"\btown\b"),
    ("taluk", "Taluk", r"\btaluk\b|\btaluka\b"),
    ("district", "District", r"\bdistrict\b"),
)


def _requested_loc_column_order(message: str) -> Optional[List[str]]:
    """"order columns as district, taluk, ward, block" / "show district first"
    / "put block before ward": a custom order for the 5 location columns a
    listing table shows, read off the ORDER those words appear in the
    message. Needs at least two of the five named (one name alone is just an
    ordinary field question, e.g. "which district is it in") and the message
    must actually be about reordering/columns, not a filter ("from district
    X") or an unrelated question that happens to mention two of these words.
    Any location word the message does NOT name is appended afterward in the
    default order, so naming only some of them still produces a complete,
    valid column list."""
    text = (message or "").lower()
    if not re.search(r"\border\w*\b|\bcolumn\w*\b|\bfirst\b|\blast\b|\bbefore\b|\bafter\b|\bthen\b|\brearrange\w*\b|\bsequence\b|\bsort\w*\s+column", text):
        return None
    found = []
    for key, label, pat in _LOC_COLUMN_WORDS:
        m = re.search(pat, text)
        if m:
            found.append((m.start(), label))
    default = ["Block", "Ward", "Town", "Taluk", "District"]
    # "show district first" / "put block last" -- only ONE location word, so
    # there is nothing to sort by position; move it to the named end of the
    # default order instead.
    if len(found) == 1:
        label = found[0][1]
        if re.search(r"\bfirst\b", text):
            return [label] + [c for c in default if c != label]
        if re.search(r"\blast\b", text):
            return [c for c in default if c != label] + [label]
        return None
    if len(found) < 2:
        return None
    found.sort(key=lambda t: t[0])
    order = [label for _, label in found]
    for label in default:
        if label not in order:
            order.append(label)
    return order


def _build_table_data(intent: str, message: str, user_id: str, structured_data: Dict[str, Any] = None) -> Optional[Dict[str, Any]]:
    """`_build_table_data_raw` plus district names on every application row."""
    table = _build_table_data_raw(intent, message, user_id, structured_data)
    if isinstance(table, dict):
        from backend.config import DISTRICT_CODE_MAP
        for key in ("applications", "multi_tables"):
            for row in (table.get(key) or []):
                if not isinstance(row, dict):
                    continue
                jur = row.get("jurisdiction") if isinstance(row.get("jurisdiction"), dict) else {}
                if jur.get("district") not in (None, "", "N/A") or row.get("district_name") not in (None, "", "N/A"):
                    continue
                parts = str(row.get("application_number") or "").split("/")
                code = parts[2] if len(parts) == 4 else str(row.get("district_code") or "")
                name = DISTRICT_CODE_MAP.get(code.zfill(2)) if code else None
                if name:
                    row["district_name"] = name
        if "applications" in table:
            _order = _requested_loc_column_order(message)
            if _order:
                table["loc_column_order"] = _order
    return table


def _build_table_data_raw(intent: str, message: str, user_id: str, structured_data: Dict[str, Any] = None) -> Optional[Dict[str, Any]]:
    if not structured_data:
        return None
        
    intent_lower = intent.lower()

    # "is this from citizen or CSC?" wants one word back. The 21-field
    # application table buried that answer under fields nobody asked about, so
    # this intent renders the sentence alone.
    if intent_lower in ("submission_channel_check", "can_apply_check"):
        return None
    
    # 1. Survey details lookup
    if intent_lower in ["survey_lookup", "survey_detail"]:
        if not structured_data.get("found", True):
            return None
        subdivs = []
        for sd in structured_data.get("sub_divisions", []):
            if sd.get("sub_division_no"):
                area = sd.get("area_sqm")
                if area:
                    subdivs.append(f"{sd.get('sub_division_no')} ({int(area)} sq.m)")
                else:
                    subdivs.append(sd.get("sub_division_no"))
        block_name = structured_data.get("jurisdiction", {}).get("block") or "N/A"
        return {
            "query_type": "Survey Number Details",
            "jurisdiction": {
                "district": structured_data.get("jurisdiction", {}).get("district", "N/A"),
                "taluk": structured_data.get("jurisdiction", {}).get("taluk", "N/A"),
                "town": structured_data.get("jurisdiction", {}).get("town", "N/A"),
                "ward_number": structured_data.get("jurisdiction", {}).get("ward_number") or structured_data.get("jurisdiction", {}).get("ward") or "N/A",
                "block_number": block_name
            },
            "surveys_by_block": {
                block_name: [
                    {
                        "survey_no": structured_data.get("survey_no"),
                        "area_sqm": structured_data.get("total_area_sqm"),
                        "land_type": structured_data.get("land_type") or "Urban",
                        "subdivisions": subdivs
                    }
                ]
            }
        }
        
    # 2. Pending / typed application lists
    elif intent_lower in [
        "pending_applications", "town_applications", "block_applications", "immediate_action",
        "isd_applications", "nisd_applications", "highest_priority_applications"
    ] or (intent_lower == "workload" and "applications" in structured_data):
        apps = []
        for app in structured_data.get("applications", []):
            apps.append({
                "application_number": app.get("application_number"),
                "type": app.get("type"),
                "survey_no": app.get("survey_no") or app.get("raw_survey_no") or "N/A",
                "subdivisions": app.get("subdivisions") or app.get("sub_division_no") or "N/A",
                # The DISTRICT column was rendering "N/A" on every row while
                # taluk, town, ward and block were right beside it, correct.
                # The query layer had the value all along -- it is simply never
                # copied here, so the renderer read a key that was never sent.
                # The same bug CLAUDE.md records for the CAN column: printing
                # "N/A" is a false statement about data that is on record,
                # which is worse than leaving the column out.
                "district_name": (app.get("district_name")
                                  or app.get("jurisdiction", {}).get("district")
                                  or "N/A"),
                "taluk_name": app.get("taluk_name") or app.get("jurisdiction", {}).get("taluk", "N/A"),
                "town_name": app.get("town_name") or "N/A",
                "ward_number": app.get("ward_number") or "N/A",
                "block_number": app.get("block_number") or "N/A",
                "status": app.get("status") or "Pending",
                "current_stage": app.get("stage") or app.get("current_stage"),
                "submission_date": app.get("submission_date"),
                "is_overdue": app.get("is_overdue"),
                "field_visit_scheduled": app.get("field_visit_scheduled"),
                "field_visit_date": app.get("field_visit_date")
            })
        return {
            "query_type": structured_data.get("query_type", "Pending Applications"),
            # The frontend trims District/Taluk/Town to what actually varies
            # for this officer's jurisdiction level (see prepareApplicationsTable
            # in table_renderer.js) -- without this key it always fell back to
            # the narrowest case (Block only), dropping Ward too.
            "jurisdiction_type": structured_data.get("jurisdiction_type"),
            "applications": apps
        }

    # 2b. Both / mixed type applications combined (isd+nisd, all, etc.)
    elif intent_lower == "both_applications":
        apps = []
        for app in structured_data.get("applications", []):
            apps.append({
                "application_number": app.get("application_number"),
                "type": app.get("type"),
                # Same omission as the listing builder above -- the renderer
                # shows a District column, so the value has to be sent.
                "district_name": (app.get("district_name")
                                  or app.get("jurisdiction", {}).get("district")
                                  or "N/A"),
                "taluk_name": app.get("taluk_name") or app.get("jurisdiction", {}).get("taluk", "N/A"),
                "town_name": app.get("town_name") or "N/A",
                "ward_number": app.get("ward_number") or "N/A",
                "block_number": app.get("block_number") or "N/A",
                "status": app.get("status") or "Pending",
                "current_stage": app.get("stage") or app.get("current_stage"),
                "submission_date": app.get("submission_date")
            })
        return {
            "query_type": structured_data.get("query_type", "Combined Applications"),
            "jurisdiction_type": structured_data.get("jurisdiction_type"),
            "applications": apps
        }
        
    # 3. Field visit
    # NOTE: only intents whose structured_data actually carries a "field_visits"
    # list belong here. fv_unassigned_awaiting (unassigned_applications),
    # fv_scheduled_this_week (taluk_cases), fv_recently_rescheduled (a count) and
    # fv_scheduling_conflicts (a single date) do not — listing them here rendered
    # an empty "No records found" table under an otherwise correct answer.
    elif intent_lower in [
        "field_visit", "field_visits", "awaiting_field_visit",
        "fv_between_dates", "fv_overdue_inspections"
    ]:
        visits = []
        for visit in structured_data.get("field_visits", []):
            app_type = visit.get("application_type") or visit.get("type") or "N/A"
            fv_date = visit.get("field_visit_date") or visit.get("scheduled_date") or "Not Scheduled"
            visits.append({
                "application_number": visit.get("application_number") or "N/A",
                "survey_no": visit.get("survey_no") or "N/A",
                "block_number": visit.get("block_number") or "N/A",
                "application_type": app_type,
                "type": app_type,
                "status": visit.get("status") or "N/A",
                "field_visit_date": fv_date,
                "scheduled_date": fv_date
            })
        if not visits:
            # No rows — suppress the table entirely rather than rendering an
            # empty "No records found" card under the text answer.
            return None
        return {
            "query_type": structured_data.get("query_type", "Field Visits"),
            "field_visits": visits
        }
        
    # 4. Owner lookup
    elif intent_lower in ["owner_lookup", "survey_owners"]:
        owners_list = []
        for o in structured_data.get("owners", []):
            sub_div = o.get("sub_division")
            if sub_div == "Survey Level":
                sub_div = None
            share = o.get("ownership_share")
            if share is not None:
                share = float(share)
            owners_list.append({
                "owner_name": o.get("owner_name") or o.get("name") or "N/A",
                "sub_division": sub_div,
                "ownership_share": share if share is not None else "N/A",
                "ownership_type": o.get("ownership_type") or ("Joint" if o.get("is_joint_owner") else "Primary"),
                "is_joint_owner": bool(o.get("is_joint_owner"))
            })
        return {
            "query_type": structured_data.get("query_type", "Owner Details"),
            "survey_no": structured_data.get("survey_no", ""),
            "owners": owners_list
        }
        
    # 5. Workload summary
    elif intent_lower in ["officer_workload"] or (intent_lower == "workload" and "total_active" in structured_data):
        total = structured_data.get("total_active", 0)
        pending = structured_data.get("ISD", 0) + structured_data.get("NISD", 0)
        overdue = structured_data.get("overdue", 0)
        unscheduled = structured_data.get("unscheduled_visits", 0)
        return {
            "query_type": "Workload Summary",
            "total_applications": total,
            "pending_count": pending,
            "overdue_count": overdue,
            "unscheduled_visits": unscheduled,
            "other_codes_active": structured_data.get("other_codes_active", 0),
            "total_applications_all_codes": structured_data.get("total_active_all_codes", total),
        }
        
    # 6. Status check, and every sub-topic that answers about ONE application.
    # These all carry a get_application_detail() payload. Without them here the
    # chain fell through to the jurisdiction rule below -- whose payload happens
    # to contain a "jurisdiction" key -- and rendered a two-row District/Taluk
    # table of N/A under an otherwise correct answer.
    elif intent_lower in [
        "status_check", "application_status",
        "is_nisd_or_isd", "check_sale_deed", "sale_deed_check", "check_documents",
        "isd_processing",
    ]:
        # Multi-app query: return list of tables, one per application
        if "multi_applications" in structured_data:
            multi_tables = []
            for _app_sd in structured_data["multi_applications"]:
                if not _app_sd or not _app_sd.get("found", True):
                    continue
                multi_tables.append({
                    "query_type": "Application & Applicant Details",
                    "serial_number": _app_sd.get("serial_number"),
                    "application_number": _app_sd.get("application_number"),
                    "application_id": _app_sd.get("application_id") or _app_sd.get("application_number"),
                    "user_id": _app_sd.get("user_id"),
                    "service_code": _app_sd.get("service_code"),
                    "district_code": _app_sd.get("district_code"),
                    "taluk_code": _app_sd.get("taluk_code"),
                    "village_code": _app_sd.get("village_code"),
                    "urban_unit_code": _app_sd.get("urban_unit_code"),
                    "ward_code": _app_sd.get("ward_code"),
                    "block_code": _app_sd.get("block_code"),
                    "district_name": _app_sd.get("district_name"),
                    "taluk_name": _app_sd.get("taluk_name"),
                    "jurisdiction": _app_sd.get("jurisdiction"),
                    "application_date": _app_sd.get("application_date") or _app_sd.get("submission_date"),
                    "application_status": _app_sd.get("application_status") or _app_sd.get("status"),
                    "survey_number": _app_sd.get("survey_number") or _app_sd.get("survey_no"),
                    "subdivision_number": _app_sd.get("subdivision_number"),
                    "current_subdivision_number": (
                        _app_sd.get("current_subdivision_number")["proposed_sub_division_no"]
                        if isinstance(_app_sd.get("current_subdivision_number"), dict)
                        else _app_sd.get("current_subdivision_number")
                    ),
                    "patta_number": _app_sd.get("patta_number"),
                    "role_id": _app_sd.get("role_id"),
                    "source_code": _app_sd.get("source_code"),
                    "source_name": _app_sd.get("source_name"),
                    "can_number": _app_sd.get("can_number"),
                    "workflow_state": _app_sd.get("workflow_state") or _app_sd.get("stage"),
                    "type": _app_sd.get("type"),
                    "included_subdivisions": _app_sd.get("included_subdivisions"),
                    "status": _app_sd.get("status"),
                    "stage": _app_sd.get("stage"),
                    "submission_date": _app_sd.get("submission_date"),
                    "field_visit_scheduled": bool(_app_sd.get("field_visit_scheduled")),
                    "field_visit_date": _app_sd.get("field_visit_date"),
                    "is_overdue": bool(_app_sd.get("is_overdue")),
                    "priority_flag": bool(_app_sd.get("priority_flag")),
                    "applicant_name": _app_sd.get("applicant_name"),
                    "applicant_mobile": _app_sd.get("applicant_mobile"),
                    "applicant_address": _app_sd.get("applicant_address"),
                    "declared_reason": _app_sd.get("declared_reason"),
                })
            if not multi_tables:
                return None
            return {"query_type": "Application & Applicant Details", "multi_tables": multi_tables}

        if not structured_data.get("found", True):
            return None
        if "history" in structured_data:
            return {
                "query_type": structured_data.get("query_type", "Workflow History"),
                "application_number": structured_data.get("application_number"),
                "history": structured_data.get("history", [])
            }
        return {
            "query_type": "Application & Applicant Details",
            "serial_number": structured_data.get("serial_number"),
            "application_number": structured_data.get("application_number"),
            "application_id": structured_data.get("application_id") or structured_data.get("application_number"),
            "user_id": structured_data.get("user_id"),
            "service_code": structured_data.get("service_code"),
            "district_code": structured_data.get("district_code"),
            "taluk_code": structured_data.get("taluk_code"),
            "village_code": structured_data.get("village_code"),
            "urban_unit_code": structured_data.get("urban_unit_code"),
            "ward_code": structured_data.get("ward_code"),
            "block_code": structured_data.get("block_code"),
            # get_application_detail() already resolves these (names from the
            # master tables, not the codes) -- this dict just never carried
            # them through, so the card fell back to the code every time.
            # prepareApplicationDetailTable() on the frontend already prefers
            # district_name / taluk_name / jurisdiction.ward / jurisdiction.block
            # over the _code fields; it just never received them.
            "district_name": structured_data.get("district_name"),
            "taluk_name": structured_data.get("taluk_name"),
            "jurisdiction": structured_data.get("jurisdiction"),
            "application_date": structured_data.get("application_date") or structured_data.get("submission_date"),
            "application_status": structured_data.get("application_status") or structured_data.get("status"),
            "survey_number": structured_data.get("survey_number") or structured_data.get("survey_no"),
            "subdivision_number": structured_data.get("subdivision_number"),
            "current_subdivision_number": (
                structured_data.get("current_subdivision_number")["proposed_sub_division_no"]
                if isinstance(structured_data.get("current_subdivision_number"), dict)
                else structured_data.get("current_subdivision_number")
            ),
            "patta_number": structured_data.get("patta_number"),
            "role_id": structured_data.get("role_id"),
            "source_code": structured_data.get("source_code"),
            "source_name": structured_data.get("source_name"),
            "can_number": structured_data.get("can_number"),
            "workflow_state": structured_data.get("workflow_state") or structured_data.get("stage"),
            "type": structured_data.get("type"),
            "included_subdivisions": structured_data.get("included_subdivisions"),
            "status": structured_data.get("status"),
            "stage": structured_data.get("stage"),
            "submission_date": structured_data.get("submission_date"),
            "field_visit_scheduled": bool(structured_data.get("field_visit_scheduled")),
            "field_visit_date": structured_data.get("field_visit_date"),
            "is_overdue": bool(structured_data.get("is_overdue")),
            "priority_flag": bool(structured_data.get("priority_flag")),
            "applicant_name": structured_data.get("applicant_name"),
            "applicant_mobile": structured_data.get("applicant_mobile"),
            "applicant_address": structured_data.get("applicant_address"),
            "declared_reason": structured_data.get("declared_reason")
        }

    # 7. Rejection info
    elif intent_lower in ["rejection_info"]:
        return {
            "query_type": structured_data.get("query_type", "Rejection History"),
            "application_number": structured_data.get("application_number"),
            "rejections": structured_data.get("rejections", [])
        }

    # 8. Jurisdiction summary. The "jurisdiction" fallback is for payloads that
    # ARE a jurisdiction summary -- an application detail also carries the key,
    # and rendering it as one drops everything the question was about.
    elif intent_lower in ["jurisdiction_summary"] or (
            "jurisdiction" in structured_data
            and not structured_data.get("application_number")):
        return {
            "query_type": structured_data.get("query_type", "Jurisdiction Summary"),
            "jurisdiction": structured_data.get("jurisdiction", {})
        }
        
    # 9. Unassigned field visits — show application detail table
    elif intent_lower == "fv_unassigned_awaiting":
        apps = structured_data.get("unassigned_applications", [])
        if not apps:
            return None
        return {
            "query_type": "Unassigned Field Visits — Awaiting Scheduling",
            "applications": apps
        }
    
    # 9b. Overdue field visits — show field visit table
    elif intent_lower == "fv_overdue_inspections":
        visits = structured_data.get("field_visits", [])
        if not visits:
            return None
        return {
            "query_type": "Overdue Field Visits",
            "field_visits": visits
        }

    # 10. Immediate action — show application detail table
    elif intent_lower == "immediate_action":
        apps = structured_data.get("applications", [])
        if not apps:
            return None
        return {
            "query_type": "Immediate Action Required — Overdue Applications",
            "applications": apps
        }

    # 11. Highest priority applications — show application table with warning symbols
    elif intent_lower == "highest_priority_applications":
        apps = structured_data.get("applications", [])
        if not apps:
            return None
        return {
            "query_type": "High Priority Applications",
            "applications": apps
        }

    # 12. Escalation check — applications approaching deadline
    elif intent_lower == "escalation_check":
        apps = structured_data.get("applications", [])
        if not apps:
            return None
        # Map to standard applications table format, adding days info as pseudo-field
        rows = []
        for a in apps:
            rows.append({
                "application_number": a.get("application_number"),
                "type": a.get("type"),
                "status": a.get("status"),
                "current_stage": a.get("stage"),
                "submission_date": a.get("submission_date"),
                "town_name": a.get("town_name", "N/A"),
                "ward_number": a.get("ward_number", "N/A"),
                # Overload days_pending for display — show working days elapsed
                "days_pending": a.get("working_days_elapsed", 0),
                "priority": a.get("urgency", "N/A"),
            })
        return {
            "query_type": structured_data.get("query_type", "Escalation Threshold"),
            "applications": rows
        }

    return None


async def handle_chat(
    message: str,
    session_id: str,
    officer: OfficerContext,
    db: AsyncSession
) -> Dict[str, Any]:
    """Alias/wrapper for process_chat to comply with prompt signature specifications"""
    return await process_chat(message, session_id, officer, db)



