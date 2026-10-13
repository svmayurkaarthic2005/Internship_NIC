# SIS Chatbot — AI Assistant Guide

## Project Overview

**Sub Inspector Surveyor (SIS) AI Chatbot** — A bilingual (Tamil/English/Tanglish) AI assistant for Sub Inspector Surveyor officers in Tamil Nadu, India. Officers interact via natural language chat to manage survey applications, track status, check documents, and query field visits.

- **Backend**: FastAPI + SQLAlchemy (async) + PostgreSQL + pgvector
- **Database**: `sis_chatbot_db` on `127.0.0.1:5432` — the single database for everything (CSV-shaped source tables, ORM tables, and the `knowledge_embeddings` vector store). ChromaDB is gone; there is no separate vector DB and no `vectorstore/` directory.
- **LLM**: Llama 3.1 8B Tamil (`mervinpraison/Llama-3.1-8B-Instruct-Tamil`) with the SIS QLoRA adapter merged, served as Q4_K_M GGUF through Ollama. Stock `llama3.1:8b` is the `.env` default and the fallback; the agent-behaviour notes below were observed on it.
- **Embeddings**: `nomic-embed-text` via Ollama
- **Frontend**: Vanilla HTML/CSS/JS (no framework)
- **Auth**: JWT (python-jose + passlib/bcrypt)

---

## Dev Commands

```powershell
# Activate virtual environment (Windows)
.venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# First-time DB setup — sis_chatbot_db (run in order)
python backend/sample_db/seed_sample_db.py     # 1. create db + seed the 16 CSV-shaped tables
python backend/sample_db/verify_sample_db.py   # 2. verify structure/refs/signatures/non-leakage
python -m backend.sample_db.load_master_dumps  # 3. load the 5 TAMILNILAM master pg_dumps
python -m backend.sample_db.adopt_master_district_taluk  # 3b. first run only: drop districts/taluks
python -m backend.sample_db.build_app_tables   # 4. project them into the app's ORM tables
python -m backend.sample_db.verify_identifiers # 5. verify every Aadhaar / CAN in both layers
python -m backend.ingest                       # 6. load document embeddings into knowledge_embeddings

# Start backend
python -m uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
# Or use the PowerShell script:
.\start_backend.ps1

# Serve frontend (separate terminal)
python serve_frontend.py       # Serves on http://localhost:3000

# Database integrity checks
python backend/sample_db/verify_sample_db.py   # CSV-shaped layer: structure, orphans, non-leakage
python -m backend.sample_db.verify_identifiers # both layers: Aadhaar + CAN formats
python check_missing_values.py                 # ORM layer: required fields not NULL
python check_sis_chatbot_db_tables.py          # Lists every table + row count
python check_login_credentials.py              # Prints the seeded officer logins

# Rebuild the ORM projection (idempotent — truncates what it owns, re-derives)
python -m backend.sample_db.build_app_tables

# Run tests
python -m backend.sample_db.test_intent_coverage   # routing, no DB/LLM (instant)
python -m backend.sample_db.test_date_queries       # date phrases parse + answer correctly
python -m backend.sample_db.test_channel_queries   # submission channel: routing, derivation, answers
python -m backend.sample_db.test_channel_queries --fast   # skips the LLM cases
python -m backend.sample_db.test_workflow_logic    # workflow invariants + answer consistency
python -m backend.sample_db.test_questions         # answer quality (--fast skips LLM cases)
python -m backend.sample_db.check_app_wiring --chat
python test_comprehensive_suite.py
python backend/test_200_suite.py
```

**API Docs** (dev only): http://localhost:8000/api/docs

**Test login credentials**: officers are the SIS usernames seeded from the workflow chain, one ward each, in the form `<name>@sis.tn.gov.in` with password `Test@1234` — currently `csenthil@` (ward 002), `msivakumar@` (ward 102) and `muthulakshmis@` (ward 103). Run `python check_login_credentials.py` for the current list with each officer's jurisdiction. The old `arjun.kumar` / `priya.devi` / `ramesh.babu` / `lakshmi.narayanan` accounts no longer exist.

---

## Project Structure

```
nic_internship/
├── backend/
│   ├── main.py               # FastAPI app, lifespan, middleware, routers
│   ├── config.py             # pydantic-settings (Settings class + DISTRICT_CODE_MAP)
│   ├── database.py           # Async SQLAlchemy engine + Base + get_db()
│   ├── models.py             # All SQLAlchemy ORM models (UUID PKs, TIMESTAMP(tz=True))
│   ├── schemas.py            # Pydantic schemas (StandardResponse, OfficerContext, etc.)
│   ├── dependencies.py       # get_current_officer() JWT dependency
│   ├── ingest.py             # Document ingestion into pgvector (knowledge_embeddings)
│   ├── schema.sql            # Raw SQL schema reference for the ORM tables
│   ├── test_200_suite.py     # 200-question backend suite (+ test_200_results.json)
│   ├── routers/
│   │   ├── auth.py           # POST /auth/login
│   │   ├── chat.py           # POST /api/v1/chat/stream, /chat/upload, GET /chat/history
│   │   ├── applications.py   # GET/PUT /applications
│   │   └── survey.py         # Survey endpoints
│   ├── services/
│   │   ├── chatbot.py        # Main orchestrator (~29000 lines) — entry point for all chat logic
│   │   ├── rag.py            # Intent detection, language detection, LLM calls, prompt building
│   │   ├── semantic_intent.py # Embedding-similarity detection of greetings / thanks / small talk
│   │   ├── postgres.py       # All database query handlers (get_officer_applications, etc.)
│   │   ├── agent.py          # LLM tool-calling loop (the general_query fallback)
│   │   ├── followup_context.py # Reference context for implicit follow-ups
│   │   ├── doc_extract.py     # Uploaded file → located text segments + citations
│   │   ├── attachment_store.py # Attachment tables, authorization, evidence retrieval
│   │   ├── csv_ops.py         # Deterministic count/sum/min/max/group over CSV rows
│   │   ├── attachment_qa.py   # Which file, which entity, evidence or refusal
│   │   ├── agent_tools.py    # The 14 authorized domain tools the agent may call
│   │   ├── readonly_guard.py # a chat turn may read the register, write only the transcript
│   │   ├── pgvector_store.py # pgvector operations (init, similarity search, ingest)
│   │   ├── embeddings.py     # Embedding generation via Ollama
│   │   └── auth_service.py   # Login, JWT creation/verification
│   ├── sample_db/            # sis_chatbot_db build pipeline — see backend/sample_db/README.md
│   │   ├── schema_builder.py      # Reads CSV headers, infers PG types, emits DDL
│   │   ├── schema_sis_chatbot_db.sql  # Generated DDL (regenerate, don't hand-edit)
│   │   ├── dbconn.py              # Shared connection helper for the scripts
│   │   ├── identifiers.py         # Aadhaar + CAN rules, shared by seed and projection
│   │   ├── dsc.py                 # X.509 certs + PKCS#7 signatures for the DSC columns
│   │   ├── seed_sample_db.py      # Creates the DB, applies DDL, generates + inserts rows
│   │   ├── load_master_dumps.py / adopt_master_district_taluk.py  # Layer 0 masters
│   │   ├── build_app_tables.py    # Projects sample tables → the app's ORM tables
│   │   ├── verify_sample_db.py / verify_identifiers.py  # Integrity checks
│   │   ├── check_app_wiring.py    # Smoke test: app queries + chatbot answer from this DB
│   │   ├── question_bank.py, appinfo_/workflow_action_/workflow_doc_question_bank.py  # Shared question sets
│   │   ├── test_intent_coverage.py, test_workflow_logic.py, test_questions.py,
│   │   │   test_question_bank.py, test_date_queries.py, test_channel_queries.py
│   │   ├── generate_user_test_fixtures.py  # Builds backend/test_fixtures/user_tests
│   │   ├── build_lora_dataset*.py # QLoRA training-set builders (see "Fine-tuning" below)
│   │   ├── clean_ / redact_ / validate_lora_dataset.py, strip_bad_*.py,
│   │   │   reinforce_glossary_facts.py   # dataset hygiene passes
│   │   ├── run_eval_baseline.py + eval_set.jsonl  # held-out eval, never trained on
│   │   ├── SIS_QLoRA_Training.ipynb   # Colab training notebook
│   │   └── README.md              # Source of truth for the DB layout
│   ├── sample_table/         # TAMILNILAM urban CSV + master pg_dumps (gitignored; empty in a fresh clone)
│   ├── documents/            # RAG corpus: workflow_guide.txt, faq_english/tamil.txt, land_rules.txt,
│   │                         #   survey_manual.txt, district_codes.txt, sis_upload_checklist.txt,
│   │                         #   database_structure_reference.txt, tamilnilam_urban_services_and_districts.txt
│   ├── test_fixtures/        # Upload-test files (PDF/DOCX/CSV/TXT), incl. user_tests/
│   └── utils/
│       ├── fuzzy.py          # Fuzzy month/token matching for typo tolerance
│       ├── helpers.py        # Misc helpers + SIS_URBAN_SERVICES service-code table
│       ├── translit.py       # Deterministic Tamil <-> Latin name transliteration
│       └── logger.py         # structlog-based logger (get_logger)
├── frontend/
│   ├── login.html / chatbot.html / channel_report.html
│   ├── css/
│   └── js/                   # auth.js, chat.js, chatStorage.js,
│                             #   table_renderer.js, lucide-fallback.js
├── var/attachments/          # Uploaded-file bytes (gitignored, outside anything served)
├── tools/                    # create_chatbot_test_fixtures.js
├── test_*.py / test_questions_*.txt   # Top-level suites and question sets (see Testing)
├── check_*.py, debug_*.py, trace_intent.py, query_channels.py   # One-off DB / routing probes
├── serve_frontend.py, start_backend.ps1, quick_setup.py
├── *.md reports              # AGENTS.md, README.md, *_FINDINGS.md, *_SUMMARY.md, DISTRICT_HANDLING.md ...
├── .env                      # Secrets (not committed)
├── .env.example              # Template
├── requirements.txt
└── CLAUDE.md                 # This file
```

Scratch and backup files in the repo root (`_*.py`, `*.log`, `*.bak`, `*.pre_*.bak`,
`b64_part_*.txt`, `kaggle_*_stage/`, `adapter_chunks/`) are working artefacts of the
fine-tuning and debugging passes, not part of the application.

---

## Fine-tuning (QLoRA) and the local model

The chatbot still answers deterministic questions from PostgreSQL; the fine-tune only
improves the LLM fallback and the Tamil / Tanglish phrasing of its answers.

**All QLoRA training data and the trained adapter live outside this repo, at
`C:\lora_data\lora_training\` / `C:\lora_data\sis-qlora-adapter\`** -- not even
as a junction inside the project folder. Scripts under `backend/sample_db/`
that read or write them (`audit_train_grounding.py`, `repair_train_labels.py`,
`run_eval_baseline.py`, `reinforce_glossary_facts.py`) use that absolute path
directly. The dataset-build scripts themselves also live entirely outside the
repo now, at `C:\lora_data\sample_db_lora\` -- not even as a junction under
`backend/sample_db/lora/` anymore. Each of those scripts' own ROOT-finding was
rewritten from a relative `Path(__file__).absolute().parents[3]` (which
assumed the script sat 3 levels under the project root) to the hardcoded
project path, and their cross-script imports that used to read
`backend.sample_db.lora.X` now add `C:\lora_data\sample_db_lora` to `sys.path`
and import `X` directly, since that folder is no longer part of the
`backend.sample_db` package tree at all.

`lora_training/` (now only ever meant as that external path) holds only what the current model actually trained on --
`train_qlora.py`, its `train_augmented.jsonl` + `validation.jsonl` inputs, and the
`eval_*_results.jsonl` history. The dataset-build and cleaning scripts
(`backend/sample_db/build_lora_dataset*.py` → `clean_lora_dataset.py` →
`redact_lora_dataset.py` → `reinforce_glossary_facts.py`, plus a handful of one-off
editing scripts) already ran; each wrote its output into `lora_training/` and, once
the next stage consumed it, was removed rather than kept as a stale intermediate.
Regenerating the dataset from scratch means rebuilding that chain from
`build_lora_dataset*.py` again.

```
build_lora_dataset*.py → clean / redact / reinforce_glossary_facts.py
   → lora_training/train_augmented.jsonl + validation.jsonl   (messages-only; meta stripped for training)
   → lora_training/train_qlora.py or SIS_QLoRA_Training.ipynb (Colab L4) → sis-qlora-adapter/
   → merge into an fp16 base → convert to GGUF → Q4_K_M → Ollama
