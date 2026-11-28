# P3_2_REJECTION_REASON_REPORT — Application-Specific Rejection Reason Precedence

**Date:** 2026-11-22 · **Branch:** `main`

## 1. Executive summary

"What was its rejection reason?" was answered by generic workflow guidance,
and separately by a non-existent application-information column, because
two independent handlers — `howto_qa` and `appinfo_qa` — each claimed the
question before the per-application answer could reach the data that
already existed to answer it correctly. Fixed both precedence gaps with
narrow regex exclusions and one new handler that reuses an existing,
already-correct function (`get_workflow_remarks()`). Verified live that the
real recorded remark for `2026/0153/02/045470` — *"[N/06]//NO LINK from RH
வேதராமன் மகன் பாலசுப்பிரமணியன்/ N-Parent /Linked Documents are not
submitted"*, dated `2026-09-13` — is now returned correctly after the full
P3.1 switch-back sequence, in both the normal and no-client-history
variants. One genuine, narrower limitation was found in the deliberately
adversarial stale-history variant and is documented in §14 rather than
fixed, since it sits in the shared application-resolution gate, not in
anything this task's two named precedence bugs touch.

## 2. Root cause of generic-handler precedence

Two independent claimants, found by reading the code (not guessed):

1. **`howto_qa.match()`**'s `rejection_reasons` topic regex,
   `\b(?:rejection\s+reasons?|reasons?\s+for\s+rejection)\b`, matches the
   bare substring "rejection reason" inside "What was **its** rejection
   reason?" with no distinction between a pronoun-anchored, one-application
   question and a genuinely generic, plural one. `howto_qa` is checked very
   early in the turn (`orchestrator.py` ~line 1234), well before any
   per-application resolution runs.
2. **`appinfo_qa.parse()`**'s field map, `if "reject" in ws and ("reason"
   in ws or "code" in ws): add(REJECT)`, mapped the tokens "reject"+"reason"
   to `reject_reason_code` — a column that does not exist in
   `full_field_patta_transfer_application_information` for an NISD file
   (confirmed live, before this fix: *"Reject reason code: the NISD
   application-information record has no such column"*). This claim
   happens inside `_early_subdivision_answer_inner()`, further down the
   same dispatch chain this task's prior sessions already worked in.

## 3. Actual authoritative source table and field

Found by reading the real record (`scratch/trace_p32_rejection_source.py`),
not guessed:

```
Application row: 2026/0153/02/045470  status=rejected  stage=REJECTED

workflow_history rows (3), in order:
  from=None     to=SIS         rejection_reason=None
  from=SIS      to=TAHSILDAR   rejection_reason=None
  from=TAHSILDAR to=REJECTED   rejection_reason='[N/06]//NO LINK from RH வேதராமன் மகன்
                                 பாலசுப்பிரமணியன்/ N-Parent /Linked Documents are not
                                 submitted'   performed_at=2026-09-13 21:01:39 UTC
```

**Table:** `workflow_history`. **Field:** `rejection_reason` (identical text
mirrored into `remarks` on the same row). **Which row:** the one whose
`to_stage == 'REJECTED'` — the closing hop; the two earlier intake hops
carry no reason at all. **Already correctly selected** by
`get_workflow_remarks()` in `postgres.py` (line ~4708:
`elif status == "rejected" and to == "REJECTED" and remark: rec["reason"]
= remark`) — this function already existed, already applies
`get_jurisdiction_filter()` (fails closed), and was already being called
for a two-application *comparison*'s `rejection_remark` field
(`chat_application_queries.py:2600`). The only gap was that nothing called
it for a **single** application.

## 4. Before/after routing flow

```
BEFORE:
  "What was its rejection reason?"
    -> howto_qa.match() claims "rejection_reasons" (bare substring match)
    -> generic workflow-guide text, no application named, no DB read

  (with the howto claim hypothetically bypassed)
    -> appinfo_qa.parse() adds REJECT key ("reject"+"reason" tokens)
    -> renders reject_reason_code from full_field_patta_transfer_application_information
    -> "the NISD application-information record has no such column"

AFTER:
  "What was its rejection reason?"
    -> howto_qa.match(): _SPECIFIC_APP_POINTER ("its") present,
       _GENERIC_REJECTION_CUE absent -> `continue`, topic not claimed -> None
    -> _early_subdivision_answer(): _rejection_reason_answer() matches
       _REJECTION_REASON_CUE, resolves app via the existing gate_app /
       context chain, calls get_workflow_remarks(db, officer, [app])
    -> "Recorded rejection remark for 2026/0153/02/045470 (2026-09-13):
       [N/06]//NO LINK from RH ... Linked Documents are not submitted"
