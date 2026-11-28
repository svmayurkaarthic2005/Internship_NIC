# P3.3 Canonical Entity Resolution, Focus Correctness and Workflow History — Final Report

Supersedes the earlier P3.3 report. Baseline and call-path map: `P3_3_ENTITY_RESOLUTION_BASELINE.md`
(that baseline was written after the first fix landed; its pre-fix evidence is the P3.2 live run and a
mutation run with the fix patched out).

## 1. The original stale-history defect

Server context: `current_application = 2026/0153/02/045470`, pair 045470 / 045472.
Client `chat_history`, resent unchanged every turn: a comparison of `2026/0154/02/012766` and
`2026/0154/02/999999`. For "What was its rejection reason?" the P3.2 live run answered
*"2026/0154/02/012766 is not in a rejected status"*, so the client copy outvoted the server focus.

Cause: `ChatRequest.chat_history` went straight from the router into `process_chat` /
`process_chat_stream`. Every history fallback that recovers an application number read it:
`resolve_application_reference` (record scan, `_app_numbers_in_recent_context`,
`_extract_app_number_from_context`, `_app_numbers_from_last_listing`), `unheld_qa.context_app`
(used by the rejection, who-decided, workflow and documents hooks), `_context_from_history`,
`_cm_strong`, `_app_evidence_in_scope`.

## 2. Design and precedence

**One request-scoped canonical context, loaded once per path:**

1. `_server_transcript()` (orchestrator) runs at the start of `process_chat` and `process_chat_stream`.
   It is this officer's own stored transcript for the session:
   - bounded to 40 messages, the frontend's own limit;
   - respects "clear" via `messages_since_session_start`;
   - `[]` for a missing, foreign or unreadable session.

   The client copy is accepted (the API field is kept) and logged by count and application numbers,
   but it no longer feeds anything.
2. `_load_followup_context()` is still called **once** per path. Its result is
   - published for the turn as `followup_context.CANONICAL` (a ContextVar: reset at entry, so never
     carried between turns or requests), and
   - passed to the resolver as `canonical_focus`.
3. `followup_context.canonical_tiebreak(app)` checks any number a history fallback recovered against
   the canonical set:
   - inside a set of several, the focus wins; with no focus nothing is chosen and the caller asks;
   - outside the set it stands;
   - it never introduces a number.

   It is used by `resolve_application_reference` (after its record scan and after all its fallbacks)
   and by `unheld_qa.context_app`.
4. `followup_context.singular_target(message, ctx)` decides what "its / this application" means over a
   carried set: an ordinal pick, else `current_application`, else *ask*. It is used by details,
   workflow remarks and the list branch of `resolve()`.

**Precedence, as implemented:**

| # | rule | where |
|---|---|---|
| 1 | explicit IDs in the message win (two IDs → comparison, message order) | unchanged P3 paths; the gate's `extract_application_number` |
| 2 | pair / ordinal references resolve from the server pair | P3.1 `_resolve_pair_reference`; "both" may also restore `prior_pair` (§4) |
| 3 | a singular follow-up uses `current_application` | `singular_target`, `canonical_tiebreak`, resolver `canonical_focus` |
| 4 | one unambiguous canonical application is used when there is no focus | `singular_target` (set of 1); transcript fallback outside a carried set |
| 5 | missing or ambiguous → clarification | `ambiguous_context` verdict / `build_ambiguous_reference_message` / `resolve()` clarification |
| 6 | client history never selects | `_server_transcript` |
| 7 | every retrieval is re-authorized | unchanged: `lookup_application_access`, `get_jurisdiction_filter`, `get_workflow_history` |

**`chat_history` audit:** 555 references across 22 modules. They fall into three groups:
- **Identity consumers** (choose an application): the resolver family, `_context_from_history`,
  `unheld_qa.context_app`, `_recent_app_number_in_history`, `_cm_strong`, `_app_evidence_in_scope`,
  `_bare_app_status`, the record-module parsers (`_na/_um/_tslr/_sd_parse`), `negq._hint`,
  `compare_followup._sides`.
