"""
Document ingestion script for pgvector (PostgreSQL)
Run this script to load knowledge documents into the vector store.
"""
import sys
# Force UTF-8 output on Windows to prevent UnicodeEncodeError on emoji/symbols
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
from pathlib import Path
from langchain_text_splitters import RecursiveCharacterTextSplitter
import re
import csv
import unicodedata

try:
    from pypdf import PdfReader
except ImportError:  # PDF ingestion is optional; .txt/.csv still work without it
    PdfReader = None

try:
    import docx as _docx  # python-docx
except ImportError:
    _docx = None

# Every extension the loader understands. A file dropped into backend/documents/
# with one of these suffixes is picked up automatically -- no config entry needed.
SUPPORTED_SUFFIXES = {".txt", ".md", ".csv", ".pdf", ".docx"}

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.services.pgvector_store import init_pgvector, add_documents, get_collection_stats
from backend.utils.logger import get_logger

logger = get_logger(__name__)

# Document metadata configuration
DOCUMENT_CONFIG = {
    "workflow_guide.txt": {
        "category": "workflow",
        "language": "english",
        "source": "official_manual"
    },
    "survey_manual.txt": {
        "category": "survey_rules",
        "language": "english",
        "source": "official_manual"
    },
    "faq_english.txt": {
        "category": "faq",
        "language": "english",
        "source": "knowledge_base"
    },
    "faq_tamil.txt": {
        "category": "faq",
        "language": "tamil",
        "source": "knowledge_base"
    },
    "land_rules.txt": {
        "category": "regulations",
        "language": "english",
        "source": "official_manual"
    },
    "field_inspection_report_sample.txt": {
        "category": "field_report",
        "language": "english",
        "source": "sis_upload"
    },
    "district_codes.txt": {
        "category": "reference",
        "language": "english",
        "source": "official_manual"
    },
    "tamilnilam_urban_services_and_districts.txt": {
        "category": "reference",
        "language": "bilingual",
        "source": "tamilnilam_official_portal"
    },
    "sis_upload_checklist.txt": {
        "category": "upload_guidance",
        "language": "bilingual",
        "source": "sis_upload"
    },
    "sample_boundary_observations.csv": {
        "category": "field_report",
        "language": "english",
        "source": "sis_upload"
    },
    "database_structure_reference.txt": {
        "category": "database_reference",
        "language": "english",
        "source": "system_documentation"
    },
    "sample_sis_site_note.pdf": {
        "category": "field_report",
        "language": "english",
        "source": "sis_upload"
    }
}


# Tamil pre-base vowel signs (ெ ே ை and the two-part ொ ோ ௌ). In a PDF these
# are drawn to the LEFT of their consonant but stored logically AFTER it; a
# layout engine that emits glyphs in visual order (Chrome's print-to-PDF, many
# report generators) makes pypdf read "வே" back as "ேவ" — not a real word, and
# it wrecks both the embedding and the LLM answer. Swap each pre-base sign back
# behind its consonant cluster, but ONLY when it is not already preceded by a
# consonant (valid Tamil always has consonant-then-sign, so that case is left
# untouched).
_PREBASE = "ெேைொோௌ"   # left-side vowel signs ெ ே ை ொ ோ ௌ
_CONS = "க-ஹ"                               # base consonants க .. ஹ
_TAMIL_SWAP_RE = re.compile(f"([{_PREBASE}])((?:[{_CONS}]்)*[{_CONS}])")
# A pre-base sign not sitting right after a consonant (or after a virama) is
# misplaced -- the fingerprint of visual-order glyph extraction.
_TAMIL_MISPLACED_RE = re.compile(f"(?<![{_CONS}])[{_PREBASE}]")
_TAMIL_PREBASE_RE = re.compile(f"[{_PREBASE}]")


