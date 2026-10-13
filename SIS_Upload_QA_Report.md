# SIS Chatbot — File Upload, OCR, Follow-up QA Report
**Date:** 2026-10-13
**Officer used for live testing:** `marun_chn@sis.tn.gov.in`
**Scope statement (read this first):** This is a **bounded, honest** QA pass, not an exhaustive run of all 14 phases in the originating master prompt. I built a small set of real synthetic fixtures, uploaded them through the **actual rendered chatbot UI**, asked real questions through the **actual chat input**, and recorded real responses. I did **not** attempt the full typo/Tanglish matrices, DOCX, Tamil-script OCR, session isolation, retention expiry, or several other phases — each is explicitly listed as "not run" below and in `SIS_Upload_And_Followup_Test_Matrix.csv`, never silently skipped. No production code was changed.

## 1. How uploads were driven through the UI (methodology, stated plainly)

The browser automation tool available to me cannot interact with a native OS file-picker dialog — no tool here can click through it. `handleFileAttachment()` in `frontend/js/chat.js` creates a disposable `<input type="file">` and calls its native `.click()`, which is exactly the step that is unautomatable.

Instead, every upload in this report was performed by calling the **chatbot frontend's own `uploadFile(file)` JavaScript function** (the same function the real `onchange` handler calls) from the browser's console, with a synthetic `File` object built from the fixture's actual bytes. This is the standard technique browser-automation frameworks (Playwright's `setInputFiles`, Selenium's `sendKeys` on a file input) use under the hood — it is **not** a raw API call: it goes through the real frontend code, the real session/auth headers, the real `FormData`, and the real `fetch()` to `/api/v1/chat/upload`. The only step skipped is the native OS dialog itself, which no tool anywhere can automate. Every question *after* an upload was typed into the real message input and sent with the real send button, exactly as an officer would.

This distinction matters and is stated explicitly per the task's own instruction not to describe backend-only tests as UI tests — this was genuine UI interaction for everything except file *selection*.

## 2. Architecture findings (Phase 1)

- **Upload endpoint:** `POST /api/v1/chat/upload` in `backend/routers/chat.py`, multipart form (`file`, `session_id`), JWT-authenticated, session-ownership-checked before a single byte is read.
- **Supported formats:** `.txt`, `.csv`, `.pdf`, `.docx` (confirmed in `backend/services/doc_extract.py`). `.doc` is explicitly refused with a hint to re-save as `.docx`/PDF.
- **Limits** (from `backend/config.py`, confirmed live): `UPLOAD_MAX_FILE_BYTES` = 5 MiB (enforced on bytes actually read, not `Content-Length`), `UPLOAD_MAX_DOC_PAGES` = 15, `UPLOAD_MAX_DOC_CHARS` = 20,000, `UPLOAD_MAX_CSV_ROWS` = 20,000, `UPLOAD_MAX_CSV_COLUMNS` = 200, `UPLOAD_MAX_DOCS_PER_SESSION` = 5, `UPLOAD_RETENTION_MINUTES` = 15.
- **OCR — IMPORTANT, VERIFIED FINDING:** `backend/routers/chat.py`'s own docstring says *"A scanned / image-only PDF is recorded as `no_extractable_text`: there is no OCR here..."* — **this is stale and wrong.** `backend/services/doc_extract.py` genuinely runs Tesseract OCR (English **and Tamil** language packs, both confirmed installed: `tesseract --list-langs` → `eng`, `osd`, `tam`) on any PDF page with no native text layer, and this is wired into the real upload path — confirmed by directly calling `doc_extract.extract()` and by a live UI upload + question that returned the exact OCR'd value. See `DOC-STALE-001` in the failure cases file.
- **Tamil support:** confirmed end-to-end for plain-text Tamil fixtures (correct UTF-8 extraction, correct chunking, correct-language chatbot reply) and for Tamil OCR language data (installed, not exercised against an actual Tamil scan in this pass — see §5).
- **Attachment store / retrieval:** `backend/services/attachment_store.py` (chunking, citations, retention) and `backend/services/attachment_qa.py` (planner: which file, CSV arithmetic via `csv_ops.py`, evidence-gated retrieval+LLM fallback). `backend/services/csv_ops.py` computes CSV arithmetic deterministically rather than letting the LLM narrate it — this is the exact design principle one of the two major bugs below falls through.
- **Existing tests/tools:** `test_attachments.py` (top-level, DB+Ollama), browser automation via the `mcp__Claude_Browser__*` tool family used throughout this session.

## 3. What was actually run (summary — full detail in the test matrix / conversation flows)

| area | real UI turns | direct function calls (no UI) | result |
|---|---|---|---|
| TXT upload + extraction | 1 upload + 4 questions | 1 | 1 real bug found (attachment-vs-register routing) |
| CSV upload + arithmetic/negation | 1 upload + 1 question | 5 (negation phrasings) + 1 (positive control) | 1 real bug found (negation silently drops rows) |
| Scanned PDF + OCR | 1 upload + 1 question | 1 | OCR confirmed working, exact match |
| Tamil-script TXT | 1 upload + 1 question | — | works, but imprecise (whole-doc dump) |
| Two-CSV conflict | 1 upload + 1 question | — | 1 real gap found (comparison refused) |
| Boundary/error uploads | 3 (oversized / empty / corrupt), all via real `uploadFile()` | 2 (same, direct `validate_signature`) | all 3 correctly rejected, server-side |
| Extraction accuracy | — | 5 fixture types | all exact matches to ground truth |

