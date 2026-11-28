# P3_CANONICAL_CONTEXT_REPORT — Explicit Application Resolution

**Date:** 2026-11-21 · **Branch:** `main`

## A. Executive summary

The chatbot never answers about the wrong application because it "prefers"
stale context over an explicit identifier — it answers about the wrong
application because, one layer before intent routing even runs,
`followup_context.classify()` rewrites the message itself, discarding the
officer's own explicit application number and substituting an older one.
The fix is a single, narrow early-return in that function: a message naming
exactly one explicit application number is never treated as a bare
follow-up fragment, so it reaches `parse_intent()` /
`resolve_application_reference()` unmodified — both of which were already
correct on their own, confirmed by calling them directly with the real
message before changing anything. Verified live, with **and** without the
client sending `chat_history`, that `"Goodbye, but first show the status of
2026/0154/02/012766."` now correctly answers about `2026/0154/02/012766`,
never `2026/0153/02/045470`, and that the next turn continues on the newly
named application.

## B. Existing architecture — why the split produced inconsistent results

Two context mechanisms coexist, as the P2 report already found:

1. **Server-side `FollowupContext`**, built from `chat_messages.structured_data`
   and reconstructed by `_load_followup_context()` on every turn —
   authoritative, session-scoped, works with no client history at all.
2. **Client-supplied `ChatRequest.chat_history`**, read directly by a
   separate set of functions (`_app_evidence_in_scope()`, `_cm_strong()`,
   the record-module hooks inside `_early_subdivision_answer()`).

The P3 task asked whether this split itself caused the 045470-vs-012766 bug.
Tracing the actual failure (§C) found it did not: the bug reproduces
identically whether or not `chat_history` is sent (confirmed live, §F),
because it happens in a THIRD place neither mechanism owns —
`followup_context.classify()` deciding, with no check of the current
message's own content, that the message was a bare fragment to resolve
against mechanism #1. The split documented in the P2 report still exists
after this fix (unifying it is a larger refactor than the actual root cause
required), but it is no longer the cause of this particular failure class.

## C. New architecture — the actual fix

```python
# backend/services/followup_context.py, top of classify()
if len(_APP_NUMBER_PATTERN_RE.findall(text)) == 1:
    return FOLLOWUP_NONE
```

Placed before the existing comparison-trigger check (which already handles
the two-explicit-number case by declining to treat it as a follow-up either)
and before every other follow-up-kind branch. `resolve()` then returns an
unresolved `Resolution()`, `_apply_followup_resolution()` is never called,
and the message reaches `parse_intent()` / `resolve_application_reference()`
exactly as the officer typed it. No other function was changed:
`extract_application_number()`, `parse_intent()` and
`resolve_application_reference()` were all confirmed correct in isolation
before this fix, so none of them needed to change.

## D. Resolution precedence (as implemented, confirmed by test)

1. **Exactly one explicit application number in the current message** →
   `classify()` returns `FOLLOWUP_NONE` immediately; `resolve_application_reference()`
   uses that number, independently re-validated for existence and
   jurisdiction, regardless of anything discussed earlier. Confirmed live
   (§F, Test A/B) and by `tests/unit/test_canonical_conversation_context.py::TestExplicitApplicationPrecedence`.
2. **Two explicit application numbers** → the existing comparison-trigger
   branch (unchanged) treats it as a comparison, not a follow-up; both
   numbers are extracted, validated independently, and handed to the
   existing `compare_applications` routing in their original order.
   Confirmed: `test_two_explicit_ids_still_produce_a_comparison`.
3. **Explicit number plus an unambiguous reference to a previous pair
   member** ("compare X with the first application from our earlier
   comparison") — handled by `_resolve_comparison()` /
   `_resolve_pair_reference()`, which run *before* `classify()` inside
   `resolve()` and are unaffected by this fix (not independently
   re-verified in this session — see §J).
4. **Current focused application in stored context, no explicit number in
   the message** → the pre-existing `FollowupContext.current_application` /
   pair-focus mechanism, untouched by this fix. Confirmed:
   `test_status_followup_after_focus_switch_uses_the_focused_application`.
5. **Unambiguous single application or pair member in context** →
   unchanged pre-existing behavior.
6. **Ambiguous / multiple candidates, no reliable reference** → unchanged
   pre-existing `ambiguous_context` / clarification verdicts in
   `resolve_application_reference()`.
7. **Invalid or inaccessible explicit identifier** → `resolve_application_reference()`'s
   existing refusal path (unchanged), confirmed to never fall back to a
   stale application: `2026/9999/99/999999` after discussing `045470`
   answers *"Application 2026/9999/99/999999 does not exist in the
   database..."*, never 045470's data (`scratch/trace_p3_invalid.py`,
   `test_invalid_explicit_id_does_not_fall_back_to_stale_application`).

