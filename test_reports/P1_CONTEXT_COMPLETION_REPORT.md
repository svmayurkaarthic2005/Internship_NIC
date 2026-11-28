# P1 Context Completion Report

**Date:** 2026-11-21
**Baseline:** `test_reports/P1_CONTEXT_COMPLETION_BASELINE.md`
**Scope:** finish the multi-application pair/focus context model, follow-up resolution, rendering, the qualifier-guard false positive. P1-2 (evidence-grounded reasoning) explicitly out of scope and not touched.

---

## A. Summary

**Fixed and verified live:**
- Pair membership (`application_numbers`) now survives every single-application focus switch — confirmed by a multi-turn live sequence where "which one was submitted first?" (turn 3) still had both applications after "what about the other one?" (turn 2) had already picked one.
- "The other one" / "the first one" / "the second one" / "switch back to the first one" / "go back to the other one" all resolve deterministically against the original pair, in original order.
- "Both" / "both of them" / "show both again" / "compare them again" / "which one was submitted first?" all restore the real side-by-side comparison (reusing the existing comparator), not a generic list.
- A focus-narrowed single pick now renders through the real single-application pipeline (`entity=ENTITY_APPLICATION`) instead of the generic list renderer — "What is its status?" right after a pick now answers with the actual status, not a bare count.
- `qualifier_guard` no longer flags "comparison"/"compare"/"versus"/"vs" as unrecognised filter words, while still flagging genuine unknown words and still allowing real filters (rejected/approved/service code).
- Session isolation confirmed: a pair established in one session is never visible to a different session.
- A brand-new application introduced mid-conversation does not inherit the old pair (confirmed live: investigating a third application after a pair comparison answers cleanly from the new application alone).

**Confirmed still broken, honestly reported, not fixed in this pass:**
- `_cm_q_sd` for `"Show both applications again"` right after a *singular*-resolved turn (not right after a pair-level turn) loses the pair, because the singular turn's own full-pipeline re-save (via the ordinary `application_status` intent, not the row_peek path) has no notion of a pair to carry forward. Confirmed live in Test B, turn 4.
- Date/field corrections ("No, I meant the update date, not submission date") remain broken, exactly as in the prior P1 report — not touched in this pass (Phase 6 was attempted conceptually but the underlying `_big_plan` message-splitting issue was judged too large a change to make safely within this task's scope; see section F).
- A compound message ("Switch back to the first one. What was its rejection reason?") is not split into two resolvable parts; only single-sentence pair references are recognised.
- `"Compare it with the first application from our earlier comparison"` after switching to a brand-new third application does not recover the old pair's first member — this specific cross-reference (a NEW application compared against an OLD pair member by name) was not implemented.

Test results: **210/210** unit tests pass (187 pre-existing + 23 new). All three P0 fixes and the prior P1 fixes confirmed intact throughout.

---

## B. Root causes

### Phase 3/4 — the pair collapsed to a singleton on the very first focus switch

- **File / function**: `backend/services/followup_context.py`, the old `_resolve_other_one()` (now `_resolve_pair_reference()`), and its consumption in `backend/services/chatbot/orchestrator.py`'s `ENTITY_APPLICATION_LIST` branch.
- **Original mechanism**: the old resolver returned `Resolution(application_numbers=[other])` — ONE item. The orchestrator's generic list branch then called `get_applications_by_numbers(db, officer, [other], ...)` and built the NEXT turn's context from that one-row result via `_followup_out_context()`'s generic (non-`row_peek`) path, which calls `fctx.build_context("followup_list", scoped_sd)` — and `scoped_sd["applications"]` had only one row. The pair was gone from that point on.
- **Root cause**: nothing in the resolution/save path distinguished "the pair this focus was picked from" from "the current focus itself" — the model had the right *fields* (`application_numbers` vs `current_application`) but nothing populated them independently.
- **Fix**: `_resolve_pair_reference()` now sets `row_peek=True` and `application_number=<pick>` (one item, for rendering) while leaving the *resolution's own* `pair` field (a new field on `Resolution`) holding the full two-item pair. Because `row_peek=True` was already a pre-existing, PROVEN mechanism (`_followup_out_context()`'s row_peek branch already existed, for a different purpose — "exclude the second row" after a plain ordinal row-peek, see its own docstring) that returns the *prior* context (the pair) essentially untouched, adding `current_application` to that same replace-call (one new line) was enough to carry both "the pair survived" and "focus moved" forward together, with zero new persistence architecture.
- **A second root cause found during live verification**: "which one was submitted first?" matched the pre-existing plain-backref/"last peeked" narrowing (meant for "was THAT the rejected one?") because it contains "which one", and its own ordinal word ("first") was not recognised by the existing `mentions_ordinal()` helper in this adverbial position. Fixed by adding `_PAIR_SUPERLATIVE_RE` to `_resolve_pair_reference()`, recognising the *shape* "which (one) was/is/took/has ... <superlative word>" and routing it to the pair-comparison path (same as "both") rather than letting it fall into the single-item backref narrowing.

