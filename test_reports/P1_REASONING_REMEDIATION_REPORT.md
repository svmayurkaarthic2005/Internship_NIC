# P1-2 Remediation Report — Evidence-Grounded Reasoning

Date: 2026-11-21. Branch `main`. Baseline: `test_reports/P1_REASONING_BASELINE.md`.

## A. Executive summary

**Implemented:** a controlled, evidence-grounded reasoning path for explanatory questions
("does X mean Y", "why should A and B not be treated as identical") that previously had no
home — either hijacked by a single-field handler answering with one unrelated value, or
falling to the plain LLM fallback with no structured, source-tagged evidence at all.

- A new module, `backend/services/chatbot/reasoning_qa.py`:
  - `is_reasoning_question(message)` — a narrow, cue-based classifier. Fires only on an
    explicit explanatory shape (does/mean/prove/mandatory/conclude/not-necessarily/
    discrepancy/"what information is missing"/"explain the difference") combined with a
    real evidentiary word (register, workflow, remark, recommend, owner, record, ...). A
    direct field lookup never matches it.
  - `gather_evidence(db, officer, app_no)` — pure data assembly, no LLM. Pulls from
    `applications`, raw `application_workflow` (including `field_visit_dt`, which no other
    handler surfaces), `workflow_history`'s closing remark, `correction_modification_flow`,
    the application-information extract, `full_field_patta_transfer_urban`, and the correct
    old-owner source for the file's type plus `full_field_patta_transfer_igrs_owner` — each
    tagged with its source table and one of `present` / `no_matching_row` / `not_applicable`
    / `not_queried`, so a blank column and a missing row never read the same.
  - `render_recorded_facts()` — deterministic prose from that package, plus the project
    rules relevant to the question (`_PROJECT_RULES`, three short, CLAUDE.md-sourced facts).
  - `answer()` — calls the LLM (the existing `rag_llm.call_llama`, via an `llm_call` test
    seam) ONLY to write the "Explanation" and "What cannot be concluded" sections, with a
    system prompt that forbids inventing a fact, document, date, visit, approval, closure,
    owner identity or legal conclusion not already in the Recorded Facts text.
- A fail-closed authorization gate, `_is_accessible()`, inside `gather_evidence()` itself —
  the one place every other `_qa.py` loader's jurisdiction-blind query is reached from.
- Three narrow routing changes (none reorder the dispatch chain, none disable a handler):
  1. `_early_subdivision_answer()` (`chat_application_queries.py`) claims a reasoning
     question FIRST, ahead of every single-field record module.
  2. `_big_plan()` (`chat_corrections.py`) stands aside for a reasoning question instead of
     shredding it into fragments.
  3. `orchestrator.py`'s application gate (`resolve_application_reference()`'s
     `"register_only"` verdict) is overridden to `"ok"` for a reasoning question, so a
     register-only application still reaches `_early_subdivision_answer` instead of the
     gate's own pre-rendered details card.

**What remains unresolved:** Scenario A's live LLM explanation correctly used the recorded
facts but did not literally restate "a populated date column does not establish the service
requires an inspection" in response to the follow-up's exact wording — see §I. No change was
made to date/field correction splitting, compound pair-reference messages, cross-references
between a new application and an older pair member, or the hybrid embedding-based intent
router, all explicitly out of scope.

## B. Architecture

```
POST /api/v1/chat/stream
    -> _big_plan(message)                         <-- NEW stand-aside: is_reasoning_question(message) -> None
    -> parse_intent() / resolve_application_reference()  <-- gate: exists? accessible? which verdict?
         "register_only" verdict + is_reasoning_question(message)
              -> NEW: rewritten to "ok"            <-- gate's OWN pre-rendered card no longer short-circuits
    -> _early_subdivision_answer()
         NEW: if is_reasoning_question(message):
                  app = extract_application_number(message) or gate_app
                        or _extract_app_number_from_context(...)
                        or [chat-history fallback via cmflow_qa._APP_NO.search]
                  if app: return await reasoning_qa.answer(db, officer, message, app, language)
         (else, unchanged) _who_decided / _other_code_workflow / subdivision_qa / oldowner_qa /
                            cmflow_qa / appinfo_qa / fulltransfer_qa / igrs_owner_qa / ...
    -> (nothing above claimed it) -> general_query -> agent.py / plain RAG prompt (unchanged)
```

Inside `reasoning_qa.answer()`:

