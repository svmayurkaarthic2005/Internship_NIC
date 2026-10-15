# Sub Inspector Surveyor (SIS) AI Assistant

A bilingual (Tamil/English/Tanglish) AI-powered chatbot for Sub Inspector Surveyor officers in Tamil Nadu, India. Officers interact via natural language chat to manage survey applications, track status, check documents, and query field visits.

**Covers every TAMILNILAM urban service code, not just ISD/NISD.** ISD (`0154`) and NISD (`0153`) are the two codes with a full multi-desk workflow machine (`applications` / `workflow_history` / `field_visits`), but an officer's jurisdiction sees applications under ~27 other codes too — Modification (`0158`), Addition (`0159`), Deletion (`0160`), ULC Subdivision (`0164`), F-Line (`0178`), TSLR (`0156`), and more. Every one of them is answered equally: listings, counts, status filters, comparisons and per-file detail all read from the flat `service_register_entries` register (see "Database Architecture" below), with no code singled out or silently dropped.

## Tech Stack

- **Backend**: FastAPI + SQLAlchemy (async) + PostgreSQL + pgvector
- **Database**: `SISchatbot` and the `knowledge_embeddings` vector store
- **LLM**: Ollama (`llama3.1:8b`) running locally
- **Embeddings**: `nomic-embed-text` via Ollama
- **Frontend**: Vanilla HTML/CSS/JS (no framework)
- **Auth**: JWT (python-jose + passlib/bcrypt)

## Quick Start

### Prerequisites
- Python 3.8+
- PostgreSQL 12+ with `pgvector` extension
- Ollama with models: `llama3.1:8b`, `nomic-embed-text`

### Setup

```powershell
# 1. Activate virtual environment (Windows)
.venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure
copy .env.example .env
# Edit .env with your database credentials

# 4. First-time DB setup (run in order)
python backend/sample_db/seed_sample_db.py     # Create DB + seed 16 CSV-shaped tables
python backend/sample_db/verify_sample_db.py   # Verify structure/refs/signatures/non-leakage
python -m backend.sample_db.load_master_dumps  # Load the 5 TAMILNILAM master pg_dumps
python -m backend.sample_db.adopt_master_district_taluk  # First run only: drop the duplicate districts/taluks
python -m backend.sample_db.build_app_tables   # Project into the app's ORM tables
python -m backend.sample_db.verify_identifiers # Verify every Aadhaar / CAN in both layers
python -m backend.ingest                       # Load document embeddings into knowledge_embeddings

# 5. Start backend
python -m uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
# Or use the PowerShell script:
.\start_backend.ps1

# 6. Serve frontend (separate terminal)
python serve_frontend.py       # Serves on http://localhost:3000
```

**Access**: `http://localhost:3000/login.html`

**API Docs** (dev only): `http://localhost:8000/api/docs`

**Test Credentials** (all use password `Test@1234`, Thoothukudi district 28):
- `csenthil@sis.tn.gov.in` — ward 002
- `msivakumar@sis.tn.gov.in` — ward 102
- `muthulakshmis@sis.tn.gov.in` — ward 103

