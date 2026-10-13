# SIS Chatbot — F-Line / F-Line Appeal, Follow-up Context and Uploaded-File Retrieval: Fix Report

**Date:** 2026-10-13
**Repository:** github.com/svmayurkaarthic2005/Internship_NIC (local checkout: `C:\proj\nic_internship`)
**Tester:** live chatbot UI (`http://localhost:3000/chatbot.html`), officer `marun_chn@sis.tn.gov.in` (SIS-002)
**Companion documents:** `SIS_FLine_Appeal_Regression_Results.csv`, `SIS_FLine_Appeal_Failure_Cases.jsonl`, `SIS_ServiceCode_FileDB_Regression_Tests/test_servicecode_alias_collision.py`, `SIS_ServiceCode_FileDB_Regression_Tests/test_csv_plural_column_resolution.py`, `SIS_ServiceCode_FileDB_Regression_Tests/test_fline_appeal_multicode_comparison.py`

## Note on concurrent work

Partway through this pass, a **separate, independent session** made substantial, unrelated changes to the same backend files (`chatbot.py`, `followup_context.py`, a second, complementary fix to the F-Line alias matcher, and more — documented in its own `SIS_Continue_*` deliverables, a "continuation/pagination" audit). Those changes are **not** part of this report and were not reviewed in depth here; where they interacted with this pass's own fixes (one case did — see FLINEAPPEAL-002 below), that interaction is called out explicitly. Nothing from that other session was reverted or modified by this pass.

## 1. Reproduction (before any code change)

The exact three-turn sequence from the task's screenshot was reproduced live, in a fresh conversation, with `sis_svccode_fileA.csv` attached (754 characters, 5 CSV rows, `extraction_status: ok` — confirmed via the upload API response, so this was never an extraction failure):

| Turn | Message | Response |
|---|---|---|
| 1 | "What is F-Line Appeal?" | "I could not find this in the uploaded document." |
| 2 | "What is the difference between F-Line and F-Line Appeal?" | "I could not find this in the uploaded document." |
| 3 | "List all the service codes in this file." | "I could not find this in the uploaded document." |

All three reproduced exactly as reported. A fourth check — the same two questions with **no file attached** — showed a *different* failure mode: both returned the 0178 "F-Line" definition verbatim, never mentioning 0185. **Attachment state genuinely changes the failure**, confirming the task's own hint that root cause must be traced, not assumed.

## 2. Root causes found (three, each confirmed with a standalone, no-DB/no-LLM regression test)

### FLINEAPPEAL-001 — the alias short-circuit (`backend/utils/helpers.py`)

`find_service_code_by_name()` ran `_svc_name_alias_match()` — a bare substring check for "f-line"/"f line"/"fline" that always returns 0178 — **before** its own keyword-overlap scoring loop. That scoring loop, if it had been allowed to run, already resolves "F-Line Appeal" correctly and *uniquely* to 0185: its official name's two distinctive words ("line", "appeal") are both present, coverage ratio 1.0, and no other code comes close. The alias short-circuit simply never let it.

**Verified by direct function call before any fix:**
```
find_service_code_by_name("What is F-Line Appeal?")  ->  '0178'   (wrong)
```
**After fix:**
```
find_service_code_by_name("What is F-Line Appeal?")  ->  '0185'   (correct)
find_service_code_by_name("what is f-line?")          ->  '0178'   (unchanged, still correct)
```

**Fix:** reordered `find_service_code_by_name()` to run the keyword-scoring loop first; the alias lookup now runs only as a fallback when scoring finds nothing at all — exactly the case the alias exists for ("f-line" alone has only one of 0178's three distinctive words, below the scoring threshold).

### ATTACH-INTERCEPT-001 — the deeper, previously-undiagnosed cause (`backend/services/attachment_qa.py`)

This is the real explanation for Turns 1 and 2 of the reported screenshot (where a file *was* attached). `targets_attachment()` — the function that decides whether a message is about the uploaded file — **correctly** returns `False` for a bare service-code question (no filename, no document word, intent is `service_code_lookup` not `general_query`; confirmed by calling it directly). The question should never have reached the attachment layer at all.

But `plan_answer()` has a second path: when `targets_attachment()` says no, it still checks `attachment_topic_active()`, which reads the **most recent assistant message's** stored metadata and returns `True` if its `entity` is `"attachment"`. The upload endpoint (`backend/routers/chat.py`) saves its own upload-confirmation message with exactly `structured_context={"entity": "attachment", ...}` — so immediately after *any* upload, before a single real question has been asked about the file, this flag already reads `True`. The next message — whatever it is — falls into the `topic_active = True` branch and is routed through the attachment-evidence path, which then refuses because the CSV genuinely never mentions "F-Line Appeal".

**Fix:** added `intent == "service_code_lookup"` to the same early-return list that already exempts an application-number reference and a register-shaped question — a service-code definition has its own complete, file-independent answer and must never be judged by whether a file happens to be open. Scoped narrowly to this confirmed case, per the task's minimal-change restriction; other deterministic intents were not tested under the same condition and may share this exposure (flagged, not fixed).

### CSVCOL-FLINE-001 — the plural CSV column match (`backend/services/csv_ops.py`)

Turn 3's failure is unrelated to Turns 1–2. `resolve_column()` requires the normalised header ("service code", from `service_code`) to be followed by a non-alphanumeric boundary in the question text. The natural plural "service codes" has a trailing "s" that is itself alphanumeric, so the boundary never closes and the full-header match fails. The function then falls back to matching any single distinctive header word — but "service" is a distinctive word of **two** columns in this fixture (`service_code` and `service_description`), so it refuses to guess and returns `None`. `parse_csv_question()` therefore never produces an operation, and the question skips the deterministic "computed, never narrated" CSV engine entirely, landing on the generic evidence-floor refusal.

**Verified by direct function call before any fix:**
```
resolve_column("List all the service codes in this file.", headers)  ->  None
```

**Fix:** `resolve_column()`'s full-header match now allows an optional trailing "s" after the (already-escaped) header text before the boundary check, so "service codes" matches `service_code` directly and wins the strong full-header match before the ambiguous fallback is ever reached. This also closed a **second** symptom from the prior QA pass without any extra code: "Compare the service codes in both files." across two attached CSVs, which previously fell through to the ungrounded LLM (which then stated File A's values wrong — dropped one value, duplicated another), now runs through the deterministic multi-CSV path and gets **both** files exactly right, confirmed live.

