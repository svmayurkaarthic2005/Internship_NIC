# Sub Inspector Surveyor (SIS) AI Assistant

A bilingual (Tamil/English/Tanglish) AI-powered chatbot for Sub Inspector Surveyor officers in Tamil Nadu, India. Officers interact via natural language chat to manage survey applications, track status, check documents, and query field visits.

**Covers every TAMILNILAM urban service code, not just ISD/NISD.** ISD (`0154`) and NISD (`0153`) are the two codes with a full multi-desk workflow machine (`applications` / `workflow_history` / `field_visits`), but an officer's jurisdiction sees applications under other codes too — Modification (`0158`), Addition (`0159`), Deletion (`0160`), ULC Subdivision (`0164`), F-Line (`0178`), TSLR (`0156`), and more. Every one of them is answered equally: listings, counts, status filters, comparisons and per-file detail all read from the flat `service_register_entries` register (see "Database Architecture" below), with no code singled out or silently dropped.

## Tech Stack

- **Backend**: FastAPI + SQLAlchemy (async) + PostgreSQL + pgvector
- **Database**: `sis_chatbot_db` and the `knowledge_embeddings` vector store
- **LLM**: Ollama (`llama3.1:8b`) running locally
- **Embeddings**: `nomic-embed-text` via Ollama
- **OCR**: Tesseract (`eng`+`tam`), used as a fallback for scanned/image-only PDF pages
- **Frontend**: Vanilla HTML/CSS/JS (no framework)
- **Auth**: JWT (python-jose + passlib/bcrypt)

## Quick Start

### Prerequisites
- Python 3.8+
- PostgreSQL 12+ with the `pgvector` extension
- Ollama with models: `llama3.1:8b`, `nomic-embed-text`
- Tesseract OCR with the `eng` and `tam` language packs (optional — only needed for scanned-document uploads)

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
python backend/sample_db/seed_sample_db.py     # Create DB + seed the CSV-shaped source tables
python backend/sample_db/verify_sample_db.py   # Verify structure/refs/signatures/non-leakage
python -m backend.sample_db.load_master_dumps  # Load the TAMILNILAM master pg_dumps
python -m backend.sample_db.adopt_master_district_taluk  # First run only
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

**Test credentials**: officer accounts seeded from the workflow chain, password `Test@1234`.
Query the database directly for the current list (`SELECT email, name FROM sis_officers;`) — see CLAUDE.md's "Dev Commands" for the officer-lookup query, since seeded accounts change whenever the database is reseeded.

## Project Structure

```
nic_internship/
├── backend/
│   ├── main.py               # FastAPI app, lifespan, middleware, routers
│   ├── config.py             # pydantic-settings (Settings class)
│   ├── database.py           # Async SQLAlchemy engine + Base + get_db()
│   ├── models.py             # All SQLAlchemy ORM models (UUID PKs, TIMESTAMP(tz=True))
│   ├── schemas.py            # Pydantic schemas (StandardResponse, OfficerContext, etc.)
│   ├── dependencies.py       # get_current_officer() JWT dependency
│   ├── ingest.py             # Document ingestion into pgvector (knowledge_embeddings)
│   ├── routers/
│   │   ├── auth.py           # POST /auth/login
│   │   ├── chat.py           # POST /api/v1/chat (+ /stream, /upload), GET /chat/history
│   │   ├── applications.py   # GET/PUT /applications
│   │   └── survey.py         # Survey endpoints
│   ├── services/
│   │   ├── chatbot/          # The orchestrator package (~45 modules):
│   │   │   ├── orchestrator.py          # process_chat() / process_chat_stream() — main entry points
│   │   │   ├── chat_dispatch.py / _stream.py  # routes a parsed intent to its deterministic handler
│   │   │   ├── chat_application_queries.py    # all-service-code register listings/counts
│   │   │   ├── chat_shared.py           # shared renderers, status/field logic
│   │   │   ├── chat_followups.py / chat_smalltalk.py / chat_corrections.py / chat_routing.py
│   │   │   ├── agent.py, agent_tools.py # LLM tool-calling fallback (read-only domain tools)
│   │   │   ├── attachment_qa.py, calc_qa.py, neg_scope.py, qualifier_guard.py
│   │   │   └── *_qa.py                  # per-record-type handlers: owner, parcel, F-Line, TSLR,
│   │   │                                 #   workflow, cross-checks, …
│   │   ├── rag.py, rag_language.py, rag_llm.py  # intent detection, language detection, LLM calls
│   │   ├── postgres.py       # DB-only query functions (ISD/NISD `applications` + the
│   │   │                     #   all-code `service_register_entries` register)
│   │   ├── followup_context.py   # structured context for pronoun-less follow-ups
│   │   ├── attachment_store.py, doc_extract.py, csv_ops.py   # uploaded-file handling
│   │   ├── pgvector_store.py, embeddings.py                  # knowledge_embeddings (RAG corpus)
│   │   ├── readonly_guard.py # enforces that a chat turn can read the register but never write it
│   │   ├── llm_gate.py, overdue_refresh.py
│   │   └── auth_service.py
│   ├── sample_db/            # DB build/seed/verify pipeline — see backend/sample_db/README.md
│   ├── documents/            # RAG corpus: workflow_guide.txt, faq_*.txt, survey_manual.txt, land_rules.txt, …
│   └── utils/
│       ├── fuzzy.py          # Fuzzy month/token matching for typo tolerance
│       ├── helpers.py        # SIS_URBAN_SERVICES service-code table + misc helpers
│       ├── sla.py            # Field-visit deadline + service SLA rules
│       ├── translit.py       # Tamil <-> Latin name transliteration
│       └── logger.py         # structlog-based logger (get_logger)
├── frontend/
│   ├── login.html / chatbot.html / channel_report.html
│   ├── css/
│   └── js/                   # auth.js, chat.js, chatStorage.js, table_renderer.js, config.js
├── test_questions_*.txt      # Plain-text question sets (one per line) used by the DB-side test suites
├── serve_frontend.py, start_backend.ps1, quick_setup.py, benchmark_ocr_engines.py
├── .env                      # Secrets (not committed)
├── .env.example               # Template
├── requirements.txt
├── CLAUDE.md                 # Comprehensive AI assistant / developer guide
└── README.md                 # This file
```