Run `python check_login_credentials.py` for the current list (see CLAUDE.md — the
older `arjun.kumar` / `priya.devi` / `arun_m` style accounts no longer exist).

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
│   ├── routers/
│   │   ├── auth.py           # POST /auth/login
│   │   ├── chat.py           # POST /api/v1/chat/stream, GET /api/v1/chat/history
│   │   ├── applications.py   # GET/PUT /applications
│   │   └── survey.py         # Survey endpoints
│   ├── services/
│   │   ├── chatbot/          # The orchestrator package (formerly one ~29000-line chatbot.py,
│   │   │   │                 #   now split into ~45 modules under this directory):
│   │   │   ├── orchestrator.py          # process_chat() / process_chat_stream() — main entry points
│   │   │   ├── chat_dispatch.py / _stream.py  # routes a parsed intent to its deterministic handler
│   │   │   ├── chat_application_queries.py    # all-service-code register listings/counts (ISD, NISD,
│   │   │   │                                  #   and every other TAMILNILAM code alike)
│   │   │   ├── chat_shared.py           # shared renderers (the all-service-code table, field maps)
│   │   │   ├── chat_followups.py / chat_smalltalk.py / chat_corrections.py / chat_routing.py
│   │   │   ├── agent.py, agent_tools.py # LLM tool-calling fallback (14 read-only domain tools)
│   │   │   └── *_qa.py                  # ~25 per-record-type handlers: owner, parcel, F-Line, TSLR,
│   │   │                                 #   workflow, cross-checks, attachment Q&A, …
│   │   ├── rag.py            # Intent detection (~60 intents), language detection, LLM calls
│   │   ├── postgres.py       # DB-only query functions (ISD/NISD `applications` + the
│   │   │                     #   all-code `service_register_entries` register)
│   │   ├── followup_context.py   # structured context for pronoun-less follow-ups
│   │   ├── attachment_store.py, doc_extract.py, csv_ops.py   # uploaded-file handling
│   │   ├── pgvector_store.py, embeddings.py                  # knowledge_embeddings (RAG corpus)
│   │   └── readonly_guard.py # enforces that a chat turn can read the register but never write it
│   │                         # See CLAUDE.md's Project Structure for the current full breakdown.
│   ├── documents/            # RAG corpus: workflow_guide.txt, faq_*.txt, survey_manual.txt
│   └── utils/
│       ├── fuzzy.py          # Fuzzy month/token matching for typo tolerance
│       ├── helpers.py        # Misc helpers
│       └── logger.py         # structlog-based logger (get_logger)
├── frontend/
│   ├── login.html
│   ├── chatbot.html
│   ├── css/
│   └── js/
├── .env                      # Secrets (not committed)
├── .env.example              # Template
├── requirements.txt
└── CLAUDE.md                 # Comprehensive AI assistant guide
```

## How It Works

### 1. Request Flow

```
POST /api/v1/chat/stream
        ↓
  chat.py router
        ↓
  chatbot.py → process_chat_stream()   ← MAIN ENTRY POINT
        ↓
  rag.py → parse_intent()              ← classify the user message (~60 intents)
        ↓
  postgres.py → <query handler>()      ← fetch structured DB data
        ↓
  rag.py → call_llama_stream()         ← stream LLM response with context
        ↓
  SSE stream → frontend
```

### 2. Intent Detection (`rag.py`)

`parse_intent()` resolves ~60 intents by exact token-boundary matching plus edit-distance typo tolerance. Rough priority order:

```
1.  greeting / farewell
2.  Deterministic identifiers (application_status, survey_detail, can_number_info)
3.  last_application — "my previous / last approved application"
4.  Per-application checks (joint_owner_check, check_documents, check_sale_deed,
    is_nisd_or_isd, litigation_check)
5.  Workload / listing (pending, overdue, isd/nisd applications, jurisdiction_summary)
6.  Field-visit family (fv_*)
7.  Sub-division desk family (sd_*)
8.  Reference lookups (service_code_lookup, sub_registrar, rejection_info)
9.  compare_applications — 8 shapes (older, type/status/channel counts, duration,
    ward, period, month, superlative)
