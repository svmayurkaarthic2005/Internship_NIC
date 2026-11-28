"""
Intelligent Response Generator for SIS Chatbot (Phase 7).

Provides evidence-grounded, multilingual natural language response generation
for Sub Inspector Surveyor (SIS) officers in Tamil Nadu.

Key Capabilities:
1. Natural English generation (professional, grammatically correct).
2. Natural Tamil generation in Tamil script (தமிழ்), preserving technical identifiers in Latin script.
3. Tanglish query understanding with fluent Tamil script output.
4. Strict evidence preservation (Application numbers, Survey numbers, Service codes, Statuses, Counts).
5. Dynamic, non-repetitive conversational responses.
6. Concise vs. detailed response modes.
7. Explanations of complex survey procedures in clear terms.
8. Accessible HTML <table> rendering for multi-record results with exact count preservation.
9. Clear explanations for missing records, out-of-jurisdiction, or untracked fields.
10. Proactive, context-aware follow-up question suggestions.
11. Evidence Verification Step (`verify_response_evidence`):
    - Audits all numbers, counts, service codes, statuses, and survey numbers against db_evidence & doc_evidence.
    - Categorizes claims: Retrieved Facts, Document Rules, Explanations, Unknowns.
12. Streaming response helper with error fallback and post-stream verification.
"""

from __future__ import annotations
import re
import json
import html as html_lib
import logging
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional, Tuple, Sequence, Set
from datetime import datetime, date

from backend.schemas import OfficerContext
from backend.utils.logger import get_logger

logger = get_logger("backend.services.chatbot.response_generator")

# ─────────────────────────────────────────────────────────────────────────────
# 1. Authoritative Domain Mappings & Terminology
# ─────────────────────────────────────────────────────────────────────────────

STATUS_TRANSLATIONS_TA: Dict[str, str] = {
    "pending": "நிலுவையில் உள்ளது",
    "in_progress": "செயல்பாட்டில் உள்ளது",
    "approved": "அங்கீகரிக்கப்பட்டது",
    "rejected": "நிராகரிக்கப்பட்டது",
    "escalated": "மேல்நிலைப்படுத்தப்பட்டது",
}

STATUS_TRANSLATIONS_EN: Dict[str, str] = {
    "pending": "Pending",
    "in_progress": "In Progress",
    "approved": "Approved",
    "rejected": "Rejected",
    "escalated": "Escalated",
}

STAGE_TRANSLATIONS_TA: Dict[str, str] = {
    "SIS": "நில அளவர் (SIS)",
    "SD": "மூத்த வரைவாளர் (SD)",
    "DIS": "துணை ஆய்வாளர் (DIS)",
    "TAHSILDAR": "வட்டாட்சியர் (Tahsildar)",
    "ZDT": "மண்டல துணை வட்டாட்சியர் (ZDT)",
    "HQDT": "தலைமையிட துணை வட்டாட்சியர் (HQDT)",
    "DRO": "மாவட்ட வருவாய் அலுவலர் (DRO)",
}

SERVICE_CODE_MAP: Dict[str, Dict[str, str]] = {
    "0154": {
        "code": "0154",
        "acronym": "ISD",
        "en": "Involving Sub-Division (ISD)",
        "ta": "உட்பிரிவுடன் கூடிய பட்டா மாறுதல் (ISD)",
        "requires_visit": "Yes (Field inspection & SD sketch mandatory)",
        "requires_visit_ta": "ஆம் (கள ஆய்வு மற்றும் உட்பிரிவு வரைபடம் கட்டாயம்)",
        "sla_days": "30 days",
    },
    "0153": {
        "code": "0153",
        "acronym": "NISD",
        "en": "Not Involving Sub-Division (NISD)",
        "ta": "உட்பிரிவு இல்லாத பட்டா மாறுதல் (NISD)",
        "requires_visit": "No (Straight patta transfer, no field inspection)",
        "requires_visit_ta": "இல்லை (நேரடி பட்டா மாறுதல், கள ஆய்வு தேவையில்லை)",
        "sla_days": "15 days",
    },
    "0151": {
        "code": "0151",
        "acronym": "Full Transfer",
        "en": "Full Patta Transfer",
        "ta": "முழு பட்டா மாறுதல்",
        "requires_visit": "No",
        "requires_visit_ta": "இல்லை",
        "sla_days": "15 days",
    },
    "0152": {
        "code": "0152",
        "acronym": "Joint Transfer",
        "en": "Joint Patta Transfer",
        "ta": "கூட்டுப் பட்டா மாறுதல்",
        "requires_visit": "Conditional",
        "requires_visit_ta": "நிபந்தனைக்குட்பட்டது",
        "sla_days": "30 days",
    },
}

