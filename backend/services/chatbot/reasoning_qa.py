"""Evidence-grounded reasoning for explanatory questions (P1-2).

The gap this closes: a question asking what a record MEANS or what can
safely be CONCLUDED from it ("does a populated field_visit_dt mean an
inspection happened?", "does an Approved desk remark mean the application
is closed?") used to be swallowed by whichever single-field handler
recognised one keyword in it (appinfo_qa's field map, fulltransfer_qa,
cmflow_qa, ...) and answered with that one field alone -- never the
comparison across sources the question actually asked for, and never the
limitation on what the data proves.

This module does NOT replace those handlers. A direct field lookup
("what is the application status?") still answers from them, because
`is_reasoning_question()` only fires on an explicit explanatory shape
(does/mean/prove/mandatory/conclude/discrepancy/not necessarily/missing
evidence/"explain the difference", combined with a word naming a real
source: register, workflow, remark, recommend, owner, record, field,
column, document, visit, date, status). Database retrieval still
establishes every fact (`gather_evidence()`, no LLM involved, fully
testable on its own); the LLM is used only to explain the relationship
between facts already in the package and to state what the package does
NOT establish -- it is never the source of a fact, a date, a document name
or a conclusion of closure/approval/mandate.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from backend.utils.logger import get_logger
from backend.services.rag_llm import call_llama
from backend.services.postgres import get_application_detail, get_workflow_remarks
from . import fulltransfer_qa as _fulltransfer_qa
from . import cmflow_qa as _cmflow_qa
from . import appinfo_qa as _appinfo_qa
from . import oldowner_qa as _oldowner_qa
from . import igrs_owner_qa as _igrs_owner_qa
from . import newowner_qa as _newowner_qa

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# A. Recognising a genuine reasoning / explanation question
# ---------------------------------------------------------------------------

# A question-SHAPE cue: it asks what a fact means, proves or establishes,
# not what the fact IS. A bare "what is the status" matches none of these.
_REASON_CUE = re.compile(
    r"does\s+(?:that|this|it|a|the)\b.*\b(?:mean|prove|establish|require|mandat)"
    r"|is\s+(?:that|this|it)\s+(?:proof|evidence|enough)"
    r"|what\s+can\s+(?:we|i|you)\s+conclude"
    r"|what\s+does\s+.*\b(?:establish|show|mean|prove)"
    r"|not\s+necessarily\s+mean"
    r"|why\s+(?:does|should|is|are|would|do)\b.*\bnot\b"
    r"|why\s+should\s+.*\bnot\b"
    r"|explain\s+the\s+(?:difference|discrepancy)"
    r"|explain\s+what\s+each\b"
    r"|what\s+(?:information|evidence)\s+is\s+(?:still\s+)?missing"
    r"|what\s+exactly\s+was\s+missing"
    r"|what\s+cannot\s+be\s+concluded",
    re.IGNORECASE,
)

# A word naming a real evidentiary source. Without one of these the message
# is a bare definitional/comparison question ("difference between ISD and
# NISD"), which stays with service_code_guide -- never this module.
_EVIDENCE_WORD = re.compile(
    r"\b(register|workflow|remark|recommend\w*|record|table|row|field|column|"
    r"date|status|approv\w*|reject\w*|closed?|closure|owner|subdivision|"
    r"sub-?division|patta|document|evidence|source|transition|desk|"
    r"inspection|visit\w*|complet\w*|missing)\b",
    re.IGNORECASE,
)


def is_reasoning_question(message: str) -> bool:
    """True only for a genuine explanatory/evidence question -- never a
    direct field lookup, which has no reasoning cue to match at all."""
    msg = message or ""
    return bool(_REASON_CUE.search(msg) and _EVIDENCE_WORD.search(msg))


# ---------------------------------------------------------------------------
# C. Evidence package -- pure data assembly, no LLM, fully testable alone
# ---------------------------------------------------------------------------

_MAX_WORKFLOW_HOPS = 10
_MAX_OWNER_ROWS = 5

_DESK_NAME = {"1": "Submission", "41": "Sub Inspector Surveyor", "42": "Sub Inspector Surveyor",
              "44": "Sub Inspector Surveyor", "8": "Senior Draughtsman (SD)",
              "12": "Deputy Inspector Surveyor (DIS)", "16": "Zonal Level Tahsildar",
              "59": "Zonal/District Revenue Officer", "53": "Zonal/District Revenue Officer",
              "0": "Completed"}


def _desk(code: Optional[str]) -> str:
    code = str(code) if code is not None else ""
    return _DESK_NAME.get(code, f"desk {code}" if code else "unknown desk")


def _blank(v: Any) -> bool:
    if v is None:
        return True
    s = str(v).strip()
    return s in ("", "-", "0", "0.00") or s.lower() in ("none", "null")


def _redact_owner_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """Drop mobile numbers, addresses and ID numbers; keep only what the
    reasoning layer may compare (name, relation, patta, share, extent,
    presence flags) -- never a value that identifies the person further."""
    _DROP = {"mobile_number", "address", "epic_no", "ration_card_no", "pincode",
              "aadhar_no", "owner_img", "photo", "user_no", "cin_no"}
    return {k: v for k, v in row.items() if k not in _DROP}


async def _workflow_rows(db, app_no: str) -> Dict[str, Any]:
    """Raw application_workflow hops, including the field_visit_dt column no
    other handler surfaces -- the one Scenario A's question is actually
    about. Distinct from get_workflow_remarks(), which reads the same table
    but drops that column."""
    from sqlalchemy import text
    try:
        async with db.begin_nested():
            rows = (await db.execute(
                text("SELECT action_from, action_to, action_date, remarks, recommend, field_visit_dt "
                     "FROM application_workflow WHERE appl_id = :a ORDER BY action_date, slno"),
                {"a": app_no})).mappings().all()
    except Exception as exc:
        logger.warning(f"reasoning_qa: could not read application_workflow ({type(exc).__name__})")
        return {"table": "application_workflow", "status": "not_queried", "hops": []}
    if not rows:
        return {"table": "application_workflow", "status": "no_matching_row", "hops": []}
    hops = []
    for r in list(rows)[-_MAX_WORKFLOW_HOPS:]:
        hops.append({
            "from": _desk(r.get("action_from")), "to": _desk(r.get("action_to")),
            "date": r["action_date"].isoformat() if r.get("action_date") else None,
            "remark": None if _blank(r.get("remarks")) else str(r.get("remarks")),
            "recommend": None if _blank(r.get("recommend")) else str(r.get("recommend")),
            "field_visit_dt": None if _blank(r.get("field_visit_dt")) else str(r.get("field_visit_dt")),
        })
    return {"table": "application_workflow", "status": "present", "hops": hops}


async def _is_accessible(db, officer, app_no: str, detail: Dict[str, Any]) -> bool:
    """Whether `officer` may be shown anything about `app_no` at all.

    `applications` (the ORM table `get_application_detail()` reads) only
    ever admits service codes 0153/0154 -- for any other code `detail` is
    always `{"found": False}` whatever the officer's jurisdiction, so
    `detail["found"]` alone cannot be the gate. For those codes jurisdiction
    is instead checked the same way `get_all_service_code_applications()`
    does it: `service_register_entries.ward_id` against the officer's own
    `jurisdiction_ids`. Fails closed: no officer, no jurisdiction rows, or
    no matching scoped row all mean "not accessible"."""
    if not officer or not getattr(officer, "jurisdiction_ids", None):
        # No officer, or no jurisdiction rows at all: deny rather than trust
        # a downstream query that may treat an empty filter list as "no
        # filter" and return the row unscoped (get_application_detail does
        # exactly this -- see lookup_application_access's own comment,
        # "No jurisdiction rows configured -- deny rather than leak").
        return False
    code = app_no.split("/")[1] if app_no.count("/") >= 3 else ""
    if code in ("0153", "0154"):
        return bool(detail.get("found"))
    from backend.models import ServiceRegisterEntry
    from sqlalchemy import select
    try:
        async with db.begin_nested():
            row = (await db.execute(
                select(ServiceRegisterEntry.id).where(
                    ServiceRegisterEntry.application_number == app_no,
                    ServiceRegisterEntry.ward_id.in_(officer.jurisdiction_ids))
            )).first()
    except Exception as exc:
        logger.warning(f"reasoning_qa: jurisdiction check failed ({type(exc).__name__})")
        return False
    return row is not None


async def gather_evidence(db, officer, app_no: str) -> Dict[str, Any]:
    """The structured, source-attributed evidence package for one
    application. Every item says which table it came from and whether that
    table held a matching row at all (never collapsed into one generic
    'not recorded' the way a blank field and a missing row would otherwise
    read the same). No LLM call happens here.

    Fails closed on jurisdiction: an application the officer cannot see is
    reported as inaccessible, with every other table left unqueried -- the
    owner/transfer/correction-flow loaders below have no jurisdiction check
    of their own (same as every other `_qa.py` module), so this function is
    the one place that must refuse before calling any of them."""
    detail = await get_application_detail(db, app_no, officer=officer)
    code = app_no.split("/")[1] if app_no.count("/") >= 3 else ""
    if not await _is_accessible(db, officer, app_no, detail):
        return {"application_number": app_no, "service_code": code, "accessible": False, "sources": []}
    sources: List[Dict[str, Any]] = []

    sources.append({
        "table": "applications",
        "status": "present" if detail.get("found") else "no_matching_row",
        "fields": ({
            "application_type": detail.get("type") or detail.get("application_type"),
            "service_code": code,
            "current_status": detail.get("current_status") or detail.get("status"),
            "decision_date": detail.get("decision_date"),
            "decision_stage": detail.get("decision_stage"),
        } if detail.get("found") else {}),
    })

    sources.append(await _workflow_rows(db, app_no))

    wf_remarks = await get_workflow_remarks(db, officer, [app_no]) if officer else {"apps": []}
    wf_app = next((a for a in wf_remarks.get("apps", []) if a.get("application_number") == app_no), None)
    sources.append({
        "table": "workflow_history (closing remark / rejection reason)",
        "status": "present" if wf_app else "no_matching_row",
        "fields": {"reason": wf_app.get("reason")} if wf_app else {},
    })

    cm_row = await _cmflow_qa.load(db, app_no)
    sources.append({
        "table": "correction_modification_flow",
        "status": "present" if cm_row else ("not_applicable" if code in ("0153", "0154") else "no_matching_row"),
        "fields": ({k: v for k, v in cm_row.items()
                    if k.endswith(("_rec", "_remarks")) or k in ("govt_change_flag", "priv_change_flag")}
                   if cm_row else {}),
    })

    ai_row = await _appinfo_qa.load(db, app_no)
    sources.append({
        "table": ("sub_div_patta_transfer_application_information_urban" if code == "0154"
                  else "full_field_patta_transfer_application_information" if code == "0153"
                  else "f_line_service_application_information_urban" if code == "0178" else "application_information"),
        "status": "present" if ai_row else "no_matching_row",
        "fields": ({
            "missing_docs": None if _blank(ai_row.get("missing_docs")) else ai_row.get("missing_docs"),
            "missing_docs_blank": _blank(ai_row.get("missing_docs")),
            "enclosures": None if _blank(ai_row.get("enclosures")) else ai_row.get("enclosures"),
        } if ai_row else {}),
    })

    ft_rows = await _fulltransfer_qa.load(db, app_no) if code == "0153" else []
    ft_row = ft_rows[0] if ft_rows else None
    sources.append({
        "table": "full_field_patta_transfer_urban",
        "status": "present" if ft_row else ("not_applicable" if code != "0153" else "no_matching_row"),
        "fields": ({
            "reason": None if _blank(ft_row.get("reason")) else ft_row.get("reason"),
            "zdt_remarks": None if _blank(ft_row.get("zdt_remarks")) else ft_row.get("zdt_remarks"),
            "transaction_status": None if _blank(ft_row.get("transaction_status")) else ft_row.get("transaction_status"),
        } if ft_row else {}),
    })

    # Owner-record family: which table is the CORRECT one for this file's
    # type is decided here, not assumed -- an ISD file's old owner is the
    # chitta record, an NISD file's is the full-field one; they never both
    # carry rows for the same application (CLAUDE.md, "Old-owner record").
    if code == "0154":
        old_rows = await _oldowner_qa.load(db, app_no)
        old_table = "chitta_temp_old_owner"
    elif code == "0153":
        old_rows = await _newowner_qa.load_old(db, app_no)
        old_table = "full_field_patta_transfer_old_owner"
    else:
        old_rows, old_table = [], "old_owner (no source defined for this service code)"
    sources.append({
        "table": old_table,
        "status": "present" if old_rows else "no_matching_row",
        "row_count": len(old_rows),
        "rows": [_redact_owner_row(dict(r)) for r in old_rows[:_MAX_OWNER_ROWS]],
    })

    igrs_rows = await _igrs_owner_qa.load(db, app_no)
    sources.append({
        "table": "full_field_patta_transfer_igrs_owner",
        "status": "present" if igrs_rows else "no_matching_row",
        "row_count": len(igrs_rows),
        "rows": [_redact_owner_row(dict(r)) for r in igrs_rows[:_MAX_OWNER_ROWS]],
    })

    return {"application_number": app_no, "service_code": code, "sources": sources}


# ---------------------------------------------------------------------------
# Deterministic rendering -- "Recorded facts" and "Comparison across
# sources" come straight from the evidence package, no LLM, nothing added.
# ---------------------------------------------------------------------------

_PROJECT_RULES = {
    "nisd_no_visit": (
        "Project rule: NISD (service code 0153, Not Involving Sub-Division) is a straight patta "
        "transfer verified on documents alone; it is ISD (0154) that requires the field inspection "
        "and SD/DIS sketch stage. (source: CLAUDE.md, Workflow / Application Types)"
    ),
    "desk_vs_closure": (
        "Project rule: an application's current_status becomes approved/rejected only when a "
        "workflow_history hop records to_stage = COMPLETED or REJECTED. A desk's own recommendation "
        "or remark (e.g. a Tahsildar 'Approved' note in correction_modification_flow) records that "
        "desk's opinion, not the register's final closure. (source: CLAUDE.md, \"When a completed "
        "application was decided\")"
    ),
    "owner_tables_distinct": (
        "Project rule: for an NISD file, full_field_patta_transfer_old_owner records the owner(s) "
        "tied to the deed being transferred, and full_field_patta_transfer_igrs_owner records the "
        "owner(s) named on a Sub-Registrar (IGRS) referral for the same file -- two different stages "
        "of the same transfer, populated independently. A different row count between them reflects "
        "how many owners each source names, not a disagreement about who owns the land. "
        "(source: CLAUDE.md, \"IGRS owner records\" / \"Old-owner record\")"
    ),
}


def _fmt_source(src: Dict[str, Any]) -> List[str]:
    lines = []
    tbl = src["table"]
    if src["status"] == "no_matching_row":
        lines.append(f"- {tbl}: no matching row for this application (table was queried; nothing came back).")
    elif src["status"] == "not_applicable":
        lines.append(f"- {tbl}: not applicable to this application's service code.")
    elif src["status"] == "not_queried":
        lines.append(f"- {tbl}: could not be queried this turn.")
    elif tbl == "application_workflow":
        if not src["hops"]:
            lines.append(f"- {tbl}: no workflow hops recorded.")
        for h in src["hops"]:
            fv = f", field_visit_dt={h['field_visit_dt']}" if h["field_visit_dt"] else ", field_visit_dt: blank"
            rec = f", recommend={h['recommend']}" if h["recommend"] else ""
            rem = f", remark=\"{h['remark']}\"" if h["remark"] else ", remark: blank"
            lines.append(f"- {tbl}: {h['from']} -> {h['to']} on {h['date']}{rec}{rem}{fv}")
    elif "rows" in src:
        if src["row_count"] == 0:
            lines.append(f"- {tbl}: no matching row for this application.")
        else:
            lines.append(f"- {tbl}: {src['row_count']} row(s) on record.")
            for i, r in enumerate(src["rows"], 1):
                present = [f"{k}={v}" for k, v in r.items() if v not in (None, "", "-") and k not in ("row_id",)]
                lines.append(f"  row {i}: " + ", ".join(present[:8]))
    else:
        fields = src.get("fields") or {}
        if not fields:
            lines.append(f"- {tbl}: row present but no relevant field read.")
        for k, v in fields.items():
            if k.endswith("_blank"):
                continue
            if v in (None, ""):
                lines.append(f"- {tbl}.{k}: blank (the column exists and was read; it carries no value).")
            else:
                lines.append(f"- {tbl}.{k}: {v}")
    return lines


def render_recorded_facts(evidence: Dict[str, Any], rules: List[str]) -> str:
    lines = [f"Application {evidence['application_number']} (service code {evidence['service_code']}):"]
    for src in evidence["sources"]:
        lines.extend(_fmt_source(src))
    for key in rules:
        if key in _PROJECT_RULES:
            lines.append(f"- {_PROJECT_RULES[key]}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# D. Grounded answer generation -- the LLM explains, it does not supply facts
# ---------------------------------------------------------------------------

_GEN_SYSTEM = (
    "You are explaining Tamil Nadu land-register evidence to a Sub Inspector Surveyor. "
    "You are given a fixed list of RECORDED FACTS, each tagged with the database table (or "
    "project rule) it came from. Using ONLY those facts:\n"
    "1. State what the facts, taken together, actually establish.\n"
    "2. State plainly what they do NOT establish -- do not let a populated value stand in for "
    "a conclusion it does not prove.\n"
    "3. If a fact is missing or blank, say so as a gap, never as a signal that the officer should "
    "read as a value.\n\n"
    "Never invent a document name, a date, a visit, an approval, a closure, an owner's identity "
    "or a legal conclusion that is not in the RECORDED FACTS below. Never restate a mobile number, "
    "address or ID number -- none is given to you, and none should appear in your answer. If the "
    "facts given are not enough to answer the question, say exactly that instead of guessing.\n\n"
    "RECORDED FACTS:\n{facts}\n\nQUESTION: {question}\n\n"
    "Reply with two short sections only, 'Explanation:' and 'What cannot be concluded:', each a "
    "few sentences, grounded only in the facts above."
)


async def answer(db, officer, message: str, app_no: str, language: str,
                  rule_keys: Optional[List[str]] = None,
                  llm_call=None) -> str:
    """Build the evidence package, render the deterministic sections, and
    ask the (already-configured) LLM for the explanation/limits sections
    only. `llm_call` lets tests substitute a double for `call_llama`."""
    evidence = await gather_evidence(db, officer, app_no)
    if evidence.get("accessible") is False:
        # Fail closed, no LLM call: the officer is told nothing about the
        # application beyond the refusal itself (same wording the rest of
        # the pipeline uses for an out-of-jurisdiction number).
        return (f"I do not have access to {app_no} in your jurisdiction, so I cannot "
                "explain anything about its records.")
    rules = rule_keys or []
    facts_text = render_recorded_facts(evidence, rules)
    caller = llm_call or call_llama
    try:
        llm_text = await caller(_GEN_SYSTEM.format(facts=facts_text, question=message))
    except Exception as exc:
        logger.warning(f"reasoning_qa: LLM explanation failed ({type(exc).__name__})")
        llm_text = ("Explanation: unavailable right now.\n"
                     "What cannot be concluded: the explanation could not be generated; "
                     "the recorded facts above are unaffected.")
    return f"Recorded facts:\n{facts_text}\n\n{llm_text}"
