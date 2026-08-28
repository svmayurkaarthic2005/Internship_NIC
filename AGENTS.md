# SIS Chatbot — AI Assistant Guide

## Project Overview

**Sub Inspector Surveyor (SIS) AI Chatbot** — A bilingual (Tamil/English/Tanglish) AI assistant for Sub Inspector Surveyor officers in Tamil Nadu, India. Officers interact via natural language chat to manage survey applications, track status, check documents, and query field visits.

- **Backend**: FastAPI + SQLAlchemy (async) + PostgreSQL + pgvector
- **LLM**: Ollama (`llama3.1:8b`) running locally
- **Embeddings**: `nomic-embed-text` via Ollama
- **Frontend**: Vanilla HTML/CSS/JS (no framework)
- **Auth**: JWT (python-jose + passlib/bcrypt)
- **Speech**: Faster-Whisper for STT

---

## Dev Commands

```powershell
# Activate virtual environment (Windows)
.venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# First-time DB setup (sis_chatbot_db is already created and seeded)
# The database sis_chatbot_db should already exist with sample data
# If you need to re-ingest documents:
python backend/ingest.py       # Ingests documents into pgvector

# Start backend
python -m uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
# Or use the PowerShell script:
.\start_backend.ps1

# Serve frontend (separate terminal)
python serve_frontend.py       # Serves on http://localhost:3000

# Database integrity checks
python verify_no_duplicates.py   # Quick duplicate check
python check_missing_values.py   # Deep field validation

# Check login credentials
python check_login_credentials.py  # Shows current officers and passwords

# Run tests
python test_comprehensive_suite.py
python backend/test_200_suite.py
```

**API Docs** (dev only): http://localhost:8000/api/docs

