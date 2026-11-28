"""
backend/services/chatbot/query_understanding.py
================================================
LLM-Based Semantic Query Understanding & Hybrid Intent Routing.

Converts natural language queries in English, Tamil, and Tanglish into
strict Pydantic-validated SemanticQueryPlan objects.

Key Capabilities:
1. Structured entity extraction (Application No, Survey No, Service Codes) via
   authoritative deterministic parsers combined with LLM semantic interpretation.
2. Understanding of paraphrases, typos, colloquial terms, comparative & analytical queries.
3. Multi-part query decomposition into ordered sub-operations.
4. Confidence-aware hybrid routing:
   - Deterministic fast-paths for unambiguous greetings and explicit lookups.
   - LLM semantic parsing for natural language expressions and complex intents.
   - Graceful fallback to existing deterministic regex/intent rules on LLM timeout/unavailability.
5. Strict zero-SQL generation; all data operations map to authorized backend tools and handlers.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Dict, Any, List, Optional, Tuple, Set
from pydantic import BaseModel, Field, ValidationError

from backend.config import settings
from backend.utils.logger import get_logger
from backend.services import rag_language, rag
from backend.services import rag_llm
from backend.services import llm_gate
from langchain_core.messages import SystemMessage, HumanMessage

logger = get_logger(__name__)


def _get_llm():
    return rag_llm.llm


# ─────────────────────────────────────────────────────────────────────────────
# 1. Pydantic Structured Semantic Schemas
# ─────────────────────────────────────────────────────────────────────────────

class DateRangeFilter(BaseModel):
    """Temporal range criteria."""
    start_date: Optional[str] = None  # YYYY-MM-DD
    end_date: Optional[str] = None    # YYYY-MM-DD
    month: Optional[int] = Field(default=None, ge=1, le=12)
    year: Optional[int] = Field(default=None, ge=2020, le=2030)


class AgeDurationFilter(BaseModel):
    """Aging and urgency criteria."""
    min_days: Optional[int] = Field(default=None, ge=0)
    max_days: Optional[int] = Field(default=None, ge=0)
    is_overdue: Optional[bool] = None
    urgency_tier: Optional[str] = None  # "normal", "urgent", "critical"


class SortingSpec(BaseModel):
    """Sort criteria."""
    field: str = Field(default="submission_date")  # e.g., "date", "age", "priority"
    order: str = Field(default="desc")             # "asc" or "desc"


class OperationSpec(BaseModel):
    """Atomic step in a multi-part or analytical plan."""
    operation_type: str = Field(..., description="retrieve, count, analyze, compare, verify, search_docs")
    target_entity: str = Field(..., description="application, survey, field_visit, service_code, rule, workload")
    description: Optional[str] = None
    parameters: Dict[str, Any] = Field(default_factory=dict)


class SemanticQueryPlan(BaseModel):
    """
    Comprehensive, structured interpretation of an officer's query.
    Pydantic-validated to ensure type safety and schema compliance.
    """
    # Intent Classification
    intent: str = Field(..., description="Primary canonical intent")
    sub_intent: Optional[str] = Field(default=None, description="Fine-grained intent specialization")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Routing confidence score")
    
    # Structured Domain Identifiers (Authoritative)
    application_numbers: List[str] = Field(default_factory=list)
    survey_numbers: List[str] = Field(default_factory=list)
    service_codes: List[str] = Field(default_factory=list)
    entity_references: List[str] = Field(default_factory=list)  # e.g. "it", "the first one", "applicant"
    
    # Geographic & Status Filters
    status_filters: List[str] = Field(default_factory=list)     # "pending", "in_progress", "approved", "rejected", "overdue"
    application_type: Optional[str] = None                      # "ISD", "NISD"
    submission_channel: Optional[str] = None                    # "CSC", "citizen", "sub_registrar"
    ward_number: Optional[str] = None
    block_number: Optional[str] = None
    
    # Temporal & Urgency Modifiers
    date_range: Optional[DateRangeFilter] = None
    age_filter: Optional[AgeDurationFilter] = None
    sorting: Optional[SortingSpec] = None
    requested_fields: List[str] = Field(default_factory=list)   # e.g. ["applicant_name", "status", "field_visit_date"]
    
    # Multi-Part & Comparative Processing
    is_multi_part: bool = False
    operations: List[OperationSpec] = Field(default_factory=list)
    is_comparative: bool = False
    
    # Resource Requirements
    needs_database: bool = True
    needs_document_retrieval: bool = False
    
    # Conversational & Ambiguity Handling
    query_language: str = Field(default="en", description="Detected language of the query (en, ta, tanglish)")
    response_language: str = Field(default="en", description="Target response language (en, ta, tanglish)")
    is_ambiguous: bool = False
    missing_parameters: List[str] = Field(default_factory=list)
    clarification_question: Optional[str] = None
    
    # Audit & Diagnostics
    reasoning: Optional[str] = None
    source: str = Field(default="llm_semantic", description="deterministic_fast_path, llm_semantic, or fallback_regex")


# ─────────────────────────────────────────────────────────────────────────────
# 2. Deterministic Identifier Extraction (Rule 9: Always Preserved)
# ─────────────────────────────────────────────────────────────────────────────

_SERVICE_CODE_RE = re.compile(r"\b(015[1-9]|\d{4})\b")

def extract_authoritative_identifiers(query: str) -> Dict[str, Any]:
    """
    Deterministic extraction of structured identifiers from the query string.
    Ensures that real application numbers, survey numbers, and service codes
    are never mangled or hallucinated by the LLM.
    """
    app_no = rag.extract_application_number(query)
    app_numbers = [app_no] if app_no else []
    
    survey_no = rag.extract_survey_number(query)
    survey_numbers = [survey_no] if survey_no else []
    
    # Extract service codes
    service_codes: List[str] = []
    for match in _SERVICE_CODE_RE.finditer(query):
        code = match.group(1)
        if code in ("0153", "0154") and code not in service_codes:
            service_codes.append(code)
            
    # Extract submission channel
    channels = rag.extract_submission_channels(query)
    submission_channel = channels[0] if channels else None
    
    return {
        "application_numbers": app_numbers,
        "survey_numbers": survey_numbers,
        "service_codes": service_codes,
        "submission_channel": submission_channel
    }


# ─────────────────────────────────────────────────────────────────────────────
# 3. LLM Semantic Understanding Prompt
# ─────────────────────────────────────────────────────────────────────────────

_SEMANTIC_PARSER_SYSTEM_PROMPT = """You are the Lead NLU Semantic Parser for the Tamil Nadu Survey Department Sub Inspector Surveyor (SIS) AI Assistant.
Your task is to analyze an officer's input query in English, Tamil (தமிழ்), or Tanglish and output ONLY a compact, valid JSON object matching the SemanticQueryPlan schema.