## E. Live reproduction — before and after

Direct multi-turn pipeline call, real DB, real officer
(`scratch/trace_p3_full.py`):

**Before:**
```
Follow-up resolved: 'Goodbye, but first show the status of 2026/0154/02/012766.'
    -> 'What is its status 2026/0153/02/045470'
Q: Goodbye, but first show the status of 2026/0154/02/012766.
A: application_status -> The Status for 2026/0153/02/045470 is: rejected
```

**After:**
```
(no "Follow-up resolved" rewrite logged)
Q: Goodbye, but first show the status of 2026/0154/02/012766.
A: application_status -> The Status for 2026/0154/02/012766 is: approved
```

Next turn, same session, after the fix:
```
Q: When was that application submitted?
A: application_status -> The Submission Date for 2026/0154/02/012766 is: 2026-08-08
```

(`2026/0154/02/012766` never appears again as `2026/0153/02/045470` — the
switch is "sticky" for the rest of the conversation, not just the one turn.)

## F. Client-history variants (Phase 9, Test A / B)

Live API, real sessions, both run from a fresh login (`test_reports/qbank_artifacts/p3_context_conversations.py`, full transcript in `test_reports/qbank_artifacts/p3_context_results.json`):

| turn | WITH `chat_history` | WITHOUT `chat_history` |
|---|---|---|
| Investigate 045470 | 045470 details (0.4s) | 045470 details (0.3s) |
| What is its status? | "...045470 is: rejected" (0.5s) | "...045470 is: rejected" (0.3s) |
| **Goodbye, but first show the status of 012766.** | **"...012766 is: approved" (0.4s)** | **"...012766 is: approved" (0.3s)** |
| When was that application submitted? | "...012766 is: 2026-08-08" (0.4s) | "...012766 is: 2026-08-08" (0.4s) |

**Identical in both variants.** This confirms the fix sits upstream of the
client-history-vs-server-context split entirely — it corrects the message
before either mechanism reads it, so omitting `chat_history` changes nothing
about whether the bug reproduces. A "stale or conflicting client history"
variant (Phase 9 Test C) was not separately constructed beyond this, since
the WITHOUT-history run already proves client history plays no role in
either triggering or fixing this specific bug.

## G. Security verification (Phase 7)

- `fctx.resolve(message, context, language, officer_id=...)` already
  refuses a context built for a different officer
  (`context.validate_officer(officer_id)`), confirmed still enforced and
  unweakened: `test_resolve_rejects_context_from_a_different_officer`.
- Two independent sessions for the same officer do not share context: a
  brand-new session that has never named an application does not answer
  about an application discussed in a different session for the same
  officer: `test_two_independent_sessions_do_not_share_context`.
- `resolve_application_reference()`'s own jurisdiction check
  (`lookup_application_access`) is untouched by this fix — it still runs on
  every explicit or context-recovered application number, independent of
  whether the number came from the current message or from context.
- No JWT, password or Aadhaar value appears in the diagnostic trace scripts
  or their output.