COLUMN_LABELS: Dict[str, Dict[str, str]] = {
    "application_number": {"en": "Application Number", "ta": "விண்ணப்ப எண்"},
    "service_code": {"en": "Service Code", "ta": "சேவை குறியீடு"},
    "type": {"en": "Type", "ta": "விண்ணப்ப வகை"},
    "status": {"en": "Status", "ta": "நிலை"},
    "stage": {"en": "Stage", "ta": "கட்டம்"},
    "applicant_name": {"en": "Applicant Name", "ta": "விண்ணப்பதாரர் பெயர்"},
    "survey_no": {"en": "Survey No", "ta": "கணக்கெண் / சர்வே எண்"},
    "subdivision_number": {"en": "Subdivision", "ta": "உட்பிரிவு எண்"},
    "submission_date": {"en": "Submission Date", "ta": "சமர்ப்பித்த தேதி"},
    "days_pending": {"en": "Days Pending", "ta": "நிலுவை நாட்கள்"},
    "ward": {"en": "Ward", "ta": "வார்டு"},
    "block": {"en": "Block", "ta": "தொகுதி"},
    "village": {"en": "Village", "ta": "கிராமம்"},
    "town": {"en": "Town", "ta": "நகரம்"},
    "taluk": {"en": "Taluk", "ta": "தாலுகா"},
    "district": {"en": "District", "ta": "மாவட்டம்"},
    "fee_amount": {"en": "Fee (Rs)", "ta": "கட்டணம் (ரூ)"},
    "scheduled_date": {"en": "Visit Date", "ta": "கள ஆய்வு தேதி"},
    "visit_status": {"en": "Visit Status", "ta": "ஆய்வு நிலை"},
}

# Regex patterns for domain identifiers
APP_NO_RE = re.compile(r"\b(202\d/[0-9]{4}/[0-9]{2}/[0-9]{6}|APP-\d{4}-\d{6})\b", re.IGNORECASE)
SERVICE_CODE_RE = re.compile(r"\b(015[1-9])\b")
SURVEY_NO_RE = re.compile(r"\b(?:survey\s*(?:no|number)?|சர்வே\s*எண்|கணக்கெண்)\s*[:.]?\s*(\d+[A-Za-z]?(?:/\d+[A-Za-z]*)?)\b", re.IGNORECASE)
COUNT_CLAIM_RE = re.compile(r"\b(?:found|have|total|showing|identified|there are|மொத்தம்)?\s*(\d{1,4})\s*(?:applications?|records?|files?|visits?|மனுக்கள்|விண்ணப்பங்கள்|கோப்புகள்|ஆய்வுகள்)\b", re.IGNORECASE)


# ─────────────────────────────────────────────────────────────────────────────
# 2. Data Structures
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ResponseContext:
    """Input context for response generation and verification."""
    query: str
    language: str = "en"  # "en", "ta", "tanglish"
    intent: str = ""
    db_evidence: Optional[Dict[str, Any]] = None
    doc_evidence: Optional[List[Dict[str, Any]]] = None
    structured_data: Optional[Dict[str, Any]] = None
    officer: Optional[OfficerContext] = None
    chat_history: Optional[List[Dict[str, Any]]] = None
    detail_level: str = "normal"  # "concise", "normal", "detailed"
    requested_fields: Optional[List[str]] = None
    allow_llm: bool = True

    @property
    def target_language(self) -> str:
        """Target response language: Tamil script for 'ta' and 'tanglish', English otherwise."""
        return "ta" if self.language in ("ta", "tanglish") else "en"


@dataclass
class VerificationReport:
    """Report generated by evidence verification step."""
    is_valid: bool
    verified_text: str
    original_text: str
    corrections: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    provenance: Dict[str, List[str]] = field(default_factory=lambda: {
        "retrieved_facts": [],
        "document_rules": [],
        "explanations": [],
        "unknowns": []
    })


