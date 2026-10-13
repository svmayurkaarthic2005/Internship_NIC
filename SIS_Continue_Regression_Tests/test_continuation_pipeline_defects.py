"""SIS Chatbot — Continuation Pipeline Regression Test Suite
Verifies that all confirmed defects discovered during the Continuation & Follow-up Audit are FIXED:
1. Recall Interception Fix: 'continue where we left off', 'let's continue', 'continue our conversation'
   are no longer intercepted by _conversation_recall_reply.
2. Anti-Hallucination Gate Fix: 'continue', 'continue?', 'go on', 'resume' with active context are
   recognized by _domain_evidence_tier and NOT blocked by _no_evidence_for_llm.
3. Pagination / Slice Resolution Fix: 'next five', 'next 5 rows', 'show the rest' are properly handled
   by _SLICE_RE and relative slicing in followup_context.py.
4. Workflow Continuation Fix: 'What comes next?' / 'what is the next step?' maps to relative workflow
   progression (SIS -> SD -> DIS -> Tahsildar).
5. Attachment / CSV Relative Cursor Fix: 'next row', 'next 5 rows', 'continue', 'show the rest' are
   parsed with relative cursor offsets in csv_ops.py.
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

_results: list[tuple[str, bool, str]] = []

def check(name: str, cond: bool, detail: str = ""):
    _results.append((name, bool(cond), detail))
    status = "ok  " if cond else "FAIL"
    print(f"{status} {name}" + (f" -- {detail}" if detail and not cond else ""))

def test_recall_interception():
    # Verified fix: Continuation idioms are NOT treated as conversation recall queries
    for phrase in [
        "continue where we left off",
        "let's continue",
        "continue our conversation",
        "continue from before",
        "resume our conversation",
        "pick up where we left off",
        "தொடர்ந்து பேசுவோம்",
        "நிறுத்திய இடத்திலிருந்து",
    ]:
        is_rec = _is_conversation_recall(phrase)
        check(f"Recall does NOT intercept '{phrase}'", not is_rec, f"is_rec={is_rec}")

    # Ensure genuine recall queries STILL work
    for rec_phrase in [
        "what was my last question?",
        "repeat my previous message",
        "what did I ask earlier?",
        "நான் கடைசியாக என்ன கேட்டேன்?",
    ]:
        check(f"Genuine recall preserved for '{rec_phrase}'", _is_conversation_recall(rec_phrase))

def test_anti_hallucination_gate_with_context():
    # Verified fix: When context is active, continuation prompts are recognized and NOT blocked
    ctx = fctx.FollowupContext(entity=fctx.ENTITY_APPLICATION, application_numbers=["2026/0154/28/000001"])
    for phrase in ["continue", "continue?", "go on", "resume", "thodaru", "தொடரவும்."]:
        tier = _domain_evidence_tier(phrase, ctx)
        no_ev = _no_evidence_for_llm(phrase, "general_query", None, ctx=ctx)
        check(f"Domain tier recognizes '{phrase}' as context", tier == "context", f"tier={tier}")
        check(f"'_no_evidence_for_llm' allows '{phrase}' with context", no_ev is False, f"no_ev={no_ev}")

def test_slice_re_pagination():
    # Verified fix: Relative slicing ('next five', 'next 5', 'show the rest') resolves properly
    apps = [f"2026/0154/28/{i:06d}" for i in range(1, 21)]
    ctx = fctx.FollowupContext(
        entity=fctx.ENTITY_APPLICATION_LIST,
        application_numbers=apps,
        filters={"page_offset": 5, "page_size": 5}
    )

    s_next5 = fctx.slice_pick("next five", apps, offset=5)
    check("'next five' slices 5 items from offset 5", s_next5 is not None and len(s_next5) == 5 and s_next5[0] == apps[5])

    s_next_num = fctx.slice_pick("next 3", apps, offset=10)
    check("'next 3' slices 3 items from offset 10", s_next_num is not None and len(s_next_num) == 3 and s_next_num[0] == apps[10])

    s_rest = fctx.slice_pick("show the rest", apps, offset=15)
    check("'show the rest' slices remaining items", s_rest is not None and len(s_rest) == 5 and s_rest[-1] == apps[-1])

    # Slicing via fctx.resolve
    res_next = fctx.resolve("next", ctx)
    check("'next' on application list paginates chunk", res_next.resolved and len(res_next.application_numbers) == 5)

    # After 'next', page_offset is now 10, so 'show the rest' resolves the remaining 10 items
    res_rest = fctx.resolve("show the rest", ctx)
    check("'show the rest' on application list resolves remainder", res_rest.resolved and len(res_rest.application_numbers) == 10)

def test_workflow_continuation_progression():
    # Verified fix: Relative workflow inquiry resolves stages based on context
    hist_sis = [
        {"role": "user", "content": "Tell me about the SIS step for ISD"},
        {"role": "assistant", "content": "The Sub Inspector of Survey conducts field inspection and prepares the sketch."}
    ]
    prog_sis = _workflow_steps_topic("What comes next?", hist_sis)
    check("After SIS, next step maps to AFTER:sis", prog_sis == "AFTER:sis")

    hist_sd = [
        {"role": "user", "content": "What does SD do?"},
        {"role": "assistant", "content": "SD verifies the field subdivision sketch."}
    ]
    prog_sd = _workflow_steps_topic("what is the next step?", hist_sd)
    check("After SD, next step maps to AFTER:sd", prog_sd == "AFTER:sd")

    hist_dis = [
        {"role": "user", "content": "What does DIS do?"},
        {"role": "assistant", "content": "DIS scrutiny approves the subdivision boundaries."}
    ]
    prog_dis = _workflow_steps_topic("What comes next?", hist_dis)
    check("After DIS, next step maps to AFTER:dis", prog_dis == "AFTER:dis")

def test_csv_ops_relative_cursor():
    # Verified fix: csv_ops handles relative cursors and ranges
    headers = ["id", "survey_no", "area"]
    rows = [{"id": "1", "survey_no": "101", "area": "500"},
            {"id": "2", "survey_no": "102", "area": "600"},
            {"id": "3", "survey_no": "103", "area": "700"},
            {"id": "4", "survey_no": "104", "area": "800"},
            {"id": "5", "survey_no": "105", "area": "900"}]

    op_next = csv_ops.parse_csv_question("next row", headers, rows, last_row=2)
    check("'next row' resolves to row 3", op_next is not None and op_next.row_number == 3)

    op_next_many = csv_ops.parse_csv_question("next 2 rows", headers, rows, last_row=1)
    check("'next 2 rows' resolves to rows [2, 3]", op_next_many is not None and op_next_many.row_numbers == [2, 3])

    op_cont = csv_ops.parse_csv_question("continue", headers, rows, last_row=3)
    check("'continue' resolves to row 4", op_cont is not None and op_cont.row_number == 4)

    op_rest = csv_ops.parse_csv_question("show the rest", headers, rows, last_row=2)
    check("'show the rest' resolves to rows [3, 4, 5]", op_rest is not None and op_rest.row_numbers == [3, 4, 5])

if __name__ == "__main__":
    print("=== SIS Chatbot Continuation Pipeline Regression Tests ===")
    test_recall_interception()
    test_anti_hallucination_gate_with_context()
    test_slice_re_pagination()
    test_workflow_continuation_progression()
    test_csv_ops_relative_cursor()

    total = len(_results)
    passed = sum(1 for _, ok, _ in _results if ok)
    failed = total - passed
    print(f"\nSummary: {passed}/{total} checks passed ({failed} failed).")
    sys.exit(0 if failed == 0 else 1)
