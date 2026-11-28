# P3_1_PAIR_CONTEXT_REPORT — Pair Context Persistence, Restoration and Client-History Independence

**Date:** 2026-11-22 · **Branch:** `main`

## A. Executive summary

Both named bugs trace to the same root defect, plus one bug it was masking,
plus one more, newly-found, unrelated-but-blocking bug discovered only by
running the full Phase 6 sequence with `chat_history` genuinely absent.

- **Bug A** ("show both" shows only the focused application) and **Bug B**
  ("switch back to first" loses the pair) both came from
  `orchestrator.py`'s two `fctx.build_context()` call sites never passing
  `previous_context=`, so the pair in `active_applications` was silently
  dropped by every plain single-application answer. Fixed by passing
  `previous_context=_followup_ctx` at both call sites — the smallest
  possible change, since `build_context()`'s own carry-forward logic
  already existed and only needed to be given the data it was designed for.
- A second bug this exposed: `_resolve_pair_reference()` preferred
  `application_numbers` (which narrows to one entry by design) over
  `active_applications` (which now correctly carries the pair), so even a
  correctly-carried pair stayed invisible to the function literally
  written to restore it. Fixed by swapping the precedence.
- A third, latent bug this exposed: the single-application
  `build_context()` branch's `carried_active_apps or [single_num]`
  fallback would, once `carried_active_apps` started arriving non-empty,
  have kept returning the OLD pair for a genuinely new, unrelated
  application. Fixed alongside the first two.
- **Bug C** (newly found, not one of the two named in the task, but
  directly blocking "pair restoration works without client `chat_history`"
  for the "switch back to first" phrasing specifically): `_howto_turn()` in
  `chat_routing.py` short-circuited "first/second/... application"-shaped
  messages by checking **only the client-supplied `chat_history`** for an
  application number, concluding "nothing has been shown" whenever
  `chat_history` was empty or omitted — before the canonical, server-side
  context ever got a chance to answer. Fixed by standing aside when there
  is no client history to check, rather than treating that absence as
  proof nothing was shown.

All three are verified fixed, live, in all three required client-history
variants (normal, omitted, stale/conflicting).

## B. Exact structured-context lifecycle, before and after

(Read via `scratch/trace_p31_lifecycle.py`, which queries
`chat_messages.structured_data` after every turn against the live DB.)

**Before:**
```
Compare A,B          -> active_applications=[A,B]
Other one            -> active_applications=[A,B], current_application=B
What is its status?  -> active_applications=[B]            <-- pair narrowed to one
Show both again       -> answered with B's single card (compare_applications never reached)
Switch back to first  -> "Found 1 application(s)" (ambiguous/generic, pair not found)
```

**After:**
```
Compare A,B          -> active_applications=[A,B]
Other one            -> active_applications=[A,B], current_application=B
What is its status?  -> active_applications=[A,B]           <-- pair now preserved
Show both again       -> intent=compare_applications, both A and B rendered
Switch back to first  -> resolves to A (survey 110); active_applications=[A,B] still intact
```

## C. Changed files

- `backend/services/followup_context.py`:
  - Single-application branch of `build_context()`: `else [single_num]`
    instead of `else (carried_active_apps or [single_num])` (fixes the
    latent third bug).
  - `_resolve_pair_reference()`: prefers `active_applications` over
    `application_numbers` when the latter has lost the pair.
  - `_PAIR_BOTH_RE`: added `"display the two applications"` (the one
    required phrasing from the task's Phase 3 list that did not already
    match).
- `backend/services/chatbot/orchestrator.py`: `previous_context=_followup_ctx`
  added to both `fctx.build_context(intent, structured_data, ...)` call
  sites (non-streaming and streaming).
- `backend/services/chatbot/chat_routing.py`: `_howto_turn()`'s
  "first/second/... application" short-circuit now requires `chat_history`
  to be non-empty before concluding nothing was shown (Bug C).

`backend/services/followup_context.py`'s P3 fix (the single-explicit-number
`FOLLOWUP_NONE` guard added last session) was **not touched or weakened** —
confirmed by `tests/unit/test_canonical_conversation_context.py` still
passing unchanged, and by the live Test A/B sequence in the P3 report
remaining correct throughout this session's work.

## D. Pair persistence design

No new field was added. The task's three conceptual requirements
(`pair_applications`, `current_application`, `display_mode`) map onto two
`FollowupContext` fields that already existed:

| task's concept | existing field | status before this session | status after |
|---|---|---|---|
| original pair membership | `active_applications` | computed correctly on creation, dropped by every single-application answer | preserved across every intermediate single-application turn |
| pair ordering | `active_applications`'s list order | preserved (untouched) | preserved (untouched) |
| currently focused application | `current_application` | already worked correctly throughout | unchanged |
| "both" vs "single" display | implicit in how many numbers a `Resolution` carries | `_resolve_pair_reference()` already existed to produce the "both" shape, just couldn't see the pair | now reachable |

## E. Test results

```
python -m pytest tests/unit -q
350 passed
```

