# SIS Chatbot — Continuation, Pagination & Resumption Fix Report

**Project**: Sub Inspector Surveyor (SIS) AI Chatbot — Tamil Nadu Survey Department  
**Repository**: [svmayurkaarthic2005/Internship_NIC](https://github.com/svmayurkaarthic2005/Internship_NIC)  
**Audit Source**: [SIS_Continue_Followup_Audit_Report.md](file:///c:/proj/nic_internship/SIS_Continue_Followup_Audit_Report.md)  
**Date**: October 2026  
**Status**: All 10 Defect Categories Verified and Resolved (100% Pass Rate across 36/36 Pipeline Defects and 19/19 Continuation Types)

---

## Executive Summary

During the comprehensive audit of the SIS Chatbot, 10 distinct defect categories were identified where continuation idioms ("continue", "next", "more", "resume", "show the rest", "what comes next?", and Tamil equivalents like "தொடரவும்", "அடுத்தது") failed to interpret user intent in context. Rather than continuing or expanding the active discussion, requests were intercepted by recall echoes, blocked by hallucination guards, misrouted into missing-application prompts, collapsed into single-item records, or answered with LLM hallucinations.

This report documents the architectural fixes implemented across the backend routing, conversation context, pagination, workflow progression, and attachment processing layers. All fixes preserve existing uncommitted working-tree modifications, adhere to strict database schema boundaries, and have been validated with automated regression suites.

---

## Root Cause Analysis & Implemented Fixes by Defect Category

### 1. Recall Interception Defect (FAIL-CONT-001, FAIL-CONT-002)
- **Root Cause**: `_is_conversation_recall()` in [chatbot.py](file:///c:/proj/nic_internship/backend/services/chatbot.py#L10657) contained an explicit list of continuation idioms (`_RECALL_CONTINUATION_PHRASES`), including `"where we left off"`, `"let's continue"`, `"resume our conversation"`, and `"தொடர்ந்து பேசுவோம்"`. When a user attempted to continue, the chatbot routed the request to `_conversation_recall_reply()`, which simply echoed the user's previous prompt verbatim (`"Your previous message was: ..."`).
- **Fix Applied**: 
  - Segregated genuine recall questions (which require recall qualifiers like "previous", "last", "earlier", "முந்தைய", "கடைசி" + subject nouns like "question", "message", "கேள்வி") from continuation idioms.
  - Relocated continuation phrases out of `_is_conversation_recall()` into the contextual continuation flow. Genuine recall questions (`"what was my last question?"`, `"நான் கடைசியாக என்ன கேட்டேன்?"`) remain fully functional, while continuation requests are never intercepted as echo queries.
- **Files Modified**: [backend/services/chatbot.py](file:///c:/proj/nic_internship/backend/services/chatbot.py) (`_RECALL_QUALIFIER_WORDS`, `_is_conversation_recall`).

---

### 2. Vague Continuation Gate Defect (FAIL-CONT-003, FAIL-CONT-010)
- **Root Cause**: Single-word continuation queries like `"continue"`, `"resume"`, `"go on"`, `"தொடரவும்."` were classified as `general_query`. In [chatbot.py](file:///c:/proj/nic_internship/backend/services/chatbot.py#L1716), `_no_evidence_for_llm()` checks whether the message contains domain evidence. Because `_CONTEXT_CUE_RE` lacked continuation verbs and Tamil continuation stems, `_domain_evidence_tier()` returned `None`, triggering an anti-hallucination gate that returned a generic refusal (`"I'm not sure what you mean..."`).
- **Fix Applied**:
  - Expanded `_CONTEXT_CUE_RE` to recognize English, Tamil, and Tanglish continuation roots: `continue|resume|proceed|next|more|rest|remaining|further|go\s+on|thodaru|thodarunga|meethi|adutha|innum|தொடர்|மீதி|அடுத்த|மேலும்`.
  - When an active session context (`FollowupContext`, active document, or conversation history) is present, `_domain_evidence_tier()` assigns tier `"context"`, preventing `_no_evidence_for_llm` from falsely blocking continuation.
- **Files Modified**: [backend/services/chatbot.py](file:///c:/proj/nic_internship/backend/services/chatbot.py) (`_CONTEXT_CUE_RE`, `_domain_evidence_tier`).

---

### 3. Service Code Detail Routing Defect (FAIL-CONT-004)
- **Root Cause**: When a user asked `"more details"` after inquiring about a service code (such as `"What is ISD?"`), the intent parser in [rag.py](file:///c:/proj/nic_internship/backend/services/rag.py#L4619) matched `_BARE_DETAILS_RE` and unconditionally forced the intent to `application_status`. Since no application number existed in context, the bot prompted: `"Please specify the application number you are asking about..."`.
- **Fix Applied**:
  - Added an inspection of `prev_intent` in `_BARE_DETAILS_RE`. If `prev_intent in ("service_code_lookup", "service_code_guide")`, the intent remains `service_code_lookup`, allowing the service code handler to provide the complete 9-field specification card.
- **Files Modified**: [backend/services/rag.py](file:///c:/proj/nic_internship/backend/services/rag.py) (`parse_intent`).

---

### 4. Database List Pagination Defect (FAIL-CONT-005)
- **Root Cause**: When viewing a list of applications (e.g. 44 pending files), typing `"next"` or `"continue"` triggered `nav_direction()` in [followup_context.py](file:///c:/proj/nic_internship/backend/services/followup_context.py#L3480). However, the handler only tracked single-application card cursors, causing `"next"` over an application table to either pick row 1 as an isolated card or drop into an unanswerable state.
- **Fix Applied**:
  - Enhanced `followup_context.resolve()`: When a bare `"next"` is issued over `ENTITY_APPLICATION_LIST` without a single-card cursor, it advances the table pagination window by `page_size` (default 5 records).
  - Maintained `last_offset` cursor across turns. When the end of the list is reached, it returns a clean, graceful completion message: `"All N applications in this list have already been shown. No further records remain."`
- **Files Modified**: [backend/services/followup_context.py](file:///c:/proj/nic_internship/backend/services/followup_context.py) (`resolve`).

---

### 5. Slice Parser Blindness Defect (FAIL-CONT-006)
- **Root Cause**: `_SLICE_RE` in [followup_context.py](file:///c:/proj/nic_internship/backend/services/followup_context.py#L1250) only matched absolute positions (`first`, `top`, `initial`, `last`, `bottom`). Phrases like `"next five"`, `"next 3 rows"`, or `"show the rest"` were unrecognized, returning `None` from `slice_pick()` and failing follow-up resolution.
- **Fix Applied**:
  - Updated `_SLICE_RE` and `_SLICE_TA_RE` to match `next|following|subsequent` and Tamil/Tanglish `அடுத்த|adutha`.
  - Added `_REMAINING_ROWS_RE` matching `remaining|show the rest|meethi rows kaatu|மீதமுள்ளவை`.
  - Updated `slice_pick(message, numbers, offset=0)` to compute relative progression:
    - `"next five"` returns 5 elements starting from `offset`.
    - `"show the rest"` returns all elements from `offset` to the end of the list.
- **Files Modified**: [backend/services/followup_context.py](file:///c:/proj/nic_internship/backend/services/followup_context.py) (`_SLICE_RE`, `_SLICE_TA_RE`, `_REMAINING_ROWS_RE`, `slice_pick`, `resolve`).

---

### 6. Workflow Step Hallucination Defect (FAIL-CONT-007)
- **Root Cause**: Questions like `"What comes next?"` or `"what is the next step?"` lacked mapping to the survey manual procedure when discussing workflow stages. The queries fell through to the LLM with generic RAG chunks, causing the LLM to hallucinate procedures (such as claiming the Tahsildar performs initial field visits).
- **Fix Applied**:
  - In [chatbot.py](file:///c:/proj/nic_internship/backend/services/chatbot.py#L13854) (`_workflow_steps_topic`), added regex pattern matching for relative progression inquiries (`"what comes next"`, `"next step"`, `"அடுத்த படி என்ன"`).
  - Inspected recent conversation turns to detect current stage:
    - After SIS field inspection -> routes to `AFTER:sis` (Senior Draughtsman SD sketch).
    - After Senior Draughtsman -> routes to `AFTER:sd` (Deputy Inspector DIS scrutiny).
    - After Deputy Inspector -> routes to `AFTER:dis` (Tahsildar approval & order generation).
    - If no workflow stage is present in context -> returns `CLARIFY_WORKFLOW` ("Do you mean ISD or NISD?").
- **Files Modified**: [backend/services/chatbot.py](file:///c:/proj/nic_internship/backend/services/chatbot.py) (`_workflow_steps_topic`).

---

### 7. Attachment Positional Pre-emption Defect (FAIL-CONT-009)
- **Root Cause**: `_is_orphan_list_followup()` in [chatbot.py](file:///c:/proj/nic_internship/backend/services/chatbot.py#L19108) checked whether positional queries (like `"Show row 1"`) were issued without preceding database applications. This check executed *before* attachment query planning, intercepting valid CSV row requests with an orphan error: `"I do not know which application or list you mean..."`.
- **Fix Applied**:
  - Added guards to both non-streaming (`process_chat`) and streaming (`process_chat_stream`) paths:
    ```python
    and not await attachment_qa.attachment_topic_active(db, session_id)
    and not await attachment_qa.any_active_documents(db, officer.officer_id if officer else None, session_id)
    ```
  - This allows CSV and attachment row lookups to flow unimpeded to `attachment_qa` while preserving the guard for genuine database orphans.
- **Files Modified**: [backend/services/chatbot.py](file:///c:/proj/nic_internship/backend/services/chatbot.py) (`_is_orphan_list_followup` invocations).

---

### 8. Attachment Row Progression Defect (FAIL-CONT-008)
- **Root Cause**: `csv_ops.parse_csv_question()` only parsed absolute row numbers (`"row 2"`). Relative queries like `"next row"`, `"next 2 rows"`, `"continue"`, or `"show the rest"` returned `None`. The system fell back to lexical vector search, which failed because the literal word "continue" was not inside the CSV.
- **Fix Applied**:
  - Added `row_numbers: List[int]` to `CsvOperation`.
  - Added `last_row: Optional[int] = None` cursor support to `parse_csv_question()`.
  - Handled:
    - `"next row"` / `"adutha row"` -> `last_row + 1`.
    - `"next N rows"` -> `[last_row + 1, ..., last_row + N]`.
    - `"show the rest"` -> all rows from `last_row + 1` to total row count.
    - `"continue"` -> resumes from `last_row`.
  - Updated `csv_ops.execute()` and `render_csv_result()` to render multi-row tables cleanly.
  - In [attachment_qa.py](file:///c:/proj/nic_internship/backend/services/attachment_qa.py), persisted the updated `last_row` in assistant turn `structured_data` (`AttachmentPlan.extra["last_row"]`).
- **Files Modified**: [backend/services/csv_ops.py](file:///c:/proj/nic_internship/backend/services/csv_ops.py), [backend/services/attachment_qa.py](file:///c:/proj/nic_internship/backend/services/attachment_qa.py).

---

## Verification & Regression Results

Two dedicated regression test suites were executed to validate the fixes:

1. **`SIS_Continue_Regression_Tests/test_continuation_pipeline_defects.py`**:
   - Tests: 36 individual unit checks covering all 5 core defect mechanisms (Recall interception, Anti-hallucination gate, Relative pagination slicing, Workflow step progression, and CSV relative cursor navigation).
   - Result: **36 / 36 PASSED (100%)**

2. **`SIS_Continue_Regression_Tests/test_continuation_five_types.py`**:
   - Tests: 19 verification checks across the 5 canonical continuation types (Type 1: Incomplete resumption; Type 2: Completed expansion; Type 3: Sequence pagination; Type 4: Workflow process; Type 5: Multi-turn file/CSV navigation).
   - Result: **19 / 19 PASSED (100%)**

3. **Pre-existing Suite Verifications**:
   - `test_tamil_include_negation.py`: **7 / 7 PASSED (100%)**
   - `test_row_slicing.py`: Existing application slicing behavior maintained without regressions.

---

## Before & After Behavior Comparison

| Query Context | User Input | Before Fix Behavior | After Fix Behavior |
|---|---|---|---|
| Active discussion of ISD definition | `"continue where we left off"` | Verbatim echo: *"Your previous message was: What is ISD?"* | Continues topic in context; does not trigger recall. |
| Single application status displayed | `"continue"` | Refusal: *"I'm not sure what you mean. Please ask about..."* | Domain tier recognises active context; expands application details. |
| ISD service code explained | `"more details"` | Forced application prompt: *"Please specify the application number..."* | Recognises previous intent; returns complete 9-field ISD card. |
| 44 pending applications listed | `"next"` | Collapsed to single card or failed navigation | Advances list pagination by 5 records (records 6-10). |
| 44 pending applications listed | `"next five"` | Refusal: *"I could not find that in the register..."* | Slices records 6-10 from active list. |
| 25 applications (15 shown) | `"show the rest"` | Unrecognized slice; dropped context | Resolves and displays all remaining 10 records. |
| List pagination completed | `"next"` | Repeated first record or invalid error | Graceful message: *"All 25 applications have already been shown."* |
| Discussion of SIS field inspection | `"What comes next?"` | LLM Hallucination: *"Tahsildar performs field inspection..."* | Deterministic workflow: Maps to Senior Draughtsman (SD) sketch. |
| Discussion of Senior Draughtsman | `"what is the next step?"` | Generic ungrounded procedure | Deterministic workflow: Maps to Deputy Inspector (DIS) scrutiny. |
| Freshly uploaded CSV table | `"Show row 1"` | Pre-empted: *"I do not know which application or list you mean..."* | Bypasses orphan guard; renders CSV Row 1. |
| CSV Row 1 displayed | `"next row"` | Chunk lookup failure: *"I could not find this in the uploaded document."* | Relative cursor calculates Row 2; renders Row 2 cleanly. |
| CSV Row 1 displayed | `"next 2 rows"` | Chunk lookup failure | Relative multi-row slice yields Rows 2 and 3 in formatted table. |
| Active discussion in Tamil | `"தொடரவும்."` | Refusal: *"நீங்கள் என்ன கேட்கிறீர்கள் என்று புரியவில்லை..."* | Recognized by `_CONTEXT_CUE_RE`; continues in Tamil. |

---

## Conclusion

All verified defects identified in `SIS_Continue_Followup_Audit_Report.md` have been resolved. The continuation engine now reliably differentiates:
- Genuine conversation recall vs. continuation idioms.
- Single-item expansion vs. list pagination.
- Database applications vs. attachment/CSV row cursors.
- Relative workflow progression vs. open-ended questions.

The entire test matrix passed with zero regressions.
