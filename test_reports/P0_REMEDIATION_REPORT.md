# P0 Remediation Report — SIS Chatbot Routing Bugs

**Date:** 2026-11-21
**Baseline:** `test_reports/P0_REMEDIATION_BASELINE.md` (recorded before any change)
**Scope:** exactly the three P0 bugs named in `test_reports/SIS_CHATBOT_QUESTION_BANK_TEST_REPORT.md`. No refactoring, no new framework, no new LLM, no database schema change. Nothing was committed or pushed.

---

## A. Changes made

### P0-1 — "format" substring match collided with "information"

- **Root cause:** `backend/services/chatbot/orchestrator.py` (two copies, non-streaming at the old line 2148 and streaming at the old line 5088) checked `"format" in message.lower()` with a bare substring test. `"format"` is a literal substring of the ordinary word **"information"** (in-FORMAT-ion), so any message containing both "application number" and "information" anywhere was hijacked into the canned format explainer.
- **File / function changed:** `backend/services/chatbot/orchestrator.py`. Added one new module-level helper, `_asks_application_number_format(msg_lower: str) -> bool`, placed right after the module's `logger` definition (so both the streaming and non-streaming call sites can share it). Both call sites (`_process_chat_impl` and `_process_chat_stream_impl`) now call `_asks_application_number_format(_msg_lower_fmt)` instead of duplicating the inline condition.
- **What changed exactly:** the single-word cues (`format`, `structure`, `structured`, `pattern`, and the two Tamil words) are now matched with `\b...\b` word-boundary regex (`_APP_NUMBER_FORMAT_WORD_RE`), never a bare substring. The multi-word phrase `"look like"` is still matched as a plain substring, because no single English word contains that two-word phrase with a space in it, so it carries no collision risk.
- **Why this is safe:** it is a pure narrowing of an existing condition — every message the old code recognized via one of the explicit multi-word phrases (`"application number format"`, `"format of application number"`, etc.) is still recognized identically (that `any(p in msg_lower for p in [...])` branch is untouched). Only the second, broader branch (single-word OR-list) is tightened, and only to exclude false positives; it cannot newly match anything the old code did not already match.
- **Before / after:**
  | Message | Before | After |
  |---|---|---|
  | "Explain the application number format." | format explainer | format explainer (unchanged) |
  | "Show applications under service code 0154 and whether owner information is present." | format explainer (**wrong**) | real list query runs |
  | "I need information about application 2026/0154/02/012766." | format explainer (**wrong**) | real application details |

### P0-2 — "automatically" canonicalized to the `automutated` field