## How It Works

### 1. Request Flow

```
POST /api/v1/chat
        ↓
  chat.py router
        ↓
  orchestrator.py → process_chat() / process_chat_stream()   ← MAIN ENTRY POINTS
        ↓
  rag.py → parse_intent()              ← classify the user message (~60 intents)
        ↓
  postgres.py → <query handler>()      ← fetch structured DB data
        ↓
  rag_llm.py → call_llama[_stream]()   ← LLM response (fallback only; most intents never reach it)
        ↓
  JSON / SSE stream → frontend
```

### 2. Intent Detection (`rag.py`)

`parse_intent()` resolves ~60 intents by exact token-boundary matching plus edit-distance typo tolerance. Rough priority order:

```
1.  greeting / sign-off / thanks
2.  Deterministic identifiers (application_status, survey_detail, can_number_info)
3.  last_application — "my previous / last approved application"
4.  Per-application checks (joint_owner_check, check_documents, check_sale_deed,
    is_nisd_or_isd, litigation_check)
5.  Workload / listing (pending, overdue, isd/nisd applications, jurisdiction_summary)
6.  Field-visit family (fv_*)
7.  Sub-division desk family (sd_*)
8.  Reference lookups (service_code_lookup, service_code_guide, rejection_info)
9.  compare_applications — several shapes (older, type/status/channel counts, duration,
    ward, period, month, superlative)
10. general_query — falls back to the agent layer (tool calling), then plain RAG / pgvector
```

**Language Detection** (`rag_language.py → detect_language()`):
- **Tamil**: Unicode range U+0B80–U+0BFF detection
- **Tanglish**: Phonetic patterns (e.g., "vanakkam", "enna")
- **English**: Default fallback

### 3. Database Architecture — `sis_chatbot_db`

One PostgreSQL database. Two layers worth knowing apart:

**Register — every service code, one flat table.** `service_register_entries` is the source for any question that names no specific type — listings, counts, status filters, per-ward/block/channel breakdowns, comparisons. No code is singled out: `get_all_service_code_applications()` in `postgres.py` and `chat_application_queries.py` / `chat_shared.py` in the chatbot package query and render it generically, keyed off `SIS_URBAN_SERVICES` (`backend/utils/helpers.py`), never a hardcoded code list.

**The ISD/NISD workflow machine.** `applications` is narrower by design: only ISD (`0154`) and NISD (`0153`) get a full multi-desk workflow (`workflow_history`, `field_visits`, `patta_transfers`, `applicants`, `application_documents`) — the department's own data only runs that chain for those two codes. A question that names ISD/NISD specifically, or something only they carry (overdue field visits, workflow stage, per-file applicant detail), is answered from here instead of the flat register.

**Per-code detail tables**, each read by its own `*_qa.py` handler in `backend/services/chatbot/`, for the service codes that carry extra structure beyond the register row (F-Line, TSLR, modification/correction flows, sub-division sketches, patta-transfer owners, etc.).

**Geography masters** (department-wide, statewide): district, taluk, town, ward, block — loaded from TAMILNILAM pg_dumps; the app's own `towns` → `wards` → `blocks` carry the officer-jurisdiction scoping and reference the masters for names.

**Everything the chatbot reads goes through `postgres.py`** — no handler queries a table directly. `knowledge_embeddings` (vector store, HNSW index, cosine similarity) lives in the same database and backs the RAG/LLM fallback.

### 4. Application Types

- **ISD** (`0154`) — **Involving Sub-Division**: field inspection + SD sketch required
- **NISD** (`0153`) — **Not Involving Sub-Division**: document verification only, no field visit
- **Every other service code** — register-only, no field-visit tracking of its own; `SIS_URBAN_SERVICES` states whether the service needs one

### 5. Application Statuses

`applications` (ISD/NISD) runs the full state machine: `pending` → `in_progress` → `escalated` → `approved` / `rejected`. `service_register_entries` (every code, including ISD/NISD) only ever carries the coarser `pending` / `approved` / `rejected` the source extract gives — no `in_progress`/`escalated` split.

