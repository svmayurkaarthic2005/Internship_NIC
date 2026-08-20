# Sub Inspector Surveyor AI Assistant

A bilingual (Tamil/English) AI-powered chatbot system for Sub Inspectors to manage survey applications through natural language conversations.

## Tech Stack

- **Backend**: FastAPI, PostgreSQL, Ollama (Llama 3.1:8b)
- **Frontend**: HTML/CSS/JavaScript (Vanilla)
- **Vector Store**: pgvector (PostgreSQL)
- **Authentication**: JWT

## Quick Start

### Prerequisites
- Python 3.8+
- PostgreSQL 12+
- Ollama with models: `llama3.1:8b`, `nomic-embed-text`

### Setup
```bash
# 1. Clone and setup environment
git clone <repo>
cd nic_internship
python -m venv .venv
.venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure
copy .env.example .env
# Edit .env with your database credentials

# 4. Setup database
python create_database.py
python backend/seed.py
python backend/ingest.py

# 5. Start backend
python -m uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000

# 6. Start frontend (new terminal)
cd frontend
python -m http.server 3000
```

**Access**: `http://localhost:3000/login.html`

**Test Credentials**:
- arjun.kumar@sis.tn.gov.in / Test@1234 (Block SIS)
- priya.devi@sis.tn.gov.in / Test@1234 (Ward SIS)
- ramesh.babu@sis.tn.gov.in / Test@1234 (Taluk SIS)
- lakshmi.narayanan@sis.tn.gov.in / Test@1234 (District SIS)

## Project Structure

```
nic_internship/
├── backend/
│   ├── main.py                      # FastAPI entry point
│   ├── config.py                    # Configuration
│   ├── database.py                  # Database connection
│   ├── models.py                    # SQLAlchemy models
│   ├── schemas.py                   # Pydantic schemas
│   ├── routers/
│   │   ├── auth.py                  # Login/register endpoints
│   │   ├── chat.py                  # Chat & streaming endpoints
│   │   ├── applications.py          # Application CRUD
│   │   ├── survey.py                # Survey endpoints
│   │   └── speech.py                # TTS/STT endpoints
│   ├── services/
│   │   ├── chatbot.py               # Main chatbot orchestration
│   │   ├── rag.py                   # Intent detection & routing
│   │   ├── postgres.py              # Database query handlers
│   │   ├── pgvector_store.py        # Vector store operations (pgvector)
│   │   ├── embeddings.py            # Embedding generation
│   │   └── speech_service.py        # Speech processing
│   └── documents/                   # Knowledge base documents
├── frontend/
│   ├── login.html                   # Login page
│   ├── chatbot.html                 # Chat interface
│   ├── css/                         # Stylesheets
│   └── js/                          # JavaScript modules
├── verify_no_duplicates.py          # Quick integrity check
├── check_missing_values.py          # Deep data validation
└── requirements.txt
```

## How It Works

### 1. Request Flow

```
User Message → FastAPI (/chat/stream)
           ↓
    services/chatbot.py (orchestrator)
           ↓
    rag.py (intent detection)
           ↓
    postgres.py (database queries)
           ↓
    LLM (Ollama) + Context
           ↓
    Streaming Response → User
```

### 2. Intent Detection (rag.py)

The system detects user intent and routes to appropriate handlers:

```python
Priority Order:
1. greeting              # "Hello", "வணக்கம்"
2. farewell              # "Bye", "நன்றி"
3. joint_owner_check     # "Who are the joint owners?"
4. application_status    # "Status of APP-2024-000001"
5. check_documents       # "What documents are missing?"
6. check_sale_deed       # "Is sale deed registered?"
7. is_nisd_or_isd        # "What type is this application?"
8. field_specific_query  # "What is the applicant name?"
9. general_query         # Falls back to RAG search
```

**Language Detection**:
- Tamil script (Unicode range detection)
- Tanglish (phonetic patterns)
- English (default)

### 3. Database Architecture

**Key Tables**:
- `applications` - Application records with status/stage tracking
- `survey_numbers` - Survey number registry with geographic links
- `field_visits` - Field visit scheduling and status
- `application_sub_divisions` - MERGE application subdivisions
- `sis_officers` - Officer accounts
- `officer_jurisdictions` - Officer geographic assignments

**Important Constraint**:
```sql
-- One active application per survey number
CREATE UNIQUE INDEX idx_unique_active_app_per_survey 
ON applications (survey_number_id) 
WHERE current_status IN ('pending', 'in_progress', 'escalated');
```

### 4. Query Handling (postgres.py)

Each intent has a dedicated query handler:

- `get_officer_applications()` - Get apps by officer + jurisdiction + stage
- `get_field_visits()` - Field visits **excluding rejected apps**
- `get_pending_applications()` - Pending/in-progress apps only
- `get_application_detail()` - Full application details
- `get_survey_detail()` - Survey number information

