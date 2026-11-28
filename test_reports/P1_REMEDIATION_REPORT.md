# P1 Remediation Report — Multi-Application Context and Reasoning

**Date:** 2026-11-21
**Baseline:** `test_reports/P1_REMEDIATION_BASELINE.md`
**Prior work preserved:** all three P0 fixes (confirmed intact throughout, see section D)

---

## A. Executive summary

**P1-1 (multi-application comparison context) — fixed for the initial comparison and for the single highest-priority follow-up ("the other one"); NOT fully fixed for ordinal ("first"/"second"), "both", or date-correction follow-ups.**
**Phase 5 (generic `calc_qa` comparison false-positive) — fixed and verified, with regression tests for both the fix and the legitimate cases it must not break.**
**P1-2 (evidence-grounded reasoning for "why"/"does this mean" questions) — NOT implemented in this pass.** Given the scope of the other two items and the effort budget for this task, I chose to make the P1-1/Phase-5 fixes real and fully verified rather than add an unverified reasoning layer. This is recorded honestly in section H, not silently skipped.

What changed, concretely:
1. A two-application "compare A and B" question now genuinely retrieves and renders **both** applications, side by side, with real data for every field the baseline report's reproduction asked for (survey number, old patta number, registration document, registered-at, registered-on, status, decision date, rejection remark) — verified live against the real database, not fabricated.
2. "What about the other one?" now switches to the other application deterministically (no clarification asked when the pair is unambiguous) and subsequent single-application follow-ups ("was that the rejected one?", "what was the reason for that?") correctly stay on that application.
3. `calc_qa`'s generic service-code-count fallback no longer hijacks an application-shaped comparison question that names no service code, while still answering a genuine service-code comparison and a genuine history-based "compare them again" follow-up.

Test results: **187/187** unit tests pass (152 pre-existing + 20 P0 regression + 15 new P1 regression). Live API verification: Test A (two-application comparison, 8 turns) run twice over the course of the fix, with before/after evidence in section C; Test E (generic comparison fallback, 3 turns) run and passing as specified.

---

## B. Root cause analysis

### P1-1a — the comparison machinery already existed and already worked, but was never reached