```

- **Base model**: `mervinpraison/Llama-3.1-8B-Instruct-Tamil` (apache-2.0; per its model card an Unsloth/TRL SFT of `unsloth/Meta-Llama-3.1-8B-Instruct-bnb-4bit`, i.e. Llama 3.1 8B Instruct; the card tags it English and makes no Tamil claim, so the name is not evidence of Tamil ability). Its own `config.json` is
  wrong (`vocab_size=32000`, `num_key_value_heads=32`); load it with the config from
  `NousResearch/Meta-Llama-3.1-8B-Instruct` (128256 vocab, 8 KV heads).
- **Loss on assistant turns only**: pass the raw `messages` column with
  `completion_only_loss=True`. Flattening to a `text` field trains on the system
  prompt and the question too.
- **Never put live records in the data** — the set teaches behaviour, not application
  or CAN numbers.
- **`meta` is audit-only.** Mixed-type `meta.filters` breaks `datasets` schema
  inference, so strip it (`{"messages": ...}` only) before loading.
- **Two silent no-learning traps** (each cost a full run): LoRA params reloaded with
  `requires_grad=False` after `resume_from_checkpoint`, and `Trainer.create_optimizer()`
  returning a stale, empty optimizer because it only builds `if self.optimizer is None`.
  Check `[len(g["params"]) for g in trainer.optimizer.param_groups]` and a nonzero
  `lora_B` grad before trusting a run.
- **Export**: merge the adapter into an **fp16** base (not the 4-bit one), then
  `convert_hf_to_gguf.py` → `llama-quantize … Q4_K_M` (~4.6 GB). `llama-cli -p` runs raw
  completion, so judge answer quality through Ollama with the Llama-3 chat template.
- **Evaluation**: `backend/sample_db/eval_set.jsonl` is held out; `run_eval_baseline.py`
  records the un-tuned pipeline, and `lora_training/eval_*_results.jsonl` hold each later model.
- The project's model is this Tamil-base fine-tune. Point `LLM_MODEL` at the Ollama
  model created from the GGUF (Llama-3 chat template in the Modelfile); until then
  `config.py` still defaults to stock `llama3.1:8b`.

## Semantic routing and name transliteration

- `services/semantic_intent.py` classifies short, SIS-vocabulary-free messages as
  greeting / farewell / thanks / small talk by `nomic-embed-text` similarity to
  prototype phrases. It never routes SIS questions and returns `None` on any failure,
  so the deterministic parser carries on. `test_greeting_semantic.py`,
  `test_semantic_router_eval.py`.
- `utils/translit.py` adds a readable Tamil or Latin form beside an applicant name of
  record, which is stored in whichever script the extract carried. It never replaces
  the stored name.

---

## Database Layout — `sis_chatbot_db`

One PostgreSQL database holds **two layers**. Know which one you are touching before you write a query.

**Layer 1 — the 16 CSV-shaped tables** (source of record, seeded from the TAMILNILAM urban extracts). Column lists come straight from the CSV headers; each table gets a `row_id BIGSERIAL` PK because the extracts have no natural key. Code columns (`service_code`, `district_code`, `block_code`, statuses) stay `VARCHAR` so leading zeros survive — `0154`, `0015`, `01` must never become integers.

| Table | Rows | Table | Rows |
|---|---|---|---|
| `appl_log_urban_demo` | 1211 | `full_field_patta_transfer_old_owner_demo` | 630 |
| `application_workflow_demo` | 288087 | `full_field_patta_transfer_return_owner_demo` | 46 |
| `areg_temp_subdivclub_demo` | 49 | `full_field_patta_transfer_urban_demo` | 229 |
| `chitta_temp_subdivclub_demo` | 117 | `sub_div_patta_transfer_application_information_urban_demo` | 41 |
| `full_field_patta_transfer_application_information_demo` | 166 | `sub_div_patta_transfer_urban_demo` | 50 |
| `full_field_patta_transfer_igrs_owner_demo` | 100 | `uareg_demo` | 1033 |
| `full_field_patta_transfer_new_owner_demo` | 620 | `uaregmap_ds_demo` | 1036 |
| `uchitta_natham_demo` | 551 | `uchitta_nathammap_ds_demo` | 439 |

`application_workflow_demo` is a district-wide dump. Most of its 288087 rows
belong to settlement service codes (`0167` / `0169`, …) that never become an
application: only 4694 name an `application_id` that `appl_log_urban_demo`
also carries, and 982 of those belong to the `0153` / `0154` codes the
chatbot works with. `appl_log_urban_demo`'s 1211 rows cover 1139 distinct
application ids — an application spanning several parcels has one row per
parcel.

**Every TAMILNILAM service code, not just ISD/NISD — `service_register_entries`
(in progress, not yet committed).** `Application` (`ck_application_type`)
admits only `0153`/`0154`, because a full ISD/NISD workflow/stage/field-visit
machine is the only thing this register was originally built to run. But an
SIS officer's jurisdiction sees applications under the other ~27 TAMILNILAM
urban service codes too (`0158` Modification, `0159` Addition, `0160`
Deletion, `0162` Street/Door Modification, `0164` ULC Subdivision, `0165` TSLR
Owner Name Correction, `0169` Govt-to-Private, `0178` F-Line, …), and asking
about one of them used to get silently folded into the ISD/NISD answer or
pushed to the documents-only definition ("what is 0178?") with no way to see
an actual F-Line application.

This is now closed by a new `ServiceRegisterEntry` model (`backend/models.py`)
— one flat row per `application_id` across every service code, read from the
live `service_register_entries` table: application number, service code (+its
official name from `SIS_URBAN_SERVICES`), survey/subdivision number, a coarse
`status` (approved/pending/rejected — not the ISD/NISD state machine) and
`stage` (`Open`/`Completed` only), submission channel and date, ward/block. No
workflow history, no field-visit tracking, no applicant/owner detail — an
officer wanting that level of detail on an actual ISD/NISD file still uses
`Application`. `get_all_service_code_applications()` in `postgres.py` queries
it (ward-scoped, with optional `service_code` / `submission_channel` / `status`
filters); `rag.py` routes to it as the `all_service_code_applications` intent,
with an explicit regex for "F-Line application(s)" (`0178`) alongside the
general "applications of/across all service codes" phrasing; `chatbot.py`
renders it as a flat table in both chat paths.

**This whole feature is uncommitted, in-progress work** — `git status` shows
large diffs against `HEAD` in `models.py`, `postgres.py`, `rag.py` and
`chatbot.py`, plus an entirely new, untracked `backend/sample_db_new/`
directory (its own CSVs including `f_line_service_urban_demo.csv`,
`correction_modification_flow_demo.csv`, `uareg_modification_demo.csv`, …,
`column_map.py`, `build_app_tables_new.py`, `load_sample_db_new.py`) that
replaces the committed `backend/sample_db/build_app_tables.py` pipeline as the
thing that actually populated the live database — the committed pipeline does
not create or populate `service_register_entries` at all, and the live table
names (`appl_log_urban`, not `appl_log_urban_demo`) and row counts no longer
match the Layer 1 table above because they were loaded from
`sample_db_new`, not `sample_db`. None of this — the new table, the model, the
query function, the intent, the renderer, or the new build pipeline — is
reflected anywhere else in this file yet, and `sample_db_new/` has no
`verify_*` / `test_*` counterpart to the checks `sample_db/` has. Treat the
Layer 1/Layer 2 tables below as the superseded pipeline's shape until
`sample_db_new` is reconciled with (or replaces) `sample_db` and this file is
updated accordingly.

**Every service code is treated alike unless the question names ISD/NISD.**
`applications` holds only ISD/NISD, so answering generic questions from it
told an officer with 47 pending files they had 17, and the same officer got
57, 36 or 152 "applications" depending on phrasing. The register
(`service_register_entries`) is now the source for any question that names no
type:

| question | answered from |
|---|---|
| listing / count with a status, period, ward, block, channel, sort or "first N" ("show my pending applications", "how many are approved in June") | register, all service codes (`_register_listing()` in `chatbot.py`, routed by `rag._is_register_wide_listing()` wrapping `parse_intent`) |
| follow-ups on that list ("how many of them are CSC", "how many are 0158") | the rows on screen, register rows included (`get_applications_by_numbers`, `number_qa`) |
| totals, per-month/year, percentages, completion rate, by block/ward, status/channel/ward/month/type comparisons | register (`get_analytics_rows`, `get_comparison`, `_register_completion`, `get_applications_by_block`) |
| names ISD/NISD, or overdue / escalated / in progress / field visit / stage / workload / per-file fields / a survey or application number | the detailed ISD/NISD handlers, as before |
| average time, longest to approve, fee totals | ISD/NISD only (the register holds no decision date or fee); the answer says so |

`parse_intent` returns `pending_applications` for ~30 phrasings; the widening
is one rule on top of them, not 30 edits. Rejected files stay out of a list
unless asked for, and the answer reports how many were left out.
`python test_register_equal_treatment.py` (`--routing` skips the database).

**Equal-treatment audit (2026-10-11).** Of the 29 canonical `SIS_URBAN_SERVICES`
codes, 11 carry live rows for this seed (0153, 0154, 0156, 0158, 0159, 0160,
0162, 0164, 0165, 0169, 0178); the other 18 are zero-record, a data fact —
`all_service_code_applications` already answers them truthfully ("No
applications under X found"), never as "unsupported". A full sweep across all
11 (define / list / count / status-filter / ward-filter / compare / details /
status / workflow / field-visit-requirement / negation) found the architecture
already generic (no per-code branches anywhere: `_register_listing`,
`calc_qa.compare_codes`/`code_counts`/`_RANK`, `othercode_detail.card`/
`workflow` all key off the row data and `SIS_URBAN_SERVICES`, never a code
allowlist) — a register-only code with NO specialized detail table of its own
(0165, TSLR Owner Name Correction: no row in `correction_modification_flow`,
`uareg_modification`, `uchitta_natham_modification`, `f_line_service_urban`
or `tslr_sketch_urban`) still gets a correct details card, status, and
workflow history from the base register fields plus the universal
`application_workflow` table alone.

The single-turn case always worked; three bugs needed a SECOND turn to show,
specifically one naming a non-ISD/NISD code:

* "Show applications for service code 0158." → "Exclude rejected
  applications." dropped the 0158 scope and answered from the WHOLE register
  (126 instead of 20) — `_register_listing` inherited a carried ward/block
  from `followup_ctx.filters` on a bare continuation, but never did the same
  for `service_code`. Fixed: same inheritance rule added for `service_code`.
* "Show applications for service code 0160." → "Also show the block number."
  misrouted entirely to `officer_directory` — proven NOT code-specific by
  reproducing it after an ISD listing too. Two-layer root cause: (1)
  `rag._WARD_DIRECTORY_RE`'s "show wards/blocks" alternative matched "show
  the block NUMBER" (its exclusion list had application/survey/pending/
  overdue but not "number"); (2) `officer_directory` sits in
  `fctx.SELF_CONTAINED_INTENTS`, so `chatbot._followup_is_self_contained()`
  took the wrong intent as proof of a fresh question and never even called
  `fctx.resolve()` — the correctly-returning `field_projections()` never got
  a chance to run. A narrower, same-family gap rode along:
  `_PROJECT_TRIGGER_RE` recognised "along"/"add"/"include" but not "also".
* "Which service code 0158 applications have no subdivision recorded?"
  (the Mode B standalone field-presence query) answered over the whole
  register instead of the 20 under 0158 — `_standalone_field_presence_answer`'s
  generic branch never called `_requested_service_code_filter`, unlike
  `_register_listing`. Fixing this surfaced a FOURTH, pre-existing defect:
  once a code reached where the asked field is not applicable to ANY row
  (subdivision, for a 0158 file), `_render_field_presence` asserted "All of
  the ... applications have a subdivision recorded" in the same sentence as
  "(20 not applicable)" — a NULL turned into a false positive. Fixed: the
  "nothing matched" branch now checks how many rows were actually
  determinable before asserting all/none.

A second pass over the paired-negation list turned up three more:

* "Show applications that are not service code 0158." was answered with
  exactly 0158's own 20 applications — the OPPOSITE of what was asked, not
  merely an omission. `_requested_service_code_filter` read the bare digits
  with no awareness of the "not" in front of them, and `neg_scope.py` (which
  already negates status/type/channel) has no concept of a service code at
  all. Fixed with a new `_excluded_service_code_filter()` (the same negation
  vocabulary `calc_qa._CC_OUT` already uses for count questions) and a new
  `exclude_service_code` parameter threaded through
  `get_all_service_code_applications` → `_register_listing` →
  `_render_all_service_code_answer`, carried forward across a continuation
  turn the same way an included code already was.
* "Show applications for service code 0158 without a recorded subdivision."
  (the imperative phrasing, not "which...have") fell through BOTH
  field-presence modes and silently dropped the field filter entirely. Fixed
  by widening `parse_standalone_field_presence_request()` to also accept the
  imperative shape — but the first version of this fix was ITSELF a
  regression, caught by the existing `test_sequence_c_table_ops.py` and
  `test_row_slicing.py` suites: a plain column-selection sentence ("show
  applications ... with application number, status, survey number,
  subdivision number...") also trivially contains "show...applications" + a
  bare "with" + the word "subdivision", and was misread as the same presence
  filter. Fixed by requiring the word "recorded"/"scheduled" as an anchor —
  present in every genuine field-presence phrasing, absent from an ordinary
  column list.
* "What service codes are there?" / "...exist?" / "...do you support?" fell
  through to the generic "what is `<field>`" fallback and came back "Please
  specify the application number..." — a register-wide catalog question
  answered as if it named one unspecified file. Routed to the existing
  catalog answer (`service_code_guide`) instead.

`python test_servicecode_equal_treatment.py` (`--routing` skips the database).

Three more live tables fit the same pattern — real data, no mention anywhere
in this file, and no evident ORM/query/intent wiring found so far:
`correction_modification_flow` (72 rows, a much longer multi-desk chain than
documented — SIS → ZDT → SD → DIS → Tahsildar → RDO → Collector/DRO → COSS →
CLA, none of which besides SIS/SD/DIS/Tahsildar appear elsewhere in this file),
`uareg_modification` / `uchitta_natham_modification` (134 / 121 rows, modify
operations `01`–`06` on the survey and natham-chitta registers), and
`tslr_sketch_urban` / `chitta_temp_old_owner` / `sd_attachment` /
`file_attachment`. Whether any of these already have handlers elsewhere in
`chatbot.py` has not been checked; this note only records that they exist and
are unexplained here.

**Layer 2 — the app's ORM tables** (`backend/models.py`), a projection built from layer 1 by `build_app_tables.py`:

| Sample table | App table |
|---|---|
| `uareg_demo` | `towns` → `wards` → `blocks`, `survey_numbers`, `sub_divisions` |
| `uchitta_natham_demo` | `owners`, `survey_ownership` |
| `appl_log_urban_demo` | `applications` (+ `applicants`, `application_documents`) |
| `application_workflow_demo` | `workflow_history`, `field_visits` |
| `areg_temp_subdivclub_demo` | `application_sub_divisions` |
| `nisd_/sub_div_patta_transfer_urban_demo` | `patta_transfers` |
| workflow usernames at role 41 | `sis_officers`, `officer_jurisdictions` |

Rules that follow from this split:

- **The chatbot queries only layer 2.** `backend/services/postgres.py` goes through the ORM models — never against the CSV-shaped tables directly.
- Only service codes `0153` (NISD) and `0154` (ISD) become `applications` — `ck_application_type` admits no others, so settlement and govt-to-private rows (`0167`, `0169`, …) stay in layer 1 only.
- Rebuilding the projection is idempotent: it truncates what it owns and re-derives. `knowledge_embeddings` is left alone.
- `knowledge_embeddings` lives in the same database: 768-dim vectors (`nomic-embed-text`), HNSW index, cosine similarity.

### Layer 0 — the TAMILNILAM master tables

`backend/sample_table/` also holds five `pg_dump` files, loaded verbatim by
`python -m backend.sample_db.load_master_dumps`:

| dump | table | rows | key |
|---|---|---|---|
| `district.sql` | `district_unicode` | 36 | `district_code` |
| `taluk.sql` | `taluk` (+ `taluk_id_seq`) | 132 | `(district_code, taluk_code)` |
| `townmaster.sql` | `town` | 216 | `(district, taluk, town)` |
| `wardmaster.sql` | `ward` | 1073 | `(district, taluk, town, ward)` |
| `blockmaster.sql` | `block` | 30697 | `(district, taluk, town, ward, block)` |

These are the department's **statewide** geography masters, keyed by natural
code.

**`district_unicode` and `taluk` ARE the app's district and taluk tables.** The
app used to keep its own `districts` and `taluks` beside them holding the same
two places; column for column those carried nothing the masters do not -- only
`district_code` + name and `taluk_code` + name -- so they were dropped by
`adopt_master_district_taluk.py`. Their one real contribution was the surrogate
UUID identity that ~65 `District.id` / `Taluk.id` / `Taluk.district_id` /
`Town.taluk_id` references and every foreign key are built on, so that identity
was added TO the masters instead:

| master | added column | meaning |
|---|---|---|
| `district_unicode` | `app_uid` | `District.id` |
| `taluk` | `app_uid`, `district_uid` | `Taluk.id`, `Taluk.district_id` |

`app_uid` is `md5` of the natural key, so it is **deterministic**: reloading a
dump regenerates the same UUIDs and `towns.taluk_id` /
`officer_jurisdictions.*` keep resolving. `load_master_dumps.py` re-applies the
column and the foreign keys after every reload, so one command leaves the
database consistent.

`towns`, `wards` and `blocks` still have tables of their own; the masters
supply their **names** (below). Nothing lists districts or taluks unscoped --
every read is `WHERE District.id = ...` through the officer's jurisdiction --
which is why growing them to 36 and 132 rows changes no answer.

`build_app_tables.py` reads them through `load_master_names()` and takes
`towns.name`, `wards.ward_name` and `blocks.block_name` from the English
columns (`town_ename`, `ward_ename`, `block_ename`, each stripped of CHAR
padding). District and taluk need no copying -- they are read in place.
Before this, those names were invented — `f"Ward {int(wd)}"`, `f"Block
{int(bl)}"`, and a taluk that was given its *district's* name because the
script had nothing better. A level the masters do not cover falls back to the
old synthesised name, so the build still runs on a database where the dumps
were never loaded; the run prints which it used.

Two things the loader deliberately skips, and reports:
the dumps' `GRANT`s to roles from the source system (`temple`, `igrs`, `clap`,
`web_anon`, `authenticator`, `ultuser`, `postgrest_auth`, `murugesh`), none of
which exist here; and `wardmaster.sql`'s `ward_to_hist` trigger, whose function
`public.ward_to_history()` is not in the dump — creating it would make every
write to `ward` fail.

**Codes, not names, are what the code matches on.** A listing table renders
`ward_number` / `block_number` (`102`, `0015`), which is what the "in this
ward" back-reference in `_geo_scope()` parses; only the single-application
detail card and `get_survey_detail()` show the human name. Changing a master
name is therefore safe; changing a code is not.

### The seeded jurisdiction

One urban jurisdiction, matching the extracts: **Thoothukudi (district `28`)**, taluk `01`, town `001`, block `0015`, wards `002` / `102` / `103`, streets `0001`–`0008`. Survey numbers run in a 13xx series in ward 002 and low series in wards 102/103; each carries a patta number. Officers hold the wards that actually carry applications (`002` / `102` / `103` — the parcel register also covers `004`, which has no applications), so jurisdiction filtering has real effect in tests and every application is assigned to an officer who covers its ward.

### Dates

Open applications (`pending` / `in_progress` / `escalated`) are dated **relative to the day the seed ran**, so "overdue by N days" stays believable. Closed applications (`approved` / `rejected`) keep the full 2022–2026 spread. Nothing is dated in the future — the workflow chain stops at today.

---

## Architecture & Request Flow

```
POST /api/v1/chat/stream
        ↓
  chat.py router
        ↓
  chatbot.py → process_chat_stream()   ← MAIN ENTRY POINT
        ↓
  rag.py → parse_intent()              ← classify the user message
        ↓
  postgres.py → <query handler>()      ← fetch structured DB data
        ↓
  rag.py → call_llama_stream()         ← stream LLM response with context
        ↓
  SSE stream → frontend
```

### Intent Priority Order (in `rag.py`)

`parse_intent(message, prev_intent=None)` resolves ~60 intents by exact token-boundary matching plus edit-distance typo matching (never arbitrary substrings). `prev_intent` disambiguates follow-up filter phrases like "only isd". Rough order:

1. `greeting` — "Hello", "hi there", "good morning", "வணக்கம்", and the
   sign-offs too: there is **no separate `farewell` intent**. Matching is
   token-based, not an exact-phrase list — after the politeness filler is
   dropped, a message whose every word is a greeting / farewell / small-talk
   word is a greeting. (The old exact list missed "hi there", "thanks a lot",
   "bye bye", "see you", "good night" and "romba nandri" — 25 of 46 ordinary
   openings and sign-offs — which then cost a 20-45 second LLM call apiece to
   answer a message with no question in it.) The handler in `chatbot.py` tells
   the three cases apart: `_greeting_is_farewell` answers a sign-off with a
   sign-off (it used to answer "bye" with *"Hello! ... What can I assist you
   with today?"*), `_greeting_is_thanks` covers "thanks" **and** `nandri` /
   `nanri`, and everything else gets the hello.
2. Deterministic identifiers — `application_status` ("Status of 2025/0154/28/000001"), `survey_detail`, `can_number_info`
3. `last_application` — "my previous application", "my last approved application", "is my last application rejected?", "what was the area of my last rejected application" (no number given; resolved from the officer's own history, most recent workflow action first). A status word is a filter before the noun ("my last APPROVED application") and a yes/no question after it ("is my last application APPROVED?"). One field asked in the same breath — area, applicant, mobile, address, fee, patta, CAN, sub-divisions, survey, deed, reason, submission date, channel — is answered from the record that lookup already returned.
4. Per-application checks — `joint_owner_check`, `check_documents`, `check_sale_deed`, `is_nisd_or_isd`, `litigation_check`
5. Workload / listing — `pending_applications`, `overdue_applications`, `officer_workload`, `isd_applications`, `nisd_applications`, `jurisdiction_summary`
6. Field-visit family (`fv_*`) — scheduling, rescheduling, conflicts, overdue inspections, and `fv_visit_plan` for "which application should I field visit tomorrow / next week, and in which block?" (answers with the overdue visits first, then the applications with no visit booked, each with its ward and block — never just "nothing is scheduled")
7. Sub-division desk family (`sd_*`) — sketch readiness, encroachment, forwarding, remarks
8. Reference lookups — `service_code_lookup`, `service_code_guide`,
   `rejection_info`. (`sub_registrar` is **not** an intent — it is a value of
   `submission_channel`, returned by `extract_submission_channel()`. A question
   about the office itself, "who is the sub registrar" / "what does the SRO
   do", is answered by `igrs_can_rule`'s `sro_what` topic; before that it was
   claimed by `sale_deed_check`, which replied "Please specify the application
   number you are asking about" to a question that names no application.)
   `service_code_lookup` also answers the type words: "what is ISD?" / "what is
   NISD?" are service codes 0154 / 0153 and are looked up, not generated —
   left to llama3.1:8b, "what is NISD" came back inventing an application
   number format (`NISD/DISTRICT_CODE/YEAR`) this register has never used. A
   bare "difference between ISD and NISD" is a definition and goes to
   `service_code_guide`; "ISD vs NISD" and "compare ISD and NISD" stay counts.
9. `compare_applications` — seven shapes, all answered from the register:

   | kind | question | answer |
   |---|---|---|
   | `applications` | "which is older, A or B" | both files side by side, plus the verdict |
   | `type` / `status` / `channel` | "ISD vs NISD" | a count each way, and the gap |
   | (the same, by time) | "do ISD take longer than NISD" | mean days to decide each way |
   | `ward` | "compare ward 102 and ward 103" | a count each way |
   | `period` | "compare this month and last month" | applications filed in each |
   | `month` | "which month had the most" | the busiest / quietest month |
   | `superlative` | "which took the longest to approve" | that file, with the median beside it |
   | `average_duration` | "average time to approve" | mean, median, fastest, slowest |

   `parse_comparison_query()` in `rag.py` names the sides and the quality;
   `get_comparison()` counts them from the ORM tables; `_comparison_answer()`
   states the verdict. Boundaries that took work to get right:
   - "is there a **fee** difference between ISD and NISD" asks the fee
     schedule → `service_code_guide`, never a count of the officer's files.
   - "which has been **pending** the longest" measures waiting →
     `pending_longest`, not time-to-decision.
   - "which **ward is** 2022/… **in**" is a field lookup → an application
     number rules the ward and month branches out entirely.
   - "how long did X **take**" on a decided file asks the turnaround, not
     whether it is overdue — `_DURATION_RE` in `chatbot.py` steers it.
   - `_compare_periods()` keeps the two periods **apart**;
     `extract_month_scopes()` deliberately merges contiguous months, which is
     right for "June and July applications" and wrong for "compare June and
     July".
   - A duration superlative and the busiest month both break ties on a stable
     key, so the same question names the same file or month every time, and
     the superlative says how many others tied.

   Matching is **token-based and typo-tolerant** — every keyword goes through
   `is_token_typo_match`, the same edit-distance rule the rest of
   `parse_intent` uses, so "compair", "nsid", "approvd" and "longst" all land
   where they should. Two consequences worth knowing:
   - `isd` is three letters, so `_max_edits_for` gives it **no** typo budget.
     That is deliberate: it stops `isd` absorbing `nisd`.
   - `_NEVER_TYPO` in `rag.py` holds words that must only ever match exactly.
     `last` is one edit from `least`: unguarded it made "what about last
     month" a superlative and "the least time" an age question.
   - The application number is stripped from the message before group sides are
     read, because `0153` / `0154` inside it are the NISD / ISD tokens —
     left in, "compare 2026/0153/28/001190 and 9999" answered as ISD vs NISD.
10. `general_query` — falls back to the **agent layer** (tool calling over
    the authorized domain tools, see below), and to a plain RAG / pgvector
    prompt if the agent cannot run

Full list: `python -m backend.sample_db.test_intent_coverage` routes one question per intent and reports misroutes without touching the DB or the LLM.

### Language Detection

Handled in `rag.py → detect_language()`:
- **Tamil**: Unicode range U+0B80–U+0BFF detection
- **Tanglish**: Phonetic patterns (e.g., "vanakkam", "enna")
- **English**: Default fallback

---

## Key Domain Concepts

### Citizen Identifiers

`backend/sample_db/identifiers.py` holds both formats, shared by the seed and
the ORM projection:

- **Aadhaar** — synthetic, 12 digits, leading digit 2-9, valid Verhoeff check
  digit, derived from the person's name so one person keeps one number across
  every extract and across both layers.
- **CAN** (Citizen Access Number) — the length identifies the **counter that
  issued it**, not the channel that filed the application: **15 digits** from a
  Common Service Centre / CSC counter (`133` series), **12 digits** from the
  TN portal. `CAN_LENGTHS` bounds what each channel may carry (`CSC` 15,
  `sub_registrar` 12, `citizen` either) and is enforced when `applications` is
  projected; layer 1 keeps the extract's value verbatim. The channel itself
  comes from `source_name` + `camp_flag` — see **Submission channels** below.

`python -m backend.sample_db.verify_identifiers` re-checks both in the built DB.

### Submission channels

`applications.submission_channel` is derived by `can_channel()` in
`identifiers.py` from two columns of `appl_log_urban_demo`. **The rule (set by
the domain owner):**

| channel | `source_name` | `camp_flag` | seeded apps |
|---|---|---|---|
| `sub_registrar` | `-` | -- | 93 |
| `citizen` | present | `P` | 2 (`2024/0154/28/001397` rejected, `2022/0153/28/001405` approved) |
| `CSC` | present | anything else | 114 |

A mobile-shaped `source_name` is **not** a signal on its own. The CAN's length
names the counter that issued it (15 digits CSC counter / 12 TN portal) and does
not decide the channel; `CAN_LENGTHS` bounds what each channel may carry
(`CSC` 15, `sub_registrar` 12, `citizen` either). `source_name` and `camp_flag`
are carried into `applications` as `submission_source_name` /
`submission_camp_flag`, and `_render_submission_channel_basis()` in `chatbot.py`
shows the derivation.

Only one of the two citizen files is approved, so the citizen list is short;
"No applications found" for a channel is the data, and `empty_note` says so.
A channel scopes the question like a period does: it suppresses the
current-stage pin and the active-status default.

Every unattended row carries an `igrs_form6_number` equal to its CAN; no CSC or
citizen row has one, so an empty IGRS field there is the rule, not a gap.

`test_channel_queries` step 2b checks the ruling and cross-checks the other
evidence (internal `10.236.251.x` IP, IGRS number, `133`-series CAN) for the SRO
and CSC rows; citizen rows are exempt from the CAN-series check, since a camp
file carries whichever counter's number. Known artefact: `2023/0153/28/000327`
has a placeholder CAN.

`appl_log_urban_demo.source_code` carries no channel signal; do not use it.

### Application Types
- **ISD** (`0154`) — **Involving Sub-Division**: the parcel is split, so the file
  needs a field inspection and an SD sketch.
- **NISD** (`0153`) — **Not Involving Sub-Division**: a straight patta transfer of
  the whole survey number, no new sub-division and no field visit.

An ISD parcel carries two sub-division numbers, both projected into
`application_sub_divisions`: the temporary `{subdiv}/T{seq}` one it runs under
(`temporary_sub_division_no`, e.g. `3/T1`) and the final one assigned on
approval (`proposed_sub_division_no`, e.g. `4` — it holds the temporary number
while the file is open or was rejected, since no final number exists then).
`get_application_detail()` returns both as `temporary_subdivision_number` /
`final_subdivision_number`. `python test_temp_subdivision_queries.py` checks the
CSV, both layers and the answers.

### Officer Hierarchy
- **Block SIS** → narrowest jurisdiction
- **Ward SIS** → ward-level
- **Taluk SIS** → taluk-level
- **District SIS** → broadest jurisdiction

### Application Statuses
`pending` → `in_progress` → `escalated` → `approved` / `rejected`

What the extracts actually carry, via `appl_log_urban_demo.application_status`
cross-checked against the wording in the transfer extracts and against how each
workflow chain ends:

| code | `workflow_state` | wording in the transfer extract | projected status |
|---|---|---|---|
| `01` | `C` | Approved By ZDT/HQDT, Order Generated | `approved` |
| `02` | `C` | Rejected By ZDT/HQDT, Rejected | `rejected` |
| `03` | `P` | Send to SIS → `pending`; Forward To ZDT → `in_progress` | open |
| `05` | `C` | Rejected By ZDT/HQDT, Rejected | `rejected` |

Nothing in the extracts marks an application `escalated`, so the seeded database
has none. The current split is 150 approved, 52 rejected, 5 pending,
2 in progress.

### Workflow

**ISD** (`0154`, Involving Sub-Division) — the full chain:

```
application  (CSC operator / citizen portal / Sub-Registrar referral)
   → SIS        Sub Inspector Surveyor — mandatory field inspection & cadastral verification
   → SD         Senior Draughtsman — prepares the sub-division sketch
   → DIS        Deputy Inspector Surveyor — reviews sketch + field report, approves or rejects
   → Tahsildar  holds the Digital Signature Certificate (DSC) key; applies it to
                approve and generate the patta transfer order
```

**NISD** (`0153`, Not Involving Sub-Division) is shorter — no field visit, no
SD sketch, no DIS:

```
application  (CSC operator / citizen portal / Sub-Registrar referral)
   → SIS                    Sub Inspector Surveyor — document verification only
   → Zonal Level Tahsildar  holds the DSC key; applies it to approve and generate
                            the patta transfer order   (role 16; `TAHSILDAR` stage)
```

**There is no FMB (Field Measurement Book) anywhere in this data**, so nothing
in the code should mention one. Checked in both layers and in the extracts: no
column, value or document carries an FMB book number, page number or sketch.
The only thing that comes close is `sub_div_patta_transfer_urban_demo.sketch_sent_date`
(31 of 50 rows) and `sketch_received_date` (**0** of 50) in layer 1 — a date the
sub-division sketch went to the Senior Draughtsman, never a returned sketch, and
neither column is projected into the ORM tables, so the chatbot cannot see even
that. The FMB references that used to sit in `workflow_guide.txt`,
`faq_english.txt` and `rag.py`'s concept list were removed for this reason: they
made the assistant discuss a record the department's data does not hold. Do not
re-add FMB to the corpus or the prompts unless an extract starts carrying it.

Each hop is a row in `application_workflow_demo` (layer 1), projected into
`workflow_history` (layer 2) by `build_app_tables.py` through `ROLE_TO_STAGE`.
`workflow_history.performed_at` comes from `last_updated_datetime`, not
`action_date`: a file often clears three desks in one day, and dating the hops
to the day alone loses their order.

### When a completed application was decided

`applications` carries a `submission_date` but **no decision date column**. The
date a file was approved or rejected is the `performed_at` of the
`workflow_history` hop that closed it -- the one whose `to_stage` is
`COMPLETED` or `REJECTED`. All 202 completed applications have exactly one, and
its stage always agrees with `current_status`.

Do **not** use `patta_transfers.tahsildar_signature_date` for this: 13 completed
applications carry none, and 21 carry a date that contradicts the workflow chain
(some are deed dates as old as 2013).

`get_application_detail()` exposes it as `decision_date` (plus `decision_stage`,
`decision_by`, and the `approval_date` / `rejection_date` aliases, whichever
applies). It is `None` while the file is open -- the submission date is never a
fallback, since answering the filing date to "when was this approved" is a wrong
answer, not a vague one. `_asked_decision_date()` in `chatbot.py` routes those
questions to `_decision_date_answer()` ahead of the field map, which otherwise
sends every "when" and every "date" to `submission_date`.

### Workflow Roles

The role ids in `application_workflow_demo`, read off the data rather than
assumed — the applications whose wording says "Send to SIS" are sitting at role
44 or 41, and 42 shares its actors with 44:

| role | who | stage |
|---|---|---|
| `1` | the CSC operator or citizen who submits | not a desk (no `from_stage`) |
| `44`, `42`, `41` | the surveyor's office (SIS) | `SIS` |
| `8` | Senior Draughtsman | `SD` |
| `12` | Deputy Inspector Surveyor (DIS) | `DIS` |
| `16` | Zonal Level Tahsildar (ZDT) — holds the DSC, approves and generates the order | `TAHSILDAR` |
| `59`, `53` | higher revenue desks (ZDT / DRO) | `TAHSILDAR` |

**How the seeded ISD applications actually flow (they do NOT complete the chain
above).** 41 ISD applications: 21 approved, 16 rejected, 4 pending. Their
`workflow_history` stage-paths:

| path | count |
|---|---|
| `SIS → SD → COMPLETED` | 20 |
| `SIS → SD → REJECTED` | 8 |
| `SIS → REJECTED` | 7 |
| `SIS` only (pending) | 3 |
| other | 3 |

- `application → SIS` ✅ (role `1 → 44`, then intake `44 → 42 → 41`)
- `SIS → SD` ✅ — every non-pending ISD file goes through SD (`41 → 8`); 29 of 41
- `SD → DIS` ❌ **missing.** No ISD application has a `DIS` hop — role `12` and
  the `8 → 12 → 59 → 53` tail exist in layer 1 only for settlement /
  govt-to-private service codes (`0167`, `0169`, …) that never become
  `applications`.
- `DIS → Tahsildar` ❌ **missing.** No ISD application reaches a `TAHSILDAR`
  stage; the DSC-approval step is collapsed — `SD → COMPLETED` is the terminal
  hop. (The 167 `TAHSILDAR` rows in `workflow_history` are all NISD, via role
  `16`.)

So ISD in the seed = `SIS → SD → COMPLETED/REJECTED`. `workflow_history` never
carries `DIS`, and the `DIS` entry in `chatbot.py`'s `_stage_labels` is
unreachable from data. To make ISD follow the full chain, `build_app_tables.py`
would need to synthesise the missing `8 → 12` (SD→DIS) and `12 → 16`
(DIS→Tahsildar) hops when projecting `workflow_history` for `0154`.

**How the seeded NISD applications actually flow — they DO follow the chain
above.** 168 NISD applications: 129 approved, 36 rejected, 2 in progress,
1 pending. `workflow_history` stage-paths:

| path | count |
|---|---|
| `SIS → TAHSILDAR → COMPLETED` | 87 |
| `SIS → SIS → TAHSILDAR → COMPLETED` | 40 |
| `SIS → TAHSILDAR → REJECTED` | 31 |
| `SIS → SIS → TAHSILDAR → REJECTED` | 7 |
| `SIS → TAHSILDAR` (in progress) | 2 |
| `SIS` only (pending) | 1 |

- `application → SIS` ✅ — 168 of 168 (role `1 → 44`, then intake `44 → 42`)
- `SIS → Zonal Level Tahsildar` ✅ — 167 of 168 reach the `TAHSILDAR` stage
  (role `42 → 16` / `44 → 16`); the one exception is the single still-`pending`
  file. No NISD file touches `SD` or `DIS` — correct for NISD.
- `Zonal Level Tahsildar → approve` ✅ — `TAHSILDAR → COMPLETED` (127) or
  `TAHSILDAR → REJECTED` (38); role `16 → 0` closes the chain.

Raw layer-1 for approved NISD `2022/0153/28/000254`:
`1→44, 44→42, 42→16, 16→0` — surveyor's office straight to the Zonal Level
Tahsildar (role 16), who signs and closes it.

### Time limits — two different clocks

`backend/utils/sla.py` holds both, taken from the documents; `python test_sla_rules.py`
checks the code against `land_rules.txt` / `workflow_guide.txt` and every boundary.

| clock | rule | source |
|---|---|---|
| **Field-visit deadline** | an open ISD file whose visit is not completed more than **15 working days** after submission is *overdue* (`applications.is_overdue`); a completed visit stops the clock; NISD has none | `workflow_guide.txt` |
| **Service SLA** | NISD 15-20, ISD 30-35 working days from submission to completion | `land_rules.txt` |

The SLA is a **range**, so a file is *within* it up to the lower figure, *in the SLA
window* between the two, and *past* it only after the UPPER figure -- no single point is
invented. Working days are Monday-Friday; the register has no holiday calendar and the
answer says so. `is_overdue` is re-derived by `overdue_refresh.py` at start-up and every
6 hours (it used to be frozen on the build day). The answer to "how long has X been
pending" names both limits and never a universal "15-day SLA". There is no MERGE service code (`0155`) in the register -- `appl_log_urban_demo` has no row carrying it, so `applications.application_type` admits only `ISD`/`NISD` (`ck_application_type`); a MERGE application number, listing or fee lookup was removed from the code, the documents and the LoRA question banks for that reason.

### Active applications per survey number

**The rule: mutation on a survey number is a synchronous process.** While one
application on a survey number is live, no other application may be filed on it
-- and that holds *across sub-divisions*, because the parcel's record as a whole
is what is being worked on. A sub-division does not get its own slot.

`check_survey_application_lock()` in `postgres.py` answers this for a survey
reference the officer types ("5", "5/4A"); the `can_apply_check` intent renders
it. The sub-division in the reference is echoed back but never narrows the
check.

```sql
-- Kept as a plain index rather than UNIQUE: the seeded extract violates the
-- rule (survey 5 in ward 103 carries three concurrently active applications --
-- 2026/0153/28/001876, 2026/0154/28/001280, 2026/0154/28/001281), so a UNIQUE
-- index cannot be built without rewriting real statuses. The rule is therefore
-- enforced at the query layer, not by the schema.
CREATE INDEX idx_active_app_per_survey
ON applications (survey_number_id)
WHERE current_status IN ('pending', 'in_progress', 'escalated');
```
`build_app_tables.py` drops the older UNIQUE version of this index if the
database still carries it.

**All queries exclude `rejected` applications** to prevent ghost data appearing in lists.

---

## Coding Conventions

### Python / Backend

- **Async everywhere**: All DB operations use `async with session` + `await`. Never block the event loop.
- **UUID primary keys**: All models use `UUID(as_uuid=True)` with `default=uuid.uuid4`.
- **Timezone-aware timestamps**: Always use `TIMESTAMP(timezone=True)` and `datetime.now(timezone.utc)`. Never use `datetime.utcnow()` (deprecated in Python 3.12+).
- **Standard responses**: All API endpoints return `StandardResponse` from `schemas.py` — use `StandardResponse.success_response()` / `StandardResponse.error_response()`.
- **Logging**: Use `get_logger(__name__)` from `backend.utils.logger` (structlog-based). Do not use `print()` in service/router code.
- **Settings**: Import from `backend.config import settings`. Never hardcode secrets or URLs.
- **Windows UTF-8**: `main.py` reconfigures stdout/stderr to UTF-8 on Windows — required for emoji print statements on startup. Keep this guard in place.

### Service Layer

- **`chatbot.py`** is the single orchestration layer — all chat logic flows through it. It is large (~29000 lines) by design; new intent handlers belong here or in `postgres.py`.
- **`postgres.py`** contains *only* database query functions. No LLM calls, no intent logic.
- **`rag.py`** contains *only* NLP utilities: intent detection, language detection, extraction helpers, LLM calls, and prompt builders.
- Numeric/count data **always** comes from the database directly. Never let the LLM generate counts or application numbers — this prevents hallucination.

### Database / Models

- All models inherit from `Base` (imported from `backend.database`).
- Use `select()` + `await session.execute()` pattern (not `session.query()`).
- Geography hierarchy: `District → Taluk → Town → Block/Ward → SurveyNumber`.
- JSONB columns (`structured_data` on `ChatMessage`, `new_values` on `AuditLog`) store structured context for auditability.

### Frontend

- Vanilla JS only — no frameworks, no npm.
- Chat uses **SSE (Server-Sent Events)** via `EventSource` for streaming.
- All API calls include `Authorization: Bearer <token>` header (JWT stored in `sessionStorage`).

---

## Environment Variables (`.env`)

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | Async URL: `postgresql+asyncpg://user:pass@host/db` |
| `SYNC_DATABASE_URL` | Sync URL: `postgresql://user:pass@host/db` |
| `SECRET_KEY` | JWT signing key |
| `ALGORITHM` | JWT algo (default: `HS256`) |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Default: `480` (8 hours) |
| `OLLAMA_BASE_URL` | Default: `http://localhost:11434` |
| `LLM_MODEL` | Default: `llama3.1:8b` |
| `EMBEDDING_MODEL` | Default: `nomic-embed-text` |
| `ENVIRONMENT` | `development` or `production` |
| `CORS_ORIGINS` | JSON array of allowed origins |

---

## Common Gotchas

1. **Ollama must be running** before starting the backend. Check with `ollama list`. Pull missing models:
   ```bash
   ollama pull llama3.1:8b
   ollama pull nomic-embed-text
   ```

2. **pgvector extension** must be enabled in PostgreSQL before `ingest.py`:
   ```sql
   CREATE EXTENSION IF NOT EXISTS vector;
   ```

3. **`chatbot.py` is very large** — use IDE symbol search to navigate. Key entry points:
   - `process_chat_stream()` — streaming chat
   - `process_chat()` — non-streaming chat
   - `create_chat_session()` — session creation
   - `extract_month_from_query()` — month extraction with fuzzy matching

4. **Abbreviated query forms** are all supported by intent detection:
   ```
   "show app" / "show appl" / "show applications" → pending_applications intent
   ```

5. **Follow-ups that point back at the previous message.** Two shapes, both
   resolved before the LLM is reached:
   - *Singular* — "when was **it** approved / rejected / submitted". These name
     an **event**, not one of `rag.py`'s field nouns, so the field-keyword rule
     missed them and they fell through to the LLM, even though "which block is
     it from" resolved the same reference. Routed to `application_status` by
     the event + when + back-reference rule, which then resolves the number
     from history as usual.
   - *List* — "how many of **them** are approved", "which of them are ISD",
     "do they have an IGRS number", and the pronoun-less "how many are
     approved". `_rescope_list_followup()` in `chatbot.py` folds these back
     into the question they continue — the same mechanism as the bare-date
     re-scope beside it — so the channel / status / type scope survives
     instead of resetting to the officer's whole open queue. A plural pronoun
     is proof enough on its own; without one the fragment must name no subject
     of its own, so "how many ISD applications do I have" stays a fresh
     question.

   A yes/no about the list gets its answer in words above the table, not just
   the table: `_igrs_over_list_note()` answers "do they have an IGRS number?"
   from the channel of each row.

6. **Application number extraction** follows a fallback chain:
   - Explicit: `"APP-2024-000001"` → matched directly
   - Reference: `"this application"` → checks last 2 messages in history
   - Field query: `"what is the name?"` → checks conversation context
   - No reference found → chatbot asks user to specify

7. **CORS_ORIGINS** in `.env` must be a valid JSON array string, e.g.:
   ```
   CORS_ORIGINS=["http://localhost:3000","http://127.0.0.1:5500"]
   ```
   The `Settings` validator parses it from string automatically.

8. **Windows stdout encoding**: If you see `UnicodeEncodeError` on startup, ensure the UTF-8 reconfiguration block at the top of `main.py` is intact.

9. **Month filtering** uses fuzzy token matching (`backend/utils/fuzzy.py`) — handles spelling errors like `"jaunary"` → January. Do not replace this with naive string comparison.

---

---

## Implicit follow-ups — the reference context

`backend/services/followup_context.py`, plus `_load_followup_context()` /
`_scoped_list_answer()` in `chatbot.py` and `get_applications_by_numbers()` in
`postgres.py`.

**The gap it closes.** An officer rarely repeats themselves. After "show my
pending ISD applications" they ask "which one is oldest?"; after "show the
field visit for 2026/0154/28/001280" they ask "when was it scheduled?". Neither
carries a pronoun, an application number or a scope. Explicit-pronoun
follow-ups ("how many of **them** are approved") were already handled by
`_rescope_list_followup`; the **pronoun-less** ones were not, and they failed
in ways that read like confident answers:

| follow-up | was | now |
|---|---|---|
| "when was it scheduled?" after a field-visit answer | the application's **submission date** | the visit's scheduled date |
| "when was approved?" after a status answer | "not on record" | the decision date from the closing workflow hop |
| "show only NISD" after a June listing | the whole open NISD queue (June dropped) | the NISD rows of that June list |
| "what is the total fee?" after a 34-row listing | the fee over all 50 applications | ₹7,700 over those 34 |
| "which is oldest?" after a listing | a re-sorted table, no verdict | the oldest of the rows shown |

**Root cause.** `ChatMessage.structured_data` existed for exactly this and was
never written. Every follow-up recovered its referent by regex-scraping the
*rendered* previous answer and re-concatenating the previous user message.
That works for the shapes somebody coded and fails silently otherwise.

**How it works now.** Each deterministic answer records what it was *about*, as
data, in `chat_messages.structured_data`:

```
FollowupContext(entity=application | application_list | field_visit | survey,
                application_numbers=[...],   # verified, from the query result
                filters={...}, query_type=..., intent=...)
```

The next turn is resolved against that record **before intent routing**, so
every handler downstream sees a question that names its own subject. A
singular follow-up has the application number appended to the message (and
"field visit" too, when the question is about the visit) — deliberately the
whole mechanism, because every existing handler already knows how to answer a
question that names its application. A list follow-up is answered from
`get_applications_by_numbers()`, which re-reads **exactly the rows that were on
screen**.

**Four rules keep it from becoming a new hallucination surface:**

1. **Carrying a number forward is not a grant.** `get_applications_by_numbers()`
   re-checks every number against `get_jurisdiction_filter()`; one that is no
   longer the officer's is dropped, never returned. A refusal records no
   context at all, so "that application is outside your jurisdiction" leaves
   nothing behind for the next turn to pick up. Context is read only from the
   officer's own session.
2. **Never infer when more than one referent fits.** A singular question
   against a multi-row list asks which one, in Tamil for a Tamil turn. It never
   picks the first.
3. **Never claim a question that is already a question.** A message routing to
   a `SELF_CONTAINED_INTENTS` intent is left alone — "pending versus approved"
   and "average time to approve" are complete comparisons, short as they read.
   A plural pronoun is left to `_rescope_list_followup`, which already answers
   it. With no context at all this layer stands aside entirely rather than
   asking, because the handlers below already ask when they need a number.
   Each of those three rules exists because breaking it regressed a shipped
   suite.
4. **Every figure still comes from the database.** Counts, dates, fees and
   application numbers in a scoped answer come from the re-query, never from
   the previous answer's text.

Rejected applications stay out of a carried set unless the follow-up asks for
them by status, the same standing rule the other listings follow.

**Tamil, English and Tanglish.** The context is *data*, not text, so it
carries across a script change mid-conversation: an English listing followed by
"எது பழையது?" or "edhu pazhusu?" resolves the same way. Three things this took:

* **Tamil is matched as substrings, never with `\b`.** `என்\b` matched *inside*
  `என்ன` ("what") — the virama `்` is not a word character, so the regex found
  a boundary mid-word — which made every Tamil question ending in "…என்ன?" look
  like a fresh question naming its own subject. This is the same trap CLAUDE.md
  documents for the comparison parser; the own-subject test now uses
  `எனது` / `எனக்கு` / `விண்ணப்பங்க`, and deliberately not the stem `விண்ணப்ப`,
  which also begins `விண்ணப்பதாரர்` ("applicant").
* **Tanglish is a first-class input**, with the spellings officers actually
  type: `enna`, `eppo`, `yaaru`, `edhu`, `evlo` / `evvalavu`, `pazhusu`,
  `mattum`, `naal`. `evlo` sits in the *aggregate* pattern rather than the
  singular cues, because "evlo approved?" is a count over the list and the word
  "approved" alone would otherwise make it a one-record question.
* **The reply matches the officer's script.** A Tanglish turn gets Tamil, like
  everywhere else in the app (`is_tamil = language in ("ta", "tanglish")`) — an
  English clarification landing beside a Tamil-script answer read as two
  different assistants.

A scoped answer also names the set it covers ("20 of those 21 CSC
application(s) are approved"), and the scope word is read off the re-queried
rows — only claimed when every row agrees — so it is as verified as the count
beside it.

**"…of the previous question" is a data question, not a recall question.**
`_is_conversation_recall()` already refuses to claim a message that names a
data word ("status of my previous application"), but the list held only a
handful of nouns. "What is the CAN number of the previous question?" matched
none of them and was answered *"Your previous message was: …"* — a correct
answer to a question nobody asked. `_RECALL_DATA_WORDS` now carries the field
nouns (`can`, `name`, `ward`, `fee`, `date`, `igrs`, …) in English and Tamil.
`python test_can_context_fix.py` covers it.


`python test_followup_context.py` (~110 checks, no LLM); `--routing` skips the
database.

---

## Service codes — what a code means is a lookup, not a generation

`SIS_URBAN_SERVICES` / `describe_service_code()` / `service_code_one_liner()` in
`backend/utils/helpers.py`; `_service_code_lookup_answer()` in `chatbot.py`;
the `service_code_lookup` rule in `parse_intent`.
`python test_service_code_queries.py` (routing + answers, no LLM);
`--routing` skips the answers.

**"what is 0153?" was answered "The service code is 0153."** — the question
restated. A bare code carries none of the words the `service_code_guide` rule
looks for ("service code", "difference", "fee"), so it fell to `general_query`
and llama3.1:8b, which had nothing to look the code up in. All 30 official
TAMILNILAM urban codes are in `SIS_URBAN_SERVICES` with their names, so this is
a table lookup and is now answered without the model.

- **An exact code is explained in full.** For the three the register carries
  (`0153` / `0154`) that is the name, what it is, the workflow chain,
  the government + CSC fee and the SLA. For the other 27 it is the official
  name, whether the service needs a field visit, and the fact that **no
  application in the officer's register uses it** — the `applications` table
  admits only those three, so "you have no 0169 applications" is a fact about
  the schema, not an empty result.
- **A partial number is a prefix.** "how many service codes start with 016"
  lists the nine it matches. This rule sits *ahead* of `service_code_guide`,
  which claimed it on the word "how" alone and dumped the whole three-code
  table instead of counting.
- **An application number carries a code of its own.** `2026/**0154**/28/001280`
  contains `0154`, so the number is stripped before the message is read for
  codes — otherwise "difference between A and B" reads as an ISD-vs-NISD
  definition question. A code question that *names* a file ("which service code
  is 2026/0154/28/001280?") is a field lookup on that file, routed to
  `application_status`; the field answer now states the code **and** what it
  means on the next line, because a bare `0153` answers nothing on its own.
- **A number nobody labelled is not assumed to be a service code.** `0015` is
  a block number here; it could equally be a typo. "what is 0015?" routes to
  `unidentified_number`, whose answer is that the number was **not recognised**
  — with the shapes it could have been (service code, application number, ward,
  block, survey) — and no meaning attached to it. Deciding it must be a service
  code because it is four digits is the same guess the LLM was making, one step
  earlier. Only a value actually in `SIS_URBAN_SERVICES` is treated as a code.
- **Saying "service code" changes the question.** "what is **service code**
  0015" asserts what the number is, so "there is no urban service code 0015" is
  a real answer and is given, without dumping the 30-row table after it.
- Tamil and Tanglish are first-class here too ("0154 என்றால் என்ன", "0154
  endral enna").

## IGRS and CAN questions

`python test_igrs_can_queries.py` (37 checks, no LLM). It recomputes every
expectation from the register, so it still means something after a reseed.

**An IGRS question is a Sub-Registrar question.** Only a Sub-Registrar referral
carries an `igrs_form6_number` — 93 of 93, and none of the 115 CSC or the one
citizen file — so `extract_submission_channel()` reads "which of my
applications have an IGRS number" as that channel. Without it the question fell
through to the officer's open desk queue and answered *"all 1 carry an IGRS
number"* to an officer holding 20 of them, because the current-stage pin
applies to an unscoped listing. The rule is skipped when the message names an
application: "does 2022/0154/28/000156 have an IGRS number" is a question about
one file, not a scope. An explicit channel in the same breath still wins, so
"show applications from CSC" → "do they have an IGRS number" stays CSC.

**A CAN answer names the counter that issued it, and the channel separately.**
The short "just the number" reply used to give the digits alone, which invites
exactly the reading CLAUDE.md warns against — that the *length* is the channel.
It is not. A 12-digit CAN means the TN citizen portal issued the number; the
channel is a separate fact derived from `source_name` + `camp_flag`, and the
two genuinely differ: `2022/0154/28/000156` carries a 12-digit portal-issued
CAN on a file that arrived as a Sub-Registrar referral. Both are now stated.

**Tamil needs a bare `igrs` keyword.** The field map held only English phrases
(`igrs number`, `igrs form 6`), so "IGRS எண் என்ன?" — which puts the Tamil word
for "number" after IGRS — matched nothing and answered "I could not find that
particular detail" for a file that carries one.

An absent IGRS is still explained rather than reported as a gap
(`_missing_field_answer()`), and a list answers in words above the table
(`_igrs_over_list_note()`): "No — none of these 31 carries an IGRS Form 6
number. Only a Sub-Registrar referral is given one."

**The rule asked AS a rule.** "If the IGRS number is absent, what does it mean
— so it's not from SRO?" names no application, and answering it with a table of
applications answers a question nobody asked. That is what happened once an
IGRS question was read as a Sub-Registrar channel filter, and the LLM fallback
was no better: **"SRO" appeared nowhere in the corpus**, so "what is SRO" came
back as *"the query was refused, which means it is outside the jurisdiction of
the officer"* — not even the right kind of answer. Fixed in three places:

* `_igrs_can_rule_topic()` / `_igrs_can_rule_answer()` in `chatbot.py` answer
  six rule topics deterministically (`igrs_absent`, `igrs_who`,
  `igrs_equals_can`, `can_length`, `can_what`, `sro_what`), in English and
  Tamil. These are documented invariants, not counts, so they are stated in
  Python rather than left to the model.
* `land_rules.txt` now spells out SRO, defines the CAN, and states the
  **contrapositive** the corpus never had: *no IGRS number ⇒ the file did not
  come from the SRO*. `faq_english.txt` carries the same two questions in the
  officer's own words. Both are the top pgvector hits after `python -m
  backend.ingest`, so the LLM fallback is grounded even on a phrasing the
  deterministic handler misses.
* The article carries the whole distinction between the two readings: "what is
  **a** CAN number" is a definition, "what is **the** CAN number" is this
  application's — and the second is the commonest follow-up there is. A bare
  `what is` claimed both until `_RULE_DEFN_RE` split them.

**Three more, found by asking for the CAN numbers of a list:**

* **A CAN column printed `N/A` for every row.** The renderer reads
  `can_number` off each row, and the list builders in
  `get_officer_applications()` / `get_applications_by_numbers()` never set it —
  so the table asserted "N/A" about data that is on record, which is worse than
  leaving the column out. `can_number` and `igrs_form6_number` are now carried
  on the list rows.
* **`"how"` is a substring of `"sHOW"`.** The CAN-guide cues were matched
  unbounded, so "show my applications with their can numbers" — a request for
  the officer's own list — was answered with the static guide, while "list the
  can numbers of my applications" went to the listing. One request, two
  answers, depending only on the verb. The cues are token-bounded now, the
  rule the rest of `parse_intent` already follows.
* **The guide left out the one fact it needed.** `number_format` — "15 digits
  (133 series) from a CSC counter, 12 from the TN portal; a Sub-Registrar
  referral carries a 12-digit portal CAN" — was computed into the payload and
  never rendered, so the table read as though length were the channel. It is
  now a row in the table.

---

## Owner-record fields the register does not carry

`_UNTRACKED_OWNER_FIELDS` / `_asked_untracked_owner_field()` /
`_untracked_owner_field_answer()` in `chatbot.py`, run in **both** chat paths
right after `_gate_app_number` is resolved and before the `survey_owners`
branch. `python test_owner_field_queries.py` (routing + answers, no LLM;
`--routing` skips the DB).

**The gap it closed.** `build_app_tables.py` projects only a handful of the
`uchitta_natham_demo` / `full_field_patta_transfer_igrs_owner_demo` columns into `owners`
/ `survey_ownership` (name, Tamil name, relative/father name, **relationship
type** — `relationship_code` mapped `5`→`s/o`, `4`→`w/o`, `6`→`d/o`, ~171 rows;
`0` (380 rows) stays NULL — **gender** from `sex`, ~140 rows, `address`, Aadhaar
last-4, ownership share). The rest — `ration_card_number`, `epic_no` (voter ID),
`pin_code`, `occupation_code`, `assignment_number`, `own_num`, `rel_num`,
`relation_code` (the raw numeric), `user_no`, `door_number` — is dropped, and is
**blank in the source too** (0 non-null across all 551 natham-chitta rows;
`pin_code` / `cin_no` carry the literal `'0'` on every populated row;
`door_number` 7 rows).

A pointed question about one of those ("what is the ration card number of the
owner of `2023/0153/28/000367`?") carries the word "owner", so it routed to
`survey_owners`, which **dumped every owner on the parcel** (14 lines for that
file) and never acknowledged the field asked — a confident non-answer, the
same failure `_UNTRACKED_WF_FIELDS` / `_UNTRACKED_SOURCE_FIELDS` /
`_UNTRACKED_APPINFO_FIELDS` already fix for the other extract layers.

**The fix.** A fourth untracked family, same shape as the three above: a
deterministic answer (no LLM, no owner dump) that names the field, says it is
not in the SIS register, and points at what *is* held for an owner. English,
Tamil and Tanglish cues. It fires only when the message also carries an
application/survey reference (inline number or `_gate_app_number` from
context), so a bare "what is a ration card" is left alone. Cues are specific —
`"occupation code"` not bare `"occupation"`, `"relation code"` not
`"relation"` — so applicant-field lookups (`applicant_occupation`,
`applicant_gender`, …) are untouched.

The same family also covers the transfer-owner extracts
(`full_field_patta_transfer_return_owner_demo` / `_old_owner` / `_new_owner`, which feed nothing
in layer 2): `owner_status`, `uds_details` (undivided share) and owner-scoped
`extent` — all empty in the source, none projected.

Two adjacent phrasing fixes rode along: `"town code of <app>"` now answers
(added to `_field_keywords`; it was falling to the generic details card while
district/taluk/ward/block code all worked), and `"relative name"` /
`"relative's name"` map to `applicant_father_name` instead of the owner dump.
`_UNTRACKED_APPINFO_FIELDS`' `return_status` also learned the bare-imperative
shapes ("was `<app>` returned", "has it been returned", "sent back") — the
resolved application number is stripped from the message (`_msg_lower_nonum`)
before the untracked-field cues run, so a cue that brackets the number still
matches. `"returned"` / `"return status"` are `_field_keywords` so the
deterministic block opens for them.

Owner-scoped `relationship` / `gender` / `mobile` / `address` stay with
`_owner_detail_line`, which appends the value (or a per-row "not recorded")
when the question asks for that field. `relationship` and `gender` are now real
projected columns; `mobile` is blank for every owner in this extract.

**Unseen phrasings.** `_asked_untracked_owner_field()` runs the exact-substring
cues first, then a narrow typo-tolerant fallback (`_UOF_FUZZY`: hand-picked
content-word *pairs* — "ration"+"card", "occupation"+"code", "relation"+"code",
… — matched with `is_token_typo_match`, the same edit-distance rule
`parse_intent` uses), so "raton card", "occuption code", "reltion code" land
right. Single weak tokens ("number", "status") are deliberately excluded — they
would false-match ordinary questions. Personal attributes that appear in **no**
land record — caste, religion, age, email, marital status, income, blood group,
… — are caught by `_asked_unknown_owner_attr()` (requires the word "owner") and
answered "not held … a land-mutation register, not a personal profile", instead
of falling through to the 100-plus-row `survey_owners` dump. Known gap: the
exact phrase "relationship **type** of owner <app>" still hits the field map's
bare `type` → "Application Type"; "relationship of the owner" / "which
relationship does the owner have" route correctly.

## Parcel-register fields the survey projection does not carry

`_UNTRACKED_PARCEL_FIELDS` / `_asked_untracked_parcel_field()` /
`_untracked_parcel_field_answer()` in `chatbot.py`, checked in **both** chat
paths right after the owner-field guard and before the `survey_detail` branch —
survey-scoped, and skipped when an application number is in view (that sends the
same words to `_UNTRACKED_SOURCE_FIELDS` on the field-lookup path instead).
`python test_parcel_field_followups.py` (routing + classifier + answer, no LLM).

**The gap.** `build_app_tables.py` carries a `uareg_demo` row's
geography, `survey_number`, `subdivision_number`, `patta_number`,
`land_type_code` → `land_type` and `extent_value_3` → `total_area_sqm` into
`survey_numbers` / `sub_divisions`, and drops the other ~35 columns. Unlike the
owner extract these are largely **populated** in the source — soil types ~540
rows, `tax_per_hectare` / `total_tax` / the `double_crop` / `partition` /
`government_priority` / `assessed` / `cultivable` flags all 1033, `remarks`
~800, `form6_number` ~490, `old_survey_number` 1019 — so "held in the source
parcel register, not in this assistant's projection" is the honest answer, not
"blank everywhere". A pointed question ("what is the soil type of survey 5?",
"is survey 5 double crop?", "what is the Form 6 number?") carried the word
"survey", routed to `survey_detail`, and got the generic parcel card (area /
land type / patta / sub-divisions) that silently omits the field — a confident
non-answer. As a bare follow-up ("what is the irrigation source?") it lost the
survey reference and fell to the LLM.

**The fix.** Same shape as `_UNTRACKED_OWNER_FIELDS`: a deterministic answer
naming the field, saying it is in `uareg_demo` but not the
projection, and pointing at what *is* held for a survey number (patta, area
sq.m, land type, sub-divisions, encroachment / litigation). Plus the bare
follow-up cues in `followup_context._SINGULAR_FIELD_CUES` (`irrigation`,
`crop`, `partition`, `form 6/7/8`, `relinquish`, `alienat`, `acquisit`,
`assess`, `cultivab`, `door`, `street`, and the Tamil stems), so
"நீர்ப்பாசன ஆதாரம் என்ன?" after a survey answer keeps the reference.

Cues are specific: bare `"remarks"` is left out (it belongs to five other
handlers — order / SIS / SD / auto-recommendation / proposed remarks); soil /
tax-rate / land-use / tax-per-hectare overlap `_UNTRACKED_SOURCE_FIELDS`, which
still owns them on the application-number path. `assignment number` is in both
this family and `_UNTRACKED_OWNER_FIELDS`; the owner one is checked first but
only fires with an application number, so a survey-scoped ask lands here.

---

## Chat attachments — uploaded files as cited evidence

`backend/services/doc_extract.py`, `attachment_store.py`, `csv_ops.py`,
`attachment_qa.py`; `POST /api/v1/chat/upload` in `backend/routers/chat.py`;
tables `chat_attachments` / `attachment_chunks` / `attachment_rows`.

**What it replaced.** An upload used to be parsed to a flat string, kept in a
module-level dict in the worker process, and pasted into the prompt as
"the first 16 000 characters". That store vanished on restart, was keyed by a
client-supplied `session_id` with no ownership check, and gave the model a wall
of text with no way to say where anything came from — so a page number in an
answer was, necessarily, invented.

**The shape now.**

```
POST /api/v1/chat/upload
   → session ownership checked against the JWT officer
   → extension + content signature + declared MIME  (bytes decide, not the name)
   → doc_extract.extract()  in a worker thread, under a wall-clock budget
        segments, each carrying WHERE it came from
        (PDF page · DOCX paragraph range / table · CSV row range · TXT line range)
   → attachment_store.save_document()
        chat_attachments   one reference record per file
        attachment_chunks  the retrievable evidence + its location + its citation
        attachment_rows    CSV rows verbatim, for computing on

chat turn
   → chatbot: attachment_qa.plan_answer()  ← before every DB handler, returns
                                             None unless this turn is really
                                             about an attachment
        which file? → one active file answers bare questions; several ask which
        CSV arithmetic? → csv_ops computes it; no LLM touches the number
        evidence? → none means the grounded refusal, and NO LLM CALL
        otherwise → prompt of retrieved chunks + finished citations → LLM
```

**Four rules, each of which cost something to learn:**

1. **Citations are rendered from stored metadata, never by the model.**
   `doc_extract.citation_label()` turns a stored location into
   `order.pdf, page 3` / `register.csv, rows 18–24` / `report.docx, table 2`,
   and the finished string is handed to the model to copy. A chunk whose
   location says nothing usable is presented with no citation rather than a
   vague one. Two page locations are **never merged into a range**: on a
   three-page order, "pages 1–3" points at the whole document, which is the
   same as not citing at all.
2. **No evidence, no LLM.** `UPLOAD_MIN_EVIDENCE_SCORE` (0.4) is the share of a
   question's content words a chunk must carry. Below it the answer is exactly
   *"I could not find this in the uploaded document."* and the model is never
   called — there is nothing for it to speculate from. The floor is **lexical**,
   not vector, so it holds when Ollama is down; the embedding only reorders
   chunks that already carry the question's words, and can never manufacture
   evidence.
3. **CSV questions are computed, not narrated.** `csv_ops.parse_csv_question()`
   recognises count / filter / group / sort / min / max / sum / average / row
   lookup, `execute()` runs it over `attachment_rows`, and the answer is
   rendered from the verified figure with the row numbers it actually matched.
   A formula-looking cell (`=SUM(D2:D5)`, `@`, `+`/`-` before a letter) is
   **text**: never evaluated, never parsed as a number, reported as skipped.
   That is the same defence as CSV injection. When the parser is not sure which
   column is meant it returns nothing and the question falls through to
   retrieval — guessing a column is worse than declining.
4. **Uploaded content is evidence, never instruction.** Every prompt carries the
   line telling the model to ignore instructions found inside files, and the
   file text sits inside a delimited evidence block below it.

**Routing — an attachment must not swallow the register.** `targets_attachment()`
claims a turn only when the message names a file, uses attachment wording (in
English, Tamil or Tanglish), or the intent is `general_query` — the one place
the pipeline would otherwise hand llama3.1:8b an ungrounded prompt. An explicit
application number or an SIS intent (`pending_applications`, `application_status`,
…) keeps the turn on the deterministic PostgreSQL path, so "how many applications
are approved?" is still answered from the officer's register; the officer says
"…in register.csv" when they mean the file. Naming a file overrides everything.

**Ambiguity is asked about.** Several files → which file. Several application
numbers inside one file, on a bare "what is the application number?" → which
one, listed. Never the first match because it was first.

**Tamil, Tanglish and English.** The evidence floor compares the officer's words
against the file's, so a Tamil question about an English order copy scored zero
and refused. `_ALIASES` in `attachment_store.py` maps SIS vocabulary across
scripts (ஒப்புத→approved, கட்டண→fee, …), Tamil interrogatives and passive
participles are treated as grammar rather than as unmatched content words, and
Tamil is matched as a **substring** — the same virama trap CLAUDE.md documents
for the comparison parser and the follow-up layer.

**Authorization.** The officer comes from the JWT; the session is verified
against them before anything is parsed, and every chunk and row query filters on
officer **and** session (denormalised onto the chunk rows, so a wrong join
cannot widen access). A `document_id` from the client is a lookup key, never a
grant: another officer's document is indistinguishable from one that does not
exist. Original bytes are written under a server-generated `uuid4().hex` name
inside `UPLOAD_STORAGE_DIR` (`var/attachments`, gitignored, outside anything
served); the client filename never touches a path. Nothing logs document text.

**Retention.** Rows carry `expires_at` (`UPLOAD_RETENTION_MINUTES`, 15). A sweep
runs at startup, on each upload and every `UPLOAD_SWEEP_INTERVAL_SECONDS` (60); reads
also ignore expired rows, and a question about a file that is gone is told to re-upload.
The same bytes uploaded again in the same chat reuse the existing document (session +
`content_hash`) and renew its window. Because the store is PostgreSQL, a restart
loses nothing that has not expired — the exact failure the in-memory store had.

**Document extraction & OCR support:**
- **Searchable PDFs, Word (.docx), CSV, Plain Text (.txt):** Text and tabular structures are extracted directly using native parsers (`pypdf`, `python-docx`, `csv.DictReader`).
- **Scanned / image-only PDF pages (OCR fallback):** When a PDF page contains fewer than `OCR_MIN_CHARS_PER_PAGE` (20) selectable characters, it falls back to page-level OCR via Tesseract (`backend.services.doc_extract._ocr_pdf_pages`, reusing `backend.ingest._ocr_page` with English + Tamil language models `eng+tam`). Pages successfully OCR'd are recorded in `doc.ocr_pages`; pages where OCR yields no text are recorded in `doc.failed_ocr_pages`.
- **Standalone image files (unsupported):** Uploads of standalone image files (`.png`, `.jpg`, `.jpeg`, `.bmp`, `.tiff`) remain unsupported and are rejected at the upload endpoint with an explanatory message ("Images are not read - this assistant reads text only, and does not do vision / OCR over image files.").
- **Unsupported formats:** Legacy `.doc` (Word 97-2003 binary), audio, and video files are unsupported.
- **Extraction limitations:** OCR quality depends on scan resolution and legibility; handwritten text may not be recognized. Exact physical line numbering is maintained for text files; OCR'd PDF pages are indexed by page number.

**Config** (`backend/config.py`, all `UPLOAD_*`): file bytes, pages, extracted
chars, CSV rows/columns, extraction timeout, chunk size/overlap, retrieval
top-k, evidence floor, docs per session, retention hours, embeddings on/off,
raw-file storage and its directory.

### A survey number from an attachment carries its subdivision number too

`survey_subdivision_pairs()` / `_render_survey_pairs_answer()` /
`_render_survey_subdivision_table()` in `attachment_qa.py`; the same
column-pairing rule in `csv_ops.py`'s `_sibling_subdivision_column()` /
`execute()`. `python SIS_Upload_Regression_Tests/test_survey_subdivision_pairing.py`
(22 checks, no LLM). Full writeup: `SIS_Survey_Subdivision_Pairing_Fix_Report.md`.

Whenever an uploaded document or CSV answer names a survey number, it now
carries whatever subdivision number(s) sit beside it in the source — the
database-path rule in "Database Layout" above (an ISD parcel carries two
sub-division numbers) applies to attachment evidence the same way, not just
to the ORM-backed `survey_detail` intent.

- **Free text**: `survey_subdivision_pairs()` scans a window after each
  survey-number match (bounded by the next survey match or
  `_PAIR_WINDOW_CHARS = 400`) for subdivision matches, excluding boundary
  mentions ("South — Town Survey Number 999"), and keeps every distinct
  subdivision in document order — a survey with two sub-divisions (e.g. an
  ISD parcel split two ways) gets both, never just the first.
- **CSV**: `_sibling_subdivision_column()` finds a requested survey column's
  subdivision column by **header name** (never position), and
  `render_csv_result()` renders `row N: V (subdivision: SUB or Not specified
  in source)` instead of silently dropping the paired column.
- **Missing subdivision is stated, never guessed**: `"Not specified in
  source"`, both in prose and as its own table row.
- **Known gaps, confirmed and documented, not fixed in this pass** (see
  `SIS_Survey_Subdivision_Failure_Cases.jsonl`, PAIR-005/006): a reverse
  lookup naming one survey number and asking for its subdivisions ("find all
  subdivisions belonging to survey 101") is not a registered shape and falls
  through to the ordinary LLM/retrieval path; a follow-up after a paired
  enumeration answer ("what other subdivisions belong to it?") does not
  resolve, because `_enumerate_entities_answer()` / `_render_survey_pairs_answer()`
  record no `FollowupContext` for the next turn to resolve against — the same
  pre-existing gap "Implicit follow-ups" above documents for the ORM-backed
  intents, just not yet closed for attachment enumeration answers.

## Agent layer — LLM tool calling

`backend/services/agent.py` + `backend/services/agent_tools.py`.

**Where it runs.** Exactly one place: the point in `process_chat()` /
`process_chat_stream()` where every deterministic handler has passed and the
pipeline was about to hand llama3.1:8b a free-text prompt that can look nothing
up. The ~60 deterministic intents are untouched and are still tried first — the
agent is the *fallback*, not the router. `AGENT_ENABLED=false` in `.env` restores
the old plain-prompt behaviour exactly.

```
parse_intent → deterministic handler ──────────────► answer (unchanged)
                    │ no handler matched
                    ▼
              agent.gather_evidence()   ← tool-selection loop, ≤3 rounds
                    │  llama3.1:8b picks tools
                    ▼
              agent_tools.execute_tool()  ← validate args, enforce jurisdiction,
                    │                        call the existing postgres.py fn
                    ▼
              agent.build_answer_prompt() → call_llama[_stream]() → answer
                    │ AgentUnavailable / timeout / any error
                    ▼
              the previous build_prompt() + call_llama path (unchanged)
```

**Authorization is never the model's.** Three rules, enforced in
`agent_tools.py` and asserted by `test_agent_layer.py`:

1. **The officer is not a parameter.** `ToolContext` binds the JWT-derived
   `OfficerContext` and the `AsyncSession` out of band. No tool schema declares
   an officer, jurisdiction, district or SQL argument, and `validate_args()`
   *raises* on one (`_FORBIDDEN_ARGS`) rather than dropping it — an
   officer-shaped argument is evidence of an attempt to read as someone else,
   not an ordinary model slip. Ordinary invented arguments are dropped with a
   log line, because failing a whole turn over one costs more than it saves.
2. **No parallel permission system.** Every handler forwards `ctx.officer` into
   the same `postgres.py` function the deterministic handlers call, which goes
   through `get_jurisdiction_filter()`. `get_application_details` additionally
   runs `lookup_application_access()` first, so "no such application" and "not
   yours" stay different answers and neither is decided by the model.
3. **Geography the model names is a filter, never a grant.** A `ward_number` /
   `block_number` in a tool call is checked against the wards and blocks the
   officer actually holds *before* the query runs, and refused by name if not.
   Refusing beats passing it down to an empty result: "no applications in ward
   999" reads as a fact about ward 999, and it is not one.

**The tools** (all read-only, all domain-level — there is no SQL tool):

| tool | wraps |
|---|---|
| `list_applications` / `count_applications` | `get_officer_applications()` |
| `get_pending_applications` | `get_pending_applications()` |
| `get_overdue_applications` | `get_overdue_applications()` |
| `get_officer_workload` | `get_officer_workload()` |
| `get_application_details` | `lookup_application_access()` + `get_application_detail()` |
| `get_survey_details` / `check_survey_application_lock` | the survey pair |
| `get_field_visits` / `get_visit_plan` | the field-visit pair |
| `get_last_application` | `get_last_application()` |
| `get_fee_summary` | `get_fee_summary()` |
| `get_my_jurisdiction` | the officer's own posting, wards and blocks |
| `search_documents` | `pgvector_store.similarity_search()` — the only tool with no jurisdiction dimension, because the corpus is public policy documentation |

An unfiltered `list_applications` / `count_applications` answers **what is on
the officer's desk right now**, not their whole history — it inherits the
current-stage pin and active-status default from `get_officer_applications()`.
Both tool descriptions say so, because a model that does not know this reports
"you have 1 application" to an officer holding 50. The same rule is re-derived
in the test's own SQL rather than by calling the function back.

**Two LLM passes, deliberately.** The bound loop picks and runs tools; a
second, tool-free call writes the answer from their results. It buys token
streaming (a tool-bound call's first token may be a tool call, so it cannot be
streamed), an answer prompt that states the grounding rules without competing
with the tool-selection instructions, and an answer even when the model ends
its tool round with an empty message.

**Failure is always a fallback, never a guess.** A bad argument, an
authorization refusal, an unknown tool name and a crashing query all come back
to the model as a readable tool *result* so it can correct itself inside the
turn; a crashed query's result explicitly says not to answer from memory. If
the model layer itself is unusable — Ollama down, `bind_tools` unsupported, the
loop past `AGENT_TIMEOUT_SECONDS` — `AgentUnavailable` is raised **before the
first SSE chunk is written**, and the caller runs the original prompt. The
streaming hook pulls the agent's first chunk before writing anything, which is
what makes that guarantee hold.

**What llama3.1:8b actually does, and what was built to absorb it.** Four
behaviours showed up repeatedly in `test_agent_layer.py` and each is handled in
code rather than by hoping the prompt holds:

* **It copies its own system prompt back as arguments.** When the tool-selection
  prompt named the officer's jurisdiction, the model answered "how many
  applications do I have?" with `count_applications(block_number="ward 102")`
  — its own ward, in the wrong field, promptly refused. Telling it not to did
  not stop it; removing the ward from that prompt did. `_TOOL_SYSTEM` therefore
  names the officer but not their geography, and anything that needs it calls
  `get_my_jurisdiction`.
* **It fills every field of a schema**, writing the string `"None"` (or
  `"null"`, `"N/A"`) into the ones it has no value for. Read literally,
  `ward_number="None"` produced the refusal *"Ward None is outside your
  jurisdiction"* on a question that named no ward. `_ABSENT_TOKENS` in
  `validate_args()` treats those as absent.
* **It narrows questions nobody narrowed** — `get_overdue_applications(application_type="ISD")`
  for a bare "which applications are overdue?", then reported "no overdue
  applications" when unfiltered there were two. The tool-selection prompt bans
  invented filters, and the answer prompt requires the filters actually applied
  to be stated, so an over-narrow answer reads as narrow instead of as false.
* **It re-asks the same question** when a result is an empty list, spending the
  whole round budget. `gather_evidence()` keys each call on name + arguments and
  replays the stored result with "do not call it again".
* **It sometimes calls nothing at all** and answers a data question from its own
  head -- the exact failure this layer exists to remove. A first round that
  selects no tool gets one `_NO_TOOL_NUDGE` and no more; if it still wants none,
  the answer prompt forbids stating any record on an evidence-free turn.
* **It reaches for the widest-sounding tool and relabels the field it gets
  back.** `get_officer_workload` answered "how many approved applications do I
  have?" with its open-workload count, reported as "1 approved application" when
  34 are approved. Its description now says in terms that it counts OPEN files
  only and names `count_applications` for a count by status, and the answer
  prompt forbids relabelling a figure as something the tool did not call it.

* **It overshoots a numeric limit and the refusal became the answer.** Asked to
  search the corpus it requested 10 passages where the ceiling is 8; the call
  was refused, and an officer who had typed one word read *"The search for
  'clear' was refused because the 'max_results' value of 10 exceeds the maximum
  allowed of 8."* Three things were wrong at once, and all three are fixed:

  | | |
  |---|---|
  | a presentational limit was enforced like a semantic one | `_CLAMPED_ARGS` in `agent_tools.py` clamps `max_results`; `submission_month`, `submission_year` and `min_days_overdue` are still refused, because clamping month 13 to 12 answers confidently about a December nobody asked about |
  | an argument fault was reported as if it were a fact | `execute_tool` now returns `internal_error` for a `ToolArgumentError` instead of the `refused` shape an authorization refusal uses; both prompts say an internal fault is the model's own mistake to correct, never something to describe |
  | the turn had no answer and gave one anyway | a turn where nothing succeeded and at least one call was rejected on its arguments raises `AgentUnavailable`, so the plain prompt answers instead — raised inside `gather_evidence`, before the first SSE chunk, which is what makes the streaming guarantee hold |

An authorization refusal is also *phrased* carefully: the answer prompt forbids
turning it into an absence of records, because "there are no applications in
ward 999" asserts a fact about a ward the officer cannot see. The answer prompt
additionally forbids naming a tool, an argument, a parameter or a limit at all:
the officer is a survey officer, not an operator of this system.

## The chatbot cannot change the data

`backend/services/readonly_guard.py`; armed around both entry points in
`chatbot.py`; `python test_readonly_guard.py` (36 checks).

**The rule: a chat turn may read the department's register and write nothing
but the conversation.** Applications, field visits, owners, survey numbers,
workflow history, patta transfers, the geography masters, the CSV-shaped
source layer and `knowledge_embeddings` are read-only for the whole turn. The
only tables a turn may write are its own record:

| table | written by |
|---|---|
| `chat_sessions` | create / touch `last_activity` |
| `chat_messages` | the transcript |
| `chat_attachments`, `attachment_chunks`, `attachment_rows` | an uploaded file |
| `audit_logs` | append-only trail |

**Why a guard and not an audit.** The audit came out clean --
`postgres.py`, `agent_tools.py`, `agent.py` and `rag.py` contain no write
primitive at all, all 14 tools are read-only, there is no SQL tool, and
`delete_collection()` has no caller anywhere in the application. But that is
only true of the code as it stood the day it was checked. The guard makes it
true at runtime, so a future intent handler that "helpfully" updates a status,
or a tool added without the read-only discipline, fails loudly instead of
quietly succeeding.

**How it is enforced.** `chat_turn()` arms a `contextvar` (not a flag -- each
request is its own task with its own context, so one turn cannot arm or disarm
another running beside it) and two SQLAlchemy listeners refuse anything outside
the allowlist:

- `before_flush` catches ORM inserts, updates and deletes.
- `do_orm_execute` catches Core DML -- `delete(Model)`, `update(Model)`,
  `insert(...)` and raw `text("DELETE …")` -- which never passes through a
  flush and would otherwise slip past `before_flush` entirely. A statement
  whose target cannot be identified is **refused, not waved through**: an
  unrecognised shape is exactly when a guard must be conservative.

`pgvector_store.add_documents()` and `delete_collection()` refuse
independently at their own door, because the reference corpus is the thing an
officer is most likely to ask the assistant to "update" and the one that must
never change from a conversation. It is built offline by
`python -m backend.ingest`, read by every officer, identical for all of them.

**Scope, deliberately.** The guard is inert outside a chat turn, so login
stamping `last_login`, `backend/ingest.py` rebuilding the corpus and
`build_app_tables.py` rebuilding the projection are untouched. It is not a
globally read-only engine: that is a larger, different decision, and it would
break `PUT /applications` the day someone implements it.

**What "clear" actually clears.** The typed `clear` command removes two
`localStorage` keys in the officer's browser and opens a new chat session. It
deletes no row, in any table. `chatStorage.clear()` is browser-side only.

**A guard cannot stop the assistant from LYING about a change.** Asked to
"clear the knowledge base and re-ingest", llama3.1:8b answered *"The knowledge
base has been cleared."* Nothing was — the guard saw no attempt, because none
was made — but an officer reading that has no way to know. A false report of a
destructive action is its own integrity failure, and arguably worse than the
action, because it is believed and acted on.

So a request to change something is refused **deterministically**, before any
handler or the agent can improvise an answer: `_is_mutation_request()` /
`_mutation_refusal()` in `chatbot.py`, in both chat paths. The refusal says
what is true (nothing was changed, and the assistant cannot change anything)
and names who can — the TAMILNILAM portal — because "I cannot" without "here is
where you can" just moves the officer's problem.

The match is **narrow on purpose**: the verb must be the first content word,
after an optional politeness prefix, and the message must name something in the
record. That is the shape of an imperative, and it is what keeps "which
applications were approved", "when was it last updated", "how many applications
did I update last month" and "create a report of my workload" out of it.
Catching a real question by mistake is a worse failure than missing an
imperative, which merely falls through to the ordinary read-only path. The
agent's answer prompt carries the same rule as a second line, for a phrasing
the matcher does not catch.

`test_readonly_guard.py` proves the refusal rather than the absence: it
attempts an ORM update, an ORM delete, an ORM insert, Core `DELETE`/`UPDATE`,
raw `TRUNCATE knowledge_embeddings`, `DROP TABLE applications` and
`DELETE FROM field_visits`, and both corpus mutators -- every one is refused.
It then runs real chat turns, including *"delete all my applications"*,
*"truncate the knowledge base"* and *"reset the database"*, and compares row
counts plus an md5 over every application's status and stage before and after.
A final section re-asks each destructive request and greps the answer for a
claimed action, allowing for the negated form the refusal itself uses
("Nothing has been modified").

## Field visits — the count is the officer's whole record

`get_field_visits()` in `postgres.py`; the summary caption in `rag.py`.

"How many field visits do I have?" answered **1** to an officer holding 22, and
the single surviving row read as a random application. The cause was a
current-stage pin in the query:

```python
Application.current_stage == officer.officer_stage   # removed
```

**A field visit is a record of work the officer DID.** It does not stop being
one when the file moves on to the Tahsildar. Pinning the *application's* stage
to the SIS desk threw away the officer's entire history and left whichever file
happened to still be at SIS — which is exactly what "some random application"
looks like from the officer's side.

In the seeded data the pin was **exactly equivalent** to "the visit is not
completed": every `scheduled` / `unscheduled` visit sits at `SIS`, every
`completed` one has moved to `COMPLETED` / `REJECTED`. So it filtered out the
whole record and bought nothing that `to_be_visited_only` does not already say
directly. What each officer sees now (rejected applications still excluded, the
standing rule):

| officer | was | now | of which |
|---|---|---|---|
| `csenthil@` | 1 | 3 | 2 completed, 1 to visit |
| `msivakumar@` | 1 | 13 | 12 completed, 1 to visit (1 overdue) |
| `muthulakshmis@` | 2 | 9 | 7 completed, 2 to visit |

This is the same failure CLAUDE.md already records twice — the channel filter
("an officer holding 30 CSC files was told *No applications found*") and
`count_applications` ("reports *you have 1 application* to an officer holding
50"). The pin belongs to **what is on my desk now**, and to nothing else.
`get_overdue_applications()`, `get_highest_priority_applications()`,
`get_officer_workload()` and `get_unscheduled_visits()` keep it, correctly: all
four ask about the desk.

Three things fixed alongside it, each a case of the answer not matching the
question:

- **The total now carries its breakdown** — "13 field visit(s) — 12 completed,
  1 still to visit, 1 overdue". A bare total over a table whose top rows all
  read *Completed* invites the reading that 13 are outstanding. The figures come
  from the counts the query already returned, never recomputed from the rendered
  page, and the breakdown is suppressed when a status filter is in force.
- **"how many field visits are completed"** fell through to the unfiltered
  summary and answered with every visit, finished or not — the question answered
  with a superset of itself. `completed` is now a status filter.
- **"which field visits are pending"** was given the current month as a default
  date scope and answered *"No field visits scheduled"* to an officer whose one
  outstanding visit was overdue from July — the very visit being asked about.
  The month default now applies only to date-shaped questions.

## Messages that ask nothing

`_contentless_message()` / `_contentless_reply()` in `chatbot.py`, answered in
both chat paths before intent routing.

`parse_intent` routes everything it does not recognise to `general_query`,
which now means the agent layer. That is right for a real question phrased in a
way no handler matched, and wrong for a message that is not a question. An
officer typing **"clear"** expects the transcript to be wiped; what happened
instead was that llama3.1:8b searched the policy corpus for the word "clear"
and the officer read a sentence about a tool argument limit. `ok`, `cls`,
`...`, `சரி` all went the same way — two LLM generations spent to produce
something worse than silence.

These are answered deterministically: no LLM call, no tool call. Three kinds:

| kind | examples | what happens |
|---|---|---|
| `clear` | `clear`, `clear chat`, `cls`, `reset`, `new chat`, `அழி` | the transcript is **wiped** — the turn returns `action="clear_chat"` and the frontend clears the DOM, drops stored history and opens a fresh session |
| `session_command` | `exit`, `logout`, `quit` | told which control does it (Logout, top right), once |
| `ack` | `ok`, `done`, `hmm`, `...`, `சரி` | a short acknowledgement naming what they *can* ask |

**A command is carried out, not explained.** A closed set of literals was too small for the one command that is typed in
sentences. `clear`, `clear chat` and `clear conversation` matched; "clear the
conversation", "clear chat history", "delete this chat" and "chat history
clear" did not, and were answered as questions. `_is_clear_command()` in
`chatbot.py` recognises the command by **shape** instead: a wipe verb
(`clear` / `reset` / `wipe` / `erase` / `delete` / `remove` / `flush` /
`clean`, and their gerund forms `clearing` / `resetting` / …), and every other
token filler or a word for the transcript itself (`chat`, `conversation`,
`history`, `messages`, `transcript`, `screen`, `session`, `everything`). Four
rules keep it from claiming real questions:

- **Still matched whole.** "clear my pending applications" carries
  `applications`, which is not a word for the transcript, so it is not a wipe.
- **A trailing verb needs the noun.** "chat history clear" is the command;
  "all clear" is an acknowledgement, and `all` is filler rather than a noun for
  exactly this reason.
- **Only `clear` / `reset` / `wipe` / `erase` are a wipe on their own.**
  `delete` and `remove` must name what to delete, since alone they say nothing.
- **Tamil is matched as substrings**, never with `\b` — the same virama trap
  documented for the comparison parser and the follow-up layer.

Three implementation details worth knowing:

- **Token punctuation is stripped per-token** (not just from the whole message).
  Whitespace-splitting leaves `"hey,"` with its comma attached, which didn't
  match `"hey"` in `_CLEAR_FILLER`. `_is_clear_command` now strips
  `_TRIM_CHARS` from each token after the split, so `"Hey, could you go ahead
  and clear the entire conversation history for me"` classifies correctly.
- **Gerund forms** (`clearing`, `resetting`, …) are in `_CLEAR_VERBS` — they are
  just as unambiguous as the base verb. `"Would you mind clearing the
  conversation?"` is the same command. The safety property is unchanged: a gerund
  aimed at a domain object (`"clearing the database"`) still fails because
  `"database"` is not a transcript noun.
- **`"mind"`** is in `_CLEAR_FILLER` — it appears only in `"would you mind …"`,
  pure politeness, not a domain word.

`clear all`, `clear everything` and `delete all messages` are also shaped like
a mutation request aimed at "everything". The clear path runs first in both
chat paths, but `_is_mutation_request()` excludes them explicitly so nothing
depends on that ordering.

**The frontend has to be the version that knows about the action.** The wipe is
carried out by `chat.js`, and `chatbot.html` requests it with a `?v=` cache
buster. Shipping a `chat.js` change without bumping that version leaves a
cached browser running the old file — which looked exactly like the bug the
feature fixed: the new backend answered "Conversation cleared." and nothing was
cleared. **Bump the `?v=` on every `js/*.js` file you change.** `clearRequested`
is also read in `sendMessage`'s `catch`, so a stream that breaks *after* the
instruction arrived still carries the wipe out.

**Clear is a context reset, not a deletion.** The frontend opens a new session;
the backend also moves the old session's `started_at` to the moment of the clear
(`_mark_session_cleared`) for an API client that keeps the session. The
`chat_messages` rows are **kept** (audit). Every reader of the transcript or its
context -- the follow-up loader, `_recent_answers`, `_type_swap_followup`, the
listing fallback, the attachment-focus readers and `GET /sessions/{id}/history`
-- filters through `models.messages_since_session_start()`, so the older rows
cannot reach an answer or be restored into the page. On the frontend, New Chat
and a streamed `clear_chat` bump `conversationGen` and abort the in-flight
stream, so a late answer from the old conversation writes nothing to the page or
localStorage; `currentSessionId` is null (send disabled) until the server
confirms the new session, a failed creation shows a retry, and a second click
joins the transition already running. `python test_clear_chat.py`
(`--routing` skips the DB; runs `node test_clear_chat_frontend.js`).

`python test_clear_command.py` (117 classification cases — 70 wordings that must
wipe, 13 register-mutation requests that must not, 13 ordinary questions, 2 long
real Tamil/English questions, plus other contentless kinds; and an end-to-end
turn on both chat paths — the action, the confirmation, and that a clear turn
writes no `chat_messages` row); `--routing` skips the database. No LLM either
way: a contentless turn is answered before any model is reached.

## Off-topic questions, and "what can you do"

`_is_out_of_scope()` / `_is_capability_question()` / `_scope_reply()` in
`chatbot.py`, answered in **both** chat paths right after the mutation guard and
before intent routing. `python test_out_of_scope.py` (49 checks, no LLM).

**The gap.** `parse_intent` sends anything it does not recognise to
`general_query` → the agent / RAG fallback, and llama3.1:8b there will happily
write a weather report, a poem, or an arithmetic answer as if that were the
job — the answer prompt only forbids fabricating *records*, not answering an
off-topic question. And "what can you do" mis-parsed as an `application_status`
lookup (the word "can").

**The fix.** A deterministic one-liner naming what the assistant is for. Both
guards are **high-precision, low-recall on purpose**: `_is_out_of_scope` fires
only when the message matches an unmistakably non-SIS cue (`weather`, `cricket`,
`write me a poem`, `capital of`, `translate … to <lang>`, `2 + 2`, horoscope,
…) **and** carries no survey / application / land vocabulary at all
(`_DOMAIN_TERMS`). "translate the remarks on survey 5", "what is the SLA for
ISD", "what is the deadline" all carry a domain term and pass straight through.
Anything the guards do not clearly own falls to the pipeline unchanged —
catching a real question is a worse failure than letting an odd off-topic one
reach the model, the same rule `_is_mutation_request` follows. English, Tamil
and Tanglish.

**Unseen data needs no new guard.** A question about a non-existent survey /
application / ward routes to a *deterministic* handler (`application_status`,
`survey_detail`, `survey_owners`, `litigation_check`, …), which returns
`{"found": False}` / a jurisdiction refusal — the LLM never sees it, so there
is nothing to hallucinate from. `test_out_of_scope.py` re-checks that those
questions still land on a deterministic intent and are not swept up by the
scope guard.

## Every way an officer asks for a channel

`_CHANNEL_FUZZY` / `_CHANNEL_FUZZY_NEVER` / `names_only_a_channel()` and the
channel-listing gate in `rag.py`. Covered by `test_answer_number_guard.py`
section 3a.

A sweep of ~180 phrasings against the canonical `show <channel> applications`
found the frames all working — `what are the`, `list out`, `give me`, `i need`,
`how many`, `csc application list`, `from csc how many applications`, Tamil,
Tanglish, unions, and every combined scope. Eight did not, in two shapes:

| shape | what happened |
|---|---|
| `csc aplications` | the channel was read correctly and the **noun** was misspelt, so the listing gate — plain substrings — did not fire, and the question fell to the LLM |
| `citizn applications`, `sub registrer applications` | the **channel name** was misspelt, so no channel was named at all |
| `show ctizen applications` | worse: it *did* reach a listing, with the misspelt word silently ignored — the officer's whole desk queue, presented as the answer |
| bare `csc` / `sro` / `citizen` | no noun at all, so the gate never fired; the LLM answered instead — the one path that cannot look a channel up |

All three now resolve, with the same edit-distance rule the rest of
`parse_intent` uses (`_max_edits_for`: 4-7 characters one edit, 8+ two, 3 or
fewer exact only — so `csc` and `sro` get no budget, the rule that stops `isd`
absorbing `nisd`). A message that is only a channel's name is that channel's
listing.

**The typo tolerance is fenced on both sides**, because a channel filter that
fires wrongly is worse than one that misses:

- `citizen` is deliberately **not** in `_CHANNEL_FUZZY`. Matching it there
  would reach the caller ahead of the guard that keeps "citizen access number"
  a CAN question, and every CAN question would become a channel question. The
  citizen typo is handled inside that guard instead, with the CAN carve-out
  intact.
- `_CHANNEL_FUZZY_NEVER` holds real words that sit inside a channel word's edit
  budget. **`register` is two edits from `registrar`** — exactly the budget an
  eight-letter target gets — so "does your register store the applicant's
  occupation?" became a Sub-Registrar question, and one such question was
  re-routed from a field lookup to a channel *listing*. This is the same trap
  `_NEVER_TYPO` exists for.

Both fences were found by A/B-ing `parse_intent` + `extract_submission_channels`
over all 6218 questions in `test_questions*.txt` before and after the change.
The first pass moved 4 of them; the finished change moves **0**. Re-run that
diff for any edit to the channel vocabulary — a routing change is invisible in
a suite that only asks the questions it already knew about.

## "CSC" is a word, not a list bullet

The bullet stripper at the top of `parse_intent` in `rag.py`.

`"CSC applications"` was answered **"There are 9 CSC applications"** and
given nine application numbers. The officer holds **31**; the nine were their
ISD list, relabelled. Two rules met:

```python
message = re.sub(r'^\s*[a-zA-Z][\.\)\-]\s*', '', message)   # strip "a." / "b)" / "a-"
```

`"CSC applications"` starts `e-`, so the stripper took it for a list bullet
and left `"sevai applications"` — which matches none of the channel vocabulary
(`e[\s-]?sevai` needs the `e`). The message then named no channel, no
deterministic handler claimed it, and it fell through to the agent, which
reached for the widest tool it had and called the result "CSC" — the
relabelling failure CLAUDE.md already documents for `get_officer_workload`.

A hyphen is now a bullet only when a space follows it (`"a - show my files"`).
`"a."` and `"b)"` are unchanged. This mattered because **CSC is the
department's own name for the CSC counters** and is used throughout
`backend/documents`; `CSC` and `CSC` had always worked, so the failure
was invisible unless the officer typed the hyphen — which is how it is written
everywhere else.

## An empty channel list says why it is empty

The `empty_note` branch of `get_officer_applications()` in `postgres.py`.

Only **one** citizen application exists in the whole register
(`2024/0154/28/001397`, ward 103), and it is rejected — so every officer's
citizen list is empty, two of them because they hold none at all. The answer was
a bare *"No applications found"*, which an officer cannot tell apart from a
broken filter, and which is what "the channel filter is not displaying my
applications" looks like from their side.

An empty list now answers the question it raises — *then where did my
applications come from?*

| case | note |
|---|---|
| the matching files were rejected | "1 matching application was rejected … ask for rejected applications to see it" (unchanged; asking for them works) |
| the officer holds none of that channel | "No application in your jurisdiction came in through the citizen portal. Your applications came from 30 through a CSC counter, 24 through the Sub-Registrar." |

The mix is counted from the officer's own jurisdiction with the same clauses the
listing used, so it is as scoped as the empty list it explains.

## A type scope is a register question, not a desk one

`get_pending_applications()` in `postgres.py`; the `status_filter` block in both
chat paths in `chatbot.py`.

"Show my ISD applications" answered **2** to an officer holding 9, and "show my
NISD applications" answered **1** to an officer holding 58. Two filters were
stacked on the same question, at two layers:

- `chatbot.py` defaulted `status_filter` to `"pending"` for any listing that
  named no status; neither type was exempt.
- `get_pending_applications()` then re-applied `ACTIVE_STATUSES` whenever
  `status is None`. The exemption list there named `submission_channel` but not
  `application_type`.

Both now treat an explicit type the way they already treat a channel and a
period. This is the missing half of a rule the code had already written down:
`has_scope_filter` in `get_officer_applications()` stopped pinning the current
stage for an explicit type, with a comment saying the officer wants their full
ISD history — and then the status default threw that history away again.

The unscoped queue is untouched: "show applications" is still the desk, still
`ACTIVE_STATUSES` at the officer's own stage. A named status still wins, so
"show pending ISD applications" is 2 and "show approved ISD applications" is 7.

`test_workflow_logic`'s `queue ISD` / `queue NISD` checks asserted the old
behaviour — they compared a type-filtered listing against SQL carrying
`current_stage = 'SIS'`, and so encoded the very pin `has_scope_filter` exists
to drop. Those two expectations lost the stage pin; the unscoped, pending and
overdue ones kept it.

## "Applicant details" asks about the people

`_asks_applicant_focus()` / `_carried_details_message()` and the
`detail_focus` branch of the multi-application renderer in `chatbot.py`.

After a listing, "applicant details" was answered *"Here are the details for 2
application(s): … (Status: Pending, Stage: SIS)"* — the application card, for a
question about the applicants. `_wants_carried_list_details()` rewrote the
follow-up to a bare `"show details for A and B"`, and the word the officer used
to say what they wanted was dropped in the rewrite.

The rewrite now carries two things forward instead of discarding them:

- **"applicant"**, which becomes `detail_focus` and gets an answer made of
  name, mobile and address — a field the register does not carry is named as
  *not recorded*, not left out.
- **a position.** "Details of the first one" was expanded to *every* listed
  application, because `_wants_carried_list_details` claims the turn before the
  ordinal layer is reached. `_pick_from_listing` now runs inside the rewrite, so
  the ordinal survives it.

## An answer may not carry a number the register does not

`_verify_answer_numbers()` / `_reconcile_count_claims()` /
`_scrub_unverified_app_numbers()` in `chatbot.py`, applied in both chat paths to
the **LLM-written answer only** — every other answer is rendered from the query
result and has nothing to disagree with.

Counts and application numbers are supposed to come from the database, and the
deterministic handlers make sure they do. The two LLM paths (the agent's answer
pass, the plain RAG prompt) are handed the query result as context and can still
paraphrase it wrongly — a transposed sequence number, or a total that disagrees
with the count the query returned. Both read as fact.

- **A stated total is made to agree with the count.** Only a total claim ("you
  have N applications", "there are N pending applications") is rewritten. A
  sentence that already states the database figure is left alone, which is what
  keeps "3 of your 9 applications are approved" from having its 3 rewritten.
- **An unverifiable application number is removed, never corrected.** There is
  no way to tell which real file a wrong number was meant to be, and guessing
  one would be the same failure again. The set of real numbers is every number
  anywhere in the payload — the wider it is, the fewer true numbers are
  mistaken for invented ones. An empty set (the turn fetched no records) means
  the guard stands aside rather than stripping numbers out of a corpus answer.
- **The officer is told.** A correction is not made silently: an answer that had
  to be corrected is one to read with care. On the streaming path the chunks are
  already on the wire and cannot be recalled, so the note is streamed after them
  and the corrected text is what is stored — which also keeps the next turn's
  follow-up context free of an invented number.

A bare imperative also names a channel now: `"show citizen"` / `"list citizen"`
carry no noun for `extract_submission_channels()`'s context-word guard to find,
and were read as no scope at all. `"citizen access number"` is still the CAN and
still not a channel.

## Negation over application lists

`backend/services/neg_scope.py`; hooks in `chatbot.py` right after the follow-up context is
loaded (both chat paths) and just before `_apply_result_limit`. `python test_negation_scope.py`
(`--stream` too; ~40 chains, English / Tamil / Tanglish, no LLM).

`parse_intent` reads ONE status / type / channel, so "I don't want NISD" was answered with the
NISD list and a second exclusion ("... not rejected either", "except ISD and rejected", "neither
approved nor rejected") was dropped. The exclusions (status, type, channel, ward, "without a
[completed] field visit") are now taken out of the message, the rest is asked as an ordinary
positive question ("all applications" when nothing positive is left), and the rows that come back
are filtered -- every figure is the length of the filtered rows. The exclusions and the positive
base are recorded in the follow-up context (`excluded`, `excluded_base`), so "and not ISD", "show
them" (after a count) and "show the rest" (the rows that were left out) continue the same list.
A bare `not` negates ONE item ("not A and B" keeps B; "not A or B" / "not A, not B" exclude both);
`except` / `other than` / `neither` / `without` negate the whole list, and
`followup_context.negation_normalise` expands "other than A and B" to "not A and not B" before
anything else reads it. Cues are fenced: `no of` / `no.` (number of) is not a negation, and
visit-table questions stay with the field-visit follow-up layer.

## A list request with a word nothing can filter by

`backend/services/qualifier_guard.py`, answered through `_special_scope_kind()` (`unknown_from`,
`unknown_qualifier`) in both chat paths. `python test_date_table_followups.py` carries the chains.

"display applications ftom sri", "show applications in xyz", "show urgent applications" were
answered with the officer's default desk queue: `parse_intent` reads the words it knows and drops
the rest, so the list read as the answer to the whole sentence. A short, list-shaped English or
Tanglish request whose words are neither in the small allow-list, nor a word of the documents
(6+ letters), nor a slip within edit budget of one, is answered by naming the word and the filters
that exist (status, type, channel, ward / block, month / year, overdue). A one-word source
("from sri") also offers the near miss (SRO). Tamil-script messages are left to the other rules.
A/B over all 6665 questions in `test_questions*.txt` + `eval_set.jsonl` flags none of them --
re-run that (`ab.py`-style: `unknown_words()` over the files) after touching the allow-list.
`applications in survey N` is a scope, not noise: `neg_scope` keeps that survey's rows only.

## Testing

```powershell
# IGRS Form 6 and CAN number questions (invariants, answers, both scripts)
python test_igrs_can_queries.py            # everything (DB, no LLM)
python test_igrs_can_queries.py --data     # invariants + routing only

# Service code meanings -- "what is 0153?", "which service code is <app>?"
python test_service_code_queries.py            # routing + answers (no LLM)
python test_service_code_queries.py --routing  # routing only

# Owner-record fields -- ration card, voter ID, PIN, occupation/relation code
python test_owner_field_queries.py            # routing + answers (DB, no LLM)
python test_owner_field_queries.py --routing  # cue detection only, no DB

# Owner sub-fields as follow-ups -- gender / relationship / share / aadhaar
python test_owner_field_followups.py          # classification + render (no LLM)

# Parcel-register fields the survey projection drops -- soil, tax, flags,
# old survey number, Form 6-8, door / street code
python test_parcel_field_followups.py         # routing + classifier + answer (no LLM)

# The LLM number guard, the type scope, and "applicant details"
python test_answer_number_guard.py            # everything (DB, no LLM)
python test_answer_number_guard.py --routing  # the guard only, no database

# Off-topic questions ("weather", "write a poem") + "what can you do";
# and that unseen-data questions still reach a deterministic handler
python test_out_of_scope.py                    # classification + routing (no LLM)

# Implicit follow-ups -- "which is oldest?", "when was it scheduled?"
python test_followup_context.py            # everything (DB, no LLM)
python test_followup_context.py --routing  # classification only, no DB

# Chat attachments -- extraction, citations, deterministic CSV, grounding,
# authorization, persistence
python test_attachments.py            # everything (DB + Ollama)
python test_attachments.py --fast     # skips the live-model cases
python test_attachments.py --no-db    # extraction / CSV / prompts only

# Agent layer -- tool calling, authorization, grounding, follow-ups
python test_agent_layer.py            # everything (needs Ollama + the DB)
python test_agent_layer.py --fast     # skips every case that calls the LLM
python test_agent_layer.py --no-db    # schema + validation only

# Comprehensive suite (top-level)
python test_comprehensive_suite.py

# 200-question suite (backend-focused)
python backend/test_200_suite.py

# Fee / service-charge / money questions (routing, DB aggregate, answers)
python test_fee_queries.py            # all three layers (needs Ollama)
python test_fee_queries.py --fast     # routing + data only

# Submission-channel questions (routing, derivation from layer 1, answers)
python -m backend.sample_db.test_channel_queries
python -m backend.sample_db.test_channel_queries --fast

# "My last / previous application" (routing, DB lookup, answers, follow-ups)
python test_last_application.py            # everything (no LLM needed)
python test_last_application.py --routing  # routing only, no database

# Temporary sub-division numbers ({subdiv}/T{seq}) -- CSV, both DB layers, answers
python test_temp_subdivision_queries.py            # everything (no LLM needed)
python test_temp_subdivision_queries.py --csv      # CSV vs backend/documents claims
python test_temp_subdivision_queries.py --routing  # routing only, no database

# Per-application field-visit follow-ups ("is field visit scheduled?")
python test_field_visit_followups.py            # routing + answers (no LLM needed)
python test_field_visit_followups.py --routing  # routing only, no database

# Completed (approved / rejected) applications -- block, applied date,
# decision date, status
# Comparison questions -- "which is older A or B", "ISD vs NISD",
# "which took the longest to approve"
python test_comparison_queries.py            # routing + data + answers (no LLM)
python test_comparison_queries.py --routing  # routing only, no database

python test_completed_applications.py            # routing + data + answers (no LLM)
python test_completed_applications.py --routing  # routing only, no database
python test_completed_applications.py --data     # routing + data, no answers

# "Which application should I field visit next, and where?" (routing, plan, answers)
python test_visit_plan_queries.py            # everything (no LLM needed)
python test_visit_plan_queries.py --routing  # routing only, no database

# Negation over lists -- not X / except X and Y / neither ... nor / without a field visit
python test_negation_scope.py [--stream]

# The typed "clear" command -- what counts as one, and what it does
python test_clear_command.py            # classification + end-to-end (DB, no LLM)
python test_clear_command.py --routing  # classification only, no database

# Test question sets (plain text, one question per line)
# test_questions_100.txt, test_questions_200.txt, test_questions_206.txt
```

---

## License

Developed for **National Informatics Centre (NIC)** internship — Tamil Nadu Survey Department.

## Sub-division plot fields of one application

`backend/services/subdivision_qa.py`, hooked by `_early_subdivision_answer()` in
`chatbot.py` (both chat paths, ahead of the "not held in your register" guards).
`python test_subdivision_qa.py` (no DB, no LLM).

Answers temporary / new sub-division number, existing / new patta, area, land type / use,
tax, plot, soil, group numbers and owners from the plot rows in `areg_temp_subdivclub`
(via `application_sub_divisions`), in English, Tamil and Tanglish, tolerant of typos
("temp sub div no", "pata", "aria", "nmbr"). Exact words win over fuzzy matches
("ares" / "area" are confusable). A back-reference ("this / intha / இந்த") also claims
a turn routed to a listing intent, which then becomes `application_status`.
**Area is stored as hectare + ares + sq.m**: total sq.m = 10000·ha + 100·ares + sq.m
(`build_app_tables_new.py` used to keep only the sq.m part).

## Old-owner record (`chitta_temp_old_owner`) and its comparison with the sub-division record

`backend/services/oldowner_qa.py`, hooked first inside `_early_subdivision_answer()`;
`python test_oldowner_qa.py` (no DB, no LLM). Answers old-owner name, patta, relative
(father / husband), relation / relation code, owner no, share, extent u1-u3, survey /
sub-division no, status code, gender, "is there an old-owner record" and a full card, and
compares the old-owner row with `areg_temp_subdivclub` field by field (survey, sub-division,
existing / new patta, new sub-division, status, district-block codes, extent vs area):
✔ match, ✘ differs, ◐ some plots only, – not comparable (one side blank). Blank columns
(`relation = '...'`, no share / extent / sex) are "not recorded", never guessed; the status
codes (M / E) belong to different tables, so a difference is flagged as a note, not a conflict.
A bare "owner name" is left to the fuller owner handler. A comparison question skips the
multi-request splitter and the "town-level data" jurisdiction keyword guard, and a hook answer
forces `intent = application_status` so a later listing branch cannot overwrite it.

## Desk recommendations, change flags and application-information fields

`backend/services/cmflow_qa.py` and `backend/services/appinfo_qa.py`, both answered from
`_early_subdivision_answer()` (`python test_record_qa.py`, no DB, no LLM).

* **`cmflow_qa`** reads `correction_modification_flow`: SIS / ZDT / SD / DIS / Tahsildar / RDO /
  Collector-DRO / DRO / COSS / CLA recommendation (Y / N), remarks and submit date; reason code,
  govt / private change flags, SIS / DIS certificates; compare, who-recommended, workflow, first /
  last submitted, missing recommendation. The table holds only service codes 0158-0169; an ISD / NISD
  file has no row and the answer says so (a SIS-only question on an ISD / NISD file stays with the
  existing workflow handlers). Reason codes are shown raw: the register does not define them.
* **`appinfo_qa`** reads the application-information extract (ISD: `sub_div_patta_transfer_application_information_urban`,
  NISD: `full_field_patta_transfer_application_information`): challan no / date, payment mode,
  amount, status / reject-reason code, proposed field-visit date and remarks, missing documents,
  enclosures, SRO, CSC name, permanent address, mother / father, gender, DOB. A blank column is
  "not recorded" with the rule that explains it (no SRO on a CSC file).
* **Plumbing** (`_cm_strong()` in `chatbot.py`): a claimed message is forced to `application_status`,
  skips the multi-request splitter, the `workflow_remarks` early handler, the channel-clarification
  guard and the pending-list routing; `_hook_done` stops the later application_status branches from
  overwriting the answer. A service code other than 0153 / 0154 is not in `applications`: the gate
  returns the register row (`_register_card()`), or lets a record question through to the hook.
  "what is the current application status?" right after an answer about one application is that
  application's status (`_bare_app_status()`), not the pending list. The gate looks back 8 officer
  turns for the application number.

**Details of an application under any other service code** (`backend/services/othercode_detail.py`):
"show details of 2026/0158/02/000905" (any code but 0153 / 0154, which have no row in `applications`)
returns one card assembled by `appl_id` from the register entry, `appl_log_urban`, the workflow log,
`correction_modification_flow`, `uareg_modification` / `uchitta_natham_modification` (the parcel and
owners being modified; extent = ha + ares + sq.m) and, for F-Line 0178, the application-information
extract (applicant, challan, amount, proposed visit). Built in `resolve_application_reference()`
(`register_only` verdict); a number that exists in the register but outside the officer's ward is
refused as out of jurisdiction, not reported as non-existent. Attachment bodies are never read.

**F-Line (0178) record** (`backend/services/fline_qa.py`, tested in `test_record_qa.py`): survey / sub-division /
patta, land type and extent, govt / private code, transaction status, total boundaries, sketch available (the
base64 body is never read), SIS and DIS recommendation / remarks / reason / verify date / user, scheduled date,
applicant type, from `f_line_service_urban`. For an F-Line application every field is answered from that row;
for any other file an F-Line-only field says no F-Line record is held and the general fields stay with the
general handlers. The govt / private code (`govt_pri`) is shown raw: the register does not define its values.
A record question with no number resolves its application from the last 14 chat messages (either side), because
an answer carries it as "[number]"; such a question skips the follow-up layer, the unknown-qualifier guard
("I don't know how to filter by 'checking'") and the pending-list routing.

**NISD application-information columns** are covered by `appinfo_qa` too: barcode flag, encertify, physical
verification, documents sent / received dates, RTR str, owner-correction reason, merged application id,
automutated, occupation, last-updated + remarks, current address and mobile. Columns that exist only in
`full_field_patta_transfer_application_information` (barcode, physical verification, sent / received, owner
correction reason, automutated, occupation) are reported on an ISD file as "held only for NISD applications".
A message `appinfo_qa` claims skips the MERGE-code guard, the record-field (ownership / updated) turn and the
uploaded-document planner, which each used to answer it first ("There is no MERGE service code (0155)",
"I don't have an uploaded document for this chat").

**Context hygiene for record answers.** An answer from `_early_subdivision_answer()` records its application
(`_HOOK_APP`) as the follow-up context, and a register-only card (`register_only` gate verdict) does too; before,
neither did, so a numberless follow-up after "status of <0158 file>" fell back to an older application. A listing
under one service code (`all_service_code_applications`) now records its rows as the carried list (with the code as
a filter), so "show details of the second one" after "show my pending 0158 applications" picks that list, not an
earlier ISD one. A Tamil cue that is a substring of another word is a trap here too: `வரி` (tax) sits inside
`முகவரி` (address), so `subdivision_qa` replaces `முகவரி` first.

**Merge ids.** There is no merge service code; the old guard that answered every "merge" question with a
"no MERGE service code (0155)" line no longer names a code. A merge is only `applications.merged_application_id`
(source `merge_applid` in the application-information extracts; one ISD file, 2026/0154/02/013642, carries one, merged
with 2026/0154/02/011886). Forward ("merge id of X", "with which application is it merged", "merge applid enna") is a
field answered by `appinfo_qa`; `backend/services/merge_qa.py` adds the reverse ("which applications are merged with X")
and the overview ("which applications have a merge id", "how many merged applications"), each re-checked against the
officer's jurisdiction (a parent outside it is never confirmed) and recorded as the carried list, so "show details of the
first one" and "how many of them are approved" continue it. A generic "what is the merge service code" / "can I merge
two applications" is answered by saying merging is a merge id on an ISD file, not a service.

**Cross-checks** (`backend/services/crosscheck_qa.py`, parse tested in `test_record_qa.py`): comparisons of one
application against its other records, computed from the rows -- applicant vs the chitta old owner (names compared
through `utils/translit`, so Tamil and Latin spellings meet), a full mismatch report (patta, survey, ward, block),
application status vs the correction / modification flow, address / mobile shared with other applications (only
ones in the officer's jurisdiction are named), challan date vs submission date, physical verification vs the proposed
visit, missing documents vs submitted, owner-correction reason, automutated vs manual, what a merge id indicates,
payment completeness. A patta-transfer applicant is usually the NEW owner, so an applicant that differs from the old
owner is stated as expected, not as an error. Where a column does not exist for the file's type (physical verification,
owner correction reason and automutated are NISD-only) the answer says so. The application a numberless follow-up
means is the one an answer is ABOUT: its trailing "[number]", else the number it opens with (`_LastAppNo`), never a
number quoted inside it.

**IGRS owner records** (`backend/services/igrs_owner_qa.py`, tested in `test_record_qa.py`): the owners a Sub-Registrar
(IGRS) referral names, from `full_field_patta_transfer_igrs_owner` (9 rows, 6 NISD files, none of which also has a chitta
old-owner row -- the two owner records never coexist on one file). Fields: owner (Tamil / English), patta, door, relative
and relation, owner / relative no, share, address and pincode, IGRS form 6 no, form 6 and assignment no, ration card,
EPIC, Aadhaar (never shown, only "recorded"), mobile, sex, occupation code, last updated, user no, image. Several owners
are listed one by one. A file with no IGRS rows answers an explicit IGRS question with "no IGRS owner record held";
an owner question without the word IGRS goes to the other owner handlers unless the file has IGRS rows. Comparisons
(IGRS owner vs chitta old owner, vs the patta-transfer applicant; patta, form 6, relative, relation, share, address;
"same application") are computed from the rows, name compared across scripts by transliteration, narrowed to the
topic asked, and say when the chitta side does not exist. The owner-photo refusal and the record-field handler step
aside for these questions.

**New-owner records and UDS** (`backend/services/newowner_qa.py`, tested in `test_record_qa.py`): the owners a full-field
patta transfer (NISD) gives the land to, from `full_field_patta_transfer_new_owner` (30 rows, 16 NISD files; the old
owners of those files are in `full_field_patta_transfer_old_owner`, not in the chitta table, which is ISD's). Fields:
name (Tamil / English), relationship and relative, extent, patta, owner and relative no, owner-record status code (raw:
the register does not define it), UDS, share, photo, Aadhaar (never shown), sex, survey and sub-division no, geography,
mobile. Comparisons (new vs old owner, vs IGRS, vs F-Line, vs the sub-division record, vs the application; "same
person", "only the fields that changed") are computed from the rows and say when the other side does not exist: IGRS,
F-Line, chitta and new-owner records never coexist on one file. Two rows can carry the same owner no (a split into
sub-divisions), so owners are numbered by position and paired with the old owners by position. **UDS means Undivided
Share** (the proportion of land held in common that goes with a flat or plot): "what is UDS" is answered by definition
before any application is needed (`_merge_turn` carries it), and the same text is in `land_rules.txt` for the next
`python -m backend.ingest`. A bare "who is the owner" is only a SOFT claim of the IGRS / new-owner modules: it never
steers a turn, so "who is the owner of survey 20" still reaches `survey_owners` (that regression was caught here).

**Old owners of a full-field (NISD) transfer** are answered by `newowner_qa` too (`full_field_patta_transfer_old_owner`:
name, relative, relationship, extent, patta, owner / relative no, status, UDS, share, sex, Aadhaar presence, geography,
"the application linked to this old owner", "complete old owner details"); an ISD file's old owner is still the chitta
record (`oldowner_qa`). Old-vs-new comparisons pair the owners by position and answer "which fields changed", "which
stayed the same", "same person or changing the owner", "same share", "any inconsistent details", district / taluk / town /
ward / block, status and Aadhaar presence (the numbers are never shown, so equality of Aadhaar cannot be stated). "Old
owner of survey 5" is a survey question and is left to the survey handlers.

**Returned owners** (`newowner_qa`: `full_field_patta_transfer_return_owner`): the owners of a transfer that was returned.
The live table is EMPTY (0 rows), so every returned-owner question -- field or comparison -- is answered "no returned-owner
record is held for this application (the table holds no records for any application at present)", never invented; a
"why was it returned" answer adds that the record has no reason column and quotes the application's own remarks, and an
"overall status" question adds the application's status. When rows do exist the same functions render them (fields, update
date, status and owner-status code, geography) and compare returned vs old / new / IGRS by name, patta, share, extent, UDS
and relative -- exercised against fake rows in `test_record_qa.py`.

**Context rules the owner / record modules rely on** (checked across mixed conversations, three officers, both chat paths):
a register-only application (0158 ... 0178) carried along from an earlier turn does not answer a question that names a
survey number ("who is the owner of survey 20" stays a survey question); "and the old owner?" asks who it is; "which fields
changed between them?" after an owner answer compares those owners (`_owner_ctx_compare`); every module re-checks the
officer's jurisdiction, so another officer asking about the same file is refused with nothing shown.

**The full-field transfer record** (`backend/services/fulltransfer_qa.py`, tested in `test_record_qa.py`): the NISD
patta-transfer main row in `full_field_patta_transfer_urban` (46 rows, one per NISD file; no ISD file has one): survey,
sub-division, old patta, land type code, extent (u1 ha / u2 ares / u3 sq.m; the extent-in-ares column is blank, so it is
computed), registration document / place / date / year, reason (Sale / Settlement deed), ZDT patta / order / remarks,
transaction status and code, submit and update time, tax rate, succession / legal-heir / court details, purchased area
and unit, street and door, SIS recommendation / remarks / reason, group numbers, owner-correction reason, transaction type,
direct-transfer flag, land category. Codes the register does not define are shown raw and said to be undefined. Cross-checks
(old patta vs the old owner's, ZDT patta vs the new owner's, survey / sub-division vs the new owner, door, street, tax,
purchased area vs extent, registration vs reason, succession / court support, SIS recommendation vs transaction status,
group numbers vs the sub-division record, "differences vs the new-owner record") are computed and narrowed to the topic.
On the sample, the ZDT patta of 048323 (84103) differs from the new owner's recorded patta (69522): that is what the two
tables carry. A question that names a survey number and no application is never claimed by any record module
(`_cm_strong` returns False), so "tax rate of survey 5" stays with the survey handlers.

**SD attachments** (`backend/services/sdattach_qa.py`, tested in `test_record_qa.py`): the Senior Draughtsman's sketch PDFs
kept against an ISD file in `sd_attachment` (52 rows, 29 files; 015395 has two, both survey / sub-division `120/1`). Answers:
exists / how many / which files / sl no / survey-sub-division per attachment / missing / duplicates (compared by content hash)
/ complete details, and the cross-checks against the application's survey and temp sub-division record. The rows carry NO file
name, only the embedded PDF, so a file-name question reports type and size and says so; the file contents are never read or
shown. F-Line and full-field records never exist for an ISD file, so those comparisons say so. The question must say "SD" or
name an application (or follow an SD answer, `_sd_parse`) -- a chat-uploaded file ("the attached file", "register.csv") still
goes to `attachment_qa`.

**TSLR sketch records** (`backend/services/tslr_qa.py`, tested in `test_record_qa.py`): `tslr_sketch_urban` carries the
survey / sub-division, old patta, door, type (e-category), land type code, extent (u1 ha / u2 ares / u3 sq.m), submit time,
challan / payment (treasury, bank, mode, amount) and CAN of a TSLR-sketch application (service code **0156**; 22 rows, 11
files, usually two per file). It is a register-only service, so the question is answered with an access check through the
register, not `applications`. Comparisons against F-Line, full-field transfer, chitta and application-information say those
records do not exist for a 0156 file; the TSLR rows are compared with the register entry's survey, sub-division, ward and block
(all ✔ on the sample). The details card of a 0156 file now lists its TSLR rows. A bare follow-up ("and the sub division no?")
after a TSLR answer continues it (`_tslr_parse`); an ISD file such as 015395 has none and says so.

**Parcel-register modifications** (`backend/services/uamod_qa.py`, tested in `test_record_qa.py`): `uareg_modification` (134
rows, 72 files, services 0158 / 0159 / 0160 / 0162 / 0164 / 0169) -- what a correction / modification application changes in
the survey register: survey / sub-division and old survey no, door, govt / private, land type and use, irrigation, tax, soil,
extent, patta numbers, remarks, forms, relinquishment / assignment, alienation / acquisition, assessed / cultivable,
municipal tax, approval file no / date, status, reject reason, who / when updated, flow type, citizen street, COSS block.
A file usually has two rows (operation 01 and 04; 0164 files three). **The register does not say which operation is the
"before" and which the "after"**, so "what changed" / "what stayed the same" compares the rows field by field and states
that; the dedicated old / new columns (`osurvey_no`, `existing_patta_no`, `new_patta_no`) are shown beside it and
`oldpatta` / `newpatta` / `osubdiv_no` / form 6-8 / relinquishment / alienation / flow type are blank in every row. Codes
(operation, govt / private, land type, status, reject reason) are shown raw and flagged undefined. The modification status
is compared with the register status only (the correction flow has no status column). The word "modification" (or a column
only this record has) must be in the question, or it must follow a parcel-modification answer; fuzzy matching must never
turn "approved" into "approval" (`test_register_equal_treatment` caught that). An ISD / NISD file says it has no
parcel-modification record.

**One large message with many questions** (`_big_plan()` / `_answer_big()` / `_single_turn()` in `chatbot.py`, `python
test_big_message.py`). A message of 14+ words that is a numbered list ("1) ... 2) ..."), "<head> : f1, f2, f3", "tell me /
give me / show me f1, f2, f3 ...", "<application no> f1, f2, f3 ...", "details of A, B, C including f1, f2 ..." or several
"?" questions is split into its own questions BEFORE any handler sees it; each question goes through the ordinary pipeline
in the same order `process_chat` uses (`_single_turn`), and every answer joins the history the next question sees, so "its
SIS recommendation" and "the same application" resolve. Before this a large message was claimed whole by one record module
(the other parts silently dropped) or reduced to its last clause by `_plan_requests` ("focus"). A field list over several
applications asks each field of each application (capped at 30 questions; "details of A, B, C" over the cap falls back to
one details card per application); a qualified field ("IGRS owner name") hands its qualifier to the short fields after it
("IGRS share", "IGRS relative"); "does it match X, Y and Z" stays one comparison; a trailing "answer in tamil" is dropped
from the list. One failing question gives "(could not answer this part)" instead of losing the others. A message with
fewer than 14 words, or no list shape, is never split.

**Natham-chitta modifications** (`backend/services/natham_qa.py`, tested in `test_record_qa.py`): `uchitta_natham_modification`
(121 rows, 43 files, services 0158 / 0159 / 0160 / 0164 / 0169) -- the owners a modification application records against a natham
patta: owner and relative (Tamil / English), relationship, patta and "S patta" (blank), door, share (blank), address, ID numbers
(all blank; Aadhaar never shown), sex, status, operation, full data, flow type (blank), update time. A file has several rows tagged
with operation codes 01 / 02 / 05 / 06 whose meaning the register does not give, so rows are grouped by operation code and "who
changed" compares those groups (how many owners appear in both) and says so. The word "natham" must be in the question (or it
follows a Natham answer). Comparisons with IGRS / chitta / TSLR / full-field / new- / old-owner records say those do not exist for
such a file; patta, status, operation and geography are compared with the parcel-modification rows (`uareg_modification`) of the
same file (patta matches). An ISD / NISD file says it has no Natham-chitta modification record.

**Negated record questions** (`backend/services/negq.py`, `python test_negq.py`, no DB / LLM): "is there no new owner record", "tslr sketch illaya", "which fields are not recorded", "which fields did not change", "doesn't the ZDT patta match", "who has not recommended it", "natham patta match aagala". A record module answers a positive question, so `negq.rewrite()` (both chat paths, ahead of `neg_scope`) takes the negation out and names the record in the form each module reads: existence -> `<record> everyfield` / `complete details`, blank fields -> the same card, kept to the "not recorded" lines, "did not change" -> "which fields changed" kept to the ✔ lines, "not matching" -> the compare question with a "Correct, they do not match" / "No, they match" lead, "not recommended" -> `missing recommendation`. `_early_subdivision_answer()` is a wrapper that applies `negq.shape()` / `lead()` (a contextvar carries the kind). Fenced: it fires only with an application number in the message or the last 8 turns, never on lists / visits / documents / status words (`neg_scope` owns those), "door no" is a number not a negation, Tamil-script plain negations are left to the modules. A/B over the 7786 bank questions rewrites 0. Each record module's `parse()` has a sentinel (`everyfield`, plus its own record word) that returns every column. A file of another service code asked for an IGRS / new / old owner / full-field record answers "no such record" (`_other_code_absent`). Also fixed: a listing on screen no longer supplies "the application" to a numberless record question (`_LastAppNo` returns None for 3+ numbers, strips markup, prefers the first number), and the stream path no longer overwrites a hook answer with the application_status "not found" card.

**What-if questions** (`negq.rewrite()` wrapper, same module): "what if there is no IGRS owner", "what if the challan is not paid", "suppose the DIS recommendation is blank", "sis recommend pannalana enna aagum", "ஒருவேளை … என்றால் என்ன ஆகும்". The register cannot simulate a change, so the wrapper is taken off, the underlying (possibly negated) question is answered from the record as it stands, and the answer opens with a note saying it is not a prediction (`HYP`, `HYP_NOTE`); polarity leads ("Correct, …") are dropped for a what-if. With no application in the message or the last 8 turns the what-if is left to the ordinary handlers. TSLR "payment" alone now asks challan no / payment mode / amount.

**Mixed, long and unsupported questions.** `_big_plan()` now also splits (a) one sentence holding two questions ("which tslr fields are blank and which are the same as the application": `_compound_split`, the second keeps the record the first named; "whether / if" count as question starts), (b) several applications and one question each (`_multi_app_plan`: "compare A and B on status, applicant ..." asks each field of each file; "A, B and C: which has a new owner record, which has no old owner record" asks "does it have ..." of each), and (c) long prose of comma-joined questions (`_prose_plan`: each piece is asked of the application named most recently BEFORE it, greetings / "thanks" / "give the same for" dropped; English and Tanglish). `backend/services/unheld_qa.py` answers what no table holds -- caste / religion, market / guideline value, sale amount, stamp duty, acres / cents, an officer's name, age, income -- with "not held" and what IS held (Aadhaar: "never shown"); it runs ahead of the record modules and needs an application in view. "court case / litigation ... against it" carries the application and goes to the litigation handler. Staleness: `unheld_qa.context_app()` returns nothing when the newest answer is a listing (3+ applications) so a singular follow-up never lands on a list row; no record module caches anything (every turn re-reads its table). `test_negq.py` carries the plan cases. Audit: every application number in ~300 answers exists in the register (the only others are the deliberately fake numbers echoed in "does not exist").

**One look for every answer** (`backend/services/card_html.py`): the ISD / NISD details render as a titled FIELD | DETAILS card with a Copy button, while the record modules, register-only cards (0156, 0158 ... 0178) and "not held" answers were plain text bullets, so the same kind of question looked different. `_early_subdivision_answer()` (and the register-only card) now return the same card markup (`data-table-card`, styles already in `table_styles.css`; Copy falls back to the DOM rows); a one-line answer becomes the `table-intro` sentence. Text is escaped; nothing is added. `_LastAppNo` strips markup so follow-ups still find the application, and `negq._hint` ignores "No ..." absence answers when guessing which record "which fields are blank" means.

**Plain-text answers on the page** (`frontend/js/chat.js`, `plainToCard()`): any plain answer made of bullets or "Label: value" lines (the field-visit plan, workload, "not held" answers ...) is drawn as the same titled FIELD | DETAILS (or APPLICATION | DETAILS) card as the details, whichever backend path produced it, including streamed ones; prose and answers with long lines are left as text. The old bullet conversion ran after newlines had become `<br>` and never matched; lists are converted first now. `chatbot.html` bumps `?v=` for `chat.js` (20261003_002) -- bump it again on any change.

**"Overdue" has two meanings, and the answers now say which.** The documented overdue rule is the 15-working-day field-visit deadline (`backend/utils/sla.py`, `applications.is_overdue`); a visit past its *scheduled date* is a different, earlier state. "Which applications are overdue" with none past the deadline now says so with the oldest open ISD file's age and how many visits are past their scheduled date (`postgres._no_overdue_note`); the field-visit summary says "N past the scheduled date", not "overdue"; "which visits are past scheduled date / missed the date" routes to `fv_overdue_inspections`. Bug found by cross-checking answers against SQL: that handler built its jurisdiction filter with `and_(*conditions)` (every ward required at once) instead of `or_`, so it reported "no field visit is past its scheduled date" while three were. A count in a ward / block ("how many applications in ward 001") is over the register, not narrowed to pending.

**Calculations over the whole register** (`backend/services/calc_qa.py`, `_calc_turn()` ahead of `_stats_turn` in both chat paths, `python test_calc.py`): "pending plus approved", "approved minus rejected", "how many more approved than rejected", "ratio of approved to rejected", "what percentage of 0158 applications are approved", "sum of pending in ward 001", "which service code / ward / channel / month has the most / least", "top 3 wards", "average applications per day / week", and plain arithmetic ("47 plus 17", "20 percent of 150", via a whitelisted `ast` evaluator). The operands are counted from `get_all_service_code_applications(hide_rejected=False)` -- every service code, ISD / NISD being two of them -- and shown beside the result. The register's coarse status has three values; "pending" is its open status (it already holds files in progress), so a pending count is 47 everywhere. Bare "how many are approved" and a percentage with no noun stay follow-ups over the list on screen (`number_qa`); a ratio over a list of one kind compares the register instead. A bare status request ("i m asking abt pending", "pending ones only") is a register-wide listing like "show my pending applications" (it used to fall to the 17-row ISD/NISD desk queue).

**Workflow history for every service code, one shape.** "workflow history of X", "workflow of X", "workflow steps for X", "where is it" / "who has it now" give the same answer for ISD / NISD and every other service code: "Workflow history for X (N step(s)):" then one dated line per step ("Submission → Sub Inspector Surveyor", `[recommended]`, ` · "remark"`) and where the file is now. Files under 0156 / 0158 / 0178 ... read `application_workflow` by `appl_id` (`othercode_detail.workflow()`, hooked by `_other_code_workflow()`); role ids use the ISD / NISD names (1 Submission, 41 / 42 / 44 Sub Inspector Surveyor, 8 Senior Draughtsman, 12 Deputy Inspector Surveyor, 16 Zonal Level Tahsildar, 0 Completed). Before, those codes answered the whole details card and "workflow of X" on an ISD / NISD file did the same.

**Policy / how-to questions are answered from the documents, not by the model** (`backend/services/howto_qa.py`, `_howto_turn()` first in the turn chain, `python test_howto.py`): "what documents are needed for a patta transfer", "explain the 15 working day rule", "is it allowed to approve without a field visit", "how is a sub-division sketch prepared", "steps from application to patta order", "what is a field visit / what should I check", "what if the applicant does not turn up", "how many days does the Tahsildar take", "how can I speed up my pending files", "write a note to an applicant with incomplete documents", "Aadhaar numbers of all owners" (refused), F-Line / other-code fees (the documents give a fee only for 0153 and 0154; the F-Line amounts actually paid are quoted from the register), "anything suspicious or duplicated" (survey numbers with more than one open application), "applicants with more than one application" (matched by name + mobile). Each answer restates `workflow_guide.txt` / `land_rules.txt` and says so where they are silent; Tamil and Tanglish cues have Tamil answers. A how-to question is never split by `_big_plan` / `_plan_requests`. `unheld_qa.intent_alias()` maps "how busy am I", "summarise my day" to the workload and "which of my files should I worry about first" (any language) to the field-visit plan. The LLM (`LLM_MODEL=sis-assistant:latest`, the SIS fine-tune) is now reached by about 5 of 42 open questions and the mean answer time on that set dropped from 13.6 s to 3.1 s. Do not put `<...>` placeholders in answer text: the page treats anything tag-shaped as HTML.

**Four gaps closed.** (1) "difference between 0153 and 0154" is the service-code definition (like ISD / NISD); "how many more 0154 than 0153" stays a count. (2) "what does litigation imply for new applications" (and the bare "what does that imply" right after a litigation answer) is answered from the rules (`howto_qa` topic `litigation_implies`); a how-to question that contains a survey number is a data question and is split, not answered whole. (3) "compare ... this week and last week" counts applications filed in each Monday-Sunday week from the register (`calc_qa`). (4) An LLM-written sentence that states a total ("You have 2 open applications") whose figure is nowhere in the data it was written from is dropped (`_scrub_unverified_counts`, inside `_verify_answer_numbers`, both chat paths) -- never corrected, since there is no telling which count was meant.

**Sweep of everyday questions (68 questions, English / Tamil / Tanglish / typos) -- what it found and fixed.** A count with a period ("how many rejected in september", "how many approved this year") ignored the period (27 / 78 for the whole register): `number_qa` now leaves any period to the register listing, and "how many <status>" is a register-wide count (not the ISD / NISD desk). "list SRO applications" / "show applications from sro" asked for an application number (the `sro` column of the application-information record claimed the turn): a channel scope on a list is no longer a record question (`_cm_strong`). "how many applications are awaiting field visit" / "which have no field visit scheduled" -> the unassigned-visit count and list. "pending files evlo irukku" counts the register like "pending applications". Tamil "வேலைப்பளு" is the workload. "SRO name" on an NISD file no longer claims the column exists only for NISD (SRO / CSC name exist only in the ISD record). New `howto_qa` topics: common rejection reasons (workflow guide), "who is my district officer / the Tahsildar of my ward" (names are not held), "which survey numbers have multiple applications" (grouped by ward / block / survey; the open-duplicate check counts ISD / NISD only, the documented one-active-per-survey rule), "which applications have encroachment / litigation" (reads the survey flags; none are flagged in the sample data), "applications for settlement / sale deed" (declared reason). "who approved / rejected it" names the desk and date and says officer names are not held (`othercode_detail.decider`). A greeting waits at most 6 s for the model before using its fixed text (it used to wait 30 s).

**Three leftovers closed.** A single-field survey question ("patta number of survey 120", "area of ...", "how many sub-divisions", "land type", "is there litigation") answers that one value (`_survey_attribute_answer`) instead of the whole parcel card; a total area of 0 is data that was never loaded (24 of 48 surveys), so it reads "not recorded" in both the answer and the card, not "0.00 sq.m". "show details of the first one" with nothing on screen says nothing has been shown yet (`_howto_turn`), instead of "not sure what you mean". "applications with sale deed" is the declared-reason list (sale).

## Service-code / F-Line / TSLR training data (2026-10-04)

`backend/sample_db/build_lora_dataset_allcodes.py` (all 29 codes, 0155 excluded, equal quota per code, half the code-level examples carry the code table / counts in the system prompt so a code added later is read from context, not remembered), `build_lora_dataset_flinetslr.py` (record-level F-Line 0178 and TSLR 0156 questions asked through the real pipeline), `build_lora_testset.py` (held-out phrasings, never trained on). Outputs in `lora_training/`: `train_augmented_v2.jsonl` (old rows + new, shuffled), `validation_v2.jsonl`, `test_heldout_allcodes.jsonl`, `test_heldout_flinetslr.jsonl`; train, validation and test share no question. Application numbers are invented serials, applicant names / addresses / mobiles / user ids are replaced, "No applications ..." answers are capped at 40% per group. `train_qlora.py` now reads the v2 files (lr 1e-4, dropout 0.1, weight decay, early stopping); after training run `python lora_training/eval_heldout.py --model <ollama model>` and compare held-out with validation scores. A fake serial can contain the digits "0155"; that is not the service code.

There is no service code 0155 and no MERGE application type. The older rows that invented them (a "MERGE (0155)" meaning/listing, and the stale "the schema only admits 0153 / 0154 / 0155, other codes are not handled" wording) were removed from `train_augmented_v2.jsonl` (-286 rows) and `validation_v2.jsonl` (-44); `train_augmented.jsonl` / `validation.jsonl` still contain them, so train only on the v2 files. A merge is only a merge id on an ISD file.

**Officer jurisdiction in the training data (2026-10-04).** Every data-dependent training row now carries the officer and the data its answer is read from in the system prompt (wards held, the register figures, the rows or the record card), so the model reads them instead of remembering them. `build_lora_dataset_jurisdiction.py` adds 80 synthetic officers (different wards, any of the 29 codes, some with no applications; ward and block questions outside their wards are refused by name); validation officers and the 25 test officers are unseen, and the test wards (061-100) never occur in training. Older rows that stated register data from the previous register (lists, counts, applicant names) were dropped; `assemble_lora_final.py` builds `train_v2_clean.jsonl` / `validation_v2_clean.jsonl` (what `train_qlora.py` reads) and re-applies the 0155 / MERGE / stale-wording filter. Rows are only kept if every figure in the answer is in the prompt.

**Evaluating the fine-tune (2026-10-05).** `lora_training/eval_grounded.py` grades each answer `correct` / `missing` / `WRONG` by comparing the values it states (count, date, ward, block, channel, status, survey, code) with the expected answer; an invented application number or a different value is WRONG (a hallucination), "I do not know" is `missing`. `--selftest` has 15 cases. The Colab evaluation of the first adapter (best checkpoint-350, eval loss 0.393) scored the old keyword metric 29% tuned vs 6% base, but its all-codes test had no officer/register data in the prompt for the data-dependent groups, so those scores were meaningless -- `build_lora_testset.part1()` never attached the context (fixed: it now calls `with_officer`, and `g_chain_deep` puts the application's row in the prompt). Copy the three test files in `lora_training/upload_to_drive/` to the Drive folder `sis_qlora_data` (meta `real_app` removed) before the next evaluation run.

**v3 correction set (2026-10-05).** First full evaluation of the best-350 adapter (grounded scorer, 2,038 held-out items): 60% / 67% / 50% correct and 18% / 28% / 25% WRONG on the all-codes / jurisdiction / F-Line+TSLR sets -- it reads supplied counts well but invents a record that does not exist, mixes up neighbouring service codes (0164 -> 0161), guesses a channel the prompt does not carry, and drifts on "not rejected" vs "total". `backend/sample_db/build_lora_dataset_v3.py` builds a small targeted set (1,001 train examples: channel now a column of the application rows in the prompt; "no F-Line / TSLR record is held" and "does not exist" answers in the pipeline's own wording, English + Tamil; service-code <-> name pairs with the neighbouring codes as contrasts; "how many" = not rejected + how many left out, "including rejected" = total, with each code's not-rejected / total printed in the prompt so nothing is added up; multi-turn follow-ups with "not in the data I was given" for values the rows do not hold). `assemble_lora_v3.py` drops the 12 v2 rows that stated a channel with none in the prompt and writes `train_v3_clean.jsonl` (4,848) / `validation_v3_clean.jsonl` (728). `bench_v3_extra.jsonl` (196) uses phrasings never trained on. The 2,038-item original evaluation files are untouched and share no question with v3, so v3 must beat best-350 on THEM before it replaces it. Arithmetic ("114 - 2") is not taught further: totals and differences belong to the deterministic handlers.
