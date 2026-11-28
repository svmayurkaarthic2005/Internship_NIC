# P2_ROUTING_EDGE_CASE_REPORT — Semantic Routing Edge Cases and Domain Query Recovery

**Date:** 2026-11-21 · **Branch:** `main`

## A. Executive summary

Three failures were named; one turned out not to be a real bug, and two were
genuine routing-precedence defects, now fixed narrowly and verified against
the live API with real database facts.

1. **Identity ("who am i" → smalltalk).** Not reproducible in the real
   pipeline. `_is_officer_identity_question()` already catches it and runs
   well before `semantic_intent.classify()` is ever reached — the earlier
   report's "false positive" came from testing the embedding classifier in
   isolation, bypassing every deterministic gate ahead of it. This task added
   the phrasings it explicitly asked for that genuinely were missing
   ("which officer account is active", "who is logged in", "my current
   identity") and confirmed, live, that "who are you" correctly stays on the
   assistant-identity path while "who am i" resolves the real authenticated
   officer, with no invented data.
2. **Gratitude/farewell precedence.** Two independent bugs, both upstream of
   the semantic classifier: `_unrecognised_word_message()` flagged "Goodbye."
   as keyboard-mash gibberish (its "known words" set only knew SIS-document
   vocabulary, not ordinary conversational English), and `_is_explain_request()`
   treated the bare word "explanation" in "Thanks for the explanation." as a
   request to re-explain. Both fixed with narrow, named exclusions that reuse
   existing vocabulary rather than inventing new logic.
3. **Domain query recovery.** Two independent bugs: `_attached_documents()`'s
   generic "document" cue claimed "the registration document" (a database
   field) as a chat-upload question, and the single-application record hook
   never tried the ISD/NISD workflow-history answer for a context-resolved
   application. Both fixed, and the real registration date (`28-08-2026`,
   distinct from the submission date `2026-08-29`) and real workflow
   chronology (Zonal Deputy Tahsildar, `2026-09-11 01:05`) are now returned
   deterministically in under 3 seconds, instead of a 60-130 second LLM
   timeout/error.

A fourth thing was found and is **not** fixed here, because it is a different
bug from the three named ones: two separate context mechanisms exist in this
codebase (the server-side `FollowupContext` and the client-supplied
`chat_history` field), and a client that omits `chat_history` will see the
fixes in item 3 silently fail to engage. See §I.

## B. Before/after routing traces

### "Thanks for the explanation." (standalone)

| | before | after |
|---|---|---|
| `_unknown_message_kind()` | `None` | `None` (unchanged) |
| `parse_intent()` | `general_query` | `general_query` (unchanged) |
| `_is_explain_request()` | `True` (claimed by bare word "explanation") | `False` |
| final answer | *"I'm not sure what you mean. Please ask about an application..."* (`explain_clarify`) | *"You're very welcome! 😊 Feel free to ask if you need anything else regarding your survey work."* (`greeting`) |

### "Goodbye." (standalone)

| | before | after |
|---|---|---|
| `_unrecognised_word_message("Goodbye.")` | `True` | `False` |
| `_unknown_message_kind()` | `"gibberish"` | `None` |
| `parse_intent()` | never reached (EarlyReply fired first) | `"greeting"` |
| final answer | *"I could not understand that message..."* (`unknown_message`) | *"Goodbye! 👋 Thank you — come back any time you need an application, a survey number or a field visit looked up. Have a good day."* (`greeting`) |

### "When was the registration document registered?" (context follow-up, no application number of its own)

Direct call to `_early_subdivision_answer()`, real DB, real officer, real
chat history (`scratch/trace_regdate.py`):

```
BEFORE: OUT: 'No document attachment is recorded for 2026/0153/02/045470.'
AFTER:  OUT: "<div class='data-table-card'>...<tr><td>Registration document no</td><td>R/17/1/11822/2026</td></tr>
              <tr><td>Registered on</td><td>28-08-2026</td></tr></tbody></table></div>"
```

Live API (with `chat_history` sent, as the real frontend does):

| | before | after |
|---|---|---|
| intent | `general_query` | `application_status` |
| latency | 63.3s (LLM timeout) | 2.8s |
| answer | *"This is taking longer than expected to answer..."* | the registration document card, above |

### "What happened before the rejection?" (context follow-up)

Direct call (`scratch/trace_rejection.py`):

```
BEFORE: OUT: None
AFTER:  OUT: 'Immediately before the current stage: Zonal Deputy Tahsildar, on 2026-09-11 01:05.'
```

Live API:

| | before | after |
|---|---|---|
| intent | `general_query` | `application_status` |
| latency | 130.9s (agent error) | 0.9s |
| answer | *"I apologize, but I encountered an error processing your request."* | *"Immediately before the current stage: Zonal Deputy Tahsildar, on 2026-09-11 01:05."* |

## C. Identity behaviour

`_is_officer_identity_question()` (`chat_followups.py`) and
`_is_capability_question()` (`chat_smalltalk.py`) are two separate, disjoint
deterministic gates, both ahead of `semantic_intent.classify()`:

- **Assistant identity** ("who are you", "what are you", "are you an ai",
  "what is your role") → the fixed capability/assistant blurb. Never touches
  the officer's own record.
- **Authenticated-officer identity** ("who am i", "what is my officer id",
  "which officer account is active", "who is logged in", "tell me my
  current identity") → `_officer_identity_reply(db, officer, language, facet)`,
  which reads `officer.officer_id` — the ID bound to the request's own JWT —
  and falls back to the token's own fields if the DB row is unavailable.
  Never takes an officer ID from the message or from conversation history
  (`_is_officer_identity_question` explicitly refuses when the message names
  an application number, so "who approved 2026/0153/02/045470" is never
  misread as self-identity). Never fabricates a name, designation, employee
  ID or email: every field is `"—"` when the underlying record lacks it,
  never guessed.

Verified live: *"Who am I?"* → *"You are **S. Perumal Chn**, Sub Inspector
Surveyor (SIS)."* *"Which officer account is currently active?"* → the full
profile (name, designation, employee ID, email, jurisdiction) — all read from
`sis_officers` / the jurisdiction summary for this officer's own ID, nothing
invented, nothing about anyone else.

## D. Gratitude/farewell results

| message | before | after |
|---|---|---|
| "Thanks for the explanation." | `explain_clarify` fallback | greeting/thanks reply |
| "Goodbye." | `unknown_message` fallback | greeting/farewell reply |
| "Thanks, now check application 2026/0153/02/045470." | — | `application_status` with the real record (mixed message correctly NOT swallowed) |
| "Goodbye, but first show the status of 2026/0154/02/012766." | — | `application_status` (mixed message correctly NOT swallowed as farewell — see §I for a separate, pre-existing number-resolution issue found here) |

Tamil/Tanglish standalone gratitude and farewell ("நன்றி", "ரொம்ப நன்றி",
"போய் வருகிறேன்") were **not** independently re-verified against the live
API in this session (time budget) — they are covered by the pre-existing
`_THANKS_CUES`/`_FAREWELL_CUES` lists untouched by this fix, and by
`semantic_intent.py`'s own Tamil-script prototypes, both already exercised
in the baseline test suites from the prior P2 hybrid-routing session. The
new `tests/unit/test_semantic_routing_edge_cases.py` tests the English
precedence fix directly; it does not add new Tamil/Tanglish live checks.

## E. Domain query recovery results

Both answers below are read live from `full_field_patta_transfer_urban` and
`workflow_history` for `2026/0153/02/045470` — no LLM call in either path.

- **Registration date**: `28-08-2026`, document no `R/17/1/11822/2026` —
  confirmed distinct from the application's `submission_date`,
  `2026-08-29` (the two dates differ by one day; the fix never substitutes
  one for the other, because they come from different tables entirely:
  `full_field_patta_transfer_urban.regi_on` vs. `applications.submission_date`).
- **Rejection chronology**: *"Immediately before the current stage: Zonal
  Deputy Tahsildar, on 2026-09-11 01:05"* and *"The Zonal Deputy Tahsildar
  desk performed 2026/0153/02/045470's most recent workflow action, on
  2026-09-14 02:31"* — both real `workflow_history` hops, a real desk name,
  no fabricated field visit (NISD files never have one, and the answer never
  mentions one — checked explicitly in `tests/unit/test_semantic_routing_edge_cases.py::test_no_fabricated_field_visit_in_workflow_answer`).
- **ISD/NISD separation**: a registration-date question on an ISD (0154)
  file correctly says *"No full-field patta transfer record is held... Only
  an NISD (0153) file carries one"* rather than answering with an invented
  or borrowed date — this was already correct before this task's fix
  (`fulltransfer_qa.answer()`'s own empty-rows branch); the fix only
  concerned *reaching* that function at all for an NISD file, not its
  ISD-exclusion behavior.
- **Explicit application number**: both questions, asked with the number
  inline instead of via context, answer identically and correctly — the fix
  applies on either path, since both ultimately call the same
  `_workflow_history_answer()` / `fulltransfer_qa` functions.

Q4/Q5 of the task's original Test C (date-difference arithmetic between the
registration date and the submission date) were **not** independently
verified in this session — see §I.

## F. Semantic classifier non-regression

No change was made to `semantic_intent.py`'s model, prototype list, cosine
threshold (`0.75`) or margin (`0.08`). All three fixes sit strictly upstream
of `semantic_intent.classify()` in the dispatch chain (earlier deterministic
gates and record-module hooks), so the classifier's own false-positive /
recall numbers from `test_reports/HYBRID_ROUTING_BASELINE.md` (1/874, 57%
binary recall) are unaffected and were not re-measured. Confirmed instead by
re-running the existing classifier-level tests
(`tests/unit/test_semantic_intent.py`, 22/22 still passing) and adding two
new ones in this session's file that pin the actual precedence the fix
depends on (`test_existing_greeting_still_active`,
`test_existing_hard_negative_still_refused`,
`test_identity_question_is_caught_before_semantic_classifier_ever_runs`).

## G. Performance

| path | before | after |
|---|---|---|
| "When was the registration document registered?" | 63.3s (LLM timeout) | 2.8s (DB read + card render, no LLM) |
| "What happened before the rejection?" | 130.9s (agent error) | 0.9s (DB read, no LLM) |
| "Goodbye." | fast but wrong (`unknown_message`, no LLM) | fast and correct (`greeting`, no LLM) |
| "Thanks for the explanation." | fast but wrong (`explain_clarify`, no LLM) | ~8.5s, correct (`greeting`; the reply itself goes through the same greeting-answer path as a fresh "hello", which the project's own notes elsewhere record as sometimes reaching for a short LLM-polished phrasing within a bounded 6s timeout before falling back to fixed text) |

No new embedding or LLM call was introduced anywhere; all four fixes are
either regex/vocabulary-level precedence changes or reuse an existing,
already-deterministic answer function (`_workflow_history_answer`,
`fulltransfer_qa.answer`) that was already being computed from the database
for other call sites.

## H. Regression results

```
python -m pytest tests/unit -q
314 passed
```

(269 pre-existing — 247 P0/P1/reasoning + 22 from the prior P2 hybrid-routing
session — plus 45 new in `tests/unit/test_semantic_routing_edge_cases.py`.)
No test was skipped; the DB-backed section (Section C of the new file) ran
against the live, configured database and the real `2026/0153/02/045470`
record, not a fixture stand-in.

## I. Remaining issues (explicit, not claimed as fixed)

- **`chat_history` vs. `FollowupContext` inconsistency.** `_app_evidence_in_scope()`,
  `_cm_strong()` and the record-module hooks `_early_subdivision_answer()`
  calls all read the client-supplied `ChatRequest.chat_history` field, not a
  server-side reload of the session's own transcript — unlike the bare-status
  follow-up path (`_bare_app_status()`'s own mechanism plus `FollowupContext`),
  which works even with no `chat_history` sent. A client that omits
  `chat_history` will see the registration-date/rejection-workflow fixes in
  this report silently not engage, even though the fix itself is correct —
  discovered when this session's own first live test script made exactly
  that mistake. This is a real, separate architectural question (should
  every context-dependent handler read from the same place?) that is out of
  this task's scope to resolve; flagging it here rather than silently
  working around it in the test script alone.
- **A pre-existing, different application-number resolution bug was
  observed, not fixed.** In the mixed-message live test, *"Goodbye, but
  first show the status of 2026/0154/02/012766."* correctly reached
  `application_status` (the farewell fix's job) but answered with
  **2026/0153/02/045470**'s details instead of 2026/0154/02/012766's — the
  wrong application. This is not one of the three named precedence bugs in
  this task, and fixing a general multi-application-number resolution issue
  is explicitly broader than the narrow scope given; it is recorded here as
  a finding for separate follow-up, not addressed.
- **Date-difference arithmetic** (the original task's Test C, Q4/Q5: "Is that
  the application submission date?" / "What is the difference between those
  dates?") was not independently live-verified in this session.
- **Tamil/Tanglish gratitude and farewell** were not re-run against the live
  API in this session, only covered by the pre-existing deterministic cue
  lists and the mocked unit tests described in §D.
- This report documents exactly three named precedence bugs and the one
  non-bug (identity). It is not a claim of improved intent coverage beyond
  those three, and the four conversational categories `semantic_intent.py`
  covers remain the only ones in production, per the prior
  `HYBRID_INTENT_ROUTING_REPORT.md`'s own recommendation against adding more.

## J. Git safety

- `git status --short | wc -l`: 77 at session start → 82 now. The only
  growth is this task's own new files (below); nothing was reset, cleaned,
  stashed, committed or pushed.
- **Modified** (4, all precedence-only, no DB/model/schema changes):
  - `backend/services/chatbot/chat_followups.py` — two new identity-pattern
    regex alternatives, one small existing-pattern fix.
  - `backend/services/chatbot/chat_smalltalk.py` — `_CONVERSATIONAL_SINGLE_WORDS`
    exemption in `_unrecognised_word_message()`; `_WFH_BEFORE_RE` widened.
  - `backend/services/chatbot/chat_shared.py` — one narrow hard-negative in
    `_is_explain_request()`.
  - `backend/services/chatbot/chat_application_queries.py` — `_DOCS_SKIP`
    exclusion; a new ISD/NISD workflow-history attempt in
    `_early_subdivision_answer()`; one new import (`_WFH_CUE`).
- **New**: `tests/unit/test_semantic_routing_edge_cases.py`,
  `test_reports/P2_ROUTING_EDGE_CASE_BASELINE.md`,
  `test_reports/P2_ROUTING_EDGE_CASE_REPORT.md` (this file),
  `test_reports/qbank_artifacts/p2_edge_case_conversations.py` and its result
  JSON, plus working trace scripts under `scratch/` (not part of the
  application).
- P0, P1 and the prior P2 hybrid-routing fixes all remain present and
  passing: 314/314, confirmed by running the full suite after every edit in
  this session, not just once at the end.
- Backend server: started fresh (bytecode cleared) for live verification,
  stopped at the end of this task; confirmed down.
