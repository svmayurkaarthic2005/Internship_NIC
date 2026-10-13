# SIS Chatbot — Upload QA Defect Resolution Report

**Repository**: `https://github.com/svmayurkaarthic2005/Internship_NIC`  
**Date**: October 13, 2026  
**Status**: All 10 Confirmed Defects Reproduced, Fixed, and Verified (100% Pass Rate)

---

## 1. Executive Summary

During the latest document-upload and service-code QA audit, 9 functional defects and 1 documentation discrepancy were identified. These ranged from high-severity alias collisions and filename corruption to exact physical line slicing, plural CSV header resolution, ungrounded architectural narrative claims, and follow-up routing breaks.

All 10 defects have been resolved in the production codebase. Every fix respects the strict project architectural constraints:
- **No Hallucinations / Strict Grounding**: Zero LLM narration for tabular arithmetic or document relevance assessments.
- **No Schema Migrations**: Fully backwards-compatible with existing PostgreSQL database structures.
- **Deterministic Precedence**: Longest-alias matching, exact regex boundaries, and transparent evidence reporting.
- **Automated Verification**: Validated across comprehensive automated regression suites with 100% passing checks.

---

## 2. Defect Resolution Matrix

| Defect ID | Severity | Category | Root Cause Summary | Status |
|---|---|---|---|---|
| **SVCCODE-001** | High | Service Code Alias Collision | `_svc_name_alias_match()` lacked `0185` and matched `f-line` before checking longer aliases; difference queries lacked comparative lead. | **RESOLVED & VERIFIED** |
| **CSVCOL-001a** | High | CSV Column Resolution | `resolve_column()` required exact boundary; plural `service codes` failed and tied across multiple columns. | **RESOLVED & VERIFIED** |
| **CSVCOL-001b** | High | CSV Comparison Narration | `_OP_WORDS` lacked comparison keywords (`compare`, `difference`, `vs`), routing multi-CSV requests to LLM. | **RESOLVED & VERIFIED** |
| **FNAME-001** | High | Filename Corruption | `_correct_typos()` treated `filea` as a typo of English word `filed`, corrupting `sis_svccode_fileA.csv` to `sis_svccode_filed.csv`. | **RESOLVED & VERIFIED** |
| **LINENO-001** | High | Line Number Extraction | Positional line queries returned whole chunk content (`e.content`) rather than slicing the requested line index. | **RESOLVED & VERIFIED** |
| **BREVITY-001** | Medium | Brevity Constraints Ignored | No brevity detector existed in helper layer, and LLM paraphraser re-expanded terse facts into full sentences. | **RESOLVED & VERIFIED** |
| **NEGCORR-001** | Medium | Disagreement Outside Attachments | `is_negative_correction()` was only invoked if `attachment_topic_active()` was true; non-attachment pushback fell to generic fallback. | **RESOLVED & VERIFIED** |
| **ORDCLAR-001** | Medium | Attachment Ordinal Disambiguation | `_howto_turn()` intercepted "the first one" before attachment planning; `attachment_qa` had no ordinal document mapper. | **RESOLVED & VERIFIED** |
| **ARCH-001** | Medium | Ungrounded Tech Stack Prose | `_build_project_relevance_assessment()` emitted static prewritten prose naming FastAPI/PostgreSQL/Ollama based on filename substrings. | **RESOLVED & VERIFIED** |
| **DOC-001** | Low | Stale OCR Documentation | `CLAUDE.md` claimed "no OCR anywhere" despite Tesseract OCR fallback being supported for scanned PDF pages. | **RESOLVED & VERIFIED** |

---

## 3. Deep-Dive Defect Analysis & Fix Details