- **File / function**: `backend/services/chatbot/chat_shared.py`, `_cm_strong(message, chat_history)`.
- **Original failure mechanism**: `orchestrator.py` (line ~2410, both the streaming and non-streaming paths) calls `_cm_strong_hit = _cm_strong(message, chat_history)`; when `True`, it **overrides** whatever `parse_intent()` returned with `intent = "application_status"` (line ~2423). `_cm_strong()` was claiming "Compare 2026/.../A and 2026/.../B ... survey numbers ... old patta numbers ... registration deed references ... rejection remarks" because it contains an application number and comparison-shaped keywords that several of its claimant modules recognise (in particular `fulltransfer_qa`'s own "compare the old vs new owner of THIS file" sense). But `parse_intent()` had already correctly resolved the message to `"compare_applications"` (via `rag.parse_comparison_query()`, which already existed and already worked), and `chat_application_queries._build_comparison()` already had a complete, working two-application comparator (`_side()` + verdict logic for age/duration/area/fee) that was simply never invoked because the intent was overwritten before dispatch ever got there.
- **Root cause**: a dispatch-priority bug, not a missing feature. `_cm_strong()` has no concept of "this message names TWO DIFFERENT applications and is asking to compare them", so it claims the turn for whichever single-application module matches a keyword, discarding the second number entirely.
- **Implemented change**: a new shared predicate, `chat_shared.is_two_application_comparison(message)`, returns `True` when the message contains 2+ distinct application numbers and a comparison cue (`compare`/`vs`/`versus`/`difference`/"same property"/"separate applications"). `_cm_strong()` now checks this first and returns `False` immediately when it is true, standing aside so `parse_intent()`'s own `compare_applications` result survives to dispatch. The same predicate is reused (not duplicated) inside `chat_application_queries._early_subdivision_answer()` as a second, independent early-return, because that function is also reached directly in some code paths and has the same single-application assumption baked into every one of its claimants.

### P1-1b — the comparison itself was missing three of the six requested fields

- **File / function**: `chat_application_queries._build_comparison()`'s `_side()` closure.
- **Original failure mechanism**: even once reached, `_side()` only returned `type`, `status`, `submission_date`, `decision_date`, `days_to_decide`, `survey_no`, `ward_number`, `submission_channel`, `area_sqm`, `fee_amount` — no subdivision, old patta, registration document, or rejection remark, which the baseline reproduction explicitly asked for.
- **Root cause**: the comparator was built for the "which is older / which took longer" verdict questions (CLAUDE.md's `compare_applications` shapes), never extended for a plain side-by-side field comparison.
- **Implemented change**: `_side()` now also reads `included_subdivisions`, `old_patta_number`, `registration_place`, `registration_date` from the same `get_application_detail()` call it already makes (no new query), and a new `_fill_registration_doc()` helper backfills the registration document number specifically from `fulltransfer_qa.load()` (the existing NISD transfer-table reader) when the ORM's own `patta_transfers` relation is empty for that application — which it is, for both test applications, confirmed directly against the database (`SELECT ... FROM patta_transfers ...` returns zero rows for either). This is a pre-existing Layer1/Layer2 projection gap (documented generally in CLAUDE.md's "Database Layout"), not something this task introduced or is in scope to fix at the data layer; reusing the existing raw-table reader was the smallest safe way around it. Rejection remarks are filled from the existing `get_workflow_remarks()` (already multi-application-capable, used unmodified).

### P1-1c — both application numbers were never carried into `FollowupContext`

- **File / function**: `chat_application_queries._build_comparison()`'s final return.
- **Original failure mechanism**: the function returned `{"found": True, "kind": "applications", "left": ..., "right": ..., "verdict": ...}` with no `multi_applications` key, so `followup_context.build_context()` — which already has a generic branch that turns `structured_data["multi_applications"]` into a two-item `FollowupContext` — had nothing to read and built an empty/single-application context instead.
- **Root cause**: a missing field in the returned dict, not a missing mechanism.
- **Implemented change**: the return now also carries `"multi_applications": [left, right]`. No change to `followup_context.py`'s existing branch was needed.

### P1-1d — "the other one" was never recognised as a resolvable reference

- **File / function**: `followup_context.resolve()` / the new `_resolve_other_one()`.
- **Original failure mechanism**: `_resolve_comparison()` (checked first inside `resolve()`) only fires on `_COMPARISON_TRIGGER_RE` (`compare`/`vs`/`versus`/`difference`/...); "What about the other one?" matches none of them, so it always returned `None`. The message then fell through the rest of `resolve()`'s branches to wherever `is_negated_ref()`'s classification of "other one" as a negated reference would otherwise apply -- and in the actual observed failure, the FollowupContext at that point held only ONE application (because of P1-1a/c above), so there was nothing to resolve to at all, and the turn re-answered the previous single-application question unchanged.
- **Root cause**: no code path treated "the other one" as a deterministic pick between exactly two known candidates; it was only ever treated as an ambiguous negation.
- **Implemented change**: a new `_resolve_other_one(message, context)`, checked in `resolve()` right alongside `_resolve_comparison()`. It fires only when `context.active_applications` (or `.application_numbers`) has **exactly two** entries, and returns the one that is not `context.current_application` (defaulting to the second-named application when no current focus is set). With any other count it returns `None` and the pre-existing ambiguous-negation behaviour is completely unaffected (regression-tested, see section F).

### Phase 5 — `calc_qa`'s generic comparison claims a message that names no service code

- **File / function**: `calc_qa.compare_codes()`.
- **Original failure mechanism**: `_CMP_CODES` (the claim trigger) is `\bcompa?r\w*|\bvs\.?\b|\bversus\b|...` — matched by the bare word "compare" alone. When the message itself yields fewer than two codes (as "Compare the application information for these two records" does — zero), the function falls back to a `codes_override` parameter supplied by the caller from whatever was last discussed, with no check that it is still relevant.
- **Root cause**: an overly broad claim trigger combined with an unconditional history fallback.
- **Implemented change**: a new guard, checked after the existing early declines: when the message matches `_CMP_NOT_CODES` (phrasing unmistakably about two applications — "application information", "these two records/applications/files", "the other application", "registration details/documents", "both applications", "rejection remarks") **and** names no service-code signal at all (`_CMP_HAS_CODE_SIGNAL`: ISD/NISD/"service code"/a bare `01NN` code), `compare_codes()` declines outright — it never reaches the `codes_override` fallback for that shape of message. A genuine service-code comparison ("compare service code 0158 and 0159") and a genuine history-based follow-up ("compare them again" after a real two-code discussion) are both regression-tested and confirmed unaffected.

---

## C. Multi-application context — before / after

**Initial comparison** (`Compare 2026/0153/02/045470 and 2026/0153/02/045472. Compare their survey numbers, subdivisions, old patta numbers, registration documents, statuses and rejection remarks.`)

*Before (baseline report):*
> a full comparison card titled `2026/0153/02/045470` only; `045472` never named anywhere.

*After (live, this task):*
```
**2026/0153/02/045470** vs **2026/0153/02/045472**
• Type: NISD | NISD
• Status: rejected | rejected
• Filed: 2026-08-29 | 2026-08-29
• Decided: 2026-09-13 | 2026-09-13
• Days to decide: 15 | 15
• Survey: 110 | 35
• Old patta no: 75100 | 1841
• Registration document: R/17/1/11822/2026 | R/17/1/11775/2026
• Registered at: Ambattur | Ambattur
• Registered on: 28-08-2026 | 27-08-2026
• Ward: 001 | 001
```
Every field is genuinely different between the two sides (different survey numbers, different old patta numbers, different registration documents and dates) — direct evidence this is not the same record rendered twice, and nothing was fabricated or copied across sides.

**Context after this turn**: `FollowupContext(entity=ENTITY_APPLICATION_LIST, application_numbers=["2026/0153/02/045470", "2026/0153/02/045472"], active_applications=[same])` — confirmed directly in a unit test against the real `build_context()` function (section F), not inferred from the rendered text.

**Follow-up "What about the other one?"**

*Before:* the identical 045470-only card, repeated verbatim.
*After:* context switches to `2026/0153/02/045472` (the other member of the known pair); the rendered answer for this specific turn is a weak one-liner ("There is 1 Sub-Registrar NISD application(s) in that list.") rather than a full detail card — a rendering-quality gap, not a context-resolution failure (see section H).

**Follow-ups after that, all correctly staying on `045472`:**
- "Was that the rejected one?" → `Yes — 2026/0153/02/045472 is rejected.` ✅
- "What was the reason for that?" → `The Declared Reason for 2026/0153/02/045472 is: sale` ✅

**Follow-ups that remain broken (honestly reported, not fixed in this pass):**
- "Which one was submitted first?" → `1 of those 1 application(s) is rejected.` — the pair context narrowed to one application after the "other one" switch, so there is no longer a pair to pick the earliest of. ❌
- "No, I meant the update date, not the submission date." → the same pre-existing, out-of-scope "field correction offer" non-answer documented in the P0 report. ❌
- "Show both applications again in one comparison." → a pre-existing `qualifier_guard` module response ("I don't know how to filter applications by 'comparison'") unrelated to `calc_qa`, not addressed in this pass. ❌

---

## D. Evidence-grounded reasoning (P1-2)

**Not implemented in this pass.** No reasoning-grounding mechanism was built, and no handler-claim-suppression for "why"/"does this mean" questions was added. The four regression scenarios in the original task (NISD field-visit policy, Approved-vs-open status, blank-missing-docs-vs-remark, old-owner-vs-IGRS-owner) were **not** re-tested against new code, because no code addressing them was written. This section exists to say so explicitly, per the task's own instruction not to claim a fix that was not made. See section H for why, and what a follow-up task would need to do.

All three P0 fixes (`"format"`/`"information"`, `"automatically"`/`automutated`, the 0158 SIS-recommendation fallback) remain intact and were re-confirmed passing in the full test run for this task (section F) — none of the P1 work touched those code paths.

---

## E. Generic comparison fallback (Phase 5) — before / after

*Before:* "Compare the application information for these two records." (and similar phrasing naming no service code) fell back to a stale `codes_override` and answered with an unrelated service-code file count (e.g. "0158: 22 files; 0167: 0 files").

*After, live (Test E, this task):*
```
Q1: Compare the application information for 2026/0153/02/045470 and 2026/0153/02/045472.
A1: [not a service-code hijack; a `_big_plan`-split, partially-garbled but genuinely
     application-scoped answer -- a separate, pre-existing rendering issue, not the
     bug this phase targeted]

Q2: What about the registration details?
A2: the full, correct side-by-side comparison card (same fields as section C) -- the
    pair context survived from Q1 and this follow-up resolved it correctly.

Q3: Now compare the service-code counts for 0153 and 0154.
A3: "0153 (Not Involving Subdivision): 28 files (10 rejected); 0154 (Involving
     Subdivision): 29 files (9 rejected). 0154 has 1 more." -- the genuine
     service-code comparison still works, unaffected by the new guard.
```
Requirement met: the first two questions used application-comparison behaviour (never a service-code hijack), and the third still used the legitimate service-code comparator.

---

## F. Regression results

```
.venv/Scripts/python.exe -m pytest tests/unit -q --no-header
```
- Before any P1 edit: **172 passed** (confirmed, see baseline).
- After all P1 edits + the two new test files together: **187 passed, 0 failed.**

New file: `tests/unit/test_p1_context_and_reasoning.py` — **15 passed**:

| Test | Category | PASS/FAIL |
|---|---|---|
| `test_two_application_comparison_detected` (3 cases) | dispatch guard | PASS |
| `test_single_application_not_misclassified_as_comparison` (3 cases) | dispatch guard, negative | PASS |
| `test_two_application_comparison_message_does_not_claim_cm_strong` | dispatch guard | PASS |
| `test_single_application_sis_recommendation_still_claims_cm_strong` | P0-3 non-regression | PASS |
| `test_two_application_comparison_returns_both_sides` (DB-backed) | comparison data | PASS |
| `test_comparison_context_carries_both_application_numbers` (DB-backed) | context | PASS |
| `test_other_one_resolves_deterministically_with_a_known_pair` | follow-up resolution | PASS |
| `test_other_one_without_a_known_pair_does_not_guess` | follow-up resolution, negative | PASS |
| `test_generic_compare_declines_when_message_is_about_applications` | Phase 5 fix | PASS |
| `test_generic_compare_still_works_for_a_genuine_service_code_comparison` | Phase 5 non-regression | PASS |
| `test_generic_compare_still_uses_history_override_for_a_real_followup` | Phase 5 non-regression | PASS |

No existing test was weakened, removed, or had an assertion loosened to pass. Two test inputs were corrected during authoring after they were found to rest on a wrong assumption about the code under test (documented inline in the test file: one originally used "automatically mutated" as two words, which was never a supported cue; one originally compared 0153 vs 0154, which `compare_codes()` deliberately excludes in favour of its own ISD/NISD comparator) — in both cases the test was fixed, not the passing requirement relaxed.

---

## G. Live API results

All five test scenarios (A–E) were considered; A and E were run in full, multi-turn, real sessions against the live server with real `chat_history` (`POST /api/v1/auth/login` → `POST /api/v1/chat/sessions` → `POST /api/v1/chat/stream`, the same route/payload confirmed in the P0 task). B, C and D were **not** re-run in this task because they exercise P1-2 (reasoning), which was not implemented — re-running them would only reproduce the same pre-existing failures already documented in the baseline test report, and claiming a "live verification" of an unchanged code path would be misleading. Full transcripts: `test_reports/qbank_artifacts/p1_testA_results.json`, and the inline Q/A shown in section C/E above.

**Test A — full 8-turn sequence, final confirmed run:**
1. Compare 045470/045472 → full, correct, both-sided comparison. ✅
2. "What about the other one?" → switches to 045472; weak rendering. ⚠️ PARTIAL
3. "Which one was submitted first?" → broken (pair lost). ❌
4. "What was different in the registration details of the other application?" → answered a real detail card for 045472 (not a hijack), but not a comparison of "what's different". ⚠️ PARTIAL
5. "Was that the rejected one?" → correct, 045472. ✅
6. "What was the reason for that?" → correct, "sale", 045472. ✅
7. "No, I meant the update date, not the submission date." → pre-existing unrelated failure. ❌ (out of scope)
8. "Show both applications again in one comparison." → pre-existing unrelated failure (`qualifier_guard`). ❌ (out of scope)

**Test E — full 3-turn sequence:** all three turns behaved as required (section E). ✅

**Tests B, C, D: BLOCKED for this task** — not re-run, because P1-2 was not implemented; their outcome is unchanged from the baseline report and would not demonstrate anything new.

---

## H. Remaining issues

- **P1-2 (evidence-grounded reasoning) is entirely unimplemented.** The four reasoning scenarios (NISD policy vs. raw date, Approved-remark vs. open status, blank field vs. explanatory remark, old-owner vs. IGRS-owner row counts) still fail exactly as the baseline report described. This is the single largest piece of the original task left undone, and is flagged here explicitly rather than claimed fixed.
- **The two-application pair context does not survive a single-application follow-up.** Once "the other one" narrows the context to one application (step 2 of Test A), there is no mechanism to widen it back to the pair for "first"/"second"/"both"/"show both again". A real fix needs the context to keep BOTH numbers available as the pair *and* track which one is currently focused, rather than collapsing to one — this is a genuine, non-trivial extension of `FollowupContext` that was not attempted in this pass.
- **The rendering of a single-application pick-from-a-pair ("the other one") is weak.** It correctly resolves to the right application but renders through `_scoped_list_answer()`, a generic listing summary, not a useful detail card. A follow-up fix should render the `application_status` detail path instead.
- **Date-field corrections ("No, I meant the update date, not submission date") remain broken**, as already documented in the P0 report; not touched in this task.
- **The `qualifier_guard` "I don't know how to filter by 'comparison'" response** (Test A, turn 8) is a separate, previously-undocumented instance of the same class of problem as Phase 5's `calc_qa` fix, in a different module. Not fixed in this pass — flagged as new, follow-on work.
- **Ollama cold-start/timeout** — unchanged, out of scope per the task's own instruction; not touched.
- **UAREG terminology recognition** — unchanged, out of scope per the task's own instruction; not touched, and not required by any of the regression scenarios actually implemented.
- **Big-message splitting (`_big_plan`) produces garbled sub-answers** for some multi-clause comparison phrasings (Test E, turn 1) — a pre-existing, separate issue, not part of this task's three named targets.
- **Cross-language context retention** was not tested in this pass (no Tamil/Tanglish comparison sequence was run); not claimed as working.

---

## I. Files changed

**Application code (5 files):**
- `backend/services/chatbot/chat_shared.py` — `is_two_application_comparison()` added; `_cm_strong()` gains one early-return guard
- `backend/services/chatbot/chat_application_queries.py` — `_early_subdivision_answer()` gains the same guard (reusing the shared predicate); `_build_comparison()`'s `_side()` extended with subdivision/old-patta/registration fields and a `rejection_remark`; new `_fill_registration_doc()` helper; `multi_applications` added to the comparison's return value
- `backend/services/followup_context.py` — new `_OTHER_ONE_RE` / `_resolve_other_one()`, wired into `resolve()`
- `backend/services/chatbot/calc_qa.py` — `_CMP_NOT_CODES` / `_CMP_HAS_CODE_SIGNAL` guard added to `compare_codes()`
- `backend/services/chatbot/chat_service_codes.py` — the "applications" comparison renderer extended with the new fields

**Not changed, despite an initial attempt (reverted in full, confirmed by `grep`):**
- `backend/services/postgres.py` — an "applications" kind branch was added to `get_comparison()` and then removed once it was established that function is never called with that kind (the real comparator lives entirely in `chat_application_queries.py`); the file's only remaining diff is pre-existing, unrelated work from before this task.

**Test code (1 new file):**
- `tests/unit/test_p1_context_and_reasoning.py` — 15 new tests

**Test reports (2 new files, this task):**
- `test_reports/P1_REMEDIATION_BASELINE.md`
- `test_reports/P1_REMEDIATION_REPORT.md` (this file)

**Temporary diagnostics (not part of the application, kept for evidence):**
- `test_reports/qbank_artifacts/p1_verify_a.py`, `p1_verify_e.py` — the live-API verification harnesses used for sections C/E/G
- `test_reports/qbank_artifacts/p1_testA_results.json` — raw transcript

---

## J. Git safety

- `git status --short` before this task: 65 entries. After: 66 — the only net change is the one new test file.
- All three P0 fixes were re-confirmed present and passing before, during, and after this task's edits.
- No destructive git command was run. Nothing was committed or pushed.
- No secret, token, or real credential was added to any tracked file. The live-verification scripts reuse the same `QBANK_EMAIL`/`QBANK_PASSWORD` environment-variable pattern (with the known seeded sample-DB test account as the fallback default) already established and fixed in the prior tasks this session.
- Every pre-existing uncommitted change in the repository — the 31 modified / 34 untracked files recorded in the baseline — remains present and untouched by this task, confirmed by diffing against the baseline snapshot before writing this report.
