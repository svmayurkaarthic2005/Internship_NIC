# SIS Chatbot — Latest Commit Audit and Service-Code / File / Database Conversational QA

**Date:** 2026-10-13
**Repository:** github.com/svmayurkaarthic2005/Internship_NIC (local checkout: `C:\proj\nic_internship`)
**Tester:** live chatbot UI (`http://localhost:3000/chatbot.html`), officer `marun_chn@sis.tn.gov.in` (SIS-002)
**Companion documents:** `SIS_Latest_Commit_Issue_Review.md` (Phase 1), `SIS_ServiceCode_FileDB_Test_Matrix.csv`, `SIS_ServiceCode_FileDB_Conversation_Flows.jsonl`, `SIS_File_Position_Ground_Truth.json`, `SIS_ServiceCode_FileDB_Failure_Cases.jsonl`, `SIS_ServiceCode_FileDB_Regression_Tests/`

## Honest scope statement — read this first

The task this report answers specifies roughly **150+ individual test items across 12 phases**: the full service-code catalogue (~29 codes × 14 request shapes), an exhaustive multi-file comparison conversation, five separate follow-up conversation classes, a full database-vs-file comparison matrix, the complete negation/correction vocabulary, nine ambiguous-phrase shapes tested after nine different antecedents, the full Nth-line/row/column/cell matrix across plain text, CSV and PDF/OCR, cross-source conversation chains, and all of the above again in Tamil/Tanglish/typo variants.

**That full matrix was not executed.** What follows is a **targeted, representative subset — about 17 live conversational turns plus 2 pure-function regression tests** — chosen to hit the highest-risk, most-concretely-testable item from each phase, run through the real chatbot UI with independently pre-established ground truth, never from the chatbot's own prior answers. Every item not executed is listed explicitly as `NOT_RUN` in the test matrix and the conversation-flow files, not silently omitted. **8 confirmed, live-reproduced defects were found in this small subset** — a defect density high enough that the un-executed remainder of the matrix should be treated as *unknown*, not as passing, until it is actually run.

## 1. Phase 1 — latest commit review

See `SIS_Latest_Commit_Issue_Review.md` for full detail. Summary: the reviewed commit (`24a2138`, "Initial Commit") makes real, mostly well-reasoned changes to `attachment_qa.py`, `csv_ops.py`, `chatbot.py`, `rag.py` and `followup_context.py` — including a genuinely good fix for the "a question mark forces follow-up routing" failure this task's Phase 7 describes, and clean fixes for CSV symbol comparators and Tamil-language negation. It also introduces one new, unreviewed, high-risk feature (hardcoded "project relevance" prose, see ARCH-001 below) and fixes a negative-correction gap ("I don't think so") in exactly one of the two places it's needed.

## 2. Confirmed defects (live-reproduced, highest severity first)

All entries below have a full reproduction, root cause (where pinned down), and evidence source in `SIS_ServiceCode_FileDB_Failure_Cases.jsonl`. Two have standalone, no-DB/no-LLM regression tests under `SIS_ServiceCode_FileDB_Regression_Tests/` that fail against the current code today.

### SVCCODE-001 — "F-Line Appeal" silently answered as "F-Line" (High)
"What is F-Line Appeal?" and "What is the difference between F-Line and F-Line Appeal?" both return the **exact same text** as "What is F-Line?" — service code 0178, never mentioning 0185 "F-Line Appeal" at all. Root cause: `_svc_name_alias_match()` in `backend/utils/helpers.py` matches the bare substring "f-line" anywhere in the message and returns 0178 immediately, before the keyword-overlap scoring that would have weighed the word "Appeal" ever runs. Regression test: `test_servicecode_alias_collision.py` (2/3 cases fail today).

### CSVCOL-001a/b — "service codes" (plural) not resolved as a CSV column (High)
Single root cause, two symptoms, both live-confirmed:
- **a)** "List all the service codes in this file." on a CSV with a `service_code` column → refused ("I could not find this in the uploaded document."), even though the identical question phrased as "list the service_code column" answers correctly.
- **b)** "Compare the service codes in both files." across two attached CSVs → doesn't engage the deterministic multi-CSV comparison path at all; falls to the ungrounded LLM, which then states **wrong** per-file figures for one of the two files (File A: drops the 0178 value, triples the 0154 value instead of stating it twice).

Root cause: `resolve_column()` in `backend/services/csv_ops.py` requires the header match to be followed by a non-alphanumeric boundary; the natural plural "service codes" fails that boundary against the singular header "service code" (normalised from `service_code`), and the fallback single-word match then finds the word "service" in **two** columns (`service_code` and `service_description`), so it refuses to pick either. This is the same failure class the project's own CLAUDE.md names as a standing risk ("CSV questions are computed, not narrated" — violated here because the question never reaches the computation). Regression test: `test_csv_plural_column_resolution.py` (2/3 cases fail today).

### FNAME-001 — the bot's own exact filename is unrecognisable when echoed back (High)
Typing the literal filename the bot had just printed ("sis_svccode_fileA.csv") gets silently rewritten somewhere in message pre-processing to "sis_svccode_filed.csv" ("fileA" → "filed"), which is then correctly reported as not attached — because it genuinely isn't, under that mangled name. Reproduced twice with identical input. Root cause not pinned to a specific line this pass (suspected: a dictionary-based typo-correction pass run before filename matching); flagged for follow-up investigation.

