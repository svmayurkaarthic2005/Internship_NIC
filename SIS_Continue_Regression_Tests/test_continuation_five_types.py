"""SIS Chatbot — Continuation Types 1 to 5 Verified Regression Test Suite
Tests that all five distinct continuation types operate reliably with context awareness:
Type 1 — Resume an incomplete answer (unblocked by anti-hallucination, no recall hijack)
Type 2 — Expand a completed answer (service code preservation, expansion context recognized)
Type 3 — Continue a sequence or result set (database records / pagination / relative slicing)
Type 4 — Continue an underlying process or workflow ('What comes next?' relative stage progression)
Type 5 — Continue a multi-turn task (file extraction, CSV relative row cursors, range lookups)
"""
from __future__ import annotations

import sys
import os

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, r"C:\proj\nic_internship")

from backend.services.rag import parse_intent
from backend.services.chatbot import (
    _is_conversation_recall,
    _no_evidence_for_llm,
    _domain_evidence_tier,
    _workflow_steps_topic,
)
from backend.services import followup_context as fctx
from backend.services import csv_ops
from backend.config import settings

_results = []

def check(name, cond, detail=""):
    _results.append((name, bool(cond), detail))
    print(f"{'ok  ' if cond else 'FAIL'} {name}" + (f" -- {detail}" if detail and not cond else ""))

def test_type1_resume_incomplete():
    # Type 1: Resume an incomplete answer
    ctx = fctx.FollowupContext(entity=fctx.ENTITY_APPLICATION, application_numbers=["2026/0154/28/000001"])
    # 1. 'continue' with context is NOT blocked by _no_evidence_for_llm
    is_blocked = _no_evidence_for_llm("continue", "general_query", None, ctx=ctx)
    check("Type 1: 'continue' is NOT blocked by _no_evidence_for_llm", not is_blocked)
    # 2. 'resume' is recognized as context
    tier = _domain_evidence_tier("resume", ctx)
    check("Type 1: 'resume' tier is 'context'", tier == "context")
    # 3. Continuation idioms are NOT hijacked by conversation recall
    check("Type 1: 'continue from where we left off' is not intercepted as recall",
          not _is_conversation_recall("continue from where we left off"))
    check("Type 1: 'let's continue' is not intercepted as recall",
          not _is_conversation_recall("let's continue"))

def test_type2_expand_completed():
    # Type 2: Expand a completed answer
    # Disambiguation: "more details" after service_code_lookup stays in service_code domain
    intent_sc = parse_intent("more details", prev_intent="service_code_lookup")
    check("Type 2: 'more details' after service code stays service_code_lookup", intent_sc == "service_code_lookup")

    intent_app = parse_intent("more details", prev_intent="application_status")
    check("Type 2: 'more details' after application stays application_status", intent_app == "application_status")

    # Follow-up expansion context recognized
    ctx_app = fctx.FollowupContext(entity=fctx.ENTITY_APPLICATION, application_numbers=["2026/0154/28/000001"])
    tier_more = _domain_evidence_tier("more details", ctx_app)
    check("Type 2: 'more details' tier is 'context'", tier_more == "context")

def test_type3_continue_sequence_pagination():
    # Type 3: Continue a sequence or result set
    apps = [f"2026/0154/28/{i:06d}" for i in range(1, 26)]
    ctx_list = fctx.FollowupContext(
        entity=fctx.ENTITY_APPLICATION_LIST,
        application_numbers=apps,
        filters={"last_offset": 0, "page_size": 5}
    )
    # 1. "next" advances pagination chunk (items 5..10)
    res_next = fctx.resolve("next", ctx_list)
    check("Type 3: 'next' advances pagination chunk (5 records)", res_next.resolved and len(res_next.application_numbers) == 5)

    # 2. "next five" resolves relative slice (items 10..15)
    res_next5 = fctx.resolve("next five", ctx_list)
    check("Type 3: 'next five' resolves relative slice (5 records)", res_next5.resolved and len(res_next5.application_numbers) == 5)

    # 3. "show the remaining records" resolves all remaining 10 records (items 15..25)
    res_rem = fctx.resolve("show the remaining records", ctx_list)
    check("Type 3: 'show the remaining records' resolves remainder", res_rem.resolved and len(res_rem.application_numbers) == 10)

    # 4. Graceful termination when all records are exhausted
    res_end = fctx.resolve("next", ctx_list)
    check("Type 3: pagination gracefully completes at end", res_end.resolved and len(res_end.application_numbers) == 0 and "already been shown" in (res_end.clarification or ""))

