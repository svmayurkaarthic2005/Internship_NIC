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

Follow these files in order. Changes touch exactly three service files.

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
4. Return a plain `dict` with a `"data"` key; chatbot.py unpacks it.

### Step 3 — Wire it in `chatbot.py`

File: [`backend/services/chatbot.py`](file:///c:/proj/nic_internship/backend/services/chatbot.py)

1. Find `process_chat_stream()` (the main dispatch block — large `if/elif` chain on intent).
2. Add an `elif intent == "your_intent_name":` branch.
3. Call your `postgres.py` handler, build context string, call `call_llama_stream()`.
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

### Comprehensive Suite (top-level)

```powershell
.venv\Scripts\activate
python test_comprehensive_suite.py
```

Covers basic smoke tests across all intents.

### 200-Question Suite (detailed NLP accuracy)

```powershell
python backend/test_200_suite.py
```

- Tests all intent categories including bilingual (Tamil/Tanglish/English) queries.
- Output shows pass/fail per question + a summary accuracy %.
- Target: **>90% pass rate** before merging changes.

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

```powershell
# Run exactly in this order
python create_database.py        # Creates the PostgreSQL DB
python -m uvicorn backend.main:app --reload  # Let SQLAlchemy create tables on startup (then Ctrl+C)
python backend/seed.py           # Seed all officers, applications, survey numbers
python backend/ingest.py         # Ingest documents into pgvector
```

**Prerequisite**: pgvector must be installed in PostgreSQL:
```sql
-- Run once in psql as superuser
CREATE EXTENSION IF NOT EXISTS vector;
```

### Resetting Data

```powershell
python reset_database.py   # Drops all rows, re-seeds from scratch
```

Use this when seed data is inconsistent or you've made model changes.

### Integrity Checks

Always run after seeding or schema changes:

```powershell
python verify_no_duplicates.py   # Checks unique active-app-per-survey constraint
python check_missing_values.py   # Validates no required fields are NULL
```

Both scripts print a summary and exit non-zero on failure.

---

## 4. Key Rules to Never Break

1. **Never use `datetime.utcnow()`** — use `datetime.now(timezone.utc)` (Python 3.12+ deprecation).
2. **Never hardcode DB URLs or secrets** — always read from `settings` in `backend/config.py`.
3. **Never use `session.query()`** — use async `select()` + `await session.execute()`.
4. **Never let the LLM count or enumerate application numbers** — always fetch from DB and inject into context.
5. **Never add `print()` in service/router code** — use `get_logger(__name__)` from `backend.utils.logger`.
6. **Always exclude rejected applications** in query results to avoid ghost data.

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
