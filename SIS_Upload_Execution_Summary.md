# SIS Upload QA — Execution Summary
**Date:** 2026-10-13

## Counts

| category | count |
|---|---|
| **Real UI test turns executed** (upload or chat message, through the actual rendered chatbot UI) | 16 |
| ...of which PASS | 9 |
| ...of which PASS_WEAK (correct but low-quality/imprecise) | 2 |
| ...of which FAIL | 3 |
| ...of which FAIL_SAFE (no hallucination, but genuinely answerable question refused) | 1 |
| ...of which boundary/error cases correctly rejected | 3 |
| **Direct function-level checks** (no UI, no DB, no LLM) | 13 |
| ...of which PASS | 7 |
| ...of which FAIL (confirming a real bug) | 6 |
| **Fixtures built** | 10 files (+ 1 reproducible build script) |
| **Fixtures explicitly NOT built** (named, not silently dropped) | 6 |
| **Test-matrix rows not run** (named, not silently dropped) | 8 |
| **Confirmed, reproducible bugs** | 3 functional (2 HIGH, 1 MEDIUM) + 1 documentation-only |
| **Executable regression tests added** | 2 (both reproduce their bug today; both will flip to passing once fixed) |
| **Production code modified** | 0 files (none, per the task's explicit restriction) |
| **Committed / pushed** | 0 (none, per the task's explicit restriction) |

## By phase (of the original 14-phase request)

| phase | status |
|---|---|
| 1 — Inspect the application | DONE |
| 2 — Generate synthetic test documents | PARTIAL (10 of ~16-18 suggested fixtures; DOCX, Tamil-PDF, low-quality-scan, multi-page-table not built) |
| 3 — Ground-truth answers | DONE (for the fixtures actually tested) |
| 4 — Test uploads through the UI | DONE (for the fixtures actually tested) |
| 5 — English upload/follow-up tests | PARTIAL (Scenario A run in full; Scenario B fixture verified by extraction only, not by live conversation; Scenario C not run) |
| 6 — Spelling/noisy messages | NOT RUN |
| 7 — Tamil/Tanglish | PARTIAL (1 real case run) |
| 8 — Negation/yes-no/contradictions | PARTIAL (1 real case run against an uploaded table; found a real bug) |
| 9 — OCR/citations/extraction integrity | PARTIAL (English scan confirmed working end-to-end; Tamil scan not built) |
| 10 — Multiple documents / source separation | PARTIAL (1 real conflict case run; found a real gap) |
| 11 — Unsupported/conflicting questions | NOT RUN as a dedicated phase (overlaps with §10's finding) |
| 12 — Upload/UI edge cases | PARTIAL (oversized/empty/corrupt confirmed rejected; session isolation, retention expiry, repeated-same-file, long documents, filename-in-Tamil not run) |
| 13 — Automated and UI execution | DONE for the scope actually covered |
| 14 — Deliverables | DONE, all 8 files produced |

## Honesty check

Every "PASS" above is backed by an exact-match comparison against `ground_truth.json`, computed **before** any chatbot answer was seen. Every "FAIL" is backed by a live transcript (`SIS_Upload_Conversation_Flows.jsonl`) and, where possible, a second confirmation at the function level with no LLM involved (`SIS_Upload_Regression_Tests/`). No test is marked passed without having actually run; no phase is marked done without the corresponding evidence existing in this directory.