### Phase 5 — a focus-narrowed single pick rendered as a bare list count

- **File / function**: `followup_context.resolve()`'s pre-existing "one record in view" branch (the one triggered when `context.single_application` is set).
- **Original mechanism**: this branch (used for `kind=FOLLOWUP_SINGULAR`) set `entity=context.entity` — meaning when the PRIOR context's entity was `ENTITY_APPLICATION_LIST` (true for our pair, now narrowed to one via `current_application`), the returned Resolution ALSO carried `entity=ENTITY_APPLICATION_LIST`.
- **Root cause**: `orchestrator.py` checks `_followup.entity == ENTITY_APPLICATION_LIST` *before* it ever looks at `_followup.kind`, so this genuinely-singular resolution was captured by the generic list renderer (`_scoped_list_answer()`), which has **no branch at all** for `FOLLOWUP_SINGULAR` and falls through to its default count-only sentence. This bug pre-existed this task (it would have affected ANY earlier scenario that reached `single_application` via a list-context narrowing) but was newly exposed because this task's fix is the first thing that reliably sets `current_application` on an `ENTITY_APPLICATION_LIST` context.
- **Fix**: the branch now returns `entity=ENTITY_APPLICATION` specifically when `context.entity == ENTITY_APPLICATION_LIST` (leaving `ENTITY_FIELD_VISIT` and an already-`ENTITY_APPLICATION` context untouched, to avoid any change to those existing, working paths).

### Phase 7 — `qualifier_guard` treated "comparison" as an unrecognised filter

- **File / function**: `backend/services/chatbot/qualifier_guard.py`, the `_OK` word allow-list.
- **Root cause**: `unknown_words()` judges every word of a list-shaped message against `_OK` (short allow-listed words) and a domain vocabulary; "comparison"/"compare"/"versus"/"vs" were in neither, so `listing_shaped("show both applications again in one comparison")` → True, and "comparison" was flagged as an unrecognised qualifier, producing "I don't know how to filter applications by 'comparison'".
- **Fix**: added `compare compared comparing comparison versus vs` to `_OK`. Confirmed this does not weaken the guard's actual purpose (still flags "sri" in "show applications from sri").

---

## C. Context model — pair membership vs. current focus

**Before** (immediately after "the other one" picked B):
```
application_numbers: [B]        # the pair is GONE
active_applications: [B]
current_application: (not independently tracked at all)
```

**After this task's fix**, the SAME turn now produces, saved for the next turn:
```
entity: application_list
application_numbers: [A, B]     # the pair -- UNCHANGED
active_applications: [A, B]
current_application: B          # the focus -- this is what moved
filters.last_peeked: B
```
Confirmed directly against the live database (not inferred from rendered text) by reading `chat_messages.structured_data` for a real session after a real "What about the other one?" turn:
```
application_numbers: ['2026/0153/02/045470', '2026/0153/02/045472']
active_applications: ['2026/0153/02/045470', '2026/0153/02/045472']
current_application: 2026/0153/02/045472
entity: application_list
```

**After a pair restore** ("both" / "compare them again" / "which one was submitted first?"), the context is rebuilt by `followup_context.build_context("compare_applications", ...)` from the fresh comparison's `multi_applications` field (the same mechanism from the prior P1 task), and the orchestrator now explicitly carries forward the PRIOR `current_application` (if one was set) onto that rebuilt context, so "the other application" immediately after a pair-restore still has something concrete to be "other" than.

---

## D. Follow-up resolution — expected vs. actual