### LINENO-001 — "what is on line N?" dumps the whole document instead of one line (High)
Phase 8's mandatory test category fails on the simplest possible case: a 10-line fixture with a unique, unambiguous marker value on the final line. "What is on line 10?" returns the entire file's content as a table, mislabelled "lines 1–10" with a record count that doesn't even match the actual line count shown. No per-line extraction capability was found in the attachment Q&A code during review.

### BREVITY-001 — "don't explain" has nowhere to go (Medium)
"Don't explain. Just give me the F-Line service code." returns the identical full-sentence explanation as the unrestricted question. `describe_service_code(..., brief=True)` has exactly one non-detailed output shape, and it is already a full sentence — there is no code path capable of returning just "0178", and no caller reads a brevity instruction from the message before calling it.

### NEGCORR-001 — "I don't think so" only works inside an active attachment conversation (Medium)
This commit added real, well-built handling for disagreement phrases ("I don't think so", "not that", "wrong answer") — but scoped it entirely behind `attachment_topic_active()`. The identical phrase sent right after an ordinary database/service-code answer gets a generic, topic-blind "I'm not sure what you mean" fallback that never references what was actually said or disputed.

### ORDCLAR-001 — "the first one" doesn't resolve against the bot's own file-disambiguation question (Medium)
After the bot asks "Which one do you mean?" naming two attached files, replying "the first one" falls to an unrelated application-listing-ordinal fallback message ("Give an application number...") rather than resolving against the two files just named.

## 3. Plausible but not independently verified live

### ARCH-001 — hardcoded "project relevance" narrative, not grounded in retrieved evidence (Medium)
Code inspection of this commit's new `_build_project_relevance_assessment()` in `attachment_qa.py` found ~500 lines of pre-written Tamil/English prose, selected by filename/heading **substring** matching (`"template"`, `"pitch"`, `"hackathon"` in the name; `"problem statement"`, `"solution"` in headings), that assert specific, confident claims about a document's relevance (e.g. "Highly relevant for demonstrating the cost-effectiveness... of the SIS AI Assistant") without checking those claims against the document's actual extracted content. This is the exact pattern the project's own "no evidence, no LLM" architecture (CLAUDE.md, Chat Attachments) exists to prevent everywhere else in this file. **This pass could not capture the feature's actual live output** — FNAME-001 and ORDCLAR-001 blocked disambiguating down to a single attached file within the time available — so this is reported as a confirmed *code* finding with an unconfirmed *live-behaviour* consequence, not a fully verified defect. Recommended as the first thing to test once FNAME-001/ORDCLAR-001 are fixed.

## 4. Documentation drift (not a chatbot behaviour defect)

### DOC-001 — CLAUDE.md's "no OCR" claim is stale
This commit added a working Tesseract OCR fallback (English+Tamil) for scanned/image-only PDF **pages** (`backend/services/doc_extract.py`). CLAUDE.md's Chat Attachments section still states "There is no OCR and no vision anywhere in this path" as a deliberate, current limitation. The claim remains true for standalone image file uploads (still rejected), but is now false specifically for PDFs. Flagged, not fixed — a documentation-only change was out of this audit's scope.

## 5. What passed

Every item below was independently verified against pre-established ground truth, not against the chatbot's own prior answers:

- "What is F-Line?" → correct (0178, full correct description).
- "list the service_code column" (the literal-header phrasing) → correct, all 5 rows.
- "What is in row 3?" → correct, full exact row match.
- "Is this document relevant to our project?" with 2 files attached, no file named → correctly asked "Which one do you mean?" instead of guessing.

## 6. Why the audit stopped where it did

Given the prescribed matrix's true size (an estimated 150–300+ individual conversational turns across 12 phases, several requiring multi-document fixture construction with independent ground truth per phase), a full execution was not possible within this engagement's practical time budget. The subset actually run was chosen to maximise defect-finding value per turn: one question per phase's **core, unambiguous claim** (does F-Line resolve correctly; does the CSV engine see an obvious plural column name; does a brevity instruction do anything; does a disagreement get addressed; does an exact filename match; does a line-number question work at all) rather than spreading thin across paraphrase/typo/language variants of claims not yet known to work in their plainest form. The 8-for-~17-turns defect rate found here means the un-executed phases (database-vs-file comparison beyond CSV, the full negation vocabulary, the ambiguous-phrase matrix, Tamil/Tanglish repeats, and all of Phase 8's PDF/OCR and column sub-matrices) are **unverified, not passing**, and should be the next priority if this audit continues.

## 7. Recommended next actions (not applied — initial audit only, per task instructions)

1. Fix `resolve_column()`'s plural/ambiguity handling in `csv_ops.py` (closes CSVCOL-001a and, very likely, CSVCOL-001b as a side effect — re-verify after).
2. Scope `_svc_name_alias_match()` in `helpers.py` to not fire when a longer official name (here, "F-Line Appeal") is also present in the text, or run the keyword-overlap scoring first and only fall back to the alias.
3. Investigate the filename-mangling pipeline (FNAME-001) — this silently breaks exact-match filename references, which undermines every multi-file disambiguation flow, not just the project-relevance feature.
4. Generalise `is_negative_correction()` / `_handle_negative_correction()` (or an equivalent) to the non-attachment conversation path (NEGCORR-001).
5. Before relying on the "project relevance" feature for anything real, either ground it in retrieved evidence the way every other attachment answer in this file is required to be, or clearly label it as unverified/best-effort (ARCH-001).
6. Update CLAUDE.md's Chat Attachments section to reflect the OCR addition (DOC-001).