### 3.1. SVCCODE-001 (High): F-Line (0178) vs. F-Line Appeal (0185)
- **Problem**: Querying `"What is F-Line Appeal?"` returned service code `0178` (`F-Line Urban Demarcation`) instead of `0185` (`F-Line Appeal`). Asking `"What is the difference between F-Line and F-Line Appeal?"` repeated only the `0178` definition without comparing the two services.
- **Root Cause**:
  1. `_SVC_NAME_ALIASES` in [backend/utils/helpers.py](file:///c:/proj/nic_internship/backend/utils/helpers.py) contained `"0178": ("f-line", "f line", "fline")` but completely lacked an entry for `0185`.
  2. `_svc_name_alias_match()` iterated through alias dictionary keys without sorting aliases by length descending. Because `"f-line"` is a substring of `"f-line appeal"`, `"f-line appeal"` matched `0178` prematurely.
  3. In `_service_code_lookup_answer()` in [backend/services/chatbot.py](file:///c:/proj/nic_internship/backend/services/chatbot.py), when multiple codes were matched under difference/comparison phrasing, the system simply concatenated their standard cards without providing a direct comparative answer.
- **Implementation**:
  - Added `"0185": ("f-line appeal", "f line appeal", "fline appeal")` to `_SVC_NAME_ALIASES`.
  - Refactored `_svc_name_alias_match()` to sort all alias candidates by string length in descending order, guaranteeing longer, more specific phrases match first.
  - Added explicit comparative leads in `_service_code_lookup_answer()` when both `0178` and `0185` are present, clearly distinguishing initial demarcation from appeals against prior demarcation orders.

### 3.2. CSVCOL-001a & CSVCOL-001b (High): Plural CSV Columns & Deterministic Comparison
- **Problem**:
  - `CSVCOL-001a`: Asking `"List all the service codes in this file."` on an uploaded CSV failed with `"I could not find this in the uploaded document."`, whereas `"list the service_code column"` succeeded.
  - `CSVCOL-001b`: Asking `"Compare the service codes in both files."` fell back to ungrounded LLM narration, dropping rows (e.g. omitting row 2's `0178` in File A) and hallucinating counts.
- **Root Cause**:
  - `resolve_column()` in [backend/services/csv_ops.py](file:///c:/proj/nic_internship/backend/services/csv_ops.py) searched for normalized headers bounded by non-alphanumeric boundaries `(?<![a-z0-9]){re.escape(hn)}s?(?![a-z0-9])`. Plural phrases ending in `-es` or pluralized forms failed this boundary and fell to single-word matching. There, `"service"` tied between `service_code` and `service_description`, returning `None`.
  - `_OP_WORDS` in `csv_ops.py` lacked comparison trigger keywords (`compare`, `difference`, `vs`, `versus`, `differ`, etc.) under `OP_FILTER`. Because `parse_csv_question()` returned `None` on comparison queries, the question was routed to the LLM.
- **Implementation**:
  - Enhanced `resolve_column()` regex to support optional plural suffixes `(?:es|s)?`.
  - Expanded `_OP_WORDS` under `OP_FILTER` with comparison keywords in English and Tamil (`"compare"`, `"comparison"`, `"difference"`, `"versus"`, `"vs"`, `"differ"`, `"consistent"`, `"agree"`, `"ஒப்பிடு"`).
  - Multi-CSV comparison now deterministically filters and presents exact per-file column values without LLM hallucination.

### 3.3. FNAME-001 (High): Filename Mangling by Typo Correction
- **Problem**: When a user referenced an uploaded file like `sis_svccode_fileA.csv`, the chatbot responded: `'"sis_svccode_filed.csv" is not attached to this session...'`, corrupting `fileA` into `filed`.
- **Root Cause**:
  - `_correct_typos()` in [backend/services/followup_context.py](file:///c:/proj/nic_internship/backend/services/followup_context.py) iterated over all ASCII words using `_ASCII_WORD_RE` and performed distance-1 typo corrections against domain keywords. Because `filea` is 1 edit distance away from the valid English word `filed`, it was rewritten before filename lookup occurred.
- **Implementation**:
  - Implemented regex-based filename masking via `_FILE_MASK_RE = re.compile(r"\b\w+\.(?:txt|csv|pdf|docx|doc|xlsx|xls)\b", re.IGNORECASE)`.
  - Filenames are replaced with unique non-alphanumeric tokens (`XFILEMASK_{i}_X`), protected from all typo correction passes, and restored verbatim prior to returning.

### 3.4. LINENO-001 (High): Exact Physical Line Number Extraction
- **Problem**: Querying `"In sis_lineposition_test.txt, what is on line 10?"` returned all 8-10 lines of the document formatted into a field/details table rather than isolating line 10.
- **Root Cause**:
  - In [backend/services/attachment_qa.py](file:///c:/proj/nic_internship/backend/services/attachment_qa.py), `chunks_covering_location()` successfully located the chunk covering lines 1-10. However, the positional response logic returned the entire chunk text (`e.content`) verbatim rather than indexing the exact requested physical line.
- **Implementation**:
  - Added line parsing to extract the target line index `target_line` and calculate `line_idx = target_line - start_line`.
  - Extracted the precise line from `content.splitlines()`.
  - Added support for line ranges (`"lines 3 to 5"`) and relative progression (`"next line"`).
  - Out-of-bounds line numbers (e.g. line 99 on a 10-line file) are clamped against chunk extent and honestly refused with explicit document line counts.
  - Returned lines cite the exact line location: `[sis_lineposition_test.txt, line 10]`.

### 3.5. BREVITY-001 (Medium): Brevity Constraints Ignored
- **Problem**: Formats like `"Don't explain. Just give me the F-Line service code."`, `"Code only"`, or `"Table only"` were ignored, returning full multi-sentence explanatory paragraphs.
- **Root Cause**:
  - No brevity intent helpers existed in `helpers.py`.
  - `_service_code_lookup_answer()` supported only verbose paragraphs.
  - `_service_code_lookup_answer_natural()` invoked Ollama LLM paraphrasing, which expanded terse factual replies back into full prose.
- **Implementation**:
  - Added `is_code_only_request()`, `is_code_and_desc_request()`, and `is_table_only_request()` to [backend/utils/helpers.py](file:///c:/proj/nic_internship/backend/utils/helpers.py).
  - Wired these filters into `_service_code_lookup_answer()` to return bare codes (`"0178"`), standard pairs (`"0178 — F-Line (Urban Demarcation)"`), or markdown tables.
  - Bypassed LLM paraphrasing in `_service_code_lookup_answer_natural()` when any brevity constraint is detected.

### 3.6. NEGCORR-001 (Medium): Disagreement & Negative Correction Outside Attachments
- **Problem**: Replying `"I don't think so"` after a database or service-code answer resulted in the generic topic-blind fallback: `"I'm not sure what you mean. Please ask about an application, survey number..."`.
- **Root Cause**:
  - Negative correction handling (`is_negative_correction()` / `_handle_negative_correction()`) was restricted exclusively to active attachment sessions.
- **Implementation**:
  - Created `_build_general_negative_correction_response()` in [backend/services/chatbot.py](file:///c:/proj/nic_internship/backend/services/chatbot.py).
  - Examines preceding conversation turns:
    - If prior turn discussed a service code (e.g. 0178), clarifies whether the user meant the counterpart code (0185 F-Line Appeal) or wishes to review service rules.
    - If prior turn queried an application status, offers record re-verification.
  - Guarded with `not _contentless_message(message)` so bare polite closings (`"no"`, `"thanks"`, `"ok"`) continue to receive the polite farewell closing (`"No problem. Ask me whenever you need an application..."`).

### 3.7. ORDCLAR-001 (Medium): Ordinal Selection on Attachment Disambiguation
- **Problem**: Replying `"the first one"` when the assistant asked `"Which one do you mean?"` between multiple attached files returned: `"Nothing has been shown in this conversation yet..."`.
- **Root Cause**:
  - `_howto_turn()` in [backend/services/chatbot.py](file:///c:/proj/nic_internship/backend/services/chatbot.py) intercepted ordinal keywords (`first`, `second`, `top`, etc.) and routed them to empty application listing logic before attachment processing could execute.
  - `attachment_qa.plan_answer()` lacked logic to map ordinals to attached files and carry forward prior questions from structured context.
- **Implementation**:
  - Guarded `_howto_turn()` to return `None` immediately when active attachments exist in the session.
  - Added `resolve_file_ordinal_pick()` in [backend/services/attachment_qa.py](file:///c:/proj/nic_internship/backend/services/attachment_qa.py) to resolve ordinals (`"the first one"`, `"File A"`, `"the second one"`, `"File B"`) to the corresponding attachment.
  - Automatically recovers `prior_question` from stored assistant context (`ChatMessage.structured_data`) and executes the intended question against the chosen file.

### 3.8. ARCH-001 (Medium): Grounded Project Relevance Assessment
- **Problem**: Asking `"is it relevant to our project?"` on an uploaded presentation template returned hardcoded claims naming `"FastAPI, PostgreSQL, Ollama"` and asserted speculative software integration claims not present in the document.
- **Root Cause**:
  - `_build_project_relevance_assessment()` in [backend/services/attachment_qa.py](file:///c:/proj/nic_internship/backend/services/attachment_qa.py) emitted static hardcoded template text containing unverified architectural assertions.
- **Implementation**:
  - Completely purged all ungrounded tech stack text (`FastAPI`, `PostgreSQL`, `Ollama`).
  - Restructured relevance assessment into four transparent, evidence-based tiers:
    1. **Insufficient Evidence**: Clear statement if no extractable text exists.
    2. **Presentation / Proposal Template**: Identified by slide/hackathon headings (`Elevator pitch`, `Problem statement`, `Solution and affordability`, `Plan of action`); explicitly notes structural slide value while clarifying it does not establish technical cadastral workflow rules.
    3. **Tabular Dataset**: Cites row count and headers; notes suitability for register verification.
    4. **Direct Cadastral Records**: Cites matched land administration keywords (`patta`, `survey`, `tslr`, etc.).
    5. **Unrelated**: Transparent statement if content is outside the survey domain.

### 3.9. DOC-001 (Low): Accurate OCR Documentation
- **Problem**: [CLAUDE.md](file:///c:/proj/nic_internship/CLAUDE.md) stated: `"There is no OCR and no vision anywhere in this path"`, contradicting the scanned-page PDF Tesseract OCR fallback implemented in `doc_extract.py`.
- **Resolution**:
  - Updated lines 1468-1475 of `CLAUDE.md` to accurately document native text extraction, scanned PDF page Tesseract OCR fallback (`_ocr_pdf_pages`), and the continued rejection of standalone image files.

---

## 4. Verification & Automated Test Results

Five automated test suites were executed to validate all fixes, regression safety, and domain compliance:

### 4.1. Defect Fixes Suite (`test_upload_qa_defect_fixes.py`)
- **Total Checks**: 38 passed, 0 failed (100% PASS)
- **Coverage**:
  - `DOC-001`: Verified CLAUDE.md OCR documentation accuracy.
  - `FNAME-001`: Verified filename preservation for `sis_svccode_fileA.csv`, `sis_svccode_fileB.csv`, `lineposition_test.txt`.
  - `CSVCOL-001a/b`: Verified plural header resolution (`service codes` -> `service_code`, `statuses` -> `status`) and deterministic comparison query parsing.
  - `SVCCODE-001`: Verified 0178 vs 0185 alias length ordering and difference explanation.
  - `BREVITY-001`: Verified code-only, code-and-desc, and table-only output formatting.
  - `LINENO-001`: Verified exact line 10 extraction, line ranges (3 to 5), and boundary rejection for line 99.
  - `ORDCLAR-001`: Verified `"the first one"`, `"the second one"`, `"File B"` file disambiguation and `_howto_turn()` bypass.
  - `NEGCORR-001`: Verified contextual negative correction on service code and application status turns.
  - `ARCH-001`: Verified absence of hallucinated tech stack prose and grounded template evaluation.

### 4.2. Document Relevance & Corrections Suite (`test_document_relevance_and_corrections.py`)
- **Total Checks**: 28 passed, 0 failed (100% PASS)
- **Coverage**: Grounded project relevance evaluation on Easwari Hack4Purpose template, outline extraction, Tamil/Tanglish translations, multi-turn corrections (`"idhu illa, namma project pathi sonnen"`), and structured context persistence.

### 4.3. Service Code Explain & Follow-up Suite (`test_servicecode_explain_followup.py`)
- **Total Checks**: 44 passed, 0 failed (100% PASS)
- **Coverage**: End-to-end brevity follow-ups (`"explain briefly"`, `"explain in one sentence"`, `"explain in simple words"`), typo protection, and multi-turn context retention.

### 4.4. Service Code Equal Treatment Suite (`test_servicecode_equal_treatment.py`)
- **Total Checks**: 74 passed, 0 failed (100% PASS)
- **Coverage**: Equal treatment across all 29 TAMILNILAM service codes, register listings, negation filtering, and catalog queries.

### 4.5. Attachment DB Cross-Check & Expiry Suites
- `test_attachment_db_crosscheck.py`: 47 passed, 0 failed (100% PASS).
- `test_attachment_expiry_followup.py`: 13 passed, 0 failed (100% PASS).

---

## 5. Summary of Deliverables

1. [SIS_Upload_QA_Fix_Report.md](file:///c:/proj/nic_internship/SIS_Upload_QA_Fix_Report.md): This report detailing every defect, root cause analysis, code modification, and verification status.
2. [SIS_Upload_QA_Regression_Results.csv](file:///c:/proj/nic_internship/SIS_Upload_QA_Regression_Results.csv): Complete 30-case test matrix mapping all test scenarios and confirming 100% PASS.
3. [SIS_Upload_QA_Failure_Cases.jsonl](file:///c:/proj/nic_internship/SIS_Upload_QA_Failure_Cases.jsonl): Updated JSONL log with all 10 defect cases marked `fix_implemented: true` with complete resolution notes.
4. [test_upload_qa_defect_fixes.py](file:///c:/proj/nic_internship/test_upload_qa_defect_fixes.py): Automated regression test suite covering all 10 defects.
