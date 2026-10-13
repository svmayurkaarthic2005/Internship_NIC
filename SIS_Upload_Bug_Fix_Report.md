# SIS Chatbot — Upload QA Bug-Fix Report
**Date:** 2026-10-13
**Source QA pass:** `SIS_Upload_QA_Report.md` (2026-10-13), `SIS_Upload_Failure_Cases.jsonl`
**Officer used for live verification:** `marun_chn@sis.tn.gov.in`
**No production database was seeded, cleared or modified. No commit/push was made.**

## Summary

| id | severity | fixed | reproduced-after-fix (live UI) | regression tests |
|---|---|---|---|---|
| ATTACH-ROUTE-001 | High | ✅ Yes (two root causes, see §1) | ✅ Both original repro questions now answer correctly | 12/12 + 17/17 (new) |
| CSV-NEG-001 | High | ✅ Yes | ✅ Exact original wording now returns the correct 10 rows | 18/18 |
| MULTIDOC-CONFLICT-001 | Medium | ✅ Yes, root cause confirmed and fixed | ⚠️ Partial — see §3's caveat | 23/23 |
| DOC-STALE-001 | Low (docs only) | ✅ Yes | — (docstring only) | — |

All four issues from the QA pass are addressed. One of the three functional fixes (ATTACH-ROUTE-001) needed two additional, deeper root causes beyond what the original QA pass had isolated — found only by re-driving the exact original failing conversations through the real chatbot UI after the first-layer fix, per this task's own instruction to re-run the original failing conversations and not just the unit-level regression tests. Both were investigated and fixed with explicit authorization mid-session (see §1.2).

---

## 1. ATTACH-ROUTE-001 — High — Attachment questions hijacked by live-database intents

### 1.1 The originally-identified root cause (routing) — fixed

**Bug:** `parse_intent()` in `rag.py` classifies "survey number" / "subdivision number" wording as the deterministic `survey_detail` / `survey_owners` intents even when the message names no concrete number. With an attachment active, the deterministic DB handler found nothing to look up and answered "No records found." — the attachment pipeline was never consulted.

**Fix (already present when this verification pass began):** a new `_is_unresolved_survey_question()` in `chatbot.py` — called from both `process_chat()` and `process_chat_stream()` — reclassifies the turn to `general_query` when: the intent is `survey_detail`/`survey_owners`, the message carries no digit, nothing in the officer's follow-up context names a survey or application either, AND an attachment is actually active (`attachment_qa.any_active_documents()`). `parse_intent()` itself is deliberately left unchanged — it is a pure, stateless function every other caller relies on; narrowing it directly would have been the keyword-only workaround the task explicitly warns against.

**Files:** `backend/services/chatbot.py` (`_is_unresolved_survey_question`, call sites in both chat paths).

**Verified:** `SIS_Upload_Regression_Tests/test_attachment_intent_routing_gap.py`, 12/12. Live UI: the officer's question no longer gets "No records found." from the register.

### 1.2 A second, deeper defect found by re-driving the original conversations live — fixed

Re-sending the bug's own two original repro questions through the real chatbot UI (not just the routing unit test) showed the routing fix alone did not resolve the original complaint — a *different* wrong answer replaced the first one:

| question | before any fix | after routing fix only (still wrong) | after this fix |
|---|---|---|---|
| "List every survey number mentioned." | "No records found." | "I could not find survey numbers in the uploaded document." (reproduced twice, consistently) | "The survey number mentioned is 777." |
| "What subdivision number belongs to the first record?" | "Which survey number...?" then "No records found." | "I could not find this in the uploaded document." | "The subdivision number mentioned is 777/9." |

**Root cause A (the "list" question):** evidence retrieval was correct (score 0.44, above the 0.4 floor) and the retrieved chunk genuinely contained `Synthetic Survey Number: 777` — confirmed by calling `attachment_store.retrieve_evidence()` and `rag.call_llama()` directly with the real prompt. The LLM (llama3.1:8b) read "list"/"every" as requiring more than one match and refused rather than treating one value as a complete list of one. A prompt-only instruction addition reduced but did not reliably eliminate this across phrasings ("List survey numbers.", "List all survey numbers mentioned." still refused after the prompt change) — confirming the project's own established principle that an 8B model's wording judgement cannot be trusted for this class of question.

**Root cause B (the "subdivision" question):** evidence retrieval itself returned *nothing* (lexical score 0.0) despite the document containing `Synthetic Sub-division Number: 777/9`. `_TOKEN_RE` in `attachment_store.py` keeps a hyphen as part of a token (needed for codes like sub-division numbers elsewhere), so the question's `subdivision` (no hyphen) and the document's `Sub-division` (hyphenated) tokenised to two different strings and never matched.