- **Root cause:** `backend/services/chatbot/appinfo_qa.py`, the `_EXACT` token-canonicalization map, had `"automatically": "automutated"`. Any message containing the ordinary adverb "automatically" was tokenized and immediately rewritten to the literal DB-field keyword `automutated`, which `parse()`'s `if "automutated" in ws: add(AUTOMUT)` then claimed.
- **File / function changed:** `backend/services/chatbot/appinfo_qa.py`, the `_EXACT` dict literal (module-level constant, no function to name — it's data, not logic).
- **What changed exactly:** removed the single entry `"automatically": "automutated"`, with a comment explaining why. No other entry in `_EXACT`, and no other line of `parse()`, was touched. `"automated"` (also present) was deliberately **left unchanged** — the task named only "automatically" as the bug to fix, and touching "automated" as well would be outside this task's named scope (it is flagged as a residual risk in section E).
- **Why this is safe:** removing one dictionary entry cannot add a new false positive; it can only make the parser claim *fewer* things. The only way to still reach `AUTOMUT` is the literal word "automutated" (or its already-present typo variants `automautated`, `automated`, `automutation`), which is the real, intended cue.
- **Before / after:**
  | Message | Before | After |
  |---|---|---|
  | "Does a populated date column automatically mean a visit is required?" | `Automutated: not recorded` (**wrong**) | no longer claims `AUTOMUT` |
  | "Was the application automutated?" | `Automutated: not recorded` | `Automutated: not recorded` (unchanged — genuine cue) |

### P0-3 — `fulltransfer_qa` answered a non-NISD application's SIS-recommendation question

- **Root cause:** `full_field_patta_transfer_urban` (the table `fulltransfer_qa.py` reads) only ever has a row for an NISD (0153) application. But `fulltransfer_qa.parse()`'s `SISREC` key (triggered by "sis" + "rec[ommendation]" anywhere in the message, with no service-code awareness) is in that module's `DISTINCT` set, so `claims_strong()` returns `True` for **any** SIS-recommendation question regardless of the application's actual service code. In the render path (`chat_application_queries.py`, the function with the `_ft` / `_ft_strong` / `_cm_q` local variables, inside `_early_subdivision_answer_inner`), when `_ft["distinct"]` is true but `_ftrows` comes back empty (because the application genuinely isn't NISD), `fulltransfer_qa.answer()` is still called and its own `if not rows: return "...only an NISD (0153) file carries one."` fires and is returned directly — even though `cmflow_qa` (`_cm_q`, already parsed earlier in the same function) legitimately recognizes the exact same question for a 0158-0169 correction/modification file and had the real answer sitting in a variable that was never reached.
  - This exact failure mode was **already a known, documented pattern in this codebase** for two other service codes: the surrounding code already has hand-written comments and fallbacks for a 0154 file ("the full-field transfer extract also carries 0154 files... try the canonical workflow-history answer first") and a 0178 F-Line file ("full_field_patta_transfer_urban never has a row for a 0178 file either. fline_qa actually has this field."). The 0158-0169 correction/modification case was simply the one code family this existing pattern had not yet been extended to.
- **File / function changed:** `backend/services/chatbot/chat_application_queries.py`, inside `_early_subdivision_answer_inner` — the same `if _ft and _ft_ok:` block that already carries the 0154 and 0178 fallbacks.
- **What changed exactly:** a new `if not _ftrows and ... app_no.split("/")[1] not in ("0153", "0154", "0178") and _cm_q:` branch was added immediately after the existing 0178 fallback and before the `if _ft["kind"] == "compare":` line. When fulltransfer has no row (true for every service code except 0153) **and** `cmflow_qa` has already recognized the question (`_cm_q` truthy), it loads and answers from `cmflow_qa` first; only if that also produces nothing does the original fulltransfer "no record" explanation still run.
- **Why the smaller, renderer-level fix was chosen over reordering `chat_shared.py`'s dispatch chain:** the task's own named location, `chat_shared.py:2798`, is a different function — a boolean "does this message belong to the record-module family at all" gate (`_cm_strong`), used upstream by the orchestrator to decide whether to force `intent = application_status` and skip the multi-request splitter. That gate returning `True` for a SIS-recommendation question is **correct** in both the broken and fixed behaviour — the question genuinely *is* a record-module question, routed to *some* record handler either way. Reordering or re-gating that `or`-chain would not have fixed anything (the actual wrong *answer* text came from a different function entirely) and would have risked changing dispatch behaviour for every other handler in that same `or`-chain, which the task explicitly asked to avoid ("Avoid a broad dispatcher reordering that might break unrelated handlers"). The actual defect was in the renderer that decides which module's answer to *return*, and that is exactly where the fix landed, in the same pattern the surrounding code already used twice.
- **Why this is safe:** the new branch can only ever fire when `_ftrows` is empty (fulltransfer genuinely has nothing to say) **and** the service code is not 0153/0154/0178 (the three codes with their own existing, untouched handling right above it). It therefore cannot take an answer away from any application fulltransfer_qa actually owns data for. Requirement #6 (0153 retains full-transfer behaviour) and #9 (other full-transfer fields unaffected) hold because the branch is scoped to exactly the codes fulltransfer_qa never has a row for in the first place.
- **Before / after** (the exact live reproduction, same conversation, same session):
  | Turn | Before | After |
  |---|---|---|
  | "Investigate application 2026/0158/02/000002..." | correct correction-modification table shown | unchanged, still correct |
  | "When did the SIS recommendation occur?" | `No full-field patta transfer record is held for application 2026/0158/02/000002. Only an NISD (0153) file carries one.` (**contradicts the previous turn**) | `SIS — recommendation: Recommended (Y); submitted on: 2026-01-19 [2026/0158/02/000002]` |
  | "For application 2026/0153/02/045470, show the SIS recommendation and remarks." (0153, unrelated regression check) | fulltransfer_qa answers normally | unchanged: `SIS recommendation: Not recommended (N)`, `SIS remarks: NO LINK` |