10. general_query — falls back to agent layer (tool calling) then RAG / pgvector
```

Run `python -m backend.sample_db.test_intent_coverage` to verify routing without touching the DB or LLM.

**Language Detection** (`rag.py → detect_language()`):
- **Tamil**: Unicode range U+0B80–U+0BFF detection
- **Tanglish**: Phonetic patterns (e.g., "vanakkam", "enna")
- **English**: Default fallback

### 3. Database Architecture — `SISchatbot`

One PostgreSQL database (52 tables, live). Table names here are the names as they
actually exist in the running database.

**Register — every service code, one flat table.** `service_register_entries`
(176 rows across the 11 service codes currently populated: ISD `0154`, NISD
`0153`, `0156`, `0158`, `0159`, `0160`, `0162`, `0164`, `0165`, `0169`, `0178`)
is the source for any question that names no specific type — listings, counts,
status filters, per-ward/block/channel breakdowns, comparisons. No code is
singled out: `get_all_service_code_applications()` in `postgres.py` and
`chat_application_queries.py` / `chat_shared.py` in the chatbot package query
and render it generically, keyed off `SIS_URBAN_SERVICES` (all 29 canonical
codes, in `backend/utils/helpers.py`), never a hardcoded code list.

**The ISD/NISD workflow machine.** `applications` (57 rows) is narrower by
design: only ISD (`0154`) and NISD (`0153`) get a full multi-desk workflow
(`workflow_history`, `field_visits`, `patta_transfers`, `applicants`,
`application_documents`) — the department's own data only runs that chain for
those two codes. A question that names ISD/NISD specifically, or something
only they carry (overdue field visits, workflow stage, per-file applicant
detail), is answered from here instead of the flat register.

**Per-code detail tables**, each read by its own `*_qa.py` handler in
`backend/services/chatbot/`, for the service codes that carry extra structure
beyond the register row: `f_line_service_urban` / `f_line_service_application_
information_urban` (F-Line `0178`), `tslr_sketch_urban` (TSLR `0156`),
`correction_modification_flow` + `uareg_modification` + `uchitta_natham_
modification` (Modification/Addition/Deletion `0158`/`0159`/`0160`, ULC
Subdivision `0164`, Govt-to-Private `0169`), `sd_attachment` (ISD sketch
PDFs), `full_field_patta_transfer_*` (NISD patta-transfer owners), `chitta_
temp_*` / `areg_temp_subdivclub` (ISD sub-division + natham-chitta owners).

**Geography masters** (department-wide, statewide): `district_unicode`,
`taluk`, `town`, `ward`, `block` — loaded verbatim from TAMILNILAM pg_dumps;
the app's own `towns` → `wards` → `blocks` carry the officer-jurisdiction
scoping and reference the masters for names.

**Everything the chatbot reads goes through `postgres.py`** — no handler
queries a table directly. `knowledge_embeddings` (768-dim vectors, HNSW index,
cosine similarity) lives in the same database and backs the RAG/LLM fallback.

### 4. Application Types

- **ISD** (`0154`) — **Involving Sub-Division**: field inspection + SD sketch required
- **NISD** (`0153`) — **Not Involving Sub-Division**: document verification only, no field visit
- **Every other service code** (`0156`, `0158`–`0169`, `0178`, …) — register-only, no field-visit
  tracking of its own; `SIS_URBAN_SERVICES` states whether the service needs one.

### 5. Application Statuses

`applications` (ISD/NISD) runs the full state machine: `pending` → `in_progress` → `escalated` →
`approved` / `rejected`. `service_register_entries` (every code, including ISD/NISD) only ever
carries the coarser `pending` / `approved` / `rejected` the source extract gives — no
`in_progress`/`escalated` split.

**All queries exclude `rejected` applications by default** (a listing says how many were left
out and that asking for them shows them) to prevent ghost data appearing in lists.

### 6. Chatbot Logic (`services/chatbot/`)

**Context Management** — implicit follow-ups:
- `followup_context.py` stores structured context from each deterministic answer in `chat_messages.structured_data`, enabling pronoun-less follow-ups ("which is oldest?", "when was it scheduled?")
- Application number extraction: explicit `"2025/0154/28/000001"` → context reference `"this application"` → conversation history → ask user

**Response Accuracy**:
- Numeric/count data always comes from the database — never from the LLM
- Streaming SSE for better UX

### 7. Agent Layer (LLM Tool Calling)

`backend/services/agent.py` + `backend/services/agent_tools.py`

Runs only as a fallback when all ~60 deterministic handlers pass. The model picks from 14 authorized, read-only domain tools (all backed by `postgres.py`). Authorization is enforced in code, never delegated to the model. Set `AGENT_ENABLED=false` in `.env` to disable.

```
parse_intent → deterministic handler ──────────────► answer (unchanged)
                    │ no handler matched
                    ▼
              agent.gather_evidence()   ← tool-selection loop, ≤3 rounds
                    │
                    ▼
              agent_tools.execute_tool()  ← validate args, enforce jurisdiction
                    │
                    ▼
              agent.build_answer_prompt() → call_llama[_stream]() → answer