**Key Filter**: All queries exclude rejected applications to prevent duplicates

### 5. Chatbot Logic (services/chatbot.py)

**Response Accuracy**:
- Direct database responses for count queries
- Strict pattern matching for application numbers
- Structured data validation before response generation
- No LLM interpretation for numeric data

**Context Management**:
- Extracts application numbers from user messages
- Maintains conversation history for implicit references
- Validates references against chat context

**Response Building**:
- Language detection (Tamil/English/Tanglish)
- Database-backed numeric responses
- Proper Tamil/English formatting
- Streaming for better UX

**Query Recognition**:
```python
# Abbreviated forms supported:
"show app" → pending_applications
"show appl" → pending_applications  
"show applications" → pending_applications

# Count queries use database directly:
"how many apps" → exact count
"number of applications" → exact count
```

**Application Number Extraction**:
```python
# Explicit: "APP-2024-000001" → Found
# Reference: "this application" → Checks last 2 messages
# Field query: "what is the name?" → Checks context
# No reference: → Asks user for application number
```

### 6. Vector Store (pgvector)

Documents ingested:
- `faq_english.txt` - English FAQs
- `faq_tamil.txt` - Tamil FAQs  
- `survey_manual.txt` - Survey procedures
- `workflow_guide.txt` - Workflow and business rules

Used for:
- Intent detection (semantic similarity)
- General queries fallback
- Document retrieval for RAG

## Core Features

### Bilingual Support
- Full Tamil and English support
- Tanglish (Tamil written in English) recognition
- Language-specific response formatting
- Spelling error tolerance

### Application Queries
- Status tracking by application number or survey number
- Document verification and missing document identification
- Sale deed registration status
- Application type identification (ISD/NISD/MERGE)
- Field-specific queries (name, mobile, email, etc.)
- Joint owner identification

### Smart Features
- **Context Continuity**: Remembers previous application references
- **Implicit Continuation**: "What's the name?" uses last mentioned app
- **Month-based Filtering**: "Show January applications" with fuzzy matching
- **Overdue Warnings**: Visual indicators for overdue field visits
- **Table Rendering**: Clean tables for structured data

### Data Integrity
- Unique constraint prevents duplicate active applications
- Rejected applications excluded from active queries
- Field visits for rejected apps marked as 'cancelled'
- Comprehensive validation scripts

## Database Verification

```bash
# Quick check for duplicates and integrity
python verify_no_duplicates.py

# Deep validation of all fields
python check_missing_values.py
```

## Configuration (.env)

```env
# Database
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/sis_chatbot
SYNC_DATABASE_URL=postgresql://user:pass@localhost:5432/sis_chatbot

# Ollama
OLLAMA_BASE_URL=http://localhost:11434
LLM_MODEL=llama3.1:8b
EMBEDDING_MODEL=nomic-embed-text

# Security
SECRET_KEY=your_secret_key
ACCESS_TOKEN_EXPIRE_MINUTES=480
```

## API Endpoints

**Authentication**
- `POST /auth/login` - User login

**Chat**
- `POST /chat/stream` - Streaming chat (SSE)
- `GET /chat/history` - Get chat history

**Applications**
- `GET /applications` - List applications
- `GET /applications/{id}` - Get details
- `PUT /applications/{id}` - Update application

## Troubleshooting

**Application List Queries**
```bash
# All forms are supported:
"show applications"
"show app"
"show appl"
"list applications"
```

**Count Queries**
```bash
# Returns exact database count:
"how many applications"
"number of applications in july"
"total applications"

# Result: Precise count from database (e.g., "15 applications")
```

**Ollama Connection**
```bash
# Verify Ollama is running
curl http://localhost:11434/api/tags

# Check installed models
ollama list

# Install missing models
ollama pull llama3.1:8b
ollama pull nomic-embed-text
```

**Database Connection**
```bash
# Test connectivity
python -c "from backend.database import engine; print('Connected')"

# Reset data if needed
python backend/seed.py
```

**Data Integrity**
```bash
# Verify database integrity
python verify_no_duplicates.py
python check_missing_values.py
```

## Recent Updates

### Version 1.4 (Current)
**Query Accuracy Improvements:**
- Direct database count responses for numeric queries
- Support for abbreviated forms: "app", "appl", "apps"
- Enhanced intent detection for application list queries
- Structured data responses prevent hallucination

**Previous Updates:**
- Duplicate prevention with unique survey number constraint
- Rejected application filtering in all queries
- Month-based filtering with fuzzy matching
- Overdue warning indicators
- Data integrity verification scripts

## License

Developed for National Informatics Centre (NIC) internship.