---

## B. Regression tests

New file: `tests/unit/test_p0_routing_fixes.py`. All pure-parser tests are deterministic and need no LLM; the two P0-3 integration tests go through the real `_early_subdivision_answer` render path against the live DB using the project's own `test_officer` / `db_session` fixtures (not an isolated handler call).

| Test | Input | Expected | Actual | PASS/FAIL |
|---|---|---|---|---|
| `test_genuine_format_questions_still_recognised` (5 cases) | "Explain the application number format.", "What is the structure of an application number?", "What does an application number look like?", "application number pattern", "how is an application number structured" | format explainer fires | fires for all 5 | PASS |
| `test_ordinary_data_questions_are_not_hijacked` (5 cases) | "...owner information is present.", "...information about application 2026/0154/02/012766.", "Compare the application information...", "Show the application information and payment details.", "Does the application number contain a service code?" | format explainer does NOT fire | does not fire for any | PASS |
| `test_format_substring_inside_information_does_not_match_alone` | isolates the exact substring collision | guard returns `False` | returns `False` | PASS |
| `test_automatically_does_not_claim_automutated_field` (2 cases) | the two live-reproduction "automatically" questions | `AUTOMUT` not in parsed keys | not in keys | PASS |
| `test_explicit_automutated_cue_still_claims_field` (2 cases) | "Was the application automutated?", "Show the automutated status for this application." | `AUTOMUT` in parsed keys | in keys | PASS |
| `test_other_appinfo_fields_unaffected` | "What is the challan number and payment mode?" | `CHALLAN_NO` and `PAYMODE` both claimed | both claimed | PASS |
| `test_fulltransfer_sisrec_claim_unchanged_at_parser_level` | "When did the SIS recommendation occur?" | `claims_strong()` still `True` (module-level behaviour intentionally untouched) | `True` | PASS |
| `test_cmflow_also_recognises_the_same_question` | same message | `cmflow_qa.parse()` returns a non-`None` `Q` with `desks == ["sis"]` | matches | PASS |
| `test_0158_sis_recommendation_reaches_cmflow_not_fulltransfer_refusal` (DB-backed) | app `2026/0158/02/000002`, "When did the SIS recommendation occur?", via the real render function | answer does NOT contain "only an NISD" / "full-field patta transfer record is held" | does not contain either | PASS |
| `test_0153_sis_recommendation_still_uses_fulltransfer` (DB-backed) | app `2026/0153/02/045470`, same question | answer does NOT contain "correction / modification workflow record is held" (i.e. did not get hijacked the other direction) | does not contain it | PASS |

**Full new-file result:** `pytest tests/unit/test_p0_routing_fixes.py -q` → **20 passed**.

---

## C. End-to-end verification (real API, real DB, real session, real `chat_history`)

Server: `uvicorn backend.main:app --port 8000`, started fresh for this verification pass and stopped afterward. Officer: `sperumal_chn@sis.tn.gov.in` (via `POST /api/v1/auth/login` → `POST /api/v1/chat/sessions` → `POST /api/v1/chat/stream`, exactly the real frontend flow). Full raw transcripts: `test_reports/qbank_artifacts/p0_verify_results.json` and `p0_verify2_results.json`.