- **Conversational** (they don't choose an application): prior-turn language, `_conversation_recall_reply`,
  `_last_user_message`, `_resolve_prev_intent`, `_reformat_previous_answer`, the agent's prompt
  history, `_howto_turn`'s "what does that imply", `_rescope_list_followup`, `_type_swap_followup`.
- **Internal:** `_answer_big`, `_answer_multi`, `_single_turn` build their own sub-question history
  and never see the client copy.

None of the 555 references was rewritten:
- Conversational consumers get the same text as before, because a real client sends exactly what the
  server stored.
- Identity consumers become server-sourced and, where a carried set exists, tie-broken by the
  canonical context.
- One behavioural difference: a client that sends history for a session the server never recorded
  (another browser, or after a server-side clear) loses that context. That is the required invariant.

## 3. Call paths corrected

| path | before | after |
|---|---|---|
| router → `process_chat` / `process_chat_stream` | client history used throughout | `_server_transcript` at entry |
| `resolve_application_reference` record scan (`_cm_strong`) | first number in history | + `canonical_tiebreak` |
| `resolve_application_reference` fallbacks | ambiguous → ask, or first match | `canonical_focus` breaks ties; + `canonical_tiebreak` |
| `unheld_qa.context_app` (rejection / who-decided / workflow / documents hooks) | newest number in history | + `canonical_tiebreak` |
| `fctx.resolve` list branch | "its details" over a pair → field over both | clarification when no focus |
| `general_query` with a gate-resolved app | LLM fallback | `_ask_as_named`: number appended, intent re-read |

## 4. Focused details and workflow-remarks fixes

* `_carried_details_message`: "its / this application" → `singular_target` (focus, or ordinal).
  "Their / both / all" keep the pair. `_wants_carried_list_details` no longer claims a singular
  question that has no focus, so `resolve()` asks.
* `_workflow_remarks_answer`: same narrowing. With no focus it returns
  `build_ambiguous_reference_message`. "Both applications' workflow remarks" keeps the pair.
  The P3.2 `workflow_history` remark retrieval is unchanged.
* **Pair membership:** the remarks answer still saves the pair context. In `build_context`, a new
  unrelated explicit application keeps P3.1's `active_applications == [C]` (pinned by an existing
  test). The previous pair is kept as a breadcrumb, `filters["prior_pair"]`, which only an explicit
  "both" reads. Returning to A or B makes the pair active again. So "Show the status of C" then
  "Show both applications again" restores A/B.

## 5. Workflow-history route

The existing `postgres.get_workflow_history` (jurisdiction-checked; `None` means inaccessible or
missing, `[]` means no rows) and the existing `chat_smalltalk._workflow_history_answer` are reused.
No new data layer was added.

* New shapes in `_workflow_history_answer`:
  - **full log** ("workflow history", "previous stages", "events recorded", "stage history"), rendered
    by the existing `_render_workflow_history`, so both log paths print identically;
  - **"when did it move to <stage>"**, the hop that entered that stage, or a statement that it never did.
* **"What happened before it was rejected?"**: keeps the P2 "Immediately before…" lead (a P2 test pins
  it), then lists every recorded event before the outcome, in stored order, with stored timestamps.
* **Timestamp fix:** `_wfh_fmt_dt` converted stored times to IST. That printed 2026-09-11 01:05 for a
  hop the source records as `update_dt 2026-09-10 19:35:14`, and disagreed with the log, decision
  date and remark date. It now prints the stored value.