def test_type4_continue_workflow():
    # Type 4: Continue an underlying process or workflow ('What comes next?')
    # After SIS -> SD
    hist_sis = [{"role": "assistant", "content": "SIS completes the field inspection and prepares subdivision sketch."}]
    check("Type 4: After SIS, relative next step maps to AFTER:sis",
          _workflow_steps_topic("What comes next?", hist_sis) == "AFTER:sis")

    # After SD -> DIS
    hist_sd = [{"role": "assistant", "content": "Senior Draughtsman verifies and prepares the SD sketch."}]
    check("Type 4: After SD, relative next step maps to AFTER:sd",
          _workflow_steps_topic("what is the next step?", hist_sd) == "AFTER:sd")

    # After DIS -> Tahsildar
    hist_dis = [{"role": "assistant", "content": "Deputy Inspector of Survey (DIS) performs technical scrutiny."}]
    check("Type 4: After DIS, relative next step maps to AFTER:dis",
          _workflow_steps_topic("What comes next?", hist_dis) == "AFTER:dis")

    # Without workflow context -> asks clarification
    check("Type 4: Without workflow context, prompts CLARIFY_WORKFLOW",
          _workflow_steps_topic("What comes next?", []) == "CLARIFY_WORKFLOW")

def test_type5_continue_multiturn_task():
    # Type 5: Continue a multi-turn task (CSV and attachment operations)
    headers = ["id", "survey_no", "area", "status"]
    rows = [
        {"id": "1", "survey_no": "101", "area": "500", "status": "pending"},
        {"id": "2", "survey_no": "102", "area": "600", "status": "approved"},
        {"id": "3", "survey_no": "103", "area": "700", "status": "rejected"},
        {"id": "4", "survey_no": "104", "area": "800", "status": "pending"},
        {"id": "5", "survey_no": "105", "area": "900", "status": "approved"},
    ]

    # Relative next single row
    op_next = csv_ops.parse_csv_question("next row", headers, rows, last_row=2)
    check("Type 5: 'next row' from cursor 2 yields row 3", op_next is not None and op_next.row_number == 3)

    # Relative next multiple rows
    op_next3 = csv_ops.parse_csv_question("next 2 rows", headers, rows, last_row=1)
    check("Type 5: 'next 2 rows' yields rows [2, 3]", op_next3 is not None and op_next3.row_numbers == [2, 3])

    # Continuation command
    op_cont = csv_ops.parse_csv_question("continue", headers, rows, last_row=3)
    check("Type 5: 'continue' from cursor 3 yields row 4", op_cont is not None and op_cont.row_number == 4)

    # Remainder
    op_rest = csv_ops.parse_csv_question("show the rest", headers, rows, last_row=2)
    check("Type 5: 'show the rest' yields remaining rows [3, 4, 5]", op_rest is not None and op_rest.row_numbers == [3, 4, 5])

if __name__ == "__main__":
    print("=== SIS Continuation Types 1 to 5 Tests ===")
    test_type1_resume_incomplete()
    test_type2_expand_completed()
    test_type3_continue_sequence_pagination()
    test_type4_continue_workflow()
    test_type5_continue_multiturn_task()
    total = len(_results)
    passed = sum(1 for _, ok, _ in _results if ok)
    failed = total - passed
    print(f"\nSummary: {passed}/{total} checks passed ({failed} failed).")
    sys.exit(0 if failed == 0 else 1)
