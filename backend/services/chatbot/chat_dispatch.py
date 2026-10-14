"""
Intent dispatch stage of the NON-STREAMING chat pipeline (process_chat).

Given the routed intent, runs the deterministic handler that answers it:
fetches the structured data (from postgres.py, through the officer's
jurisdiction) or the ready answer text for intents that build their own.
One handler (a CAN-number answer) completes the turn itself -- it saves the
turn and hands back the final response, which the orchestrator returns.

The streaming pipeline has its own dispatch stage (chat_dispatch_stream.py):
the two chains have diverged (different branches, different output names),
so they are deliberately not merged.
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
    ASK_FOR_APP_NUMBER,
    _asks_distinct_service_count,
    _build_comparison,
    _fetch_last_application,
    _register_completion,
    _register_listing,
    _summarize_joint_owners,
)
from .chat_corrections import (
    _refine_field_visits,
    _single_period_range,
)
from .chat_followups import (
    _asks_applicant_focus,
    _expand_listed_details,
    _extract_app_number_from_context,
    _geo_scope,
    _survey_ref,
    _wants_applicant_card,
    _wants_workflow_history,
)
from .chat_pipeline import (
    StageOutcome,
    _UNSET,
)
from .chat_response_formatting import (
    _asked_completed_visits,
    _asked_open_visits,
    _asked_unscheduled,
    _channel_breakdown_note,
    _channel_scope_label,
    _extract_app_types,
    _is_count_only_query,
)
from .chat_service_codes import (
    _comparison_answer,
)
from .chat_shared import (
    _MONTH_NAMES,
    _SORT_OVERRIDE,
    _STATUS_LABELS,
    _asks_transfer_party,
    _build_jurisdiction_summary,
    _cm_strong,
    _explicit_status_request,
    _fetch_visit_plan,
    _period_from_message,
    _render_all_service_code_answer,
    _resolve_month_year,
    _whole_month_label,
    _year_match,
    extract_month_from_query,
)
from .chat_smalltalk import (
    _fee_lookup_data,
    _igrs_over_list_note,
)
from .chat_untracked_fields import (
    _sub_division_numbers,
    _transfer_party_answer,
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


async def dispatch_intent(
        *,
        _followup_ctx,
        _gate_app_number,
        _hook_done,
        _original_message,
        chat_history,
        db,
        intent,
        language,
        message,
        officer,
        response_text,
        session_id,
        start_time,
        structured_data,
        app_number=_UNSET,
        i=_UNSET,
        is_tamil=_UNSET,
        msg_lower=_UNSET,
        res_txt=_UNSET,
) -> StageOutcome:
    """Run the deterministic handler for the routed intent (non-streaming pipeline).

    Extracted verbatim from chatbot._process_chat_impl (L3294-5404 before
    extraction). Reads: _followup_ctx, _gate_app_number, _hook_done, _original_message, app_number, chat_history, db, i, intent, is_tamil, language, message, msg_lower, officer, res_txt, response_text, session_id, start_time, structured_data.
    Hands back (only if bound): _an, _app_no, _field_keywords, _isd_app_no, _missing, _tp_marker, app_type, apps, completed, days, e, is_tamil, overlap_date, response_text, structured_data, sub_date, survey_no, taluk, today, total, working_days.
    """
    if app_number is _UNSET:
        del app_number
    if i is _UNSET:
        del i
    if is_tamil is _UNSET:
        del is_tamil
    if msg_lower is _UNSET:
        del msg_lower
    if res_txt is _UNSET:
        del res_txt
    if intent == "compare_applications":
        # Both sides come from the register and the verdict is computed
        # here -- a comparison whose numbers came from the LLM is worse
        # than no comparison at all.
        structured_data = await _build_comparison(
            db, officer, message, language in ("ta", "tanglish"))
        structured_data["query_type"] = "Comparison"
        response_text = _comparison_answer(
            structured_data, language in ("ta", "tanglish"))

    elif intent == "all_service_code_applications":
        structured_data = await _register_listing(
            db, officer, message, session_id, chat_history, followup_ctx=_followup_ctx)
        response_text = _render_all_service_code_answer(
            structured_data, language in ("ta", "tanglish"),
            count_only=_is_count_only_query(message),
            distinct_services=_asks_distinct_service_count(message))

    elif intent in ("pending_applications", "isd_applications", "nisd_applications", "both_applications"):
        message_lower = message.lower()

        # Detect one or multiple application types (e.g. "isd", "nisd", "isd and nisd")
        app_type = _extract_app_types(message_lower, intent=intent)  # str, list, or None

        # Detect time of day session
        session_label = ""
        if any(w in message_lower for w in ["morning", "காலை"]):
            session_label = " (Morning Session: 09:00 AM – 01:00 PM)"
        elif any(w in message_lower for w in ["afternoon", "பிற்பகல்", "மதியம்"]):
            session_label = " (Afternoon Session: 02:00 PM – 05:00 PM)"
        elif any(w in message_lower for w in ["evening", "மாலை"]):
            session_label = " (Evening Session: 04:00 PM – 06:00 PM)"

        # Extract date range first (e.g. "between 2026-07-03 and 2026-07-20", "today", "yesterday")
        start_d, end_d = extract_date_range(message)
        # Months asked for as a union -- "June and July", "March and May",
        # "last month and the month before that". extract_date_range spans
        # from the first to the last, which is right for "from March to
        # May" and wrong for "March and May"; the segments keep the gap.
        month_scopes = extract_month_scopes(message)
        if month_scopes:
            start_d, end_d = month_scopes[0][0], month_scopes[-1][1]
        if session_label and not start_d and not end_d:
            start_d = date.today()
            end_d = date.today()

        # An open-ended range ("since 2026-07-01", "before 2026-08-01") only
        # sets one side. Treating that as "no date range" let the year/month
        # extraction below run alongside it and silently override the filter,
        # so either side present must count as a real date-range query.
        has_date_range = start_d is not None or end_d is not None
        is_negated_date_range = any(w in message_lower for w in [
            "not between", "not in", "outside", "other than", "இடைப்பட்டவை அல்ல", "இடையில் இல்லாத", "தவிர"
        ])

        # Extract year from message (only used when NO full date range is present)
        submission_year = None
        submission_month = None
        if not has_date_range:
            year_match = _year_match(message)
            submission_year = int(year_match.group(1)) if year_match else None
            # Extract month from message (handles English/Tamil with fuzzy matching)
            submission_month = extract_month_from_query(message)
            # "applications in June" names a month but no year — pin it to
            # the most recent June rather than every June on record.
            if submission_month and not submission_year:
                submission_year = _resolve_month_year(submission_month)

        # Extract geography filters
        taluk_name = extract_taluk_name(message)
        # "in block 0015", "in this block", "வார்டு 2" all name a scope.
        ward_num, block_num = await _geo_scope(
            db, session_id, message, chat_history, officer)

        # Extract the submission channel scope (CSC / citizen /
        # sub_registrar). A list, because an officer names one channel, two
        # ("CSC and SRO"), or all three ("from every channel") -- and
        # reading only the first answered "CSC and SRO" with the CSC half
        # and no sign that the other half had been dropped.
        channel_filter = extract_submission_channels(message) or None

        # Detect whether overdue vs non-overdue (on-time) filter is requested
        is_not_overdue = any(w in message_lower for w in [
            "not overdue", "non overdue", "non-overdue", "on time", "not late",
            "not delayed", "within sla", "தாமதமில்லாத", "தாமதம் இல்லாத", "காலதாமதமாகாத"
        ]) or bool(re.search(
            # the general negation words the rest of the app uses ("except", "without", …)
            # over "overdue" -- without this, "except overdue" / "excluding overdue" matched
            # none of the phrases above and fell through to the plain "overdue" match, showing
            # the overdue file instead of excluding it.
            r"\b(?:except|excluding|other\s+than|without|skip(?:ping)?)\b[\w\s]{0,15}\boverdue\b"
            r"|\boverdue\b[\w\s]{0,15}\b(?:excluded|not\s+included|left\s+out)\b"
            r"|thavir(?:thu|a)?[\w\s]{0,15}overdue|overdue[\w\s]{0,15}thavir(?:thu|a)?"
            r"|(?:தவிர்த்து|தவிர).{0,25}(?:தாமத|காலதாமத|ஓவர்)|(?:தாமத|காலதாமத|ஓவர்)\w*.{0,25}(?:தவிர்த்து|தவிர)",
            message_lower))
        # Word-boundary, not a bare substring: "escalated" contains
        # "late" (esca-LATE-d), so "how many escalated applications"
        # set is_overdue_requested=True and the reply named the wrong
        # status entirely ("There are no Overdue applications") even
        # though status_filter itself was correctly "escalated" --
        # the overdue flag drove the SENTENCE, independent of it.
        is_overdue_requested = bool(re.search(r'\b(?:overdue|late|delayed)\b', message_lower)) and not is_not_overdue
        is_overdue_filter = False if is_not_overdue else (True if is_overdue_requested else None)

        # Determine status filter
        # An application-type scope is a register question, not a desk one.
        # "show ISD applications" / "how many NISD applications do I have"
        # asks for the officer's whole ISD or NISD history; defaulting it to
        # `pending` answered 2 to an officer holding 9 ISD files, and 0 to
        # one holding 58 NISD. Both types work this way now, and get_officer_applications() already
        # suppresses the current-stage pin for an explicit type
        # (has_scope_filter), so this is the matching half of that rule.
        is_type_scoped = bool(app_type)
        _named_status = _explicit_status_request(message_lower)
        if _named_status is not None:
            # The officer named a status -- it wins over every scope default,
            # "all" included. "show all pending applications" was falling
            # through to the final elif below on the word "all" alone,
            # silently turning "pending" into every status and showing
            # approved/rejected files under a "pending" listing.
            status_filter = _named_status
        elif has_date_range or is_overdue_filter is not None:
            # Date-range or overdue/non-overdue query: show all statuses within that scope
            status_filter = None
        elif submission_year:
            status_filter = None  # Show all statuses when querying by specific year
        elif is_type_scoped or (channel_filter and not any(w in message_lower for w in ["pending", "நிலுவை"])):
            status_filter = None
            # "show ALL citizen applications" asks for every file of that
            # channel, rejected ones included -- a channel scope alone hides
            # them (standing rule), but "all" overrides it.
            if channel_filter and re.search(r'\b(?:all|every)\b|அனைத்து', message_lower):
                status_filter = ["pending", "in_progress", "escalated", "approved", "rejected"]
        else:
            status_filter = "pending"
            if "history" in message_lower or "approved n rejected" in message_lower or "approved and rejected" in message_lower:
                status_filter = ["approved", "rejected"]
            elif "complete" in message_lower or "approved" in message_lower:
                status_filter = "approved"
            elif "reject" in message_lower:
                status_filter = "rejected"
            elif "all" in message_lower:
                status_filter = ["pending", "in_progress", "escalated", "approved", "rejected"]
            elif re.search(r'\btotal\b', message_lower) or "மொத்த" in message_lower:
                # "show total applications" → all statuses; checked AFTER specific statuses
                # so "total completed" → approved, not all-statuses.
                # \btotal\b avoids matching "totally".
                status_filter = ["pending", "in_progress", "escalated", "approved", "rejected"]

        # Check if this is a follow-up query on an active/previously discussed single application.
        # IMPORTANT: For list intents (pending_applications etc.), we must NOT use implicit
        # continuation — that would cause "show applications" to pull the last-discussed app
        # from chat history and fetch only that single app's detail.
        # Only use allow_implicit_continuation if the message itself has an explicit reference
        # like "this application" / "that app" / "previous application".
        target_app_num = extract_application_number(message)
        if not target_app_num:
            # Only allow implicit continuation if the message has an explicit reference phrase
            # (e.g. "this app", "that application", "previous app"), NOT for bare list queries.
            _has_explicit_app_ref = any(p in message_lower for p in [
                "this application", "that application", "same application",
                "this app", "that app", "the application", "the app",
                "prev application", "previous application", "prev app", "previous app",
                "last application", "last app", "above application",
                "இந்த விண்ணப்பம்", "அந்த விண்ணப்பம்", "முந்தைய விண்ணப்பம்",
            ])
            if _has_explicit_app_ref:
                target_app_num = (_extract_app_number_from_context(message, chat_history, allow_implicit_continuation=False) or _gate_app_number)
        has_projected_cols = _get_projected_application_columns(message) is not None
        is_explicit_plural = any(w in message_lower for w in ["all", "every", "list all", "show all", "between", "today", "yesterday", "this week", "last week", "month", "முழு", "அனைத்து"])

        # "in ascending order", "newest first", "sorted by application
        # number" -- applied in SQL over the whole result set. None leaves
        # the default (oldest submission first) in place.
        _sort_field, _sort_dir = _SORT_OVERRIDE.get() or extract_sort_order(message) or (None, None)

        if target_app_num and not is_explicit_plural and has_projected_cols:
            app_detail = await get_application_detail(db, target_app_num, officer=officer)
            if app_detail and app_detail.get("found", True):
                structured_data = {
                    "applications": [app_detail],
                    "count": 1,
                    "query_type": f"Application {target_app_num}"
                }
            else:
                structured_data = await get_pending_applications(
                    db, officer, 
                    application_type=app_type,
                    status=status_filter, 
                    submission_year=submission_year,
                    submission_month=submission_month,
                    taluk_name=taluk_name,
                    ward_number=ward_num,
                    block_number=block_num,
                    start_date=start_d,
                    end_date=end_d,
                    date_ranges=month_scopes or None,
                    is_overdue=is_overdue_filter,
                    exclude_date_range=is_negated_date_range,
                    submission_channel=channel_filter,
                    sort_by=_sort_field,
                    sort_dir=_sort_dir,
                )
        else:
            structured_data = await get_pending_applications(
                db, officer, 
                application_type=app_type,   # str, list, or None
                status=status_filter, 
                submission_year=submission_year,
                submission_month=submission_month,
                taluk_name=taluk_name,
                ward_number=ward_num,
                block_number=block_num,
                start_date=start_d,
                end_date=end_d,
                date_ranges=month_scopes or None,
                is_overdue=is_overdue_filter,
                exclude_date_range=is_negated_date_range,
                submission_channel=channel_filter,
                sort_by=_sort_field,
                sort_dir=_sort_dir,
            )
        if is_negated_date_range:
            structured_data["exclude_date_range"] = True

        # Build human-readable type label for the query title
        if isinstance(app_type, list):
            type_str = " " + " & ".join(app_type)   # e.g. " ISD & NISD"
        elif app_type:
            type_str = f" {app_type}"
        else:
            type_str = ""

        year_str = f" in {submission_year}" if submission_year else ""

        # Date range label
        date_range_str = ""
        if month_scopes:
            date_range_str = f" in {format_month_scopes(month_scopes)}{session_label}"
        elif has_date_range:
            if is_negated_date_range:
                date_range_str = f" (not {start_d} to {end_d})"
            elif start_d and end_d and start_d == end_d:
                if start_d == date.today():
                    date_range_str = f" Received Today ({start_d}){session_label}"
                elif start_d == date.today() - timedelta(days=1):
                    date_range_str = f" Received Yesterday ({start_d}){session_label}"
                elif start_d == date.today() - timedelta(days=2):
                    date_range_str = f" Received Day Before Yesterday ({start_d}){session_label}"
                elif start_d == date.today() + timedelta(days=1):
                    date_range_str = f" for Tomorrow ({start_d}){session_label}"
                else:
                    date_range_str = f" Received on {start_d}{session_label}"
            elif start_d and end_d and _whole_month_label(start_d, end_d):
                # "this month" / "last month" / a full calendar month — say
                # the month, not the pair of boundary dates.
                date_range_str = f" in {_whole_month_label(start_d, end_d)}{session_label}"
            elif start_d and end_d:
                date_range_str = f" ({start_d} to {end_d}){session_label}"
            elif start_d and not end_d:
                # Open-ended range: "since 2026-07-01", "starting from ..."
                date_range_str = f" (from {start_d} onwards){session_label}"
            elif end_d and not start_d:
                # Open-ended range: "before 2026-08-01", "until ..."
                date_range_str = f" (up to {end_d}){session_label}"

        # Month label
        month_str = ""
        if submission_month:
            month_names = _MONTH_NAMES
            # Month and year read as one scope — "in March 2026", not
            # " March" followed by " in 2026".
            if submission_year:
                month_str = f" in {month_names[submission_month]} {submission_year}"
                year_str = ""
            else:
                month_str = f" in {month_names[submission_month]}"

        # A ward/block-scoped list has to say so. Without this the answer
        # read "There are 2 applications in your jurisdiction" whether or
        # not the block filter had been applied, and the officer could not
        # tell the scoped count from the whole queue.
        _geo_bits = []
        if ward_num:
            _geo_bits.append(f"Ward {ward_num}")
        if block_num:
            _geo_bits.append(f"Block {block_num}")
        geo_str = f" in {' / '.join(_geo_bits)}" if _geo_bits else ""

        # The channel belongs in the label too: "All Applications: no
        # applications found" gave no sign that the channel filter had been
        # applied, so an empty result read as an empty jurisdiction.
        chan_str = _channel_scope_label(channel_filter)

        # "do they have an IGRS number" is a yes/no, and a table is not a
        # yes/no -- lead with the answer.
        _igrs_note = _igrs_over_list_note(
            message, structured_data.get("applications") or [])
        if _igrs_note:
            structured_data["lead_note"] = _igrs_note

        # A list covering more than one channel says the split in words
        # above the table, shows a CHANNEL column so each row can be
        # placed, and records the scope so a follow-up ("how many of them
        # are approved?") stays inside it.
        if channel_filter:
            _chans = list(channel_filter)
            structured_data["submission_channels"] = _chans
            if len(_chans) == 1:
                structured_data["submission_channel"] = _chans[0]
            else:
                structured_data["show_channel_column"] = True
                _rows = structured_data.get("applications") or []
                _seen_chans = {r.get("submission_channel") for r in _rows}
                _missing = [c for c in _chans if c not in _seen_chans]
                _rejected_by_chan = (
                    await count_rejected_by_channel(db, officer, _missing)
                    if _missing and not _explicit_status_request(message_lower)
                    else {})
                _breakdown = _channel_breakdown_note(
                    _chans, _rows, language in ("ta", "tanglish"),
                    _rejected_by_chan)
                if _breakdown:
                    _lead = structured_data.get("lead_note")
                    structured_data["lead_note"] = (
                        f"{_lead} {_breakdown}" if _lead else _breakdown)

        if is_not_overdue:
            structured_data["query_type"] = f"Non-Overdue{type_str}{chan_str} Applications{month_str}{year_str}{date_range_str}{geo_str}"
        elif is_overdue_requested:
            structured_data["query_type"] = f"Overdue{type_str}{chan_str} Applications{month_str}{year_str}{date_range_str}{geo_str}"
        elif taluk_name:
            structured_data["query_type"] = f"{type_str.strip()}{chan_str} Applications in {taluk_name}{month_str}{year_str}{date_range_str}{geo_str}".lstrip()
        elif status_filter == ["approved", "rejected"]:
            structured_data["query_type"] = f"SIS{type_str} History (Approved & Rejected){month_str}{year_str}{date_range_str}{geo_str}"
        elif isinstance(status_filter, list) and len(status_filter) == 5 and channel_filter:
            structured_data["query_type"] = f"All{type_str}{chan_str} Applications{month_str}{year_str}{date_range_str}{geo_str}"
        elif isinstance(status_filter, list) and set(status_filter) == {"pending", "in_progress", "approved", "rejected"}:
            structured_data["query_type"] = f"Total{type_str}{chan_str} Applications{month_str}{year_str}{date_range_str}{geo_str}"
        elif status_filter is None:
            structured_data["query_type"] = f"All{type_str}{chan_str} Applications{month_str}{year_str}{date_range_str}{geo_str}"
        elif status_filter == "approved":
            structured_data["query_type"] = f"Approved{type_str}{chan_str} Applications{month_str}{year_str}{date_range_str}{geo_str}"
        elif status_filter == "rejected":
            structured_data["query_type"] = f"Rejected{type_str}{chan_str} Applications{month_str}{year_str}{date_range_str}{geo_str}"
        elif isinstance(status_filter, list):
            # The officer named more than one status ("pending and
            # completed"). Falling to the Pending catch-all below labelled
            # all of them pending -- 51 approved-and-pending NISD files
            # reported as "51 pending NISD applications".
            _st_label = " & ".join(_STATUS_LABELS.get(s, s.title()) for s in status_filter)
            structured_data["query_type"] = f"{_st_label}{type_str}{chan_str} Applications{month_str}{year_str}{date_range_str}{geo_str}"
        else:
            # A scalar status other than approved/rejected -- pending,
            # escalated, in_progress -- fell to a hardcoded "Pending"
            # label regardless of which one was actually asked for:
            # "show my escalated applications" (correctly spelled, no
            # typo at all) reported "There are no pending applications",
            # naming a different status than the one filtered on.
            _scalar_label = _STATUS_LABELS.get(status_filter, str(status_filter).title()) \
                if isinstance(status_filter, str) else "Pending"
            structured_data["query_type"] = f"{_scalar_label}{type_str}{chan_str} Applications{month_str}{year_str}{date_range_str}{geo_str}"

    elif intent == "overdue_applications":
        # Extract application type if mentioned in message
        app_type = None
        message_lower = message.lower()
        # "nisd" contains "isd", so the ISD test has to be word-bounded and
        # NISD has to be checked first -- otherwise "overdue NISD
        # applications" was filtered to ISD and answered with the wrong type.
        if re.search(r'\bnisd\b|\b0153\b', message_lower):
            app_type = "NISD"
        elif re.search(r'\bisd\b|\b0154\b', message_lower):
            app_type = "ISD"

        min_days_overdue = None
        match_days = re.search(r'(?:overdue|late|delayed)\s+(?:by\s+)?(\d+)\s*days?', message_lower) or \
                     re.search(r'(\d+)\s*days?\s+(?:overdue|late|delayed)', message_lower)
        if match_days:
            min_days_overdue = int(match_days.group(1))

        start_d, end_d = extract_date_range(message)
        if not (start_d and end_d):
            start_d, end_d, _ = _period_from_message(message)

        # The overdue queue is scoped by ward/block like every other list.
        _ov_ward, _ov_block = await _geo_scope(
            db, session_id, message, chat_history, officer)
        _ov_geo_bits = []
        if _ov_ward:
            _ov_geo_bits.append(f"Ward {_ov_ward}")
        if _ov_block:
            _ov_geo_bits.append(f"Block {_ov_block}")
        _ov_geo = f" in {' / '.join(_ov_geo_bits)}" if _ov_geo_bits else ""

        structured_data = await get_overdue_applications(
            db, officer, 
            application_type=app_type, 
            min_days_overdue=min_days_overdue,
            start_date=start_d,
            end_date=end_d,
            ward_number=_ov_ward,
            block_number=_ov_block,
        )
        structured_data["min_days_overdue"] = min_days_overdue
        days_str = f" by {min_days_overdue}+ Days" if min_days_overdue else ""
        # Say the month when the range is exactly one, same as the lists do.
        _ov_month = _whole_month_label(start_d, end_d)
        range_str = (f" in {_ov_month}" if _ov_month
                     else (f" ({start_d} to {end_d})"
                           if (start_d and end_d and start_d != end_d) else ""))
        if app_type:
            structured_data["query_type"] = f"Overdue {app_type} Applications{days_str}{range_str}{_ov_geo}"
        else:
            structured_data["query_type"] = f"Overdue Applications{days_str}{range_str}{_ov_geo}"

    elif intent == "officer_workload":
        structured_data = await get_officer_workload(db, officer)
        structured_data["query_type"] = "Officer Workload Summary"

    elif intent == "fv_overdue_inspections":
        # Get field visits that are overdue (scheduled date in past, not completed)
        try:
            from sqlalchemy.orm import joinedload

            today = date.today()
            logger.info("=== OVERDUE FIELD VISITS QUERY ===")
            logger.info(f"Today: {today}")
            logger.info(f"Officer ID: {officer.officer_id}")

            # Add jurisdiction filter to ensure only applications within officer's jurisdiction
            jurisdiction_filter = await get_jurisdiction_filter(db, officer)
            # jurisdiction_filter returns a list, we need to unpack it
            jur_conditions = jurisdiction_filter if isinstance(jurisdiction_filter, list) else [jurisdiction_filter]

            # "Show my overdue field visits in Ward 001" named an EXPLICIT
            # scope narrower than the officer's own jurisdiction -- the
            # foreign-ward check above already refuses a ward the officer
            # does not hold at all; this narrows the query itself to one
            # that is held. Without it every ward/block variant returned
            # the SAME unfiltered jurisdiction-wide list, silently
            # dropping a scope the officer explicitly asked for.
            _ovd_ward, _ovd_block = await _geo_scope(db, session_id, message, chat_history, officer)

            overdue_visits_stmt = select(FieldVisit).options(
                joinedload(FieldVisit.application).joinedload(Application.survey_number).joinedload(SurveyNumber.block)
            ).join(
                Application, FieldVisit.application_id == Application.id
            ).join(
                SurveyNumber, Application.survey_number_id == SurveyNumber.id
            ).join(
                # The geography chain must be joined explicitly: jur_conditions reference
                # Block/Ward/Town/Taluk/District, and without these joins SQLAlchemy adds
                # them as an uncorrelated FROM (cartesian product), silently voiding the filter.
                Block, SurveyNumber.block_id == Block.id
            ).join(
                Ward, Block.ward_id == Ward.id
            ).join(
                Town, Ward.town_id == Town.id
            ).join(
                Taluk, Town.taluk_id == Taluk.id
            ).join(
                District, Taluk.district_id == District.id
            ).where(
                and_(
                    FieldVisit.id.in_(_field_visit_jurisdiction_subquery(await get_jurisdiction_filter(db, officer))),
                    FieldVisit.scheduled_date.isnot(None),
                    FieldVisit.scheduled_date < today,
                    FieldVisit.status.in_(['scheduled', 'rescheduled', 'overdue']),
                    or_(*jur_conditions)  # the conditions are alternatives (one per jurisdiction), not all required
                )
            ).order_by(FieldVisit.scheduled_date.asc())
            if _ovd_ward:
                overdue_visits_stmt = overdue_visits_stmt.where(Ward.ward_number == str(_ovd_ward))
            if _ovd_block:
                overdue_visits_stmt = overdue_visits_stmt.where(Block.block_number == str(_ovd_block))

            overdue_visits = (await db.execute(overdue_visits_stmt)).unique().scalars().all()
            logger.info(f"Overdue visits found: {len(overdue_visits)}")

            overdue_list = []
            for visit in overdue_visits:
                app = visit.application
                if app:
                    app_type = app.application_type if app.application_type else "N/A"
                    logger.info(f"Overdue visit: App={app.application_number}, Type={app_type}, Scheduled={visit.scheduled_date}, Status={visit.status}")
                    overdue_list.append({
                        "application_number": app.application_number,
                        "type": app_type,  # Use variable with fallback
                        "status": visit.status,  # Field visit status, not application status
                        "stage": app.current_stage if app.current_stage else "N/A",
                        "survey_no": app.survey_number.survey_no if app.survey_number else "N/A",
                        "block_number": app.survey_number.block.block_number if app.survey_number and app.survey_number.block else "N/A",
                        "scheduled_date": visit.scheduled_date.isoformat() if visit.scheduled_date else "Not Scheduled",
                        "submission_date": app.submission_date.isoformat() if app.submission_date else "N/A",
                        # Every row this query returns is past its scheduled
                        # date by construction (the WHERE clause above already
                        # enforces it) -- without this flag, build_context()'s
                        # generic field_visits splitter (which relies on each
                        # row's own is_overdue to tell an overdue row from an
                        # on-schedule one) saw none of them as overdue and
                        # computed fv_overdue_count=0, so any ordinal follow-up
                        # naming "overdue" sliced the carried list to nothing.
                        "is_overdue": True,
                    })
                    # Log the complete dictionary for debugging
                    logger.info(f"  Complete data: {overdue_list[-1]}")

            structured_data = {
                "overdue_visits_count": len(overdue_list),
                "field_visits": overdue_list,
                "query_type": "Overdue Field Visits",
                "ward_number": _ovd_ward,
                "block_number": _ovd_block,
            }
            if not overdue_list:
                # "All field visits are on schedule" is an overstatement
                # when nothing is on the schedule at all. An application
                # waiting 91 days with no visit BOOKED carries no
                # scheduled_date, so it can never be past one and never
                # appears above -- and the officer reads "none overdue" as
                # "nothing to do". Same failure CLAUDE.md records for
                # "which field visits are pending".
                try:
                    _await = await get_unscheduled_visits(db, officer)
                    _n_await = int(_await.get("count", 0) or 0)
                    structured_data["awaiting_scheduling_count"] = _n_await
                    _is_ta_fv = language in ("ta", "tanglish")
                    if _n_await and _is_ta_fv:
                        structured_data["empty_message"] = (
                            f"திட்டமிட்ட தேதியைக் கடந்த கள ஆய்வு எதுவும் இல்லை. "
                            f"ஆனால் {_n_await} விண்ணப்பத்திற்கு இதுவரை கள ஆய்வே "
                            f"பதிவு செய்யப்படவில்லை — எனவே அவற்றுக்கு தாமதமாக "
                            f"ஆக ஒரு தேதியே இல்லை. 'அடுத்து எந்த விண்ணப்பத்திற்கு "
                            f"கள ஆய்வு செய்ய வேண்டும்?' எனக் கேளுங்கள்.")
                    elif _n_await:
                        structured_data["empty_message"] = (
                            f"No field visit is past its scheduled date. But "
                            f"{_n_await} application(s) are still awaiting a "
                            f"visit that has never been booked, so they carry "
                            f"no scheduled date to be late against — ask "
                            f"\"which application should I field visit next?\" "
                            f"to see them.")
                except Exception as _e:
                    logger.warning(f"awaiting-visit count skipped: {_e}")
        except Exception as e:
            logger.error(f"Error getting overdue field visits: {e}", exc_info=True)
            structured_data = {"error": str(e), "field_visits": []}


    elif intent in ["field_visits", "fv_between_dates"]:
        import calendar

        msg_lower = message.lower()
        start_d, end_d = extract_date_range(message)
        if not start_d and not end_d:
            start_d, end_d = _single_period_range(message)
        if not start_d and not end_d:
            # `_single_period_range` only takes an EXPLICIT year ("June
            # 2026"). "field visits in March" / "field visits in October"
            # named no year and fell straight through both helpers above,
            # so the bare month was silently dropped and the unfiltered
            # summary answered instead -- the same failure the applications
            # listing already avoids via `_period_from_message`, which
            # infers the most recent occurrence of that month
            # (_resolve_month_year). Reused here rather than duplicated.
            start_d, end_d, _ = _period_from_message(message)

        # Detect negation: "not between", "outside", "except between", "not in
        # range" caught the RANGE phrasings, but "not on Monday" / "not in
        # July" / "not in 2026" -- a single day/month/year negated the same
        # way -- named none of those exact phrases, so `exclude_range`
        # stayed False and the date extracted from "Monday"/"July"/"2026"
        # was applied as an ordinary POSITIVE filter: "field visits not on
        # Monday" showed the visits ON that Monday, the literal opposite of
        # the question. `\bnot\s+(?:on|in|during|for)\b` covers the single-
        # period shape too, alongside the original range phrasings.
        # "neither today nor tomorrow" -- `extract_date_range` above only
        # ever returns ONE range, so this catches just one of the two
        # named days ("tomorrow"), same as an ordinary single-day
        # question would. Marking it excluded rather than included is
        # still a real improvement over the alternative: unmarked, that
        # one day was applied as a POSITIVE filter and the answer showed
        # visits ON tomorrow for a question that named tomorrow as one of
        # the two days to stay OFF of. Excluding the day it did catch is
        # honest; correctly excluding both named days would need this
        # function to return more than one range, which it does not.
        exclude_range = bool(re.search(r"\bnot\s+(?:on|in|during|for)\b", msg_lower)
                             or re.search(r"\bneither\b.*\bnor\b", msg_lower)) or any(
            w in msg_lower for w in [
                "not between", "outside", "except between", "not in range",
                "outside the range", "exclude", "excluding"
            ])

        # Extract application type filter(s) from message — supports multi-type queries
        # e.g. "isd and nisd", "all types"
        app_type_filter = []
        if any(w in msg_lower for w in ["nisd", "non-isd", "non isd", "transfer"]):
            app_type_filter.append("NISD")
        if any(w in msg_lower for w in ["isd", "subdivision", "sub division", "sub-division"]):
            if "NISD" not in app_type_filter:  # avoid double-match since "nisd" contains "isd"
                app_type_filter.append("ISD")
        # Normalise: None means no filter (all types)
        app_type_filter = app_type_filter if app_type_filter else None
        type_label = f" {'+'.join(app_type_filter)}" if app_type_filter else ""

        to_be_visited = any(w in msg_lower for w in [
            "needed to be visited", "to be visited", "need to visit", "need to be visited",
            "pending visit", "yet to visit",
            # "which field visits are pending / remaining / outstanding" is
            # the same question. We are already inside the field-visit
            # branch, so a bare "pending" here can only mean the visit.
            "are pending", "is pending", "still pending", "remaining",
            "outstanding", "not yet visited", "still to visit",
            # "upcoming" is deliberately NOT here -- see the dedicated
            # "upcoming" branch below. Folding it into this list answered
            # "show upcoming scheduled field visits" with the SAME
            # overdue+awaiting "to do" queue the prioritization answer
            # already gives under a different heading, which is the exact
            # conflation the "upcoming calendar" framing must not make.
        ])

        # Detect morning / afternoon / evening time session
        session_label = ""
        if any(w in msg_lower for w in ["morning", "காலை"]):
            session_label = " (Morning Session: 09:00 AM – 01:00 PM)"
        elif any(w in msg_lower for w in ["afternoon", "பிற்பகல்", "மதியம்"]):
            session_label = " (Afternoon Session: 02:00 PM – 05:00 PM)"
        elif any(w in msg_lower for w in ["evening", "மாலை"]):
            session_label = " (Evening Session: 04:00 PM – 06:00 PM)"

        # If user asked for morning/afternoon without specific date, default to today
        if session_label and not start_d and not end_d:
            start_d = date.today()
            end_d = date.today()

        status_filter = None
        if _asked_open_visits(msg_lower):
            # "uncompleted" / "incomplete" / "not completed" -- the visits
            # still to be made. Checked BEFORE the completed cue because
            # every one of these phrasings CONTAINS it as a substring, and
            # the old unbounded test therefore answered a request for the
            # outstanding visits with the finished ones: not an empty
            # result, the exact opposite set.
            to_be_visited = True
            status_filter = None
            query_type = "Field Visits Still To Be Made"
        elif _asked_completed_visits(msg_lower):
            # "how many field visits are completed" used to fall through to
            # the unfiltered summary and answer with every visit, finished
            # or not -- the question answered with a superset of itself.
            status_filter = "completed"
            query_type = "Completed Field Visits"
        elif _asked_unscheduled(msg_lower, exclude_range):
            # Unscheduled applications have NO FieldVisit row — they are
            # Applications with field_visit_scheduled==False. Passing
            # status_filter='unscheduled' to get_field_visits() always
            # returned 0 results because it queries FieldVisit.status.
            # Call get_unscheduled_visits() directly instead.
            _unsch = await get_unscheduled_visits(db, officer)
            _unsch["query_type"] = "Unscheduled Field Visits"
            _unsch["status_filter"] = "unscheduled"
            structured_data = _unsch
        elif "upcoming" in msg_lower and not start_d and not end_d:
            # "upcoming scheduled field visits" asks for genuinely FUTURE
            # booked visits -- distinct from both the overdue+awaiting
            # "to be visited" queue (see the comment on `to_be_visited`
            # above) and from a bare "scheduled" query with no date
            # floor, which would still include a visit whose status is
            # 'scheduled' but whose date has already passed --
            # overdue, not upcoming, by the same date-based rule
            # get_visit_plan() already uses to tell the two apart.
            status_filter = "scheduled"
            start_d = date.today()
            query_type = "Upcoming Scheduled Field Visits"
        elif intent == "fv_between_dates" or start_d or end_d or "between" in msg_lower or to_be_visited:
            # "Which field visits are pending?" carries no date, and it must
            # not be given one. Defaulting to the current month answered "no
            # field visits scheduled" to an officer whose one outstanding
            # visit was overdue from July -- the very visit the question was
            # asking about. The month default belongs to date-shaped
            # questions ("field visits this month"), not to this one.
            _open_ended_to_visit = (
                to_be_visited and intent != "fv_between_dates"
                and "between" not in msg_lower
            )
            if not start_d and not end_d and not _open_ended_to_visit:
                today = date.today()
                start_d = today.replace(day=1)
                _, last_day = calendar.monthrange(today.year, today.month)
                end_d = today.replace(day=last_day)
            type_label = f" {'+'.join(app_type_filter)}" if app_type_filter else ""
            if exclude_range and start_d and end_d:
                query_type = (f"{type_label} Field Visits Outside Dates".strip())
            elif start_d and end_d and start_d == end_d:
                if start_d == date.today() + timedelta(days=2):
                    query_type = (f"{type_label} Field Visits for Day After Tomorrow ({start_d}){session_label}".strip())
                elif start_d == date.today() + timedelta(days=1):
                    query_type = (f"{type_label} Field Visits for Tomorrow ({start_d}){session_label}".strip())
                elif start_d == date.today():
                    query_type = (f"{type_label} Field Visits for Today ({start_d}){session_label}".strip())
                elif start_d == date.today() - timedelta(days=1):
                    query_type = (f"{type_label} Field Visits for Yesterday ({start_d}){session_label}".strip())
                elif start_d == date.today() - timedelta(days=2):
                    query_type = (f"{type_label} Field Visits for Day Before Yesterday ({start_d}){session_label}".strip())
                else:
                    query_type = (f"{type_label} Field Visits on {start_d}{session_label}".strip())
            else:
                _fv_month = _whole_month_label(start_d, end_d)
                query_type = ("Field Visits Needed To Be Visited" if to_be_visited
                              else (f"{type_label} Field Visits in {_fv_month}".strip()
                                    if _fv_month
                                    else f"{type_label} Field Visits Between Dates".strip()))
        elif "scheduled" in msg_lower or "visit date" in msg_lower or "when" in msg_lower or "schedule" in msg_lower:
            status_filter = "scheduled"
            query_type = "Scheduled Field Visits"
        else:
            type_label = f" {'+'.join(app_type_filter)}" if app_type_filter else ""
            query_type = f"{type_label} Field Visits Summary".strip()

        # "Show its field visit" after an application was confirmed asks about
        # THAT application, not the whole jurisdiction. Scope to it whenever the
        # gate confirmed one and the officer did not ask for a broader list.
        _fv_app_no = _gate_app_number if not (start_d or end_d or app_type_filter) else None
        structured_data = await get_field_visits(
            db, officer,
            status_filter=status_filter,
            start_date=start_d,
            end_date=end_d,
            to_be_visited_only=to_be_visited,
            application_type=app_type_filter,
            exclude_date_range=exclude_range,
            application_number=_fv_app_no
        )
        _refine_field_visits(structured_data, message, _fv_app_no)
        if _fv_app_no:
            query_type = f"Field Visit for {_fv_app_no}"
            structured_data["application_number"] = _fv_app_no
        structured_data["query_type"] = query_type
        if start_d:
            structured_data["start_date"] = start_d.isoformat()
        if end_d:
            structured_data["end_date"] = end_d.isoformat()
        structured_data["to_be_visited_only"] = to_be_visited
        # Told to the renderer so a filtered list is never captioned with a
        # completed/outstanding split that only makes sense for the whole set.
        structured_data["status_filter"] = status_filter

    elif intent == "active_applications_taluks":
        query = select(Application, Taluk.name).join(
            SurveyNumber, Application.survey_number_id == SurveyNumber.id
        ).join(
            Block, SurveyNumber.block_id == Block.id
        ).join(
            Ward, Block.ward_id == Ward.id
        ).join(
            Town, Ward.town_id == Town.id
        ).join(
            Taluk, Town.taluk_id == Taluk.id
        ).where(
            and_(
                Application.id.in_(_application_jurisdiction_subquery(await get_jurisdiction_filter(db, officer))),
                Application.current_stage == officer.officer_stage,
                Application.current_status.in_(["pending", "in_progress"])
            )
        )
        result = await db.execute(query)
        rows = result.all()
        from collections import Counter
        taluk_counts = Counter([row[1] for row in rows])
        structured_data = {
            "total_active": len(rows),
            "taluk_counts": dict(taluk_counts),
            "query_type": "Active Applications by Taluk"
        }

    elif intent == "highest_priority_applications":
        # Use the dedicated high priority query function
        # This returns applications with is_overdue=True OR priority_flag=True
        from backend.services.postgres import get_highest_priority_applications

        # Extract application type if mentioned
        app_type = None
        message_lower = message.lower()
        # word-bounded, NISD first: "nisd" contains "isd"
        if re.search(r'\bnisd\b|\b0153\b', message_lower):
            app_type = "NISD"
        elif re.search(r'\bisd\b|\b0154\b', message_lower):
            app_type = "ISD"

        structured_data = await get_highest_priority_applications(db, officer, application_type=app_type)
        structured_data["query_type"] = "High Priority Applications"

        # Log for debugging
        logger.info(f"🔥 High priority applications query: found {structured_data.get('count', 0)} applications")
        logger.info(f"   Applications: {[app['application_number'] for app in structured_data.get('applications', [])]}")

    elif intent == "assigned_today":
        today = date.today()
        query = select(func.count(Application.id)).where(
            and_(
                Application.id.in_(_application_jurisdiction_subquery(await get_jurisdiction_filter(db, officer))),
                Application.current_stage == officer.officer_stage,
                Application.submission_date == today
            )
        )
        res = await db.execute(query)
        structured_data = {
            "count": res.scalar(),
            "query_type": "Applications Assigned Today"
        }

    elif intent == "immediate_action":
        from sqlalchemy.orm import joinedload
        from datetime import date as _date_imm
        from backend.utils import sla as _sla_imm

        # Get all pending/in-progress applications assigned to this officer
        apps_query = select(Application).options(
            joinedload(Application.survey_number).joinedload(SurveyNumber.block).joinedload(Block.ward).joinedload(Ward.town)
        ).where(
            and_(
                Application.id.in_(_application_jurisdiction_subquery(await get_jurisdiction_filter(db, officer))),
                Application.current_stage == officer.officer_stage,
                Application.current_status.in_(["pending", "in_progress"])
            )
        ).order_by(Application.submission_date.asc())

        res_apps = await db.execute(apps_query)
        all_apps = res_apps.scalars().all()

        # Two different clocks (backend/utils/sla.py): an ISD file's field-visit
        # deadline is 15 working days, but NISD has no field visit at all -- its
        # own service SLA runs 15-20. A flat ">15 working days = overdue" used to
        # apply the ISD-only field-visit figure to NISD too, so a NISD file at 16
        # working days (still inside its own 15-20 day SLA) was reported "Action
        # Required" with "1 day overdue", which it was not.
        _today_imm = _date_imm.today()
        rows = []

        for a in all_apps:
            if not a.submission_date:
                continue

            working_days = _sla_imm.working_days_between(a.submission_date, _today_imm)
            _state, _lo, _hi = _sla_imm.sla_state(a.application_type, working_days)
            _fv_overdue = _sla_imm.field_visit_overdue(
                a.application_type, a.current_status, a.submission_date, _today_imm)
            if _state != "past" and not _fv_overdue:
                continue
            days_overdue = (working_days - _hi if _state == "past"
                            else working_days - _sla_imm.FIELD_VISIT_DEADLINE_WD)

            sn = a.survey_number
            bl = sn.block if sn else None
            w = bl.ward if bl else None
            t = w.town if w else None

            rows.append({
                "application_number": a.application_number,
                "type": a.application_type,
                # survey_no/block_number were omitted here, so the table
                # rendered "N/A" for both even though the joinedload had
                # already fetched them.
                "survey_no": sn.survey_no if sn else "N/A",
                "block_number": bl.block_number if bl else "N/A",
                "town_name": t.name if t else "N/A",
                "ward_number": w.ward_number if w else "N/A",
                "status": "Action Required",
                "current_stage": a.current_stage,
                "submission_date": a.submission_date.isoformat(),
                "working_days_elapsed": working_days,
                "days_overdue": days_overdue
            })

        structured_data = {
            "applications": rows,
            # "apps" kept alongside "applications" so both response phases
            # can read either key without drifting apart again.
            "apps": [r["application_number"] for r in rows],
            "query_type": "Immediate Action Required — Overdue Applications"
        }


    elif intent == "awaiting_field_visit":
        query = select(func.count(FieldVisit.id)).where(
            and_(
                FieldVisit.id.in_(_field_visit_jurisdiction_subquery(await get_jurisdiction_filter(db, officer))),
                FieldVisit.status.in_(["scheduled", "unscheduled"])
            )
        )
        res = await db.execute(query)
        structured_data = {
            "count": res.scalar(),
            "query_type": "Awaiting Field Visit"
        }

    elif intent == "can_number_info":
        # "What is the CAN number of 2026/0153/28/001854?" asks for the value
        # on that file, and "was CAN 1332... taken at a CSC?" asks about one
        # number. The generic explainer is only right when the officer named
        # neither -- it was being returned for both.
        _can_app_no = extract_application_number(message) or _gate_app_number
        if not _can_app_no:
            _can_app_no = _extract_app_number_from_context(message, chat_history, allow_implicit_continuation=True)
        _can_token = re.search(r'\b(\d{12,15})\b', message)
        _can_details = None
        if _can_app_no or _can_token:
            _can_details = await get_can_details(
                db, officer,
                application_number=_can_app_no,
                can_number=None if _can_app_no else _can_token.group(1),
            )
        if _can_details:
            # Check if user wants JUST the CAN number (not full details)
            _msg_lower_can = message.lower()
            _asking_just_number = (
                any(p in _msg_lower_can for p in [
                    "what is the can", "what's the can", "give can",
                    "can number is", "tell can", "can no", "can number of",
                    "display can", "can num", "show me can", "show can",
                ])
            ) and not any(re.search(rf'\b{re.escape(w)}\b', _msg_lower_can) for w in [
                "how", "why", "explain", "details", "information", "assigned", "assigns"
            ]) and not any(p in _msg_lower_can for p in [
                "about can", "what does", "how does"
            ])

            if _asking_just_number and _can_details.get("found"):
                # Return ONLY the CAN number, not the full table
                _can_val = _can_details.get("can_number", "")
                _app_no = _can_details.get("application_number", "")
                is_ta_can = language in ("ta", "tanglish")

                if _can_val and _can_val != "not recorded":
                    _can_len = len(_can_val)
                    # Name the counter that issued the number and the
                    # channel the file came in through. The short answer
                    # used to give the digits alone, which invites the
                    # reading CLAUDE.md warns against -- that the LENGTH is
                    # the channel. It is not: 15 digits means a CSC
                    # counter issued the number, 12 the TN portal, and the
                    # channel is a separate fact derived from source_name
                    # and camp_flag.
                    _issuer = ("a CSC counter"
                               if str(_can_val).startswith("133")
                               else "the TN citizen portal")
                    _issuer_ta = ("CSC மையம்"
                                  if str(_can_val).startswith("133")
                                  else "தமிழ்நாடு குடிமக்கள் இணையதளம்")
                    _chan_txt = _can_details.get("channel") or ""
                    if is_ta_can:
                        res_txt = (f"விண்ணப்பம் {_app_no}-க்கான CAN எண்: "
                                   f"**{_can_val}** ({_can_len} இலக்கங்கள்) — "
                                   f"{_issuer_ta} வழங்கியது.")
                        if _chan_txt:
                            res_txt += f" விண்ணப்பம் வந்த வழி: {_chan_txt}."
                    else:
                        res_txt = (f"CAN number for application {_app_no}: "
                                   f"**{_can_val}** ({_can_len} digits) — "
                                   f"issued by {_issuer}.")
                        if _chan_txt:
                            res_txt += f" The application itself came in via {_chan_txt}."
                else:
                    if is_ta_can:
                        res_txt = f"விண்ணப்பம் {_app_no}-க்கு CAN எண் பதிவு செய்யப்படவில்லை."
                    else:
                        res_txt = f"No CAN number recorded for application {_app_no}."

                await _cb().save_chat_messages(
                    db=db, session_id=session_id, user_message=_original_message,
                    assistant_message=res_txt, language=language,
                    response_time_ms=int((time.time() - start_time) * 1000),
                    officer_id=officer.officer_id if officer else None
                )
                return StageOutcome(response={
                    "response": res_txt,
                    "language": language,
                    "intent": "can_number_info",
                    "sources": [],
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "context_used": True,
                    "response_time_ms": int((time.time() - start_time) * 1000),
                    "table_data": None
                }, early=True)
            else:
                # User wants full details or asking "how/why" questions
                structured_data = {"can_details": _can_details, "query_type": "CAN Details"}
        else:
            structured_data = {
                "can_summary": {
                    "assigned_by": "Common Service Center (CSC) / Citizen Portal",
                    "description": "Citizen Access Number (CAN) is a unique citizen identity number assigned through CSC or citizen self-registration.",
                    "number_format": "A CAN issued at a Common Service Centre is 15 digits and starts with the 133 series; one generated on the TN portal is 12 digits. An application referred by the Sub-Registrar carries the citizen's 12-digit CAN, and its IGRS Form 6 number is the same value.",
                    "role_in_patta_transfer": "CAN links the citizen's Aadhaar, mobile number, and identity across all Patta transfer requests (ISD, NISD).",
                    "csc_charges": "The CSC service charge is the fee recorded on the application in the register (it can be revised); ask \"what is the CSC fee\".",
                    "service_codes_linked": "0153 (NISD), 0154 (ISD)"
                },
                "query_type": "CAN Number & CSC Assignment Guide"
            }

    elif intent == "fee_lookup":
        structured_data = await _fee_lookup_data(db, officer, message, language in ("ta", "tanglish"))

    elif intent == "fee_summary":
        # "how much fee have I collected", "total fee collected in June"
        _fee_start, _fee_end, _fee_label = _period_from_message(message)
        _fee_msg = message.lower()
        _fee_type = None
        if re.search(r'\bnisd\b|\b0153\b', _fee_msg):
            _fee_type = "NISD"
        elif re.search(r'\bisd\b|\b0154\b', _fee_msg):
            _fee_type = "ISD"
        structured_data = await get_fee_summary(
            db, officer, application_type=_fee_type,
            start_date=_fee_start, end_date=_fee_end,
        )
        if _fee_label:
            structured_data["fee_summary"]["period_label"] = _fee_label

    elif intent == "service_code_guide":
        structured_data = {
            "service_codes": [
                {
                    "service_code": "0153",
                    "type": "NISD (Not Involving Sub-Division)",
                    "tamil_name": "உட்பிரிவு இல்லாத பட்டா மாறுதல்",
                    "govt_fee": "₹100.00",
                    "csc_fee": "₹60.00",
                    "sla_days": "15-20 working days",
                    "workflow": "Citizen / CSC / Sub-Registrar → SIS Officer (Document Verification) → Zonal Level Tahsildar (Review + Digital Signature / DSC + Approval) [No field visit required]"
                },
                {
                    "service_code": "0154",
                    "type": "ISD (Involving Sub-Division)",
                    "tamil_name": "உட்பிரிவு உள்ள பட்டா மாறுதல்",
                    "govt_fee": "₹400.00",
                    "csc_fee": "₹60.00",
                    "sla_days": "30-35 working days",
                    "workflow": "Citizen / CSC → SIS Officer (Mandatory Field Visit within 15 days) → Senior Draughtsman (SD Sketch) → DIS (Approval) → Tahsildar (Digital Signature / DSC)"
                }
            ],
            "query_type": "Service Codes Workflow & Fee Comparison (0153 / 0154)"
        }

    elif intent == "workload_by_type":
        structured_data = await get_officer_workload(db, officer)
        structured_data["query_type"] = "Workload by Type"

    elif intent == "applications_by_block":
        _grp = "ward" if (re.search(r"\bward\b|வார்டு", message, re.IGNORECASE)
                          and not re.search(r"\bblock\b|பிளாக்", message, re.IGNORECASE)) else "block"
        structured_data = await get_applications_by_block(db, officer, group_by=_grp)
        structured_data["query_type"] = (
            "Applications by Ward" if _grp == "ward" else "Applications by Block")

    elif intent == "officer_directory":
        _dir_ward = extract_ward_number(message) if re.search(
            r"\bward\b|வார்டு", message, re.IGNORECASE) else None
        structured_data = await get_ward_directory(db, officer, ward_number=_dir_ward)
        structured_data["query_type"] = "Ward Directory"

    elif intent == "completion_rate":
        message_lower = message.lower()
        is_this_month = any(p in message_lower for p in ["this month", "month", "monthly", "current month"])
        month_start = date.today().replace(day=1)

        # "What is my completion rate in June 2026" named a period the
        # handler never looked at, so it answered with the all-time rate.
        period_start, period_end, period_label = _period_from_message(message)

        completed, total, scope_label = await _register_completion(db, officer, message)
        structured_data = {
            "completed": completed,
            "total": total,
            "rate": int((completed / total) * 100) if total > 0 else 0,
            "scope": scope_label,
            "query_type": "Completion Rate"
        }

    elif intent == "pending_longest":
        query = select(Application).where(
            and_(
                Application.id.in_(_application_jurisdiction_subquery(await get_jurisdiction_filter(db, officer))),
                Application.current_stage == officer.officer_stage,
                Application.current_status.in_(["pending", "in_progress"])
            )
        ).order_by(Application.submission_date.asc())
        result = await db.execute(query)
        apps = result.scalars().all()
        days = (date.today() - apps[0].submission_date).days if apps else 0
        structured_data = {
            "apps": [a.application_number for a in apps],
            "days": days,
            "query_type": "Pending Longest"
        }

    elif intent in ["is_nisd_or_isd", "check_documents", "check_sale_deed",
                    "submission_channel_check"]:
        app_number = extract_application_number(message)
        if not app_number:
            # Allow implicit continuation since these are specific queries about an application
            app_number = (_extract_app_number_from_context(message, chat_history, allow_implicit_continuation=True) or _gate_app_number)

        if not app_number:
            # No app number - ask for it
            is_tamil_lang = language in ("ta", "tanglish")
            if is_tamil_lang:
                response_text = "தயவுசெய்து விண்ணப்ப எண்ணை குறிப்பிடவும். (எ.கா: 2026/0154/28/NNNNNN)"
            else:
                response_text = "Please specify which application you're asking about. For example: 2026/0154/28/NNNNNN"
            structured_data = {"found": False, "query_type": "Application Details"}
        else:
            structured_data = await get_application_detail(db, app_number, officer=officer)
            structured_data["query_type"] = "Application Details"

    elif intent == "isd_processing":
        app_number = extract_application_number(message) or (_extract_app_number_from_context(message, chat_history) or _gate_app_number)
        if not app_number:
            # Never stand in an arbitrary application for one the officer
            # did not name — that answers a question nobody asked.
            structured_data = {"found": False, "query_type": "ISD Processing"}
            response_text = ASK_FOR_APP_NUMBER[language if language in ASK_FOR_APP_NUMBER else "en"]
        else:
            structured_data = await get_application_detail(db, app_number, officer=officer)
            structured_data["query_type"] = "ISD Processing"
        _isd_app_no = app_number


    elif intent in ["sd_additional_info", "sd_encroachment_check", "sd_sketch_readiness", "sd_forward_check", "sd_remarks", "fv_date_select", "fv_nearby_pending", "fv_scheduled_this_week", "fv_reschedule_availability", "fv_change_date", "fv_deadline_check", "fv_overdue_inspections", "fv_unassigned_awaiting", "fv_recently_rescheduled", "fv_scheduling_conflicts"]:
        app_number = extract_application_number(message)

        # ── fv_scheduled_this_week without a specific application ──────────
        # When the officer asks "how many scheduled in this taluk this week?"
        # without citing an app number, answer from the officer's own taluk directly.
        _handled_week_query = False
        if intent == "fv_scheduled_this_week" and not app_number:
            _handled_week_query = True

            # Resolve officer's taluk
            jur_result = await db.execute(
                select(OfficerJurisdiction).where(OfficerJurisdiction.officer_id == officer.officer_id).limit(1)
            )
            jur = jur_result.scalar_one_or_none()

            taluk_obj = None
            if jur:
                if jur.taluk_id:
                    taluk_obj = (await db.execute(select(Taluk).where(Taluk.id == jur.taluk_id))).scalar_one_or_none()
                elif jur.block_id:
                    # Walk up: block → ward → town → taluk
                    block_obj = (await db.execute(select(Block).where(Block.id == jur.block_id))).scalar_one_or_none()
                    if block_obj:
                        ward_obj = (await db.execute(select(Ward).where(Ward.id == block_obj.ward_id))).scalar_one_or_none()
                        if ward_obj:
                            town_obj = (await db.execute(select(Town).where(Town.id == ward_obj.town_id))).scalar_one_or_none()
                            if town_obj:
                                taluk_obj = (await db.execute(select(Taluk).where(Taluk.id == town_obj.taluk_id))).scalar_one_or_none()
                elif jur.ward_id:
                    ward_obj = (await db.execute(select(Ward).where(Ward.id == jur.ward_id))).scalar_one_or_none()
                    if ward_obj:
                        town_obj = (await db.execute(select(Town).where(Town.id == ward_obj.town_id))).scalar_one_or_none()
                        if town_obj:
                            taluk_obj = (await db.execute(select(Taluk).where(Taluk.id == town_obj.taluk_id))).scalar_one_or_none()

            taluk_name = taluk_obj.name if taluk_obj else "your taluk"
            taluk_id = taluk_obj.id if taluk_obj else None

            today = datetime.now(timezone.utc).date()
            start_of_week = today - timedelta(days=today.weekday())
            end_of_week = start_of_week + timedelta(days=6)

            week_count = 0
            week_app_numbers = []
            if taluk_id:
                stmt_week = select(Application).join(
                    FieldVisit, FieldVisit.application_id == Application.id
                ).join(
                    SurveyNumber, Application.survey_number_id == SurveyNumber.id
                ).join(
                    Block, SurveyNumber.block_id == Block.id
                ).join(
                    Ward, Block.ward_id == Ward.id
                ).join(
                    Town, Ward.town_id == Town.id
                ).where(
                    and_(
                        Town.taluk_id == taluk_id,
                        FieldVisit.id.in_(_field_visit_jurisdiction_subquery(await get_jurisdiction_filter(db, officer))),
                        FieldVisit.status == "scheduled",
                        FieldVisit.scheduled_date >= start_of_week,
                        FieldVisit.scheduled_date <= end_of_week
                    )
                )
                week_apps = (await db.execute(stmt_week)).scalars().all()
                week_count = len(week_apps)
                week_app_numbers = [a.application_number for a in week_apps]

            structured_data = {
                "taluk_scheduled_count": week_count,
                "taluk_name": taluk_name,
                "taluk_cases": week_app_numbers,
                "week_start": start_of_week.isoformat(),
                "week_end": end_of_week.isoformat(),
                "query_type": "Scheduled Field Visits This Week"
            }
            # response is built in the fv_scheduled_this_week handler below

        if intent == "fv_unassigned_awaiting" and not app_number:
            _handled_week_query = True
            from sqlalchemy.orm import joinedload
            from datetime import date as _date_ns

            unassigned_stmt_ns = select(Application).options(
                joinedload(Application.applicant),
                joinedload(Application.application_sub_divisions).joinedload(ApplicationSubDivision.sub_division),
                joinedload(Application.survey_number).joinedload(SurveyNumber.block).joinedload(Block.ward).joinedload(Ward.town)
            ).join(
                FieldVisit, FieldVisit.application_id == Application.id
            ).where(
                and_(
                    FieldVisit.id.in_(_field_visit_jurisdiction_subquery(await get_jurisdiction_filter(db, officer))),
                    FieldVisit.status == "unscheduled"
                )
            )
            unassigned_res_ns = (await db.execute(unassigned_stmt_ns)).unique().scalars().all()

            unassigned_list_ns = []
            for ua in unassigned_res_ns:
                days_p = (_date_ns.today() - ua.submission_date).days if ua.submission_date else 0
                sn = ua.survey_number
                bl = sn.block if sn else None
                wd = bl.ward if bl else None
                tw = wd.town if wd else None
                sis_nos, dis_nos = _sub_division_numbers(ua)
                unassigned_list_ns.append({
                    "application_number": ua.application_number,
                    "applicant_name": ua.applicant.name if ua.applicant else "N/A",
                    "survey_no": sn.survey_no if sn else "N/A",
                    "sis_temp_sub_div": sis_nos,
                    "dis_fixed_sub_div": dis_nos,
                    "town_name": tw.name if tw else "N/A",
                    "ward_number": wd.ward_number if wd else "N/A",
                    "block_number": bl.block_number if bl else "N/A",
                    "current_stage": ua.current_stage or "N/A",
                    "current_status": ua.current_status or "N/A",
                    "submission_date": ua.submission_date.isoformat() if ua.submission_date else "N/A",
                    "days_pending": days_p,
                    "priority": "High" if ua.priority_flag else "Normal"
                })

            structured_data = {
                "unassigned_visits_count": len(unassigned_list_ns),
                "unassigned_applications": unassigned_list_ns,
                "query_type": "திட்டமிடல் காத்திருக்கும் கள ஆய்வுகள்" if language in ("ta", "tanglish") else "Unassigned Field Visits — Awaiting Scheduling"
            }
            # response built by fv_unassigned_awaiting handler below

        if intent == "fv_deadline_check":
            _handled_week_query = True
            # Resolve application number from message or chat history
            resolved_app = app_number or (_extract_app_number_from_context(message, chat_history) or _gate_app_number)
            if not resolved_app:
                structured_data = {
                    "found": False,
                    "message": "Please specify an application number, e.g. 2026/0154/28/NNNNNN, to check the deadline."
                }
            else:
                from sqlalchemy.orm import joinedload
                app_res = await db.execute(
                    select(Application)
                    .where(Application.application_number == resolved_app)
                )
                a_dl = app_res.scalar_one_or_none()
                if not a_dl:
                    structured_data = {"found": False, "message": f"Application {resolved_app} not found.", "searched_number": resolved_app}
                else:
                    sub_date = a_dl.submission_date
                    today_dl = datetime.now(timezone.utc).date()
                    working_days_dl = 0
                    curr = sub_date
                    while curr < today_dl:
                        curr += timedelta(days=1)
                        if curr.weekday() < 5:
                            working_days_dl += 1
                    structured_data = {
                        "found": True,
                        "application_number": a_dl.application_number,
                        "submission_date": sub_date.isoformat(),
                        "working_days": working_days_dl,
                        "deadline_days": 15,
                        "is_overdue": working_days_dl > 15,
                        "days_overdue": max(0, working_days_dl - 15),
                        "days_remaining": max(0, 15 - working_days_dl),
                        "query_type": "Field Visit Deadline Check"
                    }

        if not _handled_week_query:
            if not app_number:
                app_number = _extract_app_number_from_context(
                    message, chat_history, allow_implicit_continuation=True
                )

            from sqlalchemy.orm import joinedload

            app_res = await db.execute(
                select(Application)
                .options(joinedload(Application.survey_number).joinedload(SurveyNumber.block).joinedload(Block.ward).joinedload(Ward.town).joinedload(Town.taluk))
                .where(Application.application_number == app_number)
            )
            a = app_res.scalar_one_or_none()

            if not a:
                structured_data = {"found": False, "message": f"Application {app_number} not found.", "searched_number": app_number}
            else:
                doc_stmt = select(ApplicationDocument).where(ApplicationDocument.application_id == a.id)
                docs = (await db.execute(doc_stmt)).scalars().all()
                missing_docs = [d.document_type for d in docs if not d.is_uploaded]

                visit_stmt = select(FieldVisit).where(FieldVisit.application_id == a.id)
                visit = (await db.execute(visit_stmt)).scalars().first()

                hist_stmt = select(WorkflowHistory).where(WorkflowHistory.application_id == a.id).order_by(WorkflowHistory.performed_at.asc())
                history = (await db.execute(hist_stmt)).scalars().all()

                sd_clarification = None
                sd_remarks = None
                forwarded_to_sd_date = None

                for h in history:
                    if h.from_stage == "SD":
                        sd_clarification = h.rejection_reason or h.remarks
                        sd_remarks = h.remarks or h.rejection_reason
                    if h.to_stage == "SD":
                        forwarded_to_sd_date = h.performed_at.date().isoformat()

                nearby_count = 0
                ward_num = "N/A"
                block_num = "N/A"
                if a.survey_number and a.survey_number.block:
                    bl = a.survey_number.block
                    ward_num = bl.ward.ward_number if bl.ward else "N/A"
                    block_num = bl.block_number

                    nearby_stmt = select(func.count(Application.id)).join(
                        SurveyNumber, Application.survey_number_id == SurveyNumber.id
                    ).where(
                        and_(
                            SurveyNumber.block_id == bl.id,
                            Application.id != a.id,
                            Application.current_status.in_(["pending", "in_progress"])
                        )
                    )
                    nearby_count = (await db.execute(nearby_stmt)).scalar() or 0

                taluk_name = "N/A"
                taluk_scheduled_count = 0
                taluk_cases = []
                if a.survey_number and a.survey_number.block and a.survey_number.block.ward and a.survey_number.block.ward.town:
                    town = a.survey_number.block.ward.town
                    taluk = town.taluk
                    if taluk:
                        taluk_name = taluk.name
                        today = datetime.now(timezone.utc).date()
                        start_of_week = today - timedelta(days=today.weekday())
                        end_of_week = start_of_week + timedelta(days=6)

                        stmt_week = select(Application).join(
                            FieldVisit, FieldVisit.application_id == Application.id
                        ).join(
                            SurveyNumber, Application.survey_number_id == SurveyNumber.id
                        ).join(
                            Block, SurveyNumber.block_id == Block.id
                        ).join(
                            Ward, Block.ward_id == Ward.id
                        ).join(
                            Town, Ward.town_id == Town.id
                        ).where(
                            and_(
                                Town.taluk_id == taluk.id,
                                FieldVisit.id.in_(_field_visit_jurisdiction_subquery(await get_jurisdiction_filter(db, officer))),
                                FieldVisit.status == "scheduled",
                                FieldVisit.scheduled_date >= start_of_week,
                                FieldVisit.scheduled_date <= end_of_week
                            )
                        )
                        week_apps = (await db.execute(stmt_week)).scalars().all()
                        taluk_scheduled_count = len(week_apps)
                        taluk_cases = [wa.application_number for wa in week_apps]

                reschedule_date = None
                for offset in range(1, 10):
                    test_date = datetime.now(timezone.utc).date() + timedelta(days=offset)
                    if test_date.weekday() >= 5:
                        continue
                    visit_count = (await db.execute(
                        select(func.count(FieldVisit.id)).where(
                            and_(
                                FieldVisit.id.in_(_field_visit_jurisdiction_subquery(await get_jurisdiction_filter(db, officer))),
                                FieldVisit.scheduled_date == test_date
                            )
                        )
                    )).scalar() or 0
                    if visit_count == 0:
                        reschedule_date = test_date.isoformat()
                        break
                if not reschedule_date:
                    reschedule_date = (datetime.now(timezone.utc).date() + timedelta(days=1)).isoformat()

                sub_date = a.submission_date
                today = datetime.now(timezone.utc).date()
                working_days = 0
                curr = sub_date
                while curr < today:
                    curr += timedelta(days=1)
                    if curr.weekday() < 5:
                        working_days += 1

                overdue_visits_stmt = select(func.count(FieldVisit.id)).where(
                    and_(
                        FieldVisit.id.in_(_field_visit_jurisdiction_subquery(await get_jurisdiction_filter(db, officer))),
                        FieldVisit.status == "overdue"
                    )
                )
                overdue_visits_count = (await db.execute(overdue_visits_stmt)).scalar() or 0

                unassigned_visits_stmt = select(func.count(FieldVisit.id)).where(
                    and_(
                        FieldVisit.id.in_(_field_visit_jurisdiction_subquery(await get_jurisdiction_filter(db, officer))),
                        FieldVisit.status.in_(["unscheduled"])
                    )
                )
                unassigned_visits_count = (await db.execute(unassigned_visits_stmt)).scalar() or 0

                # Fetch actual unassigned applications with details for table display
                from sqlalchemy.orm import joinedload
                unassigned_apps_list = []
                unassigned_apps_stmt = select(Application).options(
                    joinedload(Application.applicant),
                    joinedload(Application.application_sub_divisions).joinedload(ApplicationSubDivision.sub_division),
                    joinedload(Application.survey_number).joinedload(SurveyNumber.block).joinedload(Block.ward).joinedload(Ward.town)
                ).join(
                    FieldVisit, FieldVisit.application_id == Application.id
                ).where(
                    and_(
                        FieldVisit.id.in_(_field_visit_jurisdiction_subquery(await get_jurisdiction_filter(db, officer))),
                        FieldVisit.status.in_(["unscheduled"])
                    )
                )
                unassigned_apps_res = (await db.execute(unassigned_apps_stmt)).unique().scalars().all()
                for ua in unassigned_apps_res:
                    days_p = (date.today() - ua.submission_date).days if ua.submission_date else 0
                    block_n = ua.survey_number.block.block_number if (ua.survey_number and ua.survey_number.block) else "N/A"
                    ward_n = ua.survey_number.block.ward.ward_number if (ua.survey_number and ua.survey_number.block and ua.survey_number.block.ward) else "N/A"
                    town_n = ua.survey_number.block.ward.town.name if (ua.survey_number and ua.survey_number.block and ua.survey_number.block.ward and ua.survey_number.block.ward.town) else "N/A"
                    survey_n = ua.survey_number.survey_no if ua.survey_number else "N/A"
                    # temporary "3/T1" number, and the final number that
                    # replaces it once the sub-division is confirmed
                    sis_temp_nos, dis_fixed_nos = _sub_division_numbers(ua)
                    unassigned_apps_list.append({
                        "application_number": ua.application_number,
                        "applicant_name": ua.applicant.name if ua.applicant else "N/A",
                        "survey_no": survey_n,
                        "sis_temp_sub_div": sis_temp_nos,
                        "dis_fixed_sub_div": dis_fixed_nos,
                        "town_name": town_n,
                        "ward_number": ward_n,
                        "block_number": block_n,
                        "current_stage": ua.current_stage or "N/A",
                        "current_status": ua.current_status or "N/A",
                        "submission_date": ua.submission_date.isoformat() if ua.submission_date else "N/A",
                        "days_pending": days_p,
                        "priority": "High" if ua.priority_flag else "Normal"
                    })

                recently_rescheduled_count = (await db.execute(
                    select(func.count(FieldVisit.id)).where(
                        and_(
                            FieldVisit.id.in_(_field_visit_jurisdiction_subquery(await get_jurisdiction_filter(db, officer))),
                            FieldVisit.updated_at >= datetime.now(timezone.utc) - timedelta(days=7)
                        )
                    )
                )).scalar() or 0

                overlap_date = None
                overlap_stmt = select(FieldVisit.scheduled_date).where(
                    and_(
                        FieldVisit.id.in_(_field_visit_jurisdiction_subquery(await get_jurisdiction_filter(db, officer))),
                        FieldVisit.status == "scheduled"
                    )
                ).group_by(FieldVisit.scheduled_date).having(func.count(FieldVisit.id) > 1)
                overlap_res = (await db.execute(overlap_stmt)).scalars().first()
                if overlap_res:
                    overlap_date = overlap_res.isoformat()

                structured_data = {
                    "found": True,
                    "application_number": a.application_number,
                    "current_stage": a.current_stage,
                    "submission_date": a.submission_date.isoformat(),
                    "missing_documents": missing_docs,
                    "field_visit_present": visit is not None,
                    "field_visit_date": visit.scheduled_date.isoformat() if (visit and visit.scheduled_date) else None,
                    "encroachment_found": visit.encroachment_found if visit else False,
                    "area_verified": visit.area_verified if visit else None,
                    "visit_notes_present": bool(visit.visit_notes) if visit else False,
                    "sd_clarification": sd_clarification,
                    "sd_remarks": sd_remarks,
                    "forwarded_to_sd_date": forwarded_to_sd_date,
                    "nearby_count": nearby_count,
                    "ward_number": ward_num,
                    "block_number": block_num,
                    "taluk_name": taluk_name,
                    "taluk_scheduled_count": taluk_scheduled_count,
                    "taluk_cases": taluk_cases,
                    "reschedule_date": reschedule_date,
                    "working_days": working_days,
                    "overdue_visits_count": overdue_visits_count,
                    "unassigned_visits_count": unassigned_visits_count,
                    "unassigned_applications": unassigned_apps_list,
                    "recently_rescheduled_count": recently_rescheduled_count,
                    "overlap_date": overlap_date,
                    "query_type": "Workflow Check"
                }

        # PARITY: process_chat_stream serves these three intents from dedicated
        # fetch branches that set a specific query_type (and, for fv_change_date,
        # a message). process_chat serves them from the shared group above, which
        # would otherwise label them all "Workflow Check".
        if isinstance(structured_data, dict) and intent in (
            "fv_recently_rescheduled", "fv_scheduling_conflicts", "fv_change_date"
        ):
            structured_data["query_type"] = {
                "fv_recently_rescheduled": "Recently Rescheduled Field Visits",
                "fv_scheduling_conflicts": "Field Visit Scheduling Conflicts",
                "fv_change_date": "Field Visit Date Change",
            }[intent]
            if intent == "fv_change_date":
                structured_data["message"] = (
                    "To change the date of a field visit, you should ask the Tahsildar."
                )


    elif intent == "all_surveys_in_jurisdiction":
        structured_data = await get_all_surveys_in_jurisdiction(db, officer)
        structured_data["query_type"] = "All Surveys in Your Jurisdiction"

    elif intent == "fv_visit_plan":
        _vp_ward, _vp_block = await _geo_scope(
            db, session_id, message, chat_history, officer)
        structured_data = await _fetch_visit_plan(
            db, message, officer, ward_number=_vp_ward, block_number=_vp_block)

    elif intent == "last_application":
        _la_ward, _la_block = await _geo_scope(
            db, session_id, message, chat_history, officer)
        structured_data = await _fetch_last_application(
            db, message, officer, ward_number=_la_ward, block_number=_la_block)

    elif intent == "application_status" and not _hook_done:
        # Extract from current message first; only check history if user uses reference words
        # Use findall to support multi-app queries like "Show details for A and B"
        _app_numbers_in_msg = extract_application_numbers(message)
        if len(_app_numbers_in_msg) > 1 or _wants_applicant_card(message, _app_numbers_in_msg):
            # Multiple application numbers detected — fetch details for each
            _multi_details = []
            for _an in _app_numbers_in_msg:
                _det = await get_application_detail(db, _an, officer=officer)
                _multi_details.append(_det)
            _appl_focus = _asks_applicant_focus(message)
            structured_data = {
                "multi_applications": _multi_details,
                # "applicant details" over several files asks about the
                # people; without this the answer listed status and stage,
                # which is the application card, not the applicant one.
                "detail_focus": "applicant" if _appl_focus else None,
                # Use the correct query_type so the table header reads
                # "Applicant Details" rather than "Application Status".
                "query_type": "Applicant Details" if _appl_focus else "Application & Applicant Details"
            }
            app_number = None
        else:
            app_number = _app_numbers_in_msg[0] if _app_numbers_in_msg else None
        if not _app_numbers_in_msg and wants_details_of_listed(message):
            # "now show details of both the applications" -- the numbers are
            # in the list the previous turn returned, not in this message.
            _multi_sd, _one = await _expand_listed_details(
                db, session_id, chat_history, message, officer)
            if _multi_sd:
                structured_data = _multi_sd
                app_number = None
            elif _one:
                app_number = _one
        if not app_number and "multi_applications" not in structured_data:
            # Check for explicit reference patterns OR implicit continuation for field queries
            # Implicit continuation: if user just discussed an app, next field query refers to it
            _field_keywords = [
                "name", "address", "mobile", "phone", "status", "stage",
                "overdue", "days", "scheduled", "visit", "delay", "delayed", "late",
                # "can number" (not bare "can") -- "can" alone is also the
                # commonest modal verb in English ("Can you explain?",
                # "Can I see...") and a substring match on bare "can" made
                # every such question a false-positive field query that
                # got answered with the CAN number instead of what was
                # actually asked. Mirrors the same "can you/i/we" guard
                # already established in _is_conversation_recall() above.
                "survey", "patta", "can number", "can_number", "reason", "priority",
                "subdivision", "subdiv", "user", "role",
                "service", "source", "district", "taluk", "ward", "block",
                "received", "workflow",
                "serial", "applicant", "type", "date", "year",
                # Bare "day"/"month" are too broad (substrings of "today",
                # "yesterday", "birthday") to add as single tokens here --
                # they would broaden this gate for unrelated messages and
                # flip _bypass_html unexpectedly. Multi-word phrases only.
                "which day", "what day", "day of the week", "day of week",
                "weekday", "weekend", "which month", "what month",
                "monday", "tuesday", "wednesday", "thursday", "friday",
                "saturday", "sunday",
                "பெயர்", "முகவரி", "தொலைபேசி", "நிலை", "கட்டம்", "தாமதம்",
                "கணக்கெண்", "பட்டா", "காரணம்", "முன்னுரிமை"
            ]
            is_field_query = (any(kw in message.lower() for kw in _field_keywords)
                              or _subdivision_qa.claims(_subdivision_qa.attributes(message))
                              or bool(_oldowner_qa.attributes(message)) or _cm_strong(message))
            app_number = (_extract_app_number_from_context(message, chat_history, allow_implicit_continuation=is_field_query) or _gate_app_number)

            # Only fall back to most recent application if explicit reference pattern found
            if not app_number:
                # No substitution here. "the most recently updated application
                # assigned to this officer" is not what the officer referred to,
                # and answering about it looks authoritative while being wrong.
                # With no resolvable reference we ask which application they mean.
                pass
        if app_number:
            if _wants_workflow_history(message):
                from sqlalchemy.orm import joinedload
                app_res = await db.execute(select(Application).where(Application.application_number == app_number))
                a = app_res.scalar_one_or_none()
                if not a:
                    structured_data = {"found": False, "message": f"Application {app_number} not found.", "searched_number": app_number}
                else:
                    history_res = await db.execute(
                        select(WorkflowHistory)
                        .options(joinedload(WorkflowHistory.performed_by_officer))
                        .where(WorkflowHistory.application_id == a.id)
                        .order_by(WorkflowHistory.performed_at.asc())
                    )
                    history = history_res.scalars().all()

                    history_list = [
                        {
                            "from_stage": h.from_stage,
                            "to_stage": h.to_stage,
                            "changed_at": h.performed_at.isoformat(),
                            "note": h.remarks,
                            "changed_by_name": h.performed_by_officer.name if h.performed_by_officer else "System"
                        }
                        for h in history
                    ]
                    structured_data = {
                        "application_number": a.application_number,
                        "history": history_list,
                        "query_type": f"Workflow History for {a.application_number}"
                    }
            else:
                structured_data = await get_application_detail(db, app_number, officer=officer)
                structured_data["query_type"] = "Application Status"

    elif intent == "jurisdiction_summary":
        structured_data = await _build_jurisdiction_summary(
            db, officer, focus=detect_jurisdiction_focus(message))

    elif intent == "town_applications":
        town_name = extract_town_name(message)
        from sqlalchemy.orm import joinedload
        query = select(Application).join(
            SurveyNumber, Application.survey_number_id == SurveyNumber.id
        ).join(
            Block, SurveyNumber.block_id == Block.id
        ).join(
            Ward, Block.ward_id == Ward.id
        ).join(
            Town, Ward.town_id == Town.id
        ).options(
            joinedload(Application.survey_number).joinedload(SurveyNumber.block).joinedload(Block.ward).joinedload(Ward.town)
        ).where(
            and_(
                Application.id.in_(_application_jurisdiction_subquery(await get_jurisdiction_filter(db, officer))),
                Application.current_stage == officer.officer_stage,
                Application.current_status.in_(["pending", "in_progress"]),
                Town.name.ilike(f"%{town_name}%") if town_name else True
            )
        ).order_by(Application.application_number)
        res = await db.execute(query)
        apps = res.scalars().all()

        app_rows = []
        for a in apps:
            sn = a.survey_number
            bl = sn.block if sn else None
            w = bl.ward if bl else None
            t = w.town if w else None
            app_rows.append({
                "application_number": a.application_number,
                "type": a.application_type,
                "town_name": t.name if t else "N/A",
                "ward_number": w.ward_number if w else "N/A",
                "status": "Pending",
                "stage": a.current_stage,
                "submission_date": a.submission_date.isoformat()
            })

        structured_data = {
            "applications": app_rows,
            "query_type": f"Pending Applications in {town_name}" if town_name else "Pending Applications"
        }

    elif intent == "block_applications":
        block_no = extract_block_number(message)
        from sqlalchemy.orm import joinedload
        query = select(Application).join(
            SurveyNumber, Application.survey_number_id == SurveyNumber.id
        ).join(
            Block, SurveyNumber.block_id == Block.id
        ).join(
            Ward, Block.ward_id == Ward.id
        ).join(
            Town, Ward.town_id == Town.id
        ).options(
            joinedload(Application.survey_number).joinedload(SurveyNumber.block).joinedload(Block.ward).joinedload(Ward.town)
        ).where(
            and_(
                Application.id.in_(_application_jurisdiction_subquery(await get_jurisdiction_filter(db, officer))),
                Application.current_stage == officer.officer_stage,
                Application.current_status.in_(["pending", "in_progress"]),
                Block.block_number.ilike(f"%{block_no}%") if block_no else True
            )
        ).order_by(Application.application_number)
        res = await db.execute(query)
        apps = res.scalars().all()

        app_rows = []
        for a in apps:
            sn = a.survey_number
            bl = sn.block if sn else None
            w = bl.ward if bl else None
            t = w.town if w else None
            app_rows.append({
                "application_number": a.application_number,
                "type": a.application_type,
                "town_name": t.name if t else "N/A",
                "ward_number": w.ward_number if w else "N/A",
                "status": "Pending",
                "stage": a.current_stage,
                "submission_date": a.submission_date.isoformat()
            })

        structured_data = {
            "applications": app_rows,
            "query_type": f"Pending Applications in Block {block_no}" if block_no else "Pending Applications"
        }

    elif intent == "rejection_info":
        app_number = extract_application_number(message) or _extract_app_number_from_context(
            message, chat_history, allow_implicit_continuation=True
        )
        app_res = await db.execute(select(Application).where(Application.application_number == app_number))
        a = app_res.scalar_one_or_none()
        if not a:
            structured_data = {"found": False, "message": f"Application {app_number} not found.", "searched_number": app_number}
        else:
            history_res = await db.execute(
                select(WorkflowHistory)
                .where(WorkflowHistory.application_id == a.id)
                .order_by(WorkflowHistory.performed_at.asc())
            )
            history = history_res.scalars().all()

            rejections = []
            for i, h in enumerate(history):
                if h.to_stage == "REJECTED" or "REJECT" in (h.action or ""):
                    resub_date = None
                    for next_h in history[i+1:]:
                        if next_h.from_stage == "REJECTED" or "RESUBMIT" in (next_h.action or "") or next_h.to_stage != "REJECTED":
                            resub_date = next_h.performed_at.isoformat()
                            break
                    rejections.append({
                        "source": h.from_stage or "SD",
                        "reason_code": "REJ-01",
                        "reason_text": h.rejection_reason or h.remarks or "Boundary mismatch",
                        "rejected_at": h.performed_at.isoformat(),
                        "resubmitted_at": resub_date
                    })

            structured_data = {
                "application_number": a.application_number,
                "rejections": rejections,
                "query_type": "Rejection History"
            }

    elif intent == "taluk_summary":
        q = select(OfficerJurisdiction).where(OfficerJurisdiction.officer_id == officer.officer_id)
        res = await db.execute(q)
        jurisdictions = res.scalars().all()
        if jurisdictions:
            first = jurisdictions[0]
            tk = first.taluk
            d = first.district
            structured_data = {
                "taluk_name": tk.name if tk else "N/A",
                "district_name": d.name if d else "N/A",
                "query_type": "Taluk Summary"
            }
        else:
            structured_data = {"found": False, "message": "No taluk assigned."}

    elif intent == "litigation_check" and not response_text:
        # `not response_text`: "is X a court order transfer?" reads as
        # litigation to parse_intent, but the transfer-reason pre-empt has
        # already answered it -- do not overwrite it with a survey scan.
        # "litigation on 2022/0153/28/000016" names an application, not a
        # survey. extract_survey_number would pull "0153/28" out of the
        # middle of the application number and report it as not found, so
        # resolve the survey through the application first.
        survey_no = None
        _lit_app = extract_application_number(message)
        if _lit_app:
            _lit_survey = (await db.execute(
                select(SurveyNumber)
                .join(Application, Application.survey_number_id == SurveyNumber.id)
                .where(Application.application_number == _lit_app)
            )).scalars().first()
            if _lit_survey is not None:
                survey_no = _lit_survey.survey_no
        if not survey_no:
            survey_no = await _survey_ref(db, session_id, message, chat_history)
        if not survey_no:
            # No number in this message, no application to resolve it
            # through -- e.g. "is there litigation on it?" straight after
            # something unrelated. This used to silently fall back to a
            # hardcoded "145" and confidently report that survey as "not
            # found", which reads as a real answer instead of what it
            # actually is: the officer's reference couldn't be resolved.
            is_tamil = language in ("ta", "tanglish")
            structured_data = {
                "found": False,
                "message": ("தயவுசெய்து சர்வே எண்ணைக் குறிப்பிடவும். (எ.கா: 1345)" if is_tamil
                            else "Please specify the survey number you are asking about (e.g. 1345).")
            }
        else:
            # survey_no is not unique -- the same number exists in more than one
            # block, so this must never be scalar_one_or_none(): a number like
            # "15" raised MultipleResultsFound and killed the whole request.
            # A subdivision-qualified number ("1344/2") never matches survey_no
            # exactly -- the stored value is the base number -- so fall back to
            # it the same way get_survey_detail() does, or "litigation on
            # subdivision 1344/2" always reported "not found".
            _lit_base = survey_no.split('/')[0] if '/' in survey_no else survey_no
            _lit_rows = (await db.execute(
                select(SurveyNumber).where(
                    or_(SurveyNumber.survey_no == survey_no, SurveyNumber.survey_no == _lit_base)
                )
            )).scalars().all()
            sn = next((r for r in _lit_rows if r.has_litigation), None) or (
                _lit_rows[0] if _lit_rows else None)
            if sn:
                structured_data = {
                    "survey_no": sn.survey_no,
                    "litigation_flag": sn.has_litigation,
                    "parcels_with_this_number": len(_lit_rows),
                    "query_type": "Litigation Check"
                }
            else:
                structured_data = {"found": False, "message": f"Survey number {survey_no} not found."}

    elif intent in ["check_sale_deed", "sale_deed_check"]:
        app_number = extract_application_number(message)
        if not app_number:
            app_number = (_extract_app_number_from_context(message, chat_history) or _gate_app_number)

        if not app_number:
            # No app number provided - ask user for it
            is_tamil_lang = language in ("ta", "tanglish")
            if is_tamil_lang:
                response_text = "தயவுசெய்து விண்ணப்ப எண்ணை குறிப்பிடவும். (எ.கா: 2026/0154/28/NNNNNN)"
            else:
                response_text = "Please specify which application you're asking about. For example: 2026/0154/28/NNNNNN"
            structured_data = {"found": False, "query_type": "Sale Deed Verification"}
        else:
            structured_data = await get_application_detail(db, app_number, officer=officer)
            structured_data["query_type"] = "Sale Deed Verification"
            structured_data["sale_deed_verified"] = structured_data.get("sale_deed_registered", False)

    elif intent == "joint_owner_check":
        # Check if asking about an application's survey ownership or direct survey ownership
        app_number = extract_application_number(message)
        if not app_number:
            # Allow implicit continuation for joint owner queries
            app_number = (_extract_app_number_from_context(message, chat_history, allow_implicit_continuation=True) or _gate_app_number)

        if app_number:
            # Get application details to find the survey number
            app_data = await get_application_detail(db, app_number, officer=officer)
            survey_no = app_data.get("survey_no") if app_data.get("found") else None
            if not survey_no:
                structured_data = {"found": False, "message": f"Application {app_number} not found or has no survey linked"}
            else:
                owners_data = await get_survey_owners(db, survey_no, officer=officer)
                structured_data = {
                    "found": True,
                    "application_number": app_number,
                    "survey_no": survey_no,
                    "query_type": "Joint Ownership Check",
                    **_summarize_joint_owners(owners_data),
                }
        else:
            # Direct survey number query
            survey_no = await _survey_ref(db, session_id, message, chat_history)
            if not survey_no:
                structured_data = {"found": False, "message": "Please provide an application number or survey number"}
            else:
                owners_data = await get_survey_owners(db, survey_no, officer=officer)
                if not owners_data.get("found", True):
                    structured_data = {"found": False, "message": owners_data.get("message", f"Survey number {survey_no} not found or not accessible")}
                else:
                    structured_data = {
                        "found": True,
                        "survey_no": survey_no,
                        "query_type": "Joint Ownership Details",
                        **_summarize_joint_owners(owners_data),
                    }

    elif intent == "escalation_check":
        # Find applications approaching OR past the 15-working-day escalation threshold
        # "Approaching" = 12-15 working days elapsed; "Overdue" = 16+ working days
        from datetime import date as _date_esc
        _today_esc = _date_esc.today()

        # Get all pending/in-progress apps for this officer
        # joinedload is imported locally further down this function, which
        # shadows the module-level name and makes it unbound up here.
        from sqlalchemy.orm import joinedload as _joinedload
        esc_query = select(Application).options(
            # survey_number/block are read when building each row; without
            # eager loading that is a lazy load inside async context and
            # the whole answer fails.
            _joinedload(Application.survey_number).joinedload(SurveyNumber.block)
        ).where(
            and_(
                Application.id.in_(_application_jurisdiction_subquery(await get_jurisdiction_filter(db, officer))),
                Application.current_status.in_(["pending", "in_progress", "escalated"])
            )
        ).order_by(Application.submission_date.asc())
        # "escalated ISD applications" named a type; without this the answer
        # mixed ISD and NISD rows together.
        _esc_msg = message.lower()
        if re.search(r'\bnisd\b|\b0153\b', _esc_msg):
            esc_query = esc_query.where(Application.application_type == "NISD")
        elif re.search(r'\bisd\b|\b0154\b', _esc_msg):
            esc_query = esc_query.where(Application.application_type == "ISD")
        esc_result = await db.execute(esc_query)
        all_pending_apps = esc_result.scalars().all()

        approaching_apps = []
        for a in all_pending_apps:
            if not a.submission_date:
                continue
            # Count working days elapsed
            wd_count = 0
            curr = a.submission_date
            while curr < _today_esc:
                curr += timedelta(days=1)
                if curr.weekday() < 5:
                    wd_count += 1

            # Include if in warning zone (day 10+) or overdue
            if wd_count >= 10:
                days_remaining = max(0, 15 - wd_count)
                is_overdue = wd_count > 15
                _esc_sn = a.survey_number
                _esc_bl = _esc_sn.block if _esc_sn else None
                approaching_apps.append({
                    "application_number": a.application_number,
                    "type": a.application_type,
                    # same omission as immediate_action: the survey and block
                    # are loaded but were never put on the row
                    "survey_no": _esc_sn.survey_no if _esc_sn else "N/A",
                    "block_number": _esc_bl.block_number if _esc_bl else "N/A",
                    "status": a.current_status,
                    "stage": a.current_stage,
                    "submission_date": a.submission_date.isoformat(),
                    "working_days_elapsed": wd_count,
                    "days_remaining": days_remaining,
                    "is_overdue": is_overdue,
                    "urgency": "⚠ OVERDUE" if is_overdue else (
                        "🔴 Critical (1–2 days)" if days_remaining <= 2 else
                        "🟡 Warning (3–5 days)" if days_remaining <= 5 else
                        "🟢 Watch"
                    )
                })

        structured_data = {
            "applications": approaching_apps,
            "total_approaching": len(approaching_apps),
            "overdue_count": sum(1 for a in approaching_apps if a["is_overdue"]),
            "query_type": "Escalation Threshold — Applications Approaching Deadline"
        }

    elif intent == "survey_detail":
        # A pointed question about an unprojected parcel / signature column
        # ("soil type of survey 5", "document hash of survey 5") was already
        # answered as text by the untracked-field blocks above; don't let the
        # generic parcel card overwrite it.
        if not response_text:
            survey_no = await _survey_ref(db, session_id, message, chat_history)
            if survey_no:
                structured_data = await get_survey_detail(db, survey_no, officer=officer)
                structured_data["query_type"] = "Survey Number Details"

    elif intent == "survey_owners":
        survey_no = await _survey_ref(db, session_id, message, chat_history)
        _tp_side = _asks_transfer_party(message)
        _tp_app = _gate_app_number or extract_application_number(message)
        if _tp_side and _tp_app and not response_text:
            # "who is the new / previous owner of <app>" -- the transfer
            # parties (transferor / transferee), not the whole parcel's
            # co-owner register.
            _owner_app = await get_application_detail(db, _tp_app, officer=officer)
            if _owner_app.get("found"):
                structured_data = {
                    "found": True,
                    "query_type": "Patta Transfer Parties",
                    "application_number": _owner_app.get("application_id") or _tp_app,
                    "transfer_party": _tp_side,
                    "direct_answer": _transfer_party_answer(
                        _owner_app, _tp_side, message,
                        language in ("ta", "tanglish")),
                }
                _tp_marker = (structured_data["application_number"], _tp_side)
            else:
                structured_data = _owner_app
        else:
            if not survey_no and _gate_app_number:
                # "What about the owner?" after an application was confirmed means
                # the owner of THAT application's survey. Without this the branch
                # finds no survey number and reports "survey not found", which
                # reads as though the data were missing.
                _owner_app = await get_application_detail(db, _gate_app_number, officer=officer)
                if _owner_app.get("found"):
                    survey_no = _owner_app.get("survey_no")
            if survey_no:
                structured_data = await get_survey_owners(db, survey_no, officer=officer)
                structured_data["query_type"] = "Survey Ownership"
            else:
                structured_data = {
                    "found": False,
                    "message": _cb().ASK_FOR_SURVEY_NUMBER[language if language in _cb().ASK_FOR_SURVEY_NUMBER else "en"],
                    "query_type": "Survey Ownership",
                }

    elif intent == "can_apply_check":
        survey_no = await _survey_ref(db, session_id, message, chat_history)
        if survey_no:
            structured_data = await check_survey_application_lock(db, survey_no, officer=officer)
        else:
            structured_data = {"found": False}
        structured_data["query_type"] = "New Application Allowed?"

    elif intent == "next_subdivision":
        survey_no = await _survey_ref(db, session_id, message, chat_history)
        if survey_no:
            structured_data = await get_next_subdivision_number(db, survey_no, officer=officer)
            structured_data["query_type"] = "Next Sub-division Number"

    elif intent == "ward_surveys" or intent == "block_surveys":
        ward_id = extract_ward_number(message)
        block_id = extract_block_number(message)

        # If no ward specified in message, use officer's ward from jurisdiction
        if not ward_id:
            if officer.jurisdiction_type in ["ward", "block"]:
                # Use officer's assigned jurisdiction to find ward

                if officer.jurisdiction_type == "block":
                    # Officer is assigned to a block, get its ward
                    block_result = await db.execute(
                        select(Block, Ward).join(Ward, Block.ward_id == Ward.id).where(
                            Block.id.in_(officer.jurisdiction_ids)
                        ).limit(1)
                    )
                    row = block_result.first()
                    if row:
                        _, ward_obj = row
                        ward_id = ward_obj.ward_number
                        logger.info(f"Using officer's block's ward: {ward_id}")
                elif officer.jurisdiction_type == "ward":
                    # Officer is assigned to a ward directly
                    ward_result = await db.execute(
                        select(Ward).where(Ward.id.in_(officer.jurisdiction_ids)).limit(1)
                    )
                    ward_obj = ward_result.scalar_one_or_none()
                    if ward_obj:
                        ward_id = ward_obj.ward_number
                        logger.info(f"Using officer's assigned ward: {ward_id}")

        if ward_id:
            structured_data = await get_ward_surveys(db, ward_id, block_id, officer=officer)
            structured_data["query_type"] = "Ward Survey Numbers and Sub-divisions"
        else:
            structured_data = {"found": False, "message": "Please specify a ward number or ensure your officer profile has a ward assignment."}
    _bound_now = locals()
    return StageOutcome(bound={k: _bound_now[k] for k in ('_an', '_app_no', '_field_keywords', '_isd_app_no', '_missing', '_tp_marker', 'app_type', 'apps', 'completed', 'days', 'e', 'is_tamil', 'overlap_date', 'response_text', 'structured_data', 'sub_date', 'survey_no', 'taluk', 'today', 'total', 'working_days') if k in _bound_now})
