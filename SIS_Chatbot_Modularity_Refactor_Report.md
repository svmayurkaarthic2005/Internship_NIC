# SIS Chatbot — Modularity Refactor Report

**Repository**: `Internship_NIC`
**Reviewed commit**: `e20d608` (plus this session's uncommitted frontend fix,
unrelated to this refactor and left in place per "preserve unrelated
uncommitted changes")
**Files in scope**: `backend/services/chatbot.py`, `backend/services/rag.py`

## Honest scope statement (read this first)

The task spec (`SIS Chatbot — Behavior-Preserving Modular Refactoring of
chatbot.py and rag.py`) describes a 10-phase, both-file modularization into
roughly 15 target modules, with its own Phase 7 requiring one small cohesive
extraction at a time, a full test run after **each** step, before moving to
the next. The two files in scope are **32,450 lines / 430 functions**
(`chatbot.py`) and **8,629 lines / 72 functions** (`rag.py`) -- 41,079 lines,
502 functions combined.

Given that scale, I asked the user how to scope this session rather than
attempt the full spec in one pass (a full attempt would mean touching
hundreds of call sites without the kind of careful, test-after-every-step
verification the task itself demands -- the fastest way to silently break
jurisdiction checks, negation handling, or the 406-pair service-code
comparison matrix this codebase has been hardened against over many prior QA
passes). **The user chose: one real, cohesive, low-risk extraction, fully
verified, with an honest report on what remains.**

This report covers exactly that: Phase 1 (inspection) and Phase 2 (baseline)
were done for real across both files; Phase 3 was carried out for ONE module
(`rag_language.py`, extracted from `rag.py`); `chatbot.py` was left **entirely
untouched**. Nothing below claims more than this.

## Phase 1 — Inspection

### Current structure

| File | Lines | Top-level functions/classes |
|---|---|---|
| `backend/services/chatbot.py` | 32,450 | 430 |
| `backend/services/rag.py` (before) | 8,629 | 72 |
| `backend/services/rag.py` (after) | 8,409 | 68 |
| `backend/services/rag_language.py` (new) | 249 | 4 |

### rag.py — major responsibility clusters (traced by actual call sites, not filenames)

- **Language detection** (moved -- see Phase 3 below).
- **RAG/pgvector retrieval**: `get_rag_context`, `get_rag_context_async`,
  `format_structured_data_for_llm`. Calls `backend.services.pgvector_store.
  similarity_search`; merges filtered + unfiltered results by distance
  (documented dedup/merge behavior, untouched).
- **HTML/table rendering**: `build_html_response` / `_build_html_response_core`
  (a ~1,300-line function -- the single largest function in `rag.py`).
- **Prompt assembly**: `build_prompt` (~185 lines).
- **LLM transport**: `call_llama`, `call_llama_stream`, `paraphrase_facts_naturally`.
  Module-level `llm = ChatOllama(...)` client is constructed once at import
  time (settings-driven: `LLM_MODEL`, `OLLAMA_BASE_URL`, `LLM_KEEP_ALIVE`,
  `LLM_NUM_PREDICT`, `LLM_NUM_CTX`, `LLM_HTTP_READ_TIMEOUT`).
- **Comparison-query parsing**: `parse_comparison_query`, `_compare_aspect`,
  `_compare_sides`, `_has_compare_cue`, `_compare_periods`,
  `_classify_application_subtopic`.
- **Intent parsing**: `parse_intent` → `_parse_intent_core` (the single
  largest block in the file, lines ~3926-7046, ~3,100 lines -- keyword +
  fuzzy token-boundary matching for ~60 intents, in priority order per
  CLAUDE.md's documented intent table).
- **Entity extraction**: `extract_survey_number`, `extract_application_number(s)`,
  `extract_ward_number`, `extract_block_number`, `extract_town_name`,
  `extract_taluk_name`, `extract_district_name`, `extract_submission_channel(s)`,
  `extract_month_scopes`, `extract_date_range`, `is_bare_date_scope`,
  `extract_date_scope_fragment`, `resolve_month_year`, `season_in_text`, and
  supporting helpers (`_month_name_re`, `_month_bounds`, `_shift_month`,
  `_written_period_bound`).
- **Misc rendering helpers**: `_status`, `_reason`, `_geo_scope_label`,
  `_money`, `_app_link`, `_ordinal_suffix`, `extract_row_selection`,
  `extract_result_limit`, `extract_sort_order`.

### chatbot.py — major responsibility clusters (sampled; full 430-name list
is in the working tree's own symbol index -- IDE symbol search, per
CLAUDE.md's own navigation guidance, is more reliable here than reproducing
430 lines in this report)

- **Orchestration entry points**: `process_chat`, `process_chat_stream` (the
  two ~29,000-line-spanning pipelines CLAUDE.md documents as having
  "two near-duplicate code paths... requiring parallel edits at both call
  sites" -- confirmed true by this session's own prior work in this
  conversation, e.g. the `_route_negative_correction` wiring that had to be
  added at both call sites).
- **Service-code comparison**: `parse_service_comparison_mode`,
  `_build_service_code_comparison_response`, `_all_service_code_comparison_answer`,
  `_service_code_lookup_answer[_natural]` -- this session's earlier work (the
  `SVCCOMPARE-ALL-001..005` fixes, already committed at `fbd6620`) lives here.