def fix_tamil_reordering(text: str) -> str:
    """Repair visual-order Tamil pre-base vowels from PDF text extraction.

    In valid Tamil every left-side vowel sign (ெ ே ை ொ ோ ௌ) sits immediately
    after its consonant. A layout engine that lays glyphs left-to-right (Chrome
    print-to-PDF and many report generators) makes pypdf read "வே" back as "ேவ".
    Only runs when a meaningful share of pre-base signs are misplaced, then does
    one left-to-right pass swapping each sign past the consonant cluster that
    follows it; NFC then recomposes any split two-part sign (ே + ா -> ோ).
    """
    if not text or not _TAMIL_PREBASE_RE.search(text):
        return text
    total = len(_TAMIL_PREBASE_RE.findall(text))
    misplaced = len(_TAMIL_MISPLACED_RE.findall(text))
    if total == 0 or misplaced / total < 0.12:
        return text
    fixed = _TAMIL_SWAP_RE.sub(lambda m: m.group(2) + m.group(1), text)
    return unicodedata.normalize("NFC", fixed)


def load_document(file_path: Path) -> str:
    """
    Load document content from file
    """
    try:
        if file_path.suffix.lower() == ".pdf":
            if PdfReader is None:
                logger.error(f"Skipping {file_path.name}: pypdf not installed "
                             f"(pip install pypdf) — PDF ingestion unavailable")
                return ""
            reader = PdfReader(str(file_path))
            content = "\n".join(page.extract_text() or "" for page in reader.pages)
            content = fix_tamil_reordering(content)
            logger.info(f"Loaded PDF document: {file_path.name} ({len(content)} chars)")
            return content

        if file_path.suffix.lower() == ".docx":
            if _docx is None:
                logger.error(f"Skipping {file_path.name}: python-docx not installed "
                             f"(pip install python-docx) — .docx ingestion unavailable")
                return ""
            d = _docx.Document(str(file_path))
            parts = [p.text for p in d.paragraphs if p.text.strip()]
            for tbl in d.tables:
                for row in tbl.rows:
                    cells = [c.text.strip() for c in row.cells]
                    if any(cells):
                        parts.append(" | ".join(cells))
            content = "\n".join(parts)
            logger.info(f"Loaded DOCX document: {file_path.name} ({len(content)} chars)")
            return content

        if file_path.suffix.lower() == ".csv":
            with open(file_path, "r", encoding="utf-8-sig", newline="") as f:
                rows = list(csv.DictReader(f))
            # Preserve the headers in every row so an embedding has meaning
            # even when retrieved independently of the rest of the CSV.
            content = "\n".join(
                " | ".join(f"{column}: {value}" for column, value in row.items())
                for row in rows
            )
            logger.info(f"Loaded CSV document: {file_path.name} ({len(content)} chars)")
            return content

        # Try UTF-8 first
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
        except UnicodeDecodeError:
            # Try UTF-16 or other encodings for Tamil files
            with open(file_path, 'r', encoding='utf-16') as f:
                content = f.read()
        
        logger.info(f"Loaded document: {file_path.name} ({len(content)} chars)")
        return content
    except Exception as e:
        logger.error(f"Error loading {file_path}: {e}")
        return ""



def detect_language(content: str) -> str:
    """Rough language tag for auto-discovered files: english / tamil / bilingual.

    Only "bilingual" when both scripts are a real presence (each >= 25% of the
    letters); a mostly-Tamil file with an English footnote is "tamil". The RAG
    retrieval filter accepts a "bilingual" doc for both en and ta queries, so an
    over-eager "bilingual" tag is safe but a wrong "english"/"tamil" one hides
    the file from the other language.
    """
    sample = content[:8000]
    tamil = sum(1 for ch in sample if "஀" <= ch <= "௿")
    latin = sum(1 for ch in sample if ch.isascii() and ch.isalpha())
    if tamil + latin < 20:
        return "english"
    # Both scripts a real presence (each >= 80 letters or >= 12% share) -> the
    # doc is useful to both en and ta queries. The RAG filter treats 'bilingual'
    # as matching either language, so leaning this way only ever helps recall.
    minor = min(tamil, latin)
    if minor >= 80 or minor / (tamil + latin) >= 0.12:
        return "bilingual"
    return "tamil" if tamil > latin else "english"


