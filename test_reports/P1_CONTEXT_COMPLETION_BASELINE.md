# P1 Context Completion — Baseline Snapshot

**Recorded:** 2026-11-21, before any edit for this task.

## Repo state

- Branch: `main`, HEAD: `eaff14e50b7162339dc458b630cf406a037efd1d`
- `git status --short`: **66 entries** (matches the prior P1 report exactly — nothing has changed since).
- All three P0 fixes confirmed present (`_asks_application_number_format` in `orchestrator.py`, the "automatically" removal in `appinfo_qa.py`, the 0158 cmflow fallback in `chat_application_queries.py`).
- All prior P1 work confirmed present: `is_two_application_comparison()` (2 call sites), `_resolve_other_one()` in `followup_context.py`, `_CMP_NOT_CODES`/`_CMP_HAS_CODE_SIGNAL` in `calc_qa.py`.
- `tests/unit`: **187 passed**, confirmed before touching anything.
- `backend/services/chatbot/qualifier_guard.py` already carries one small, unrelated pre-existing diff (adds `"am"` to an allow-list word set, line 29) — confirmed it does not overlap the region this task needs to change.

## Current comparison-context implementation (traced, not assumed)

- `followup_context.FollowupContext` already has both `application_numbers` (the full set) and `active_applications` (same set, used by `_resolve_comparison`/`_resolve_other_one`) but **no separate "current focus" field that survives independently of the list** — `current_application` exists but nothing in the prior P1 work's `_resolve_other_one()` *writes* a new `FollowupContext` with both the full pair AND an updated `current_application`; it returns a `Resolution(application_numbers=[other])` with only ONE number, which `orchestrator.py`'s `ENTITY_APPLICATION_LIST` branch (line ~2076) then re-queries via `get_applications_by_numbers(db, officer, _followup.application_numbers, ...)` — a single-element list — and the structured data that comes back, and whatever `FollowupContext` gets built FROM that structured data for the NEXT turn, only ever contains that one application. This is the exact mechanism of the "pair collapses to a singleton" bug: the resolution step itself throws the other pair member away by only forwarding the resolved one.
- `qualifier_guard.py`: `listing_shaped()` / the unknown-qualifier detector reads a short list-shaped message's content words against an allow-list (`_OK`) and a noun pattern (`_NOUN`); "comparison" is apparently not in `_OK` and gets read as an unrecognised qualifier word, producing "I don't know how to filter applications by 'comparison'". Confirmed by inspection in Phase 2 below, before editing.

## Unresolved issues, confirmed still present

All of these were documented in `P1_REMEDIATION_REPORT.md` section H and are confirmed still present at this baseline (not re-verified live again here, to avoid duplicating that evidence — this task's own Phase 9 live run re-confirms the starting point as part of its "before" column):
- pair-to-singleton collapse after "the other one"
- weak single-line rendering for a pair-member pick
- date-field corrections broken
- `qualifier_guard` comparison false-positive

## Existing test command

`.venv/Scripts/python.exe -m pytest tests/unit -q --no-header` → 187 passed.
