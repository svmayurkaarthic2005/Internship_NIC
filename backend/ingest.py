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
import uuid

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
    }
}


def load_document(file_path: Path) -> str:
    """
    Load document content from file
    """
    try:
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


def chunk_document(content: str, document_name: str) -> list:
    """
    Split document into chunks using RecursiveCharacterTextSplitter
    """
    try:
        # Initialize text splitter
        # 500 tokens ≈ 2000 characters (rough estimate)
        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=2000,
            chunk_overlap=200,
            length_function=len,
            separators=["\n\n", "\n", ". ", " ", ""]
        )
        
        # Split text
        chunks = text_splitter.split_text(content)
        
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
            chunk_id = f"{doc_name}_{i}_{uuid.uuid4().hex[:8]}"
            
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