### P0-1 — mandatory regression set (standalone turns)
| # | Message | Result |
|---|---|---|
| 1 | "Explain the application number format." | Format explainer ✅ |
| 2 | "What is the structure of an application number?" | Format explainer ✅ |
| 3 | "Show applications under service code 0154 and whether owner information is present." | **Real 29-application listing returned**, no hijack ✅ |
| 4 | "I need information about application 2026/0154/02/012766." | Real application details (ISD, Approved, COMPLETED, Dhanalashmi, survey 76), no hijack ✅ |
| 5 | "Compare the application information for these two records." | Not hijacked into the format explainer (routed instead to an unrelated service-code-count comparison — a separate, pre-existing, out-of-scope issue, see section E) |
| 6 | "Does the application number contain a service code?" | Real service-code answer with explanation, no hijack ✅ |

### P0-1 — the exact Chain 19 reproduction from the baseline report
> "Show me the applications under service code 0154, but I am mainly interested in applications involving more than one temporary subdivision. For each matching application, include the application number, survey number, existing patta, number of subdivision rows, application status and whether the proposed owner information is present. Do not count owner rows as separate applications."

**Before (baseline report):** the canned "An application number follows the format YEAR/SERVICE_CODE/..." explainer — the real query was never attempted.
**After:** `"ISD 29, NISD 28 (of 57 in your jurisdiction). ISD leads NISD by 1."` — **the format-explainer hijack is gone** (P0-1 fixed), but the answer is still not the specific filtered/column list the question asked for; the handler that actually runs instead (a generic ISD-vs-NISD count comparator) does not support the compound ">1 temporary subdivision" + per-application-column filter this question asks for. Per the task's own instruction ("Do not fabricate a successful list if the underlying handler cannot support the requested filters. Report any remaining limitation separately."), this is reported as a **separate, pre-existing limitation**, not claimed as fixed by this task — see section E.

### P0-2 — mandatory regression set (standalone turns)
| # | Message | Result |
|---|---|---|
| 1 | "Does a populated date column automatically mean a visit is required?" | No `Automutated` text; generic "nothing shown yet" fallback (no prior context in this standalone send) ✅ not hijacked |
| 2 | "Why should these two tables not automatically be treated as identical?" | No `Automutated` text ✅ not hijacked |
| 3 | "Was the application automutated?" | Asked for an application number (genuine cue recognized, just no app in context) ✅ |
| 4 | "Show the automutated status for this application." | Same, asked for app number ✅ |
| 5 | "Is auto mutation recorded?" | LLM cold-start timeout — "auto mutation" (two words) is **not** a supported cue by design (see section E); falls through to the LLM, which timed out for the pre-existing, unrelated infrastructure reason already documented in the baseline report |
| 6 | "Show the application information and payment details." | Real ISD/NISD fee-and-payment breakdown returned, confirming ordinary application-information retrieval still works ✅ |

### P0-2 — re-run of the **exact original failing conversations**, with real preceding context
Chain4 (`2026/0153/02/045470`), final turn "Does a populated date column automatically mean that the service requires an inspection?":
**Before:** `Automutated: not recorded [2026/0153/02/045470]`
**After:** `'Overdue' does not apply to 2026/0153/02/045470 -- it was rejected and is closed, not an open file being tracked against a deadline.`
The `automutated` misfire is gone. The new answer is a *different* misroute (an unrelated "overdue" check) — this is the pre-existing, explicitly out-of-scope P1-5 "explanatory reasoning follow-ups" gap named in the baseline report, not something this task was asked to fix, and not a regression this task introduced (it is a different handler claiming the turn, not `appinfo_qa`).

Chain14 (`2026/0153/02/045472`), final turn "Can you explain why these two tables should not automatically be treated as identical?":
**Before:** `Automutated: not recorded [2026/0153/02/045472]`
**After:** `The CAN Number for 2026/0153/02/045472 is: 202653322998`
Same pattern: the `automutated` misfire is confirmed gone; the replacement answer is still wrong, but wrong for the separate, out-of-scope reasoning-follow-up reason, not the bug this task fixed.