**Fix:**
- `backend/services/attachment_qa.py`: a new `len(values) == 1` branch in `_answer_from_selected()`'s existing "one entity, or ask which" block (which already extracted a `survey number` / `application number` deterministically via regex to decide whether to ask "which one?") now answers that single, already-extracted value directly via a new `_single_entity_answer()` helper — bypassing the LLM's unreliable judgement entirely for this shape of question. A new `subdivision number` entry was added to `_ENTITY_PATTERNS` / `_ENTITY_QUESTION_WORDS` (previously only `survey number` and `application number` were registered). `distinct_entities()`'s capture-group handling, previously hardcoded to `entity == "survey number"`, was generalised to any pattern that has its own capture group, so the new subdivision pattern's `777/9` is extracted correctly rather than the whole raw matched text.
- `backend/services/attachment_store.py`: a new `_ALIASES` entry (`"subdivision"` / `"sub-division"`, both directions) — not a cross-script Tamil/English pair like the rest of that table, but reusing its exact matching mechanism for a plain English hyphen-spelling variant, since `_TOKEN_RE` itself was deliberately left untouched (it also needs to keep hyphens in genuinely meaningful places).
- `build_prompt()` also gained a general "a single value IS a complete list of one" rule as defense-in-depth for free-text fields that have no registered `_ENTITY_PATTERNS` entry (where the deterministic path cannot apply).

**Files:** `backend/services/attachment_qa.py`, `backend/services/attachment_store.py`.

**Verified:** live UI, officer `marun_chn@sis.tn.gov.in`, exact original wording, both questions — see table above. Direct function-level: `SIS_Upload_Regression_Tests/test_attachment_single_entity_completion.py`, 17/17 (new file).

