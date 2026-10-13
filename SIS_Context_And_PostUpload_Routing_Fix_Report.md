# SIS Chatbot — Follow-up Context, Negative Corrections and Post-Upload Routing: Fix Report

**Date:** 2026-10-13
**Repository:** github.com/svmayurkaarthic2005/Internship_NIC (local checkout: `C:\proj\nic_internship`)
**Builds on:** `SIS_FLine_Appeal_Upload_Retrieval_Fix_Report.md` (F-Line/F-Line Appeal, plural CSV columns, the narrow `service_code_lookup` post-upload exemption)
**Tester:** live chatbot UI (`http://localhost:3000/chatbot.html`), officer `marun_chn@sis.tn.gov.in` (SIS-002)
**Companion documents:** `SIS_Context_And_PostUpload_Regression_Results.csv`, `SIS_Context_And_PostUpload_Failure_Cases.jsonl`, `SIS_ServiceCode_FileDB_Regression_Tests/test_negative_correction_antecedent_routing.py`

## 0. Protecting existing work

`git status` at the start of this pass showed the same modified files as the end of the prior pass (`attachment_qa.py`, `chatbot.py`, `csv_ops.py`, `helpers.py`, `rag.py`, `followup_context.py`, `CLAUDE.md`), plus a large, unrelated set of changes and new `SIS_Continue_*` artifacts from a separate, concurrent session working on continuation/pagination idioms in the same files. Nothing from that session was reverted or altered. The three regression test files from the F-Line Appeal pass were re-run before any new edit and confirmed still passing (`test_servicecode_alias_collision.py`, `test_csv_plural_column_resolution.py`, `test_fline_appeal_multicode_comparison.py` — 12/12). All line numbers and function bodies were re-read from the current file state before editing, not assumed from the prior report.

## 1. Negative-correction antecedent resolution (FLINE-NEGATION-001)

### Reproduction

The exact reported sequence was reproduced live before any change:
1. "What is the difference between F-Line and F-Line Appeal?" → correct, both codes.
2. "Compare the service codes in both files." → correct, deterministic 2-file table.
3. "I don't think they are the same." → **"You have 2 files attached in this session: ... Which one do you mean?"** — silently resolved against the file topic, the F-Line/F-Line Appeal claim never considered.

### Root cause

`attachment_qa.plan_answer()`'s negative-correction gate asks exactly one question: was the **single most recent** assistant turn about a file (`attachment_topic_active()`)? If yes, it claims the correction outright and hands it to `_handle_negative_correction()`, which only ever reasons about files — it has no visibility into anything said further back in the conversation. A genuinely more-plausible non-attachment claim never gets a chance to be the answer.

### Fix — three parts, found iteratively by testing each fix live

**Part 1 — antecedent routing.** A new `_route_negative_correction()` in `backend/services/chatbot.py` runs *before* the attachment layer is given the turn. It checks, in order:
1. Does the message itself explicitly name one side ("the files...", "the definitions...")? If so, use it — no ambiguity check at all (task requirement D).
2. Is there an active attachment topic **and** a plausible non-attachment claim (service code / application / field visit) within the last 3 assistant turns? If both, ask a clarification **naming both alternatives** — never silently pick one (requirement B).
3. Otherwise, whichever single side is plausible (or neither, in which case the existing generic fallback runs unchanged — requirement E).

