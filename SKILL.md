---
name: sis-chatbot-dev
description: >-
  Use this skill when working on the SIS (Sub Inspector Surveyor) Chatbot project
  at c:\proj\nic_internship. Covers the end-to-end workflow for adding new chat
  intents, running the test suites, and managing the database (seed / reset /
  integrity checks). Activate when the user asks to add a feature, fix a bug,
  run tests, or reset data in this project.
---

# SIS Chatbot Developer Skill

> Always read `CLAUDE.md` at the project root first for architecture context before making changes.

---

## 1. Adding a New Chat Intent

Follow these files in order: `rag.py`, `postgres.py`, then the two dispatch modules of the
`backend/services/chatbot/` package (one per chat path).

### Step 1 — Register the intent keyword in `rag.py`

File: [`backend/services/rag.py`](file:///c:/proj/nic_internship/backend/services/rag.py)

1. Find `parse_intent()` — it returns a string intent name.
2. Add your new intent **above** `general_query` (which is always the last fallback).
3. Use pattern matching + semantic similarity if needed. Follow the existing style:
   ```python
   # Example pattern
   if any(kw in message_lower for kw in ["your", "keywords"]):
       return "your_intent_name"
   ```
4. Add any extraction helpers you need (e.g., `extract_X_from_text()`) near the bottom of `rag.py`.

### Step 2 — Add the DB query handler in `postgres.py`

File: [`backend/services/postgres.py`](file:///c:/proj/nic_internship/backend/services/postgres.py)

1. Create an `async def get_your_data(db: AsyncSession, officer_context: OfficerContext, ...) -> dict` function.
2. Use `select()` + `await db.execute()` — never use `session.query()`.
3. **Always** filter out `rejected` applications:
   ```python
   .where(Application.current_status != "rejected")
   ```
4. Return a plain `dict` with a `"data"` key; the dispatch modules unpack it.

### Step 3 — Wire it into both chat paths

Files: [`chat_dispatch.py`](file:///c:/proj/nic_internship/backend/services/chatbot/chat_dispatch.py)
(`dispatch_intent()`, used by `process_chat`) and
[`chat_dispatch_stream.py`](file:///c:/proj/nic_internship/backend/services/chatbot/chat_dispatch_stream.py)
(`dispatch_intent_stream()`, used by `process_chat_stream`), both in `backend/services/chatbot/`.
The old single `chatbot.py` is now this package; `orchestrator.py` holds the two entry points.

1. Add the same `intent == "your_intent_name"` branch to **both** dispatch functions.
2. Call your `postgres.py` handler and render the answer from its data (the stream path yields SSE chunks).
3. Streaming and non-streaming must answer identically -- add a test that runs both.
4. Numeric/count data must come from DB **directly** — do not ask the LLM to count.

### Step 4 — Verify

```powershell
# Start backend and hit the streaming endpoint manually
python -m uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000

# In another terminal, send a test message
curl -X POST http://localhost:8000/api/v1/chat/stream `
  -H "Authorization: Bearer <jwt_token>" `
  -H "Content-Type: application/json" `
  -d '{"message": "your test query", "session_id": "<uuid>", "language": "auto"}'
```

---

## 2. Running the Test Suites

### Unit + pipeline suite (pytest)

```powershell
.venv\Scripts\activate
python -m pytest tests/unit -q
```

- Needs the live PostgreSQL (`sis_chatbot_db`) and the seeded officers; no LLM calls.
- Add new tests under `tests/unit/` (fixtures `db_session`, `test_officer`, `secondary_officer` in `tests/conftest.py`).
- The old `test_comprehensive_suite.py` / `backend/test_200_suite.py` scripts were removed (commit `39f9e25`);
  the question banks `test_questions_*.txt` remain for manual runs.

### Interpreting Failures

| Failure Pattern | Likely Cause | Fix Location |
|---|---|---|
| Wrong intent returned | Intent keywords too generic | `parse_intent()` in `rag.py` |
| Correct intent but wrong data | DB query filter issue | `postgres.py` handler |
| Correct data but wrong format | Prompt/context builder | `build_prompt()` in `rag.py` |
| Count is wrong | LLM hallucinated the count | Force DB count — never use LLM for numerics |
| Month filter not working | Fuzzy match missed | `backend/utils/fuzzy.py` — add token variant |

---

## 3. Database Workflow

### First-Time Setup

Follow "First-time DB setup" in `CLAUDE.md` (Dev Commands) and `backend/sample_db/README.md`.
Note that the live database was built by `backend/sample_db_new/` (`load_sample_db_new.py`,
`build_app_tables_new.py`), not the older `backend/sample_db/` seed; then:

```powershell
python -m backend.ingest          # Ingest documents into pgvector (knowledge_embeddings)
```

**Prerequisite**: pgvector must be installed in PostgreSQL:
```sql
-- Run once in psql as superuser
CREATE EXTENSION IF NOT EXISTS vector;
```

### Rebuilding Data

```powershell
python -m backend.sample_db.build_app_tables   # re-derive the ORM projection (idempotent)
```

There is no reset script any more (`reset_database.py` was removed).

### Integrity Checks

Always run after seeding or schema changes:

```powershell
python backend/sample_db/verify_sample_db.py     # CSV-shaped layer: structure, orphans, non-leakage
python -m backend.sample_db.verify_identifiers   # Aadhaar + CAN formats, both layers
python -m backend.sample_db.check_app_wiring --chat   # app queries + a chat answer
```

---

## 4. Key Rules to Never Break

1. **Never use `datetime.utcnow()`** — use `datetime.now(timezone.utc)` (Python 3.12+ deprecation).
2. **Never hardcode DB URLs or secrets** — always read from `settings` in `backend/config.py`.
3. **Never use `session.query()`** — use async `select()` + `await session.execute()`.
4. **Never let the LLM count or enumerate application numbers** — always fetch from DB and inject into context.
5. **Never add `print()` in service/router code** — use `get_logger(__name__)` from `backend.utils.logger`.
6. **Always exclude rejected applications** in query results to avoid ghost data.
7. **A survey number answered from an uploaded document must carry its subdivision number(s)** when the source has one — don't add a new entity-extraction path for survey numbers without routing it through `survey_subdivision_pairs()` in `attachment_qa.py` (or its CSV counterpart in `csv_ops.py`), or the pairing regresses silently.

---

## 5. Useful Reference Files

| File | Purpose |
|---|---|
| [`CLAUDE.md`](file:///c:/proj/nic_internship/CLAUDE.md) | Full project context, architecture diagram, all gotchas |
| [`backend/schema.sql`](file:///c:/proj/nic_internship/backend/schema.sql) | Raw SQL schema — source of truth for table structure |
| [`backend/models.py`](file:///c:/proj/nic_internship/backend/models.py) | SQLAlchemy ORM models |
| [`backend/services/rag.py`](file:///c:/proj/nic_internship/backend/services/rag.py) | All intent detection + LLM call helpers |
| [`backend/services/postgres.py`](file:///c:/proj/nic_internship/backend/services/postgres.py) | All DB query functions |
| [`backend/utils/fuzzy.py`](file:///c:/proj/nic_internship/backend/utils/fuzzy.py) | Fuzzy month/token matching logic |
| [`test_questions_206.txt`](file:///c:/proj/nic_internship/test_questions_206.txt) | Largest question bank for manual testing |
| [`backend/services/chatbot/attachment_qa.py`](file:///c:/proj/nic_internship/backend/services/chatbot/attachment_qa.py) | Uploaded-document Q&A: entity extraction, enumeration, survey/subdivision pairing |
