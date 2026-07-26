"""
Main chatbot service - RAG orchestration
"""
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta, date
import time
import re
from difflib import SequenceMatcher

from backend.schemas import OfficerContext
from backend.services.rag import (
    detect_language,
    get_rag_context,
    build_prompt,
    build_html_response,
    call_llama,
    call_llama_stream,
    parse_intent,
    extract_survey_number,
    extract_application_number,
    extract_ward_number,
    extract_block_number,
    extract_town_name
)
from backend.services.postgres import (
    get_pending_applications,
    get_overdue_applications,
    get_officer_workload,
    get_application_detail,
    get_survey_detail,
    get_survey_owners,
    get_unscheduled_visits,
    get_field_visits,
    get_next_subdivision_number,
    get_ward_surveys,
    get_all_surveys_in_jurisdiction,
    get_merge_application_detail,
    get_officer_applications
)
from backend.models import (
    ChatSession, ChatMessage, Application, SurveyNumber, Block, Ward, Town, Taluk,
    FieldVisit, ApplicationDocument, WorkflowHistory, Applicant, ApplicationSubDivision,
    OfficerJurisdiction, District, PattaTransfer
)
from backend.utils.logger import get_logger
from sqlalchemy import select, and_, func

logger = get_logger(__name__)


def extract_month_from_query(message: str) -> Optional[int]:
    """
    Extract month from message for filtering applications by submission month.
    Handles English and Tamil month names with fuzzy matching for spelling mistakes.
    
    Returns:
        Month number (1-12) if found, None otherwise
    """
    # Month mappings with common variations and misspellings
    month_patterns = {
        1: ["january", "jan", "ஜனவரி", "januvary", "janaury", "jenuary"],
        2: ["february", "feb", "பிப்ரவரி", "feburary", "febuary", "februry"],
        3: ["march", "mar", "மார்ச்", "மார்ச", "marh"],
        4: ["april", "apr", "ஏப்ரல்", "aprill", "aprl"],
        5: ["may", "மே", "மேமாதம்"],
        6: ["june", "jun", "ஜூன்", "joon", "juun"],
        7: ["july", "jul", "ஜூலை", "jully", "julai"],
        8: ["august", "aug", "ஆகஸ்ட்", "agust", "augest"],
        9: ["september", "sep", "sept", "செப்டம்பர்", "septembar", "septmber"],
        10: ["october", "oct", "அக்டோபர்", "octobr", "octobar"],
        11: ["november", "nov", "நவம்பர்", "novembar", "novembr"],
        12: ["december", "dec", "டிசம்பர்", "decembr", "desember"]
    }
    
    msg_lower = message.lower()
    
    # Direct exact match first
    for month_num, patterns in month_patterns.items():
        for pattern in patterns:
            if pattern in msg_lower:
                logger.info(f"Month extracted: {month_num} (matched '{pattern}')")
                return month_num
    
    # Fuzzy matching for spelling mistakes (threshold 0.75 = 75% similarity)
    words = msg_lower.split()
    for word in words:
        if len(word) < 3:  # Skip very short words
            continue
        for month_num, patterns in month_patterns.items():
            for pattern in patterns:
                if len(pattern) < 3:
                    continue
                similarity = SequenceMatcher(None, word, pattern).ratio()
                if similarity >= 0.75:
                    logger.info(f"Month extracted (fuzzy): {month_num} (word '{word}' ~= '{pattern}', similarity={similarity:.2f})")
                    return month_num
    
    return None


def _extract_app_number_from_context(message: str, chat_history: list = None, allow_implicit_continuation: bool = False) -> str:
    """
    Extract application number from current message or recent chat history.
    Handles references like "this application", "that application", etc.

    Args:
        message: Current user message
        chat_history: List of previous messages
        allow_implicit_continuation: If True, check immediate previous message for app number
                                     even without explicit reference words (for field queries)

    Returns:
        Application number in uppercase, or None if not found
    """
    import re

    # Pattern 1: Standard format (ISD|NISD|MERGE)/XX/YYYY/NNN
    app_match = re.search(r'(ISD|NISD|MERGE)/\w+/\d+/\d+', message, re.IGNORECASE)
    if app_match:
        return app_match.group(0).upper()

    # Pattern 2: APP-YYYY-NNNNNN format
    app_match = re.search(r'APP-\d{4}-\d{6}', message, re.IGNORECASE)
    if app_match:
        return app_match.group(0).upper()

    # Pattern 3: Check if user is referring to a previous application using strict patterns
    # Avoid generic words like "the" which cause false positives
    reference_patterns = [
        "this application", "that application", "same application",
        "this app", "that app", "the application",
        "இந்த விண்ணப்பம்", "அந்த விண்ணப்பம்",
    ]
    _msg_lower = message.lower()
    has_explicit_reference = any(pattern in _msg_lower for pattern in reference_patterns)
    
    # Pattern 4: Implicit continuation - if asking a field query immediately after
    # an application was discussed, assume continuity (only check last 2 messages)
    if allow_implicit_continuation and not has_explicit_reference and chat_history:
        # Check only the immediate previous exchange (last 2 messages: user + assistant)
        for msg in reversed(chat_history[-2:]):
            content = msg.get("content", "")
            app_match = re.search(r'(ISD|NISD|MERGE)/\w+/\d+/\d+', content, re.IGNORECASE)
            if not app_match:
                app_match = re.search(r'APP-\d{4}-\d{6}', content, re.IGNORECASE)
            if app_match:
                logger.info(f"Found implicit application continuation '{app_match.group(0)}' from immediate context")
                return app_match.group(0).upper()
    
    # Pattern 5: Explicit reference - search further back in history (last 5 messages)
    if has_explicit_reference and chat_history:
        for msg in reversed(chat_history[-5:]):
            content = msg.get("content", "")
            app_match = re.search(r'(ISD|NISD|MERGE)/\w+/\d+/\d+', content, re.IGNORECASE)
            if not app_match:
                app_match = re.search(r'APP-\d{4}-\d{6}', content, re.IGNORECASE)
            if app_match:
                logger.info(f"Found application reference '{app_match.group(0)}' from chat history")
                return app_match.group(0).upper()

    return None


def _fuzzy_match_keywords(message_lower: str, keywords: Dict[str, tuple], threshold: float = 0.75) -> Optional[tuple]:
    """
    Fuzzy match keywords with spelling error tolerance.
    
    Args:
        message_lower: Lowercased user message
        keywords: Dictionary mapping keywords to (field_key, field_label) tuples
        threshold: Similarity threshold (0.0 to 1.0), default 0.75 for good balance
    
    Returns:
        (field_key, field_label, matched_keyword) tuple if match found, None otherwise
    """
    # First try exact substring match (fastest)
    for kw, (field_key, field_label) in keywords.items():
        if kw in message_lower:
            return (field_key, field_label, kw)
    
    # If no exact match, try fuzzy matching for spelling errors
    # Split message into words for better matching
    message_words = message_lower.split()
    
    best_match = None
    best_ratio = threshold
    
    for kw in keywords.keys():
        # Check against each word in the message
        for word in message_words:
            # Skip very short words to avoid false matches
            if len(word) < 3:
                continue
            
            # Calculate similarity ratio
            ratio = SequenceMatcher(None, kw.lower(), word).ratio()
            
            # Also check if keyword is a substring (for partial matches)
            if kw in word or word in kw:
                ratio = max(ratio, 0.8)  # Boost partial matches
            
            # Update best match if this is better
            if ratio > best_ratio:
                best_ratio = ratio
                best_match = kw
    
    if best_match:
        field_key, field_label = keywords[best_match]
        logger.info(f"Fuzzy matched '{best_match}' (ratio: {best_ratio:.2f}) from message")
        return (field_key, field_label, best_match)
    
    return None