```
gather_evidence(db, officer, app_no)        <-- no LLM; fails closed on jurisdiction
   -> _is_accessible(...)                   <-- deny before touching any other table
   -> applications, application_workflow, workflow_history, correction_modification_flow,
      application_information, full_field_patta_transfer_urban, old-owner (type-correct
      source), igrs_owner      [each: present | no_matching_row | not_applicable | not_queried]
render_recorded_facts(evidence, rules)      <-- deterministic text, no LLM
call_llama(system_prompt_with_facts_and_question)   <-- Explanation + What-cannot-be-concluded only
```

## C. Routing — how a reasoning question is told apart from a field lookup

`is_reasoning_question()` requires BOTH an explanatory-shape cue (`_REASON_CUE`: does/mean,
is-that-proof, what-can-we-conclude, why...not, not-necessarily-mean, explain-the-difference,
what-information-is-missing, ...) AND an evidentiary word (`_EVIDENCE_WORD`: register,
workflow, remark, recommend, record, owner, status, visit, complete, missing, ...). Verified
against both lists in the task:

| message | classified |
|---|---|
| "Does that mean an inspection was mandatory or actually completed?" | reasoning (True) |
| "Why should we not automatically treat them as identical?" | reasoning (True) |
| "What is the application status for 2026/0153/02/045470?" | direct (False) |
| "Show the latest workflow transition." | direct (False) |
| "What is the difference between ISD and NISD?" | direct (False) — stays with `service_code_guide` |
| "Compare 2026/0153/02/045470 and 2026/0153/02/045472." | direct (False) — stays with the two-app comparator |

`tests/unit/test_p1_evidence_reasoning.py::test_genuine_reasoning_question_recognised` /
`test_direct_field_question_not_reasoning` / `test_ambiguous_or_unrelated_not_hijacked`
cover these exactly.

## D. Evidence provenance per scenario (as actually retrieved from the live database)

**Scenario A** (`2026/0153/02/045470`, NISD): `applications` (type NISD, status rejected,
decision 2026-09-13); `application_workflow` 3 hops, including the populated
`field_visit_dt=2026-09-10` on the SIS→Tahsildar hop and two blank ones; `workflow_history`'s
closing remark (the same "Linked Documents are not submitted" text); `correction_modification_flow`
not applicable (0153 code); `full_field_patta_transfer_application_information.missing_docs`
present-but-blank; `full_field_patta_transfer_urban` (reason "Sale deed", `zdt_remarks`);
`full_field_patta_transfer_old_owner` 1 row; `full_field_patta_transfer_igrs_owner` 1 row.

**Scenario B** (`2026/0158/02/001804`): `applications` no matching row (correct — 0158 is
never in that table); `application_workflow` 6 hops, latest `SD (8) -> DIS (12)` on
2026-09-23 — NOT a closing hop; `correction_modification_flow` present, `thl_rec='Y'`,
`thl_remarks='Approved'`; `application_information` no matching row (0158 has none).

**Scenario C** (`2026/0153/02/045470`): `full_field_patta_transfer_application_information.missing_docs`
present and blank (a real column, read, carrying no value — confirmed by a direct `SELECT *`
against the table, not assumed); `full_field_patta_transfer_urban.zdt_remarks` states
*"NO LINK from RH ... /Linked Documents are not submitted"*; the closing `application_workflow`
hop's own remark quotes the identical text.

**Scenario D** (`2026/0153/02/045472`, NISD): `full_field_patta_transfer_old_owner` 1 row
(the correct NISD old-owner source — `chitta_temp_old_owner`, the ISD-only source, correctly
has 0); `full_field_patta_transfer_igrs_owner` 2 rows. No mobile number, address, ration/EPIC
number or Aadhaar value is ever placed in the evidence package (`_redact_owner_row()` drops
them before the package is built, not after).

## E. Four scenario results

All four were run through the real pipeline (`POST /auth/login` -> `/chat/sessions` ->
`/chat/stream`), against the live PostgreSQL database and `llama3.1:8b` via Ollama — not
mocked, not hand-written.

### Scenario A

> **Q:** This is an NISD application, which normally does not require a field visit.
> However, I can see a field_visit_dt in one workflow row. Does that mean an inspection was
> mandatory or actually completed? Application 2026/0153/02/045470. (96.7–206.4s across two runs)

> **A (Explanation):** The application was rejected on 2026-09-13, and the rejection reason
> recorded in the workflow_history is "[N/06]//NO LINK from RH வேதராமன் மகன்
> பாலசுப்பிரமணியன்/ N-Parent /Linked Documents are not submitted". This indicates that the
> application was rejected because the required documents were not submitted, not because an
> inspection was not completed.

