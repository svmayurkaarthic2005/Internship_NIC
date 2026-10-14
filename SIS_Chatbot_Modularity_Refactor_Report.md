# SIS Chatbot — Modularity Refactor Report

**Repository**: `Internship_NIC` · **Branch**: `main` · **HEAD at start**: `29927ad`
**Primary file**: `backend/services/chatbot.py` · **Nothing committed or pushed.**

## Result at a glance (updated — second pass: stage decomposition + IP fix)

| | Original | After pass 1 (modules only) | After pass 2 (stage decomposition, this pass) |
|---|---|---|---|
| `chatbot.py` lines (`wc -l`) | **32,472** | 13,806 | **8,668** |
| `_process_chat_impl` lines | 6,351 | 6,178 | **2,993** |
| `_process_chat_stream_impl` lines | 5,786 | 5,685 | **3,729** |
| New modules | — | 10 | **13** (+3: `chat_dispatch.py`, `chat_dispatch_stream.py`, `chat_answer.py`) |
| Circular imports | — | 0 | **0** (all 13 still import first in a fresh interpreter) |
| Unresolved global names (stdlib `symtable` check) | — | 0 live / 3 dead | **0** (dead-code references fixed too, see Findings) |
| IP cross-comparison defect | crashes (`TypeError`) | crashes (preserved, flagged) | **fixed**, tested separately (12/12) |
| New test regressions | — | 0 | **0** |

Line counts are measured, not estimated. This pass is a second session on top
of pass 1 (module extraction only, documented below as "Earlier pass"). It
does the thing pass 1 explicitly left undone: carving the two processing
functions' *bodies* into stages, not just moving the helpers around them.

## What criterion 3 actually achieved — second pass

Pass 1 left both processing functions largely monolithic (6,178 / 5,685
lines) and said so. This pass extracted three more cohesive stages, one at a
time, each verified (compile → import-order → architecture test →
differential-equivalence check) before the next was attempted:

| extraction | from | to | lines moved | new module |
|---|---|---|---|---|
| 1 | `_process_chat_stream_impl`'s `compare_applications` intent block | `chat_dispatch_stream.dispatch_intent_stream()` | 1,988 | `chat_dispatch_stream.py` |
| 2 | `_process_chat_impl`'s equivalent dispatch block (incl. the CAN-number early-return branch) | `chat_dispatch.dispatch_intent()` | 2,111 | `chat_dispatch.py` |
| 3 | `_process_chat_impl`'s answer-building block | `chat_answer.build_answer()` | 1,125 | `chat_answer.py` |

Each stage hands back a `StageOutcome` (`bound: dict`, `response`, `early:
bool`) via `chat_pipeline.py`'s new `_bound()` / `StageOutcome` helpers — the
same uniform contract as pass 1's `early_guard_reply()`, generalised so a
stage with an early `return` inside its original span still works (the
caller checks `_stage.early` and returns `_stage.response`). Every stage
parameter was classified by definite-assignment analysis into always-present
(passed by name) vs maybe-present (passed only if actually bound, via
`_bound(locals(), (...))`, which preserves `UnboundLocalError` semantics for a
name neither side ever assigns). Function bodies were moved by exact line
span, byte-for-byte; only the call site and the hand-off wrapper are new code.