**Test login credentials** (sis_chatbot_db):
- `csenthil@sis.tn.gov.in` / `Test@1234` — Ward 2 SIS (Thoothukudi) — Employee ID: SIS-001
- `msivakumar@sis.tn.gov.in` / `Test@1234` — Ward 102 SIS (Thoothukudi) — Employee ID: SIS-002
- `muthulakshmis@sis.tn.gov.in` / `Test@1234` — Ward 103 SIS (Thoothukudi) — Employee ID: SIS-003

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
│   ├── ingest.py             # Document ingestion into pgvector
│   ├── seed.py               # Full database seeding (officers, applications, etc.)
│   ├── schema.sql            # Raw SQL schema reference
│   ├── routers/
│   │   ├── auth.py           # POST /auth/login
│   │   ├── chat.py           # POST /api/v1/chat/stream, GET /api/v1/chat/history
│   │   ├── applications.py   # GET/PUT /applications
│   │   ├── survey.py         # Survey endpoints
│   │   └── speech.py         # TTS/STT endpoints
│   ├── services/
│   │   ├── chatbot.py        # Main orchestrator (~6500 lines) — entry point for all chat logic
│   │   ├── rag.py            # Intent detection, language detection, LLM calls, prompt building
│   │   ├── postgres.py       # All database query handlers (get_officer_applications, etc.)
│   │   ├── pgvector_store.py # pgvector operations (init, similarity search, ingest)
│   │   ├── embeddings.py     # Embedding generation via Ollama
│   │   ├── auth_service.py   # Login, JWT creation/verification
│   │   └── speech_service.py # Faster-Whisper STT
│   └── utils/
│       ├── fuzzy.py          # Fuzzy month/token matching for typo tolerance
│       ├── helpers.py        # Misc helpers
│       └── logger.py         # structlog-based logger (get_logger)
├── frontend/
│   ├── login.html
│   ├── chatbot.html
│   ├── css/
│   └── js/
├── vectorstore/              # pgvector document store (via PostgreSQL)
├── .env                      # Secrets (not committed)
├── .env.example              # Template
├── requirements.txt
└── AGENTS.md                 # This file
```

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

1. `greeting` — "Hello", "வணக்கம்"
2. `farewell` — "Bye", "நன்றி"
3. `joint_owner_check` — "Who are the joint owners?"
4. `application_status` — "Status of APP-2024-000001"
5. `check_documents` — "What documents are missing?"
6. `check_sale_deed` — "Is sale deed registered?"
7. `is_nisd_or_isd` — "What type is this application?"
8. `field_specific_query` — "What is the applicant name?"
9. `general_query` — Falls back to RAG/vector search

### Language Detection

Handled in `rag.py → detect_language()`:
- **Tamil**: Unicode range U+0B80–U+0BFF detection
- **Tanglish**: Phonetic patterns (e.g., "vanakkam", "enna")
- **English**: Default fallback

---

## Key Domain Concepts

### FMB (Field Measurement Book) - Limited Data
**Note**: While the database has FMB sketch workflow columns (`sketch_sent_date`, `sketch_received_date` in `isd_transfer_urban_detail`), there is **no actual FMB data** in the current dataset:
- 31 out of 50 ISD applications have `sketch_sent_date` populated
- 0 applications have `sketch_received_date` (all NULL)
- No FMB book numbers, page numbers, or actual sketches stored
- SD = Senior Draughtsman (Survey Department officer)

The FMB workflow exists in the schema but is not actively used in this test database.

### Citizen Identifiers

`backend/sample_db/identifiers.py` holds both formats, shared by the seed and
the ORM projection:

- **Aadhaar** — synthetic, 12 digits, leading digit 2-9, valid Verhoeff check
  digit, derived from the person's name so one person keeps one number across
  every extract and across both layers.
- **CAN** (Citizen Access Number) — the length identifies the channel: **15
  digits** for a Common Service Centre / e-Sevai submission, **12 digits** for
  one the citizen filed on the portal. `urban_application_log.source_name`
  decides the channel (`-` = citizen, an operator code = CSC) and the length is
  enforced against it when `applications` is projected. Layer 1 keeps the
  extract's value verbatim.

`python -m backend.sample_db.verify_identifiers` re-checks both in the built DB.

### Application Types
- **ISD** — Individual Sub-Division
- **NISD** — Non-Individual Sub-Division
- **MERGE** — Merge application (multiple survey numbers combined)

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

### Workflow Roles

The role ids in `application_workflow_action`, read off the data rather than
assumed — the applications whose wording says "Send to SIS" are sitting at role
44 or 41, and 42 shares its actors with 44:

| role | who | stage |
|---|---|---|
| `1` | the CSC / e-Sevai operator who submits | not a desk (no `from_stage`) |
| `44`, `42`, `41` | the surveyor's office | `SIS` |
| `8` | Senior Draughtsman | `SD` |
| `16` | ZDT / HQDT, who approves and generates the order | `TAHSILDAR` |

`workflow_history.performed_at` comes from `last_updated_datetime`, not
`action_date`: a file often clears three desks in one day, and dating the hops
to the day alone loses their order.

### Active applications per survey number
```sql
-- A parcel can be under more than one live request at a time -- the TAMILNILAM
-- extracts contain such survey numbers -- so this is a plain index, not unique.
CREATE INDEX idx_active_app_per_survey
ON applications (survey_number_id)
WHERE current_status IN ('pending', 'in_progress', 'escalated');
```
An older schema made this UNIQUE; `build_app_tables.py` drops that superseded
index if the database still carries it, since enforcing it meant rewriting real
statuses to fit.

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

- **`chatbot.py`** is the single orchestration layer — all chat logic flows through it. It is large (~6500 lines) by design; new intent handlers belong here or in `postgres.py`.
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

5. **Application number extraction** follows a fallback chain:
   - Explicit: `"APP-2024-000001"` → matched directly
   - Reference: `"this application"` → checks last 2 messages in history
   - Field query: `"what is the name?"` → checks conversation context
   - No reference found → chatbot asks user to specify

6. **CORS_ORIGINS** in `.env` must be a valid JSON array string, e.g.:
   ```
   CORS_ORIGINS=["http://localhost:3000","http://127.0.0.1:5500"]
   ```
   The `Settings` validator parses it from string automatically.

7. **Windows stdout encoding**: If you see `UnicodeEncodeError` on startup, ensure the UTF-8 reconfiguration block at the top of `main.py` is intact.

8. **Month filtering** uses fuzzy token matching (`backend/utils/fuzzy.py`) — handles spelling errors like `"jaunary"` → January. Do not replace this with naive string comparison.

---

## Testing

```powershell
# Comprehensive suite (top-level)
python test_comprehensive_suite.py

# 200-question suite (backend-focused)
python backend/test_200_suite.py

# Test question sets (plain text, one question per line)
# test_questions_100.txt, test_questions_200.txt, test_questions_206.txt
```

---

## License

Developed for **National Informatics Centre (NIC)** internship — Tamil Nadu Survey Department.