async def process_chat(
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
        logger.info(f"=== CHAT CONTEXT DEBUG ===")
        logger.info(f"Received {len(chat_history)} previous messages from client")
        logger.info(f"Current message: '{message}'")
        if chat_history:
            for i, msg in enumerate(chat_history[-3:]):  # Show last 3
                logger.info(f"  History[{i}]: {msg.get('role')} said: {msg.get('content', '')[:50]}...")

        # Step 1: Detect language
        language = detect_language(message)
        logger.info(f"Detected language: {language}")
        
        # Step 2: Parse intent to determine which DB query to run
        intent = parse_intent(message)
        logger.info(f"Parsed intent: {intent}")

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
            "fv_reschedule_availability", "fv_deadline_check", "fv_overdue_inspections",
            "fv_unassigned_awaiting", "fv_recently_rescheduled", "fv_scheduling_conflicts",
            "sd_additional_info", "sd_encroachment_check", "sd_sketch_readiness",
            "sd_forward_check", "sd_remarks", "application_status", "isd_processing",
            "officer_workload", "field_visits",
        }
        _skip_keyword_check = intent in _FIELD_VISIT_INTENTS

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
                user_message=message, assistant_message=response_text,
                language=language, response_time_ms=int((time.time() - start_time) * 1000)
            )
            return {
                "response": response_text,
                "language": language,
                "intent": intent,
                "sources": [],
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "context_used": False,
                "response_time_ms": int((time.time() - start_time) * 1000),
                "table_data": None
            }

        # Step 2c: Direct greeting response handling
        if intent == "greeting":
            msg_lower = message.lower().strip()
            is_tamil = language == "ta" or any(w in message for w in ["வணக்கம்", "காலை", "மாலை", "நன்றி", "ஹாய்"])

            if "காலை" in msg_lower or "morning" in msg_lower:
                if is_tamil:
                    response_text = "காலை வணக்கம்! 👋 நான் உங்கள் Sub Inspector Surveyor (SIS) AI உதவியாளர். நில அளவை எண்கள், விண்ணப்பங்கள் (ISD/NISD/MERGE), கள ஆய்வுகள் தொடர்பாக இன்று உங்களுக்கு எவ்வாறு உதவ முடியும்?"
                else:
                    response_text = "Good morning! 👋 I am your Sub Inspector Surveyor (SIS) AI assistant. How can I help you today with survey numbers, applications (ISD/NISD/MERGE), or field visits?"
            elif "மாலை" in msg_lower or "evening" in msg_lower:
                if is_tamil:
                    response_text = "மாலை வணக்கம்! 👋 நான் உங்கள் Sub Inspector Surveyor (SIS) AI உதவியாளர். நில அளவை மற்றும் விண்ணப்பங்கள் தொடர்பான தகவல்களுக்கு இன்று உங்களுக்கு எவ்வாறு உதவ முடியும்?"
                else:
                    response_text = "Good evening! 👋 I am your Sub Inspector Surveyor (SIS) AI assistant. How can I help you with your survey work today?"
            elif "நன்றி" in msg_lower or "thanks" in msg_lower or "thank" in msg_lower:
                if is_tamil:
                    response_text = "நல்வரவு! 😊 உங்களுக்கு மேலும் ஏதேனும் உதவி தேவைப்பட்டால் தயங்காமல் கேளுங்கள்."
                else:
                    response_text = "You're very welcome! 😊 Feel free to ask if you need anything else regarding your survey work."
            else:
                if is_tamil:
                    response_text = "வணக்கம்! 👋 நான் உங்கள் Sub Inspector Surveyor (SIS) AI உதவியாளர். நில அளவை எண்கள், விண்ணப்பங்களின் நிலை, கள ஆய்வுகள் மற்றும் பட்டா பரிமாற்றங்கள் பற்றிய கேள்விகளுக்கு உதவ தயாராக உள்ளேன். இன்று உங்களுக்கு என்ன உதவி தேவை?"
                else:
                    response_text = "Hello! 👋 I am your Sub Inspector Surveyor (SIS) AI assistant. I am here to help you manage survey applications, check document statuses, track field visits, and navigate workflow procedures. What can I assist you with today?"

            from backend.services.chatbot import save_chat_messages
            await save_chat_messages(
                db=db, session_id=session_id,
                user_message=message, assistant_message=response_text,
                language=language, response_time_ms=int((time.time() - start_time) * 1000)
            )
            return {
                "response": response_text,
                "language": language,
                "intent": "greeting",
                "sources": [],
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "context_used": False,
                "response_time_ms": int((time.time() - start_time) * 1000),
                "table_data": None
            }

        # Step 3: Execute structured database queries based on intent
        structured_data = {}
        response_text = ""
        
        if intent == "pending_applications":
            # Extract application type if mentioned in message
            app_type = None
            message_lower = message.lower()
            if "isd" in message_lower:
                app_type = "ISD"
            elif "nisd" in message_lower:
                app_type = "NISD"
            elif "merge" in message_lower:
                app_type = "MERGE"
            
            # Extract year from message (e.g., "2025", "2026")
            year_match = re.search(r'\b(20\d{2})\b', message)
            submission_year = int(year_match.group(1)) if year_match else None
            
            # Extract month from message (handles English/Tamil with fuzzy matching)
            submission_month = extract_month_from_query(message)
                
            # For MERGE apps show all statuses; for others default to pending
            if app_type == "MERGE":
                status_filter = None
            else:
                status_filter = "pending"
                if "history" in message_lower or "approved n rejected" in message_lower or "approved and rejected" in message_lower:
                    status_filter = ["approved", "rejected"]
                elif "all" in message_lower:
                    status_filter = None
                elif "complete" in message_lower or "approved" in message_lower:
                    status_filter = "approved"
                elif "reject" in message_lower:
                    status_filter = "rejected"
                
            structured_data = await get_pending_applications(
                db, officer, 
                application_type=app_type, 
                status=status_filter, 
                submission_year=submission_year,
                submission_month=submission_month
            )
            
            # Determine appropriate query title
            type_str = f" {app_type}" if app_type else ""
            year_str = f" in {submission_year}" if submission_year else ""
            
            # Add month string to title if month filter is present
            month_str = ""
            if submission_month:
                month_names = ["", "January", "February", "March", "April", "May", "June",
                             "July", "August", "September", "October", "November", "December"]
                month_str = f" {month_names[submission_month]}" if submission_year else f" in {month_names[submission_month]}"
            
            if app_type == "MERGE":
                structured_data["query_type"] = f"MERGE Applications{month_str}{year_str}"
            elif status_filter == ["approved", "rejected"]:
                structured_data["query_type"] = f"SIS{type_str} History (Approved & Rejected){month_str}{year_str}"
            elif status_filter is None:
                structured_data["query_type"] = f"All{type_str} Applications{month_str}{year_str}"
            elif status_filter == "approved":
                structured_data["query_type"] = f"Approved{type_str} Applications{month_str}{year_str}"
            elif status_filter == "rejected":
                structured_data["query_type"] = f"Rejected{type_str} Applications{month_str}{year_str}"
            else:
                structured_data["query_type"] = f"Pending{type_str} Applications{month_str}{year_str}"
            
        elif intent == "overdue_applications":
            # Extract application type if mentioned in message
            app_type = None
            message_lower = message.lower()
            if "isd" in message_lower:
                app_type = "ISD"
            elif "nisd" in message_lower:
                app_type = "NISD"
            elif "merge" in message_lower:
                app_type = "MERGE"
                
            structured_data = await get_overdue_applications(db, officer, application_type=app_type)
            if app_type:
                structured_data["query_type"] = f"Overdue {app_type} Applications"
            else:
                structured_data["query_type"] = "Overdue Applications"
            
        elif intent == "officer_workload":
            structured_data = await get_officer_workload(db, officer)
            structured_data["query_type"] = "Officer Workload Summary"
            
        elif intent == "fv_overdue_inspections":
            # Get field visits that are overdue (scheduled date in past, not completed)
            try:
                logger.info(f"🔍 Fetching overdue field visits for officer {officer.officer_id}")
                from datetime import date
                today = date.today()
                
                query = select(FieldVisit).where(
                    and_(
                        FieldVisit.officer_id == officer.officer_id,
                        FieldVisit.scheduled_date < today,
                        FieldVisit.status.in_(["scheduled", "rescheduled", "overdue"])
                    )
                )
                
                result = await db.execute(query)
                overdue_visits = result.scalars().all()
                
                logger.info(f"📊 Found {len(overdue_visits)} overdue field visits")
                
                # Build structured data for table rendering
                visit_data = []
                for fv in overdue_visits:
                    app = await db.get(Application, fv.application_id)
                    survey = await db.get(SurveyNumber, app.survey_number_id) if app else None
                    block = None
                    if survey:
                        block = await db.get(Block, survey.block_id)
                    
                    days_overdue = (today - fv.scheduled_date).days
                    
                    visit_data.append({
                        "visit_id": str(fv.id),
                        "application_number": app.application_number if app else "N/A",
                        "survey_no": survey.survey_no if survey else "N/A",
                        "block_number": block.block_number if block else None,
                        "application_type": app.application_type if app else "N/A",
                        "field_visit_date": fv.scheduled_date.strftime("%Y-%m-%d"),
                        "status": fv.status,
                        "days_overdue": days_overdue
                    })
                
                structured_data = {
                    "overdue_count": len(overdue_visits),
                    "field_visits": visit_data,
                    "query_type": "Overdue Field Visits"
                }
                
                # Set response_text explicitly to prevent LLM fallback
                count = len(visit_data)
                if count == 0:
                    response_text = "No field visits are currently overdue. All field visits are on schedule."
                else:
                    response_text = f"Found {count} overdue field visit(s). See the table below for details."
                
                logger.info(f"✅ Built structured data with {len(visit_data)} overdue visits, response_text set")
                
            except Exception as e:
                logger.error(f"❌ Error fetching overdue field visits: {str(e)}")
                structured_data = {
                    "error": f"Error fetching overdue field visits: {str(e)}",
                    "query_type": "Overdue Field Visits"
                }
                response_text = f"Error retrieving overdue field visits: {str(e)}"
            
        elif intent == "field_visits":
            # Check if asking for scheduled or unscheduled visits
            msg_lower = message.lower()
            status_filter = None
            if "unscheduled" in msg_lower or "not scheduled" in msg_lower or "yet to schedule" in msg_lower:
                status_filter = "unscheduled"
                query_type = "Unscheduled Field Visits"
            elif "scheduled" in msg_lower or "visit date" in msg_lower or "when" in msg_lower or "schedule" in msg_lower:
                status_filter = "scheduled"
                query_type = "Scheduled Field Visits"
            else:
                # Default to all field visits
                query_type = "Field Visits Summary"
                
            structured_data = await get_field_visits(db, officer, status_filter=status_filter)
            structured_data["query_type"] = query_type
            
        elif intent == "active_applications_taluks":
            from backend.models import SurveyNumber, Block, Ward, Town, Taluk
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
                    Application.assigned_officer_id == officer.officer_id,
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
            # Return applications in table format, not just app numbers
            structured_data = await get_pending_applications(
                db, officer, 
                status=["pending", "in_progress"]
            )
            
            # SIS officers should only see priority apps in SIS stage
            # They don't handle DIS, SD, or Tahsildar stage applications
            # Auto-filter by officer's stage jurisdiction
            officer_stage = getattr(officer, "current_stage", None) or "SIS"  # Default to SIS if not set
            
            # Extract explicit stage filter from message if specified (optional override)
            message_lower = message.lower()
            stage_filter = None
            if "all stages" in message_lower or "all stage" in message_lower:
                stage_filter = None  # Show all stages if explicitly requested
            elif "sis" in message_lower and "stage" in message_lower:
                stage_filter = "SIS"
            elif "sd" in message_lower and "stage" in message_lower:
                stage_filter = "SD"
            elif "dis" in message_lower and "stage" in message_lower:
                stage_filter = "DIS"
            elif "tahsildar" in message_lower:
                stage_filter = "TAHSILDAR"
            else:
                # Default: auto-filter by officer's stage
                stage_filter = officer_stage
            
            # Filter to only priority applications
            # Priority = manual flag OR overdue OR status contains warning
            if structured_data and structured_data.get("applications"):
                priority_apps = []
                for app in structured_data["applications"]:
                    is_priority = (
                        app.get("priority_flag") == True or
                        app.get("is_overdue") == True or
                        "⚠" in str(app.get("status", "")) or
                        "⚠" in str(app.get("stage", ""))
                    )
                    # Apply stage filter if specified
                    if stage_filter:
                        matches_stage = app.get("stage") == stage_filter or app.get("current_stage") == stage_filter
                        is_priority = is_priority and matches_stage
                    
                    if is_priority:
                        priority_apps.append(app)
                
                structured_data["applications"] = priority_apps
                structured_data["count"] = len(priority_apps)
            
            # Update query type to reflect stage filter
            if stage_filter:
                structured_data["query_type"] = f"High Priority Applications — {stage_filter} Stage"
            else:
                structured_data["query_type"] = "High Priority Applications — All Stages"

        elif intent == "assigned_today":
            today = date.today()
            query = select(func.count(Application.id)).where(
                and_(
                    Application.assigned_officer_id == officer.officer_id,
                    Application.submission_date == today
                )
            )
            res = await db.execute(query)
            structured_data = {
                "count": res.scalar(),
                "query_type": "Applications Assigned Today"
            }

        elif intent == "immediate_action":
            from backend.models import Application, FieldVisit, SurveyNumber, Block, Ward, Town
            from sqlalchemy.orm import joinedload
            from datetime import date as _date_imm
            
            # Get all pending/in-progress applications assigned to this officer
            apps_query = select(Application).options(
                joinedload(Application.survey_number).joinedload(SurveyNumber.block).joinedload(Block.ward).joinedload(Ward.town)
            ).where(
                and_(
                    Application.assigned_officer_id == officer.officer_id,
                    Application.current_status.in_(["pending", "in_progress"])
                )
            ).order_by(Application.submission_date.asc())
            
            res_apps = await db.execute(apps_query)
            all_apps = res_apps.scalars().all()
            
            # Calculate working days and identify overdue applications (>15 working days)
            _today_imm = _date_imm.today()
            rows = []
            
            for a in all_apps:
                if not a.submission_date:
                    continue
                    
                # Calculate working days (exclude weekends)
                working_days = 0
                current_date = a.submission_date
                while current_date < _today_imm:
                    current_date += timedelta(days=1)
                    if current_date.weekday() < 5:  # Monday = 0, Sunday = 6
                        working_days += 1
                
                # Consider overdue if more than 15 working days have elapsed
                if working_days > 15:
                    sn = a.survey_number
                    bl = sn.block if sn else None
                    w = bl.ward if bl else None
                    t = w.town if w else None
                    
                    rows.append({
                        "application_number": a.application_number,
                        "type": a.application_type,
                        "town_name": t.name if t else "N/A",
                        "ward_number": w.ward_number if w else "N/A",
                        "status": "Action Required",
                        "current_stage": a.current_stage,
                        "submission_date": a.submission_date.isoformat(),
                        "working_days_elapsed": working_days,
                        "days_overdue": working_days - 15
                    })
            
            structured_data = {
                "applications": rows,
                "query_type": "Immediate Action Required — Overdue Applications"
            }


        elif intent == "awaiting_field_visit":
            from backend.models import FieldVisit
            query = select(func.count(FieldVisit.id)).where(
                and_(
                    FieldVisit.officer_id == officer.officer_id,
                    FieldVisit.status.in_(["scheduled", "unscheduled"])
                )
            )
            res = await db.execute(query)
            structured_data = {
                "count": res.scalar(),
                "query_type": "Awaiting Field Visit"
            }

        elif intent == "workload_by_type":
            structured_data = await get_officer_workload(db, officer)
            structured_data["query_type"] = "Workload by Type"

        elif intent == "completion_rate":
            from datetime import date as _date_cr
            _msg_lower_cr = message.lower()
            _this_month = any(p in _msg_lower_cr for p in ["this month", "month", "monthly", "current month"])
            _today_cr = date.today()
            _month_start = _today_cr.replace(day=1)

            if _this_month:
                completed_query = select(func.count(Application.id)).where(
                    and_(
                        Application.assigned_officer_id == officer.officer_id,
                        Application.current_status.in_(["approved", "rejected"]),
                        Application.updated_at >= _month_start
                    )
                )
                total_query = select(func.count(Application.id)).where(
                    and_(
                        Application.assigned_officer_id == officer.officer_id,
                        Application.submission_date >= _month_start
                    )
                )
                scope_label = f"this month ({_month_start.strftime('%B %Y')})"
            else:
                completed_query = select(func.count(Application.id)).where(
                    and_(
                        Application.assigned_officer_id == officer.officer_id,
                        Application.current_status.in_(["approved", "rejected"])
                    )
                )
                total_query = select(func.count(Application.id)).where(
                    Application.assigned_officer_id == officer.officer_id
                )
                scope_label = "overall"

            completed = (await db.execute(completed_query)).scalar() or 0
            total = (await db.execute(total_query)).scalar() or 0
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
                    Application.assigned_officer_id == officer.officer_id,
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
            
        elif intent in ["is_nisd_or_isd", "check_documents", "check_sale_deed"]:
            app_number = extract_application_number(message)
            if not app_number:
                # Allow implicit continuation since these are specific queries about an application
                app_number = _extract_app_number_from_context(message, chat_history, allow_implicit_continuation=True)
            
            if not app_number:
                # No app number - ask for it
                is_tamil_lang = language in ("ta", "tanglish")
                if is_tamil_lang:
                    response_text = "தயவுசெய்து விண்ணப்ப எண்ணை குறிப்பிடவும். (எ.கா: APP-2024-000001)"
                else:
                    response_text = "Please specify which application you're asking about. For example: APP-2024-000001"
                structured_data = {"found": False, "query_type": "Application Details"}
            else:
                structured_data = await get_application_detail(db, app_number)
                structured_data["query_type"] = "Application Details"

        elif intent == "isd_processing":
            app_number = extract_application_number(message) or _extract_app_number_from_context(message, chat_history)
            if not app_number:
                from backend.models import Application
                res_app = await db.execute(select(Application).order_by(Application.created_at.desc()).limit(1))
                a_last = res_app.scalar_one_or_none()
                app_number = a_last.application_number if a_last else "APP-2024-000001"
            structured_data = await get_application_detail(db, app_number)
            structured_data["query_type"] = "ISD Processing"
            message_lower_isd = message.lower()

            proposed = structured_data.get("proposed_sub_divisions", [])
            survey_no = structured_data.get("survey_no", "N/A")
            survey_area = structured_data.get("survey_total_area_sqm")
            proposed_area = structured_data.get("proposed_total_area_sqm")
            area_match = structured_data.get("area_match")
            patta_count = structured_data.get("patta_transfers_count", 0)

            # Q1 / Q4 – proposed sub-divisions (count or list)
            if "proposed" in message_lower_isd:
                count = len(proposed)
                if count == 0:
                    response_text = f"No proposed sub-divisions found for application {app_number} under Survey {survey_no}."
                elif "how many" in message_lower_isd:
                    response_text = f"{count} sub-division(s) are proposed under Survey No. {survey_no}: {', '.join(p['proposed_sub_division_no'] for p in proposed)}."
                else:
                    lines = [f"  • {p['proposed_sub_division_no']} — {int(p['proposed_area_sqm'])} sq.m — {p['status'].capitalize()}" for p in proposed if p.get("proposed_area_sqm")]
                    response_text = f"Proposed sub-divisions for {app_number} (Survey {survey_no}):\n" + "\n".join(lines)

            # Q2 – retrieve application status by sub-division
            elif "status" in message_lower_isd and ("retrieve" in message_lower_isd or "by sub" in message_lower_isd):
                if proposed:
                    status_parts = [f"{p['proposed_sub_division_no']} – {p['status'].capitalize()}" for p in proposed]
                    response_text = "Application status by sub-division: " + ", ".join(status_parts) + "."
                else:
                    response_text = f"No sub-division status found for application {app_number}."

            # Q3 – patta transfer count
            elif any(w in message_lower_isd for w in ["patta transfer", "transfer order"]):
                if patta_count > 0:
                    response_text = f"{patta_count} patta transfer order(s) will be generated — one per approved sub-division under {app_number}."
                else:
                    response_text = f"No patta transfer orders found for application {app_number}. They are generated after approval."

            # Q5 – assigned sub-division numbers
            elif "assigned" in message_lower_isd and any(w in message_lower_isd for w in ["number", "numbers"]):
                approved = [p for p in proposed if p.get("status") == "approved"]
                if approved:
                    nums = ", ".join(p["proposed_sub_division_no"] for p in approved)
                    response_text = f"Assigned sub-division numbers for approved {app_number}: {nums}."
                else:
                    response_text = "Sub-division numbers are assigned after the Survey Department approves the subdivision sketch."

            # Q6 – area comparison
            elif any(w in message_lower_isd for w in ["compare", "original"]) and "area" in message_lower_isd:
                if survey_area and proposed_area:
                    diff = abs(survey_area - proposed_area)
                    if area_match:
                        match_str = "✅ Areas match — no discrepancy."
                    else:
                        match_str = f"⚠ Mismatch! Difference: {diff:,.2f} sq.m. Please verify the manually entered sub-division areas."
                    response_text = (
                        f"Original Survey {survey_no} area: {survey_area:,.2f} sq.m\n"
                        f"Total proposed sub-division area: {proposed_area:,.2f} sq.m\n"
                        f"{match_str}"
                    )
                elif survey_area and not proposed_area:
                    response_text = (
                        f"Survey {survey_no} original area: {survey_area:,.2f} sq.m. "
                        f"However, no sub-division area data is available for {app_number} — "
                        f"the proposed sub-division areas may not have been entered yet."
                    )
                else:
                    response_text = f"Area data not available for application {app_number}."

            # Q7 – latest action per sub-division
            elif any(w in message_lower_isd for w in ["latest action", "action taken", "each sub-division", "each subdivision"]):
                if proposed:
                    parts = [f"{p['proposed_sub_division_no']} – {p['status'].capitalize()}" for p in proposed]
                    response_text = "Latest status for each sub-division: " + ", ".join(parts) + "."
                else:
                    response_text = f"No sub-division action data found for application {app_number}."

            else:
                # Generic fallback — show full proposed list
                if proposed:
                    lines = [f"  • {p['proposed_sub_division_no']} — {p['status'].capitalize()}" for p in proposed]
                    response_text = f"Sub-divisions for {app_number} (Survey {survey_no}):\n" + "\n".join(lines)
                else:
                    response_text = f"No ISD processing data found for application {app_number}."


        elif intent in ["sd_additional_info", "sd_encroachment_check", "sd_sketch_readiness", "sd_forward_check", "sd_remarks", "fv_date_select", "fv_nearby_pending", "fv_scheduled_this_week", "fv_reschedule_availability", "fv_deadline_check", "fv_overdue_inspections", "fv_unassigned_awaiting", "fv_recently_rescheduled", "fv_scheduling_conflicts"]:
            app_number = extract_application_number(message)

            # ── fv_scheduled_this_week without a specific application ──────────
            # When the officer asks "how many scheduled in this taluk this week?"
            # without citing an app number, answer from the officer's own taluk directly.
            _handled_week_query = False
            if intent == "fv_scheduled_this_week" and not app_number:
                _handled_week_query = True
                from backend.models import OfficerJurisdiction, Taluk, Town, Ward, Block, SurveyNumber
                from datetime import datetime, timedelta

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

                today = datetime.utcnow().date()
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
                            FieldVisit.officer_id == officer.officer_id,
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
                from backend.models import FieldVisit, ApplicationSubDivision
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
                        FieldVisit.officer_id == officer.officer_id,
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
                    sis_nos = ", ".join(
                        sd.proposed_sub_division_no for sd in ua.application_sub_divisions
                        if sd.proposed_sub_division_no
                    ) or "N/A"
                    dis_nos = ", ".join(
                        sd.sub_division.sub_division_no for sd in ua.application_sub_divisions
                        if sd.sub_division and sd.sub_division.sub_division_no
                    ) or "N/A"
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
                    "query_type": "திட்டமிடல் காத்திருக்கும் கள ஆய்வுகள்" if language == "ta" else "Unassigned Field Visits — Awaiting Scheduling"
                }
                # response built by fv_unassigned_awaiting handler below

            if intent == "fv_deadline_check":
                _handled_week_query = True
                # Resolve application number from message or chat history
                resolved_app = app_number or _extract_app_number_from_context(message, chat_history)
                if not resolved_app:
                    structured_data = {
                        "found": False,
                        "message": "Please specify an application number, e.g. APP-2024-000001, to check the deadline."
                    }
                else:
                    from backend.models import Application
                    from sqlalchemy.orm import joinedload
                    app_res = await db.execute(
                        select(Application)
                        .where(Application.application_number == resolved_app)
                    )
                    a_dl = app_res.scalar_one_or_none()
                    if not a_dl:
                        structured_data = {"found": False, "message": f"Application {resolved_app} not found."}
                    else:
                        sub_date = a_dl.submission_date
                        today_dl = datetime.utcnow().date()
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
                    from backend.models import Application
                    res_app = await db.execute(select(Application).order_by(Application.created_at.desc()).limit(1))
                    a = res_app.scalar_one_or_none()
                    app_number = a.application_number if a else "APP-2024-000001"
                
                from backend.models import Application, ApplicationDocument, WorkflowHistory, FieldVisit, SurveyNumber, Block, Ward, Town
                from sqlalchemy.orm import joinedload
                
                app_res = await db.execute(
                    select(Application)
                    .options(joinedload(Application.survey_number).joinedload(SurveyNumber.block).joinedload(Block.ward).joinedload(Ward.town).joinedload(Town.taluk))
                    .where(Application.application_number == app_number)
                )
                a = app_res.scalar_one_or_none()
                
                if not a:
                    structured_data = {"found": False, "message": f"Application {app_number} not found."}
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
                        today = datetime.utcnow().date()
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
                                FieldVisit.officer_id == officer.officer_id,
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
                    test_date = datetime.utcnow().date() + timedelta(days=offset)
                    if test_date.weekday() >= 5:
                        continue
                    visit_count = (await db.execute(
                        select(func.count(FieldVisit.id)).where(
                            and_(
                                FieldVisit.officer_id == officer.officer_id,
                                FieldVisit.scheduled_date == test_date
                            )
                        )
                    )).scalar() or 0
                    if visit_count == 0:
                        reschedule_date = test_date.isoformat()
                        break
                if not reschedule_date:
                    reschedule_date = (datetime.utcnow().date() + timedelta(days=1)).isoformat()
                
                sub_date = a.submission_date
                today = datetime.utcnow().date()
                working_days = 0
                curr = sub_date
                while curr < today:
                    curr += timedelta(days=1)
                    if curr.weekday() < 5:
                        working_days += 1
                
                overdue_visits_stmt = select(func.count(FieldVisit.id)).where(
                    and_(
                        FieldVisit.officer_id == officer.officer_id,
                        FieldVisit.status == "overdue"
                    )
                )
                overdue_visits_count = (await db.execute(overdue_visits_stmt)).scalar() or 0
                
                unassigned_visits_stmt = select(func.count(FieldVisit.id)).where(
                    and_(
                        FieldVisit.officer_id == officer.officer_id,
                        FieldVisit.status.in_(["unscheduled"])
                    )
                )
                unassigned_visits_count = (await db.execute(unassigned_visits_stmt)).scalar() or 0
                
                # Fetch actual unassigned applications with details for table display
                from backend.models import Applicant, ApplicationSubDivision
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
                        FieldVisit.officer_id == officer.officer_id,
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
                    # SIS temporary number (proposed by SIS during field visit)
                    sis_temp_nos = ", ".join(
                        sd.proposed_sub_division_no for sd in ua.application_sub_divisions
                        if sd.proposed_sub_division_no
                    ) or "N/A"
                    # DIS permanent/fixed number (from SubDivision record assigned by DIS)
                    dis_fixed_nos = ", ".join(
                        sd.sub_division.sub_division_no for sd in ua.application_sub_divisions
                        if sd.sub_division and sd.sub_division.sub_division_no
                    ) or "N/A"
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
                            FieldVisit.officer_id == officer.officer_id,
                            FieldVisit.updated_at >= datetime.utcnow() - timedelta(days=7)
                        )
                    )
                )).scalar() or 0
                
                overlap_date = None
                overlap_stmt = select(FieldVisit.scheduled_date).where(
                    and_(
                        FieldVisit.officer_id == officer.officer_id,
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
            
        elif intent == "isd_applications":
            structured_data = await get_officer_applications(db, officer, application_type="ISD")
            structured_data["query_type"] = "ISD Applications"

        elif intent == "nisd_applications":
            structured_data = await get_officer_applications(db, officer, application_type="NISD")
            structured_data["query_type"] = "NISD Applications"

        elif intent == "merge_applications":
            structured_data = await get_officer_applications(db, officer, application_type="MERGE")
            structured_data["query_type"] = "MERGE Applications"

        elif intent == "all_surveys_in_jurisdiction":
            structured_data = await get_all_surveys_in_jurisdiction(db, officer)
            structured_data["query_type"] = "All Surveys in Your Jurisdiction"

        elif intent == "merge_info":
            app_number = _extract_app_number_from_context(message, chat_history)
            if app_number:
                structured_data = await get_merge_application_detail(db, app_number, officer)
            else:
                structured_data = await get_merge_application_detail(db, None, officer)
            structured_data["query_type"] = "Merge Application Details"

        elif intent == "application_status":
            # Extract from current message first; only check history if user uses reference words
            app_number = extract_application_number(message)
            if not app_number:
                # Check for explicit reference patterns OR implicit continuation for field queries
                # Implicit continuation: if user just discussed an app, next field query refers to it
                _field_keywords = [
                    "name", "address", "mobile", "phone", "email", "status", "stage",
                    "பெயர்", "முகவரி", "தொலைபேசி", "நிலை", "கட்டம்"
                ]
                is_field_query = any(kw in message.lower() for kw in _field_keywords)
                app_number = _extract_app_number_from_context(message, chat_history, allow_implicit_continuation=is_field_query)
                
                # Only fall back to most recent application if explicit reference pattern found
                if not app_number:
                    reference_patterns = [
                        "this application", "that application", "same application",
                        "this app", "that app",
                        "இந்த விண்ணப்பம்", "அந்த விண்ணப்பம்",
                    ]
                    _msg_lower = message.lower()
                    if any(pattern in _msg_lower for pattern in reference_patterns):
                        from backend.models import Application as _AppStatusModel
                        _last_app = (await db.execute(
                            select(_AppStatusModel)
                            .where(_AppStatusModel.assigned_officer_id == officer.officer_id)
                            .order_by(_AppStatusModel.updated_at.desc())
                            .limit(1)
                        )).scalar_one_or_none()
                        if _last_app:
                            app_number = _last_app.application_number
            if app_number:
                if "history" in message.lower() or "workflow" in message.lower() or "timeline" in message.lower():
                    from backend.models import WorkflowHistory
                    from sqlalchemy.orm import joinedload
                    app_res = await db.execute(select(Application).where(Application.application_number == app_number))
                    a = app_res.scalar_one_or_none()
                    if not a:
                        structured_data = {"found": False, "message": f"Application {app_number} not found."}
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
                    structured_data = await get_application_detail(db, app_number)
                    structured_data["query_type"] = "Application Status"

        elif intent == "jurisdiction_summary":
            from backend.models import OfficerJurisdiction, District, Taluk, Town, Ward, Block
            from sqlalchemy.orm import joinedload
            q = select(OfficerJurisdiction).options(
                joinedload(OfficerJurisdiction.district),
                joinedload(OfficerJurisdiction.taluk),
                joinedload(OfficerJurisdiction.town),
                joinedload(OfficerJurisdiction.ward),
                joinedload(OfficerJurisdiction.block)
            ).where(OfficerJurisdiction.officer_id == officer.officer_id)
            res = await db.execute(q)
            jurisdictions = res.scalars().all()
            
            if not jurisdictions:
                structured_data = {"found": False, "message": "No jurisdictions assigned."}
            else:
                first = jurisdictions[0]
                d = first.district
                tk = first.taluk
                
                towns_map = {}
                for j in jurisdictions:
                    if j.town:
                        t_name = j.town.name
                        if t_name not in towns_map:
                            towns_map[t_name] = {}
                        if j.ward:
                            w_num = j.ward.ward_number
                            if w_num not in towns_map[t_name]:
                                towns_map[t_name][w_num] = []
                            if j.block:
                                towns_map[t_name][w_num].append({"block_number": j.block.block_number})
                
                towns_list = []
                for t_name, wards_map in towns_map.items():
                    wards_list = []
                    for w_num, blocks in wards_map.items():
                        wards_list.append({
                            "ward_number": w_num,
                            "blocks": blocks
                        })
                    towns_list.append({
                        "name": t_name,
                        "wards": wards_list
                    })
                
                survey_count = 0
                if tk:
                    survey_count = (await db.execute(
                        select(func.count(SurveyNumber.id))
                        .join(Block, SurveyNumber.block_id == Block.id)
                        .join(Ward, Block.ward_id == Ward.id)
                        .join(Town, Ward.town_id == Town.id)
                        .join(Taluk, Town.taluk_id == Taluk.id)
                        .where(Taluk.id == tk.id)
                    )).scalar() or 0
                
                active_count = (await db.execute(
                    select(func.count(Application.id)).where(
                        and_(
                            Application.assigned_officer_id == officer.officer_id,
                            Application.current_status.in_(["pending", "in_progress"])
                        )
                    )
                )).scalar() or 0
                
                structured_data = {
                    "jurisdiction": {
                        "district": {"name": d.name if d else "N/A", "code": d.district_code if d else "N/A"},
                        "taluk": {"name": tk.name if tk else "N/A"},
                        "towns": towns_list,
                        "survey_count": survey_count,
                        "active_applications": active_count
                    },
                    "query_type": "Jurisdiction Summary"
                }

        elif intent == "town_applications":
            town_name = extract_town_name(message)
            from backend.models import SurveyNumber, Block, Ward, Town
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
            from backend.models import SurveyNumber, Block, Ward, Town
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
            app_number = extract_application_number(message)
            if not app_number:
                app_number = "APP-2024-000006"
            from backend.models import WorkflowHistory
            app_res = await db.execute(select(Application).where(Application.application_number == app_number))
            a = app_res.scalar_one_or_none()
            if not a:
                structured_data = {"found": False, "message": f"Application {app_number} not found."}
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
            from backend.models import OfficerJurisdiction
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

        elif intent == "litigation_check":
            survey_no = extract_survey_number(message)
            if not survey_no:
                survey_no = "145"
            from backend.models import SurveyNumber
            res = await db.execute(select(SurveyNumber).where(SurveyNumber.survey_no == survey_no))
            sn = res.scalar_one_or_none()
            if sn:
                structured_data = {
                    "survey_no": sn.survey_no,
                    "litigation_flag": sn.has_litigation,
                    "query_type": "Litigation Check"
                }
            else:
                structured_data = {"found": False, "message": f"Survey number {survey_no} not found."}

        elif intent in ["check_sale_deed", "sale_deed_check"]:
            app_number = extract_application_number(message)
            if not app_number:
                app_number = _extract_app_number_from_context(message, chat_history)
            
            if not app_number:
                # No app number provided - ask user for it
                is_tamil_lang = language in ("ta", "tanglish")
                if is_tamil_lang:
                    response_text = "தயவுசெய்து விண்ணப்ப எண்ணை குறிப்பிடவும். (எ.கா: APP-2024-000001)"
                else:
                    response_text = "Please specify which application you're asking about. For example: APP-2024-000001"
                structured_data = {"found": False, "query_type": "Sale Deed Verification"}
            else:
                structured_data = await get_application_detail(db, app_number)
                structured_data["query_type"] = "Sale Deed Verification"
                structured_data["sale_deed_verified"] = structured_data.get("sale_deed_registered", False)

        elif intent == "joint_owner_check":
            # Check if asking about an application's survey ownership or direct survey ownership
            app_number = extract_application_number(message)
            if not app_number:
                # Allow implicit continuation for joint owner queries
                app_number = _extract_app_number_from_context(message, chat_history, allow_implicit_continuation=True)
            
            if app_number:
                # Get application details to find the survey number
                app_data = await get_application_detail(db, app_number)
                survey_no = app_data.get("survey_no") if app_data.get("found") else None
                if not survey_no:
                    structured_data = {"found": False, "message": f"Application {app_number} not found or has no survey linked"}
                else:
                    owners_data = await get_survey_owners(db, survey_no)
                    joint_owners = [o for o in owners_data.get("owners", []) if o.get("is_joint_owner")]
                    structured_data = {
                        "found": True,
                        "application_number": app_number,
                        "survey_no": survey_no,
                        "joint_owners": joint_owners,
                        "total_owners": len(owners_data.get("owners", [])),
                        "query_type": "Joint Ownership Check"
                    }
            else:
                # Direct survey number query
                survey_no = extract_survey_number(message)
                if not survey_no:
                    structured_data = {"found": False, "message": "Please provide an application number or survey number"}
                else:
                    owners_data = await get_survey_owners(db, survey_no)
                    joint_owners = [o for o in owners_data.get("owners", []) if o.get("is_joint_owner")]
                    structured_data = {
                        "found": True,
                        "survey_no": survey_no,
                        "joint_owners": joint_owners,
                        "total_owners": len(owners_data.get("owners", [])),
                        "query_type": "Joint Ownership Details"
                    }

        elif intent == "escalation_check":
            # Find applications approaching OR past the 15-working-day escalation threshold
            # "Approaching" = 12-15 working days elapsed; "Overdue" = 16+ working days
            from datetime import date as _date_esc
            _today_esc = _date_esc.today()

            # Get all pending/in-progress apps for this officer
            esc_query = select(Application).where(
                and_(
                    Application.assigned_officer_id == officer.officer_id,
                    Application.current_status.in_(["pending", "in_progress"])
                )
            ).order_by(Application.submission_date.asc())
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
                    approaching_apps.append({
                        "application_number": a.application_number,
                        "type": a.application_type,
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
            survey_no = extract_survey_number(message)
            if survey_no:
                structured_data = await get_survey_detail(db, survey_no)
                structured_data["query_type"] = "Survey Number Details"
        
        elif intent == "survey_owners":
            survey_no = extract_survey_number(message)
            if survey_no:
                structured_data = await get_survey_owners(db, survey_no)
                structured_data["query_type"] = "Survey Ownership"
        
        elif intent == "next_subdivision":
            survey_no = extract_survey_number(message)
            if survey_no:
                structured_data = await get_next_subdivision_number(db, survey_no)
                structured_data["query_type"] = "Next Sub-division Number"
        
        elif intent == "ward_surveys" or intent == "block_surveys":
            ward_id = extract_ward_number(message)
            block_id = extract_block_number(message)
            
            # If no ward specified in message, use officer's ward from jurisdiction
            if not ward_id:
                if officer.jurisdiction_type in ["ward", "block"]:
                    # Use officer's assigned jurisdiction to find ward
                    from backend.models import Ward, Block
                    
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
                structured_data = await get_ward_surveys(db, ward_id, block_id)
                structured_data["query_type"] = "Ward Survey Numbers and Sub-divisions"
            else:
                structured_data = {"found": False, "message": "Please specify a ward number or ensure your officer profile has a ward assignment."}
        
        # Step 4: Get RAG context from pgvector — skip if DB data was actually found
        # (avoids FAQ docs contaminating DB answers)
        has_db_results = (
            structured_data
            and structured_data.get("found", True)
            and structured_data.get("count", 0) > 0
        )
        rag_context = get_rag_context(message, language, n_results=5) if not has_db_results else ""
        context_used = len(rag_context) > 0

        # Step 5: Try to build HTML directly from structured data (no LLM needed).
        # Skip the HTML path when the user is asking a specific question about
        # the data (interrogative queries).
        _msg_lower = message.lower()
        _interrogative_keywords = [
            "which", "what", "how many", "how much", "why", "who",
            "where", "where is", "which department", "currently",
            "give me", "tell me", "show me", "get me",
            "எந்த", "என்ன", "எத்தனை", "ஏன்", "யார்",
        ]
        # Specific field keywords that indicate the user wants one piece of data (English + Tamil)
        _field_keywords = [
            "address", "mobile", "phone", "email", "name", "status", "type",
            "stage", "date", "year", "survey", "applicant", "priority", "aadhaar",
            "reason", "overdue", "nisd", "isd", "merge",
            # Tamil field keywords
            "முகவரி", "தொலைபேசி", "மின்னஞ்சல்", "பெயர்", "நாமாகும்", "நாமம்", "நிலை", "வகை",
            "கட்டம்", "தேதி", "ஆண்டு", "கணக்கெண்", "விண்ணப்பதாரர்", "முன்னுரிமை",
            "காரணம்", "காலதாமத",
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
        # Also treat as interrogative when user asks for a specific field
        # In Tamil, users often directly state field name + app number without "what is" phrasing
        _has_field_keyword = any(kw in _msg_lower for kw in _field_keywords)
        _has_interrogative_phrase = any(
            kw in _msg_lower for kw in ["give", "tell", "show", "get", "what", "provide",
                                         "where", "which", "currently", "right now", "is this"]
        )
        # If has field keyword + app number pattern, treat as field query even without interrogative words
        _has_app_number = bool(re.search(r'APP-\d{4}-\d{6}', message, re.IGNORECASE))
        _asking_specific_field = _has_field_keyword and (_has_interrogative_phrase or _has_app_number)
        _is_interrogative = _is_interrogative or _asking_specific_field
        _bypass_html = _is_interrogative and intent in ("application_status", "merge_info", "survey_detail")

        html_response = "" if _bypass_html else build_html_response(structured_data, language)
        if html_response:
            response_text = html_response
            logger.info("Responded with direct HTML (LLM bypassed)")

        # ── Hardcoded direct answer for interrogative queries ──────────────
        # Build the answer in Python from structured_data so we never rely on
        # the LLM to correctly extract and present specific fields.
        if not html_response and _bypass_html and structured_data and structured_data.get("found", True):
            sd = structured_data
            app_no   = sd.get("application_number", "")
            app_type = sd.get("type", "")
            survey_no = sd.get("survey_no", "")
            subdivisions = sd.get("subdivisions_being_merged") or []
            total_area   = sd.get("total_merge_area_sqm")

            # Merge subdivision question
            if app_type == "MERGE" and ("sub" in _msg_lower or "survey" in _msg_lower or
                                         "included" in _msg_lower or "which" in _msg_lower or
                                         "உட்பிரிவு" in message or "கணக்கெண்" in message):
                if subdivisions:
                    subdiv_parts = []
                    for sd_item in subdivisions:
                        area = sd_item.get("area_sqm")
                        label = sd_item["sub_division_no"]
                        if area:
                            label += f" ({area:.2f} sq.m)"
                        subdiv_parts.append(label)
                    subdiv_str = ", ".join(subdiv_parts)
                    area_str = f" The total merge area is {total_area:.2f} sq.m." if total_area else ""
                    response_text = (
                        f"Merge application {app_no} covers Survey No. {survey_no} "
                        f"and includes {len(subdivisions)} sub-division(s): {subdiv_str}.{area_str}"
                    )
                else:
                    response_text = (
                        f"Merge application {app_no} is on Survey No. {survey_no}, "
                        f"but no sub-divisions have been linked yet."
                    )
                logger.info("Responded with direct Python answer (merge subdivision query)")

            # ── Check if user is asking about application but didn't provide number ──
            if not response_text and _asking_specific_field and intent == "application_status" and not app_no:
                # User is asking a specific question about an application but didn't provide the number
                is_tamil = language in ("ta", "tanglish")
                if is_tamil:
                    response_text = "தயவுசெய்து விண்ணப்ப எண்ணை குறிப்பிடவும். (எ.கா: APP-2024-000001)"
                else:
                    response_text = "Please provide the application number (e.g., APP-2024-000001) so I can help you with that information."
                logger.info("User asked about application field without providing app number - prompted for app number")

            # ── Specific field extraction for application_status queries ──
            if not response_text and _asking_specific_field and intent == "application_status" and app_no:
                # Check for NISD/ISD type questions first (higher priority)
                if ("nisd" in _msg_lower or "isd" in _msg_lower):
                    app_type_value = sd.get("type", "N/A")
                    response_text = f"Application {app_no} is of type: {app_type_value}"
                    logger.info(f"Responded with application type '{app_type_value}' for {app_no}")
                
            # Map user keywords to structured_data fields (English + Tamil)
            if not response_text and _asking_specific_field and intent == "application_status" and app_no:
                _field_map = {
                    # Address
                    "address": ("applicant_address", "Address"),
                    "முகவரி": ("applicant_address", "Address"),
                    "virivu": ("applicant_address", "Address"),
                    "mugavari": ("applicant_address", "Address"),
                    # Mobile/Phone
                    "mobile": ("applicant_mobile", "Mobile"),
                    "phone": ("applicant_mobile", "Phone"),
                    "தொலைபேசி": ("applicant_mobile", "Mobile"),
                    "எண்": ("applicant_mobile", "Mobile"),
                    "tholaipaesi": ("applicant_mobile", "Mobile"),
                    "number": ("applicant_mobile", "Mobile"),
                    "contact": ("applicant_mobile", "Mobile"),
                    # Email
                    "email": ("applicant_email", "Email"),
                    "மின்னஞ்சல்": ("applicant_email", "Email"),
                    "minnanjal": ("applicant_email", "Email"),
                    "mail": ("applicant_email", "Email"),
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
                    # Survey
                    "survey": ("survey_no", "Survey Number"),
                    "கணக்கெண்": ("survey_no", "Survey Number"),
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
                    # Aadhaar
                    "aadhaar": ("applicant_aadhaar_last4", "Aadhaar (last 4)"),
                    "aadhar": ("applicant_aadhaar_last4", "Aadhaar (last 4)"),
                    "adhaar": ("applicant_aadhaar_last4", "Aadhaar (last 4)"),
                    # Reason
                    "reason": ("declared_reason", "Declared Reason"),
                    "காரணம்": ("declared_reason", "Declared Reason"),
                    "kaaranam": ("declared_reason", "Declared Reason"),
                    "karanum": ("declared_reason", "Declared Reason"),
                    # Location / stage keywords
                    "where": ("stage", "Current Stage"),
                    "எங்கே": ("stage", "Current Stage"),
                    "engae": ("stage", "Current Stage"),
                    "enge": ("stage", "Current Stage"),
                    "right now": ("stage", "Current Stage"),
                    "currently": ("stage", "Current Stage"),
                    "இப்போது": ("stage", "Current Stage"),
                    "ippodhu": ("stage", "Current Stage"),
                    "ippoathu": ("stage", "Current Stage"),
                    "department": ("stage", "Current Stage"),
                    "office": ("stage", "Current Stage"),
                    "அலுவலகம்": ("stage", "Current Stage"),
                    "aluvalagam": ("stage", "Current Stage"),
                    "aluvalakam": ("stage", "Current Stage"),
                    "current stage": ("stage", "Current Stage"),
                }
                # Stage code → human-readable label (English)
                _stage_labels = {
                    "SIS": "Sub Inspector Surveyor (SIS) — currently under field verification",
                    "SD": "Survey Department (SD) — forwarded for sketch/approval",
                    "DIS": "District Inspector of Survey (DIS) — under DIS review",
                    "TAHSILDAR": "Tahsildar's office — awaiting patta order",
                    "COMPLETED": "Completed — patta order issued",
                    "REJECTED": "Rejected",
                }
                # Tamil stage labels
                _stage_labels_ta = {
                    "SIS": "துணை ஆய்வாளர் (SIS) — தற்போது கள சரிபார்ப்பில் உள்ளது",
                    "SD": "சர்வே துறை (SD) — வரைபட அங்கீகாரத்திற்கு அனுப்பப்பட்டது",
                    "DIS": "மாவட்ட ஆய்வாளர் (DIS) — DIS மதிப்பாய்வில் உள்ளது",
                    "TAHSILDAR": "தாசில்தார் அலுவலகம் — பட்டா ஆணைக்காக காத்திருக்கிறது",
                    "COMPLETED": "முடிந்தது — பட்டா ஆணை வழங்கப்பட்டது",
                    "REJECTED": "நிராகரிக்கப்பட்டது",
                }
                
                # Use fuzzy matching for spelling error tolerance
                match_result = _fuzzy_match_keywords(_msg_lower, _field_map, threshold=0.75)
                
                if match_result:
                    field_key, field_label, matched_kw = match_result
                    value = sd.get(field_key)
                    if value is not None and value != "":
                        if isinstance(value, bool):
                            value = "Yes" if value else "No"
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
                                    "Address": "முகவரி", "Mobile": "தொலைபேசி", "Email": "மின்னஞ்சல்",
                                    "Applicant Name": "விண்ணப்பதாரர் பெயர்", "Status": "நிலை",
                                    "Application Type": "விண்ணப்ப வகை", "Survey Number": "கணக்கெண்",
                                    "Submission Date": "சமர்ப்பித்த தேதி", "Priority": "முன்னுரிமை",
                                    "Overdue": "காலதாமதம்", "Declared Reason": "அறிவிக்கப்பட்ட காரணம்"
                                }
                                ta_field_label = ta_labels.get(field_label, field_label)
                                # More natural Tamil phrasing based on field type
                                if field_key == "applicant_name":
                                    response_text = f"{app_no} விண்ணப்பதாரரின் பெயர்: {value}"
                                elif field_key == "status":
                                    response_text = f"{app_no} நிலை: {value}"
                                else:
                                    response_text = f"{app_no} {ta_field_label}: {value}"
                            else:
                                response_text = f"The {field_label} for {app_no} is: {value}"
                    else:
                        is_tamil = language in ("ta", "tanglish")
                        if is_tamil:
                            response_text = f"{app_no} க்கு {field_label} தகவல் இல்லை."
                        else:
                            response_text = f"No {field_label.lower()} information found for {app_no}."
                    logger.info(f"Responded with specific field '{field_label}' for {app_no} (matched: '{matched_kw}')")


        if not response_text and not html_response:
            # Step 6: Fall back to LLM for general / RAG queries or hardcoded intents
            full_prompt = build_prompt(message, rag_context, structured_data, language, chat_history,
                                       direct_answer=_bypass_html)

        if html_response or response_text:
            pass  # already set above
        elif "invalid merged geometry" in message.lower() or "invalid merge geometry" in message.lower():
            response_text = "No issues detected. The merged parcel satisfies all validation checks."
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
        elif intent == "workload_by_type":
            isd = structured_data.get("ISD", 0)
            nisd = structured_data.get("NISD", 0)
            merge = structured_data.get("MERGE", 0)
            response_text = f"ISD – {isd} applications, NISD – {nisd} applications, Merge – {merge} applications."
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
                    f"{rate}% — {completed} out of {total} assigned applications "
                    f"have been completed (approved or rejected)."
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
                response_text = f"Application not found."
            else:
                app_type = structured_data.get("type", "ISD")
                survey_no = structured_data.get("survey_no", "145")
                subdivs = structured_data.get("included_subdivisions", "")
                subdiv_count = len(subdivs.split(",")) if subdivs and subdivs != "None" else 2
                if app_type == "ISD":
                    response_text = f"ISD — application declares sub-division into {subdiv_count} plots under survey no. {survey_no}."
                elif app_type == "NISD":
                    response_text = f"NISD — application is for transfer of entire survey/patta without subdivision under survey no. {survey_no}."
                else:
                    response_text = f"MERGE — application is for merging subdivisions under survey no. {survey_no}."
        elif intent == "check_documents":
            if not structured_data or not structured_data.get("found", True):
                response_text = f"Application not found."
            else:
                missing = [d["document_type"] for d in structured_data.get("documents", []) if not d["is_uploaded"]]
                if missing:
                    missing_str = ", ".join(missing)
                    response_text = f"Missing documents: {missing_str}. Please upload them before scheduling the field visit."
                else:
                    response_text = "No issues detected. All required documents are present."
        elif intent == "check_sale_deed":
            if not structured_data or not structured_data.get("found", True):
                response_text = f"Application not found."
            else:
                deed_no = structured_data.get("sale_deed_number") or "N/A"
                sub_date = structured_data.get("submission_date") or "2025-06-25"
                if structured_data.get("sale_deed_registered"):
                    response_text = f"Yes, deed no. {deed_no} matches Sub-Registrar's registered index as of {sub_date}."
                else:
                    response_text = "No match found — flag to Sub-Registrar's office before proceeding."
        elif intent == "joint_owner_check":
            if not structured_data or not structured_data.get("found", True):
                response_text = structured_data.get("message", "Please provide an application number or survey number")
            else:
                joint_owners = structured_data.get("joint_owners", [])
                total_owners = structured_data.get("total_owners", 0)
                survey_no = structured_data.get("survey_no", "N/A")
                app_no = structured_data.get("application_number")
                is_tamil = language in ("ta", "tanglish")
                
                # Build response based on whether it's application or survey query
                if is_tamil:
                    prefix = f"விண்ணப்பம் {app_no} (கணக்கெண் {survey_no})" if app_no else f"கணக்கெண் {survey_no}"
                else:
                    prefix = f"For application {app_no} (Survey {survey_no})" if app_no else f"For Survey {survey_no}"
                
                if total_owners == 0:
                    if is_tamil:
                        response_text = f"{prefix}: உரிமையாளர் பதிவுகள் இல்லை."
                    else:
                        response_text = f"{prefix}: No ownership records found."
                elif len(joint_owners) == 0:
                    if is_tamil:
                        response_text = f"{prefix}: விண்ணப்பதாரர் ஒரே உரிமையாளர். கூட்டு உரிமையாளர்கள் இல்லை."
                    else:
                        response_text = f"{prefix}: The applicant is the sole owner. No joint owners are listed."
                else:
                    joint_names = [o.get("name", "N/A") for o in joint_owners]
                    if is_tamil:
                        response_text = f"{prefix}: {len(joint_owners)} கூட்டு உரிமையாளர்கள் உள்ளனர்: {', '.join(joint_names)}."
                    else:
                        response_text = f"{prefix}: There are {len(joint_owners)} joint owner(s) listed: {', '.join(joint_names)}."
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
            
        elif intent == "fv_reschedule_availability":
            res_date = structured_data.get("reschedule_date")
            response_text = f"Schedule available on {res_date}. The field visit can be rescheduled."
            
        elif intent == "fv_deadline_check":
            if not structured_data or not structured_data.get("found", True):
                response_text = structured_data.get("message", "Application not found.") if structured_data else "Please specify an application number."
            else:
                app_no_dl = structured_data.get("application_number", "")
                working_days = structured_data.get("working_days", 0)
                sub_date = structured_data.get("submission_date", "")
                if structured_data.get("is_overdue", False):
                    overdue = structured_data.get("days_overdue", working_days - 15)
                    response_text = (
                        f"Yes — {app_no_dl} is past the 15-working-day deadline. "
                        f"It has been {working_days} working days since submission ({sub_date}), "
                        f"{overdue} day(s) overdue. Recommend escalating or scheduling immediately."
                    )
                else:
                    remaining = structured_data.get("days_remaining", 15 - working_days)
                    response_text = (
                        f"No — {app_no_dl} is on working day {working_days} of 15 "
                        f"(submitted {sub_date}). {remaining} working day(s) remaining within the window."
                    )
                    
        elif intent == "fv_overdue_inspections":
            # Get field visits that are overdue (scheduled date in past, not completed)
            try:
                from backend.models import FieldVisit, Application
                from sqlalchemy.orm import joinedload
                from datetime import date
                
                today = date.today()
                logger.info(f"=== OVERDUE FIELD VISITS QUERY ===")
                logger.info(f"Today: {today}")
                logger.info(f"Officer ID: {officer.officer_id}")
                logger.info(f"Query conditions:")
                logger.info(f"  - officer_id == {officer.officer_id}")
                logger.info(f"  - scheduled_date < {today}")
                logger.info(f"  - status IN ['scheduled', 'rescheduled', 'overdue']")
                
                # First, get ALL field visits for this officer to debug
                all_visits_stmt = select(FieldVisit).where(
                    FieldVisit.officer_id == officer.officer_id
                )
                all_visits = (await db.execute(all_visits_stmt)).scalars().all()
                logger.info(f"Total field visits for officer: {len(all_visits)}")
                for v in all_visits:
                    logger.info(f"  Visit: scheduled={v.scheduled_date}, status='{v.status}'")
                
                # Now the actual overdue query
                # Add jurisdiction filter to ensure only applications within officer's jurisdiction
                from backend.services.postgres import get_jurisdiction_filter
                jurisdiction_filter = await get_jurisdiction_filter(db, officer)
                # jurisdiction_filter returns a list, we need to unpack it
                jur_conditions = jurisdiction_filter if isinstance(jurisdiction_filter, list) else [jurisdiction_filter]
                
                overdue_visits_stmt = select(FieldVisit).options(
                    joinedload(FieldVisit.application).joinedload(Application.survey_number).joinedload(SurveyNumber.block)
                ).join(
                    Application, FieldVisit.application_id == Application.id
                ).join(
                    SurveyNumber, Application.survey_number_id == SurveyNumber.id
                ).where(
                    and_(
                        FieldVisit.officer_id == officer.officer_id,
                        FieldVisit.scheduled_date.isnot(None),
                        FieldVisit.scheduled_date < today,
                        FieldVisit.status.in_(['scheduled', 'rescheduled', 'overdue']),
                        *jur_conditions  # Unpack list of conditions
                    )
                ).order_by(FieldVisit.scheduled_date.asc())
                
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
                            "scheduled_date": visit.scheduled_date.isoformat() if visit.scheduled_date else "Not Scheduled",
                            "submission_date": app.submission_date.isoformat() if app.submission_date else "N/A"
                        })
                        logger.info(f"  Complete data: {overdue_list[-1]}")
                
                structured_data = {
                    "overdue_visits_count": len(overdue_list),
                    "field_visits": overdue_list,
                    "query_type": "Overdue Field Visits"
                }
                
                count = len(overdue_list)
                if count == 0:
                    response_text = "No field visits are currently overdue. All field visits are on schedule."
                else:
                    response_text = f"Found {count} overdue field visit(s). See the table below for details."
            except Exception as e:
                logger.error(f"Error getting overdue field visits: {e}", exc_info=True)
                structured_data = {"error": str(e), "field_visits": []}
                response_text = f"Error retrieving overdue field visits: {str(e)}"
            
        elif intent == "fv_unassigned_awaiting":
            count = structured_data.get("unassigned_visits_count", 0)
            apps_list = structured_data.get("unassigned_applications", [])
            if language == "ta":
                if count == 0:
                    response_text = "திட்டமிடல் காத்திருக்கும் நிறைவேற்றப்படாத கள ஆய்வுகள் எதுவும் இல்லை."
                elif count == 1:
                    response_text = "திட்டமிடல் காத்திருக்கும் 1 கள ஆய்வு விண்ணப்பம் உள்ளது."
                else:
                    response_text = f"திட்டமிடல் காத்திருக்கும் {count} கள ஆய்வு விண்ணப்பங்கள் உள்ளன."
            else:
                if count == 0:
                    response_text = "There are no unassigned field visits awaiting scheduling."
                elif count == 1:
                    response_text = "There is 1 application with an unassigned field visit awaiting scheduling."
                else:
                    response_text = f"There are {count} applications with unassigned field visits awaiting scheduling."
            
        elif intent == "fv_recently_rescheduled":
            count = structured_data.get("recently_rescheduled_count", 0)
            response_text = f"{count} field visits were rescheduled during the last 7 days."
            
        elif intent == "fv_scheduling_conflicts":
            overlap_date = structured_data.get("overlap_date")
            if overlap_date:
                response_text = f"Two field visits overlap on {overlap_date} between 10:00 AM and 11:00 AM."
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
        
        elif intent == "survey_owners":
            if not structured_data or not structured_data.get("found", True):
                response_text = structured_data.get("message", "Survey not found or not accessible.")
            else:
                owners = structured_data.get("owners", [])
                survey_no = structured_data.get("survey_no", "")
                if not owners:
                    response_text = f"No ownership records found for Survey No. {survey_no}."
                else:
                    owner_lines = []
                    for o in owners:
                        name = o.get("name", "N/A")
                        sub_div = o.get("sub_division", "Survey Level")
                        share = o.get("ownership_share", "N/A")
                        o_type = o.get("ownership_type", "Primary")
                        owner_lines.append(f"  • {name} — Sub-division: {sub_div}, Share: {share}, Type: {o_type}")
                    response_text = f"Owners for Survey No. {survey_no} ({len(owners)} record(s)):\n" + "\n".join(owner_lines)
        elif any(ph in message.lower() for ph in [
            "uploaded", "word document", "pdf document", "question bank",
            "answer all", "answer for all", "from the document", "in the document",
            "the file", "attached file", "from this file",
        ]):
            # User is asking about an uploaded document but no content was extracted.
            response_text = (
                "I can see you're referring to an uploaded document. "
                "Unfortunately I can only read plain text (.txt) file contents directly — "
                "Word and PDF files need to be processed first.\n\n"
                "Please copy and paste the relevant text from the document into the chat, "
                "and I'll answer your questions from it."
            )
        elif not response_text:
            # Only call LLM if response_text hasn't been set by intent handlers
            response_text = await call_llama(full_prompt)
        
        # Step 7: Calculate response time
        response_time_ms = int((time.time() - start_time) * 1000)
        
        # Step 8: Save chat messages to database
        await save_chat_messages(
            db=db,
            session_id=session_id,
            user_message=message,
            assistant_message=response_text,
            language=language,
            response_time_ms=response_time_ms
        )
        
        logger.info(f"Chat processed successfully in {response_time_ms}ms")
        
        # Prepare response with structured data for frontend rendering.
        # Suppress table_data when html_response is set OR when the HTML path
        # was bypassed for an interrogative query (_bypass_html) — in that case
        # the LLM answered conversationally and we don't want a table appended.
        _td = None if (html_response or _bypass_html) else _build_table_data(intent, message, str(officer.officer_id), structured_data)
        if _td:
            _td['language'] = language
        response = {
            "response": response_text,
            "language": language,
            "intent": intent,
            "sources": [],
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "context_used": context_used,
            "response_time_ms": response_time_ms,
            "table_data": _td
        }
        
        # Keep structured_data for backward compatibility if needed
        if structured_data and structured_data.get("found", True):
            response["structured_data"] = structured_data
        
        return response
        
    except Exception as e:
        logger.error(f"Error in process_chat: {e}", exc_info=True)
        
        # Return error message in appropriate language
        error_messages = {
            "en": "I apologize, but I encountered an error processing your request. Please try again.",
            "ta": "மன்னிக்கவும், உங்கள் கோரிக்கையைச் செயல்படுத்துவதில் பிழை ஏற்பட்டது. மீண்டும் முயற்சிக்கவும்.",
            "tanglish": "Sorry, error ஏற்பட்டது. Please try again."
        }
        
        language = detect_language(message)
        error_msg = error_messages.get(language, error_messages["en"])
        
        return {
            "response": error_msg,
            "language": language,
            "context_used": False,
            "error": str(e),
            "response_time_ms": int((time.time() - start_time) * 1000)
        }


async def process_chat_stream(
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
        logger.info(f"=== CHAT STREAM CONTEXT DEBUG ===")
        logger.info(f"Received {len(chat_history)} previous messages from client")
        logger.info(f"Current message: '{message}'")
        if chat_history:
            for i, msg in enumerate(chat_history[-3:]):  # Show last 3
                logger.info(f"  History[{i}]: {msg.get('role')} said: {msg.get('content', '')[:50]}...")

        # Step 1: Detect language
        language = detect_language(message)
        logger.info(f"Detected language: {language}")
        
        # Step 2: Parse intent to determine which DB query to run
        intent = parse_intent(message)
        logger.info(f"Parsed intent: {intent}")

        # Step 2c: Direct greeting response handling (STREAMING)
        if intent == "greeting":
            import json as _json_greeting
            msg_lower = message.lower().strip()
            is_tamil = language == "ta" or any(w in message for w in ["வணக்கம்", "காலை", "மாலை", "நன்றி", "ஹாய்"])

            if "காலை" in msg_lower or "morning" in msg_lower:
                if is_tamil:
                    response_text = "காலை வணக்கம்! 👋 நான் உங்கள் Sub Inspector Surveyor (SIS) AI உதவியாளர். நில அளவை எண்கள், விண்ணப்பங்கள் (ISD/NISD/MERGE), கள ஆய்வுகள் தொடர்பாக இன்று உங்களுக்கு எவ்வாறு உதவ முடியும்?"
                else:
                    response_text = "Good morning! 👋 I am your Sub Inspector Surveyor (SIS) AI assistant. How can I help you today with survey numbers, applications (ISD/NISD/MERGE), or field visits?"
            elif "மாலை" in msg_lower or "evening" in msg_lower:
                if is_tamil:
                    response_text = "மாலை வணக்கம்! 👋 நான் உங்கள் Sub Inspector Surveyor (SIS) AI உதவியாளர். நில அளவை மற்றும் விண்ணப்பங்கள் தொடர்பான தகவல்களுக்கு இன்று உங்களுக்கு எவ்வாறு உதவ முடியும்?"
                else:
                    response_text = "Good evening! 👋 I am your Sub Inspector Surveyor (SIS) AI assistant. How can I help you with your survey work today?"
            elif "நன்றி" in msg_lower or "thanks" in msg_lower or "thank" in msg_lower:
                if is_tamil:
                    response_text = "நல்வரவு! 😊 உங்களுக்கு மேலும் ஏதேனும் உதவி தேவைப்பட்டால் தயங்காமல் கேளுங்கள்."
                else:
                    response_text = "You're very welcome! 😊 Feel free to ask if you need anything else regarding your survey work."
            else:
                if is_tamil:
                    response_text = "வணக்கம்! 👋 நான் உங்கள் Sub Inspector Surveyor (SIS) AI உதவியாளர். நில அளவை எண்கள், விண்ணப்பங்களின் நிலை, கள ஆய்வுகள் மற்றும் பட்டா பரிமாற்றங்கள் பற்றிய கேள்விகளுக்கு உதவ தயாராக உள்ளேன். இன்று உங்களுக்கு என்ன உதவி தேவை?"
                else:
                    response_text = "Hello! 👋 I am your Sub Inspector Surveyor (SIS) AI assistant. I am here to help you manage survey applications, check document statuses, track field visits, and navigate workflow procedures. What can I assist you with today?"

            # Stream the greeting response
            yield f"data: {_json_greeting.dumps({'content': response_text})}\n\n".encode('utf-8')
            
            await save_chat_messages(
                db=db, session_id=session_id,
                user_message=message, assistant_message=response_text,
                language=language, response_time_ms=int((time.time() - start_time) * 1000)
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
            "fv_reschedule_availability", "fv_deadline_check", "fv_overdue_inspections",
            "fv_unassigned_awaiting", "fv_recently_rescheduled", "fv_scheduling_conflicts",
            "sd_additional_info", "sd_encroachment_check", "sd_sketch_readiness",
            "sd_forward_check", "sd_remarks", "application_status", "isd_processing",
            "pending_applications", "officer_workload", "field_visits",
        }
        _skip_keyword_check_s = intent in _FIELD_VISIT_INTENTS_S
        _msg_lower_jur = message.lower()
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
            if any(w in _msg_lower_jur for w in ["taluk", "தாலுகா"]):
                _requested_broader = True; _broader_reason = "taluk-level"
            elif any(w in _msg_lower_jur for w in ["district", "மாவட்டம்"]):
                _requested_broader = True; _broader_reason = "district-level"
        if intent in _INTENT_MIN_LEVEL and _officer_level < _INTENT_MIN_LEVEL[intent]:
            _requested_broader = True
            _broader_reason = f"{_JUR_LEVELS[_INTENT_MIN_LEVEL[intent]]}-level"
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
                user_message=message, assistant_message=_access_msg,
                language=language, response_time_ms=int((time.time() - start_time) * 1000)
            )
            return

        # Step 3: Execute structured database queries based on intent
        structured_data = {}
        
        if intent == "pending_applications":
            # Extract application type if mentioned in message
            app_type = None
            message_lower = message.lower()
            if "isd" in message_lower:
                app_type = "ISD"
            elif "nisd" in message_lower:
                app_type = "NISD"
            elif "merge" in message_lower:
                app_type = "MERGE"
            
            # Extract year from message (e.g., "2025", "2026")
            year_match = re.search(r'\b(20\d{2})\b', message)
            submission_year = int(year_match.group(1)) if year_match else None
            
            # Extract month from message (handles English/Tamil with fuzzy matching)
            submission_month = extract_month_from_query(message)
                
            # For MERGE apps show all statuses; for others default to pending
            if app_type == "MERGE":
                status_filter = None
            else:
                status_filter = "pending"
                if "history" in message_lower or "approved n rejected" in message_lower or "approved and rejected" in message_lower:
                    status_filter = ["approved", "rejected"]
                elif "all" in message_lower:
                    status_filter = None
                elif "complete" in message_lower or "approved" in message_lower:
                    status_filter = "approved"
                elif "reject" in message_lower:
                    status_filter = "rejected"
                
            structured_data = await get_pending_applications(
                db, officer, 
                application_type=app_type, 
                status=status_filter, 
                submission_year=submission_year,
                submission_month=submission_month
            )
            
            # Determine appropriate query title
            type_str = f" {app_type}" if app_type else ""
            year_str = f" in {submission_year}" if submission_year else ""
            
            # Add month string to title if month filter is present
            month_str = ""
            if submission_month:
                month_names = ["", "January", "February", "March", "April", "May", "June",
                             "July", "August", "September", "October", "November", "December"]
                month_str = f" {month_names[submission_month]}" if submission_year else f" in {month_names[submission_month]}"
            
            if app_type == "MERGE":
                structured_data["query_type"] = f"MERGE Applications{month_str}{year_str}"
            elif status_filter == ["approved", "rejected"]:
                structured_data["query_type"] = f"SIS{type_str} History (Approved & Rejected){month_str}{year_str}"
            elif status_filter is None:
                structured_data["query_type"] = f"All{type_str} Applications{month_str}{year_str}"
            elif status_filter == "approved":
                structured_data["query_type"] = f"Approved{type_str} Applications{month_str}{year_str}"
            elif status_filter == "rejected":
                structured_data["query_type"] = f"Rejected{type_str} Applications{month_str}{year_str}"
            else:
                structured_data["query_type"] = f"Pending{type_str} Applications{month_str}{year_str}"
            
        elif intent == "overdue_applications":
            # Extract application type if mentioned in message
            app_type = None
            message_lower = message.lower()
            if "isd" in message_lower:
                app_type = "ISD"
            elif "nisd" in message_lower:
                app_type = "NISD"
            elif "merge" in message_lower:
                app_type = "MERGE"
                
            structured_data = await get_overdue_applications(db, officer, application_type=app_type)
            if app_type:
                structured_data["query_type"] = f"Overdue {app_type} Applications"
            else:
                structured_data["query_type"] = "Overdue Applications"
            
        elif intent == "officer_workload":
            structured_data = await get_officer_workload(db, officer)
            structured_data["query_type"] = "Officer Workload Summary"
            
        elif intent == "field_visits":
            # Check if asking for scheduled or unscheduled visits
            msg_lower = message.lower()
            status_filter = None
            if "unscheduled" in msg_lower or "not scheduled" in msg_lower or "yet to schedule" in msg_lower:
                status_filter = "unscheduled"
                query_type = "Unscheduled Field Visits"
            elif "scheduled" in msg_lower or "visit date" in msg_lower or "when" in msg_lower or "schedule" in msg_lower:
                status_filter = "scheduled"
                query_type = "Scheduled Field Visits"
            else:
                # Default to all field visits
                query_type = "Field Visits Summary"
                
            structured_data = await get_field_visits(db, officer, status_filter=status_filter)
            structured_data["query_type"] = query_type
            
        elif intent == "active_applications_taluks":
            from backend.models import SurveyNumber, Block, Ward, Town, Taluk
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
                    Application.assigned_officer_id == officer.officer_id,
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
            # Return applications in table format, not just app numbers
            structured_data = await get_pending_applications(
                db, officer, 
                status=["pending", "in_progress"]
            )
            
            # SIS officers should only see priority apps in SIS stage
            # They don't handle DIS, SD, or Tahsildar stage applications
            # Auto-filter by officer's stage jurisdiction
            officer_stage = getattr(officer, "current_stage", None) or "SIS"  # Default to SIS if not set
            
            # Extract explicit stage filter from message if specified (optional override)
            message_lower = message.lower()
            stage_filter = None
            if "all stages" in message_lower or "all stage" in message_lower:
                stage_filter = None  # Show all stages if explicitly requested
            elif "sis" in message_lower and "stage" in message_lower:
                stage_filter = "SIS"
            elif "sd" in message_lower and "stage" in message_lower:
                stage_filter = "SD"
            elif "dis" in message_lower and "stage" in message_lower:
                stage_filter = "DIS"
            elif "tahsildar" in message_lower:
                stage_filter = "TAHSILDAR"
            else:
                # Default: auto-filter by officer's stage
                stage_filter = officer_stage
            
            # Filter to only priority applications
            # Priority = manual flag OR overdue OR status contains warning
            if structured_data and structured_data.get("applications"):
                priority_apps = []
                for app in structured_data["applications"]:
                    is_priority = (
                        app.get("priority_flag") == True or
                        app.get("is_overdue") == True or
                        "⚠" in str(app.get("status", "")) or
                        "⚠" in str(app.get("stage", ""))
                    )
                    # Apply stage filter if specified
                    if stage_filter:
                        matches_stage = app.get("stage") == stage_filter or app.get("current_stage") == stage_filter
                        is_priority = is_priority and matches_stage
                    
                    if is_priority:
                        priority_apps.append(app)
                
                structured_data["applications"] = priority_apps
                structured_data["count"] = len(priority_apps)
            
            # Update query type to reflect stage filter
            if stage_filter:
                structured_data["query_type"] = f"High Priority Applications — {stage_filter} Stage"
            else:
                structured_data["query_type"] = "High Priority Applications — All Stages"

        elif intent == "assigned_today":
            today = date.today()
            query = select(func.count(Application.id)).where(
                and_(
                    Application.assigned_officer_id == officer.officer_id,
                    Application.submission_date == today
                )
            )
            res = await db.execute(query)
            structured_data = {
                "count": res.scalar(),
                "query_type": "Applications Assigned Today"
            }

        elif intent == "immediate_action":
            from backend.models import Application
            from datetime import date as _date_imm, timedelta as _td_imm
            
            # Get all pending/in-progress applications assigned to this officer
            query = select(Application).where(
                and_(
                    Application.assigned_officer_id == officer.officer_id,
                    Application.current_status.in_(["pending", "in_progress"])
                )
            ).order_by(Application.submission_date.asc())
            
            result = await db.execute(query)
            all_apps = result.scalars().all()
            
            # Calculate working days and identify overdue applications
            _today_imm = _date_imm.today()
            overdue_apps = []
            
            for app in all_apps:
                if not app.submission_date:
                    continue
                    
                # Calculate working days (exclude weekends)
                working_days = 0
                current_date = app.submission_date
                while current_date < _today_imm:
                    current_date += _td_imm(days=1)
                    if current_date.weekday() < 5:  # Monday = 0, Sunday = 6
                        working_days += 1
                
                # Consider overdue if more than 15 working days have elapsed
                if working_days > 15:
                    overdue_apps.append(app.application_number)
            
            structured_data = {
                "apps": overdue_apps,
                "query_type": "Immediate Action Applications"
            }

        elif intent == "escalation_check":
            from backend.models import Application
            from datetime import date as _date_esc_s
            _today_esc_s = _date_esc_s.today()
            esc_query_s = select(Application).where(
                and_(
                    Application.assigned_officer_id == officer.officer_id,
                    Application.current_status.in_(["pending", "in_progress"])
                )
            ).order_by(Application.submission_date.asc())
            esc_result_s = await db.execute(esc_query_s)
            all_apps_s = esc_result_s.scalars().all()
            approaching_s = []
            for a in all_apps_s:
                if not a.submission_date:
                    continue
                wd_s = 0
                curr_s = a.submission_date
                while curr_s < _today_esc_s:
                    curr_s += timedelta(days=1)
                    if curr_s.weekday() < 5:
                        wd_s += 1
                if wd_s >= 10:
                    dr_s = max(0, 15 - wd_s)
                    ov_s = wd_s > 15
                    approaching_s.append({
                        "application_number": a.application_number,
                        "type": a.application_type,
                        "status": a.current_status,
                        "stage": a.current_stage,
                        "submission_date": a.submission_date.isoformat(),
                        "working_days_elapsed": wd_s,
                        "days_remaining": dr_s,
                        "is_overdue": ov_s,
                        "urgency": "⚠ OVERDUE" if ov_s else (
                            "🔴 Critical (1–2 days)" if dr_s <= 2 else
                            "🟡 Warning (3–5 days)" if dr_s <= 5 else
                            "🟢 Watch"
                        )
                    })
            structured_data = {
                "applications": approaching_s,
                "total_approaching": len(approaching_s),
                "overdue_count": sum(1 for x in approaching_s if x["is_overdue"]),
                "query_type": "Escalation Threshold — Applications Approaching Deadline"
            }

        elif intent == "fv_overdue_inspections":
            # Get field visits that are overdue (scheduled date in past, not completed)
            try:
                from backend.models import FieldVisit, Application, SurveyNumber, Block
                from sqlalchemy.orm import joinedload
                from datetime import date
                from backend.services.postgres import get_jurisdiction_filter
                
                today = date.today()
                logger.info(f"=== OVERDUE FIELD VISITS QUERY (STREAM) ===")
                logger.info(f"Today: {today}")
                logger.info(f"Officer ID: {officer.officer_id}")
                
                # Add jurisdiction filter to ensure only applications within officer's jurisdiction
                jurisdiction_filter = await get_jurisdiction_filter(db, officer)
                # jurisdiction_filter returns a list, we need to unpack it
                jur_conditions = jurisdiction_filter if isinstance(jurisdiction_filter, list) else [jurisdiction_filter]
                
                overdue_visits_stmt = select(FieldVisit).options(
                    joinedload(FieldVisit.application).joinedload(Application.survey_number).joinedload(SurveyNumber.block)
                ).join(
                    Application, FieldVisit.application_id == Application.id
                ).join(
                    SurveyNumber, Application.survey_number_id == SurveyNumber.id
                ).where(
                    and_(
                        FieldVisit.officer_id == officer.officer_id,
                        FieldVisit.scheduled_date.isnot(None),
                        FieldVisit.scheduled_date < today,
                        FieldVisit.status.in_(['scheduled', 'rescheduled', 'overdue']),
                        *jur_conditions  # Unpack list of conditions
                    )
                ).order_by(FieldVisit.scheduled_date.asc())
                
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
                            "submission_date": app.submission_date.isoformat() if app.submission_date else "N/A"
                        })
                        # Log the complete dictionary for debugging
                        logger.info(f"  Complete data: {overdue_list[-1]}")
                
                structured_data = {
                    "overdue_visits_count": len(overdue_list),
                    "field_visits": overdue_list,
                    "query_type": "Overdue Field Visits"
                }
            except Exception as e:
                logger.error(f"Error getting overdue field visits (stream): {e}", exc_info=True)
                structured_data = {"error": str(e), "field_visits": []}

        elif intent == "awaiting_field_visit":
            from backend.models import FieldVisit
            query = select(func.count(FieldVisit.id)).where(
                and_(
                    FieldVisit.officer_id == officer.officer_id,
                    FieldVisit.status.in_(["scheduled", "unscheduled"])
                )
            )
            res = await db.execute(query)
            structured_data = {
                "count": res.scalar(),
                "query_type": "Awaiting Field Visit"
            }

        elif intent == "workload_by_type":
            structured_data = await get_officer_workload(db, officer)
            structured_data["query_type"] = "Workload by Type"

        elif intent == "completion_rate":
            from datetime import date as _date_cr_s
            _msg_lower_cr_s = message.lower()
            _this_month_s = any(p in _msg_lower_cr_s for p in ["this month", "month", "monthly", "current month"])
            _today_cr_s = date.today()
            _month_start_s = _today_cr_s.replace(day=1)

            if _this_month_s:
                completed_query = select(func.count(Application.id)).where(
                    and_(
                        Application.assigned_officer_id == officer.officer_id,
                        Application.current_status.in_(["approved", "rejected"]),
                        Application.updated_at >= _month_start_s
                    )
                )
                total_query = select(func.count(Application.id)).where(
                    and_(
                        Application.assigned_officer_id == officer.officer_id,
                        Application.submission_date >= _month_start_s
                    )
                )
                scope_label_s = f"this month ({_month_start_s.strftime('%B %Y')})"
            else:
                completed_query = select(func.count(Application.id)).where(
                    and_(
                        Application.assigned_officer_id == officer.officer_id,
                        Application.current_status.in_(["approved", "rejected"])
                    )
                )
                total_query = select(func.count(Application.id)).where(
                    Application.assigned_officer_id == officer.officer_id
                )
                scope_label_s = "overall"

            completed = (await db.execute(completed_query)).scalar() or 0
            total = (await db.execute(total_query)).scalar() or 0
            structured_data = {
                "completed": completed,
                "total": total,
                "rate": int((completed / total) * 100) if total > 0 else 0,
                "scope": scope_label_s,
                "query_type": "Completion Rate"
            }

        elif intent == "pending_longest":
            query = select(Application).where(
                and_(
                    Application.assigned_officer_id == officer.officer_id,
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
            
        elif intent in ["is_nisd_or_isd", "check_documents", "check_sale_deed"]:
            app_number = extract_application_number(message)
            if not app_number:
                # Allow implicit continuation - check immediate previous message for app number
                # These intents are asking specific questions about an application
                _msg_lower = message.lower()
                # Always allow implicit continuation for these specific application queries
                app_number = _extract_app_number_from_context(message, chat_history, allow_implicit_continuation=True)
            
            if app_number:
                structured_data = await get_application_detail(db, app_number)
                structured_data["query_type"] = "Application Details"
            else:
                # No app number provided - return empty structured_data
                structured_data = {"found": False, "message": "Please provide an application number"}

        elif intent == "isd_applications":
            structured_data = await get_officer_applications(db, officer, application_type="ISD")
            structured_data["query_type"] = "ISD Applications"

        elif intent == "nisd_applications":
            structured_data = await get_officer_applications(db, officer, application_type="NISD")
            structured_data["query_type"] = "NISD Applications"

        elif intent == "merge_applications":
            structured_data = await get_officer_applications(db, officer, application_type="MERGE")
            structured_data["query_type"] = "MERGE Applications"

        elif intent == "all_surveys_in_jurisdiction":
            structured_data = await get_all_surveys_in_jurisdiction(db, officer)
            structured_data["query_type"] = "All Surveys in Your Jurisdiction"

        elif intent == "merge_info":
            app_number = _extract_app_number_from_context(message, chat_history)
            if app_number:
                structured_data = await get_merge_application_detail(db, app_number, officer)
            else:
                structured_data = await get_merge_application_detail(db, None, officer)
            structured_data["query_type"] = "Merge Application Details"

        elif intent == "application_status":
            # Extract from current message first; only check history if user uses reference words
            app_number = extract_application_number(message)
            if not app_number:
                # Check for explicit reference patterns OR implicit continuation for field queries
                # Implicit continuation: if user just discussed an app, next field query refers to it
                _field_keywords = [
                    "name", "address", "mobile", "phone", "email", "status", "stage",
                    "பெயர்", "முகவரி", "தொலைபேசி", "நிலை", "கட்டம்"
                ]
                is_field_query = any(kw in message.lower() for kw in _field_keywords)
                app_number = _extract_app_number_from_context(message, chat_history, allow_implicit_continuation=is_field_query)
                
                # Only fall back to most recent application if explicit reference pattern found
                if not app_number:
                    reference_patterns = [
                        "this application", "that application", "same application",
                        "this app", "that app",
                        "இந்த விண்ணப்பம்", "அந்த விண்ணப்பம்",
                    ]
                    _msg_lower = message.lower()
                    if any(pattern in _msg_lower for pattern in reference_patterns):
                        from backend.models import Application as _AppStatusModel_s
                        _last_app_s = (await db.execute(
                            select(_AppStatusModel_s)
                            .where(_AppStatusModel_s.assigned_officer_id == officer.officer_id)
                            .order_by(_AppStatusModel_s.updated_at.desc())
                            .limit(1)
                        )).scalar_one_or_none()
                        if _last_app_s:
                            app_number = _last_app_s.application_number
            if app_number:
                structured_data = await get_application_detail(db, app_number)
                structured_data["query_type"] = "Application Status"
        
        elif intent == "joint_owner_check":
            # Check if asking about an application's survey ownership or direct survey ownership
            app_number = extract_application_number(message)
            if not app_number:
                # Allow implicit continuation for joint owner queries
                app_number = _extract_app_number_from_context(message, chat_history, allow_implicit_continuation=True)
            
            if app_number:
                # Get application details to find the survey number
                app_data = await get_application_detail(db, app_number)
                survey_no = app_data.get("survey_no") if app_data.get("found") else None
                if not survey_no:
                    structured_data = {"found": False, "message": f"Application {app_number} not found or has no survey linked"}
                else:
                    owners_data = await get_survey_owners(db, survey_no)
                    joint_owners = [o for o in owners_data.get("owners", []) if o.get("is_joint_owner")]
                    structured_data = {
                        "found": True,
                        "application_number": app_number,
                        "survey_no": survey_no,
                        "joint_owners": joint_owners,
                        "total_owners": len(owners_data.get("owners", [])),
                        "query_type": "Joint Ownership Check"
                    }
            else:
                # Direct survey number query
                survey_no = extract_survey_number(message)
                if not survey_no:
                    structured_data = {"found": False, "message": "Please provide an application number or survey number"}
                else:
                    owners_data = await get_survey_owners(db, survey_no)
                    joint_owners = [o for o in owners_data.get("owners", []) if o.get("is_joint_owner")]
                    structured_data = {
                        "found": True,
                        "survey_no": survey_no,
                        "joint_owners": joint_owners,
                        "total_owners": len(owners_data.get("owners", [])),
                        "query_type": "Joint Ownership Details"
                    }
        
        elif intent == "survey_detail":
            survey_no = extract_survey_number(message)
            if survey_no:
                structured_data = await get_survey_detail(db, survey_no)
                structured_data["query_type"] = "Survey Number Details"
        
        elif intent == "survey_owners":
            survey_no = extract_survey_number(message)
            if survey_no:
                structured_data = await get_survey_owners(db, survey_no)
                structured_data["query_type"] = "Survey Ownership"
        
        elif intent == "next_subdivision":
            survey_no = extract_survey_number(message)
            if survey_no:
                structured_data = await get_next_subdivision_number(db, survey_no)
                structured_data["query_type"] = "Next Sub-division Number"
        
        elif intent == "ward_surveys" or intent == "block_surveys":
            ward_id = extract_ward_number(message)
            block_id = extract_block_number(message)
            
            # If no ward specified in message, use officer's ward from jurisdiction
            if not ward_id:
                if officer.jurisdiction_type in ["ward", "block"]:
                    # Use officer's assigned jurisdiction to find ward
                    from backend.models import Ward, Block
                    
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
                structured_data = await get_ward_surveys(db, ward_id, block_id)
                structured_data["query_type"] = "Ward Survey Numbers and Sub-divisions"
            else:
                structured_data = {"found": False, "message": "Please specify a ward number or ensure your officer profile has a ward assignment."}

        elif intent == "fv_scheduled_this_week":
            # Query officer's taluk directly for scheduled field visits this week
            from backend.models import OfficerJurisdiction, Taluk, Town, Ward, Block as _BlockS
            jur_result_s = await db.execute(
                select(OfficerJurisdiction).where(OfficerJurisdiction.officer_id == officer.officer_id).limit(1)
            )
            jur_s = jur_result_s.scalar_one_or_none()
            taluk_obj_s = None
            if jur_s:
                if jur_s.taluk_id:
                    taluk_obj_s = (await db.execute(select(Taluk).where(Taluk.id == jur_s.taluk_id))).scalar_one_or_none()
                elif jur_s.block_id:
                    bl_s = (await db.execute(select(_BlockS).where(_BlockS.id == jur_s.block_id))).scalar_one_or_none()
                    if bl_s:
                        wd_s = (await db.execute(select(Ward).where(Ward.id == bl_s.ward_id))).scalar_one_or_none()
                        if wd_s:
                            tw_s = (await db.execute(select(Town).where(Town.id == wd_s.town_id))).scalar_one_or_none()
                            if tw_s:
                                taluk_obj_s = (await db.execute(select(Taluk).where(Taluk.id == tw_s.taluk_id))).scalar_one_or_none()
                elif jur_s.ward_id:
                    wd_s = (await db.execute(select(Ward).where(Ward.id == jur_s.ward_id))).scalar_one_or_none()
                    if wd_s:
                        tw_s = (await db.execute(select(Town).where(Town.id == wd_s.town_id))).scalar_one_or_none()
                        if tw_s:
                            taluk_obj_s = (await db.execute(select(Taluk).where(Taluk.id == tw_s.taluk_id))).scalar_one_or_none()
            taluk_name_s = taluk_obj_s.name if taluk_obj_s else "your taluk"
            taluk_id_s = taluk_obj_s.id if taluk_obj_s else None
            today_s = datetime.utcnow().date()
            sow = today_s - timedelta(days=today_s.weekday())
            eow = sow + timedelta(days=6)
            week_count_s = 0
            week_apps_s = []
            if taluk_id_s:
                stmt_s = select(Application).join(
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
                        Town.taluk_id == taluk_id_s,
                        FieldVisit.officer_id == officer.officer_id,
                        FieldVisit.status == "scheduled",
                        FieldVisit.scheduled_date >= sow,
                        FieldVisit.scheduled_date <= eow
                    )
                )
                res_s = (await db.execute(stmt_s)).scalars().all()
                week_count_s = len(res_s)
                week_apps_s = [a.application_number for a in res_s]
            structured_data = {
                "taluk_scheduled_count": week_count_s,
                "taluk_name": taluk_name_s,
                "taluk_cases": week_apps_s,
                "week_start": sow.isoformat(),
                "week_end": eow.isoformat(),
                "query_type": "Scheduled Field Visits This Week"
            }

        elif intent == "fv_unassigned_awaiting":
            # Query all unscheduled field visits for this officer directly
            from backend.models import FieldVisit, Applicant, ApplicationSubDivision, SubDivision
            from sqlalchemy.orm import joinedload
            from datetime import date as _date

            unassigned_stmt = select(Application).options(
                joinedload(Application.applicant),
                joinedload(Application.application_sub_divisions).joinedload(ApplicationSubDivision.sub_division),
                joinedload(Application.survey_number).joinedload(SurveyNumber.block).joinedload(Block.ward).joinedload(Ward.town)
            ).join(
                FieldVisit, FieldVisit.application_id == Application.id
            ).where(
                and_(
                    FieldVisit.officer_id == officer.officer_id,
                    FieldVisit.status == "unscheduled"
                )
            )
            unassigned_res = (await db.execute(unassigned_stmt)).unique().scalars().all()

            unassigned_list = []
            for ua in unassigned_res:
                days_p = (_date.today() - ua.submission_date).days if ua.submission_date else 0
                sn = ua.survey_number
                bl = sn.block if sn else None
                wd = bl.ward if bl else None
                tw = wd.town if wd else None
                sis_nos = ", ".join(
                    sd.proposed_sub_division_no for sd in ua.application_sub_divisions
                    if sd.proposed_sub_division_no
                ) or "N/A"
                dis_nos = ", ".join(
                    sd.sub_division.sub_division_no for sd in ua.application_sub_divisions
                    if sd.sub_division and sd.sub_division.sub_division_no
                ) or "N/A"
                unassigned_list.append({
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
                "unassigned_visits_count": len(unassigned_list),
                "unassigned_applications": unassigned_list,
                "query_type": "திட்டமிடல் காத்திருக்கும் கள ஆய்வுகள்" if language == "ta" else "Unassigned Field Visits — Awaiting Scheduling"
            }

        elif intent == "fv_deadline_check":
            # Resolve application number from message or chat history
            resolved_app_dl = app_number_stream if 'app_number_stream' in dir() else None
            if not resolved_app_dl:
                resolved_app_dl = extract_application_number(message) or _extract_app_number_from_context(message, chat_history)
            if not resolved_app_dl:
                structured_data = {
                    "found": False,
                    "message": "Please specify an application number, e.g. APP-2024-000001, to check the deadline."
                }
            else:
                from backend.models import Application
                app_res_dl = await db.execute(
                    select(Application).where(Application.application_number == resolved_app_dl)
                )
                a_dl = app_res_dl.scalar_one_or_none()
                if not a_dl:
                    structured_data = {"found": False, "message": f"Application {resolved_app_dl} not found."}
                else:
                    sub_date_dl = a_dl.submission_date
                    today_dl = datetime.utcnow().date()
                    working_days_dl = 0
                    curr_dl = sub_date_dl
                    while curr_dl < today_dl:
                        curr_dl += timedelta(days=1)
                        if curr_dl.weekday() < 5:
                            working_days_dl += 1
                    structured_data = {
                        "found": True,
                        "application_number": a_dl.application_number,
                        "submission_date": sub_date_dl.isoformat(),
                        "working_days": working_days_dl,
                        "deadline_days": 15,
                        "is_overdue": working_days_dl > 15,
                        "days_overdue": max(0, working_days_dl - 15),
                        "days_remaining": max(0, 15 - working_days_dl),
                        "query_type": "Field Visit Deadline Check"
                    }

        elif intent == "isd_processing":
            # Area comparison and ISD workflow queries — resolve app number from message or history
            _isd_app_no = extract_application_number(message) or _extract_app_number_from_context(message, chat_history)
            if not _isd_app_no:
                # Fall back to most recently created application
                from backend.models import Application as _AppModel
                _last = (await db.execute(select(_AppModel).order_by(_AppModel.created_at.desc()).limit(1))).scalar_one_or_none()
                _isd_app_no = _last.application_number if _last else "APP-2024-000001"
            structured_data = await get_application_detail(db, _isd_app_no)
            structured_data["query_type"] = "ISD Processing"

        # Step 4: Get RAG context from pgvector — skip if DB data was actually found
        has_db_results = (
            structured_data
            and structured_data.get("found", True)
            and structured_data.get("count", 0) > 0
        )
        rag_context = get_rag_context(message, language, n_results=5) if not has_db_results else ""
        
        # Step 5: Try to build HTML directly from structured data (no LLM needed).
        # Skip the HTML path for interrogative questions so the LLM answers
        # conversationally — same logic as the non-streaming path above.
        _msg_lower = message.lower()
        _interrogative_keywords = [
            "which", "what", "how many", "how much", "why", "who",
            "where", "where is", "which department", "currently",
            "give me", "tell me", "show me", "get me",
            "எந்த", "என்ன", "எத்தனை", "ஏன்", "யார்",
        ]
        _field_keywords = [
            "address", "mobile", "phone", "email", "name", "status", "type",
            "stage", "date", "year", "survey", "applicant", "priority", "aadhaar",
            "reason", "overdue", "nisd", "isd", "merge",
            # Tamil field keywords
            "முகவரி", "தொலைபேசி", "மின்னஞ்சல்", "பெயர்", "நாமாகும்", "நாமம்", "நிலை", "வகை",
            "கட்டம்", "தேதி", "ஆண்டு", "கணக்கெண்", "விண்ணப்பதாரர்", "முன்னுரிமை",
            "காரணம்", "காலதாமத",
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
        # Also treat as interrogative when user asks for a specific field
        # In Tamil, users often directly state field name + app number without "what is" phrasing
        _has_field_keyword = any(kw in _msg_lower for kw in _field_keywords)
        _has_interrogative_phrase = any(
            kw in _msg_lower for kw in ["give", "tell", "show", "get", "what", "provide",
                                         "where", "which", "currently", "right now", "is this"]
        )
        # If has field keyword + app number pattern, treat as field query even without interrogative words
        _has_app_number = bool(re.search(r'APP-\d{4}-\d{6}', message, re.IGNORECASE))
        _asking_specific_field = _has_field_keyword and (_has_interrogative_phrase or _has_app_number)
        _is_interrogative = _is_interrogative or _asking_specific_field
        _bypass_html = _is_interrogative and intent in ("application_status", "merge_info", "survey_detail")

        html_response = "" if _bypass_html else build_html_response(structured_data, language)
        import json

        # Only emit table_data when there's no direct HTML response AND we are
        # not in interrogative-bypass mode (where LLM answers conversationally).
        if not html_response and not _bypass_html:
            table_data = _build_table_data(intent, message, str(officer.officer_id), structured_data)
            if table_data:
                table_data['language'] = language
                yield f"data: {json.dumps({'table_data': table_data})}\n\n".encode('utf-8')

        # ── Hardcoded direct answer for interrogative queries (streaming) ──
        _direct_answer_text = ""
        if not html_response and _bypass_html and structured_data and structured_data.get("found", True):
            sd = structured_data
            app_no    = sd.get("application_number", "")
            app_type  = sd.get("type", "")
            survey_no = sd.get("survey_no", "")
            subdivisions = sd.get("subdivisions_being_merged") or []
            total_area   = sd.get("total_merge_area_sqm")

            if app_type == "MERGE" and ("sub" in _msg_lower or "survey" in _msg_lower or
                                         "included" in _msg_lower or "which" in _msg_lower or
                                         "உட்பிரிவு" in message or "கணக்கெண்" in message):
                if subdivisions:
                    subdiv_parts = []
                    for sd_item in subdivisions:
                        area = sd_item.get("area_sqm")
                        label = sd_item["sub_division_no"]
                        if area:
                            label += f" ({area:.2f} sq.m)"
                        subdiv_parts.append(label)
                    subdiv_str = ", ".join(subdiv_parts)
                    area_str = f" The total merge area is {total_area:.2f} sq.m." if total_area else ""
                    _direct_answer_text = (
                        f"Merge application {app_no} covers Survey No. {survey_no} "
                        f"and includes {len(subdivisions)} sub-division(s): {subdiv_str}.{area_str}"
                    )
                else:
                    _direct_answer_text = (
                        f"Merge application {app_no} is on Survey No. {survey_no}, "
                        f"but no sub-divisions have been linked yet."
                    )

            # ── Check if user is asking about application but didn't provide number ──
            if not _direct_answer_text and _asking_specific_field and intent == "application_status" and not app_no:
                # User is asking a specific question about an application but didn't provide the number
                is_tamil_check = language in ("ta", "tanglish")
                if is_tamil_check:
                    _direct_answer_text = "தயவுசெய்து விண்ணப்ப எண்ணை குறிப்பிடவும். (எ.கா: APP-2024-000001)"
                else:
                    _direct_answer_text = "Please provide the application number (e.g., APP-2024-000001) so I can help you with that information."
                logger.info("User asked about application field without providing app number - prompted for app number")

            # ── Specific field extraction for application_status queries (stream) ──
            if not _direct_answer_text and _asking_specific_field and intent == "application_status" and app_no:
                # Check for NISD/ISD type questions first (higher priority)
                if ("nisd" in _msg_lower or "isd" in _msg_lower):
                    app_type_value = sd.get("type", "N/A")
                    _direct_answer_text = f"Application {app_no} is of type: {app_type_value}"
                    logger.info(f"Responded with application type '{app_type_value}' for {app_no}")
                
            # Map user keywords to structured_data fields (English + Tamil)
            if not _direct_answer_text and _asking_specific_field and intent == "application_status" and app_no:
                _field_map = {
                    # Address
                    "address": ("applicant_address", "Address"),
                    "முகவரி": ("applicant_address", "Address"),
                    "virivu": ("applicant_address", "Address"),
                    "mugavari": ("applicant_address", "Address"),
                    # Mobile/Phone
                    "mobile": ("applicant_mobile", "Mobile"),
                    "phone": ("applicant_mobile", "Phone"),
                    "தொலைபேசி": ("applicant_mobile", "Mobile"),
                    "எண்": ("applicant_mobile", "Mobile"),
                    "tholaipaesi": ("applicant_mobile", "Mobile"),
                    "number": ("applicant_mobile", "Mobile"),
                    "contact": ("applicant_mobile", "Mobile"),
                    # Email
                    "email": ("applicant_email", "Email"),
                    "மின்னஞ்சல்": ("applicant_email", "Email"),
                    "minnanjal": ("applicant_email", "Email"),
                    "mail": ("applicant_email", "Email"),
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
                    # Survey
                    "survey": ("survey_no", "Survey Number"),
                    "கணக்கெண்": ("survey_no", "Survey Number"),
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
                    # Aadhaar
                    "aadhaar": ("applicant_aadhaar_last4", "Aadhaar (last 4)"),
                    "aadhar": ("applicant_aadhaar_last4", "Aadhaar (last 4)"),
                    "adhaar": ("applicant_aadhaar_last4", "Aadhaar (last 4)"),
                    # Reason
                    "reason": ("declared_reason", "Declared Reason"),
                    "காரணம்": ("declared_reason", "Declared Reason"),
                    "kaaranam": ("declared_reason", "Declared Reason"),
                    "karanum": ("declared_reason", "Declared Reason"),
                    # Location / stage keywords
                    "where": ("stage", "Current Stage"),
                    "எங்கே": ("stage", "Current Stage"),
                    "engae": ("stage", "Current Stage"),
                    "enge": ("stage", "Current Stage"),
                    "right now": ("stage", "Current Stage"),
                    "currently": ("stage", "Current Stage"),
                    "இப்போது": ("stage", "Current Stage"),
                    "ippodhu": ("stage", "Current Stage"),
                    "ippoathu": ("stage", "Current Stage"),
                    "department": ("stage", "Current Stage"),
                    "office": ("stage", "Current Stage"),
                    "அலுவலகம்": ("stage", "Current Stage"),
                    "aluvalagam": ("stage", "Current Stage"),
                    "aluvalakam": ("stage", "Current Stage"),
                    "current stage": ("stage", "Current Stage"),
                }
                _stage_labels_s = {
                    "SIS": "Sub Inspector Surveyor (SIS) — currently under field verification",
                    "SD": "Survey Department (SD) — forwarded for sketch/approval",
                    "DIS": "District Inspector of Survey (DIS) — under DIS review",
                    "TAHSILDAR": "Tahsildar's office — awaiting patta order",
                    "COMPLETED": "Completed — patta order issued",
                    "REJECTED": "Rejected",
                }
                # Tamil stage labels (streaming)
                _stage_labels_ta_s = {
                    "SIS": "துணை ஆய்வாளர் (SIS) — தற்போது கள சரிபார்ப்பில் உள்ளது",
                    "SD": "சர்வே துறை (SD) — வரைபட அங்கீகாரத்திற்கு அனுப்பப்பட்டது",
                    "DIS": "மாவட்ட ஆய்வாளர் (DIS) — DIS மதிப்பாய்வில் உள்ளது",
                    "TAHSILDAR": "தாசில்தார் அலுவலகம் — பட்டா ஆணைக்காக காத்திருக்கிறது",
                    "COMPLETED": "முடிந்தது — பட்டா ஆணை வழங்கப்பட்டது",
                    "REJECTED": "நிராகரிக்கப்பட்டது",
                }
                
                # Use fuzzy matching for spelling error tolerance
                match_result = _fuzzy_match_keywords(_msg_lower, _field_map, threshold=0.75)
                
                if match_result:
                    field_key, field_label, matched_kw = match_result
                    value = sd.get(field_key)
                    if value is not None and value != "":
                        if isinstance(value, bool):
                            value = "Yes" if value else "No"
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
                                    "Address": "முகவரி", "Mobile": "தொலைபேசி", "Email": "மின்னஞ்சல்",
                                    "Applicant Name": "விண்ணப்பதாரர் பெயர்", "Status": "நிலை",
                                    "Application Type": "விண்ணப்ப வகை", "Survey Number": "கணக்கெண்",
                                    "Submission Date": "சமர்ப்பித்த தேதி", "Priority": "முன்னுரிமை",
                                    "Overdue": "காலதாமதம்", "Declared Reason": "அறிவிக்கப்பட்ட காரணம்"
                                }
                                ta_field_label_s = ta_labels_s.get(field_label, field_label)
                                # More natural Tamil phrasing based on field type
                                if field_key == "applicant_name":
                                    _direct_answer_text = f"{app_no} விண்ணப்பதாரரின் பெயர்: {value}"
                                elif field_key == "status":
                                    _direct_answer_text = f"{app_no} நிலை: {value}"
                                else:
                                    _direct_answer_text = f"{app_no} {ta_field_label_s}: {value}"
                            else:
                                _direct_answer_text = f"The {field_label} for {app_no} is: {value}"
                    else:
                        is_tamil_s = language in ("ta", "tanglish")
                        if is_tamil_s:
                            _direct_answer_text = f"{app_no} க்கு {field_label} தகவல் இல்லை."
                        else:
                            _direct_answer_text = f"No {field_label.lower()} information found for {app_no}."
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
            pass  # already yielded above
        elif "invalid merged geometry" in message.lower() or "invalid merge geometry" in message.lower():
            chunk = "No issues detected. The merged parcel satisfies all validation checks."
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')
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
            apps = structured_data.get("apps", [])
            if apps:
                chunk = f"{', '.join(apps)} — flagged for approaching deadlines or prior escalations."
            else:
                chunk = "No high priority applications found."
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
        elif intent == "workload_by_type":
            isd = structured_data.get("ISD", 0)
            nisd = structured_data.get("NISD", 0)
            merge = structured_data.get("MERGE", 0)
            chunk = f"ISD – {isd} applications, NISD – {nisd} applications, Merge – {merge} applications."
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
                    f"{rate}% — {completed} out of {total} assigned applications "
                    f"have been completed (approved or rejected)."
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
                chunk = "Application not found."
            else:
                app_type = structured_data.get("type", "ISD")
                survey_no = structured_data.get("survey_no", "145")
                subdivs = structured_data.get("included_subdivisions", "")
                subdiv_count = len(subdivs.split(",")) if subdivs and subdivs != "None" else 2
                if app_type == "ISD":
                    chunk = f"ISD — application declares sub-division into {subdiv_count} plots under survey no. {survey_no}."
                elif app_type == "NISD":
                    chunk = f"NISD — application is for transfer of entire survey/patta without subdivision under survey no. {survey_no}."
                else:
                    chunk = f"MERGE — application is for merging subdivisions under survey no. {survey_no}."
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')
        elif intent == "check_documents":
            if not structured_data or not structured_data.get("found", True):
                chunk = "Application not found."
            else:
                missing = [d["document_type"] for d in structured_data.get("documents", []) if not d["is_uploaded"]]
                if missing:
                    missing_str = ", ".join(missing)
                    chunk = f"Missing documents: {missing_str}. Please upload them before scheduling the field visit."
                else:
                    chunk = "No issues detected. All required documents are present."
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')
        elif intent == "check_sale_deed":
            if not structured_data or not structured_data.get("found", True):
                chunk = "Application not found."
            else:
                deed_no = structured_data.get("sale_deed_number") or "N/A"
                sub_date = structured_data.get("submission_date") or "2025-06-25"
                if structured_data.get("sale_deed_registered"):
                    chunk = f"Yes, deed no. {deed_no} matches Sub-Registrar's registered index as of {sub_date}."
                else:
                    chunk = "No match found — flag to Sub-Registrar's office before proceeding."
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')
        
        elif intent == "joint_owner_check":
            if not structured_data or not structured_data.get("found", True):
                chunk = structured_data.get("message", "Please provide an application number or survey number")
            else:
                joint_owners = structured_data.get("joint_owners", [])
                total_owners = structured_data.get("total_owners", 0)
                survey_no = structured_data.get("survey_no", "N/A")
                app_no = structured_data.get("application_number")
                is_tamil = language in ("ta", "tanglish")
                
                # Build response based on whether it's application or survey query
                if is_tamil:
                    prefix = f"விண்ணப்பம் {app_no} (கணக்கெண் {survey_no})" if app_no else f"கணக்கெண் {survey_no}"
                else:
                    prefix = f"For application {app_no} (Survey {survey_no})" if app_no else f"For Survey {survey_no}"
                
                if total_owners == 0:
                    if is_tamil:
                        chunk = f"{prefix}: உரிமையாளர் பதிவுகள் இல்லை."
                    else:
                        chunk = f"{prefix}: No ownership records found."
                elif len(joint_owners) == 0:
                    if is_tamil:
                        chunk = f"{prefix}: விண்ணப்பதாரர் ஒரே உரிமையாளர். கூட்டு உரிமையாளர்கள் இல்லை."
                    else:
                        chunk = f"{prefix}: The applicant is the sole owner. No joint owners are listed."
                else:
                    joint_names = [o.get("name", "N/A") for o in joint_owners]
                    if is_tamil:
                        chunk = f"{prefix}: {len(joint_owners)} கூட்டு உரிமையாளர்கள் உள்ளனர்: {', '.join(joint_names)}."
                    else:
                        chunk = f"{prefix}: There are {len(joint_owners)} joint owner(s) listed: {', '.join(joint_names)}."
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')

        elif intent == "application_status":
            if not structured_data or not structured_data.get("found", True):
                chunk = structured_data.get("message", "Application not found.")
            elif "history" in structured_data:
                hist = structured_data.get("history", [])
                app_no = structured_data.get("application_number", "")
                chunk = f"Workflow history for {app_no}: {len(hist)} stage(s) recorded."
            else:
                app_no = structured_data.get("application_number", "N/A")
                app_type = structured_data.get("type", "N/A")
                status = structured_data.get("status", "N/A").capitalize()
                stage = structured_data.get("stage", "N/A")
                applicant = structured_data.get("applicant_name") or "N/A"
                survey = structured_data.get("survey_no", "N/A")
                chunk = (
                    f"Here are the details for {app_no}. "
                    f"Type: {app_type}, Status: {status}, Stage: {stage}, "
                    f"Applicant: {applicant}, Survey No: {survey}."
                )
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')

        elif intent in ("pending_applications", "overdue_applications"):
            count = structured_data.get("count", 0) if structured_data else 0
            qtype = structured_data.get("query_type", "applications") if structured_data else "applications"
            if count == 0:
                chunk = f"No {qtype.lower()} found in your jurisdiction."
            elif count == 1:
                chunk = f"There is 1 {qtype.rstrip('s').lower()} in your jurisdiction."
            else:
                chunk = f"There are {count} {qtype.lower()} in your jurisdiction."
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')

        elif intent == "officer_workload":
            total = structured_data.get("total_active", 0) if structured_data else 0
            isd = structured_data.get("ISD", 0)
            nisd = structured_data.get("NISD", 0)
            merge = structured_data.get("MERGE", 0)
            overdue = structured_data.get("overdue", 0)
            chunk = (
                f"Your workload: {total} active application(s) — "
                f"ISD: {isd}, NISD: {nisd}, Merge: {merge}, Overdue: {overdue}."
            )
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
            if language == "ta":
                if count == 0:
                    chunk = "திட்டமிடல் காத்திருக்கும் நிறைவேற்றப்படாத கள ஆய்வுகள் எதுவும் இல்லை."
                elif count == 1:
                    chunk = "திட்டமிடல் காத்திருக்கும் 1 கள ஆய்வு விண்ணப்பம் உள்ளது."
                else:
                    chunk = f"திட்டமிடல் காத்திருக்கும் {count} கள ஆய்வு விண்ணப்பங்கள் உள்ளன."
            else:
                if count == 0:
                    chunk = "There are no unassigned field visits awaiting scheduling."
                elif count == 1:
                    chunk = "There is 1 application with an unassigned field visit awaiting scheduling."
                else:
                    chunk = f"There are {count} applications with unassigned field visits awaiting scheduling."
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
            if count == 0:
                chunk = "No field visits are currently overdue. All field visits are on schedule."
            else:
                chunk = f"Found {count} overdue field visit(s). See the table below for details."
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')

        elif intent == "isd_processing":
            # Build context-aware response based on what the user asked
            if not structured_data or not structured_data.get("found", True):
                chunk = structured_data.get("message", "Application not found.") if structured_data else "Application not found."
            else:
                _isd_msg = message.lower()
                _app_no_isd = structured_data.get("application_number", "")
                _survey_no_isd = structured_data.get("survey_no", "N/A")
                _survey_area = structured_data.get("survey_total_area_sqm")
                _prop_area = structured_data.get("proposed_total_area_sqm")
                _area_match = structured_data.get("area_match")
                _proposed = structured_data.get("proposed_sub_divisions", [])

                if any(w in _isd_msg for w in ["compare", "original"]) and "area" in _isd_msg:
                    if _survey_area and _prop_area:
                        _diff = abs(_survey_area - _prop_area)
                        if _area_match:
                            _match_str = "✅ Areas match — no discrepancy."
                        else:
                            _match_str = f"⚠ Mismatch! Difference: {_diff:,.2f} sq.m. Please verify the manually entered sub-division areas."
                        chunk = (
                            f"Survey {_survey_no_isd} original area: {_survey_area:,.2f} sq.m\n"
                            f"Total proposed sub-division area: {_prop_area:,.2f} sq.m\n"
                            f"{_match_str}"
                        )
                    elif _survey_area and not _prop_area:
                        chunk = (
                            f"Survey {_survey_no_isd} original area: {_survey_area:,.2f} sq.m. "
                            f"However, no sub-division area data is available for {_app_no_isd} — "
                            f"the proposed sub-division areas may not have been entered yet."
                        )
                    else:
                        chunk = f"Area data not available for {_app_no_isd}."
                elif "proposed" in _isd_msg:
                    if _proposed:
                        lines = [
                            f"{p['proposed_sub_division_no']} — "
                            f"{p['proposed_area_sqm']:,.2f} sq.m — {p['status'].capitalize()}"
                            for p in _proposed if p.get("proposed_area_sqm")
                        ]
                        chunk = f"Proposed sub-divisions for {_app_no_isd} (Survey {_survey_no_isd}):\n" + "\n".join(lines) if lines else f"No area data for sub-divisions of {_app_no_isd}."
                    else:
                        chunk = f"No proposed sub-divisions found for {_app_no_isd}."
                else:
                    chunk = (
                        f"Application {_app_no_isd} — Survey {_survey_no_isd}. "
                        f"{'Survey area: ' + str(_survey_area) + ' sq.m. ' if _survey_area else ''}"
                        f"{'Proposed total: ' + str(_prop_area) + ' sq.m.' if _prop_area else 'Proposed area not yet entered.'}"
                    )
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')

        elif intent in ("field_visits", "ward_surveys", "block_surveys",
                        "survey_detail", "survey_owners", "next_subdivision",
                        "jurisdiction_summary", "rejection_info", "taluk_summary",
                        "litigation_check", "highest_priority_applications",
                        "merge_info", "town_applications", "block_applications"):
            # Table is rendered on the frontend. Just emit a short natural intro.
            found = structured_data.get("found", True) if structured_data else False
            if not found:
                chunk = structured_data.get("message", "No records found.")
            else:
                # Special message for priority applications
                if intent == "highest_priority_applications":
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
                else:
                    qtype = structured_data.get("query_type", "") if structured_data else ""
                    if qtype:
                        chunk = f"Here are the {qtype.lower()} results."
                    else:
                        chunk = "Results are shown in the table below."
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')

        elif any(ph in message.lower() for ph in [
            "uploaded", "word document", "pdf document", "question bank",
            "answer all", "answer for all", "from the document", "in the document",
            "the file", "attached file", "from this file",
        ]):
            chunk = (
                "I can see you're referring to an uploaded document. "
                "Unfortunately I can only read plain text (.txt) file contents directly — "
                "Word and PDF files need to be processed first.\n\n"
                "Please copy and paste the relevant text from the document into the chat, "
                "and I'll answer your questions from it."
            )
            full_response_text = chunk
            sse_data = f"data: {json.dumps({'content': chunk})}\n\n"
            yield sse_data.encode('utf-8')

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
            
        # Step 7: Calculate response time
        response_time_ms = int((time.time() - start_time) * 1000)
        
        # Step 8: Save chat messages to database
        await save_chat_messages(
            db=db,
            session_id=session_id,
            user_message=message,
            assistant_message=full_response_text,
            language=language,
            response_time_ms=response_time_ms
        )
        
        logger.info(f"Chat processed and streamed successfully in {response_time_ms}ms")
        
    except Exception as e:
        logger.error(f"Error in process_chat_stream: {e}", exc_info=True)
        import json
        error_messages = {
            "en": "I apologize, but I encountered an error processing your request. Please try again.",
            "ta": "மன்னிக்கவும், உங்கள் கோரிக்கையைச் செயல்படுத்துவதில் பிழை ஏற்பட்டது. மீண்டும் முயற்சிக்கவும்.",
            "tanglish": "Sorry, error ஏற்பட்டது. Please try again."
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
    response_time_ms: int
) -> None:
    """
    Save user and assistant messages to database
    
    Args:
        db: Database session
        session_id: Chat session UUID
        user_message: User's message
        assistant_message: Assistant's response
        language: Detected language
        response_time_ms: Response time in milliseconds
    """
    try:
        # Get session
        session_query = select(ChatSession).where(
            ChatSession.id == session_id
        )
        result = await db.execute(session_query)
        session = result.scalar_one_or_none()
        
        if not session:
            logger.error(f"Chat session {session_id} not found")
            return
        
        # Save user message
        user_msg = ChatMessage(
            session_id=session_id,
            role="user",
            content=user_message,
            detected_language=language,
            created_at=datetime.utcnow()
        )
        db.add(user_msg)
        
        # Save assistant message
        assistant_msg = ChatMessage(
            session_id=session_id,
            role="assistant",
            content=assistant_message,
            detected_language=language,
            response_time_ms=response_time_ms,
            created_at=datetime.utcnow()
        )
        db.add(assistant_msg)
        
        # Update session last activity
        session.last_activity = datetime.utcnow()
        
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
            started_at=datetime.utcnow(),
            last_activity=datetime.utcnow(),
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
        query = select(ChatMessage).where(
            ChatMessage.session_id == session_id
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


def _build_table_data(intent: str, message: str, user_id: str, structured_data: Dict[str, Any] = None) -> Optional[Dict[str, Any]]:
    if not structured_data:
        return None
        
    intent_lower = intent.lower()
    
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
        block_name = structured_data.get("jurisdiction", {}).get("block", "Block B1")
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
        
    # 2. Pending applications
    elif intent_lower in ["pending_applications", "town_applications", "block_applications", "immediate_action"] or (intent_lower == "workload" and "applications" in structured_data):
        apps = []
        for app in structured_data.get("applications", []):
            apps.append({
                "application_number": app.get("application_number"),
                "type": app.get("type"),
                "town_name": app.get("town_name") or "N/A",
                "ward_number": app.get("ward_number") or "N/A",
                "status": app.get("status") or "Pending",
                "current_stage": app.get("stage") or app.get("current_stage"),
                "submission_date": app.get("submission_date")
            })
        return {
            "query_type": structured_data.get("query_type", "Pending Applications"),
            "applications": apps
        }
        
    # 3. Field visit
    elif intent_lower in ["field_visit", "field_visits", "awaiting_field_visit"]:
        visits = []
        for visit in structured_data.get("field_visits", []):
            visits.append({
                "application_number": visit.get("application_number"),
                "survey_no": visit.get("survey_no"),
                "block_number": visit.get("block_number") or "N/A",
                "application_type": visit.get("application_type"),
                "status": visit.get("status"),
                "field_visit_date": visit.get("field_visit_date")
            })
        return {
            "query_type": "Field Visits",
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
        pending = structured_data.get("ISD", 0) + structured_data.get("NISD", 0) + structured_data.get("MERGE", 0)
        overdue = structured_data.get("overdue", 0)
        unscheduled = structured_data.get("unscheduled_visits", 0)
        return {
            "query_type": "Workload Summary",
            "total_applications": total,
            "pending_count": pending,
            "overdue_count": overdue,
            "unscheduled_visits": unscheduled
        }
        
    # 6. Status check
    elif intent_lower in ["status_check", "application_status"]:
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
            "application_number": structured_data.get("application_number"),
            "type": structured_data.get("type"),
            "included_subdivisions": structured_data.get("included_subdivisions") or "N/A",
            "status": structured_data.get("status") or "Pending",
            "stage": structured_data.get("stage"),
            "submission_date": structured_data.get("submission_date"),
            "field_visit_scheduled": bool(structured_data.get("field_visit_scheduled")),
            "field_visit_date": structured_data.get("field_visit_date"),
            "is_overdue": bool(structured_data.get("is_overdue")),
            "priority_flag": bool(structured_data.get("priority_flag")),
            "applicant_name": structured_data.get("applicant_name"),
            "applicant_mobile": structured_data.get("applicant_mobile"),
            "applicant_email": structured_data.get("applicant_email"),
            "applicant_address": structured_data.get("applicant_address"),
            "applicant_aadhaar_last4": structured_data.get("applicant_aadhaar_last4"),
            "declared_reason": structured_data.get("declared_reason")
        }

    # 7. Rejection info
    elif intent_lower in ["rejection_info"]:
        return {
            "query_type": structured_data.get("query_type", "Rejection History"),
            "application_number": structured_data.get("application_number"),
            "rejections": structured_data.get("rejections", [])
        }

    # 8. Jurisdiction summary
    elif intent_lower in ["jurisdiction_summary"] or "jurisdiction" in structured_data:
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