**Verdict: PARTIAL.** The Recorded Facts section is fully correct and complete (populated
`field_visit_dt`, the blank hops, the NISD-no-visit project rule all present). The LLM's
explanation is grounded (it invents nothing) but answers a slightly different question than
the one literally asked — it explains the rejection reason rather than stating the "a
populated date column does not, on its own, establish that an inspection was mandatory or
completed" conclusion the Recorded Facts and project rule already support. The follow-up
("Does a populated date column automatically mean the service requires an inspection?",
85.2s) produced the same facts again rather than a sharper restatement. Documented honestly
as a generation-quality limitation, not a grounding failure — nothing fabricated.

### Scenario B

> **Q:** Application 2026/0158/02/001804. The register shows this application is still open,
> but the Tahsildar has recorded Approved. Does that mean the complete application is
> closed? Explain using the register, correction-modification record and latest workflow
> transition. (92.6–110.9s across runs)

> **A (Explanation):** The register shows that the Tahsildar has recorded Approved in the
> correction_modification_flow, but the application's current_status is still pending. The
> workflow_history shows that the application has not reached a COMPLETED or REJECTED stage,
> so the application is still open. The Tahsildar's approval is recorded as a recommendation,
> not the final decision, so it does not change the application's status from pending to
> approved.
>
> **What cannot be concluded:** The application's current_status is still pending, and it has
> not reached a COMPLETED or REJECTED stage, so it cannot be concluded that the application
> is closed.

**Verdict: PASS.** Precisely correct, fully grounded, directly answers the question asked.