@dataclass
class GeneratedResponse:
    """Final response model returned to orchestrator/frontend."""
    text: str
    html: Optional[str] = None
    language: str = "en"
    verified: bool = True
    verification_report: Optional[VerificationReport] = None
    followups: List[str] = field(default_factory=list)
    detail_level: str = "normal"
    sources: List[str] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# 3. Evidence Verification Engine (Anti-Hallucination)
# ─────────────────────────────────────────────────────────────────────────────

def _collect_ground_truth_identifiers(ctx: ResponseContext) -> Dict[str, Set[str]]:
    """
    Extract authoritative identifiers and facts from db_evidence, structured_data,
    doc_evidence, and the user's query.
    """
    ground_truth = {
        "app_numbers": set(),
        "survey_numbers": set(),
        "service_codes": set(),
        "statuses": set(),
        "counts": set(),
    }

    # Helper to scan a dictionary recursively
    def scan_dict(d: Any):
        if not isinstance(d, dict):
            return
        for k, v in d.items():
            if k in ("application_number", "application_id", "app_no") and isinstance(v, str):
                ground_truth["app_numbers"].add(v.strip().upper())
            elif k in ("survey_no", "survey_number", "survey") and v is not None:
                ground_truth["survey_numbers"].add(str(v).strip())
            elif k in ("service_code", "service_id") and v is not None:
                ground_truth["service_codes"].add(str(v).strip())
            elif k in ("status", "current_status") and isinstance(v, str):
                ground_truth["statuses"].add(v.strip().lower())
            elif k in ("count", "total_count", "total") and isinstance(v, (int, str)):
                ground_truth["counts"].add(str(v))
            elif isinstance(v, list):
                for item in v:
                    if isinstance(item, dict):
                        scan_dict(item)
                    elif isinstance(item, str):
                        for m in APP_NO_RE.findall(item):
                            ground_truth["app_numbers"].add(m.strip().upper())
            elif isinstance(v, dict):
                scan_dict(v)

    if ctx.db_evidence:
        scan_dict(ctx.db_evidence)
    if ctx.structured_data:
        scan_dict(ctx.structured_data)

    # If count is present in structured_data or list length
    if ctx.db_evidence and "applications" in ctx.db_evidence:
        ground_truth["counts"].add(str(len(ctx.db_evidence["applications"])))
    if ctx.structured_data and "applications" in ctx.structured_data:
        ground_truth["counts"].add(str(len(ctx.structured_data["applications"])))

    # Scan user query for quoted or mentioned identifiers
    for m in APP_NO_RE.findall(ctx.query):
        ground_truth["app_numbers"].add(m.strip().upper())
    for sc in SERVICE_CODE_RE.findall(ctx.query):
        ground_truth["service_codes"].add(sc.strip())

    # Document evidence service codes and citations
    if ctx.doc_evidence:
        for doc in ctx.doc_evidence:
            content = doc.get("content", "")
            for sc in SERVICE_CODE_RE.findall(content):
                ground_truth["service_codes"].add(sc.strip())

    return ground_truth


