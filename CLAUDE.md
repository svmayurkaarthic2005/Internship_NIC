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
│   │   ├── chatbot.py        # Main orchestrator (~16000 lines) — entry point for all chat logic
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
│   └── js/                   # auth.js, chat.js, chatStorage.js, dataTable.js,
│                             #   table_renderer.js, lucide-fallback.js
├── var/attachments/          # Uploaded-file bytes (gitignored, outside anything served)
├── tools/                    # create_chatbot_test_fixtures.js
├── test_*.py / test_questions_*.txt   # Top-level suites and question sets (see Testing)
├── check_*.py, debug_*.py, trace_intent.py, query_channels.py   # One-off DB / routing probes
├── train_qlora.py, colab_merge_and_gguf.py, kaggle_merge_and_gguf.py   # Fine-tune / GGUF export
├── train*.jsonl, validation*.jsonl, lora_dataset*.jsonl, eval_*_results.jsonl   # Datasets + eval output
├── sis-qlora-adapter/        # Trained LoRA adapter (also on Google Drive)
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

```
build_lora_dataset*.py → clean / redact / validate / strip_* → finalize_train_data.py
   → train_augmented.jsonl + validation.jsonl   (messages-only; meta stripped for training)
   → train_qlora.py or SIS_QLoRA_Training.ipynb (Colab L4) → sis-qlora-adapter/
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
  records the un-tuned pipeline, and `eval_*_results.jsonl` hold each later model.
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
| `urban_application_log` | 1211 | `nisd_transfer_old_owner` | 630 |
| `application_workflow_action` | 288087 | `nisd_transfer_return_owner` | 46 |
| `urban_temp_subdivision_parcel` | 49 | `nisd_transfer_urban_detail` | 229 |
| `urban_temp_subdivision_owner` | 117 | `isd_transfer_application_info` | 41 |
| `nisd_transfer_application_info` | 166 | `isd_transfer_urban_detail` | 50 |
| `nisd_transfer_igrs_owner` | 100 | `urban_parcel_register` | 1033 |
| `nisd_transfer_new_owner` | 620 | `urban_parcel_signature` | 1036 |
| `urban_natham_chitta_owner` | 551 | `urban_natham_chitta_signature` | 439 |

`application_workflow_action` is a district-wide dump. Most of its 288087 rows
belong to settlement service codes (`0167` / `0169`, …) that never become an
application: only 4694 name an `application_id` that `urban_application_log`
also carries, and 982 of those belong to the `0153` / `0154` / `0155` codes the
chatbot works with. `urban_application_log`'s 1211 rows cover 1139 distinct
application ids — an application spanning several parcels has one row per
parcel.

**Layer 2 — the app's ORM tables** (`backend/models.py`), a projection built from layer 1 by `build_app_tables.py`:

| Sample table | App table |
|---|---|
| `urban_parcel_register` | `towns` → `wards` → `blocks`, `survey_numbers`, `sub_divisions` |
| `urban_natham_chitta_owner` | `owners`, `survey_ownership` |
| `urban_application_log` | `applications` (+ `applicants`, `application_documents`) |
| `application_workflow_action` | `workflow_history`, `field_visits` |
| `urban_temp_subdivision_parcel` | `application_sub_divisions` |
| `nisd_/isd_transfer_urban_detail` | `patta_transfers` |
| workflow usernames at role 41 | `sis_officers`, `officer_jurisdictions` |

Rules that follow from this split:

- **The chatbot queries only layer 2.** `backend/services/postgres.py` goes through the ORM models — never against the CSV-shaped tables directly.
- Only service codes `0153` (NISD), `0154` (ISD) and `0155` (MERGE) become `applications` — `ck_application_type` admits no others, so settlement and govt-to-private rows (`0167`, `0169`, …) stay in layer 1 only.
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

`parse_intent(message, prev_intent=None)` resolves ~60 intents by exact token-boundary matching plus edit-distance typo matching (never arbitrary substrings). `prev_intent` disambiguates follow-up filter phrases like "in merge". Rough order:

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
5. Workload / listing — `pending_applications`, `overdue_applications`, `officer_workload`, `isd_applications`, `nisd_applications`, `merge_applications`, `jurisdiction_summary`
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
`identifiers.py` from two columns of `urban_application_log`. **The rule (set by
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

`urban_application_log.source_code` carries no channel signal; do not use it.

### Application Types
- **ISD** (`0154`) — **Involving Sub-Division**: the parcel is split, so the file
  needs a field inspection and an SD sketch.
- **NISD** (`0153`) — **Not Involving Sub-Division**: a straight patta transfer of
  the whole survey number, no new sub-division and no field visit.
- **MERGE** (`0155`) — Merge application (several sub-divisions combined); follows
  the ISD chain.

An ISD/MERGE parcel carries two sub-division numbers, both projected into
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

What the extracts actually carry, via `urban_application_log.application_status`
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

**MERGE** (`0155`) follows the ISD chain.

**There is no FMB (Field Measurement Book) anywhere in this data**, so nothing
in the code should mention one. Checked in both layers and in the extracts: no
column, value or document carries an FMB book number, page number or sketch.
The only thing that comes close is `isd_transfer_urban_detail.sketch_sent_date`
(31 of 50 rows) and `sketch_received_date` (**0** of 50) in layer 1 — a date the
sub-division sketch went to the Senior Draughtsman, never a returned sketch, and
neither column is projected into the ORM tables, so the chatbot cannot see even
that. The FMB references that used to sit in `workflow_guide.txt`,
`faq_english.txt` and `rag.py`'s concept list were removed for this reason: they
made the assistant discuss a record the department's data does not hold. Do not
re-add FMB to the corpus or the prompts unless an extract starts carrying it.

Each hop is a row in `application_workflow_action` (layer 1), projected into
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

The role ids in `application_workflow_action`, read off the data rather than
assumed — the applications whose wording says "Send to SIS" are sitting at role
44 or 41, and 42 shares its actors with 44:

| role | who | stage |
|---|---|---|
| `1` | the CSC operator or citizen who submits | not a desk (no `from_stage`) |
| `44`, `42`, `41` | the surveyor's office (SIS) | `SIS` |
| `8` | Senior Draughtsman | `SD` |
| `12` | Deputy Inspector Surveyor (DIS) | `DIS` |
| `16` | Zonal Level Tahsildar (ZDT / HQDT) — holds the DSC, approves and generates the order | `TAHSILDAR` |
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
(DIS→Tahsildar) hops when projecting `workflow_history` for `0154` / `0155`.

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
| **Field-visit deadline** | an open ISD or MERGE file whose visit is not completed more than **15 working days** after submission is *overdue* (`applications.is_overdue`); a completed visit stops the clock; NISD has none | `workflow_guide.txt` |
| **Service SLA** | NISD 15-20, ISD 30-35, MERGE 15 working days from submission to completion | `land_rules.txt` |

The SLA is a **range**, so a file is *within* it up to the lower figure, *in the SLA
window* between the two, and *past* it only after the UPPER figure -- no single point is
invented. Working days are Monday-Friday; the register has no holiday calendar and the
answer says so. `is_overdue` is re-derived by `overdue_refresh.py` at start-up and every
6 hours (it used to be frozen on the build day). The answer to "how long has X been
pending" names both limits and never a universal "15-day SLA". `survey_manual.txt` gives
a MERGE total of 25-30 working days against `land_rules.txt`'s 15; the code follows
`land_rules.txt` (the per-service-code table) -- reconcile the two documents if that is wrong.

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

- **`chatbot.py`** is the single orchestration layer — all chat logic flows through it. It is large (~16000 lines) by design; new intent handlers belong here or in `postgres.py`.
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
  (`0153` / `0154` / `0155`) that is the name, what it is, the workflow chain,
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
`urban_natham_chitta_owner` / `nisd_transfer_igrs_owner` columns into `owners`
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
(`nisd_transfer_return_owner` / `_old_owner` / `_new_owner`, which feed nothing
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

**The gap.** `build_app_tables.py` carries a `urban_parcel_register` row's
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
naming the field, saying it is in `urban_parcel_register` but not the
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

**Retention.** Rows carry `expires_at` (`UPLOAD_RETENTION_HOURS`, 72). A sweep
runs at startup and on each upload. Because the store is PostgreSQL, a restart
loses nothing that has not expired — the exact failure the in-memory store had.

**Still unsupported, deliberately:** scanned / image-only PDFs (recorded as
`no_extractable_text`, explained to the officer, never answered from), images,
and legacy `.doc`. There is no OCR and no vision anywhere in this path.

**Config** (`backend/config.py`, all `UPLOAD_*`): file bytes, pages, extracted
chars, CSV rows/columns, extraction timeout, chunk size/overlap, retrieval
top-k, evidence floor, docs per session, retention hours, embeddings on/off,
raw-file storage and its directory.

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
  named no status. MERGE was already exempt; ISD and NISD were not.
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