Follow-up ("What information is still missing before you can definitively say it is
completed?") initially mis-routed (see §G, bug 3) and was fixed; after the fix it correctly
reaches `reasoning_qa` (52.2s) with the same evidence package.

### Scenario C

> **Q:** Application 2026/0153/02/045470. What exactly was missing? The missing_docs field
> appears blank, but the rejection remark says linked documents were not submitted. Explain
> the difference. (72.1–104.3s across runs)

> **A (Explanation):** The rejection remark states that linked documents were not submitted,
> which is the reason for rejection. The missing_docs field is blank, indicating that no
> documents were reported missing by the officer. However, the remark specifically mentions
> linked documents, which suggests that the officer did not receive them or they were not
> uploaded.
>
> **What cannot be concluded:** ... It is unclear what the officer expected to receive or
> what the linked documents are, so it cannot be concluded what was missing.

**Verdict: PASS.** Correctly distinguishes the blank structured field from the free-text
remark, and — critically — does NOT invent a specific document name or type the source does
not specify, exactly the constraint the task required.

### Scenario D

> **Q:** Application 2026/0153/02/045472. The old-owner and IGRS-owner tables have different
> structures. Why should we not automatically treat them as identical? Explain what each
> table actually records. (102.1–104.4s across runs)

> **A (Explanation):** The old-owner and IGRS-owner tables record different stages of the
> same transfer, populated independently. The old-owner table shows the owner(s) tied to the
> deed being transferred, while the IGRS-owner table shows the owner(s) named on the
> Sub-Registrar referral. The two tables may have different row counts because one source
> names more owners than the other, not because they disagree about ownership.
>
> **What cannot be concluded:** ... it is not possible to conclude that they record the same
> information.

**Verdict: PASS.** Correctly identifies `full_field_patta_transfer_old_owner` (not the
ISD-only `chitta_temp_old_owner`) as the NISD file's real old-owner source, states the two
tables' distinct roles, and explicitly avoids the "different row count = different owner"
inference the task warned against. No Aadhaar, mobile or address value appears anywhere in
the prompt or the answer.

## F. Automated test results

```
python -m pytest tests/ -q
```
**247 passed** (baseline 210 + 37 new in `test_p1_evidence_reasoning.py`).

```
python -m pytest tests/unit/test_p1_evidence_reasoning.py -v
```
37 tests, all passing, covering:
- **Routing** (6 tests): genuine reasoning questions recognised; direct field questions and
  ambiguous/unrelated ones (ISD/NISD definition, bare two-app comparison) not hijacked;
  `_reasoning_rule_keys()` topic narrowing; `_big_plan()` stand-aside for both multi-sentence
  scenario questions; the `register_only` gate-verdict override.
- **Evidence assembly** (7 tests, DB-backed via `test_officer`/`db_session`): each scenario's
  real evidence verified field-by-field against the live database; blank-field vs
  missing-row vs not-applicable distinguished; no cross-application contamination; the
  bounded-size caps (`_MAX_WORKFLOW_HOPS`, `_MAX_OWNER_ROWS`).
- **Grounding** (3 tests, controlled LLM test double): the prompt actually carries the
  retrieved facts and the question; an LLM failure returns an "unavailable" note instead of a
  fabricated conclusion; no mobile/address/ID-number key ever reaches the prompt.
- **Authorization / IDOR regression** (4 tests): no officer, an officer with empty
  `jurisdiction_ids`, and both the 0153/0154 and register-only code paths all fail closed
  with `accessible: False` and an empty `sources` list; `answer()` refuses without ever
  calling the LLM when inaccessible.
- **Regression** (3 tests): the three Phase-5 direct-lookup questions never classify as
  reasoning.

No test hardcodes a response string to force a pass; every DB-backed assertion reads the
live register, so a reseed that changes these rows would fail the test honestly rather than
pass on stale data.

## G. Live API results — three real bugs found and fixed during verification

All verification used the real pipeline end to end (`/auth/login` -> `/chat/sessions` ->
`/chat/stream`), the live PostgreSQL database, and `llama3.1:8b` via Ollama, with the server
restarted and `*.pyc` cleared before every re-check so no result could be an artifact of
stale bytecode.

**Bug 1 — IDOR / missing jurisdiction scoping (found by a background security review, fixed
before any live run quoted above).** `gather_evidence()`'s first draft called
`get_application_detail(db, app_no, officer=officer)` and then queried `cmflow_qa.load()`,
`appinfo_qa.load()`, `fulltransfer_qa.load()`, the old-owner and IGRS-owner loaders — none of
which carry a jurisdiction check of their own (same as every other `_qa.py` module; they rely
on their caller) — regardless of whether the first query found the application accessible.
An officer could therefore ask a reasoning question about an application outside their
jurisdiction and receive real record data in the answer. Fixed with `_is_accessible()`:
fails closed for no officer, for empty `jurisdiction_ids` (closing a secondary gap where
`get_application_detail()`'s own filter treats an empty filter list as "no filter" and
returns the row unscoped), for 0153/0154 via `get_application_detail()`'s own result, and for
every other service code via the SAME `ServiceRegisterEntry.ward_id.in_(officer.jurisdiction_ids)`
check `get_all_service_code_applications()` already uses. Regression tests:
`test_no_officer_fails_closed`, `test_officer_outside_jurisdiction_fails_closed_register_code`,
`test_officer_outside_jurisdiction_fails_closed_nisd`, `test_answer_refuses_without_calling_llm_when_inaccessible`.

**Bug 2 — `_big_plan()` shredded the reasoning questions before classification.** Live run 1
showed Scenario A's and B's FIRST turns answered with an unrelated NISD listing and a bare
details card respectively, at 3–60s (far too fast for the LLM call `reasoning_qa.answer()`
makes) — `_big_plan()`'s multi-question splitter (14+ words, multiple sentences, a "?") split
the deliberately multi-sentence scenario question into fragments before `parse_intent` or
`is_reasoning_question` ever saw the whole message, and answered each fragment separately.
Fixed with a narrow stand-aside at the top of `_big_plan()`: `is_reasoning_question(text)` ->
`None`, the same pattern the function already uses to stand aside for a real comparison
question. Confirmed fixed in live run 2 (both A1 and B1 produced the full `reasoning_qa`
"Recorded facts" answer).

**Bug 3 — the `register_only` gate verdict bypassed `_early_subdivision_answer` entirely.**
Live run 2's Scenario B follow-up ("What information is still missing...?") STILL answered
instantly (0.6–0.8s) with the 0158 details card, even after Bug 2's fix. Traced directly
(`resolve_application_reference()` called in isolation against the real database) to
`orchestrator.py`'s `if _app_resolution["verdict"] not in ("ok", "none"):` check: the gate,
having resolved the application from chat history and found it accessible, returns a
`"register_only"` verdict carrying its OWN pre-rendered details-card text — and the
orchestrator treats every verdict other than `"ok"`/`"none"` as a refusal to render
immediately, before `_early_subdivision_answer` (and this task's hook inside it) is ever
called. Fixed with a narrow override at both call sites (streaming and non-streaming):
`"register_only"` + `is_reasoning_question(message)` rewrites the verdict to `"ok"` — the
access decision itself is untouched (the gate already proved existence and jurisdiction), only
which answer gets built from it changes. Confirmed fixed in live run 3: the same follow-up
now returns the full `reasoning_qa` answer (52.2s) with the correct evidence package.

### Phase 5 — deterministic lookups, unaffected

```
P5-1  What is the application status for 2026/0153/02/045470?             0.4s
P5-2  When was its registration document registered?                      0.6s
P5-3  When did the SIS recommendation occur for 2026/0158/02/000002?       0.9s
P5-4  What are the temporary subdivisions for 2026/0154/02/012715?         0.5s
P5-5  What is the new patta number for 2026/0154/02/012766?                1.3s
P5-6  What amount and payment mode are recorded for 2026/0178/02/000267?   0.8s
P5-7  Show the latest workflow transition.                                 0.9s
```
All seven answer in under 1.5 seconds, through their existing deterministic handlers,
exactly as before this task — none of the three routing changes altered their behavior.

## H. Performance

| path | observed latency |
|---|---|
| deterministic field lookup (Phase 5) | 0.4 – 1.3s |
| evidence-grounded reasoning (one LLM call for the explanation only) | 52 – 207s |

The wide reasoning-turn range reflects `llama3.1:8b`'s own variance under this environment's
Ollama instance, not the evidence-assembly code (`gather_evidence()` itself completes in
well under a second against the live database — the same order of magnitude as the
deterministic lookups, confirmed by the DB-backed unit tests' own run time). A direct field
lookup is never routed through the LLM; `gather_evidence()` issues one query per source table
(bounded: `_MAX_WORKFLOW_HOPS=10`, `_MAX_OWNER_ROWS=5`), never a loop over the whole register.

## I. Limitations

- **Scenario A's generation quality.** The Explanation section, while fully grounded, did not
  literally restate the "a populated date column does not establish an inspection was
  mandatory" conclusion the facts support — it is a correct but incomplete answer to the
  exact question asked. This is a prompt/model quality limitation, not a grounding failure
  (nothing fabricated); a stronger instance-specific instruction in the prompt or a stronger
  model could likely improve it, but that is a tuning question, not this task's routing/
  grounding scope.
- **Not addressed, per the task's own scope control:** date/field correction splitting,
  compound pair-reference messages, cross-references between a new application and an older
  pair member, Ollama cold-start behavior, new embedding-based intent classification, UI
  changes.
- **Register-only gate override is narrow by construction.** It only rewrites the
  `"register_only"` verdict, and only when `is_reasoning_question()` is true; every other
  gate verdict (`invalid_format`, `not_found`, `forbidden`, `needs_confirmation`,
  `ambiguous_context`) is untouched, so an inaccessible or malformed number is still refused
  exactly as before.
- **The LLM's explanation quality was not formally scored against a larger sample** — four
  scenarios, each run multiple times, is what this task's acceptance criteria asked for;
  broader quality evaluation (as the project's existing `lora_training/eval_grounded.py` does
  for the fine-tune) was out of scope here.

## J. Git safety

No destructive git command was run. No commit, stash or push was made. `git status --short`
at the end of this task: 67 lines (66 at the start of this phase's own baseline, +1:
`backend/services/chatbot/reasoning_qa.py`, newly untracked — `test_reports/` and `tests/`
were already untracked directories before this task and are reported as single `??` lines,
not individually).

**Files changed by this task:**
- New: `backend/services/chatbot/reasoning_qa.py`
- Modified: `backend/services/chatbot/chat_application_queries.py` (the reasoning hook +
  `_reasoning_rule_keys()` + chat-history fallback, inside `_early_subdivision_answer`),
  `backend/services/chatbot/chat_corrections.py` (the `_big_plan()` stand-aside),
  `backend/services/chatbot/orchestrator.py` (the `register_only` gate override, both chat
  paths)
- New test file: `tests/unit/test_p1_evidence_reasoning.py` (37 tests)
- New report files: `test_reports/P1_REASONING_BASELINE.md`, this report
- New diagnostic scripts (not part of the application):
  `test_reports/qbank_artifacts/p1_2_verify_live.py`, `p1_2_full_text.py`,
  `p1_2_verify_b_only.py`

All P0 fixes (`_asks_application_number_format`, the `automutated` alias removal, the
0153/0154-only `cmflow_qa` fallback) and all P1 comparison/context fixes
(`is_two_application_comparison`, `Resolution.pair`, `_resolve_pair_reference`, the
single-application entity override) were re-confirmed present and passing (210/210 of the
pre-existing suite) both before and after this task's edits.
