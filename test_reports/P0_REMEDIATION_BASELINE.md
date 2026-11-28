# P0 Remediation — Baseline Snapshot

**Recorded:** 2026-11-21, before any P0 fix was applied.

## Repo state

- Branch: `main`
- HEAD: `eaff14e50b7162339dc458b630cf406a037efd1d`
- Working tree: 30 modified tracked files, 34 untracked files (64 total per `git status --short`) — **unchanged from the state at the start of the question-bank test run**; nothing has been committed, stashed, reset or discarded since.

## Pre-existing changes in the three target files

All three of P0-1's and P0-3's target files already carry **unrelated, pre-existing uncommitted edits** from earlier work this session (an "HQDT" acronym/full-form addition, and the `names_a_file()` attachment-continuity fix). None of these pre-existing hunks overlap the lines this task needs to change:

| File | Pre-existing diff | Overlaps target lines? |
|---|---|---|
| `backend/services/chatbot/chat_shared.py` | +2/-1: adds `hqdt` to `_FULLFORMS`/`_KNOWN_ACRONYMS` (lines ~1580-1591) | No — target is line 2798 (dispatch `or`-chain), untouched |
| `backend/services/chatbot/fulltransfer_qa.py` | +1/-1: adds `"hqdt": "zdt"` to `_EXACT` (line 42) | No — target is lines 201-205 (SISREC claim), untouched |
| `backend/services/chatbot/orchestrator.py` | +50/-16 across 5 hunks (pre-image lines 961, 1709, 2569, 4726, 5404, 6666): the `names_a_file()` attachment-continuity exemption and related `_cm_blocked` wiring | No — confirmed by direct grep of the live file (see below), the format-guard conditions are still at **lines 2148 and 5088** in the current working tree, outside any pre-existing hunk |
| `backend/services/chatbot/appinfo_qa.py` | **clean, matches HEAD exactly** | N/A — no pre-existing changes to preserve |

Confirmed by direct inspection of the live file (not assumed from the diff):
```
$ grep -n '"format"' backend/services/chatbot/orchestrator.py
2148:            and any(w in _msg_lower_fmt for w in ["format", "look like", "structure", "structured", "pattern", "வடிவம்", "அமைப்பு"])
5088:            and any(w in _msg_lower_fmt for w in ["format", "look like", "structure", "structured", "pattern", "வடிவம்", "அமைப்பு"])
```
(Line 5088, not 5086 as the test report estimated before this session's own earlier +18-line edit to the streaming path shifted it down two lines — the bug itself is identical, just two lines lower.)

## Current status of the three bugs (re-confirmed against the live file, pre-fix)

- **P0-1** (`orchestrator.py:2148`, `:5088`): condition still present, unpatched. `"format" in "...owner information..."` still evaluates `True`.
- **P0-2** (`appinfo_qa.py:50`, `:178-179`): `_EXACT["automatically"] = "automutated"` still present, unpatched.
- **P0-3** (`chat_shared.py:2798`, `fulltransfer_qa.py:201-205`): `fulltransfer_qa.claims_strong()` still ahead of `cmflow_qa.parse()` in the dispatch `or`-chain at `chat_shared.py:2798`; `fulltransfer_qa.py`'s SISREC claim at lines 201-205 still has no service-code check.

## Existing test commands relevant to this work

- `.venv/Scripts/python.exe -m pytest tests/unit -q --no-header` — the real (DB-backed, no-mock) unit suite from the prior session's work (152 tests passing as of the last recorded run).
- No existing unit test file targets `orchestrator.py`'s format guard, `appinfo_qa.py`'s field parser, or `fulltransfer_qa.py`'s SISREC claim specifically — new targeted tests are needed (Phase 6).
- Live-pipeline verification in the prior session used a direct HTTP harness (`test_reports/qbank_artifacts/qbank_harness.py`) against `POST /api/v1/auth/login` → `POST /api/v1/chat/sessions` → `POST /api/v1/chat/stream`; the same approach will be reused here for Phase 5.

## Plan for this task

Smallest possible targeted patches only, scoped to:
1. `orchestrator.py` lines 2148 and 5088 (and the small `if` blocks around them) — word-boundary match instead of substring.
2. `appinfo_qa.py` line 50 (`_EXACT` entry removal) — no other line needs to change.
3. `chat_shared.py` line 2798 / `fulltransfer_qa.py` lines 201-205 — add a service-code-aware guard to the SISREC claim rather than reordering the whole dispatch chain (per the task's own preference for "the smallest safe change").

No other file will be touched. No destructive git operations will be run.