* **Gate fallthrough:** a `general_query` turn the gate resolved from context ("Show its workflow
  history" with any context, even one application) used to go to the LLM and time out. Now
  `_ask_as_named` re-reads it with the number appended, and the deterministic early hook answers it.
* **Procedural questions stay on how-to:** `howto_qa` `steps_nisd` / `steps_isd` now also cover
  "how does / explain / what is the … workflow … NISD/ISD". They are fenced against data questions
  ("my", "status", "its", "this application", or an application number).

## 6. Live transcripts (real server, real DB, real JWT; 43 turns)

Full per-turn tables, joined from the server's resolution log:
`test_reports/qbank_artifacts/p3_3_live_turns.md`. Raw answers: `p3_3_entity_resolution_results.json`.
"Focus / pair" is the server context the turn resolved against. "Gate" is the verdict and app chosen
before retrieval.

| Q | question | normal | none | stale (012766/999999 every turn) | handler | auth |
|---|---|---|---|---|---|---|
| 1 | Compare 045470 and 045472 | both | both | both | compare_applications | gate ok |
| 2 | What about the other one? | 045472 | 045472 | 045472 | followup_list (pair) | re-query, jurisdiction-filtered |
| 3 | What is its status? | 045472 rejected | same | same | application_status | gate ok 045472 |
| 4 | Show both applications again. | both | both | both | compare_applications (pair) | re-query |
| 5 | Switch back to the first application. | 045470 | same | same | followup_list | re-query |
| 6 | What was its rejection reason? | 045470 remark | same | same | early hook → `_rejection_reason_answer` | gate ok 045470 |
| 7 | Show its details. | 045470 only | same | same | application_status (details) | gate ok 045470 |
| 8 | Show its workflow history. | 045470 log | same | same | early hook → `_workflow_history_answer` | gate ok 045470 |
| 9 | What happened before it was rejected? | 045470 events | same | same | early hook → `_workflow_history_answer` | gate ok 045470 |

* **Explicit ID** after focus 045470 with stale history, "Show the status of 2026/0154/02/012766.":
  gate ok 012766 → "approved".
* **Fresh session**, client history only, "What is its status?": "Please specify the application number…".
* **Server log:** every stale turn logged `client_app_ids=['2026/0154/02/012766', '2026/0154/02/999999']`,
  and no gate or answer ever selected either.

## 7. Before / after resolved application IDs (stale history)

| Q | before (P3.2 live / pre-fix trace) | after |
|---|---|---|
| Q6 rejection reason | **012766** | 045470 |
| Q7 its details | both 045470 + 045472 | 045470 |
| Q8 its workflow history | "which one?" (with one app: LLM timeout) | 045470 |
| Q9 before rejected | one hop, IST-shifted time | 045470, both preceding hops, stored times |
| after plain compare: its details / its rejection reason | both / **045470 (guessed)** | "which one do you mean" |
| after plain compare: its workflow history | "which one" | "which one" |

## 8. Database evidence for Q6–Q9 (`applications` + `workflow_history`, 045470)

`applications`: NISD, status `rejected`, stage `REJECTED`. `workflow_history`, ordered by `performed_at`
(stored values):

| from | to | performed_at | remarks / rejection_reason |
|---|---|---|---|
| — | SIS | 2026-08-29 13:33:34 | `-` |
| SIS | TAHSILDAR | 2026-09-10 19:35:14 | `-` |
| TAHSILDAR | REJECTED | 2026-09-13 21:01:39 | `[N/06]//NO LINK from RH வேதராமன் மகன் பாலசுப்பிரமணியன்/ N-Parent /Linked Documents are not submitted` |

Layer-1 source `application_workflow` for the middle hop: `action_date 2026-09-10`,
`update_dt 2026-09-10 19:35:14.335`. This confirms the stored value is the recorded time.

* Q6 quotes the rejection remark verbatim.
* Q8 lists the three hops in order.
* Q9 lists the two hops before REJECTED with `2026-08-29 13:33` and `2026-09-10 19:35`. No field visit
  is mentioned (NISD has none).
* Q7's survey no. 110 and applicant match the comparison card.

## 9. Session and jurisdiction checks

* **Transcript:** read only when `ChatSession.officer_id` equals the JWT officer. A foreign session
  gives `[]` and an unknown session gives `[]` (unit-tested). The router's `verify_session_ownership`
  is unchanged.
* **Canonical context:** comes from `_load_followup_context`, which is owner-checked and time-limited.
  It is a request-scoped ContextVar reset at entry, so there is no cross-turn or cross-session leakage.
  A fresh session has no inherited pair (P3.1 tests and live test).
* **Never a grant:** the tie-break only selects among numbers the server already holds. Every pick
  still passes `lookup_application_access` or `get_workflow_history`'s jurisdiction filter.
  `_is_accessible` and the gate are untouched.
* **Inaccessible ≠ empty:** `_workflow_history_answer` returns `None` for an officer outside
  jurisdiction (the gate's refusal answers) and "No workflow history is recorded for X." for `[]`.
* **Logs:** counts and application numbers only. No message text, JWTs or personal data.

## 10. Streaming / non-streaming parity

Both paths call:
- `_server_transcript` and `CANONICAL.set(None)` at entry;
- `_log_entity_context` and `CANONICAL.set(ctx)` after the single context load;
- `canonical_focus` at the gate, then `_log_entity_gate` and `_ask_as_named`.

Live: Q6–Q9 answers over `/chat/stream` are character-identical to `/chat` (normalised whitespace) under
stale history. Unit: `test_stream_path_matches_non_stream`.

## 11. Unit-test counts

| | count |
|---|---|
| starting baseline | 376 passed |
| final | **450 passed, 0 failed** |
| `test_canonical_entity_resolution.py` | 28 |
| `test_focused_application_handlers.py` | 26 (4 client-history variants: normal, none, stale, conflicting) |
| `test_application_workflow_history.py` | 20 |

* Everything runs on the live DB, except:
  - the "empty history" test, which stubs `get_workflow_history` (no seeded file has zero rows);
  - the "inaccessible" test, which uses a synthetic officer with no jurisdiction (all 3 seeded officers
    can see 045470), but runs the real access path.
* No existing test was changed or weakened. My own earlier no-focus test was strengthened to require
  the clarification.
* **Mutation check** (first P3.3 round): with `_server_transcript` reverted to trusting the client,
  8 of 18 behaviour tests fail.

## 12. Latency

| measure | before | after |
|---|---|---|
| `_server_transcript` (40 msgs) | — | p50 1.8 ms, p95 2.7 ms |
| live mean per turn (Q1–Q9) | P3.2 runs: about 0.2–0.5 s per turn | normal 0.38 s, none 0.33 s, stale 0.32 s; all 43 turns 0.32 s, max 0.62 s |
| "Show its workflow history" | LLM path, timeout | 0.5 s deterministic |
| "When did it move to the Tahsildar stage?" | wrong record module | about 0.8 s deterministic |

## 13. Remaining unverified behaviour

* `points_at_one` is a word test (its / it / this application / the application versus both / their /
  all…). An unusual phrasing matching neither keeps the old list-wide behaviour, which is broader but
  never selects a wrong single application.
* `prior_pair` restores only on an explicit "both" phrasing (`_PAIR_BOTH_RE`). "The other one" after
  an unrelated application is deliberately not resolved against the old pair.
* Procedural how-to coverage was widened only for type-named phrasings. "What are the stages of the
  workflow?" (no ISD/NISD named) still has no how-to topic, as before.
* A turn that saves no transcript row is invisible to the next turn's history fallbacks.
  The FollowupContext still carries it.
* Only 3 seeded officers exist and all share jurisdiction over the test files. Cross-officer refusal on
  real data is covered only by the existing P3.1 and session-ownership tests, plus the synthetic-officer test.
* The CLAUDE.md top-level scripts (`test_followup_context.py`, `test_clear_chat.py`,
  `test_last_application.py`) are not in this checkout, so they were not run.

## 14. Changed files and Git safety

| file | change |
|---|---|
| `backend/services/chatbot/orchestrator.py` | `_server_transcript`, `_ask_as_named`, `_log_entity_context`, `_log_entity_gate`, `CANONICAL` set/reset, `canonical_focus` at both gates |
| `backend/services/chatbot/chat_application_queries.py` | `canonical_focus` parameter; `canonical_tiebreak` after the record scan and the fallbacks |
| `backend/services/followup_context.py` | `points_at_one`, `singular_target`, `CANONICAL`, `canonical_tiebreak`; `resolve()` list-branch clarification; `prior_pair` in `build_context` and `_resolve_pair_reference` |
| `backend/services/chatbot/chat_followups.py` | `_carried_details_message` / `_wants_carried_list_details` use `singular_target` |
| `backend/services/chatbot/chat_corrections.py` | `_workflow_remarks_answer` uses `singular_target`, asks when there is no focus |
| `backend/services/chatbot/unheld_qa.py` | `context_app` → `canonical_tiebreak` |
| `backend/services/chatbot/chat_smalltalk.py` | full-log and entered-stage shapes, before-outcome event list, stored-time formatting, `_WFH_CUE` additions |
| `backend/services/chatbot/howto_qa.py` | `_PROC` procedural cue for `steps_nisd` / `steps_isd`, fenced against data questions |
| new tests | `tests/unit/test_canonical_entity_resolution.py`, `test_focused_application_handlers.py`, `test_application_workflow_history.py` |
| new artifacts | `test_reports/qbank_artifacts/p3_3_entity_resolution_conversations.py`, `p3_3_join_server_log.py`, `p3_3_entity_resolution_results.json`, `p3_3_live_turns.md`; this report; the baseline |

* Branch `main`, HEAD `eaff14e` unchanged.
* Working tree: 106 pre-existing entries, all intact; 132 now, the extra 26 being new untracked files only.
* Nothing staged, committed, stashed, reset, cleaned or discarded. The 2 stash entries pre-date this work.
* Server stopped.