```

### 8. Chat Attachments

Officers can upload PDFs, DOCX, CSVs, and TXT files (`POST /api/v1/chat/upload`). Evidence is extracted with source locations (page/paragraph/row), stored in `chat_attachments` / `attachment_chunks` / `attachment_rows`, and cited precisely in answers. CSV arithmetic (count/sum/min/max/group) is computed deterministically — the LLM never touches numbers.

A survey number pulled from an attachment (free text or CSV) is always paired with its subdivision number(s) when the source carries one — see CLAUDE.md's "A survey number from an attachment carries its subdivision number too" for the mechanism, the multi-subdivision case, and the two known-unfixed gaps (reverse lookup, follow-up continuation).

### 9. All 29 Service Codes — Lookup, Comparison, Catalogue

`SIS_URBAN_SERVICES` in `backend/utils/helpers.py` holds all 29 official TAMILNILAM urban service codes (not just ISD `0154` / NISD `0153`). Only `0153`/`0154` are backed by a full ISD/NISD workflow machine in `applications`; the rest have a name, category and field-visit flag, and most carry no documented fee/SLA/workflow anywhere in the corpus — the chatbot states that explicitly rather than inventing one.

**Pairwise comparison ("difference between A and B", "are A and B the same") now resolves both named codes for all 406 possible pairs**, not just the hardcoded F-Line/F-Line-Appeal (`0178`/`0185`) case. This closed `SVCCOMPARE-ALL-001` through `-005` (see `SIS_All_Service_Code_Comparison_Failures.jsonl` and `SIS_All_Service_Code_Comparison_Report.md`):
- `find_service_codes()` / `find_service_code_by_name()` in `helpers.py` split a comparison sentence on delimiters (`and`, `vs`, `versus`, `to`, `மற்றும்`) and resolve each clause independently, instead of scoring the whole message for a single winner.
- `_build_service_code_comparison_response()` in `chatbot.py` is a universal comparison engine (differences / similarities / both / identity-check / ambiguous / table) covering all 406 pairs, replacing logic that used to exist only for `0178` vs `0185`.
- A dedicated `all_service_code_comparison` intent in `rag.py` catches whole-catalogue questions ("compare all supported service codes", "similarities across all service codes") that used to misroute to application listings.
- Fee/SLA/workflow for any pair outside `{0153, 0154}` is answered "not documented in departmental guidelines" — never fabricated.

Catalogue + exhaustive sweep artifacts: `SIS_All_Service_Code_Catalogue.csv` (29 rows), `SIS_All_Service_Code_Comparison_Matrix.csv` (406 rows). Regression: `SIS_ServiceCode_FileDB_Regression_Tests/test_all_servicecode_pairwise_resolution.py`, `test_pairwise_comparison_architectural_disparity.py`, `test_all_service_comparison_routing.py`.

## API Endpoints

**Authentication**
- `POST /auth/login` — User login

**Chat**
- `POST /api/v1/chat/stream` — Streaming chat (SSE)
- `GET /api/v1/chat/history` — Get chat history
- `POST /api/v1/chat/upload` — Upload attachment

**Applications**
- `GET /applications` — List applications
- `GET /applications/{id}` — Get details
- `PUT /applications/{id}` — Update application

## Configuration (`.env`)

```env
# Database
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/SISchatbot
SYNC_DATABASE_URL=postgresql://user:pass@localhost:5432/SISchatbot