- **Negative-correction / clarification**: `_build_general_negative_correction_response`,
  `_recent_nonattachment_claim`, `_resolve_pending_clarification`,
  `_route_negative_correction`.
- **~25 per-topic `*_qa.py` record-module coordinators** imported at the top
  of the file (`subdivision_qa`, `oldowner_qa`, `cmflow_qa`, `appinfo_qa`,
  `fline_qa`, `merge_qa`, `crosscheck_qa`, `igrs_owner_qa`, `newowner_qa`,
  `fulltransfer_qa`, `sdattach_qa`, `tslr_qa`, `uamod_qa`, `natham_qa`,
  `othercode_detail`, plus `followup_context`, `semantic_intent`) -- these are
  already separate modules; `chatbot.py`'s role here is dispatch, not
  implementation.
- **Field-visit handling**: `_recount_field_visits`, `_refine_field_visits`,
  `_fv_include_exclude_answer`, `_render_field_presence`, `_field_presence_answer`,
  `_standalone_field_presence_answer`, `_fv_followup`, `_fv_row_matches`,
  `_fv_aggregate_answer`.
- **Negation**: `_parse_negation`, `_rewrite_negated_types`.
- **Contentless-message / mutation-guard / greeting handling**:
  `_is_mutation_request`, `_mutation_refusal`, `_is_clear_command`,
  `_contentless_message`, `_greeting_kind`, `_llm_greeting`.

### Dependency map (the parts that matter for a safe extraction)

- `chatbot.py` imports **68 names** from `backend.services.rag` via one
  `from ... import (...)` block (lines 77-114) -- not `import rag` +
  `rag.foo()`. This matters: anything moved out of `rag.py` must still be
  importable from `backend.services.rag` by that exact name, or all 68 names
  need their import statement edited (out of scope for a single-function
  move; in scope the moment `rag.py` itself is split further).
- `chatbot.py` also imports from `postgres.py` (28 query functions),
  `backend.models` (12 ORM classes + `messages_since_session_start`), and the
  ~17 service modules listed above.
- `rag.py` imports from `backend.config`, `backend.services.llm_gate`,
  `backend.services.pgvector_store`, `backend.utils.logger`, `backend.utils.fuzzy`
  -- no import of `chatbot.py` (confirmed: `grep -n "import chatbot"
  backend/services/rag.py` → no matches), so the existing dependency
  direction is already one-way (`chatbot.py` → `rag.py`), which is why the
  refactor spec's "no submodule imports back through the facade" requirement
  was already satisfiable without restructuring anything beyond `rag.py`
  itself.
- Global/mutable state: `rag.py` holds one module-level singleton
  (`llm = ChatOllama(...)`), constructed once at import time. No per-request
  mutable state in either file at module scope (session/officer data is
  passed as parameters throughout, consistent with CLAUDE.md's "Avoid global
  mutable state for per-request or per-session information" instruction,
  which was already being followed, not something this pass needed to fix).

## Phase 2 — Baseline (recorded before any code was moved)

Run in order, before `rag_language.py` existed:

| Suite | Type | Result |
|---|---|---|
| `test_tanglish_language.py` | unit, no DB/LLM | **100/100 passed** |
| `test_tanglish_to_tamil_policy.py` | unit, no DB/LLM | **50/50 passed** |
| `SIS_Regression_Tests/test_tamil_negation_language_bug.py` | unit, no DB/LLM | **28/28 ok** |
| `SIS_Question_Mark_Regression_Tests/test_qmark_multiturn_integration.py` | integration, live DB | **87/101 passed (14 failures)** |
| All 9 `SIS_ServiceCode_FileDB_Regression_Tests/*.py` | unit, no DB/LLM | **all passed** (re-confirmed; not language-related, used as a cross-module smoke check) |

