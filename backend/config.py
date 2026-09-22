"""
Configuration management using pydantic-settings
"""
import json
from pathlib import Path
from typing import List
from pydantic_settings import BaseSettings
from pydantic import validator

# Absolute path to the project root (the directory containing this file's parent)
_PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _resolve_env_file() -> str:
    """Find the environment file from the project root or its parent directory."""
    candidates = [
        Path.cwd() / ".env",
        Path(__file__).resolve().parents[1] / ".env",
        Path(__file__).resolve().parents[2] / ".env",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return ".env"


class Settings(BaseSettings):
    # Database
    DATABASE_URL: str
    SYNC_DATABASE_URL: str
    
    # Security
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480
    
    # Ollama / LLM
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    LLM_MODEL: str = "llama3.1:8b"
    EMBEDDING_MODEL: str = "nomic-embed-text"
    # Cosine-distance ceiling for a knowledge_embeddings hit to count as
    # relevant. pgvector's `<=>` returns 1 - cosine_similarity, so 0 is
    # identical and larger is less similar. Measured with real nomic-embed-text
    # query vectors against the seeded 47-chunk corpus:
    #   ISD-workflow question .......... 0.31   (keep)
    #   litigation-flag question ....... 0.28   (keep)
    #   "service code 0153 fee" ........ 0.41   (keep -- terse but on-topic)
    #   "chocolate chip cookie recipe" . 0.56   (drop)
    #   "football world cup 2018" ...... 0.62   (drop)
    #   "asdf qwer zxcv" ............... 0.53   (drop)
    # 0.50 sits in the gap. Anything past it is dropped rather than handed to
    # the LLM as "RELEVANT DOCUMENT CONTEXT" -- the same "no evidence, no
    # speculation" rule the upload pipeline enforces via
    # UPLOAD_MIN_EVIDENCE_SCORE. RAG here is only the fallback after ~60
    # deterministic handlers, so losing a borderline hit costs little; feeding
    # the model corpus-adjacent noise costs a confident wrong answer. Set to
    # 2.0 to disable the floor.
    RAG_MAX_DISTANCE: float = 0.50
    # Max output tokens per LLM response. Ollama's own default (128 on many
    # builds) truncates anything longer than a short reply, so this is set
    # explicitly rather than left to the server default. 2048 leaves room for
    # a full workflow explanation or a multi-row listing without the answer
    # stopping mid-sentence.
    LLM_NUM_PREDICT: int = 512
    # Context window (input + output tokens) the model can see at once. The
    # prompt is large -- ~1.8k tokens of system rules + up to 10 history turns
    # + retrieved document chunks (n_results x 2000 chars) + the structured
    # data summary -- and at 8192 that left almost nothing for the answer, so
    # long replies were being truncated (or the tail of the prompt, i.e. the
    # question itself, was dropped by Ollama). 16384 keeps the whole prompt
    # plus full room for LLM_NUM_PREDICT output. Costs ~1 GB more RAM and a
    # little more prompt-eval time on llama3.1:8b; worth it for complete
    # answers.
    LLM_NUM_CTX: int = 16384
    # Wall-clock ceiling on a single non-streaming LLM call. A long generation
    # on CPU can otherwise outrun the client / reverse-proxy timeout and hang
    # the request with nothing to show; on timeout the caller gets a readable
    # message instead.
    LLM_TIMEOUT_SECONDS: float = 90.0
    # How long Ollama keeps the chat model in memory after the last request.
    LLM_KEEP_ALIVE: str = "30m"

    # ── Agent (LLM tool-calling) layer ───────────────────────────────────
    # The ~60 deterministic handlers stay the primary path. The agent runs
    # only where the pipeline would otherwise hand a free-text prompt to the
    # LLM with no grounding, and gives it authorized tools instead. Set
    # AGENT_ENABLED=false to fall straight back to that plain prompt.
    AGENT_ENABLED: bool = True
    # Tool-selection rounds before the loop stops and answers from whatever it
    # has. Each round is one llama3.1:8b call, so this bounds worst-case
    # latency; 3 is enough for a two-part question plus a correction.
    AGENT_MAX_ITERATIONS: int = 3
    # Output cap for tool rounds after the first. A further tool call is ~30
    # tokens; anything longer is a draft answer that the separate answer pass
    # replaces, and at ~4 tokens/s (the 8B model is mostly on CPU) that draft
    # cost 13-47 s per question.
    AGENT_FOLLOWUP_MAX_TOKENS: int = 64
    # Seconds for the whole tool-selection loop. On timeout the caller falls
    # back to the existing non-agent prompt rather than leaving the officer
    # waiting.
    AGENT_TIMEOUT_SECONDS: float = 60.0

    # Chat file uploads. A big document floods the model's context and
    # llama3.1:8b starts to hallucinate, so a PDF or Word file over this many
    # pages is rejected — the officer is asked to upload only the relevant
    # pages. PDF pages are counted exactly; Word pages are estimated from the
    # extracted text length (Word has no reliable page count without rendering).
    UPLOAD_MAX_DOC_PAGES: int = 15
    # Chars-per-page used to estimate a Word document's page count.
    UPLOAD_EST_CHARS_PER_PAGE: int = 1800
    # Hard ceiling on the characters kept from any one upload, whatever its type.
    UPLOAD_MAX_DOC_CHARS: int = 20000
    # Largest upload accepted, in bytes. Checked against the bytes actually
    # read, never against a client-declared Content-Length.
    UPLOAD_MAX_FILE_BYTES: int = 5 * 1024 * 1024
    # Wall-clock budget for turning one uploaded file into text. Extraction
    # runs in a worker thread; a file that outruns this is rejected rather
    # than left holding a request open (a crafted PDF can loop pypdf).
    UPLOAD_EXTRACTION_TIMEOUT_SECONDS: float = 30.0
    # CSV bounds. Rows beyond the limit are dropped (and the officer told);
    # a file wider than the column limit is rejected, since a 5000-column
    # sheet is not an evidence document.
    UPLOAD_MAX_CSV_ROWS: int = 20000
    UPLOAD_MAX_CSV_COLUMNS: int = 200
    # Evidence chunking. Chunks are the unit of retrieval and of citation, so
    # they are small enough that a citation points somewhere specific.
    UPLOAD_CHUNK_CHARS: int = 1200
    UPLOAD_CHUNK_OVERLAP: int = 150
    # Chunks handed to the answer prompt for one question.
    UPLOAD_RETRIEVAL_TOP_K: int = 6
    # Share of the question's content words a chunk must carry to count as
    # evidence. Below this the answer is the grounded refusal and the LLM is
    # not called at all. 0.4 is the floor that makes "what is the encumbrance
    # certificate number" refuse against an order that merely contains the
    # word "number" -- a lower floor turned one incidental word into grounds
    # for an answer.
    UPLOAD_MIN_EVIDENCE_SCORE: float = 0.4
    # Active attachments per chat session; the oldest is retired past this.
    UPLOAD_MAX_DOCS_PER_SESSION: int = 5
    # How long an attachment stays answerable. Cleanup is explicit (a sweep at
    # startup and on upload), so a restart never silently loses a live
    # attachment -- it is in PostgreSQL, not in process memory.
    UPLOAD_RETENTION_HOURS: int = 72

    # A follow-up ("the 2nd one", "how many of them") only refers to what was shown
    # this recently. Older than this, the list is gone from the officer's mind and
    # the question is treated as having nothing to refer to.
    FOLLOWUP_CONTEXT_TTL_MINUTES: int = 120
    # Embed attachment chunks so retrieval can use pgvector as well as the
    # lexical score. Retrieval degrades to lexical-only when Ollama is down,
    # which is why upload never fails on an embedding error.
    UPLOAD_EMBEDDINGS_ENABLED: bool = True
    # Keep the original bytes on disk under a server-generated name. Never a
    # path derived from the client filename, and never inside a served
    # directory.
    UPLOAD_STORE_RAW_FILES: bool = True
    UPLOAD_STORAGE_DIR: str = str(_PROJECT_ROOT / "var" / "attachments")


    # Environment
    ENVIRONMENT: str = "development"
    
    # CORS
    CORS_ORIGINS: str | List[str] = '["http://localhost:3000","http://127.0.0.1:3000","http://127.0.0.1:5500","http://localhost:5500","http://localhost:8080","http://127.0.0.1:8080","http://localhost:5173","http://127.0.0.1:5173"]'
    
    @validator("CORS_ORIGINS", pre=True)
    def parse_cors_origins(cls, v):
        if isinstance(v, str):
            return json.loads(v)
        return v
    
    class Config:
        env_file = _resolve_env_file()
        env_file_encoding = "utf-8"
        case_sensitive = True


# Global settings instance
settings = Settings()

# Tamil Nadu district codes, as the DEPARTMENT's own master carries them.
#
# The authority is `district_unicode`, loaded verbatim from the TAMILNILAM
# `district.sql` dump -- CLAUDE.md is explicit that it IS the app's district
# table. This map is only a fallback for the paths that resolve a district code
# without touching the database (`_district_name_from_app_number`), so it has to
# agree with the master or the same code renders two different districts
# depending on which path ran.
#
# Codes 34 / 35 / 37 were rotated against the master and are corrected here:
# the dump reads 34 = Tenkasi, 35 = Chengalpattu, 37 = Ranipet. Getting these
# wrong does not produce an empty field, it produces a land record labelled with
# somebody else's district, which is why they are spelled out rather than left
# to a "close enough" reference list.
#
# The master supplies the IDENTITY of a code; the spelling here is the app's
# own, because the master's is raw departmental data entry and inconsistent with
# itself ("VILUPPURAM", "The Nilgiris", "Mailaduthurai"). So this table and the
# master may differ in transliteration, and must never differ in which district
# a code means -- `test_district_codes.py` enforces exactly that distinction.
DISTRICT_CODE_MAP = {
    "01": "Tiruvallur",
    "02": "Chennai",
    "03": "Kancheepuram",
    "04": "Vellore",
    "05": "Dharmapuri",
    "06": "Tiruvannamalai",
    "07": "Viluppuram",
    "08": "Salem",
    "09": "Namakkal",
    "10": "Erode",
    "11": "Nilgiris",
    "12": "Coimbatore",
    "13": "Dindigul",
    "14": "Karur",
    "15": "Tiruchirappalli",
    "16": "Perambalur",
    "17": "Ariyalur",
    "18": "Cuddalore",
    "19": "Nagapattinam",
    "20": "Tiruvarur",
    "21": "Thanjavur",
    "22": "Pudukkottai",
    "23": "Sivagangai",
    "24": "Madurai",
    "25": "Theni",
    "26": "Virudhunagar",
    "27": "Ramanathapuram",
    "28": "Thoothukudi",
    "29": "Tirunelveli",
    "30": "Kanniyakumari",
    "31": "Krishnagiri",
    "32": "Tiruppur",
    "33": "Kallakurichi",
    "34": "Tenkasi",
    "35": "Chengalpattu",
    "36": "Tirupathur",
    "37": "Ranipet",
    "38": "Mayiladuthurai"
}

DISTRICT_NAME_MAP = {name.lower(): code for code, name in DISTRICT_CODE_MAP.items()}

# Official Tamil-script name for each district code, standard government
# spelling. Kept separate from DISTRICT_CODE_MAP (English) because a district
# name typed in Tamil script never matched it -- "கோயம்புத்தூர் மாவட்ட குறியீடு
# என்ன?" ("what is Coimbatore's district code?") found no entry, fell all the
# way through to the LLM tool-calling agent, and could take 30+ seconds (once
# long enough to blow through a whole test suite's timeout budget) for a fact
# that is a plain table lookup.
DISTRICT_CODE_TO_TAMIL_NAME = {
    "01": "திருவள்ளூர்",
    "02": "சென்னை",
    "03": "காஞ்சிபுரம்",
    "04": "வேலூர்",
    "05": "தர்மபுரி",
    "06": "திருவண்ணாமலை",
    "07": "விழுப்புரம்",
    "08": "சேலம்",
    "09": "நாமக்கல்",
    "10": "ஈரோடு",
    "11": "நீலகிரி",
    "12": "கோயம்புத்தூர்",
    "13": "திண்டுக்கல்",
    "14": "கரூர்",
    "15": "திருச்சிராப்பள்ளி",
    "16": "பெரம்பலூர்",
    "17": "அரியலூர்",
    "18": "கடலூர்",
    "19": "நாகப்பட்டினம்",
    "20": "திருவாரூர்",
    "21": "தஞ்சாவூர்",
    "22": "புதுக்கோட்டை",
    "23": "சிவகங்கை",
    "24": "மதுரை",
    "25": "தேனி",
    "26": "விருதுநகர்",
    "27": "இராமநாதபுரம்",
    "28": "தூத்துக்குடி",
    "29": "திருநெல்வேலி",
    "30": "கன்னியாகுமரி",
    "31": "கிருஷ்ணகிரி",
    "32": "திருப்பூர்",
    "33": "கள்ளக்குறிச்சி",
    # Rotated against the master in the same way the English map was; the Tamil
    # spellings come from the dump's own Tamil column.
    "34": "தென்காசி",
    "35": "செங்கல்பட்டு",
    "36": "திருப்பத்தூர்",
    "37": "இராணிப்பேட்டை",
    "38": "மயிலாடுதுறை",
}
DISTRICT_TAMIL_NAME_MAP = {name: code for code, name in DISTRICT_CODE_TO_TAMIL_NAME.items()}
