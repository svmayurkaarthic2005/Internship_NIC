"""
Document ingestion script for pgvector (PostgreSQL)
Run this script to load knowledge documents into the vector store.
"""
import os
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
import uuid
import csv

try:
    from pypdf import PdfReader
except ImportError:  # PDF ingestion is optional; .txt/.csv still work without it
    PdfReader = None

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
            logger.info(f"Loaded PDF document: {file_path.name} ({len(content)} chars)")
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


def ingest_documents():
    """
    Main ingestion function
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
    
    for doc_name, metadata in DOCUMENT_CONFIG.items():
        doc_path = documents_dir / doc_name
        
        if not doc_path.exists():
            print(f"  ⚠ Warning: {doc_name} not found, skipping...")
            continue
        
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
        ingest_documents()
    except KeyboardInterrupt:
        print("\n\nIngestion interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n✗ Fatal error: {e}")
        sys.exit(1)