CANONICAL INTENTS:
- pending_applications: List or count open pending files
- overdue_applications: Files past SLA deadline, delayed, late, or needing urgent resolution
- officer_workload: Summary of officer's current live open files, pending vs overdue split
- application_status: Status, applicant, dates, or details of a specific application
- application_analysis: Analytical or urgency queries (e.g., "files pending long time needing urgent attention")
- survey_owners: Registered owners or joint owners of a survey number
- survey_detail: Area, patta number, boundaries, or location of a survey number
- can_apply_check: Whether a survey number is locked by active mutation applications
- field_visits: Inspection schedules, booked visits, completed or pending field visits
- fv_visit_plan: Visit planning ("which application should I inspect next / tomorrow")
- fv_overdue_inspections: Field inspections that missed their scheduled date
- service_code_lookup: Lookup meaning of code (0153, 0154)
- service_code_guide: Comparison / difference between ISD (0154) and NISD (0153)
- check_documents: Missing documents, required proofs for patta transfer
- greeting: Salutations, thank you, goodbye (hello, vanakkam, bye, nandri)
- general_query: General questions outside specific database records

RULES:
1. Understand colloquial expressions, paraphrases, and typos in English, Tamil, and Tanglish.
   - "தாமதமான விண்ணப்பங்கள்" -> overdue_applications
   - "files pending for a long time needing urgent attention" -> application_analysis (age_filter: is_overdue=true, urgency_tier="urgent")
   - "delayed past SLA" / "missed deadline" -> overdue_applications
   - "pending list kaattu" -> pending_applications
   - "details of the first one" -> application_status (entity_references: ["first one"])