### P0-3 — the exact 0158 multi-turn reproduction
| Turn | Message | Result |
|---|---|---|
| 1 | "Investigate application 2026/0158/02/000002. Trace its correction-modification desk recommendations and final approval information." | Correct (confirms 2 parcel-modification rows held) |
| 2 | **"When did the SIS recommendation occur?"** | `SIS — recommendation: Recommended (Y); submitted on: 2026-01-19 [2026/0158/02/000002]` — **correct, from cmflow_qa, no contradiction with Turn 1** ✅ **P0-3 FIXED** |
| 3 | "What happened at ZDT, SD and DIS after that?" | LLM cold-start timeout — pre-existing, unrelated infrastructure issue, not this task's scope |
| 4 | "Does an Approved remark automatically mean the entire application is closed?" | Non-answer ("no remark is recorded...") — the pre-existing, out-of-scope P1-5 reasoning-follow-up gap, not an `automutated` misfire |
| 5 | "Check the UAREG records too." | "I'm not sure what you mean" — a separate, pre-existing, out-of-scope terminology-recognition gap already named in the baseline report |

### P0-3 — mandatory regression singletons
- "For application 2026/0153/02/045470, show the SIS recommendation and remarks." → `SIS recommendation: Not recommended (N)`, `SIS remarks: NO LINK` — **0153 behaviour fully preserved** ✅
- "For application 2026/0154/02/012766, show the workflow recommendation history." → correctly explains the correction-modification table only covers 0158-0169, so nothing is held for this 0154 file — **unchanged, correct** ✅

---

## D. Existing tests — exact commands run and results

```
.venv/Scripts/python.exe -m pytest tests/unit -q --no-header
```
- **Before any fix (recorded in the baseline check during this task):** 152 passed.
- **After the three fixes + the new test file, run together:** **172 passed, 0 failed.**
- No other test command (e.g. the top-level `test_*.py` scripts, `backend/test_200_suite.py`) was run in this task — they are unrelated to the three files touched and were not part of this task's scope; running the full historical suite set was not requested and is noted here as not executed, per the task's own instruction not to claim an unexecuted suite as passed.

---

## E. Remaining issues (explicitly NOT claimed as fixed by this task)

These are pre-existing, out of this task's scope (Phase 7), and were **not** touched:

