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

# First-time DB setup (run in order)
python create_database.py      # Creates the sis_db PostgreSQL database
python backend/seed.py         # Seeds all test data
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

# Reset data (wipes + re-seeds)
python reset_database.py

# Run tests
python test_comprehensive_suite.py
python backend/test_200_suite.py
```

**API Docs** (dev only): http://localhost:8000/api/docs

**Test login credentials**:
- `arjun.kumar@sis.tn.gov.in` / `Test@1234` — Block SIS
- `priya.devi@sis.tn.gov.in` / `Test@1234` — Ward SIS
- `ramesh.babu@sis.tn.gov.in` / `Test@1234` — Taluk SIS
- `lakshmi.narayanan@sis.tn.gov.in` / `Test@1234` — District SIS

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
└── CLAUDE.md                 # This file
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

### Critical DB Constraint
```sql
-- Only one active application per survey number at a time
CREATE UNIQUE INDEX idx_unique_active_app_per_survey
ON applications (survey_number_id)
WHERE current_status IN ('pending', 'in_progress', 'escalated');
```

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