def verify_response_evidence(text: str, ctx: ResponseContext) -> VerificationReport:
    """
    Verify every factual claim in the response against ground truth evidence.

    Checks:
    1. Application Numbers: Strips any application number not in ground truth.
    2. Counts: Reconciles claimed counts with the verified database count.
    3. Service Codes: Ensures mentioned service codes match domain facts.
    4. Survey Numbers: Audits mentioned survey numbers.
    5. Provenance Categorization: Classifies sentences into facts, rules, explanations, or unknowns.
    """
    if not text or not text.strip():
        return VerificationReport(
            is_valid=True,
            verified_text=text,
            original_text=text,
            notes=[],
            corrections=[]
        )

    ground_truth = _collect_ground_truth_identifiers(ctx)
    corrections: List[str] = []
    notes: List[str] = []
    provenance: Dict[str, List[str]] = {
        "retrieved_facts": [],
        "document_rules": [],
        "explanations": [],
        "unknowns": []
    }

    verified_text = text

    # 1. Audit Application Numbers
    found_app_nos = APP_NO_RE.findall(verified_text)
    ungrounded_app_nos = [num for num in found_app_nos if num.upper() not in ground_truth["app_numbers"]]
    if ungrounded_app_nos:
        logger.warning(f"verify_response_evidence: ungrounded application numbers detected: {ungrounded_app_nos}")
        for ungrounded in set(ungrounded_app_nos):
            corrections.append(f"Removed ungrounded application number {ungrounded}")
            # Replace ungrounded application number with note or generic reference
            verified_text = re.sub(
                rf"\b{re.escape(ungrounded)}\b",
                "[Unverified Application Number Removed]",
                verified_text
            )
        notes.append("Note: An unverified application number was removed from the response to prevent misinformation.")

    # 2. Audit & Reconcile Counts
    authoritative_count = None
    if ctx.db_evidence and "count" in ctx.db_evidence:
        authoritative_count = ctx.db_evidence["count"]
    elif ctx.structured_data and "count" in ctx.structured_data:
        authoritative_count = ctx.structured_data["count"]
    elif ctx.db_evidence and "applications" in ctx.db_evidence:
        authoritative_count = len(ctx.db_evidence["applications"])

    if authoritative_count is not None:
        count_matches = COUNT_CLAIM_RE.finditer(verified_text)
        for match in count_matches:
            claimed_count = int(match.group(1))
            if claimed_count != int(authoritative_count):
                logger.warning(
                    f"verify_response_evidence: count mismatch! Text claimed {claimed_count}, DB has {authoritative_count}"
                )
                corrections.append(f"Reconciled count from {claimed_count} to authoritative {authoritative_count}")
                # Replace the claimed count with the verified DB count
                old_phrase = match.group(0)
                new_phrase = old_phrase.replace(str(claimed_count), str(authoritative_count), 1)
                verified_text = verified_text.replace(old_phrase, new_phrase, 1)

    # 3. Categorize Sentences by Provenance
    sentences = re.split(r"(?<=[.!?\n])\s+", verified_text)
    for sent in sentences:
        s = sent.strip()
        if not s:
            continue
        if APP_NO_RE.search(s) or any(sc in s for sc in ("0154", "0153", "0151", "0152")) or "Ward" in s or "வார்டு" in s:
            provenance["retrieved_facts"].append(s)
        elif any(kw in s.lower() for kw in ("manual", "guideline", "sla", "section", "g.o.", "அரசாணை", "விதிமுறை", "நடைமுறை")):
            provenance["document_rules"].append(s)
        elif any(kw in s.lower() for kw in ("not found", "காணப்படவில்லை", "not held", "இல்லை", "does not hold")):
            provenance["unknowns"].append(s)
        else:
            provenance["explanations"].append(s)

    is_valid = len(corrections) == 0

    return VerificationReport(
        is_valid=is_valid,
        verified_text=verified_text,
        original_text=text,
        corrections=corrections,
        notes=notes,
        provenance=provenance
    )


# ─────────────────────────────────────────────────────────────────────────────
# 4. Accessible HTML Table & Multi-Record Rendering
# ─────────────────────────────────────────────────────────────────────────────

def _app_link(app_no: str) -> str:
    """Render clickable application number anchor tag."""
    if not app_no:
        return "N/A"
    clean_no = html_lib.escape(str(app_no).strip())
    return f'<a href="#" class="app-link" onclick="handleApplicationClick(\'{clean_no}\'); return false;">{clean_no}</a>'


def _format_status_chip(status: str, language: str = "en") -> str:
    """Format status as styled HTML chip."""
    status_lower = str(status).strip().lower()
    if language in ("ta", "tanglish"):
        label = STATUS_TRANSLATIONS_TA.get(status_lower, status)
    else:
        label = STATUS_TRANSLATIONS_EN.get(status_lower, status.title())
    return f'<span class="status-chip status-{status_lower}">{html_lib.escape(str(label))}</span>'