def discover_documents(documents_dir: Path, extra_paths=None) -> dict:
    """Every ingestable file, keyed by the name used for its chunk ids.

    Starts from DOCUMENT_CONFIG (explicit metadata), then adds any supported file
    found under documents/ (recursively) or passed on the command line, with
    auto-detected metadata. Explicit config always wins.
    """
    found = {}
    for name, meta in DOCUMENT_CONFIG.items():
        p = documents_dir / name
        if p.exists():
            found[name] = (p, dict(meta))

    def _add(path: Path, base: Path):
        if path.suffix.lower() not in SUPPORTED_SUFFIXES:
            return
        key = path.relative_to(base).as_posix() if base in path.parents or base == path.parent else path.name
        if key in found:
            return
        content_head = load_document(path)[:4000] if path.stat().st_size else ""
        found[key] = (path, {
            "category": "user_upload",
            "language": detect_language(content_head),
            "source": "sis_upload",
        })

    if documents_dir.exists():
        for path in sorted(documents_dir.rglob("*")):
            if path.is_file():
                _add(path, documents_dir)

    for raw in (extra_paths or []):
        p = Path(raw).expanduser()
        if p.is_dir():
            for path in sorted(p.rglob("*")):
                if path.is_file():
                    _add(path, p)
        elif p.is_file():
            _add(p, p.parent)
        else:
            print(f"  ⚠ path not found: {raw}")
    return found


_HEADING_RE = re.compile(r'^=== .+? ===$', re.MULTILINE)


def _prefix_section_headings(content: str, chunks: list) -> list:
    """Ensure every chunk names the section it came from.

    Chunks are located in the original text so the heading in force at that
    offset can be prepended. A chunk that already begins with its heading is
    left alone.
    """
    headings = [(m.start(), m.group(0)) for m in _HEADING_RE.finditer(content)]
    if not headings:
        return chunks

    out = []
    cursor = 0
    for chunk in chunks:
        pos = content.find(chunk[:120], cursor)
        if pos == -1:
            pos = cursor
        cursor = max(cursor, pos + 1)
        if chunk.lstrip().startswith("==="):
            out.append(chunk)
            continue
        owning = None
        for start, text in headings:
            if start <= pos:
                owning = text
            else:
                break
        out.append(f"{owning}\n{chunk}" if owning else chunk)
    return out

def chunk_document(content: str, document_name: str) -> list:
    """
    Split document into chunks using RecursiveCharacterTextSplitter
    """
    try:
        # Initialize text splitter
        # 500 tokens ≈ 2000 characters (rough estimate)
        # Split on the "=== SECTION ===" headings first so each chunk keeps the
        # heading that gives it meaning. Without this the splitter could strand
        # a line like "Total Timeline: Approximately 15-20 working days" at the
        # top of a chunk with no indication that it belongs to NISD, and the
        # model would quote it for ISD.
        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=2000,
            chunk_overlap=200,
            length_function=len,
            keep_separator=True,
            separators=["\n=== ", "\n\n", "\n", ". ", " ", ""]
        )
        
        # Split text
        chunks = text_splitter.split_text(content)

        # A section longer than chunk_size still gets split, and the tail piece
        # then carries no heading -- which is how "Total Timeline: Approximately
        # 30-35 working days" ended up detached from "=== ISD WORKFLOW ===".
        # Re-attach the owning heading to any chunk that does not start with one.
        chunks = _prefix_section_headings(content, chunks)

        logger.info(f"Split {document_name} into {len(chunks)} chunks")
        return chunks
        
    except Exception as e:
        logger.error(f"Error chunking {document_name}: {e}")
        return []