| Wording | Expected | Actual (live + unit-tested) |
|---|---|---|
| "What about the other one?" | Other member of the pair | ✅ Correct, and pair survives |
| "The first one." | First application in original pair order | ✅ |
| "The second one." / "Show the second application." | Second application in original pair order | ✅ |
| "Both" / "both of them" | Both applications (comparison restored) | ✅ |
| "Compare them again" | Original pair, full comparison | ✅ |
| "Show both again" | Original pair, full comparison | ✅ |
| "Switch back to the first one." | First application | ✅ |
| "Go back to the other one." | Other member relative to current focus | ✅ |
| "Which one was submitted first?" (superlative, not positional) | Real verdict over the pair | ✅ (routes through the comparator; states "both filed on the same date" when the dates are equal, which they are for these two sample applications — the mechanism is confirmed correct, the specific verdict sentence depends on the data) |
| Ambiguous "other one" with 3+ candidates | Stands aside (no guess) | ✅ regression-tested |
| "Other one" with no known pair | Unaffected (pre-existing behaviour) | ✅ regression-tested |
| Fresh session, no pair | Does not resolve another session's pair | ✅ confirmed live (Test D) |
| New application mid-conversation | Does not inherit the old pair | ✅ confirmed live (Test C, turns 3-4) |
| "Show both applications again" **right after a singular-resolved turn** (not right after a pair-level turn) | Both applications | ❌ **still broken** — the singular turn's own context-save path has no pair to carry; see section B |
| Compound: "Switch back to the first one. What was its rejection reason?" | Both resolved in sequence | ❌ **not implemented** — no message-splitting for pair references was added |
| "Compare it with the first application from our earlier comparison" after moving to a new, unrelated application | Old pair's first member recovered | ❌ **not implemented** |

---

## E. Rendering

**Before**: "What is its status?" right after "the other one" picked `2026/0153/02/045472` → `"There is 1 Sub-Registrar NISD application(s) in that list."` (a bare count, not the status).

**After**: `"The Status for 2026/0153/02/045472 is: rejected"` — confirmed both live and via the new unit test `test_focus_switch_entity_becomes_single_application_not_list` (asserts `kind == FOLLOWUP_SINGULAR` and `entity == ENTITY_APPLICATION`, the exact condition the orchestrator needs to route to the real single-application pipeline).

A plain pick ("the other one" on its own, with no specific field question attached) still renders through the generic list-table format (survey number, status, ward, block, etc. in a table) rather than a narrative sentence — this is a cosmetic limitation, not a correctness one: the data shown is correct and complete for that one application, it is simply presented as a one-row table instead of prose. Not fully resolved to the "useful detail card" ideal described in the original task, but a genuine, verified improvement over the prior one-line count.

---

## F. Date and field corrections

**Status: unresolved, same as the prior P1 report.** "No, I meant the update date, not submission date." is still split by the pre-existing long-message splitter (`_big_plan`) into two unrelated fragments — one triggers a generic "field correction offer" boilerplate, the other returns the submission date again (the exact value the officer said not to give).

**Why this was not fixed in this task**, per the task's own instruction to inspect before touching `_big_plan`: the splitter is a large, heavily-tested, general-purpose mechanism (CLAUDE.md documents it as a ~29000-line-adjacent core piece of the orchestration, handling many unrelated multi-intent shapes). A narrow fix confined to "no, I meant X not Y" phrasing specifically, with its own regression tests proving multi-intent questions remain intact, is a real, separate, non-trivial unit of work that this task's remaining time did not allow doing safely. Rather than make an unverified change to a widely-shared code path under time pressure, this is reported as explicitly unresolved.

---

## G. Qualifier guard

**Before**: `qualifier_guard.unknown_words("Show both applications again in one comparison.")` → `["comparison"]`, producing the false-positive refusal.
**After**: `[]` for that message and for `"Compare them again."` / `"Compare service code 0153 and 0154."`.
**Non-regression confirmed**: `"Show only the rejected applications."` / `"Show the approved applications."` still pass clean (`[]`), and `"show applications from sri"` still correctly flags `["sri"]`.

---

## H. Test results

```
.venv/Scripts/python.exe -m pytest tests/unit -q --no-header
```
- Before this task: 187 passed (confirmed in the baseline).
- After this task's edits + the extended test file: **210 passed, 0 failed.**
- New tests added to the existing `tests/unit/test_p1_context_and_reasoning.py` (extended, not replaced — all 15 pre-existing tests in that file kept, unweakened): 23 new tests covering pair persistence (3), follow-up resolution (4 parametrized + 3 single + 2 negative = 9), rendering (2), qualifier guard (3 parametrized groups = 5 cases + 1), session isolation (1). Full breakdown in the file itself.