2. Multi-part questions: If the user asks for two distinct things (e.g. "How many pending ISD applications do I have and how many are overdue?"):
   - Set is_multi_part = true
   - Populate operations with each atomic step.
3. Comparative questions: If comparing ISD vs NISD, or two channels/statuses, set is_comparative = true.
4. Output strict JSON with keys:
   {
     "intent": "<canonical_intent>",
     "sub_intent": "<optional_sub_intent>",
     "confidence": 0.95,
     "status_filters": ["pending" | "approved" | "rejected" | "overdue"],
     "application_type": "ISD" | "NISD" | null,
     "submission_channel": "CSC" | "citizen" | "sub_registrar" | null,
     "date_range": null or {"month": 1, "year": 2026},
     "age_filter": null or {"is_overdue": true, "urgency_tier": "urgent"},
     "sorting": {"field": "submission_date", "order": "desc"},
     "requested_fields": [],
     "is_multi_part": false,
     "operations": [],
     "is_comparative": false,
     "needs_database": true,
     "needs_document_retrieval": false,
     "response_language": "en" | "ta" | "tanglish",
     "is_ambiguous": false,
     "missing_parameters": [],
     "clarification_question": null,
     "reasoning": "brief explanation"
   }
5. NEVER invent or generate SQL queries.
6. Do NOT invent dates, thresholds, or fake numbers.
Output raw JSON only. No markdown formatting, no code fences.
"""


# ─────────────────────────────────────────────────────────────────────────────
# 4. Fast-Path Deterministic Matcher
# ─────────────────────────────────────────────────────────────────────────────

_GREETING_TOKENS = frozenset({
    "hello", "hi", "hey", "vanakkam", "வணக்கம்", "காலை வணக்கம்", "மாலை வணக்கம்",
    "bye", "goodbye", "good night", "thanks", "thank you", "nandri", "நன்றி"
})

def _try_deterministic_fast_path(query: str, lang: str) -> Optional[SemanticQueryPlan]:
    """
    High-speed deterministic resolution for low-complexity turns (greetings, simple status).
    Bypasses LLM latency when intent is 100% unambiguous.
    """
    clean_q = query.strip().lower()
    
    # 1. Direct Conversational Greetings
    if clean_q in _GREETING_TOKENS:
        return SemanticQueryPlan(
            intent="greeting",
            confidence=1.0,
            query_language=lang,
            response_language=lang,
            needs_database=False,
            needs_document_retrieval=False,
            source="deterministic_fast_path"
        )
        
    # 2. Strict Service Code Direct Lookups
    if clean_q in ("0153", "what is 0153", "0153 enna"):
        return SemanticQueryPlan(
            intent="service_code_lookup",
            service_codes=["0153"],
            confidence=1.0,
            query_language=lang,
            response_language=lang,
            needs_database=True,
            needs_document_retrieval=False,
            source="deterministic_fast_path"
        )
    if clean_q in ("0154", "what is 0154", "0154 enna"):
        return SemanticQueryPlan(
            intent="service_code_lookup",
            service_codes=["0154"],
            confidence=1.0,
            query_language=lang,
            response_language=lang,
            needs_database=True,
            needs_document_retrieval=False,
            source="deterministic_fast_path"
        )
        
    return None


# ─────────────────────────────────────────────────────────────────────────────
# 5. Core Understanding Function: understand_query
# ─────────────────────────────────────────────────────────────────────────────

async def understand_query(
    query: str,
    chat_history: Optional[List[Dict[str, Any]]] = None,
    timeout_seconds: Optional[float] = None
) -> SemanticQueryPlan:
    """
    Main entry point for semantic query understanding.
    
    Workflow:
    1. Language detection (en, ta, tanglish).
    2. Authoritative deterministic identifier extraction (app no, survey no, service code).
    3. Fast-path check (for pure greetings / code lookups).
    4. LLM semantic understanding invocation with format='json' under concurrency gate.
    5. Pydantic validation of output JSON.
    6. Overlay authoritative identifiers on top of LLM plan.
    7. Fallback to existing deterministic regex intent parsing if LLM is unavailable or times out.
    """
    lang = rag_language.detect_language(query)
    auth_ids = extract_authoritative_identifiers(query)
    
    # Check deterministic fast-path
    fast_plan = _try_deterministic_fast_path(query, lang)
    if fast_plan:
        fast_plan.application_numbers = auth_ids["application_numbers"]
        fast_plan.survey_numbers = auth_ids["survey_numbers"]
        return fast_plan
        
    # If LLM is configured, invoke LLM Semantic Parser
    timeout = timeout_seconds or settings.LLM_HTTP_READ_TIMEOUT
    plan: Optional[SemanticQueryPlan] = None
    
    try:
        async with llm_gate.llm_slot():
            # Build messages
            messages = [
                SystemMessage(content=_SEMANTIC_PARSER_SYSTEM_PROMPT),
                HumanMessage(content=f"Query: \"{query}\"\nDetected Language: {lang}")
            ]
            
            # Use json-formatted chat invocation
            json_model = _get_llm().bind(format="json", options={"temperature": 0.0, "num_predict": 256})
            response = await asyncio.wait_for(json_model.ainvoke(messages), timeout=timeout)
            raw_text = response.content.strip()
            
            # Parse and validate JSON
            data = json.loads(raw_text)
            
            # Standardize language if missing or defaulted
            if not data.get("response_language"):
                data["response_language"] = lang
                
            plan = SemanticQueryPlan.model_validate(data)
            plan.source = "llm_semantic"
            plan.query_language = lang
            logger.info("query_understanding: successfully parsed query via LLM semantic engine",
                        intent=plan.intent, confidence=plan.confidence)
            
    except (asyncio.TimeoutError, llm_gate.LLMBusy, json.JSONDecodeError, ValidationError, Exception) as exc:
        logger.warning(f"query_understanding: LLM semantic engine bypassed or failed: {exc}; falling back to deterministic parser")
        plan = None

    # Fallback to deterministic regex/keyword parser (Ensures Rule 11: LLM unavailability never breaks functionality)
    if plan is None:
        deterministic_intent = rag.parse_intent(query)
        needs_db = deterministic_intent not in ("greeting", "smalltalk", "farewell", "thanks")
        needs_doc = deterministic_intent in ("service_code_guide", "check_documents", "general_query")
        
        plan = SemanticQueryPlan(
            intent=deterministic_intent,
            confidence=0.85,
            query_language=lang,
            response_language=lang,
            needs_database=needs_db,
            needs_document_retrieval=needs_doc,
            source="fallback_regex",
            reasoning=f"Fallback to rag.parse_intent on {query[:50]}"
        )

    # ─────────────────────────────────────────────────────────────────────────
    # ENFORCE AUTHORITATIVE IDENTIFIERS OVER LLM HALLUCINATIONS
    # ─────────────────────────────────────────────────────────────────────────
    if auth_ids["application_numbers"]:
        plan.application_numbers = auth_ids["application_numbers"]
    if auth_ids["survey_numbers"]:
        plan.survey_numbers = auth_ids["survey_numbers"]
    if auth_ids["service_codes"]:
        plan.service_codes = auth_ids["service_codes"]
    if auth_ids["submission_channel"] and not plan.submission_channel:
        plan.submission_channel = auth_ids["submission_channel"]

    # Special handling for analytical requests:
    # "Show files pending for a long time and identify which ones need urgent attention"
    # -> Guarantee urgency and age flags are mapped
    lower_q = query.lower()
    if "pending for a long time" in lower_q or "urgent attention" in lower_q:
        plan.intent = "application_analysis"
        if not plan.age_filter:
            plan.age_filter = AgeDurationFilter(is_overdue=True, urgency_tier="urgent")
        if "pending" not in plan.status_filters:
            plan.status_filters.append("pending")

    return plan