# Ollama
OLLAMA_BASE_URL=http://localhost:11434
LLM_MODEL=llama3.1:8b
EMBEDDING_MODEL=nomic-embed-text

# Security
SECRET_KEY=your_secret_key
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=480

# Agent
AGENT_ENABLED=true
AGENT_MAX_ITERATIONS=3
AGENT_TIMEOUT_SECONDS=90

# CORS
CORS_ORIGINS=["http://localhost:3000","http://127.0.0.1:5500"]

# Environment
ENVIRONMENT=development
```

## Database Verification

```powershell
# CSV-shaped layer: structure, orphans, non-leakage
python backend/sample_db/verify_sample_db.py

# Both layers: Aadhaar + CAN formats
python -m backend.sample_db.verify_identifiers

# ORM layer: required fields not NULL
python check_missing_values.py

# Lists every table + row count
python check_sis_chatbot_db_tables.py

# Prints seeded officer logins
python check_login_credentials.py

# Rebuild ORM projection (idempotent — truncates and re-derives)
python -m backend.sample_db.build_app_tables
```

## Testing

```powershell
# Intent routing (no DB or LLM)
python -m backend.sample_db.test_intent_coverage

# Date-scoped questions
python -m backend.sample_db.test_date_queries

# Submission channel: routing, derivation, answers
python -m backend.sample_db.test_channel_queries
python -m backend.sample_db.test_channel_queries --fast

# Workflow invariants + answer consistency
python -m backend.sample_db.test_workflow_logic

# Answer quality
python -m backend.sample_db.test_questions
python -m backend.sample_db.test_questions --fast

# Implicit follow-ups (DB, no LLM)
python test_followup_context.py
python test_followup_context.py --routing

# IGRS Form 6 and CAN number questions
python test_igrs_can_queries.py
python test_igrs_can_queries.py --data

# Chat attachments
python test_attachments.py
python test_attachments.py --fast
python test_attachments.py --no-db

# Survey/subdivision pairing in attachment answers (free text + CSV)
python SIS_Upload_Regression_Tests/test_survey_subdivision_pairing.py

# Agent layer (tool calling, authorization, grounding)
python test_agent_layer.py
python test_agent_layer.py --fast
python test_agent_layer.py --no-db

# Fee / service-charge questions
python test_fee_queries.py
python test_fee_queries.py --fast

# "My last / previous application"
python test_last_application.py
python test_last_application.py --routing

# Temporary sub-division numbers
python test_temp_subdivision_queries.py

# Field visit follow-ups
python test_field_visit_followups.py

# Comparison queries
python test_comparison_queries.py
python test_comparison_queries.py --routing

# Completed applications (approved / rejected)
python test_completed_applications.py

# Visit plan queries
python test_visit_plan_queries.py

# Comprehensive suite (top-level)
python test_comprehensive_suite.py

# 200-question suite (backend-focused)
python backend/test_200_suite.py
```

## Troubleshooting

**Ollama connection**
```powershell
curl http://localhost:11434/api/tags
ollama list
ollama pull llama3.1:8b
ollama pull nomic-embed-text
```

**pgvector extension** must be enabled before running `ingest.py`:
```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

**CORS_ORIGINS** must be a valid JSON array string in `.env`:
```
CORS_ORIGINS=["http://localhost:3000","http://127.0.0.1:5500"]
```

**Windows stdout encoding**: if you see `UnicodeEncodeError` on startup, ensure the UTF-8 reconfiguration block at the top of `main.py` is intact.

**Month filtering** uses fuzzy token matching (`backend/utils/fuzzy.py`) — handles spelling errors like `"jaunary"` → January. Do not replace with naive string comparison.

**`chatbot.py` is very large** — use IDE symbol search to navigate. Key entry points:
- `process_chat_stream()` — streaming chat
- `process_chat()` — non-streaming chat
- `create_chat_session()` — session creation
- `extract_month_from_query()` — month extraction with fuzzy matching

## License

Developed for **National Informatics Centre (NIC)** internship — Tamil Nadu Survey Department.
