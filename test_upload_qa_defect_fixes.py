"""Comprehensive Regression Suite for Upload QA Defects:
SVCCODE-001, CSVCOL-001a, CSVCOL-001b, FNAME-001, LINENO-001,
BREVITY-001, NEGCORR-001, ORDCLAR-001, ARCH-001, DOC-001.

Validates all 9 confirmed defects and architectural improvements.
Run with:
    .venv\\Scripts\\python test_upload_qa_defect_fixes.py
"""
import asyncio
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, List

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

passed = 0
failed = 0
failures = []


def check(name: str, cond: bool, detail: str = ""):
    global passed, failed
    if cond:
        passed += 1
        print(f"ok   {name}")
    else:
        failed += 1
        failures.append((name, detail))
        print(f"FAIL {name}" + (f" -- {detail}" if detail else ""))


async def main():
    print("=" * 60)
    print("RUNNING COMPREHENSIVE UPLOAD QA DEFECT REGRESSION SUITE")
    print("=" * 60)

    # ---------------------------------------------------------
    # 1. DOC-001: CLAUDE.md OCR Documentation Verification
    # ---------------------------------------------------------
    print("\n--- 1. DOC-001: Verifying CLAUDE.md OCR Documentation ---")
    with open("CLAUDE.md", "r", encoding="utf-8") as f:
        claude_content = f.read()

    check("DOC-001: CLAUDE.md mentions scanned-page PDF OCR",
          "scanned-page PDF OCR" in claude_content or "_ocr_pdf_pages" in claude_content)
    check("DOC-001: CLAUDE.md documents unsupported standalone image uploads",
          "Standalone image files" in claude_content or "not accepted as chat uploads" in claude_content)
    check("DOC-001: CLAUDE.md does NOT claim 'no OCR anywhere'",
          "no OCR anywhere" not in claude_content)

    # ---------------------------------------------------------
    # 2. FNAME-001: Filename Masking in Typo Corrector
    # ---------------------------------------------------------
    print("\n--- 2. FNAME-001: Verifying Filename Masking ---")
    from backend.services.followup_context import _correct_typos

    # Test fileA / fileB / CSV / PDF filenames are protected from typo replacement
    fn_input = "In sis_svccode_fileA.csv what are the service codes?"
    fn_res = _correct_typos(fn_input)
    check("FNAME-001: sis_svccode_fileA.csv is preserved intact",
          "sis_svccode_fileA.csv" in fn_res, f"Got: {fn_res}")

    fn_input2 = "Compare sis_svccode_fileA.csv and sis_svccode_fileB.csv"
    fn_res2 = _correct_typos(fn_input2)
    check("FNAME-001: Multiple filenames preserved intact",
          "sis_svccode_fileA.csv" in fn_res2 and "sis_svccode_fileB.csv" in fn_res2, f"Got: {fn_res2}")

    fn_input3 = "look at lineposition_test.txt"
    fn_res3 = _correct_typos(fn_input3)
    check("FNAME-001: .txt filename preserved intact",
          "lineposition_test.txt" in fn_res3, f"Got: {fn_res3}")

    # ---------------------------------------------------------
    # 3. CSVCOL-001a & CSVCOL-001b: Plural Columns & Deterministic Comparison
    # ---------------------------------------------------------
    print("\n--- 3. CSVCOL-001a/b: Verifying Plural Column Resolution & Comparison ---")
    from backend.services.csv_ops import resolve_column, parse_csv_question

    headers = ["application_number", "applicant_name", "service_code", "status"]
    resolved_col = resolve_column("service codes", headers)
    check("CSVCOL-001a: 'service codes' resolves to 'service_code'",
          resolved_col == "service_code", f"Got: {resolved_col}")

    resolved_cols_plural = resolve_column("statuses", headers)
    check("CSVCOL-001a: 'statuses' resolves to 'status'",
          resolved_cols_plural == "status", f"Got: {resolved_cols_plural}")

    # CSVCOL-001b: Deterministic comparison parsing without LLM narration
    rows = [{"service_code": "0178", "status": "pending"}]
    cmp_q = "Compare the service codes in both files."
    cmp_op = parse_csv_question(cmp_q, headers, rows)
    check("CSVCOL-001b: 'Compare the service codes' parsed as filter operation on service_code",
          cmp_op is not None and cmp_op.op == "filter" and cmp_op.column == "service_code",
          f"Got: {cmp_op}")

    cmp_q_diff = "What is the difference in service codes between the two files?"
    cmp_op_diff = parse_csv_question(cmp_q_diff, headers, rows)
    check("CSVCOL-001b: 'difference in service codes' parsed as filter on service_code",
          cmp_op_diff is not None and cmp_op_diff.column == "service_code",
          f"Got: {cmp_op_diff}")

    # ---------------------------------------------------------
    # 4. SVCCODE-001 & BREVITY-001: F-Line vs F-Line Appeal & Brevity Constraints
    # ---------------------------------------------------------
    print("\n--- 4. SVCCODE-001 & BREVITY-001: Service Code Distinction & Brevity ---")
    from backend.utils.helpers import (
        _svc_name_alias_match, is_code_only_request,
        is_code_and_desc_request, is_table_only_request
    )
    from backend.services.chatbot import _service_code_lookup_answer

    # Alias matching length ordering & distinction
    match_0178 = _svc_name_alias_match("What is F-Line?")
    check("SVCCODE-001: 'F-Line' resolves to 0178", match_0178 == "0178", f"Got: {match_0178}")

    match_0185 = _svc_name_alias_match("What is F-Line Appeal?")
    check("SVCCODE-001: 'F-Line Appeal' resolves to 0185, NOT 0178", match_0185 == "0185", f"Got: {match_0185}")

    # Comparison question
    diff_ans = _service_code_lookup_answer("What is the difference between F-Line and F-Line Appeal?", is_tamil=False)
    check("SVCCODE-001: Comparison states F-Line (0178) and F-Line Appeal (0185) are different services",
          "different services" in diff_ans and "0178" in diff_ans and "0185" in diff_ans,
          f"Got: {diff_ans[:150]}")

    # Brevity constraints (BREVITY-001)
    check("BREVITY-001: is_code_only_request identifies 'code only'",
          is_code_only_request("What is F-Line? Code only, don't explain"))
    check("BREVITY-001: is_code_and_desc_request identifies 'code and description only'",
          is_code_and_desc_request("What is F-Line? Code and description only"))
    check("BREVITY-001: is_table_only_request identifies 'table only'",
          is_table_only_request("Show service code 0178 as table only"))

    code_only_ans = _service_code_lookup_answer("What is F-Line? Code only", is_tamil=False)
    check("BREVITY-001: Code only returns bare code", code_only_ans.strip() == "0178", f"Got: {code_only_ans}")

    code_desc_ans = _service_code_lookup_answer("What is F-Line? Code and description only", is_tamil=False)
    check("BREVITY-001: Code and desc returns code with name and short form",
          "0178 — " in code_desc_ans and "F-Line" in code_desc_ans, f"Got: {code_desc_ans}")

    table_only_ans = _service_code_lookup_answer("What is F-Line? Table only", is_tamil=False)
    check("BREVITY-001: Table only returns markdown table structure",
          "| Code | Service Name |" in table_only_ans and "| 0178 |" in table_only_ans, f"Got: {table_only_ans}")

    # ---------------------------------------------------------
    # 5. LINENO-001: Exact Line-Number Extraction
    # ---------------------------------------------------------
    print("\n--- 5. LINENO-001: Verifying Exact Line-Number Extraction ---")
    from backend.services.chatbot.attachment_qa import location_reference, location_range_reference, is_positional_only

    loc10 = location_reference("In sis_lineposition_test.txt, what is on line 10?")
    check("LINENO-001: Location reference extracts ('lines', 10)",
          loc10 == ("lines", 10), f"Got: {loc10}")

    pos_only = is_positional_only("In sis_lineposition_test.txt, what is on line 10?", ["sis_lineposition_test.txt"])
    check("LINENO-001: Query identified as positional only", pos_only is True)

    loc_range = location_range_reference("show lines 3 to 6 of the document")
    check("LINENO-001: Location range reference extracts ('lines', 3, 6)",
          loc_range == ("lines", 3, 6), f"Got: {loc_range}")

    # Test end-to-end line extraction using DB session
    from backend.database import AsyncSessionLocal
    from backend.services import attachment_store
    from backend.services.chatbot import attachment_qa
    from backend.models import SISOfficer
    from backend.services.doc_extract import ExtractedDocument, Segment, STATUS_OK
    from sqlalchemy import select

    async with AsyncSessionLocal() as db:
        res = await db.execute(select(SISOfficer).where(SISOfficer.is_active == True).limit(1))
        officer = res.scalar_one()

        from backend.services import chatbot as cb
        session_line = await cb.create_chat_session(db, str(officer.id))
        sid_line = str(session_line.id)
        txt_content = (
            "Line 1: Header information\n"
            "Line 2: Surveyor notes\n"
            "Line 3: Ambattur Taluk Ward 4\n"
            "Line 4: Survey 144 subdivision 2A\n"
            "Line 5: Field demarcation completed\n"
            "Line 6: Boundary stone verified\n"
            "Line 7: No disputes recorded\n"
            "Line 8: Ready for approval\n"
            "Line 9: Timestamp 2026-10-13\n"
            "Final line contains the known value: LAST-LINE-VALUE-ALPHA-999."
        )
        # Store in 1 chunk spanning lines 1-10
        txt_segments = [
            Segment(txt_content, {"kind": "lines", "start": 1, "end": 10})
        ]
        doc_line = await attachment_store.save_document(
            db=db, officer_id=officer.id, session_id=sid_line,
            filename="sis_lineposition_test.txt", ext=".txt", mime_type="text/plain",
            raw=txt_content.encode("utf-8"),
            extracted=ExtractedDocument(status=STATUS_OK, segments=txt_segments)
        )

        # Plan answer for Line 10
        plan_line10 = await attachment_qa.plan_answer(
            db, officer, sid_line,
            "In sis_lineposition_test.txt, what is on line 10?",
            intent="general_query", language="en"
        )
        check("LINENO-001: Plan generated for line 10 query", plan_line10 is not None)
        if plan_line10:
            check("LINENO-001: Returns EXACT line 10 text in isolation without dumping all lines",
                  "LAST-LINE-VALUE-ALPHA-999" in plan_line10.text and "Line 1: Header" not in plan_line10.text,
                  f"Got: {plan_line10.text}")
            check("LINENO-001: Citation cites line 10 specifically",
                  any("line 10" in c for c in plan_line10.citations),
                  f"Citations: {plan_line10.citations}")

        # Plan answer for Line range 3 to 5
        plan_range = await attachment_qa.plan_answer(
            db, officer, sid_line,
            "In sis_lineposition_test.txt, show lines 3 to 5",
            intent="general_query", language="en"
        )
        check("LINENO-001: Plan generated for line range query", plan_range is not None)
        if plan_range:
            check("LINENO-001: Line range returns lines 3, 4, 5 and excludes line 1",
                  "Ambattur Taluk" in plan_range.text and "Survey 144" in plan_range.text and "Line 1: Header" not in plan_range.text,
                  f"Got: {plan_range.text}")

        # Out-of-bounds line 99
        plan_oob = await attachment_qa.plan_answer(
            db, officer, sid_line,
            "In sis_lineposition_test.txt, what is on line 99?",
            intent="general_query", language="en"
        )
        check("LINENO-001: Out-of-bounds line 99 triggers refusal/extent notice",
          plan_oob is not None and plan_oob.kind == "refusal",
          f"Got: {plan_oob.text if plan_oob else None}")

    # ---------------------------------------------------------
    # 6. ORDCLAR-001: File Disambiguation Ordinals ("the first one")
    # ---------------------------------------------------------
    print("\n--- 6. ORDCLAR-001: Verifying File Disambiguation Ordinals ---")
    from backend.services.chatbot.attachment_qa import resolve_file_ordinal_pick
    from backend.services import chatbot as cb

    async with AsyncSessionLocal() as db:
        session_ord = await cb.create_chat_session(db, str(officer.id))
        sid_ord = str(session_ord.id)
        doc_a = await attachment_store.save_document(
            db=db, officer_id=officer.id, session_id=sid_ord,
            filename="sis_svccode_fileA.csv", ext=".csv", mime_type="text/csv",
            raw=b"app_no,status\n2026/0154/28/001197,pending\n",
            extracted=ExtractedDocument(status=STATUS_OK, segments=[Segment("app_no,status", {"kind": "table"})])
        )
        doc_b = await attachment_store.save_document(
            db=db, officer_id=officer.id, session_id=sid_ord,
            filename="sis_svccode_fileB.csv", ext=".csv", mime_type="text/csv",
            raw=b"app_no,status\n2026/0154/28/001198,approved\n",
            extracted=ExtractedDocument(status=STATUS_OK, segments=[Segment("app_no,status", {"kind": "table"})])
        )
        docs = [doc_a, doc_b]

        pick_first = resolve_file_ordinal_pick("the first one", docs)
        check("ORDCLAR-001: 'the first one' resolves to doc_a",
              pick_first is not None and pick_first.filename == "sis_svccode_fileA.csv",
              f"Got: {pick_first.filename if pick_first else None}")

        pick_second = resolve_file_ordinal_pick("the second one", docs)
        check("ORDCLAR-001: 'the second one' resolves to doc_b",
              pick_second is not None and pick_second.filename == "sis_svccode_fileB.csv",
              f"Got: {pick_second.filename if pick_second else None}")

        pick_file_b = resolve_file_ordinal_pick("File B", docs)
        check("ORDCLAR-001: 'File B' resolves to doc_b",
              pick_file_b is not None and pick_file_b.filename == "sis_svccode_fileB.csv",
              f"Got: {pick_file_b.filename if pick_file_b else None}")

        # Check _howto_turn stands aside when attachments are active
        howto_res = await cb._howto_turn("the first one", sid_ord, officer, db, [])
        check("ORDCLAR-001: _howto_turn returns None when attachments are active, not hijacking ordinal",
              howto_res is None, f"Got: {howto_res}")

    # ---------------------------------------------------------
    # 7. NEGCORR-001: General Negative Correction Outside Attachments
    # ---------------------------------------------------------
    print("\n--- 7. NEGCORR-001: Verifying General Negative Correction ---")
    from backend.services.chatbot import _build_general_negative_correction_response

    hist_svc = [
        {"role": "user", "content": "What is F-Line?"},
        {"role": "assistant", "content": "0178 is F-Line Urban Demarcation..."}
    ]
    neg_svc = _build_general_negative_correction_response("I don't think so", hist_svc, "en", officer)
    check("NEGCORR-001: 'I don't think so' after service code offers clarification on 0185 F-Line Appeal",
          neg_svc is not None and ("0185" in neg_svc["response"] or "F-Line Appeal" in neg_svc["response"]),
          f"Got: {neg_svc['response'] if neg_svc else None}")

    hist_app = [
        {"role": "user", "content": "Status of 2026/0154/28/001197"},
        {"role": "assistant", "content": "Application 2026/0154/28/001197 is pending..."}
    ]
    neg_app = _build_general_negative_correction_response("That's wrong, check the database again", hist_app, "en", officer)
    check("NEGCORR-001: Pushback on application status offers re-verification of the record",
          neg_app is not None and ("2026/0154/28/001197" in neg_app["response"] or "re-verify" in neg_app["response"].lower()),
          f"Got: {neg_app['response'] if neg_app else None}")

    # ---------------------------------------------------------
    # 8. ARCH-001: Grounded Project Relevance Without Tech Stack Prose
    # ---------------------------------------------------------
    print("\n--- 8. ARCH-001: Verifying Grounded Project Relevance ---")
    from backend.services.chatbot.attachment_qa import _build_project_relevance_assessment

    # Test that ungrounded tech stack prose is NEVER included
    pres_doc = doc_a
    headings = ["Elevator pitch", "Problem statement", "Solution and affordability", "Plan of action"]
    text_res, _ = _build_project_relevance_assessment(
        pres_doc, headings, [], None, "en"
    )
    check("ARCH-001: Project relevance contains NO hallucinated tech stack prose ('FastAPI', 'PostgreSQL', 'Ollama')",
          "fastapi" not in text_res.lower() and "postgresql" not in text_res.lower() and "ollama" not in text_res.lower(),
          f"Got: {text_res}")
    check("ARCH-001: Classified as Presentation / Proposal Template",
          "presentation" in text_res.lower() or "template" in text_res.lower(),
          f"Got: {text_res[:150]}")
    check("ARCH-001: Mentions useful sections from outline",
          "Problem statement" in text_res and "Solution and affordability" in text_res and "Plan of action" in text_res,
          f"Got: {text_res}")
    check("ARCH-001: Distinguishes presentation template from technical cadastral workflow",
          "technical" in text_res.lower() or "workflow" in text_res.lower() or "cadastral" in text_res.lower(),
          f"Got: {text_res}")

    print("\n" + "=" * 60)
    print(f"RESULTS: {passed} passed, {failed} failed")
    if failed == 0:
        print("ALL DEFECT REGRESSION TESTS PASSED CLEANLY!")
    else:
        print("SOME TESTS FAILED:")
        for name, detail in failures:
            print(f"  - {name}: {detail}")
    print("=" * 60)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