The 14 failures in the integration suite **pre-date this session's changes**
and are not touched or claimed fixed here -- see
`SIS_Chatbot_Modularity_Regression_Results.csv` for the exact baseline vs.
post-extraction comparison.

**Blocked, not claimed passing**: `backend/sample_db/test_intent_coverage.py`
and the other `backend/sample_db/test_*.py` scripts CLAUDE.md documents do
not exist on disk in the current working tree. This is pre-existing,
documented repo state (CLAUDE.md: "an entirely new, untracked
`backend/sample_db_new/` directory... replaces the committed
`backend/sample_db/build_app_tables.py` pipeline"), not something this
refactor caused or could run.

## Phase 3 — The one extraction: `rag_language.py`

### Why this module, first

Of `rag.py`'s 72 functions, the language-detection cluster
(`_explicit_tamil_request`, `detect_language`, `is_short_reply`,
`get_prior_turn_language`, plus their constants) was the cleanest candidate
found by tracing real dependencies:

- **Self-contained**: depends only on `re`, `typing.Optional`, and
  `backend.utils.fuzzy.{is_token_typo_match, normalize_text}`. No DB. No LLM.
  No other `rag.py` internals (confirmed by grep: nothing in this block
  references any other `rag.py` name, and nothing elsewhere in `rag.py`
  calls into this block either -- all 20+ callers of `detect_language` live
  in `chatbot.py`).
- **No ambiguous boundary**: the one superficially-related pair of constants,
  `_TA_NB`/`_TA_NA` (a general Tamil word-boundary regex helper), is used in
  8 unrelated places throughout `rag.py` for entity matching, not language
  detection -- tracing actual call sites (not the name) is what caught this;
  moving it would have been exactly the "move things that merely sound
  related" mistake the spec warns against. It was left in `rag.py`.
- **A documented, well-tested public interface**: `detect_language` and
  friends are imported by name from `backend.services.rag` by `chatbot.py`
  (68-name import block) and by two standalone test files directly
  (`test_tanglish_language.py`, `test_tanglish_to_tamil_policy.py`), giving a
  concrete, already-written compatibility contract to preserve and verify
  against -- rather than one this report would have to invent.

### What moved

`backend/services/rag_language.py` (new, 249 lines, 4 functions):
`_explicit_tamil_request`, `detect_language`, `is_short_reply`,
`get_prior_turn_language`, plus their supporting constants
(`_TANGLISH_WORDS`, `_TANGLISH_TYPO_WORDS`, `_LANG_CLAUSE_SPLIT_RE`,
`_LANG_NEGATOR_RE_PART`, `_TAMIL_REQUEST_RE`, `_TAMIL_NEGATED_MENTION_RE`,
`_TAMIL_SCRIPT_NEGATOR`, `_LANG_SHORTHAND_FILLER`, `_SHORT_REPLY_WORDS`).
Every regex, every threshold, every comment explaining *why* a rule exists
(e.g. the virama-boundary trap, the "Tamil Nadu" place-name carve-out, the
clause-aware negation logic) was copied **verbatim** -- no parsing semantics
were changed.

`backend/services/rag.py`: the 192-line block removed, replaced with:

```python
from backend.services.rag_language import (
    detect_language,
    is_short_reply,
    get_prior_turn_language,
    _explicit_tamil_request,
)
```

`backend/services/chatbot.py`: **zero changes**. Its existing
`from backend.services.rag import (detect_language, is_short_reply,
get_prior_turn_language, ...)` import (lines 77-114) resolves the same names
to the same function objects as before, via the facade.

### Compatibility verified, not assumed

```
rag.detect_language is rag_language.detect_language        -> True
rag.is_short_reply is rag_language.is_short_reply           -> True
rag.get_prior_turn_language is rag_language.get_prior_turn_language -> True
chatbot.detect_language is rag_language.detect_language     -> True
```

## Phase 6 — Behavior preservation, checked (not assumed)

| Check | Before | After | Verdict |
|---|---|---|---|
| `py_compile` on all three files | OK | OK | unchanged |
| `import` all three modules (module-level runtime errors) | OK | OK | unchanged |
| `test_tanglish_language.py` | 100/100 | 100/100 | **identical** |
| `test_tanglish_to_tamil_policy.py` | 50/50 | 50/50 | **identical** |
| `SIS_Regression_Tests/test_tamil_negation_language_bug.py` | 28/28 | 28/28 | **identical** |
| All 9 `SIS_ServiceCode_FileDB_Regression_Tests/*.py` | all pass | all pass | **identical** (cross-module smoke check -- these don't touch language code, confirming the extraction didn't destabilize anything else in `chatbot.py`/`rag.py`'s shared import surface) |
| `SIS_Question_Mark_Regression_Tests/test_qmark_multiturn_integration.py` | 87/101 | **87/101 (identical)** | **identical** -- same 14 pre-existing failures, zero regression |

## Phase 8 — Architecture-focused tests (new)

`SIS_ServiceCode_FileDB_Regression_Tests/test_rag_language_module_extraction.py`
(7 checks, all passing):

1. `rag_language.py` imports standalone.
2. No circular import (does not import `rag.py` or `chatbot.py`).
3. No DB/LLM import leaked into a pure parsing module.
4. Facade re-export identity (`rag.X is rag_language.X`).
5. `chatbot.py` sees the same function objects.
6. Patching `backend.services.rag.detect_language` does **not** redirect
   `chatbot.py`'s calls (chatbot.py bound its own reference at import time --
   this was already true before the extraction; the test pins it down so a
   future change doesn't silently alter it).
7. The three public functions take no officer/db/session argument (pure
   parsing, no authorization-sensitive state threaded through them).

## Phase 9 — Maintainability metrics

| Metric | Before | After |
|---|---|---|
| `rag.py` lines | 8,629 | 8,409 (-220, -2.5%) |
| `rag.py` top-level functions/classes | 72 | 68 |
| `rag_language.py` lines | -- | 249 (new) |
| `rag_language.py` top-level functions | -- | 4 |
| `chatbot.py` lines | 32,450 | 32,450 (untouched) |
| Circular imports detected | 0 | 0 |
| Duplicate definitions removed | -- | 0 (none found in the extracted block) |
| Tests executed (this session) | -- | 100+50+28+9 suites+7 new = **194+ individual checks**, all passing or matching baseline exactly |

The target was never line-count reduction for its own sake (the spec says so
explicitly) -- the actual gain is that language detection is now a
52-line-of-code-equivalent-complexity, independently testable, DB/LLM-free
unit with an explicit, enforced boundary (Phase 8 tests), rather than one
cluster among 72 undifferentiated functions in an 8,629-line file.

## Files created, moved, or modified

- **Created**: `backend/services/rag_language.py`,
  `SIS_Chatbot_Modularity_Refactor_Report.md` (this file),
  `SIS_Chatbot_Modularity_Regression_Results.csv`,
  `SIS_Chatbot_Modularity_Architecture.md`,
  `SIS_ServiceCode_FileDB_Regression_Tests/test_rag_language_module_extraction.py`.
- **Modified**: `backend/services/rag.py` (192 lines removed, replaced with a
  4-line facade import).
- **Untouched**: `backend/services/chatbot.py` (0 lines changed),
  all `*_qa.py` modules, `postgres.py`, `backend/models.py`, frontend code
  (per restriction), database schema (per restriction).

## Regressions discovered

**None.** Every baseline suite re-run after the extraction produced an
identical result (see `SIS_Chatbot_Modularity_Regression_Results.csv`).

## Unresolved risks / what a future pass should know

1. **`chatbot.py` is still one 32,450-line file.** This session did not
   touch it at all. The two near-duplicate pipelines (`process_chat` /
   `process_chat_stream`) CLAUDE.md documents, and that this conversation's
   own prior work had to edit in parallel (the `_route_negative_correction`
   wiring), remain the single biggest structural risk in the codebase for
   *future* edits (not something newly introduced here) -- any change to
   shared logic still requires finding and editing both call sites by hand.
2. **`rag.py` still has 68 functions / 8,409 lines** covering retrieval,
   intents, comparisons, entity extraction, rendering, prompts and LLM
   transport, undifferentiated. The architecture doc names the entity-
   extraction cluster as the next-safest candidate (same shape as the
   language module: pure functions, no DB/LLM, heavily called from
   `chatbot.py`), but that extraction was not attempted this session.
3. **The 14 pre-existing failures** in
   `SIS_Question_Mark_Regression_Tests/test_qmark_multiturn_integration.py`
   were observed, recorded, and deliberately **not** investigated or fixed --
   out of scope for a structural refactor task per the spec's own
   restriction ("Do not bundle unrelated behavior fixes into the refactor").
4. Multi-day effort estimate stands: at the rate of one carefully-verified
   extraction per session (inspect real dependencies → extract → run the
   full baseline → write an architecture test), finishing even just the
   remaining `rag.py` split is realistically several more sessions; the full
   `chatbot.py` split is substantially larger again.