**Known remaining gap (documented, not hidden):** "List survey numbers.", "List all survey numbers mentioned.", "What survey numbers are mentioned?" (plural-noun phrasings of the *same* question, not the bug's own original wording) still return the deterministic evidence-floor refusal in this exact single-document session — the lexical score for these specific plural phrasings comes in just under the 0.4 floor, so no evidence reaches either the entity-matcher or the LLM. This is a different, narrower, lower-severity issue than the one originally reported (a safe "no evidence" refusal, not a wrong-but-confident answer) and was not part of the three confirmed defects authorized for this pass. Flagging it here rather than silently leaving it undocumented.

---

## 2. CSV-NEG-001 — High — CSV negation silently dropped rows

**Bug:** `csv_ops.parse_csv_question()` returned `None` for every negated/inequality phrasing against a status column, including the literal `"synthetic_status != rejected"` despite `!=` being a registered comparator. The question fell through to the ungrounded retrieval+LLM path, which silently dropped 3 of 10 genuinely matching rows with no caveat.

**Fix (already present when this verification pass began):**
- The symbolic comparators (`>=`, `<=`, `!=`, ...) are now rewritten to placeholder words *before* `_norm()` runs — `_norm()` reduces any run of non-alphanumeric characters to a single space, which previously erased every symbol silently, so the default `eq` operator was always used regardless of what symbol was typed.
- The column-anchored regex's `(?:is|...)?` fragment had no word boundary of its own and was consuming "is" as a bare prefix of "isn't" (normalised "isn t"), destroying the negation; it now requires "is" to be a whole word and does not swallow a following "not".
- The bare-value fallback path (`"show all records except rejected"`, no explicit column named) now checks for a negator word (`except`/`excluding`/`other than`/`not`/...) within a bounded word-gap before the matched categorical value, producing an `ne` condition instead of leaving the question unmatched.
- `parse_csv_question()` gained a case for a negated bare-value condition with no explicit column word, which previously fell all the way through to `return None`.

**Files:** `backend/services/csv_ops.py`.

**Verified:** live UI, exact original wording — "In sis_survey_records_en.csv, show all records except rejected." → "10 row(s) match (synthetic_status ≠ Rejected). [sis_survey_records_en.csv, rows 1–2, 4–6, 8–12]" (previously 7 rows, 3 silently missing). `SIS_Upload_Regression_Tests/test_csv_negation_gap.py`, 18/18.

---

## 3. MULTIDOC-CONFLICT-001 — Medium — Cross-document conflict not surfaced

**Bug:** `attachment_qa._answer_from_selected()`'s CSV branch was gated on `if len(csv_docs) == 1:`. With two CSVs selected, that guard was never true, so the deterministic "computed, never narrated" `csv_ops` engine never ran at all, and the turn fell through to the ungrounded retrieval+LLM path, which had no grounded answer to find across two separate row sets it never combined — and refused.

**Fix (already present when this verification pass began):** a new `elif len(csv_docs) >= 2:` branch runs the *same* `csv_ops.parse_csv_question()` / `execute()` independently against each file's own rows (never merged), and a new `render_multi_csv_result()` states agreement or the conflict, each value kept beside the filename it came from — the task's explicit safeguard: preserve source identity, never silently select or merge values.

**Files:** `backend/services/attachment_qa.py`.

**Verified, with a caveat:** live UI, with the fixture's own real header names ("what is the synthetic_status where synthetic_survey_no is 805") — the fix works perfectly: both files' values are shown separately, with per-file citations (`sis_survey_records_en_v2.csv, row 1: Rejected`; `sis_survey_records_en.csv, rows 5, 11: Pending, Approved`). `SIS_Upload_Regression_Tests/test_multidoc_conflict_gap.py`, 23/23 — including agreement, 3-file, and count-non-merging cases.

**The bug's own exact original wording ("is the status of survey number 805 consistent between the two files?") still fails live, unchanged from before this fix** — but for a *separate*, pre-existing, explicitly out-of-scope reason the regression test's own docstring already documents: `csv_ops`'s column matching requires the question to contain the header's own literal text, and the fixture's deliberately verbose, self-documenting header (`synthetic_survey_no`) never matches an officer's ordinary phrasing ("survey number"), for a single file or several, before or after this fix. This is a known, pre-existing `csv_ops` limitation (fuzzy column-name matching), not a flaw in the `len(csv_docs) >= 2` fix, and was explicitly left out of this pass's authorized scope.

---

## 4. DOC-STALE-001 — Low, documentation only

**Finding:** `backend/routers/chat.py`'s `upload_chat_file()` docstring stated "there is no OCR here", contradicting `doc_extract.py`'s real, working Tesseract OCR (English + Tamil).

**Fix (already present when this verification pass began):** docstring corrected to describe the actual OCR behaviour.

**Files:** `backend/routers/chat.py`.

---

## Source-diff summary

| file | change |
|---|---|
| `backend/services/rag.py` | unchanged by this pass (deliberately — see §1.1) |
| `backend/services/chatbot.py` | `_is_unresolved_survey_question()` + call sites (pre-existing when this pass began) |
| `backend/services/csv_ops.py` | symbol-to-word rewrite before `_norm()`; `is`-boundary fix; negated bare-value fallback case (pre-existing when this pass began) |
| `backend/services/attachment_qa.py` | `elif len(csv_docs) >= 2:` + `render_multi_csv_result()` (pre-existing); **this pass:** `len(values) == 1` single-entity-completion branch, `_single_entity_answer()`, `subdivision number` entity registration, generalised capture-group rule in `distinct_entities()`, a "list of one" prompt rule in `build_prompt()` |
| `backend/services/attachment_store.py` | **this pass:** `subdivision`/`sub-division` alias pair in `_ALIASES` |
| `backend/routers/chat.py` | docstring correction (pre-existing when this pass began) |

## Test results

| suite | result |
|---|---|
| `SIS_Upload_Regression_Tests/test_attachment_intent_routing_gap.py` | 12/12 |
| `SIS_Upload_Regression_Tests/test_csv_negation_gap.py` | 18/18 |
| `SIS_Upload_Regression_Tests/test_multidoc_conflict_gap.py` | 23/23 |
| `SIS_Upload_Regression_Tests/test_attachment_single_entity_completion.py` (new) | 17/17 |
| `test_attachment_routing.py` | 6/6 |
| `test_attachment_storage_safety.py` | 14/14 |
| `test_attachment_expiry_followup.py` | 13/13 |
| `test_document_relevance_and_corrections.py` | 28/28 |

All runs executed, none assumed. No test was weakened to pass. Full detail in `SIS_Upload_Regression_Test_Results.csv`.

## Outstanding, explicitly not run in this pass (unchanged from the original QA report)

Per `SIS_Upload_QA_Report.md` §5: the full typo/noisy-message matrix, the full Tanglish matrix, Tamil-script OCR, DOCX upload, session isolation, attachment-retention expiry, the full `sis_sample_workflow_en.pdf` conversational chain, near-limit/truncation behaviour, and three unbuilt fixtures. None of these were exercised in this bug-fix pass either; do not read their absence here as newly passing.

New, additionally-outstanding items from this pass:
- The plural-noun "list" phrasings noted in §1.2's "known remaining gap".
- `csv_ops`'s fuzzy column-name matching (the separate limitation noted in §3).
