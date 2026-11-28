# P3_1_PAIR_CONTEXT_BASELINE — Pair Context Persistence, Restoration and Client-History Independence

**Date:** 2026-11-22 · **Branch:** `main` · **HEAD at session start:** `eaff14e50b7162339dc458b630cf406a037efd1d`
**`git status --short | wc -l` at session start:** 88 (the P3 session's own end
state, unchanged). `python -m pytest tests/unit -q` → **332/332 passing**
before any edit in this session, reconfirmed rather than assumed.

## Exact point pair membership is lost (found by reading `chat_messages.structured_data` after every turn, not assumed)

Instrumented script: `scratch/trace_p31_lifecycle.py` — runs the real
`process_chat()` pipeline against the live DB and prints the latest
assistant `chat_messages.structured_data` row after every turn.

```
Turn 1  "Compare A and B."            -> active_applications=[A,B], current_application=B (last peeked)
Turn 2  "What about the other one?"   -> active_applications=[A,B], current_application=B   -- correct
Turn 3  "What is its status?"         -> active_applications=[B]     <-- PAIR LOST HERE
                                          application_numbers=[B]
                                          entity="application" (singular)
Turn 4  "Show both applications again." -> answered with B's single card only (intent=application_status)
Turn 5  "Switch back to the first application." -> "Found 1 application(s)" (ambiguous, generic)
```

**Root cause #1 (where the pair is actually lost):** `orchestrator.py`
builds the next turn's `FollowupContext` with
`fctx.build_context(intent, structured_data, fallback_app_number=...)` at
two call sites (non-streaming and streaming) — **neither ever passed
`previous_context=`**, so `build_context()`'s own `carried_active_apps`
logic (which already exists specifically to carry an active pair forward:
`carried_active_apps = list(previous_context.active_applications) if
previous_context and previous_context.active_applications else []`) always
saw `previous_context=None` and therefore always computed
`carried_active_apps=[]`. A plain single-application answer like "What is
its status?" then built its context with `active_applications=[single_num]`
only — not because the single-application branch's own logic was wrong
(line ~574 already said "keep the pair if the current application is a
member of it"), but because it was never given the pair to check against in
the first place.

**Root cause #2 (a second, smaller bug the first one had been masking):**
even with the pair now correctly carried into `active_applications`,
`_resolve_pair_reference()` — the function literally written to resolve
"show both again" / "switch back to the first" — read
`context.application_numbers or context.active_applications`, and
`application_numbers` is the field that DOES get narrowed to one entry by a
single-application answer (by `__post_init__`'s own design, calling it the
"legacy" field). Since `application_numbers` was non-empty (one entry), the
`or` never reached `active_applications`, so even a correctly-carried pair
in `active_applications` was invisible to this function.

**A latent third issue, exposed by fixing #1 (fixed alongside it):** the
single-application branch's own line,
`active_apps = carried_active_apps if (...) else (carried_active_apps or
[single_num])`, had an `or` in the `else` branch that — once
`carried_active_apps` actually started arriving non-empty — would have kept
returning the OLD pair even for a genuinely new, unrelated single
application, instead of starting fresh with just that one. Not reachable
before root cause #1 was fixed (since `carried_active_apps` was always
`[]`), but it would have become reachable, and wrong, the moment #1 was
fixed without also fixing this.

## Phase lifecycle, traced end to end (10 steps the task asked for)

1. **Initial comparison creates pair A/B** — `build_context()`'s
   `compare_applications` branch (not shown above, already correct):
   `active_applications=[A,B]`.
2. **Pair is persisted** — written to `chat_messages.structured_data` by
   `save_chat_messages()`, confirmed present in the DB row.
3. **"Other one" changes focus to B** — `_resolve_pair_reference()`'s
   `_PAIR_OTHER_RE` branch (already correct before this session): returns
   `current_application=B`, pair unchanged.
4. **Single-application status query runs** — `application_status`
   intent, answers from `get_application_detail()` for B.
5. **Assistant response is persisted** — via
   `fctx.build_context(intent, structured_data, ...)` — **this is where
   root cause #1 lived**.
6. **Pair state is reconstructed on the next turn** — `_load_followup_context()`
   deserializes the just-saved JSON; before the fix, the pair was already
   gone from the JSON itself, so there was nothing to reconstruct.
7. **"Show both applications again" is interpreted** — `classify()`
   returned `aggregate` (not a real aggregate question, just unmatched by
   any more specific rule) because `_resolve_pair_reference()` had already
   declined (root cause #2) and the comparison-trigger regex doesn't match
   "show both" (no "compare"/"versus" word).
8. **Both applications are retrieved and rendered** — never reached before
   the fix; after it, `_resolve_pair_reference()`'s existing
   `FOLLOWUP_COMPARISON` / `application_numbers=[A,B]` resolution renders
   the existing `compare_applications` output.
9. **Pair state is persisted again** — the `compare_applications` intent's
   own `build_context()` branch already preserves the pair correctly
   (unaffected by this session's changes).
10. **"Switch back to first" is resolved** — `_resolve_pair_reference()`'s
    `_PAIR_OTHER_RE`/ordinal branch, now reachable because step 7-8 restored
    the pair.

## Scope decision

The task's Phase 2 data model (`pair_applications`, `current_application`,
`display_mode`) describes three concepts. Tracing the actual code found two
of them already exist and already work correctly once the pair reaches
them: `active_applications` (the pair) and `current_application` (the
focus) are both real `FollowupContext` fields, already synchronized by
`__post_init__`, already read by `_resolve_pair_reference()`. No
`display_mode` field exists or is needed — "both" vs "single" is already
implicit in how many application numbers a given turn's `Resolution`
carries forward (`FOLLOWUP_COMPARISON` with two vs a singular pick with
one), and the existing `compare_applications` / `application_status`
renderers already know how to show two records or one. No new field was
added.

Full fix, before/after proof, live results and remaining issues:
`test_reports/P3_1_PAIR_CONTEXT_REPORT.md`.