**All queries exclude `rejected` applications by default** (a listing says how many were left out, and asking for them shows them) to prevent ghost data appearing in lists.

### 6. Chatbot Logic (`services/chatbot/`)

**Context management — implicit follow-ups:**
- `followup_context.py` stores structured context from each deterministic answer in `chat_messages.structured_data`, enabling pronoun-less follow-ups ("which is oldest?", "when was it scheduled?")
- Application number extraction: explicit `"2026/0154/02/001234"` → context reference `"this application"` → conversation history → ask user

**Response accuracy:**
- Numeric/count data always comes from the database — never from the LLM
- Uploaded-file arithmetic (CSV count/sum/min/max/group) is computed deterministically too
- Streaming SSE for better UX

### 7. Agent Layer (LLM Tool Calling)

`backend/services/chatbot/agent.py` + `agent_tools.py`

Runs only as a fallback when the deterministic handlers pass. The model picks from a fixed set of authorized, read-only domain tools (all backed by `postgres.py`). Authorization is enforced in code, never delegated to the model. Set `AGENT_ENABLED=false` in `.env` to disable.

```
parse_intent → deterministic handler ──────────────► answer (unchanged)
                    │ no handler matched
                    ▼
              agent.gather_evidence()   ← tool-selection loop, bounded rounds
                    │
                    ▼
              agent_tools.execute_tool()  ← validate args, enforce jurisdiction
                    │
                    ▼
              agent.build_answer_prompt() → call_llama[_stream]() → answer
```

### 8. Chat Attachments

Officers can upload PDFs, DOCX, CSVs, and TXT files (`POST /api/v1/chat/upload`). Evidence is extracted with source locations (page/paragraph/row), stored in `chat_attachments` / `attachment_chunks` / `attachment_rows`, and cited precisely in answers. CSV arithmetic is computed deterministically — the LLM never touches numbers. A scanned/image-only PDF page falls back to Tesseract OCR (English + Tamil) automatically; the officer is told when a page was read this way.

A survey number pulled from an attachment (free text or CSV) is always paired with its subdivision number(s) when the source carries one.

### 9. All Service Codes — Lookup, Comparison, Catalogue

`SIS_URBAN_SERVICES` in `backend/utils/helpers.py` holds all the official TAMILNILAM urban service codes (not just ISD `0154` / NISD `0153`). Only `0153`/`0154` are backed by a full ISD/NISD workflow machine in `applications`; the rest have a name, category and field-visit flag, and most carry no documented fee/SLA/workflow anywhere in the corpus — the chatbot states that explicitly rather than inventing one.

Pairwise comparison ("difference between A and B", "are A and B the same") resolves both named codes, not just a hardcoded special case — `find_service_codes()` / `find_service_code_by_name()` in `helpers.py` split a comparison sentence on delimiters (`and`, `vs`, `versus`, `to`, `மற்றும்`) and resolve each clause independently.

## API Endpoints

**Authentication**
- `POST /auth/login` — Officer login

**Chat**
- `POST /api/v1/chat` — Send a message (non-streaming)
- `POST /api/v1/chat/stream` — Streaming chat (SSE)
- `GET /api/v1/chat/history` — Get chat history
- `POST /api/v1/chat/upload` — Upload an attachment

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

# Smoke test: app queries + chatbot answer from this DB
python -m backend.sample_db.check_app_wiring --chat

# Rebuild ORM projection (idempotent — truncates and re-derives)
python -m backend.sample_db.build_app_tables
```

## Testing

There is no standalone top-level test suite in this checkout. For ad-hoc verification:

- `test_questions_*.txt` (repo root) are plain-text question sets, one question per line, for manual or scripted spot-checks against a running backend.
- `backend/sample_db/` contains the DB build/verify pipeline above, which is the most reliable way to confirm the database and ORM projection are internally consistent after a reseed.
- For behavioural changes, drive the running app directly: start the backend and frontend (see Quick Start), log in with a seeded officer account, and exercise the chat/upload endpoints.

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

**Tesseract OCR** (only needed for scanned-document uploads):
```powershell
tesseract --list-langs   # must show both "eng" and "tam"
```

**CORS_ORIGINS** must be a valid JSON array string in `.env`:
```
CORS_ORIGINS=["http://localhost:3000","http://127.0.0.1:5500"]
```

**Windows stdout encoding**: if you see `UnicodeEncodeError` on startup, ensure the UTF-8 reconfiguration block at the top of `backend/main.py` is intact.

**Month filtering** uses fuzzy token matching (`backend/utils/fuzzy.py`) — handles spelling errors like `"jaunary"` → January. Do not replace with naive string comparison.

**The chatbot orchestration layer is large** — it's split across ~45 modules under `backend/services/chatbot/` rather than one file; use IDE symbol search within that directory. Key entry points:
- `orchestrator.py → process_chat_stream()` — streaming chat
- `orchestrator.py → process_chat()` — non-streaming chat

## License

Developed for **National Informatics Centre (NIC)** internship — Tamil Nadu Survey Department.
