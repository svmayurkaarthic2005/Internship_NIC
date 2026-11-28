# P2_ROUTING_EDGE_CASE_BASELINE — Semantic Routing Edge Cases and Domain Query Recovery

**Date:** 2026-11-21 · **Branch:** `main` · **HEAD at session start:** `eaff14e50b7162339dc458b630cf406a037efd1d`
**`git status --short | wc -l` at session start:** 77 (the 67-entry baseline from the P1-2
session plus the P2 hybrid-routing task's own additions: modified
`backend/config.py` / `backend/services/chatbot/semantic_intent.py`, and new
`tests/unit/test_semantic_intent.py`, `test_reports/HYBRID_ROUTING_BASELINE.md`,
`test_reports/HYBRID_INTENT_ROUTING_REPORT.md`, `test_reports/HYBRID_INTENT_ROUTING_RESULTS.csv`,
`test_reports/qbank_artifacts/p2_live_conversations.py` and its result JSON). No
destructive git command was run to reach this state; it is exactly where the
prior session left off.

## Confirming prior fixes remain intact

- `python -m pytest tests/unit -q` → **269/269 passing** before any edit in
  this session (247 P0/P1/reasoning baseline + 22 from the P2 hybrid-routing
  task).
- Spot-checked: `backend/services/chatbot/semantic_intent.py` still has its
  `SEMANTIC_INTENT_MODE` shadow branch; `backend/services/chatbot/reasoning_qa.py`
  still exists and is still imported by `chat_application_queries.py` /
  `chat_corrections.py` / `orchestrator.py`.

## This task's premise, checked against the current tree

The task's bullet list ("874 real SIS question-bank lines evaluated... one
semantic false positive: 'who am i'... two actual pipeline failures: 'Thanks
for the explanation.' and 'Goodbye.'... two domain questions fell into
general_query") matches `test_reports/HYBRID_ROUTING_BASELINE.md` and
`test_reports/HYBRID_INTENT_ROUTING_REPORT.md` exactly — those are this
session's own prior findings, re-stated as this task's starting point. Fresh
inspection (not assumed) of the current code, done before any fix in this
session, found:

| claimed failure | still reproduces in the current tree? |
|---|---|
| "who am i" → smalltalk | **No** — `_is_officer_identity_question()` in `chat_followups.py` (pattern `\bwho\s+am\s+i\b`) already catches it, and runs at `orchestrator.py:2335`/`5280`, well before `semantic_intent.classify()` is ever reached at `orchestrator.py:2631`/`5523`. Confirmed by calling the function directly: `_is_officer_identity_question("who am i") == True`. The baseline report's "false positive" was a measurement artifact of calling `semantic_intent.classify()` in isolation (bypassing every gate ahead of it), not a reproducible pipeline bug. |
| "Thanks for the explanation." → `explain_clarify`, wrong fallback | **Yes**, reproduced: `parse_intent()` itself returns `general_query` (correct), but `_is_explain_request()` in `chat_shared.py` treats the bare word `"explanation"` (one of its literal trigger phrases) as a request to re-explain, before `semantic_intent.classify()` ever runs. |
| "Goodbye." → `unknown_message` (gibberish) | **Yes**, reproduced: `chat_smalltalk._unrecognised_word_message()` flags any single ASCII word ≥6 letters not in its "known words" set (SIS documents + vocabulary only, no ordinary English) as keyboard-mash; "Goodbye" (7 letters) has near-zero document-trigram overlap and was flagged, while "Bye" (3 letters) passed only because it is under the length floor. This runs inside `chat_pipeline.py`'s `_unknown_message_kind()` EarlyReply gate, *before* `parse_intent()` is even called — so `parse_intent`'s own correct token-based greeting rule (which already lists `"goodbye"` in `_bye_words`) never got the message. |
| Registration-date / rejection-workflow questions fall to `general_query` and the LLM | **Yes**, reproduced via a direct DB-backed call to `_early_subdivision_answer()`: "When was the registration document registered?" returned `'No document attachment is recorded for 2026/0153/02/045470.'` (claimed by `_attached_documents()`'s `\bdocu?\w*` cue, which has no concept of "the registration document" as a `full_field_patta_transfer_urban` field vs. a chat-uploaded file); "What happened before the rejection?" returned `None` (no handler claimed it — the ISD/NISD workflow-history answerer `_workflow_history_answer()` was never tried for a context-resolved single application on this path, only `_other_code_workflow()`, which explicitly excludes 0153/0154). |

## Database facts verified live (not assumed) before fixing anything

`2026/0153/02/045470` (NISD, Rejected):
- `full_field_patta_transfer_urban`: registration document no `R/17/1/11822/2026`, registered on `28-08-2026` — **confirmed to differ from** the application's `submission_date` of `2026-08-29` (one day later), matching the task's stated expectation exactly.
- `workflow_history`: the hop immediately before the file's current (terminal) stage was performed by the **Zonal Deputy Tahsildar** desk, on `2026-09-11 01:05`; the most recent/closing action was also a Zonal Deputy Tahsildar action, on `2026-09-14 02:31`.

## Architecture trace (Phase 2), summarized

```
chat_pipeline.py: _unknown_message_kind()         <- EarlyReply, before parse_intent at all
        |  (gibberish / help / vague-all / bare-ref / forecast)
        v
rag.py: parse_intent()                            <- token-based greeting/farewell rule lives here
        |
orchestrator.py: _is_explain_request() / _is_capability_question() / _is_out_of_scope() /
                 _special_scope_kind() / attachment_qa.plan_answer() / _cm_strong-based
                 override (needs _app_evidence_in_scope == True, which reads the CLIENT-
                 SUPPLIED `chat_history` array, not the DB)
        |  (general_query, not already claimed)
        v
orchestrator.py: _early_subdivision_answer() -> _attached_documents() / _other_code_workflow() /
                 ... -> _early_subdivision_answer_inner() (the record-module dispatch chain)
        |  (not claimed)
        v
semantic_intent.classify()                        <- only reached if intent is STILL general_query
        |  (abstains)
        v
agent / RAG / LLM fallback
```

A genuinely separate finding surfaced while tracing this (not one of the
three named bugs, recorded for the final report's "remaining issues"
section): **two different context mechanisms coexist.** The server-side
`FollowupContext` (written to `chat_messages.structured_data`) answers a bare
"What is its status?" correctly even when the client sends no history at
all. But `_app_evidence_in_scope()` / `_cm_strong()` / the record-module
hooks that `_early_subdivision_answer()` calls all read the **client-supplied**
`chat_history` field (`ChatRequest.chat_history` in `backend/routers/chat.py`,
explicitly documented there as "Previous chat messages from sessionStorage" —
*not* reloaded from the database by the server on its own). A client that
omits it (as this session's own first test script did) sees every
context-dependent fix in this task silently fail to apply, even though the
fix is correct — confirmed by re-running with `chat_history` properly
accumulated and sent, after which every fix worked as expected.

## Scope of this session's fixes

Three narrow, precedence-only fixes, none of which touch
`semantic_intent.py`'s model, prototypes, threshold or margin:

1. `backend/services/chatbot/chat_followups.py` — two new `_IDENTITY_PATTERNS`
   regex alternatives (`which officer account is active/logged in/current`,
   `who is logged in`, `logged in as`) and a small fix to the existing
   `\bmy\s+identity\b` pattern so `"my current identity"` also matches.
2. `backend/services/chatbot/chat_smalltalk.py` — a `_CONVERSATIONAL_SINGLE_WORDS`
   exemption (reusing the existing `_THANKS_CUES`/`_FAREWELL_CUES` lists) added
   to `_unrecognised_word_message()`'s "known words" set, and `_WFH_BEFORE_RE`
   widened to also match "before the rejection/approval/closure/completion"
   (naming the terminal outcome, not just literally "the current stage").
3. `backend/services/chatbot/chat_shared.py` — a narrow hard-negative in
   `_is_explain_request()` for "thanks (for) the explanation" with no real
   question word in the same message.
4. `backend/services/chatbot/chat_application_queries.py` — `_DOCS_SKIP`
   excludes "registration document/doc"; `_early_subdivision_answer()` now
   tries `_workflow_history_answer()` (the ISD/NISD workflow answer) for a
   context-resolved 0153/0154 application before falling through to
   `_other_code_workflow()` (which still correctly excludes them).

Full detail, before/after traces and live API results: `test_reports/P2_ROUTING_EDGE_CASE_REPORT.md`.
