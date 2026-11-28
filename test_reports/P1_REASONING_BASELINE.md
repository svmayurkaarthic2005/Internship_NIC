# P1-2 Remediation — Baseline (Evidence-Grounded Reasoning)

Date: 2026-11-21. Branch `main`, HEAD `eaff14e50b7162339dc458b630cf406a037efd1d` at the start of this task
(unchanged by this task — no commit was made).

## 1. Repository state before any edit

`git status --short` showed the same 66 working-tree entries recorded at the start of the
P1_CONTEXT_COMPLETION phase: ~30 modified files under `backend/services/` and `backend/services/chatbot/`,
plus a handful of untracked new modules (`agent_plan.py`, `query_understanding.py`,
`response_generator.py`, `observability.py`, `reranker.py`, `security.py`, `pytest.ini`) and a `scratch/`
directory of one-off diagnostic scripts. None of this was touched, stashed, committed or reset.

Spot-checked that the three P0 fixes and the P1 comparison/context fixes are present in the working tree
exactly as the prior reports describe:

- `orchestrator.py`: `_asks_application_number_format()` defined and called at both guard sites.
- `appinfo_qa.py`: `"automatically"` is not mapped to `"automutated"` in `_EXACT` (the comment explaining
  why is still there).
- `chat_shared.py`: `is_two_application_comparison()` defined; `_cm_strong()`'s first check returns False
  for it.
- `followup_context.py`: `Resolution.pair`, `_resolve_pair_reference()` and the entity-override fix in the
  single-application branch are all present.

## 2. Test baseline

```
python -m pytest tests/ -q
```
Result: **210 passed** (matches the count the P1_CONTEXT_COMPLETION_REPORT.md recorded as its own
starting and ending baseline — no drift since that phase closed).

## 3. Architecture traced before design

```
POST /api/v1/chat/stream
    -> chat_dispatch_stream.py -> orchestrator._process_chat_stream_impl()
         -> _big_plan(message)            <-- multi-question splitter, runs FIRST, before
                                               intent parsing or any handler
         -> parse_intent() / _cm_strong() / _bare_app_status()  <-- intent + dispatch overrides
         -> _early_subdivision_answer()   <-- chat_application_queries.py: the "record answer" chain
              -> _who_decided / _ip_comparison_answer / _other_code_workflow / _attached_documents
              -> _unheld_qa.answer_app()
              -> _early_subdivision_answer_inner()
                    -> subdivision_qa / oldowner_qa / cmflow_qa / appinfo_qa / fline_qa /
                       crosscheck_qa / igrs_owner_qa / newowner_qa / fulltransfer_qa / ...
                    (each `.claims(message)` / `.parse(message)` on keyword cues; FIRST
                     handler whose claim is truthy wins and answers from its own one table)
         -> (nothing above claimed it) -> intent == "general_query"
              -> agent.gather_evidence() / run_agent[_stream]()   <-- LLM tool-calling loop
              -> plain build_prompt() + call_llama_stream()        <-- RAG/LLM fallback
```

Nine things this trace established, in the order Phase 2 of the task asked for them:

1. **Explanatory questions were not classified at all.** Nothing in `rag.parse_intent()` or
   `_early_subdivision_answer_inner()` asks whether a message wants an explanation versus a field value;
   a message is claimed the moment ANY keyword cue in ANY `_qa.py` module's `.claims()`/`.parse()` matches,
   whichever module happens to run first in the fixed chain above.
2. **Deterministic handlers claim on keyword presence, not question shape.** `appinfo_qa.parse()`,
   `fulltransfer_qa.claims()`, `cmflow_qa.parse()` etc. all read "does the message contain my field's cue
   word" — "field_visit_dt", "missing_docs", "old owner", "Approved" are all real cues several of these
   modules already recognise for ordinary field lookups, so a reasoning question built from the same
   vocabulary is claimed by whichever one matches first and answered with that one field alone.
3. **The FIRST thing that can swallow a long or multi-sentence message is `_big_plan()`** (in
   `chat_corrections.py`, called at the very top of both `_process_chat_impl` and
   `_process_chat_stream_impl`, line 1215 / 4355 of `orchestrator.py`) — a message of 14+ words shaped
   like a numbered list, several "?" marks, or (via `_compound_split`/`_prose_plan`) two clauses joined by
   "and"/"whether", is split into separate single-sentence questions BEFORE intent parsing even runs, and
   each fragment is answered independently through the ordinary pipeline. This is the actual root cause
   this task's Scenario A/B exposed first (see §5): a reasoning question is deliberately written across
   several sentences, and the splitter read that as several unrelated questions.
4. **When no deterministic handler claims a message, `intent` becomes `general_query`** and the turn goes
   to `agent.py`'s tool-calling loop first, falling back to a plain RAG prompt (`build_prompt()` +
   `call_llama[_stream]()`) if the agent layer is unavailable. Both already retrieve SOME evidence
   (`gather_evidence()`'s tool results, or the RAG prompt's retrieved record), but neither distinguishes a
   blank field from a missing row, neither tags a fact with its source table, and neither constrains the
   model to explain rather than restate or invent — because neither was built for an explanation request,
   only for "what is X" style lookups the deterministic handlers missed.
