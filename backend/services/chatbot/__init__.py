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
from .chat_application_queries import (
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
from .chat_corrections import (
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
from .chat_followups import (
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
from .chat_response_formatting import (
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
from .chat_routing import (
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
from .chat_service_codes import (
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
from .chat_shared import (
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
from .chat_smalltalk import (
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
from .chat_untracked_fields import (
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
from .chat_pipeline import early_guard_reply
from .chat_answer import build_answer
from .chat_dispatch import dispatch_intent
from .chat_dispatch_stream import dispatch_intent_stream
from .chat_pipeline import _bound

# This module's own logger (pipeline log records are named backend.services.chatbot).
logger = get_logger(__name__)

# Re-export everything orchestrator.py defines as if it had been written
# directly in this file (which it was, until the extraction). Explicitly
# excludes the package's own module metadata -- __name__, __spec__,
# __file__, __package__, __path__, __loader__, __doc__, __builtins__,
# __cached__ -- so this package's own identity is never overwritten by
# orchestrator's (a plain-submodule package, not a package __init__ itself,
# so it has the same set of dunders but they must describe THIS module).
from . import orchestrator as _orchestrator

_PACKAGE_METADATA = {
    "__name__", "__doc__", "__package__", "__loader__", "__spec__",
    "__file__", "__path__", "__builtins__", "__cached__", "__all__",
}
globals().update({
    k: v for k, v in vars(_orchestrator).items()
    if k not in _PACKAGE_METADATA and not (k.startswith("__") and k.endswith("__"))
})
del _orchestrator, _PACKAGE_METADATA