```

## 5. Explicit-vs-contextual application resolution

`_rejection_reason_answer()` resolves the application the same way every
other single-application handler in this file already does —
`extract_application_number(message) or gate_app or
_unheld_qa.context_app(chat_history)` — and was **not given any new
resolution logic**. `gate_app` is the P3/P3.1-fixed canonical application
number for the turn; confirmed by the live transcript (§7) that the
handler correctly received `2026/0153/02/045470` — the application P3.1's
"switch back to the first application" turn had just focused — with no
change to `followup_context.py` or `orchestrator.py` in this session.

## 6. Application-specific rejection transcript (live)

Full P3.1 sequence, `test_reports/qbank_artifacts/p3_2_rejection_conversations.py`,
transcript in `test_reports/qbank_artifacts/p3_2_rejection_results.json`.

| turn | WITH chat_history | WITHOUT chat_history |
|---|---|---|
| Q1 Compare A, B | pair established | same |
| Q2 Other one | focuses B | same |
| Q3 Status | "...045472 is: rejected" | same |
| Q4 Show both | both A and B | same |
| Q5 Switch to first | resolves A | same |
| **Q6 Rejection reason** | **"Recorded rejection remark for 2026/0153/02/045470 (2026-09-13): [N/06]//NO LINK from RH...Linked Documents are not submitted"** (2.6s) | **identical** (0.5s) |

Identical in both variants — the fix does not depend on client history.

## 7. General rejection-guidance transcript (live)

```
Q: What are the common reasons applications get rejected?
A (0.1s, intent=howto_answer): Common rejection reasons (workflow guide):
   1) incomplete documents ... 2) area mismatch ... [unchanged generic list]
