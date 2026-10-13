"""Behavioural regression test suite for:
1. Grounded project-relevance assessment across English, Tamil, Tanglish.
2. Context-aware negative follow-ups and corrections.
3. Full 3-turn sequence replay:
   Turn 1: About Easwari hack4purpose spl template.pdf: what it contains?
   Turn 2: is it relevant to our project ?
   Turn 3: no
4. Edge cases: unknown projects, ordinary refusals, multi-document ambiguity, expired attachments,
   and normal register queries.

Run with:
    python test_document_relevance_and_corrections.py
"""
import asyncio
import json
import sys
import uuid
from datetime import datetime, timedelta, timezone

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

passed_count = 0
failed_tests = []


def check(name: str, condition: bool, detail: str = ""):
    global passed_count
    if condition:
        passed_count += 1
        print(f"ok   {name}")
    else:
        failed_tests.append((name, detail))
        print(f"FAIL {name}" + (f" -- {detail}" if detail else ""))


async def main():
    from sqlalchemy import select
    from backend.database import AsyncSessionLocal
    from backend.models import SISOfficer, ChatAttachment, ChatMessage
    from backend.schemas import OfficerContext
    from backend.services.auth_service import get_officer_jurisdiction_ids
    from backend.services import chatbot as cb, attachment_store, attachment_qa
    from backend.services.doc_extract import ExtractedDocument, Segment, STATUS_OK

    async def get_test_officer():
        async with AsyncSessionLocal() as db:
            res = await db.execute(select(SISOfficer).where(SISOfficer.is_active == True).limit(1))
            officer = res.scalar_one()
            jur = await get_officer_jurisdiction_ids(officer.id, db)
            ids = (jur["district_ids"] + jur["taluk_ids"] + jur["town_ids"]
                   + jur["ward_ids"] + jur["block_ids"])
            return officer, OfficerContext(
                officer_id=officer.id, employee_id=officer.employee_id, name=officer.name,
                email=officer.email, designation=officer.designation,
                jurisdiction_type=jur["jurisdiction_type"], jurisdiction_name=jur["jurisdiction_name"],
                jurisdiction_ids=[i for i in ids if i])

    officer, ctx = await get_test_officer()

    async def chat_turn(msg: str, hist: list, sid: str, streaming: bool = False):
        if not streaming:
            async with AsyncSessionLocal() as db:
                r = await cb.process_chat(
                    db=db, message=msg, officer=ctx, chat_history=hist, session_id=sid
                )
            resp = (r.get("response") if isinstance(r, dict) else r) or ""
            hist.append({"role": "user", "content": msg})
            hist.append({"role": "assistant", "content": resp})
            return r
        else:
            chunks = []
            async with AsyncSessionLocal() as db:
                async for chunk_bytes in cb.process_chat_stream(
                    db=db, message=msg, officer=ctx, chat_history=hist, session_id=sid
                ):
                    chunk_str = chunk_bytes.decode("utf-8", errors="replace")
                    for line in chunk_str.split("\n"):
                        if line.startswith("data: ") and not line.startswith("data: [DONE]"):
                            try:
                                payload = json.loads(line[6:])
                                if "content" in payload:
                                    chunks.append(payload["content"])
                            except Exception:
                                pass
            full_resp = "".join(chunks)
            hist.append({"role": "user", "content": msg})
            hist.append({"role": "assistant", "content": full_resp})
            return {"response": full_resp}

    # =========================================================================
    # PART A: Grounded Project Relevance Assessment & Document QA
    # =========================================================================
    print("\n--- Running Part A: Document Relevance Assessments ---")
    async with AsyncSessionLocal() as db:
        session = await cb.create_chat_session(db, str(officer.id))
        sid_a = str(session.id)

    # Create synthetic Easwari template PDF with the 6 reported section headings
    easwari_segments = [
        Segment("Elevator pitch\nBrief introduction to the project solution and value proposition.",
                {"kind": "heading", "level": 1, "text": "Elevator pitch", "page": 1}),
        Segment("Team details\nProject lead, surveyor technical liaison, full-stack engineers.",
                {"kind": "heading", "level": 1, "text": "Team details", "page": 2}),
        Segment("Problem statement\nSurvey application backlog, citizen wait times, and manual record verification delays in Taluk offices.",
                {"kind": "heading", "level": 1, "text": "Problem statement", "page": 3}),
        Segment("Existing solutions\nManual register cross-checking and fragmented portal interfaces.",
                {"kind": "heading", "level": 1, "text": "Existing solutions", "page": 4}),
        Segment("Solution and affordability\nOffline-capable, cost-effective SIS AI Assistant with local open-source LLM reducing inspection latency.",
                {"kind": "heading", "level": 1, "text": "Solution and affordability", "page": 5}),
        Segment("Plan of action\nPhase 1 pilot in Thoothukudi Ward 2, followed by district-wide deployment.",
                {"kind": "heading", "level": 1, "text": "Plan of action", "page": 6}),
    ]
    async with AsyncSessionLocal() as db:
        doc_easwari = await attachment_store.save_document(
            db=db, officer_id=officer.id, session_id=sid_a,
            filename="Easwari hack4purpose spl template.pdf", ext=".pdf", mime_type="application/pdf",
            raw=b"%PDF-1.4 synthetic easwari presentation template",
            extracted=ExtractedDocument(status=STATUS_OK, segments=easwari_segments)
        )

    hist_a = []
    # 1. Ask what it contains
    r_overview = await chat_turn(
        "About Easwari hack4purpose spl template.pdf: what it contains?",
        hist_a, sid_a
    )
    ans_ov = r_overview["response"]
    check("Part A: Turn 1 outline lists sections",
          "Elevator pitch" in ans_ov and "Problem statement" in ans_ov and "Plan of action" in ans_ov,
          f"Response was: {ans_ov[:150]}")

    # 2. Ask whether it is relevant to our project
    r_rel = await chat_turn(
        "is it relevant to our project ?",
        hist_a, sid_a
    )
    ans_rel = r_rel["response"]
    check("Part A: Turn 2 is NOT a refusal",
          not attachment_qa.is_refusal(ans_rel),
          f"Got refusal: {ans_rel}")
    check("Part A: Turn 2 mentions presentation/pitch template",
          "presentation" in ans_rel.lower() or "template" in ans_rel.lower(),
          f"Got: {ans_rel[:150]}")
    check("Part A: Turn 2 distinguishes presentation usefulness from technical cadastral workflow",
          "authoritative" in ans_rel.lower() or "technical" in ans_rel.lower() or "isd/nisd" in ans_rel.lower() or "workflow" in ans_rel.lower(),
          f"Got: {ans_rel}")
    check("Part A: Turn 2 mentions useful sections (Problem statement, Solution & affordability, Plan of action)",
          "Problem statement" in ans_rel and "Solution and affordability" in ans_rel and "Plan of action" in ans_rel,
          f"Got: {ans_rel}")
    check("Part A: Turn 2 discloses outline-only evidence basis",
          "outline" in ans_rel.lower() or "headings" in ans_rel.lower(),
          f"Got: {ans_rel}")
    check("Part A: Turn 2 preserves citations",
          "Easwari hack4purpose spl template.pdf" in ans_rel or bool(r_rel.get("citations")),
          f"Citations: {r_rel.get('citations')}")

    # 3. Unknown project query: Project Apollo
    r_apollo = await chat_turn(
        "is this relevant to Project Apollo?",
        hist_a, sid_a
    )
    ans_apollo = r_apollo["response"]
    check("Part A: Unknown project triggers clarification for missing project requirements",
          "Project Apollo" in ans_apollo and ("purpose" in ans_apollo.lower() or "requirements" in ans_apollo.lower()),
          f"Got: {ans_apollo}")

    # 4. Tanglish relevance query
    r_tgl = await chat_turn(
        "indha document namma project-ku relevant ah?",
        hist_a, sid_a
    )
    ans_tgl = r_tgl["response"]
    check("Part A: Tanglish relevance query answered in natural Tamil with project comparison",
          not attachment_qa.is_refusal(ans_tgl) and ("திட்டம்" in ans_tgl or "விளக்கக்காட்சி" in ans_tgl or "பயனுள்ள" in ans_tgl),
          f"Got: {ans_tgl[:150]}")

    # 5. Normal missing-evidence refusal remains intact on factual search
    r_missing = await chat_turn(
        "what is the survey boundary distance on page 10?",
        hist_a, sid_a
    )
    ans_missing = r_missing["response"]
    check("Part A: Normal non-existent page/factual inquiry produces appropriate refusal/notice",
          attachment_qa.is_refusal(ans_missing) or "page 10" in ans_missing.lower(),
          f"Got: {ans_missing}")

    # =========================================================================
    # PART B: Context-Aware Negative Follow-ups
    # =========================================================================
    print("\n--- Running Part B: Negative Follow-ups and Corrections ---")

    # 1. Bare "no" after summary/overview
    async with AsyncSessionLocal() as db:
        session = await cb.create_chat_session(db, str(officer.id))
        sid_b1 = str(session.id)
        doc_b1 = await attachment_store.save_document(
            db=db, officer_id=officer.id, session_id=sid_b1,
            filename="Easwari hack4purpose spl template.pdf", ext=".pdf", mime_type="application/pdf",
            raw=b"%PDF-1.4 synthetic",
            extracted=ExtractedDocument(status=STATUS_OK, segments=easwari_segments)
        )
    hist_b1 = []
    await chat_turn("About Easwari hack4purpose spl template.pdf: what it contains?", hist_b1, sid_b1)
    r_no_ov = await chat_turn("no", hist_b1, sid_b1)
    ans_no_ov = r_no_ov["response"]
    check("Part B: 'no' after overview asks focused clarification on sections, not generic SIS fallback",
          "whenever you need an application" not in ans_no_ov and ("outline" in ans_no_ov.lower() or "section" in ans_no_ov.lower() or "detail" in ans_no_ov.lower() or "பகுதி" in ans_no_ov),
          f"Got generic response: {ans_no_ov}")

    # 2. Reproduction of exact reported refusal -> "no"
    async with AsyncSessionLocal() as db:
        session = await cb.create_chat_session(db, str(officer.id))
        sid_b2 = str(session.id)
        doc_b2 = await attachment_store.save_document(
            db=db, officer_id=officer.id, session_id=sid_b2,
            filename="Easwari hack4purpose spl template.pdf", ext=".pdf", mime_type="application/pdf",
            raw=b"%PDF-1.4 synthetic",
            extracted=ExtractedDocument(status=STATUS_OK, segments=easwari_segments)
        )
        # Manually simulate the exact failure turn 2: a refusal with structured_data entity="attachment"
        await cb.save_chat_messages(
            db=db, session_id=sid_b2,
            user_message="is it relevant to our project ?",
            assistant_message="I could not find this in the uploaded document.",
            language="en",
            response_time_ms=100,
            officer_id=officer.id,
            structured_context={
                "entity": "attachment",
                "document_ids": [str(doc_b2.id)],
                "sources": [doc_b2.filename],
                "attachment_kind": "refusal",
                "is_refusal": True,
                "is_project_relevance": True,
                "response_topic": "evidence_refusal",
                "prior_question": "is it relevant to our project ?",
            }
        )
    hist_b2 = [
        {"role": "user", "content": "About Easwari hack4purpose spl template.pdf: what it contains?"},
        {"role": "assistant", "content": "Lists six extracted sections: Elevator pitch..."},
        {"role": "user", "content": "is it relevant to our project ?"},
        {"role": "assistant", "content": "I could not find this in the uploaded document."}
    ]
    r_no_ref = await chat_turn("no", hist_b2, sid_b2)
    ans_no_ref = r_no_ref["response"]
    check("Part B: 'no' after relevance refusal clarifies presentation vs SIS technical workflow",
          "whenever you need an application" not in ans_no_ref and ("presentation" in ans_no_ref.lower() or "workflow" in ans_no_ref.lower() or "sis" in ans_no_ref.lower()),
          f"Got: {ans_no_ref}")

    # 3. Corrections: "no, that's not what I meant", "illa", "apdi illa", "naan adha kekkala"
    for phrase in ["no, that's not what I meant", "illa", "apdi illa", "naan adha kekkala"]:
        r_phrase = await chat_turn(phrase, list(hist_b2), sid_b2)
        ans_phrase = r_phrase["response"]
        check(f"Part B: Correction '{phrase}' avoids generic fallback and retains document context",
              "whenever you need an application" not in ans_phrase and ("புரிந்தது" in ans_phrase or "understood" in ans_phrase.lower() or "presentation" in ans_phrase.lower() or "விளக்கக்காட்சி" in ans_phrase or "workflow" in ans_phrase.lower() or "ஆவணம்" in ans_phrase),
              f"Got: {ans_phrase}")

    # 4. Explicit user correction: "idhu illa, namma project pathi sonnen"
    r_explicit = await chat_turn("idhu illa, namma project pathi sonnen", list(hist_b2), sid_b2)
    ans_explicit = r_explicit["response"]
    check("Part B: Explicit correction 'idhu illa, namma project pathi sonnen' directly provides project assessment",
          not attachment_qa.is_refusal(ans_explicit) and ("திட்டம்" in ans_explicit or "விளக்கக்காட்சி" in ans_explicit or "presentation" in ans_explicit.lower()),
          f"Got: {ans_explicit}")

    # 5. "no" after ordinary application status answer (should NOT enter document correction)
    async with AsyncSessionLocal() as db:
        session = await cb.create_chat_session(db, str(officer.id))
        sid_app = str(session.id)
    hist_app = []
    r_app = await chat_turn("what is the status of 2026/0154/28/001197", hist_app, sid_app)
    r_app_no = await chat_turn("no", hist_app, sid_app)
    ans_app_no = r_app_no["response"]
    check("Part B: 'no' after register query preserves standard contentless reply",
          "No problem. Ask me whenever you need an application" in ans_app_no or "சரி, பரவாயில்லை" in ans_app_no,
          f"Got: {ans_app_no}")

    # 6. Rejection after attachment expiry
    async with AsyncSessionLocal() as db:
        session = await cb.create_chat_session(db, str(officer.id))
        sid_exp = str(session.id)
        doc_exp = await attachment_store.save_document(
            db=db, officer_id=officer.id, session_id=sid_exp,
            filename="expired_sample.pdf", ext=".pdf", mime_type="application/pdf",
            raw=b"%PDF-1.4 expired",
            extracted=ExtractedDocument(status=STATUS_OK, segments=easwari_segments)
        )
    hist_exp = []
    await chat_turn("About expired_sample.pdf: what it contains?", hist_exp, sid_exp)
    # Manually expire document
    async with AsyncSessionLocal() as db:
        row = (await db.execute(
            select(ChatAttachment).where(ChatAttachment.id == doc_exp.id)
        )).scalar_one()
        row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        await db.commit()
    r_exp_no = await chat_turn("no", hist_exp, sid_exp)
    ans_exp_no = r_exp_no["response"]
    check("Part B: 'no' after expired attachment gives re-upload notice",
          "upload the document again" in ans_exp_no.lower() or "மீண்டும் பதிவேற்றவும்" in ans_exp_no,
          f"Got: {ans_exp_no}")

    # 7. Multi-document ambiguity on correction
    async with AsyncSessionLocal() as db:
        session = await cb.create_chat_session(db, str(officer.id))
        sid_multi = str(session.id)
        doc_m1 = await attachment_store.save_document(
            db=db, officer_id=officer.id, session_id=sid_multi,
            filename="file_one.pdf", ext=".pdf", mime_type="application/pdf",
            raw=b"%PDF-1.4 file 1",
            extracted=ExtractedDocument(status=STATUS_OK, segments=easwari_segments)
        )
        doc_m2 = await attachment_store.save_document(
            db=db, officer_id=officer.id, session_id=sid_multi,
            filename="file_two.pdf", ext=".pdf", mime_type="application/pdf",
            raw=b"%PDF-1.4 file 2",
            extracted=ExtractedDocument(status=STATUS_OK, segments=easwari_segments)
        )
        # Previous turn was an ambiguous multi-file turn with no single focus
        await cb.save_chat_messages(
            db=db, session_id=sid_multi,
            user_message="summarize the uploaded documents",
            assistant_message="Which file do you mean: file_one.pdf or file_two.pdf?",
            language="en",
            response_time_ms=100,
            officer_id=officer.id,
            structured_context={
                "entity": "attachment",
                "document_ids": [str(doc_m1.id), str(doc_m2.id)],
                "sources": [doc_m1.filename, doc_m2.filename],
                "attachment_kind": "clarify",
                "response_topic": "clarify_files",
                "prior_question": "summarize the uploaded documents",
            }
        )
    hist_multi = [
        {"role": "user", "content": "summarize the uploaded documents"},
        {"role": "assistant", "content": "Which file do you mean: file_one.pdf or file_two.pdf?"}
    ]
    r_multi_no = await chat_turn("no, not that", hist_multi, sid_multi)
    ans_multi_no = r_multi_no["response"]
    check("Part B: Ambiguous multi-document correction asks which file is meant",
          "file_one.pdf" in ans_multi_no and "file_two.pdf" in ans_multi_no,
          f"Got: {ans_multi_no}")

    # =========================================================================
    # PART C: Full 3-Turn Conversation Replay (Non-streaming & Streaming)
    # =========================================================================
    print("\n--- Running Part C: Full Conversation Replay ---")

    # Non-streaming replay
    async with AsyncSessionLocal() as db:
        session = await cb.create_chat_session(db, str(officer.id))
        sid_c1 = str(session.id)
        doc_c1 = await attachment_store.save_document(
            db=db, officer_id=officer.id, session_id=sid_c1,
            filename="Easwari hack4purpose spl template.pdf", ext=".pdf", mime_type="application/pdf",
            raw=b"%PDF-1.4 synthetic",
            extracted=ExtractedDocument(status=STATUS_OK, segments=easwari_segments)
        )
    hist_c1 = []
    turn1_res = await chat_turn("About Easwari hack4purpose spl template.pdf: what it contains?", hist_c1, sid_c1)
    turn2_res = await chat_turn("is it relevant to our project ?", hist_c1, sid_c1)
    turn3_res = await chat_turn("no", hist_c1, sid_c1)

    check("Part C (Non-streaming) Turn 1: Extracted outline headings",
          "Elevator pitch" in turn1_res["response"] and "Plan of action" in turn1_res["response"])
    check("Part C (Non-streaming) Turn 2: Grounded comparison without refusal",
          not attachment_qa.is_refusal(turn2_res["response"]) and "presentation" in turn2_res["response"].lower())
    check("Part C (Non-streaming) Turn 3: Context-aware follow-up, NOT generic fallback",
          "whenever you need an application" not in turn3_res["response"] and ("presentation" in turn3_res["response"].lower() or "workflow" in turn3_res["response"].lower() or "aspect" in turn3_res["response"].lower()))

    # Verify structured context persisted in DB for Turn 2
    async with AsyncSessionLocal() as db:
        t2_msg = (await db.execute(
            select(ChatMessage)
            .where(ChatMessage.session_id == sid_c1, ChatMessage.role == "assistant")
            .order_by(ChatMessage.created_at.desc())
            .offset(1).limit(1)
        )).scalar_one_or_none()
        sd = t2_msg.structured_data if t2_msg else {}
        check("Part C (Non-streaming) Turn 2: Structured context persisted",
              sd.get("entity") == "attachment" and sd.get("response_topic") == "project_relevance" and sd.get("is_project_relevance") is True,
              f"Stored structured_data: {sd}")

    # Streaming replay
    async with AsyncSessionLocal() as db:
        session = await cb.create_chat_session(db, str(officer.id))
        sid_c2 = str(session.id)
        doc_c2 = await attachment_store.save_document(
            db=db, officer_id=officer.id, session_id=sid_c2,
            filename="Easwari hack4purpose spl template.pdf", ext=".pdf", mime_type="application/pdf",
            raw=b"%PDF-1.4 synthetic",
            extracted=ExtractedDocument(status=STATUS_OK, segments=easwari_segments)
        )
    hist_c2 = []
    s_turn1_res = await chat_turn("About Easwari hack4purpose spl template.pdf: what it contains?", hist_c2, sid_c2, streaming=True)
    s_turn2_res = await chat_turn("is it relevant to our project ?", hist_c2, sid_c2, streaming=True)
    s_turn3_res = await chat_turn("no", hist_c2, sid_c2, streaming=True)

    check("Part C (Streaming) Turn 1: Extracted outline headings",
          "Elevator pitch" in s_turn1_res["response"] and "Plan of action" in s_turn1_res["response"])
    check("Part C (Streaming) Turn 2: Grounded comparison without refusal",
          not attachment_qa.is_refusal(s_turn2_res["response"]) and "presentation" in s_turn2_res["response"].lower())
    check("Part C (Streaming) Turn 3: Context-aware follow-up, NOT generic fallback",
          "whenever you need an application" not in s_turn3_res["response"] and ("presentation" in s_turn3_res["response"].lower() or "workflow" in s_turn3_res["response"].lower() or "aspect" in s_turn3_res["response"].lower()))

    # Verify structured context persisted in DB for Streaming Turn 2
    async with AsyncSessionLocal() as db:
        s_t2_msg = (await db.execute(
            select(ChatMessage)
            .where(ChatMessage.session_id == sid_c2, ChatMessage.role == "assistant")
            .order_by(ChatMessage.created_at.desc())
            .offset(1).limit(1)
        )).scalar_one_or_none()
        s_sd = s_t2_msg.structured_data if s_t2_msg else {}
        check("Part C (Streaming) Turn 2: Structured context persisted",
              s_sd.get("entity") == "attachment" and s_sd.get("response_topic") == "project_relevance" and s_sd.get("is_project_relevance") is True,
              f"Stored structured_data: {s_sd}")

    # Summary
    print(f"\n==========================================")
    print(f"RESULTS: {passed_count} passed, {len(failed_tests)} failed")
    if failed_tests:
        print("FAILURES:")
        for name, detail in failed_tests:
            print(f"- {name}: {detail}")
        sys.exit(1)
    else:
        print("ALL TESTS PASSED SUCCESSFULLY!")
        sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