A dedicated `_NEGCORR_HISTORY_SVC_CUE_RE` is used for scanning *history* (no bare digit codes — "0178" appearing as a **data value** inside a rendered CSV table must not be read as a service-code **claim**; a bug caught by this pass's own first test and fixed before it reached the live report).

**Part 2 — subject disambiguation.** "I don't think the files have the same service codes." names both "files" and "service codes" — the symmetric explicit-cue test treated this as unresolvable. A `_NEGCORR_FILE_SUBJECT_RE` pattern ("the files **have/contain/match/...**") recognises "files" as the grammatical subject and routes it to the attachment side outright, regardless of what attribute is also named (requirement C).

**Part 3 — stale-context and sub-action bugs, found while testing Part 2 live.** Two further issues surfaced only once the full sequence was re-driven through the UI:
- The router's own "ambiguous" clarification was saved with no attachment context, which made the *next* turn's `attachment_topic_active()` read `False` even though nothing about the actual file context had changed. Fixed by carrying the prior attachment context's `document_ids` forward onto the clarification's own saved structured data.
- When the correction *did* reach the attachment handler, `_handle_negative_correction()`'s multi-document branch only knew "ask which file" — it had no case for "the disputed answer itself spanned both files" (a comparison). Fixed with a new branch that re-targets every document the prior comparison used and offers to recheck it, checked before the generic "which file" fallback.

**Regex widening (a fourth, smaller fix, NEGCORR-REGEX-GAP-001).** Several of the task's own required phrases — `"that's incorrect"`, `"I don't think they are the same"`, `"I don't think the files have the same service codes"`, `"I don't think the F-Line and F-Line Appeal definitions are correct"` — were not recognised as negative corrections *at all* by the existing `is_negative_correction()` regex (it only matched the exact phrase "I don't think so"). Widened with a bounded general pattern (`i don't think <≤6 words> is/are/was/were/have/has/match(es)`, hyphen-aware so "F-Line" survives as one token) and a bare `"that's incorrect"` alternative. Verified against both the required phrases and a negative-control set of ordinary SIS questions (none newly misclassified).

### Live verification (full 4-turn sequence, fresh conversation, after all fixes)

| Turn | Message | Response |
|---|---|---|
| 1 | "What is the difference between F-Line and F-Line Appeal?" | Both codes correctly explained |
| 2 | "Compare the service codes in both files." | Correct deterministic 2-file table |
| 3 | "I don't think they are the same." | "Understood — it's not clear which answer you mean: the uploaded file(s), or the service-code comparison you just asked about? Let me know which one, and I'll re-check it." |
| 4 | "I don't think the files have the same service codes." | "Understood — let me recheck the comparison between sis_svccode_fileB.csv and sis_svccode_fileA.csv. Name the specific service code or row you think is wrong, or I'll show the full comparison again." |

Also verified: a bare "I don't think so." with no prior answer in the conversation gets the honest "please clarify what you're looking for" fallback (requirement E) — the router correctly declines to guess when nothing is plausible.

### Not independently re-verified

- Requirement F (a negated reply to a bot-*initiated* yes/no question) was confirmed at the classification level (`is_negative_correction()` correctly returns `True` for "No, I don't think so.") but not staged as a live bot-asks-then-officer-answers flow — the routing mechanism is the same regardless of who asked the question first, so this is assessed as very likely covered, not confirmed by its own transcript.
- Tamil/Tanglish negative-correction phrasings use the pre-existing Tamil patterns in `_NEGATIVE_CORRECTION_RE`, unchanged this pass; not independently re-verified.

## 2. Post-upload routing generalization (ATTACH-INTERCEPT-002)

The prior pass's fix exempted exactly `intent == "service_code_lookup"` from the attachment topic-active fallback, with an explicit note that other intents might share the exposure. Per this task's instruction not to grow a one-off exemption list, the condition is now **`intent != "general_query"`** — the same rule `targets_attachment()` itself already uses, one line above, to decide whether to even consider the attachment in the first place. Any intent the deterministic parser resolved to something specific always has its own complete handler downstream; `general_query` is the one shape that has nothing else to answer it and is the only one the attachment fallback should ever be allowed to catch.

**Live verification**, one session: upload a CSV → "Which applications are overdue?" (first message, unrelated deterministic DB intent) → correct register answer → "What is F-Line Appeal?" → correct 0185 → "List all the service codes in this file." → correct deterministic enumeration. All three intents coexist correctly in the same conversation.

Two additional spot-checks (separate sessions, file attached, asked as the very first message): "What is my workload?" and "What is UDS?" both answered correctly. (Both happen to already be protected by other, pre-existing mechanisms — register-shape detection and a pre-pipeline glossary handler respectively — which is worth recording honestly: the specific intents spot-checked were not previously broken by ATTACH-INTERCEPT-001's narrow scope. The generalization was still made, per the task's explicit instruction, because the *prior* fix's own report flagged it as an unverified risk for intents not yet checked, and the broader condition is strictly more correct and no more expensive than the single-intent one it replaces.)

**Not independently re-verified:** the full section-3 matrix (field visits, fee queries, application-status, application-detail, file-relevance, multi-file comparison, and the complete 6-condition grid) was not exhaustively re-tested per category — see `POSTUPLOAD-MATRIX-NOTRUN` in the failure-cases file.

## 3. F-Line / CSV fixes re-checked under the task's adversarial variations

All of the following were re-verified live or via the existing regression suites this pass, with no regressions:
- "What is F-Line?" / "What is F-Line Appeal?" / "What is the difference..." — all correct, with and without an attachment present, as the first message after upload.
- "List all service codes in this file." / "List the service_code column." — both correct, deterministic.
- "Compare the service codes in both files." — correct, deterministic, per-file attribution preserved.
- `service_code` / `service_description` named together, and the "F-Line" / "F-Line Appeal" substring-collision case — both covered by the prior pass's fixes, unaffected by this pass's changes (regression-tested).

"Compare both service codes in the uploaded file." (singular file, two codes) and further plural/header-variation phrasings beyond what the prior pass's regression tests already cover were not newly exercised this pass — no regression was found in the existing coverage, and no new gap was reported in this area.

## 4. Continuation and table context interaction

This task's section 5 asks whether the negative-correction or post-upload fixes changed continuation behavior ("continue", "next five rows", pagination, etc.). That functionality is owned by the separate, concurrent session's `SIS_Continue_*` work, not touched by this pass. Code review confirms this pass's two changes are additive and narrowly scoped (a new routing function called once, before the attachment layer; a widened regex; a generalized single condition; a new branch in one existing function) — none of them alter `followup_context.py`'s pagination/slice logic, which is where continuation state actually lives. **This was not independently re-verified live** against the continuation suite; per the task's own instruction not to claim defects are fixed outside confirmed scope, no claim is made here about continuation correctness either way — it is reported as out of this pass's tested scope.

## 5. Files changed

| File | Change |
|---|---|
| `backend/services/chatbot.py` | New `_route_negative_correction()`, `_recent_nonattachment_claim()`, cue regexes, and label tables; both chat-path call sites (streaming and non-streaming) updated to call the router before the attachment layer, handle the "ambiguous" and "attachment" routes explicitly, and carry attachment context forward through the ambiguous-clarification save |
| `backend/services/attachment_qa.py` | `_NEGATIVE_CORRECTION_RE` widened (general "I don't think X" shape, bare "that's incorrect"); `plan_answer()`'s topic-active fallback condition generalized from `intent == "service_code_lookup"` to `intent != "general_query"`; `_handle_negative_correction()` gained a multi-document "recheck the comparison" branch ahead of its generic "which file" fallback |

No other production files were touched. No DB schema, authorization, session-isolation or jurisdiction-check code was modified.

## 6. Test results

268 checks across 16 pre-existing suites (10 unit + 2 integration/DB from the attachment family, re-run clean) plus 13 new unit checks (`test_negative_correction_antecedent_routing.py`) — all passing, no regressions. Full breakdown with test type (unit/integration/UI) and honest NOT_RUN markers for unexecuted scope: `SIS_Context_And_PostUpload_Regression_Results.csv`.

## 7. Remaining limitations (reported honestly, not fixed)

- Requirement F (negated reply to a bot-initiated yes/no) confirmed only at the classification level, not as a full live-staged bot-then-officer exchange.
- Tamil/Tanglish negative-correction variants: unchanged, pre-existing patterns, not independently re-verified this pass.
- The full post-upload routing matrix (section 3's complete intent-category × condition grid) was spot-checked on 2 categories, not exhaustively re-run.
- Continuation/pagination interaction (section 5) was reviewed by code inspection only, not re-tested against the live continuation suite.