- **Two-application comparison / "what about the other one?" entity-pair tracking** (P1-4 in the baseline report) — untouched.
- **General reasoning-follow-up architecture** (P1-5) — confirmed still present in both Chain4's and Chain14's re-run ("overdue" / "CAN number" non-answers replacing the old `automutated` non-answer). Fixing the `automutated` alias removed one specific *cause* of a bad answer to these questions, but did not give the chatbot an actual way to answer "why/does X mean Y" reasoning questions — that remains open.
- **The generic "compare X and Y" service-code-count fallback** (noted in the baseline report's section G, not one of the three named P0 bugs) still fires for "Compare the application information for these two records." and similar vague comparison phrasing. Not touched, as it is not one of the three named bugs.
- **"Check the UAREG records too" terminology recognition** and the **Ollama cold-start timeout** (P2-6) — both pre-existing, both explicitly out of scope per Phase 7, both reproduced again during this verification pass, unchanged.
- **`"automated": "automutated"`** in the same `_EXACT` map this task edited carries the same risk class as the fixed `"automatically"` entry (an ordinary English word collapsed to the DB field), but the task named only `"automatically"` for removal; `"automated"` was deliberately left untouched to respect the task's scope boundary, and is flagged here as a candidate for a follow-up, equally-scoped fix.
- **"Chain19" compound list/filter query** ("applications with >1 temporary subdivision, with per-application columns") still cannot be answered as asked — the format-explainer hijack that was swallowing this question is gone, but no handler in the codebase currently supports that specific compound filter+column request; it now falls to a generic, lower-information comparison answer instead. This is a feature gap, not something this task's scope covers, and is reported rather than fabricated as fixed.

None of the above was claimed as resolved. Only P0-1, P0-2 and P0-3 as narrowly defined in the task are claimed fixed, and each is backed by the before/after evidence above.

---

## F. Files changed

**Application code (3 files):**
- `backend/services/chatbot/orchestrator.py` — P0-1 fix (new shared helper + two call-site simplifications)
- `backend/services/chatbot/appinfo_qa.py` — P0-2 fix (one `_EXACT` entry removed)
- `backend/services/chatbot/chat_application_queries.py` — P0-3 fix (new fallback branch in `_early_subdivision_answer_inner`)

**Test code (1 new file):**
- `tests/unit/test_p0_routing_fixes.py` — 20 new regression tests (18 pure-parser, 2 DB-backed integration)

**Test reports (3 new files, this task):**
- `test_reports/P0_REMEDIATION_BASELINE.md`
- `test_reports/P0_REMEDIATION_REPORT.md` (this file)
- (the pre-existing `test_reports/SIS_CHATBOT_QUESTION_BANK_TEST_REPORT.md` / `.csv` from the prior task are unchanged)

**Temporary diagnostics (not part of the application, kept for evidence):**
- `test_reports/qbank_artifacts/p0_verify.py`, `p0_verify2.py` — the ad-hoc live-API verification harnesses used for section C
- `test_reports/qbank_artifacts/p0_verify_results.json`, `p0_verify2_results.json` — their raw output

No file outside this list was modified by this task. No file was modified for style/cleanliness reasons alone.

---

## G. Git safety

- `git status --short` before this task: **64** entries (30 modified, 34 untracked). After this task: **65** entries — the only net change is the one new test file (`tests/unit/test_p0_routing_fixes.py`); every pre-existing modified/untracked file is still present, unchanged, and un-discarded.
- The three target files (`orchestrator.py`, `chat_shared.py`/`fulltransfer_qa.py` region, `appinfo_qa.py`) already carried unrelated pre-existing edits from earlier work this session (an "HQDT" acronym addition); those edits were confirmed, before touching anything, to be in different line ranges than this task's targets, and are still present, untouched, in the current working tree.
- No destructive git command (`reset --hard`, `clean`, `checkout -- .`, stash, etc.) was run at any point.
- **Nothing was committed or pushed.** `git diff`/`git status` as of the end of this task still shows every change — baseline's plus this task's three targeted edits plus the one new test file — as ordinary uncommitted working-tree changes, exactly as the task required.
- No secret, token or real credential was added to any tracked file. The live-verification scripts (`p0_verify.py`, `p0_verify2.py`) read the test officer's credentials from `QBANK_EMAIL`/`QBANK_PASSWORD` environment variables with the already-known seeded sample-DB test account as a fallback default (the same pattern already used by `qbank_harness.py` from the prior task, fixed earlier this session after an automated security-review finding) — never a real account, and no token was written to a tracked file.

---

## Acceptance criteria — final status

| Criterion | Status |
|---|---|
| "information" no longer triggers the format explainer | ✅ verified (unit + live API) |
| Genuine format/structure questions still work | ✅ verified |
| "automatically" no longer triggers the automutated lookup | ✅ verified (unit + live API, including original failing context) |
| Explicit automutated-field queries still work | ✅ verified |
| 0158 SIS recommendation reaches correction-modification evidence | ✅ verified (unit + live API, exact original reproduction) |
| 0153 SIS recommendation retains full-transfer behaviour | ✅ verified |
| 0154 workflow queries reach the appropriate path | ✅ verified (unchanged, correct "not held for this code" answer) |
| Streaming and non-streaming paths covered | ✅ both `orchestrator.py` call sites patched identically; live verification used the streaming `/chat/stream` endpoint (the only one the real frontend uses) |
| Regression tests added and executed | ✅ 20 new, all passing |
| Existing tests relevant to these changes still pass | ✅ 172/172 (152 pre-existing + 20 new) |
| Actual API regressions tested where possible | ✅ live server, real DB, real sessions |
| Pre-existing uncommitted changes remain intact | ✅ confirmed (section G) |
| Final remediation report created | ✅ this document |

No criterion is marked BLOCKED — every one was directly verifiable in this environment.