### FLINEAPPEAL-002 — comparison follow-ups collapsing to one code (`backend/utils/helpers.py`)

Once FLINEAPPEAL-001 was fixed, "What is the difference between F-Line and F-Line Appeal?" still only ever named the single code `find_service_code_by_name()` returns (by design — that function picks one best match). `find_service_codes()` (its plural-aware caller, used by the actual answer-building code) needed to detect **both** 0178 and 0185 when the message is genuinely asking to compare them.

This interacted with the concurrent session's own, independent improvement to `_svc_name_alias_match()` (which now correctly prefers the longer, more specific alias "f-line appeal" over the shorter "f-line" when both are present — a good fix for single-term lookups). That improvement meant my first attempt at detecting two codes (re-calling `_svc_name_alias_match()`) stopped finding the second code, since it now only ever returns the one best alias. **Fix:** added `_all_alias_codes()`, which checks every department alias independently rather than returning only the single best match, and wired `find_service_codes()` to use it — but **only** when the message carries explicit comparison/pairing language (difference, compare, versus, both, same, ...). A bare "What is F-Line Appeal?" still names only 0185; it is not treated as naming two services just because "F-Line" is a literal substring of its own name.

## 3. Live verification after the fixes

All three original screenshot turns were re-run in a fresh conversation against the fixed code, plus several of the task's own prescribed regression phrasings:

| Question | Attachment state | Result |
|---|---|---|
| "What is F-Line Appeal?" | file attached, first message | **0185**, correct |
| "What is F-Line Appeal?" | no attachment | **0185**, correct |
| "What is the difference between F-Line and F-Line Appeal?" | file attached | both 0178 and 0185 explained |
| "Are F-Line and F-Line Appeal the same?" | no attachment | "No — ... different services ..." then both full descriptions |
| "List all the service codes in this file." | file attached | deterministic, exact ground-truth match, with citation |
| "Compare the service codes in both files." | 2 files attached | deterministic table, both files exactly correct |

One additional, unscripted observation while testing: **"I don't think they are the same."**, sent after an unrelated intervening turn (a 2-file CSV comparison), was resolved against the wrong antecedent — it asked "which file do you mean?" instead of addressing the F-Line/F-Line-Appeal claim from two turns earlier. This is reported as **FLINE-NEGATION-001**, observed but **not fixed** — it is outside this task's three confirmed-defect scope (F-Line/F-Line Appeal confusion, comparison follow-ups, uploaded-file enumeration) and the minimal-change restriction.

## 4. Files changed

| File | Change |
|---|---|
| `backend/utils/helpers.py` | `find_service_code_by_name()` reordered (scoring before alias fallback); new `_all_alias_codes()` helper; `find_service_codes()`'s name-fallback branch extended to add a second code under comparison language; `Tuple` added to the `typing` import |
| `backend/services/attachment_qa.py` | `plan_answer()`'s topic-active fallback now also exempts `intent == "service_code_lookup"` |
| `backend/services/csv_ops.py` | `resolve_column()`'s full-header match allows an optional trailing "s" |

No other production files were touched. No fixture or test data was hardcoded into the fix code itself — all three changes are general-purpose function behavior, applicable to any service code or CSV header, not special-cased to "F-Line"/"F-Line Appeal" or to this pass's own test fixtures.

## 5. Test results

223 checks across 13 suites (10 pre-existing + 3 new), all passing, no regressions. Full breakdown: `SIS_FLine_Appeal_Regression_Results.csv`.

## 6. Remaining limitations (reported honestly, not fixed)

- **FLINE-NEGATION-001**: a negative-correction reply ("I don't think so") can resolve against the wrong recent antecedent when more than one plausible "they"/"that" referent exists across the last few turns. Observed once, not root-caused to a specific line, not fixed — out of this task's scope.
- **ATTACH-INTERCEPT-001's fix is scoped to `service_code_lookup` only.** Other deterministic SIS intents asked as the very first message right after an upload were not tested under the same condition and may share the same exposure (the upload-confirmation message always marks `attachment_topic_active() == True`). Recommended as the next thing to test.
- Per this task's own restrictions, only the three confirmed defects named in the mission were fixed; the wider service-code catalogue (29 codes × multiple phrasings), the full multi-file comparison conversation (Phase 3 of the prior audit), and the Tamil/Tanglish/typo variants of these same questions were spot-checked, not exhaustively re-verified, in this pass.