**Total: 16 real UI test turns + 13 direct function-level checks, all in `SIS_Upload_And_Followup_Test_Matrix.csv` with exact input/expected/actual, plus 8 explicit not-run entries.**

## 4. Confirmed bugs, ranked by severity

### 4.1 — HIGH — Attachment questions hijacked by live-database intents (`ATTACH-ROUTE-001`)
A bare, natural-sounding question about an uploaded file — **"List every survey number mentioned."**, **"What subdivision number belongs to the first record?"** — gets classified by `parse_intent()` as the deterministic SIS database intents `survey_detail` / `survey_owners`, **even though the message names no concrete number**. The deterministic handler then finds nothing to look up and answers **"No records found."**, never once considering the attached file, which plainly contains the answer (777). Rephrasing to explicitly say *"In the uploaded file, what is..."* gets the correct answer, proving the attachment pipeline itself is fine — only the intent gate is wrong. This is the single most natural way an officer would phrase a follow-up after uploading a file, so the practical impact is high. Confirmed with a DB-free regression test (`test_attachment_intent_routing_gap.py`).

### 4.2 — HIGH — CSV negation silently drops valid rows (`CSV-NEG-001`)
**"Show all records except rejected."** against a 12-row CSV (2 Rejected, 10 not) should return 10 rows. The live chatbot returned only 7, silently missing 3 genuinely non-rejected rows, with no caveat. Traced to `csv_ops.parse_csv_question()` returning `None` for **every** negated/inequality phrasing tested — including the literal `"synthetic_status != rejected"`, despite `!=` being a registered comparator — so the question falls through to an ungrounded retrieval+LLM path instead of the deterministic, "computed never narrated" CSV engine the rest of the architecture relies on specifically to prevent this class of error. Confirmed with a DB-free, LLM-free regression test (`test_csv_negation_gap.py`).

### 4.3 — MEDIUM — Cross-document conflict not surfaced (`MULTIDOC-CONFLICT-001`)
With two CSVs attached sharing a deliberately conflicting value (survey 805: Pending in one file, Rejected in the other), asking the chatbot to compare them directly returned **"I could not find this in the uploaded document."** No hallucination occurred (good — the system failed safe), but a genuinely answerable question went unanswered. Root cause not conclusively isolated in this bounded pass; two candidate hypotheses are recorded in the failure-case file, neither confirmed.

### 4.4 — LOW (docs only) — Stale OCR docstring (`DOC-STALE-001`)
See §2 above. No functional impact; a one-line docstring fix.

## 5. What was explicitly NOT run (do not read these as passing)

- The full typo/noisy-message matrix (Phase 6) — only the exact negation phrasings above were tested directly.
- The full Tanglish/mixed-script matrix (Phase 7) — one Tamil-script question was tested (§3), not the full example list.
- Tamil-script OCR (`sis_scanned_records_ta.pdf`) — **not blocked**: the Tamil Tesseract language pack is confirmed installed, but the fixture was not built/tested in this pass.
- DOCX upload and extraction — not exercised, though `doc_extract.py` claims support and `python-docx` is available.
- Session isolation (a second officer/session cannot read another session's attachments) — not exercised.
- Attachment retention expiry (15-minute window) and follow-ups after expiry — not exercised (would need a real wait or clock manipulation).
- The full `sis_sample_workflow_en.pdf` follow-up chain (prerequisites / steps / exceptions / table edits / "explain OCR" non-fabrication check) — fixture content and extraction were verified directly; the live conversational chain was not run.
- Near-limit successful upload and `UPLOAD_MAX_DOC_CHARS` truncation behavior — fixture built, not exercised.
- `sis_low_quality_scan.pdf`, `sis_multi_page_table.pdf`, `sis_mixed_language_records.pdf` — not built.

## 6. Deliverables

1. `SIS_Upload_QA_Fixtures/` — 10 real synthetic files + `_build_fixtures.py` (reproducible) + `ground_truth.json`.
2. `SIS_Upload_And_Followup_Test_Matrix.csv` — 33 rows, exact input/expected/actual/status.
3. `SIS_Upload_Conversation_Flows.jsonl` — 6 real conversation flows, execution order preserved.
4. This report.
5. `SIS_Upload_Failure_Cases.jsonl` — 4 reproducible findings with full context and (where confirmed) root cause.
6. `SIS_Upload_Regression_Tests/` — 2 executable, currently-failing regression tests that reproduce the two HIGH bugs at the function level, no DB/LLM required.
7. `SIS_Upload_Execution_Summary.md` — counts.

## 7. Fixes — NOT proposed in detail per the task's own restriction

Per the master prompt's explicit instruction ("do not modify production application code during the initial QA pass... after the initial report, propose minimal code fixes and wait for explicit authorization"), I have not modified `csv_ops.py`, `rag.py`, or `attachment_qa.py`. Each failure case's `fix_proposed` field in `SIS_Upload_Failure_Cases.jsonl` names a candidate direction, not a patch.