5. **Retrieved evidence reaches the LLM only as rendered prose, never as a tagged structure.** `agent.py`'s
   `build_answer_prompt()` and `rag.build_prompt()` both interpolate whatever a tool/query returned as
   plain text into the prompt; there is no notion anywhere in the existing pipeline of "this fact came
   from table X and the row existed but this column was blank" versus "table X had no matching row at
   all" — confirmed by reading both prompt-builders end to end, not assumed.
6. **Structured context IS retained across turns** (`FollowupContext`, unchanged by this task) — a
   reasoning follow-up that names no application can still resolve one via
   `_extract_app_number_from_context()`, the same mechanism every other record module already uses.
7. **Source-table identity is already a first-class idea in this codebase**, just never carried to the
   LLM: every `_qa.py` module's `load()` function returns a raw dict keyed by column name from ONE named
   table, and already distinguishes "no row" (`None`/`[]`) from "row present, column blank" via each
   module's own `_v()`/`_blank()` helper. This is exactly the primitive Phase 3 needed, already proven
   correct by the modules' own existing tests — reused, not reinvented.
8. **A single-field handler can and does out-run a comparison/explanation handler already in the
   chain**: `_early_subdivision_answer()` already carries one explicit example of "stand aside for a
   bigger question" (`_is_two_application_comparison(message)` returns `None` at the very top, before
   `_who_decided` or any record module runs) — this is the existing pattern this task's own routing fix
   (§5, Phase 3-B) extends, not a new mechanism.
9. **Timeouts and LLM errors are already handled at the `call_llama()` layer** (`rag_llm.py`): bounded
   retries, a `LLMBusy` short-circuit, and a fixed "taking longer than expected" message on timeout — reused
   as-is for the new reasoning-generation call rather than re-implemented.

## 4. Known reasoning failures (reproduced before any fix)

Reproduced directly against the live database and the existing handler chain (read, not run through the
API, at this stage):

- **Scenario A** (`2026/0153/02/045470`, NISD): `application_workflow` carries a real row with
  `field_visit_dt = 2026-09-10` on the SIS→Zonal Tahsildar hop — `appinfo_qa`/`fulltransfer_qa` have no cue
  that reads this raw workflow column at all (it is not one of the columns any existing `_qa.py` module
  projects), so a question about it had no deterministic handler to even mis-claim it; it would have
  fallen straight to `general_query`.
- **Scenario B** (`2026/0158/02/001804`): `service_register_entries` shows `status=pending, stage=Open`;
  `correction_modification_flow` shows `thl_rec='Y', thl_remarks='Approved'`; the latest
  `application_workflow` hop is `SD (8) -> DIS (12)` on 2026-09-23 — NOT a closing hop. `cmflow_qa.parse()`
  claims any message naming a recommendation field, so a question using the word "Approved" would have been
  answered with that one field's value, never the comparison against the register status or the latest
  workflow hop the question actually asked for.
- **Scenario C** (`2026/0153/02/045470`): `full_field_patta_transfer_application_information.missing_docs`
  is a real, present, blank column; `full_field_patta_transfer_urban.zdt_remarks` states
  *"NO LINK from RH ... /Linked Documents are not submitted"*. `appinfo_qa.parse()`'s `MISSING` cue claims
  any "missing documents" question and answers only `"Missing documents: none recorded"` from the blank
  column — the explanation sitting in the OTHER table is never reached, because `appinfo_qa` claimed the
  message first and returns without checking `fulltransfer_qa` at all.
- **Scenario D** (`2026/0153/02/045472`, NISD): `chitta_temp_old_owner` (the ISD-only old-owner table) has
  0 rows for this file — correct, since this file is NISD — but a prior pass's note assumed that meant "no
  old-owner record exists" without checking the NISD-specific source,
  `full_field_patta_transfer_old_owner`, which has 1 row. `full_field_patta_transfer_igrs_owner` has 2 rows.
  Neither `oldowner_qa` (chitta-only) nor `igrs_owner_qa` compares itself against the OTHER owner source by
  table identity; each answers only its own table.

## 5. Scope of the fix

Per the task's own scope control, this baseline confirms the fix is additive and narrow:

- One new module, `backend/services/chatbot/reasoning_qa.py` (classifier + evidence assembly + grounded
  generation). No existing `_qa.py` module is modified.
- One narrow stand-aside inside `_early_subdivision_answer()` (ahead of every single-field handler), one
  narrow stand-aside inside `_big_plan()` (so a multi-sentence reasoning question is never shredded before
  it is even classified) — no reordering of the chain, no handler removed or disabled.
- No new LLM, no new embedding model, no new database or vector store. The existing `call_llama()`
  (`rag_llm.py`) is reused for the generation step, with a test-double seam (`llm_call` parameter) so the
  evidence-assembly and prompt-construction logic is fully testable without Ollama.

Not addressed in this pass (confirmed in scope, per the task's §10): date/field correction splitting,
compound pair-reference messages, cross-references between a new application and an older pair member,
hybrid embedding-based intent routing. None of these were touched.