- `_is_accessible()` in `reasoning_qa.py` (the P1-2 IDOR fix) was not
  touched in this session; its fail-closed behavior is unrelated to the
  bug fixed here (that bug was about WHICH application number reaches the
  gate, not about the gate's own authorization logic).

## H. Performance

No new database round trip was introduced. The fix is a single regex check
(`_APP_NUMBER_PATTERN_RE.findall(text)`, already compiled and already used
elsewhere in the same function) evaluated once per message, before any
database access happens at all. Live latencies above (0.2-0.5s per turn,
all deterministic — no LLM call on any of these turns) are consistent with
the project's existing deterministic-path performance and show no
regression from this change.

## I. Regression results

```
python -m pytest tests/unit -q
332 passed
```

(314 pre-existing — 247 P0/P1/reasoning + 22 P2 hybrid-routing + 45 P2
routing-edge-case — plus 18 new in
`tests/unit/test_canonical_conversation_context.py`.) Nothing skipped,
nothing blocked. The DB-backed sections ran against the live, configured
database with the real `2026/0153/02/045470` / `2026/0154/02/012766` /
`2026/0153/02/045472` records, not fixtures.

## J. Remaining issues (explicit, not claimed as fixed)

- **"Show both applications again." after focusing on one pair member
  returns only the focused application's card, not both.** Observed in
  both the direct DB-backed trace (`scratch/trace_p3_full2.py`) and the
  live API run (§F, Test D, both variants). This is **not** caused by this
  session's fix — the new `classify()` guard only fires on a message with
  exactly one explicit application number, and "Show both applications
  again." has zero — it is a pre-existing limitation in the pair-restoration
  mechanism, found while verifying Phase 5's "must not regress" requirement,
  and left unfixed because it is a different, broader bug than the one this
  task's root-cause analysis actually found.
- **"Switch back to the first application." loses pair membership when
  `chat_history` is omitted**, after an intermediate turn
  ("Show both applications again.") whose own structured answer apparently
  carries only the single currently-focused application forward, not the
  original pair. With `chat_history` sent, the same sequence resolves via a
  `followup_list` answer (also not literally naming "the first application"
  explicitly in the response text, though it does not claim an error);
  without it, the server-side-only path answers
  *"Nothing has been shown in this conversation yet."* — a real
  degradation, not present in the original bug report, found only by
  running the required without-`chat_history` variant. Documented here
  rather than silently fixed, since it is outside the three Phase 4 cases
  (A/B/D) this task's acceptance criteria actually describe and the
  specific pair/focus mechanics are explicitly "not to be redesigned" by
  this task.
- **Phase 4.C** ("explicit application plus a previous pair reference",
  e.g. "compare X with the first application from our earlier comparison")
  was not independently live-verified in this session; it is handled by
  `_resolve_comparison()` / `_resolve_pair_reference()`, both unchanged by
  this fix and both running before `classify()` in `resolve()`'s own
  control flow, but no live or DB-backed test specifically exercises that
  exact three-way combination here.
- **No new "canonical context-loading helper" function was built.** Tracing
  the actual code (per the task's own "do not assume" instruction) found
  `_load_followup_context()` already serves that role for every handler
  that calls `fctx.resolve()`, including with no client `chat_history` at
  all (§F proves this). Building a parallel helper would have duplicated
  working code rather than fixed the actual, narrower defect found. This is
  a deliberate scoping decision, stated here rather than silently
  substituted for what the task described.
- **The two-context-mechanism split documented in the P2 report still
  exists.** `_app_evidence_in_scope()` / `_cm_strong()` / the record-module
  hooks still read client-supplied `chat_history` directly. This fix makes
  that split harmless for THIS bug (the message reaching those functions is
  now never corrupted), but it does not unify the two mechanisms into one;
  that remains a larger, separate architectural question, as the P2 report
  already flagged.

## K. Git safety

- `git status --short | wc -l`: 82 at session start → 88 now. Growth is
  only this task's own new files; nothing was reset, cleaned, stashed,
  committed or pushed.
- **Modified** (1): `backend/services/followup_context.py` — one
  early-return added to `classify()` (~15 lines including the explanatory
  comment); nothing else in the file was touched.
- **New**: `tests/unit/test_canonical_conversation_context.py`,
  `test_reports/P3_CONTEXT_BASELINE.md`,
  `test_reports/P3_CANONICAL_CONTEXT_REPORT.md` (this file),
  `test_reports/qbank_artifacts/p3_context_conversations.py` and its result
  JSON, plus trace scripts under `scratch/` (not part of the application).
- P0, P1 and both P2 fixes all remain present and passing: 332/332,
  confirmed before and after the single edit in this session.
- Backend server: started fresh (bytecode cleared) for live verification,
  stopped at the end of this task; confirmed down.