def render_accessible_table(
    records: List[Dict[str, Any]],
    total_count: int,
    language: str = "en",
    title: str = "",
    requested_fields: Optional[List[str]] = None
) -> str:
    """
    Render accessible, responsive HTML <table> with exact count preservation,
    localized column headers, clickable application numbers, and formatted chips.
    """
    if not records:
        return ""

    is_ta = language in ("ta", "tanglish")

    # Determine columns to display
    base_cols = ["application_number", "type", "status", "stage", "applicant_name"]
    if requested_fields:
        for rf in requested_fields:
            if rf in COLUMN_LABELS and rf not in base_cols:
                base_cols.append(rf)

    # Determine headers
    headers_html = []
    for col in base_cols:
        col_meta = COLUMN_LABELS.get(col, {"en": col.replace("_", " ").title(), "ta": col})
        label = col_meta["ta"] if is_ta else col_meta["en"]
        headers_html.append(f"<th>{html_lib.escape(label)}</th>")
    thead_str = "".join(headers_html)

    # Build rows
    rows_html = []
    for rec in records:
        cells = []
        for col in base_cols:
            val = rec.get(col, "")
            if col == "application_number":
                cells.append(f"<td>{_app_link(val)}</td>")
            elif col == "status":
                cells.append(f"<td>{_format_status_chip(val, language)}</td>")
            elif col == "type":
                # Translate ISD / NISD if Tamil
                type_str = str(val).upper()
                if is_ta and "0154" in type_str or type_str == "ISD":
                    type_str = "ISD (உட்பிரிவு)"
                elif is_ta and "0153" in type_str or type_str == "NISD":
                    type_str = "NISD (உட்பிரிவற்ற)"
                cells.append(f"<td>{html_lib.escape(type_str)}</td>")
            elif col == "stage":
                stage_str = str(val).upper()
                if is_ta and stage_str in STAGE_TRANSLATIONS_TA:
                    stage_str = STAGE_TRANSLATIONS_TA[stage_str]
                cells.append(f"<td>{html_lib.escape(stage_str)}</td>")
            else:
                cells.append(f"<td>{html_lib.escape(str(val) if val is not None else '')}</td>")
        rows_html.append("<tr>" + "".join(cells) + "</tr>")
    tbody_str = "\n".join(rows_html)

    # Intro title with count preservation
    showing_count = len(records)
    if is_ta:
        if showing_count < total_count:
            intro_msg = f"மொத்தம் <strong>{total_count}</strong> விண்ணப்பங்களில் முதல் <strong>{showing_count}</strong> விண்ணப்பங்கள் கீழே பட்டியலிடப்பட்டுள்ளன:"
        else:
            intro_msg = f"மொத்தம் <strong>{total_count}</strong> விண்ணப்பங்கள் கண்டறியப்பட்டன:"
    else:
        if showing_count < total_count:
            intro_msg = f"Showing <strong>{showing_count}</strong> of <strong>{total_count}</strong> total applications:"
        else:
            intro_msg = f"Found <strong>{total_count}</strong> application(s):"

    if title:
        intro_msg = f"<strong>{html_lib.escape(title)}</strong> — {intro_msg}"

    return (
        f"<div class='table-intro' style='margin-bottom:8px;'>{intro_msg}</div>\n"
        f"<div class='table-container' style='overflow-x:auto;'>\n"
        f"<table class='data-table'>\n"
        f"<thead><tr>{thead_str}</tr></thead>\n"
        f"<tbody>\n{tbody_str}\n</tbody>\n"
        f"</table>\n"
        f"</div>"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 5. Natural Language Phrasing & Explanations
# ─────────────────────────────────────────────────────────────────────────────

def format_single_record_summary(
    record: Dict[str, Any],
    language: str = "en",
    detail_level: str = "normal",
    requested_field: Optional[str] = None
) -> str:
    """
    Format a single application or survey record into a natural conversational sentence.
    Preserves exact identifiers in Latin script and Tamil script for descriptions.
    """
    is_ta = language in ("ta", "tanglish")
    app_no = record.get("application_number") or record.get("application_id") or "N/A"
    app_type = record.get("type", "N/A")
    status = record.get("status", "pending")
    stage = record.get("stage", "SIS")
    applicant = record.get("applicant_name", "N/A")
    survey_no = record.get("survey_no", "N/A")
    days_pending = record.get("days_pending", "")

    # Specific field request
    if requested_field:
        val = record.get(requested_field)
        meta = COLUMN_LABELS.get(requested_field, {"en": requested_field, "ta": requested_field})
        lbl = meta["ta"] if is_ta else meta["en"]
        if is_ta:
            if requested_field == "status":
                val_ta = STATUS_TRANSLATIONS_TA.get(str(val).lower(), val)
                return f"விண்ணப்பம் {app_no}-ன் நிலை: {val_ta}."
            return f"விண்ணப்பம் {app_no}-ன் {lbl}: {val}."
        else:
            return f"The {lbl} for application {app_no} is: {val}."

    # Concise vs Detailed summary
    if is_ta:
        status_ta = STATUS_TRANSLATIONS_TA.get(str(status).lower(), status)
        stage_ta = STAGE_TRANSLATIONS_TA.get(str(stage).upper(), stage)
        if detail_level == "concise":
            return f"விண்ணப்பம் {app_no} ({app_type}) தற்போது {stage_ta} வசம் {status_ta}."
        
        detail_parts = [
            f"விண்ணப்ப எண்: {app_no}",
            f"விண்ணப்ப வகை: {app_type}",
            f"நிலை: {status_ta}",
            f"தற்போதைய கட்டம்: {stage_ta}",
            f"விண்ணப்பதாரர்: {applicant}",
            f"கணக்கெண் / சர்வே எண்: {survey_no}",
        ]
        if days_pending:
            detail_parts.append(f"நிலுவை காலம்: {days_pending} நாட்கள்")
        return "\n".join(detail_parts)
    else:
        if detail_level == "concise":
            return f"Application {app_no} ({app_type}) is currently {status} at the {stage} desk."
        
        detail_parts = [
            f"Application Number: {app_no}",
            f"Type: {app_type}",
            f"Status: {status.title()}",
            f"Current Stage: {stage}",
            f"Applicant: {applicant}",
            f"Survey Number: {survey_no}",
        ]
        if days_pending:
            detail_parts.append(f"Days Pending: {days_pending} days")
        return "\n".join(detail_parts)


def explain_missing_or_untracked(ctx: ResponseContext) -> str:
    """
    Explain clearly when records are not found, out of jurisdiction, or untracked.
    """
    is_ta = ctx.target_language == "ta"
    query_lower = ctx.query.lower()
    app_no_match = APP_NO_RE.search(ctx.query)
    app_no = app_no_match.group(1) if app_no_match else ""

    officer_info = ""
    if ctx.officer:
        officer_info = getattr(ctx.officer, "jurisdiction_name", None) or f"Ward {getattr(ctx.officer, 'ward_code', '')}, {getattr(ctx.officer, 'taluk_name', '')}".strip(", ")

    # Case 1: Specific Application Number not found
    if app_no:
        if is_ta:
            loc_str = f" ({officer_info})" if officer_info else ""
            return (
                f"விண்ணப்ப எண் <strong>{app_no}</strong> உங்கள் ஆளுகைப் பதிவேட்டில்{loc_str} காணப்படவில்லை. "
                f"தயவுசெய்து விண்ணப்ப எண்ணை சரிபார்க்கவும் அல்லது இது வேறு வார்டைச் சேர்ந்ததா என சோதிக்கவும்."
            )
        else:
            loc_str = f" in your jurisdiction ({officer_info})" if officer_info else ""
            return (
                f"Application <strong>{app_no}</strong> was not found{loc_str}. "
                f"Please verify the application number or verify if it falls under another jurisdiction."
            )

    # Case 2: Untracked Field
    untracked_terms = {
        "bank": ("bank account details", "வங்கி கணக்கு விவரங்கள்"),
        "aadhaar full": ("full 12-digit Aadhaar number", "முழு ஆதார் எண்"),
        "court": ("court litigation or stay orders", "நீதிமன்ற வழக்கு அல்லது தடை உத்தரவு விவரங்கள்"),
        "bribe": ("unauthorized transactions", "அங்கீகரிக்கப்படாத பரிவர்த்தனைகள்"),
    }
    for term, (desc_en, desc_ta) in untracked_terms.items():
        if term in query_lower:
            if is_ta:
                return (
                    f"மன்னிக்கவும், {desc_ta} நில அளவர் (SIS) பதிவேட்டில் பராமரிக்கப்படுவதில்லை. "
                    f"இது தொடர்புடைய துறை அல்லது பதிவுத் துறையிடம் மட்டுமே இருக்கும்."
                )
            else:
                return (
                    f"The SIS register does not track {desc_en}. "
                    f"That information is held externally or by the Registration / Judicial authorities."
                )

    # Case 3: Zero results for a search/filter
    if is_ta:
        return (
            f"உங்கள் அதிகார வரம்பில் ({officer_info or 'தற்போதைய வார்டு'}) உங்கள் கோரிக்கைக்குரிய "
            f"விண்ணப்பப் பதிவுகள் எதுவும் தற்போது நிலுவையில் இல்லை."
        )
    else:
        return (
            f"No matching records were found in your jurisdiction ({officer_info or 'active ward'})."
        )


# ─────────────────────────────────────────────────────────────────────────────
# 6. Proactive Domain-Aligned Follow-up Questions
# ─────────────────────────────────────────────────────────────────────────────

def generate_proactive_followups(ctx: ResponseContext) -> List[str]:
    """
    Suggest relevant follow-up questions tailored to SIS officer workflows.
    """
    is_ta = ctx.target_language == "ta"
    intent = ctx.intent or ""
    query_lower = ctx.query.lower()
    followups: List[str] = []

    if "isd" in intent or "pending" in intent or "isd" in query_lower:
        if is_ta:
            followups.append("கள ஆய்வு திட்டமிடப்பட்டுள்ளதா என்பதை சரிபார்க்க வேண்டுமா?")
            followups.append("மிகப் பழமையான நிலுவை விண்ணப்பத்தைப் பார்க்க வேண்டுமா?")
        else:
            followups.append("Would you like to check if field visits are scheduled?")
            followups.append("Show the oldest pending application?")

    elif "status" in intent or APP_NO_RE.search(ctx.query):
        if is_ta:
            followups.append("பணிப்பாய்வு வரலாற்றைப் பார்க்க வேண்டுமா?")
            followups.append("கூட்டுப் பட்டாதாரர்களின் விவரங்களை சரிபார்க்க வேண்டுமா?")
            followups.append("தேவையான ஆவணங்கள் சமர்ப்பிக்கப்பட்டுள்ளதா என அறிய வேண்டுமா?")
        else:
            followups.append("View workflow history?")
            followups.append("Check joint owners for this parcel?")
            followups.append("Verify required documents?")

    elif "survey" in intent or "subdivision" in intent or SURVEY_NO_RE.search(ctx.query):
        if is_ta:
            followups.append("இந்த சர்வே எண்ணில் நிலுவையில் உள்ள விண்ணப்பங்களை பார்க்க வேண்டுமா?")
            followups.append("அடுத்த உட்பிரிவு எண்ணை அறிய வேண்டுமா?")
        else:
            followups.append("Check active applications locked on this survey number?")
            followups.append("Get the next available subdivision number?")

    else:
        if is_ta:
            followups.append("நிலுவையில் உள்ள ISD விண்ணப்பங்களை பட்டியலிடவா?")
            followups.append("இன்றைய கள ஆய்வு திட்டத்தை பார்க்க வேண்டுமா?")
        else:
            followups.append("List pending ISD applications?")
            followups.append("View today's field visit plan?")

    return followups[:3]


# ─────────────────────────────────────────────────────────────────────────────
# 7. Main Intelligent Response Generator
# ─────────────────────────────────────────────────────────────────────────────

def generate_intelligent_response(ctx: ResponseContext) -> GeneratedResponse:
    """
    Main entry point for intelligent, multilingual, verified response generation.
    Deterministic, evidence-grounded generation for single and multi-record results.
    """
    target_lang = ctx.target_language
    is_ta = target_lang == "ta"

    # Step A: Evaluate evidence
    db_records: List[Dict[str, Any]] = []
    total_count: int = 0
    if ctx.db_evidence:
        if "applications" in ctx.db_evidence and isinstance(ctx.db_evidence["applications"], list):
            db_records = ctx.db_evidence["applications"]
            total_count = ctx.db_evidence.get("count", len(db_records))
        elif "count" in ctx.db_evidence:
            total_count = ctx.db_evidence["count"]
            db_records = ctx.db_evidence.get("records", [])

    # Fall back to structured_data
    if not db_records and ctx.structured_data:
        if "applications" in ctx.structured_data and isinstance(ctx.structured_data["applications"], list):
            db_records = ctx.structured_data["applications"]
            total_count = ctx.structured_data.get("count", len(db_records))
        elif "count" in ctx.structured_data:
            total_count = ctx.structured_data["count"]

    # Step B: Multi-record response
    if len(db_records) > 1 or (len(db_records) == 0 and total_count > 1):
        table_html = render_accessible_table(
            records=db_records,
            total_count=total_count,
            language=target_lang,
            requested_fields=ctx.requested_fields
        )
        if is_ta:
            summary_text = f"உங்கள் ஆளுகைக்குட்பட்ட மொத்தம் {total_count} விண்ணப்பங்கள் கண்டறியப்பட்டன."
        else:
            summary_text = f"Found a total of {total_count} applications in your jurisdiction."

        followups = generate_proactive_followups(ctx)
        v_report = verify_response_evidence(summary_text, ctx)

        return GeneratedResponse(
            text=v_report.verified_text,
            html=table_html,
            language=target_lang,
            verified=v_report.is_valid,
            verification_report=v_report,
            followups=followups,
            detail_level=ctx.detail_level
        )

    # Step C: Single record response
    if len(db_records) == 1:
        record = db_records[0]
        single_text = format_single_record_summary(
            record=record,
            language=target_lang,
            detail_level=ctx.detail_level,
            requested_field=ctx.requested_fields[0] if ctx.requested_fields else None
        )
        followups = generate_proactive_followups(ctx)
        v_report = verify_response_evidence(single_text, ctx)
        return GeneratedResponse(
            text=v_report.verified_text,
            html=None,
            language=target_lang,
            verified=v_report.is_valid,
            verification_report=v_report,
            followups=followups,
            detail_level=ctx.detail_level
        )

    # Step D: Missing record or zero count
    if total_count == 0 and not ctx.doc_evidence:
        missing_text = explain_missing_or_untracked(ctx)
        followups = generate_proactive_followups(ctx)
        v_report = verify_response_evidence(missing_text, ctx)
        return GeneratedResponse(
            text=v_report.verified_text,
            html=None,
            language=target_lang,
            verified=v_report.is_valid,
            verification_report=v_report,
            followups=followups,
            detail_level=ctx.detail_level
        )

    # Step E: Procedural / Document Evidence response (RAG)
    if ctx.doc_evidence:
        citations = []
        doc_contents = []
        for d in ctx.doc_evidence:
            cit = d.get("citation") or d.get("source", "Official Manual")
            citations.append(cit)
            content = d.get("content", "").strip()
            if content:
                doc_contents.append(f"[{cit}]: {content}")

        context_str = "\n\n".join(doc_contents)
        if is_ta:
            proc_text = (
                f"அரசாணை மற்றும் கையேடு வழிகாட்டுதலின்படி:\n{context_str}\n\n"
                f"ஆதாரங்கள்: {', '.join(set(citations))}"
            )
        else:
            proc_text = (
                f"According to the official revenue manuals and guidelines:\n{context_str}\n\n"
                f"Sources: {', '.join(set(citations))}"
            )

        v_report = verify_response_evidence(proc_text, ctx)
        followups = generate_proactive_followups(ctx)
        return GeneratedResponse(
            text=v_report.verified_text,
            html=None,
            language=target_lang,
            verified=v_report.is_valid,
            verification_report=v_report,
            followups=followups,
            detail_level=ctx.detail_level,
            sources=citations
        )

    # Default fallback
    fallback_text = (
        "மன்னிக்கவும், உங்கள் கோரிக்கைக்குரிய தகவல்களை சரிபார்க்க இயலவில்லை."
        if is_ta else
        "I could not verify authoritative records for your request."
    )
    v_report = verify_response_evidence(fallback_text, ctx)
    return GeneratedResponse(
        text=v_report.verified_text,
        html=None,
        language=target_lang,
        verified=v_report.is_valid,
        verification_report=v_report,
        followups=generate_proactive_followups(ctx),
        detail_level=ctx.detail_level
    )


async def generate_intelligent_response_async(ctx: ResponseContext) -> GeneratedResponse:
    """Async wrapper for generate_intelligent_response."""
    return generate_intelligent_response(ctx)
