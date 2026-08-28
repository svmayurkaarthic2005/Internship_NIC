# SIS Chatbot — AI Assistant Guide

## Project Overview

**Sub Inspector Surveyor (SIS) AI Chatbot** — A bilingual (Tamil/English/Tanglish) AI assistant for Sub Inspector Surveyor officers in Tamil Nadu, India. Officers interact via natural language chat to manage survey applications, track status, check documents, and query field visits.

- **Backend**: FastAPI + SQLAlchemy (async) + PostgreSQL + pgvector
- **Database**: `sis_chatbot_db` on `127.0.0.1:5432` — the single database for everything (CSV-shaped source tables, ORM tables, and the `knowledge_embeddings` vector store). ChromaDB is gone; there is no separate vector DB and no `vectorstore/` directory.
- **LLM**: Ollama (`llama3.1:8b`) running locally
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
python -m backend.sample_db.build_app_tables   # 3. project them into the app's ORM tables
python -m backend.sample_db.verify_identifiers # 4. verify every Aadhaar / CAN in both layers
python -m backend.ingest                       # 5. load document embeddings into knowledge_embeddings

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
│   ├── routers/
│   │   ├── auth.py           # POST /auth/login
│   │   ├── chat.py           # POST /api/v1/chat/stream, GET /api/v1/chat/history
│   │   ├── applications.py   # GET/PUT /applications
│   │   └── survey.py         # Survey endpoints
│   ├── services/
│   │   ├── chatbot.py        # Main orchestrator (~6500 lines) — entry point for all chat logic
│   │   ├── rag.py            # Intent detection, language detection, LLM calls, prompt building
│   │   ├── postgres.py       # All database query handlers (get_officer_applications, etc.)
│   │   ├── pgvector_store.py # pgvector operations (init, similarity search, ingest)
│   │   ├── embeddings.py     # Embedding generation via Ollama
│   │   └── auth_service.py   # Login, JWT creation/verification
│   ├── sample_db/            # sis_chatbot_db build pipeline — see backend/sample_db/README.md
│   │   ├── schema_builder.py      # Reads CSV headers, infers PG types, emits DDL
│   │   ├── identifiers.py         # Aadhaar + CAN rules, shared by seed and projection
│   │   ├── schema_sis_chatbot_db.sql  # Generated DDL (regenerate, don't hand-edit)
│   │   ├── dsc.py                 # X.509 certs + PKCS#7 signatures for the DSC columns
│   │   ├── seed_sample_db.py      # Creates the DB, applies DDL, generates + inserts rows
│   │   ├── verify_sample_db.py    # Structure/refs/population/signature/non-leakage checks
│   │   ├── build_app_tables.py    # Projects sample tables → the app's ORM tables
│   │   ├── verify_identifiers.py  # Checks every Aadhaar / CAN against identifiers.py
│   │   ├── check_app_wiring.py    # Smoke test: app queries + chatbot answer from this DB
│   │   ├── question_bank.py       # Shared question set for the suites
│   │   ├── test_intent_coverage.py / test_workflow_logic.py / test_questions.py
│   │   ├── test_date_queries.py   # date-scoped questions: parsing + answers
│   │   └── README.md              # Source of truth for the DB layout
│   ├── sample_table/         # TAMILNILAM urban CSV extracts (gitignored; empty in a fresh clone)
│   ├── documents/            # RAG corpus: workflow_guide.txt, faq_*.txt, survey_manual.txt, ...
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
└── CLAUDE.md                 # This file
```

---

## Database Layout — `sis_chatbot_db`

One PostgreSQL database holds **two layers**. Know which one you are touching before you write a query.

**Layer 1 — the 16 CSV-shaped tables** (source of record, seeded from the TAMILNILAM urban extracts). Column lists come straight from the CSV headers; each table gets a `row_id BIGSERIAL` PK because the extracts have no natural key. Code columns (`service_code`, `district_code`, `block_code`, statuses) stay `VARCHAR` so leading zeros survive — `0154`, `0015`, `01` must never become integers.

| Table | Rows | Table | Rows |
|---|---|---|---|
| `urban_application_log` | 1180 | `nisd_transfer_old_owner` | 630 |
| `application_workflow_action` | 4589 | `nisd_transfer_return_owner` | 46 |
| `urban_temp_subdivision_parcel` | 49 | `nisd_transfer_urban_detail` | 229 |
| `urban_temp_subdivision_owner` | 117 | `isd_transfer_application_info` | 41 |
| `nisd_transfer_application_info` | 166 | `isd_transfer_urban_detail` | 50 |
| `nisd_transfer_igrs_owner` | 100 | `urban_parcel_register` | 1033 |
| `nisd_transfer_new_owner` | 620 | `urban_parcel_signature` | 800 |
| `urban_natham_chitta_owner` | 551 | `urban_natham_chitta_signature` | 400 |

**Layer 2 — the app's ORM tables** (`backend/models.py`), a projection built from layer 1 by `build_app_tables.py`:

| Sample table | App table |
|---|---|
| `urban_parcel_register` | `districts` → `blocks`, `survey_numbers`, `sub_divisions` |
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

1. `greeting` / `farewell` — "Hello", "வணக்கம்", "நன்றி"
2. Deterministic identifiers — `application_status` ("Status of 2025/0154/28/000001"), `survey_detail`, `can_number_info`
3. Per-application checks — `joint_owner_check`, `check_documents`, `check_sale_deed`, `is_nisd_or_isd`, `litigation_check`
4. Workload / listing — `pending_applications`, `overdue_applications`, `officer_workload`, `isd_applications`, `nisd_applications`, `merge_applications`, `jurisdiction_summary`
5. Field-visit family (`fv_*`) — scheduling, rescheduling, conflicts, overdue inspections
6. Sub-division desk family (`sd_*`) — sketch readiness, encroachment, forwarding, remarks
7. Reference lookups — `service_code_lookup`, `service_code_guide`, `sub_registrar`, `rejection_info`
8. `general_query` — falls back to RAG / pgvector search

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

### Workflow

**ISD** (`0154`, Involving Sub-Division) — the full chain:

```
application  (CSC operator / citizen portal / Sub-Registrar referral)
   → SIS        Sub Inspector Surveyor — mandatory field inspection & cadastral verification
   → SD         Senior Draughtsman — prepares the sub-division / FMB sketch
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

Each hop is a row in `application_workflow_action` (layer 1), projected into
`workflow_history` (layer 2) by `build_app_tables.py` through `ROLE_TO_STAGE`.
`workflow_history.performed_at` comes from `last_updated_datetime`, not
`action_date`: a file often clears three desks in one day, and dating the hops
to the day alone loses their order.

### Workflow Roles

The role ids in `application_workflow_action`, read off the data rather than
assumed — the applications whose wording says "Send to SIS" are sitting at role
44 or 41, and 42 shares its actors with 44:

| role | who | stage |
|---|---|---|
| `1` | the CSC / e-Sevai operator or citizen who submits | not a desk (no `from_stage`) |
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