(332 pre-existing — 247 P0/P1/reasoning + 22 P2 hybrid-routing + 45 P2
routing-edge-case + 18 P3 canonical-context — plus 18 new in
`tests/unit/test_pair_context_persistence.py`.) Nothing skipped, nothing
blocked. The DB-backed sections ran against the live database with the
real `2026/0153/02/045470` / `2026/0153/02/045472` / `2026/0154/02/012766`
records.

## F. Live API transcripts and client-history comparison

`test_reports/qbank_artifacts/p3_1_pair_conversations.py`, full transcript
in `test_reports/qbank_artifacts/p3_1_pair_results.json`. Ran the task's
exact Phase 6 sequence (Q1-Q6) three times: with normal accumulated
`chat_history`, with it omitted entirely, and with a deliberately stale one
(mentioning `2026/0154/02/012766` and a non-existent `2026/0154/02/999999`,
never updated across turns).

| turn | WITH history | WITHOUT history | STALE history |
|---|---|---|---|
| Q1 Compare A, B | pair established (0.2-0.4s) | same | same, stale numbers never appear |
| Q2 What about the other one? | focuses B | same | same |
| Q3 What is its status? | "...045472 is: rejected" | same | same |
| Q4 **Show both applications again** | **compare_applications, both A and B** | **compare_applications, both A and B** | **compare_applications, both A and B** |
| Q5 **Switch back to the first** | followup_list, A / survey 110 | followup_list, A / survey 110 (after Bug C fix) | followup_list, A / survey 110 |
| Q6 What was its rejection reason? | generic `howto_answer` (see §I) | same | same |

**Identical across all three variants for Q1-Q5** — the fix is entirely
server-side and does not depend on, or get corrupted by, the client history
field. Neither `2026/0154/02/012766` nor the fabricated
`2026/0154/02/999999` ever appears in any answer in the stale-history run.

## G. Session-isolation results

`tests/unit/test_pair_context_persistence.py::TestPairIsolation`:

- A second, brand-new session for the same officer, which never names a
  pair itself, does not inherit the first session's pair — asking "Show
  both applications again." in a fresh session correctly declines rather
  than answering about another session's applications
  (`test_fresh_session_has_no_inherited_pair`,
  `test_second_session_cannot_inherit_first_sessions_pair`).
- A `FollowupContext` built for one officer fails `validate_officer()` for
  a different officer's ID
  (`test_pair_context_rejects_a_different_officers_validation`), confirming
  P3's existing officer-scoping on context is unaffected by these changes.
- `resolve_application_reference()`'s own jurisdiction check is untouched
  by any change in this session.

## H. Performance

No new database round trip was introduced for the main fix: `_followup_ctx`
was already loaded once per request (before this session's change, just
never passed where it was needed); passing it as an extra argument costs
nothing. The `_howto_turn()` fix (Bug C) removes a code path, it does not
add one. Live latencies in §F are all 0.1-0.4s, all deterministic (no LLM
call), consistent with the project's existing deterministic-path
performance.

## I. Remaining issues (explicit, not claimed as fixed)

- **"What was its rejection reason?" after "switch back to the first
  application" falls to a generic `howto_answer`** (common rejection
  reasons from the workflow guide, not `2026/0153/02/045470`'s own recorded
  rejection remark), in all three client-history variants. Traced far
  enough to identify the cause — `_howto_qa`'s rejection-reasons cue is
  claiming the message ahead of the per-application rejection-remark
  lookup, a precedence issue in a third module unrelated to pair-state
  persistence — but this is a **separate, third bug**, not one of the two
  named in this task's scope ("the two pair-context defects"), and fixing
  it was left out per the task's own "smallest safe change... do not
  undertake a broad redesign of unrelated handlers" instruction. Documented
  here rather than silently fixed or silently ignored.
- **Phase 4.C from the P3 task** (explicit application plus an unambiguous
  pair reference, e.g. "compare X with the first application from our
  earlier comparison") was still not independently exercised in this
  session, same as noted in the P3 report.
- No new `display_mode` field was added (see §D) — a deliberate scoping
  decision, since the existing `Resolution.application_numbers` length
  already distinguishes "both" from "single" for every renderer that reads
  it, and adding a redundant field would duplicate state the task itself
  asked not to duplicate.

## J. Git safety

- `git status --short | wc -l`: 88 at session start → 100 now. Growth is
  only this task's own new files; nothing was reset, cleaned, stashed,
  committed or pushed.
- **Modified** (3): `backend/services/followup_context.py`,
  `backend/services/chatbot/orchestrator.py`,
  `backend/services/chatbot/chat_routing.py` — all three changes are
  narrow, targeted, and documented above; no unrelated line in any of
  these files was touched.
- **New**: `tests/unit/test_pair_context_persistence.py`,
  `test_reports/P3_1_PAIR_CONTEXT_BASELINE.md`,
  `test_reports/P3_1_PAIR_CONTEXT_REPORT.md` (this file),
  `test_reports/qbank_artifacts/p3_1_pair_conversations.py` and its result
  JSON, plus trace scripts under `scratch/` (not part of the application).
- P0, P1, P2 and P3's explicit-application-number precedence fix all remain
  present and passing: 350/350, confirmed before and after every edit in
  this session.
- Backend server: started fresh (bytecode cleared) for live verification,
  stopped at the end of this task; confirmed down.