```

Unaffected — `"common"` matches `_GENERIC_REJECTION_CUE`, and there is no
`_SPECIFIC_APP_POINTER` in the message, so `howto_qa` still claims it
exactly as before this session's change.

## 8. Missing/inapplicable remark behaviour

- **Not rejected (live):** `Investigate 2026/0154/02/012766` (approved) then
  `"Why was this application rejected?"` → *"None of those applications
  was rejected, so there is no rejection reason."* This is answered by a
  **pre-existing** `workflow_remarks`-intent handler (not the new
  `_rejection_reason_answer()`, which never gets a turn here because that
  earlier handler claims it first) — confirmed correct behavior either way,
  and `_rejection_reason_answer()`'s own not-rejected guard was still
  verified directly:
  `tests/unit/test_application_rejection_reason.py::TestMissingOrInapplicableLive::test_direct_call_declines_for_a_non_rejected_application`.
- **No recorded remark:** verified only by unit test (no live application in
  this dataset is rejected with a genuinely blank `workflow_history`
  remark) — `_rejection_reason_answer()`'s own code path for this case
  (`if not reason: return "... no rejection remark is recorded ..."`) was
  not independently exercised against a real such record this session.
- **Inaccessible explicit application:** `Investigate 2026/0153/02/045470`
  then `"Why was 2026/9999/99/999999 rejected?"` correctly refuses with the
  identifier the officer typed and never mentions 045470's remark
  (`tests/unit/.../test_inaccessible_explicit_application_does_not_fall_back`).

## 9. Client-history comparison

See §6 for the primary WITH/WITHOUT comparison (identical). A third,
deliberately adversarial variant was also run live: a client that **never
updates** its `chat_history` and resends the same stale, conflicting pair
(`2026/0154/02/012766` / `2026/0154/02/999999`) on every single turn,
including Q6. See §14 — this is the one variant where Q6 answered about
the wrong (stale) application, and it is a pre-existing, broader gap, not
introduced or fixed by this session.

## 10. Authorization and jurisdiction results

`_rejection_reason_answer()` adds no new authorization logic — it calls
`get_applications_by_numbers()` (for the not-rejected/status check, same as
every other handler in this file) and `get_workflow_remarks()` (which
already applies `get_jurisdiction_filter()` and fails closed on an empty
filter), both pre-existing, both unchanged. Confirmed live and by test that
an inaccessible explicit application refuses correctly and never falls back
to a previously-authorized one.

## 11. Test results

```
python -m pytest tests/unit -q
376 passed
```

(350 pre-existing — 247 P0/P1/reasoning + 22 P2 hybrid-routing + 45 P2
routing-edge-case + 18 P3 canonical-context + 18 P3.1 pair-persistence —
plus 26 new in `tests/unit/test_application_rejection_reason.py`.) Nothing
skipped, nothing blocked.

## 12. Performance measurements

No new database query type was introduced — `_rejection_reason_answer()`
reuses `get_applications_by_numbers()` and `get_workflow_remarks()`, both
already in the codebase and already indexed/filtered the same way every
other per-application handler uses them. Live latencies: 0.1-2.6s per turn
(the one 2.6s turn was the first live call in a freshly-started server
process; the identical question answered in 0.5s on the WITHOUT-history
run moments later). No LLM call on any of these turns.

## 13. Files changed

- `backend/services/chatbot/howto_qa.py`: `_SPECIFIC_APP_POINTER` /
  `_GENERIC_REJECTION_CUE` regexes, and one new `continue` in `match()`'s
  topic loop (mirroring the existing `field_visit`/`_FV_FIELD` pattern
  already in the same function).
- `backend/services/chatbot/chat_application_queries.py`:
  `_REJECTION_REASON_CUE` regex, the new `_rejection_reason_answer()`
  function, and its call site in `_early_subdivision_answer()` (right
  after `_who_decided()`, before `_ip_comparison_answer()` — i.e. before
  `_early_subdivision_answer_inner()` ever reaches `appinfo_qa`'s claim).

**Not touched this session:** `backend/services/followup_context.py`,
`backend/services/chatbot/orchestrator.py` — P3's explicit-ID precedence
and P3.1's pair-persistence fix are both unchanged and unweakened,
confirmed by every test from both of those sessions still passing.

## 14. Remaining unverified / out-of-scope issues

- **A client that never updates `chat_history` and resends a fixed, stale,
  conflicting pair on every turn** causes Q6 to answer about the stale
  pair's application instead of the correct, server-side-focused one
  (confirmed live: answered about `2026/0154/02/012766` instead of
  `2026/0153/02/045470` after the identical switch-back sequence that
  works correctly with normal or omitted history). Traced far enough to
  locate it: `resolve_application_reference()`'s own context-recovery
  fallback (`_app_numbers_in_recent_context()` /
  `_extract_app_number_from_context()`) scans the **client-supplied**
  `chat_history` for an application number when the current message names
  none, and can find one there even when it conflicts with the canonical,
  server-side `FollowupContext`'s own current focus. This is the same
  "two competing context mechanisms" architecture the P2/P3 reports
  already named, surfacing in a genuinely new way (a message with **no**
  explicit identifier at all, so P3's explicit-ID-wins fix does not apply)
  — **not** a defect in `_rejection_reason_answer()`, `howto_qa`, or
  anything else touched this session, and explicitly out of this task's
  "narrowly focused... do not reopen P3/P3.1 context resolution" scope.
  Documented here, not fixed.
- **No live example of a rejected application with a genuinely blank
  `workflow_history` remark** exists in the current dataset, so
  `_rejection_reason_answer()`'s "no remark recorded" branch was verified
  only by direct unit-level reasoning about the code path, not against a
  real such record.
- **Multiple genuinely distinct rejection-related workflow entries**
  (the task's Phase 2 question 4) — this dataset's 3-hop chain has only one
  hop that ever carries a `rejection_reason`; chronology display beyond
  picking that one closing hop was not exercised against a file with more
  than one candidate.
- Several of the task's exact example phrasings ("What remark was recorded
  when it was rejected?", "What reason did the officer provide?") were
  verified via the `_REJECTION_REASON_CUE` regex match (unit test) rather
  than individually against the live API.

## 15. Git safety

- `git status --short | wc -l`: 100 at session start → 106 now. Growth is
  only this task's own new files; nothing was reset, cleaned, stashed,
  committed or pushed.
- **Modified** (2): `backend/services/chatbot/howto_qa.py`,
  `backend/services/chatbot/chat_application_queries.py`. Both changes are
  narrow additions (new regexes, one new function, one new call site); no
  unrelated line in either file was touched.
- **New**: `tests/unit/test_application_rejection_reason.py`,
  `test_reports/P3_2_REJECTION_REASON_BASELINE.md`,
  `test_reports/P3_2_REJECTION_REASON_REPORT.md` (this file),
  `test_reports/qbank_artifacts/p3_2_rejection_conversations.py` and its
  result JSON, plus trace scripts under `scratch/` (not part of the
  application).
- P0, P1, P2, P3 and P3.1 all remain present and passing: 376/376,
  confirmed before and after every edit in this session.
- Backend server: started fresh (bytecode cleared) for live verification,
  stopped at the end of this task; confirmed down.
