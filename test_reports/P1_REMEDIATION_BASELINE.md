# P1 Remediation — Baseline Snapshot

**Recorded:** 2026-11-21, before any P1 change was made.

## Repo state

- Branch: `main`
- HEAD: `eaff14e50b7162339dc458b630cf406a037efd1d`
- Working tree: 65 entries (31 modified tracked files, 34 untracked) — this already includes the three P0 application-code fixes (`orchestrator.py`, `appinfo_qa.py`, `chat_application_queries.py`) and the P0 regression test file, confirmed intact:
  - `_asks_application_number_format` present in `orchestrator.py` (3 occurrences — definition + 2 call sites)
  - the "automatically" removal comment present in `appinfo_qa.py`
  - the 0158 correction-modification fallback comment present in `chat_application_queries.py`
  - `tests/unit` full suite: **172 passed** (152 pre-existing + 20 P0 regression tests), confirmed before touching anything for this task.

## Existing implementation, as actually traced (not assumed)

- **Application-number extraction**: `extract_application_number()` / `extract_application_numbers()` (`chat_shared.py`) for a single/explicit set; `rag.parse_comparison_query()` independently extracts up to two numbers with its own regex (`\d{4}/\d{3,4}/\d{1,3}/\d+`) for the comparison shape specifically.
- **Multiple numbers in one message**: already has a real, working path -- `chat_dispatch.py`'s `intent == "application_status"` branch (~line 1855) calls `extract_application_numbers()`, and when it finds more than one, builds `structured_data["multi_applications"]` (a list of per-application detail dicts) via `get_application_detail()` for each. This is the mechanism "show details for A and B" already uses.
- **Two-application COMPARISON specifically**: `rag.parse_comparison_query()` already recognises `"compare A and B"` and returns `{"kind": "applications", "left": ..., "right": ...}` (line ~2992); `parse_intent()` already returns `"compare_applications"` for it (line ~4248); `chat_application_queries._build_comparison()` already has a dedicated `_side()` closure and verdict logic for `kind == "applications"` (age/duration/area/fee verdicts, fully implemented) -- **this machinery was already real and already worked when reached.**
- **Why it was never reached (the actual root cause, confirmed by direct testing, not assumed)**: `chat_shared._cm_strong(message, chat_history)` -- the gate `orchestrator.py` calls (line ~2410) to decide whether to force `intent = "application_status"` ahead of whatever `parse_intent()` returned -- claims a "compare A and B ... survey numbers, old patta, registration ..." message too, because it contains an application number and a keyword several single-application record modules (`fulltransfer_qa`'s own "compare old vs new owner" sense, in particular) recognise. `_cm_strong` returning `True` here **overrides** `parse_intent()`'s correct `"compare_applications"` result, forcing `"application_status"` instead -- and from there, `chat_application_queries._early_subdivision_answer()`'s single-application record hooks (which run before the `multi_applications` 2+-number path even gets a chance) claim the message using only the FIRST application number, because every one of those modules assumes exactly one application in view.
- **`FollowupContext`**: `backend/services/followup_context.py` (the module `chat_shared.py`/`orchestrator.py` import as `fctx`). `build_context(intent, structured_data, ...)` already has a generic branch (line ~415) that turns `structured_data["multi_applications"]` into `FollowupContext(entity=ENTITY_APPLICATION_LIST, application_numbers=[...], active_applications=[...])` carrying EVERY number named -- already the right mechanism, just never fed two numbers because `_build_comparison()`'s "applications" kind result never included a `multi_applications` key.
- **"The other one" follow-up**: `followup_context.resolve()` already has a real comparison-follow-up resolver, `_resolve_comparison()` (line ~3480), that understands ordinals ("the first and second"), "them"/"both", and an explicit second number -- but only when the message matches `_COMPARISON_TRIGGER_RE` (`compare`/`vs`/`versus`/`difference`/...). "What about the other one?" matches none of those words, so `_resolve_comparison()` always returned `None` for it, regardless of how many applications were active. Separately, `is_negated_ref()` (line ~2596) classifies "the other one" as a NEGATED reference (grouped with "not this one" / "wrong one" / "none of them") and -- when reached -- asks a clarifying question rather than resolving it, with no special case for "exactly two known applications, so there is nothing ambiguous about it".
- **Generic comparison dispatch (`calc_qa`)**: `calc_qa.compare_codes()` (line ~549) is claimed by the bare word "compare" (`_CMP_CODES` regex = `\bcompa?r\w*|...`) with **no requirement that the message itself name any service code** -- when the message's own text yields fewer than two codes, it falls back to a `codes_override` parameter the caller passes in from whatever was last discussed, with no check that the override is still relevant to what was just asked. `compare_codes_conceptual()` (line ~515) is narrower (requires "how is X different" phrasing) and was not implicated.

## Pre-existing, unrelated diffs in the files this task will touch

| File | Already modified before this task? | Overlaps this task's planned edits? |
|---|---|---|
| `backend/services/chatbot/chat_shared.py` | Yes (HQDT acronym addition, from earlier session work) | No -- different lines |
| `backend/services/chatbot/chat_application_queries.py` | Yes (the P0-3 cmflow fallback) | No -- different function (`_build_comparison`/`_side`/`_early_subdivision_answer`, not the P0-3 block) |
| `backend/services/chatbot/calc_qa.py` | No (clean, matches HEAD) | N/A |
| `backend/services/chatbot/chat_service_codes.py` | No (clean, matches HEAD) | N/A |
| `backend/services/followup_context.py` | Yes (large pre-existing diff, +597/-89 before this task even started -- this module already carries substantial uncommitted work from earlier sessions) | No overlapping lines -- confirmed by inspecting the diff hunks before editing; this task's additions are new functions appended near `_resolve_comparison()` and inside `resolve()`, not modifications of existing hunks |
| `backend/services/postgres.py` | Yes (an unrelated `get_workflow_remarks()` enrichment pass, already present) | No -- this task's first attempt to add a branch to `get_comparison()` was found to be dead code (never called for `kind == "applications"`, which is handled entirely in `chat_application_queries.py` instead) and was reverted in full; confirmed by `grep` that no trace remains |

## Existing test commands

- `.venv/Scripts/python.exe -m pytest tests/unit -q --no-header` -- 172 passed, confirmed immediately before any P1 edit.

No other baseline fact needed re-deriving; the P0 report's own baseline (git safety, credentials policy, etc.) still holds unchanged.