def ingest_documents(extra_paths=None):
    """
    Main ingestion function.

    extra_paths: optional list of file/dir paths (from the command line) to
    ingest in addition to everything under backend/documents/.
    """
    print("=" * 60)
    print("SIS CHATBOT - DOCUMENT INGESTION")
    print("=" * 60)
    
    # Initialize pgvector store
    print("\n[1/4] Initializing pgvector store...")
    try:
        init_pgvector()
        print(f"✓ pgvector store initialized")
        
        # Get initial stats
        initial_stats = get_collection_stats()
        print(f"  Current document count: {initial_stats['document_count']}")
    except Exception as e:
        print(f"✗ Error initializing pgvector store: {e}")
        return
    
    # Load documents
    print("\n[2/4] Loading documents...")
    documents_dir = Path(__file__).parent / "documents"
    
    if not documents_dir.exists():
        print(f"✗ Documents directory not found: {documents_dir}")
        return
    
    all_chunks = []
    total_docs = 0

    # Explicit config + anything else found under documents/ (or passed on the
    # command line). A plain .txt/.csv/.pdf/.docx drop-in just works.
    catalogue = discover_documents(documents_dir, extra_paths)
    print(f"  Discovered {len(catalogue)} ingestable file(s)")

    for doc_name, (doc_path, metadata) in catalogue.items():
        # Load document
        content = load_document(doc_path)
        if not content:
            continue
        
        # Chunk document
        chunks = chunk_document(content, doc_name)
        if not chunks:
            continue
        
        # Prepare chunks with metadata
        for i, chunk in enumerate(chunks):
            # Stable across runs: the store upserts ON CONFLICT (chunk_id), so a
            # random suffix here made every re-ingest insert a second copy of
            # each chunk instead of replacing it.
            chunk_id = f"{doc_name}_{i}"
            
            chunk_metadata = {
                "document_name": doc_name,
                "section": f"chunk_{i}",
                "category": metadata["category"],
                "source": metadata["source"],
                "language": metadata["language"],
                "page_number": i + 1,
                "total_chunks": len(chunks)
            }
            
            all_chunks.append({
                "id": chunk_id,
                "content": chunk,
                "metadata": chunk_metadata
            })
        
        total_docs += 1
        print(f"  ✓ Loaded {doc_name}: {len(chunks)} chunks")
    
    print(f"\n  Total documents loaded: {total_docs}")
    print(f"  Total chunks prepared: {len(all_chunks)}")
    
    # Ingest into pgvector
    print("\n[3/4] Ingesting into pgvector...")
    try:
        add_documents(all_chunks)
        print(f"✓ Successfully ingested {len(all_chunks)} chunks")
    except Exception as e:
        print(f"✗ Error ingesting documents: {e}")
        return
    
    # Verify ingestion
    print("\n[4/4] Verifying ingestion...")
    try:
        final_stats = get_collection_stats()
        print(f"✓ Verification complete")
        print(f"  Final document count: {final_stats['document_count']}")
        print(f"  Status: {final_stats['status']}")
        
        if final_stats['document_count'] > initial_stats['document_count']:
            docs_added = final_stats['document_count'] - initial_stats['document_count']
            print(f"  New documents added: {docs_added}")
    except Exception as e:
        print(f"✗ Error verifying ingestion: {e}")
        return
    
    print("\n" + "=" * 60)
    print("INGESTION COMPLETE!")
    print("=" * 60)
    print("\nYou can now start the SIS Chatbot API:")
    print("  uvicorn backend.main:app --reload")
    print()


if __name__ == "__main__":
    try:
        # Any extra file/dir paths after the script name are ingested too:
        #   python -m backend.ingest ./my_report.pdf ~/case_notes/
        ingest_documents(extra_paths=sys.argv[1:])
    except KeyboardInterrupt:
        print("\n\nIngestion interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n✗ Fatal error: {e}")
        sys.exit(1)
