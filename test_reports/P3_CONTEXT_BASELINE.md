# P3_CONTEXT_BASELINE — Canonical Server-Side Conversation Context

**Date:** 2026-11-21 · **Branch:** `main` · **HEAD at session start:** `eaff14e50b7162339dc458b630cf406a037efd1d`
**`git status --short | wc -l` at session start:** 82 (the P2 routing-edge-case
session's own end state — unchanged, no destructive command run to reach it).

## Confirming prior fixes remain intact

`python -m pytest tests/unit -q` → **332/332 passing** at the very end of this
session (314 pre-existing + 18 new). Before any edit, the baseline was
re-confirmed at **314/314** (not assumed unchanged, per the task's own
instruction) — matching the number the P2 routing-edge-case report recorded.

## The reported bug, reproduced against the live database (not assumed)

Ran the real multi-turn `process_chat()` pipeline (`scratch/trace_p3_full.py`)
against a live DB session, the real officer `sperumal_chn@sis.tn.gov.in`, and
the real applications `2026/0153/02/045470` / `2026/0154/02/012766`:

```
Q: Investigate 2026/0153/02/045470.
A: Here are the details for 2026/0153/02/045470. ...

Q: What is its status?
A: The Status for 2026/0153/02/045470 is: rejected

Q: Goodbye, but first show the status of 2026/0154/02/012766.
   [log] Follow-up resolved: 'Goodbye, but first show the status of 2026/0154/02/012766.'
         -> 'What is its status 2026/0153/02/045470'
A: The Status for 2026/0153/02/045470 is: rejected          <-- WRONG application
```

Confirmed exactly as the task describes.

## Isolating where the corruption actually happens

The obvious suspects were checked individually, against the real database,
and all three were already correct on their own:

- `extract_application_number("Goodbye, but first show the status of 2026/0154/02/012766.")`
  → `'2026/0154/02/012766'` (correct).
- `parse_intent(...)` on the same message → `'application_status'` (correct).
- `resolve_application_reference(db, message, chat_history, officer, 'application_status', session_id)`
  called directly with the real message and the real two-turn history →
  `{'verdict': 'ok', 'app_number': '2026/0154/02/012766', 'from_context': False}`
  (correct).

The corruption happens **before** any of these three ever see the real
message. The server log's own line gives it away:

```
Follow-up resolved: 'Goodbye, but first show the status of 2026/0154/02/012766.'
    -> 'What is its status 2026/0153/02/045470'
```

`orchestrator.py`'s `_apply_followup_resolution()` is called whenever
`followup_context.classify(message)` decides the message is a bare follow-up
fragment pointing at a stored `FollowupContext`. It does — the function never
checked whether the current message already carries its own explicit
application number before making that decision — so it rewrote the message,
substituting the OLD application number for the officer's own, explicit,
different one, and discarding the "Goodbye, but first..." framing entirely.
Everything downstream (`parse_intent`, `resolve_application_reference`, the
handlers) then correctly and faithfully answers the *rewritten*, wrong
question.

## Architecture trace (Phase 2), both context mechanisms

```
orchestrator.py: process_chat()
   |
   v
_load_followup_context()  <- server-side FollowupContext, from chat_messages.structured_data,
   |                          reconstructed EVERY TURN regardless of whether the client sent
   |                          chat_history at all (confirmed: Test B below, with chat_history
   |                          omitted entirely, resolves identically to Test A)
   v
fctx.resolve(_original_message, _followup_ctx, language)   <- decides the message is a "follow-up"
   |     (THE BUG: never checked for the message's own explicit application number)
   v
_apply_followup_resolution()   <- rewrites `message` using the stored context's number
   |     (THE DAMAGE: the officer's own explicit number is gone by this point)
   v
parse_intent(message) / resolve_application_reference(...)   <- both correct, but now
   |                                                              operating on the wrong text
   v
handler -> answer about the WRONG application
```

A second, separate mechanism (documented already in
`test_reports/P2_ROUTING_EDGE_CASE_REPORT.md`'s "remaining issues") reads the
**client-supplied** `ChatRequest.chat_history` field directly:
`_app_evidence_in_scope()`, `_cm_strong()` and the record-module hooks inside
`_early_subdivision_answer()`. This mechanism is unaffected by the bug above
(it operates on the rewritten message too, downstream) and is unaffected by
this session's fix (which corrects the message before either mechanism acts
on it).

## Scope decision for Phase 3

Phase 3 describes building a new "canonical context-loading helper." Tracing
the actual code first (per the task's own instruction not to assume) found
that `_load_followup_context()` already plays that role for every handler
that calls `fctx.resolve()` — it already validates session ownership, already
reconstructs `FollowupContext` from `chat_messages.structured_data`, already
works correctly with no client `chat_history` at all (that is: Test B below
passes identically to Test A, before any fix other than the one-line
`classify()` change). The actual defect was narrower than "no canonical
context exists" — it was "one function decides too eagerly, and with no
check at all for the current message's own identifier, that a message is a
bare fragment." Building a brand-new helper would not have fixed this bug
and would have duplicated working code; the fix applied instead (§ below)
closes the actual gap found.

Full fix, before/after proof, live results and remaining issues:
`test_reports/P3_CANONICAL_CONTEXT_REPORT.md`.
