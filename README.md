# Sub Inspector Surveyor (SIS) AI Assistant

A bilingual (Tamil/English/Tanglish) AI-powered chatbot for Sub Inspector Surveyor officers in Tamil Nadu, India. Officers interact via natural language chat to manage survey applications, track status, check documents, and query field visits.

## Tech Stack

- **Backend**: FastAPI + SQLAlchemy (async) + PostgreSQL + pgvector
- **Database**: `sis_chatbot_db` and the `knowledge_embeddings` vector store
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

**Test Credentials** (run `python check_login_credentials.py` for the full list):
- `csenthil@sis.tn.gov.in` / `Test@1234` — Ward 002 SIS (Thoothukudi)
- `msivakumar@sis.tn.gov.in` / `Test@1234` — Ward 102 SIS (Thoothukudi)
- `muthulakshmis@sis.tn.gov.in` / `Test@1234` — Ward 103 SIS (Thoothukudi)

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
│   │   ├── chatbot.py        # Main orchestrator (~6500 lines) — entry point for all chat logic
│   │   ├── rag.py            # Intent detection, language detection, LLM calls, prompt building
│   │   ├── postgres.py       # All database query handlers
│   │   ├── agent.py          # LLM tool-calling loop (the general_query fallback)
│   │   ├── followup_context.py  # Reference context for implicit follow-ups
│   │   ├── doc_extract.py    # Uploaded file → located text segments + citations
│   │   ├── attachment_store.py  # Attachment tables, authorization, evidence retrieval
│   │   ├── csv_ops.py        # Deterministic count/sum/min/max/group over CSV rows
│   │   ├── attachment_qa.py  # Which file, which entity, evidence or refusal
│   │   ├── agent_tools.py    # The 14 authorized domain tools the agent may call
│   │   ├── pgvector_store.py # pgvector operations (init, similarity search, ingest)
│   │   ├── embeddings.py     # Embedding generation via Ollama
│   │   └── auth_service.py   # Login, JWT creation/verification
│   ├── sample_db/            # sis_chatbot_db build pipeline — see backend/sample_db/README.md
│   │   ├── seed_sample_db.py      # Creates the DB, applies DDL, generates + inserts rows
│   │   ├── verify_sample_db.py    # Structure/refs/population/signature/non-leakage checks
│   │   ├── build_app_tables.py    # Projects sample tables → the app's ORM tables
│   │   ├── verify_identifiers.py  # Checks every Aadhaar / CAN against identifiers.py
│   │   ├── identifiers.py         # Aadhaar + CAN rules, shared by seed and projection
│   │   ├── check_app_wiring.py    # Smoke test: app queries + chatbot answer from this DB
│   │   ├── question_bank.py       # Shared question set for the suites
│   │   └── README.md              # Source of truth for the DB layout
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
5.  Workload / listing (pending, overdue, isd/nisd/merge applications, jurisdiction_summary)
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

### 3. Database Architecture — `sis_chatbot_db`

One PostgreSQL database holds **two layers**:

**Layer 1 — 16 CSV-shaped tables** (source of record, seeded from TAMILNILAM urban extracts):

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

**Layer 2 — ORM tables** (`backend/models.py`), projected from layer 1 by `build_app_tables.py`:

| Sample table | App table |
|---|---|
| `urban_parcel_register` | `towns` → `wards` → `blocks`, `survey_numbers`, `sub_divisions` |
| `urban_natham_chitta_owner` | `owners`, `survey_ownership` |
| `urban_application_log` | `applications` (+ `applicants`, `application_documents`) |
| `application_workflow_action` | `workflow_history`, `field_visits` |
| `urban_temp_subdivision_parcel` | `application_sub_divisions` |
| `nisd_/isd_transfer_urban_detail` | `patta_transfers` |
| workflow usernames at role 41 | `sis_officers`, `officer_jurisdictions` |

The chatbot queries **only layer 2** through `postgres.py`. `knowledge_embeddings` (768-dim vectors, HNSW index, cosine similarity) lives in the same database.

### 4. Application Types

- **ISD** (`0154`) — **Involving Sub-Division**: field inspection + SD sketch required
- **NISD** (`0153`) — **Not Involving Sub-Division**: document verification only, no field visit
- **MERGE** (`0155`) — Merge application; follows the ISD chain

### 5. Application Statuses

`pending` → `in_progress` → `escalated` → `approved` / `rejected`

Current split: 150 approved, 52 rejected, 5 pending, 2 in progress.

**All queries exclude `rejected` applications** to prevent ghost data appearing in lists.

### 6. Chatbot Logic (`services/chatbot.py`)

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
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/sis_chatbot_db
SYNC_DATABASE_URL=postgresql://user:pass@localhost:5432/sis_chatbot_db

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
