# P3_2_REJECTION_REASON_BASELINE — Application-Specific Rejection Reason

**Date:** 2026-11-22 · **Branch:** `main` · **HEAD at session start:** `eaff14e50b7162339dc458b630cf406a037efd1d`
**`git status --short | wc -l` at session start:** 100 (the P3.1 session's own
end state, unchanged). `python -m pytest tests/unit -q` → **350/350 passing**
before any edit this session, reconfirmed rather than assumed.

## The authoritative source, found by reading the real record (not guessed)

`scratch/trace_p32_rejection_source.py` queries `Application` and
`WorkflowHistory` directly for `2026/0153/02/045470`:

```
Application row: 2026/0153/02/045470 rejected REJECTED
{'from_stage': None,   'to_stage': 'SIS',       'rejection_reason': None, ...}
{'from_stage': 'SIS',  'to_stage': 'TAHSILDAR', 'rejection_reason': None, ...}
{'from_stage': 'TAHSILDAR', 'to_stage': 'REJECTED', 'remarks': '[N/06]//NO LINK from RH வேதராமன் மகன் பாலசுப்பிரமணியன்/ N-Parent /Linked Documents are not submitted',
 'rejection_reason': '[N/06]//NO LINK from RH வேதராமன் மகன் பாலசுப்பிரமணியன்/ N-Parent /Linked Documents are not submitted',
 'performed_at': 2026-09-13 21:01:39 UTC}
```

**Answers to Phase 2's six questions:**

1. Source table: `workflow_history` (not an `appinfo`/application-information
   table — see below for why that matters).
2. Relevant event: the hop whose `to_stage == 'REJECTED'` — the terminal,
   closing transition, not the earlier `SIS -> TAHSILDAR` intake hops (which
   carry no `rejection_reason` at all).
3. Field: `rejection_reason` (mirrored into `remarks` on this same row —
   both hold the identical text for the closing hop).
4. Multiple entries exist (3 hops for this file); only the one closing with
   `to_stage == 'REJECTED'` is the right one. Already correctly selected by
   the existing `get_workflow_remarks()` in `postgres.py`
   (`elif status == "rejected" and to == "REJECTED" and remark: rec["reason"] = remark`).
5. Timestamps exist (`performed_at`); the closing hop's date,
   `2026-09-13`, is the one to show alongside the remark.
6. Accessible to the authenticated officer — `get_workflow_remarks()`
   already applies `get_jurisdiction_filter()` and fails closed (confirmed
   by reading the function; not re-derived).
7. When no remark exists: the register fact is "rejected, but no reason
   recorded" — never substituted with generic guidance.

**`get_workflow_remarks()` already exists and already does this correctly**
(`backend/services/postgres.py:4672`) — it is the function the P1-era
comparison answer (`chat_application_queries.py:2600`) already calls to
build a `rejection_remark` field for a two-application comparison. No new
database query, table or model was needed; the only gap was that nothing
called this existing function for a **single** application's rejection
question.

## What was actually intercepting the question instead

Two separate, independent claimants, found by reading the code (not
assumed):

1. **`howto_qa.match()`**'s `rejection_reasons` topic regex,
   `\b(?:rejection\s+reasons?|reasons?\s+for\s+rejection)\b`, matches the
   substring "rejection reason" inside "What was **its rejection reason**?"
   with no check for whether the question is about ONE application
   (pronoun "its") or applications in general (plural, "common"). This
   runs very early in the turn (`orchestrator.py` line ~1234, well before
   the application-context chain), exactly as the P3.1 report already
   traced.
2. **`appinfo_qa.parse()`**'s field map: `if "reject" in ws and ("reason"
   in ws or "code" in ws): add(REJECT)` — this maps the words
   "reject"+"reason" to `reject_reason_code`, a column that does not exist
   in `full_field_patta_transfer_application_information` for an NISD file
   (confirmed live: `"Reject reason code: the NISD application-information
   record has no such column"`), a weaker, often-absent signal than the
   `workflow_history`-derived remark that already exists and is already
   correct.

Both are genuinely different "kinds of information" than the one the
officer asked for, exactly as Phase 2 warns against confusing: (1) is
procedural guidance, (2) is an unrelated intake-form status code, and
neither is the application's actual recorded rejection remark.

Full fix, before/after proof, live results and remaining issues:
`test_reports/P3_2_REJECTION_REASON_REPORT.md`.