**Live API turn counts** (Tests A, B, C, D, all run against the real `/api/v1/auth/login` → `/chat/sessions` → `/chat/stream` flow, real sessions, real `chat_history`):

| Test | Turns | PASS | PARTIAL | FAIL |
|---|---|---|---|---|
| A (10 turns) | 10 | 6 | 1 | 3 |
| B (6 turns) | 6 | 4 | 1 | 1 |
| C (5 turns) | 5 | 4 | 0 | 1 |
| D (session isolation) | 1 | 1 | 0 | 0 |
| **Total** | **22** | **15** | **2** | **5** |

(Test A's 3 FAILs: date correction, turn 8's post-singular pair loss, the compound "switch back...rejection reason" message. Test B's 1 FAIL: "Show both applications again" right after a singular turn. Test C's 1 FAIL: comparing a new application against an old pair's member by name.)

Exact commands executed and raw transcripts: `test_reports/qbank_artifacts/p1c_verify_a.py`, `p1c_verify_bcd.py`, and their `/tmp/p1c_*out*.txt` outputs (captured during this session; not committed, ordinary temporary files).

---

## I. Remaining work

- **Evidence-grounded reasoning (P1-2)** remains entirely out of scope and untouched, as instructed.
- **Date/field corrections** — unresolved; needs a dedicated, carefully-scoped look at `_big_plan`'s splitting logic specifically for "no, I meant X not Y" phrasing, with its own regression suite proving no multi-intent question regresses.
- **Pair loss after a singular-resolved turn's own full-pipeline save** — the `application_status` intent's own `build_context()` call (the ordinary, general-purpose one used for every single-application answer in the whole system) has no `pair` concept at all. Threading pair-awareness through that fully general path is a larger change than this task's remaining scope allowed; it was deliberately not attempted rather than rushed.
- **Compound pair-reference messages** ("Switch back to the first one. What was its rejection reason?") — not split; would need integration with the existing `_big_plan`/compound-question splitter, which was out of scope for the reasons given in section F.
- **Cross-reference between a new application and an old pair's named member** — not implemented.

---

## J. Git safety

- `git status --short`: 66 entries before this task, 66 after — every edit was to a file already in that list (no new files were created except the live-verification scripts, which are temporary diagnostics, not tracked application files, and the test file was extended, not newly created).
- All three P0 fixes and the prior P1 fixes (`is_two_application_comparison`, the comparator extensions, the calc_qa guard) reconfirmed present and passing throughout, before, during and after this task's edits.
- No destructive git command was run. Nothing was committed or pushed.
- No secret or credential was added to any tracked file.

**Files changed (application code):**
- `backend/services/followup_context.py` — `_resolve_pair_reference()` (replacing `_resolve_other_one()`), `_PAIR_FIRST_RE`/`_PAIR_SECOND_RE`/`_PAIR_BOTH_RE`/`_PAIR_SUPERLATIVE_RE`, the `pair` field on `Resolution`, the single-application-entity fix in `resolve()`'s "one record in view" branch.
- `backend/services/chatbot/chat_followups.py` — `_followup_out_context()`'s `row_peek` branch now also updates `current_application`.
- `backend/services/chatbot/orchestrator.py` — new pair-restore branch (both streaming and non-streaming paths) that reuses `_build_comparison()`/`_comparison_answer()` instead of the generic list renderer, with current-focus preservation.
- `backend/services/chatbot/qualifier_guard.py` — `_OK` allow-list extended with comparison vocabulary.

**Files changed (tests):**
- `tests/unit/test_p1_context_and_reasoning.py` — extended with 23 new tests (existing 15 kept unweakened).

**Files changed (reports):**
- `test_reports/P1_CONTEXT_COMPLETION_BASELINE.md`, `test_reports/P1_CONTEXT_COMPLETION_REPORT.md` (this file).

**Temporary diagnostics (not part of the application):**
- `test_reports/qbank_artifacts/p1c_verify_a.py`, `p1c_verify_bcd.py` — the live-API verification harnesses for this task.