**What is still not extracted, and why this is a deliberate stopping point,
not an oversight:** both pipelines still have one large block left —
`_process_chat_impl`'s "format `response_text` from `structured_data` by
intent, then persist and return" tail (~800 lines, the `if html_response or
response_text:` chain through the function's end) and
`_process_chat_stream_impl`'s equivalent (~900 lines plus its own SSE framing
and persistence, which genuinely differs in shape from the non-streaming
tail — see "Streaming / non-streaming" below, unchanged from pass 1). Both are
candidates for a fourth/fifth stage using the same method. They were not
attempted in this pass because:

1. The three extractions already made were independently verified end to
   end (architecture test, the dedicated IP test, a live 101/101 integration
   re-run, 10 other regression suites, and a 1,754-question
   differential-equivalence run against the pristine pre-refactor baseline —
   all below). Each further stage needs the same discipline, and stacking a
   fourth unverified extraction on top of three just-verified ones trades a
   known-good state for an unknown one for no immediate requirement.
2. The remaining blocks are *response formatting*, not *control flow* — a
   long `elif intent == X: response_text = f"..."` chain. Decomposing it
   further is safe but does not change the readability picture the way the
   dispatch/answer extractions did (those pulled ~4,000 lines of branching
   logic, including early returns, out of the orchestrators).

`_process_chat_impl` and `_process_chat_stream_impl` are now readable
orchestrations — a guard chain, a dispatch call, (non-streaming only) an
answer-building call, then response formatting and persistence — rather than
one undifferentiated ~6,000-line body each.

## New modules

| Module | Lines | Defs | Responsibility |
|---|---|---|---|
| `chat_pipeline.py` | 135 | 2 | Shared pipeline stages. `early_guard_reply()` — the deterministic guard chain both pipelines run before intent routing. |
| `chat_shared.py` | 2,677 | 51 | Foundational helpers and constants used across modules (content/clear/mutation detection, domain evidence, `_cm_strong`, register-wide filters, widely-shared regexes). Imports no other `chat_*` module. |
| `chat_smalltalk.py` | 3,299 | 68 | Greetings, acknowledgements, capability / out-of-scope / unknown-message replies, sentiment, fee text, rule-topic answers (workflow steps, IGRS/CAN rules). |
| `chat_response_formatting.py` | 2,009 | 44 | Count/result-limit formatting, reformat requests, `_scoped_list_answer`, status/stage yes-no renderers. |
| `chat_untracked_fields.py` | 2,105 | 44 | The `_UNTRACKED_*` family: owner/parcel/signature/appinfo/geo-master/transfer fields the register does not hold. |
| `chat_followups.py` | 2,278 | 60 | Recall, acronym/full-form, recent-answer and listing context, geo scope, answer-number scrubbing, follow-up carry-forward helpers. |
| `chat_corrections.py` | 1,949 | 45 | Contentless/field-visit negation and aggregation, workflow remarks, multi-question splitting (`_big_plan`, `_plan_requests`), owner/record comparison parsing. |
| `chat_application_queries.py` | 3,633 | 63 | Application-number validation messages, `resolve_application_reference`, last-application and visit-plan rendering, jurisdiction/ward/workload, comparison building, subdivision answers, register listing. |
| `chat_service_codes.py` | 1,644 | 18 | Explain/detail negation, comparison-mode parsing, pairwise and whole-catalogue service-code comparison, service-code lookup, negative-correction responses. |
| `chat_routing.py` | 680 | 19 | The `_X_turn` dispatch family (recheck, register gap, how-to, stage definition, calc, stats, merge, …). |
| `chat_dispatch.py` *(this pass)* | 2,375 | 1 | `dispatch_intent()` — the non-streaming pipeline's intent-dispatch stage (the `compare_applications` branch and everything it routes to, plus the CAN-number early-return). |
| `chat_dispatch_stream.py` *(this pass)* | 2,247 | 1 | `dispatch_intent_stream()` — the streaming pipeline's equivalent dispatch stage. Kept separate from `chat_dispatch.py` rather than merged/shared, because the two pipelines' delivery already diverges (see "Streaming / non-streaming") and forcing one shared function would either reintroduce that divergence as an `if streaming:` branch inside the stage or risk silently losing it. |
| `chat_answer.py` *(this pass)* | 1,373 | 1 | `build_answer()` — the non-streaming pipeline's answer-building stage (RAG context, LLM prompt assembly, bypass-HTML decision). No streaming equivalent extracted yet (see "still not extracted" above). |

`chat_pipeline.py` grew from 135 to 171 lines in this pass: `_Unset`/`_UNSET`,
`_bound()` and `StageOutcome` were added alongside the existing `EarlyReply`
dataclass pass 1 already used, so all four stage extractions (this pass's
three plus pass 1's guard-chain stage) share one hand-off contract.

Every module is under 4,000 lines. Names are by dominant responsibility; a few
helpers sit in `chat_shared.py` rather than their "thematic" home because
several modules need them (see Dependency strategy).

## What stayed in `chatbot.py`, and why

- `_process_chat_impl`, `_process_chat_stream_impl`, `process_chat`,
  `process_chat_stream`, `save_chat_messages`, `_build_table_data_raw` — the
  orchestration itself.
- `_load_followup_context` and the six functions that call it directly
  (`_channel_basis_evidence`, `_negation_as_exclusion`, `_drop_rows_direct`,
  `_compound_followup`, `_record_field_turn`, `_number_turn`) plus `_single_turn`.
  Tests patch `backend.services.chatbot._load_followup_context`; a caller moved
  to another module would bind its own reference and silently stop seeing the
  patch.
- `create_chat_session`, `get_session_history`, `get_officer_sessions`,
  `handle_chat`, `_build_table_data`, `_requested_loc_column_order` —
  `backend/routers/chat.py` imports the first three from `chatbot` directly.
- All import statements, unchanged.
- A compatibility facade: explicit `from backend.services.chat_X import (...)`
  re-exports of every name the remaining code calls, plus every name imported
  from `chatbot` anywhere else in the repository.

## How the work was done

1. **Inventory** with `ast`: 426 top-level functions, 481 constants, line spans.
2. **Dependency graph** of function→function, function→constant and
   constant→constant references. No cycles among the 416 movable functions.
3. **Module assignment** by source region, then a check for "backward" edges
   (an earlier module needing a later one, which would be a circular import).
   25 functions caused them; their transitive closure (51 functions, 101
   constants) became `chat_shared.py`, after which there were zero backward
   edges.
4. **Mechanical extraction by exact line span** — function bodies, constants,
   and the comment blocks directly above them were copied verbatim; nothing
   was retyped.
5. **Verification after each attempt**: `py_compile`, import, an exhaustive
   check that every name the remaining code needs resolves, then the suites.

Bugs found in my own extraction along the way, all fixed before the final run:

| Bug | Cause | Fix |
|---|---|---|
| `NameError: _COL_LIST` at import | Graph covered function bodies but not constants that reference other constants | Added constant→constant edges; closed the shared set over them |
| `_load_followup_context` moved despite being resident | Orphan-comment forward-fill overwrote the resident span | Resident spans became hard barriers to the fill |
| `chatbot.py` cut to 1,106 lines | Barrier marker not cleared past the giant functions, so they were dropped | Cleared over the whole file; the backup made this recoverable |
| Test imports failing (`_recent_nonattachment_claim`, …) | Only internal callers had been considered | AST-scanned the whole repo: 82 names imported from `chatbot`; all re-exported |
| `name '_process_chat_impl' is not defined` (integration suite, 2 cases) | Moved code calling back into `chatbot.py` had no binding | Resolved through the module object at call time (`_cb()`) |
| Circular import when `chat_routing`/`chat_corrections` loaded first | First version of that fix imported `chatbot` at module load | Moved the import inside `_cb()` |

## Streaming / non-streaming

The guard chain — contentless message, mutation request, bare language
instruction, same-message filter contradiction, unsupported service code,
unanswerable message, capability / out-of-scope — was written twice, once per
pipeline (210 and 133 lines). It is now decided once by
`chat_pipeline.early_guard_reply()`, which is pure (no DB, no session, no LLM)
and returns an `EarlyReply` (text, intent, action, save?, clear-session?, log
line). Delivery was **not** unified because it genuinely differs:

- non-streaming: mark cleared → log → save → return the response dict;
- streaming: mark cleared → log → emit the SSE frame (with `action` for a clear)
  → save → return.

Each pipeline keeps its own order, frames and persisted rows. Even the log
text is preserved: two of the streaming guards never had the " (stream)"
suffix, and `EarlyReply.stream_log_suffix` keeps it that way. The stage
returns `special_scope_kind` too, because the praise/complaint branch right
after the chain reads it.

## Compatibility and monkeypatching

- **Legacy imports**: all 82 names imported from `backend.services.chatbot`
  anywhere in the repository resolve, and are the *same objects* as in their new
  modules (not copies).
- **Patch points**: the repository patches exactly three names on `chatbot`:
  `call_llama`, `run_agent`, `_load_followup_context`. All their call sites
  are still inside `chatbot.py`, so patches still apply. Moved code that calls
  back into `chatbot.py` does so through `_cb()`, a call-time module lookup,
  so a patch on `chatbot.save_chat_messages` is also seen there (tested).
- **No circular imports**: each of the 11 modules imports successfully as the
  first import in a fresh interpreter (tested).
- **Authorization / jurisdiction**: no query, filter or access check was
  edited. Function bodies were moved byte-for-byte; the one rewritten region
  (the guard chain) touches no data.
- **CAN fix preserved**: "Can you explain?" still falls through the guard
  chain instead of being read as a CAN-number lookup (tested), and
  `_strip_can_modal()` in `followup_context.py` is untouched.

## Tests — actual commands and results

Baseline = the same suite on the original `chatbot.py` (swapped back in
temporarily), so pre-existing failures are distinguished from regressions.

| Suite | Baseline | After refactor |
|---|---|---|
| `SIS_Question_Mark_Regression_Tests/test_qmark_multiturn_integration.py` (live DB) | 101/101 | **101/101** (an intermediate run was 99/101 and exposed the moved-code back-reference bug, fixed above) |
| 10 × `SIS_ServiceCode_FileDB_Regression_Tests/*.py` (pairwise 406, catalogue routing, F-Line, CSV, negation antecedents, comparison modes, crash regressions, rag_language) | all pass | all pass |
| `test_tanglish_language.py` | 100/100 | 100/100 |
| `test_tanglish_to_tamil_policy.py` | 50/50 | 50/50 |
| `SIS_Regression_Tests/test_tamil_negation_language_bug.py` | 28/28 | 28/28 |
| `SIS_Continue_Regression_Tests/test_continuation_five_types.py` | 19/19 | 19/19 |
| `SIS_Continue_Regression_Tests/test_continuation_pipeline_defects.py` | 36/36 | 36/36 |
| `test_upload_qa_defect_fixes.py` | 38/38 | 38/38 |
| `test_dis_sis_glossary_typos.py` (live) | 17/17 | 17/17 |
| `test_dro_escalation_questions.py` (live) | 15/15 | 15/15 |
| `test_stage_referral_questions.py` (live) | 17/17 | 17/17 |
| `SIS_Question_Mark_Regression_Tests/test_qmark_classification.py` | 2/5 (3 fail) | 2/5 — **same 3, pre-existing** |
| `test_negq.py` | 43/44 (1 fail) | 43/44 — **same 1, pre-existing** |
| **New** `SIS_ServiceCode_FileDB_Regression_Tests/test_chatbot_modularity.py` | — | 48/48 (pass 1) / **48/48 re-run against this pass's 8,668-line state** |

The modularity test covers first-import cycle freedom for all modules, legacy
import paths and object identity, patch visibility through `_cb()`, the guard
stage's decisions on a table of messages, and — against the live database
with persistence patched out — that the streaming and non-streaming pipelines
return the same answer for guard-chain messages.

**Not run**: `backend/sample_db/test_*.py` (e.g. `test_intent_coverage`) — these
files no longer exist in the working tree (pre-existing; CLAUDE.md notes the
`sample_db_new` migration).

## Phase 4 — this pass's own verification (all against the final 8,668-line state)

| Check | Result |
|---|---|
| First-import-order freedom, all 5 touched/new modules (`chat_dispatch`, `chat_dispatch_stream`, `chat_answer`, `chat_pipeline`, `chatbot`), each as the **first** import in a fresh interpreter | **0/5 failed** |
| `py_compile` — every new/modified module | clean |
| Whole-repo `symtable`-based undefined-global-name check, 14 modules | **0 unresolved** (was 3, all in dead code — fixed anyway, see Findings) |
| `test_chatbot_modularity.py` (architecture, 48 checks) | **48/48** |
| `test_ip_comparison_dispatch.py` (new, 12 checks) | **12/12** |
| `SIS_Question_Mark_Regression_Tests/test_qmark_multiturn_integration.py` (live DB, 101 cases) | **101/101** — the pass-1-documented 101/101 result reproduces unchanged |
| 10 other `SIS_ServiceCode_FileDB_Regression_Tests/*.py` suites (pairwise resolution, all-service comparison routing, CSV plural-column, F-Line/appeal multi-code, negative-correction antecedent, pairwise architectural disparity, rag_language extraction, service-code alias collision, calc-crash regression, wrong-selection/table-rendering) | **all pass**, same pre-existing "expected" skips as before (CSVCOL-001, SVCCODE-001 — unrelated, untouched) |
| **Differential equivalence**: real questions through both `process_chat` and `process_chat_stream`, LLM/agent/embeddings/RAG/semantic-intent/persistence deterministically mocked, against the pristine pre-Phase-2 `chatbot.py` | **448/448 overlapping questions diff to zero** on everything except `is_overdue`-derived fields (6 of 448 — see below); **0 of 1,754 questions raised an exception** in either pipeline |
| `git diff --check` | clean except pre-existing cosmetic trailing-whitespace warnings (Finding 3) |
| Duplicate top-level `def`/`async def` names, `chatbot.py` + all 3 new stage modules | **0** |

**On the 6 differential "diffs":** every one traces to the same single field —
`is_overdue` reading `false` in the saved baseline snapshot and `true` now
(and the knock-on text/count that depends on it: "Pending ⚠️", a high-priority
count, an overdue-count field). This is CLAUDE.md's documented behaviour —
`is_overdue` is re-derived by `overdue_refresh.py` every 6 hours and the
baseline snapshot was taken hours before this run — not a code difference. No
other field in any of the 448 overlapping answers, across both pipelines,
differs.

## Phase 3 — the IP-comparison defect, fixed as a separate change

**Finding 1 above is now fixed**, independently of the stage decomposition,
as the task required. Root cause (confirmed by reproducing the crash live
through both pipelines before touching anything): `chatbot.py` defined
`_ip_comparison_answer` twice across two different extracted modules —
`chat_untracked_fields.py`'s 5-parameter cross-application version ("do these
applications share an IP?") and `chat_application_queries.py`'s 6-parameter
single-application version ("same IP as application X?"). The facade import
list bound whichever loaded second, so both pipelines' call sites — which
pass the cross-application version's 5 arguments — crashed with
`TypeError: _ip_comparison_answer() missing 1 required positional argument`
whenever an officer asked a cross-application IP question.

**Fix**: renamed the cross-application function to
`_ip_cross_comparison_answer` (one name, `backend/services/chat_untracked_fields.py`)
and updated the two call sites plus the one facade-import line in
`chatbot.py` to match. No argument was invented, no exception was swallowed,
and the single-application lookup (`_ip_comparison_answer`, unchanged) was
left exactly as it was — confirmed still correct by the new test below.
Isolated in `SIS_IP_Comparison_Fix.patch` (a standalone diff covering only
these 3 edits) so it can be reviewed independently of the much larger
structural diff.

**Test**: `SIS_ServiceCode_FileDB_Regression_Tests/test_ip_comparison_dispatch.py`
(new, 12 checks: 10 unit-level — both functions exist and are distinct,
cross-app grouping/"they"-reference/Tamil rendering, single-app lookup
unaffected, unrelated questions declined — plus 2 that run the real
`process_chat` / `process_chat_stream` against the live database with only
`get_ip_comparison` and persistence mocked). Fixtures are synthetic
(`9999/0154/99/000001` etc., never a real application number). Reproduced the
pre-fix crash first (both pipelines logged the exact `TypeError` and returned
the generic error-fallback text), then confirmed the fix: **12/12 passed**.

## Findings (pre-existing, confirmed harmless or fixed)

1. ~~Cross-application IP comparison always raises `TypeError`.~~ **Fixed in
   Phase 3 above.**
2. **Three shadowed duplicates — confirmed dead code, now internally
   consistent too.** `_render_status_role_answer`, `_is_stage_ever_question`,
   `_render_stage_ever_answer` are each defined twice inside
   `chat_response_formatting.py`; the *second* definition of each wins at
   module load (ordinary Python name-rebinding), so the first copies were
   already unreachable before this pass and remain unreachable now — this is
   not a live bug. The first copies referenced `_STAGE_LABELS`,
   `_STAGE_EVER_PHRASE_RE` (both only in `chatbot.py`) and `_wfh_fmt_dt` (only
   in `chat_smalltalk.py`) with no import, which the whole-repo
   undefined-global checker (Phase 4 below) correctly flagged as 3 unresolved
   names. Fixed anyway, since it costs three lines and leaves no landmine for
   whoever eventually deletes the dead duplicates: added a `_cb()` lazy
   back-reference to `chatbot.py` (the same pattern `chat_dispatch.py` /
   `chat_dispatch_stream.py` use) for the first two names, and a direct
   `from backend.services.chat_smalltalk import _wfh_fmt_dt` for the third.
   Re-ran the checker: **0 unresolved names repo-wide** (was 3, all dead).
3. **207 lines of trailing whitespace** exist in the original and were moved
   verbatim; none were added. `git diff --check` is clean except for these
   pre-existing, cosmetic warnings (confirmed: none are new in this pass).

## Remaining risks and limitations

- The two processing functions are still ~6,000 lines each (see above).
- Some re-exports in the facade are now used only by tests (e.g. the guard
  helpers the pipelines no longer call directly). They are kept deliberately
  for compatibility.
- `chat_shared.py` is a dependency hub by design; a helper added to another
  module and then needed by an "earlier" module must move there too, or a
  circular import results. `test_chatbot_modularity.py` catches that.

## Changed files

- **Modified this pass**: `backend/services/chatbot.py` (3 stage extractions
  + the 2 IP call-site renames + 1 facade-import line), `backend/services/
  chat_untracked_fields.py` (the IP function rename), `backend/services/
  chat_response_formatting.py` (dead-code undefined-global fix, Finding 2),
  `backend/services/chat_pipeline.py` (added `_Unset`/`_UNSET`, `_bound()`,
  `StageOutcome`). Also `frontend/js/chat.js` + `frontend/chatbot.html` — two
  user-reported frontend bugs fixed in the same session, unrelated to the
  chatbot.py refactor (see "Frontend fixes" below).
- **Added this pass**: `backend/services/chat_dispatch.py`,
  `chat_dispatch_stream.py`, `chat_answer.py`;
  `SIS_ServiceCode_FileDB_Regression_Tests/test_ip_comparison_dispatch.py`;
  `SIS_IP_Comparison_Fix.patch`.
- **Added in the earlier pass** (unchanged by this one): `backend/services/
  chat_pipeline.py` (base), `chat_shared.py`, `chat_smalltalk.py`,
  `chat_response_formatting.py` (base), `chat_untracked_fields.py` (base),
  `chat_followups.py`, `chat_corrections.py`, `chat_application_queries.py`,
  `chat_service_codes.py`, `chat_routing.py`;
  `SIS_ServiceCode_FileDB_Regression_Tests/test_chatbot_modularity.py`.
- **Regenerated by running the integration suite** (its own output, not hand
  edits): `SIS_Question_Mark_Test_Matrix.csv`,
  `SIS_Question_Mark_Conversation_Flows.jsonl`,
  `SIS_Question_Mark_Failure_Cases.jsonl`.
- **Untouched**: `rag.py`, `rag_language.py`, `followup_context.py`,
  `postgres.py`, models, schema, and every frontend file other than the two
  named above.

## Frontend fixes (this session, reported by the user, unrelated to the chatbot.py refactor)

1. **New Chat / page-reload lost the "Today" date divider.** Both
   `renderMessagesFromHistory()` and `startNewChat()` did
   `chatMessages.innerHTML = ''`, wiping the always-expected static divider
   along with the messages. Fixed with a `resetChatMessages()` helper that
   restores `<div class="date-separator-new"><span>Today</span></div>` before
   re-rendering. Verified live: reload keeps the divider and restores
   history; New Chat keeps the divider, shows a fresh welcome message, and
   clears stored history.
2. **A quick-suggestion chip ("Pending applications", "Overdue", …) sent the
   question twice and left the stale text sitting in the input box.**
   `chat.js`'s `setupEventListeners()` attached a *second* click handler to
   `#quickChips`, duplicating the one already wired inline in
   `chatbot.html`. The first call's `sendMessage()` re-filled the input with
   the question and sent it; the duplicate's call then re-filled it again and
   was rejected ("please wait"), leaving the just-typed text visible even
   after the real answer came back. Fixed by deleting the duplicate listener.
   Verified live: one message sent per click, input box empty once the
   answer renders.

`chatbot.html`'s `chat.js` cache-buster was bumped twice (`?v=20261014_001`,
then `?v=20261014_002`) so a cached browser picks up both fixes.

## Earlier pass (kept for history)

A previous session extracted language detection from `rag.py` into
`rag_language.py` (`detect_language`, `is_short_reply`,
`get_prior_turn_language`, `_explicit_tamil_request`), with `rag.py` as a
re-exporting facade and `test_rag_language_module_extraction.py` (7 checks).
That module and its test are unchanged by this pass and still pass.
