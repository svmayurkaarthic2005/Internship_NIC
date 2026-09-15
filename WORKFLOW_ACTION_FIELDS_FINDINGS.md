# `application_workflow_action` fields — test bank + findings

Scope: every column of the layer-1 `application_workflow_action` table, asked
the way an SIS officer would, in English / Tamil / Tanglish, including
previous-message reference chains.

## Environment note

This session had **no Postgres, no Ollama, no Python deps, no venv**, so the
chatbot could not be run end-to-end here. Findings below are from a static
trace of `parse_intent` (`rag.py`) + the two `_field_map` blocks and the
field-lookup gates in `chatbot.py` + the layer-1→layer-2 projection in
`backend/sample_db/build_app_tables.py`. Run the suites on the Windows box to
confirm.

## Deliverables added

| file | what |
|---|---|
| `backend/sample_db/workflow_action_question_bank.py` | generator — `build_bank()` returns 1184 `Question` records; `--txt` emits the flat form; nothing touches the DB/LLM |
| `test_questions_workflow_action_fields.txt` | 1184 numbered questions (EN + TA/Tanglish), grouped by column, with follow-up-reference chains and rule/list shapes; regenerate with `python -m backend.sample_db.workflow_action_question_bank --txt > test_questions_workflow_action_fields.txt` |

Coverage: 21 columns, 771 questions on projected columns, 413 on
not-projected columns, 264 follow-up turns across 5 chain shapes × 11 sample
applications.

## How each column reaches an answer today

| column | layer-2 home | reached by | verdict |
|---|---|---|---|
| `application_id` | `applications.application_number` | `application_status` | ok |
| `serial_number` | derived: last path segment of the app number | `_field_map` "serial number" → `serial_number` | ok |
| `district_code` | app-number segment / `District.district_code` | `_field_map` "district code" | ok |
| `taluk_code` | `Taluk.taluk_code` | `_field_map` "taluk code" | ok |
| `village_code` | **not urban** — always `None`; `urban_unit_code` is the real thing | `_field_map` "village code" → `_missing_field_answer` | ok-ish (terse "No village code information found") |
| `action_from_role_id` | `workflow_history.from_stage` (via `ROLE_TO_STAGE`) | only `_wants_workflow_history` ("who acted/processed/handled/forwarded", "all steps") | **gap** — "which desk did X come from" / "from-role" does not route anywhere; falls to LLM |
| `action_to_role_id` | `workflow_history.to_stage` / `applications.current_stage` | `_field_map` "stage"/"where"/"workflow state" | ok |
| `action_date` | `workflow_history.performed_at` (actually from `last_updated_datetime`) | `_field_map` "date" → `submission_date`; `_asked_decision_date` for "when approved" | **partial** — "on what date was the last action taken" answers the *filing* date |
| `remarks` | `workflow_history.remarks` / `rejection_reason` | `_wants_workflow_history` (timeline), `sd_remarks` (needs `\bsd\b`) | ok via "workflow history for X"; a bare "what are the remarks on X" with an app number routes to `application_status` and there is **no remarks field in `_field_map`** → LLM |
| `action_status` | not projected | — | **was** LLM; **now** deterministic (fix below) |
| `last_updated_datetime` | `workflow_history` max `performed_at` | `_field_map` "last updated" | ok |
| `updated_by_user` | `workflow_history.performed_by_officer_id` | `_wants_workflow_history` for "who processed X"; otherwise "who last updated X" hits "last updated" → returns the **timestamp, not the person** | **partial** |
| `workflow_state` | `applications.current_stage` | `_field_map` "workflow state" | ok (returns the stage label, not the raw `C`/`P`) |
| `recommendation_status` | not projected | — | **was** LLM; **now** deterministic (fix below) |
| `field_visit_date` | `applications.field_visit_date` / `field_visits.scheduled_date` (ISD/MERGE only) | `_field_map` + `_field_visit_answer` | ok |
| `received_flag` | not projected | bare `"received"` key → `submission_date` | **was WRONG** (answered the filing date); **now** deterministic (fix below) |
| `annual_income` | not projected | — | **was** LLM; **now** deterministic (fix below) |
| `review_flag` | not projected | — | **was** LLM; **now** deterministic (fix below) |
| `ip_address` | not projected (CLAUDE.md: fingerprint only, "not used by the projection") | — | **was** LLM; **now** deterministic (fix below) |
| `auto_recommendation_flag` | not projected | — | **was** LLM; **now** deterministic (fix below) |
| `auto_recommendation_remarks` | not projected | — | **was** LLM; **now** deterministic (fix below) |

## Fix applied

`backend/services/chatbot.py` — new `_UNTRACKED_WF_FIELDS` /
`_asked_untracked_wf_field()` / `_untracked_wf_field_answer()` next to
`_asked_decision_date`, and one `elif` in each of the two field-lookup gates
(non-streaming ~L8319, streaming ~L12138), mirroring the existing
`_asked_decision_date` gate exactly.

Effect: a pointed question that names one of the 8 not-projected workflow-log
columns **and** an application number is now answered deterministically —
"Application X's review flag is not held in your SIS register. It exists only
in the source workflow-action log (`review_flag`), which this assistant does
not query. I can give you the field visit, the workflow remarks, …" — in
English and Tamil, instead of an ungrounded LLM/agent answer. This also
removes the `received_flag` → filing-date misfire.

Why a separate gate and not new `_field_map` keys: the bare `"received"` key
already maps to `submission_date`, so a `"received flag"` phrase key would
produce a two-row multi-field answer (`Received Flag: N/A` + `Submission
Date: …`) rather than a clean reply. The gate runs before
`_fuzzy_match_all_fields` and side-steps the collision.

Not touched (routing-risky without the test suites): `parse_intent`, the
`_field_map` dicts, `_wants_workflow_history`.

Verification done here: `python -m py_compile backend/services/chatbot.py`
passes; the gate is pure-additive (only fires on the 8 cue sets, only inside
the `app_no` branch, sets `all_matches = []` exactly like the sibling gate).

## Still open — recommended, not applied

1. **`updated_by_user` "who last updated X"** → returns a timestamp. Add
   `who + updated/changed/modified` (without `acted/processed/…`) to
   `_WORKFLOW_HISTORY_RE`, or a small gate that answers with
   `sd["decision_by"]` / the last hop's `changed_by_name`.
2. **`action_from_role_id` "which desk did X come from"** and bare
   **`remarks` "what are the remarks on X"** (app number present, no `sd`
   token) fall to the LLM. Either add `"remarks"/"from desk"/"previous desk"`
   to `_field_map` pointing at a renderer that reads `sd["history"]`, or
   widen `_WORKFLOW_HISTORY_RE` to catch "remarks on <app>" and "where did it
   come from".
3. **`action_date` "date of the last action"** answers the submission date.
   If this matters, add an `action_date` key resolving to the last
   `workflow_history.performed_at` rather than letting "date" → `submission_date`.
4. **`village_code`** answer could say *why* it is empty (urban jurisdictions
   carry a town / urban-unit code, not a village code) the way
   `_missing_field_answer` already does for `igrs_form6_number`.

## Suggested test runs (Windows)

```powershell
# routing sanity for the field-lookup path (no DB/LLM)
python -m backend.sample_db.test_intent_coverage

# the new bank against a live stack
python backend\sample_db\check_app_wiring.py --chat   # spot-check a few
# then feed test_questions_workflow_action_fields.txt through whatever
# harness test_questions_200.txt uses, and eyeball the NOT-PROJECTED section:
# every one should say "not held in your register", none should invent a value.
```
