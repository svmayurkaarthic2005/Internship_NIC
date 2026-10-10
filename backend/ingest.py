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
import io as _io
import os
import re
import csv
import unicodedata

try:
    import fitz  # PyMuPDF — PDF text, native tables, and page images for OCR
except ImportError:  # PDF ingestion is optional; .txt/.csv still work without it
    fitz = None

# Neither OCR engine's package is imported here. Only the one actually
# selected by OCR_ENGINE is imported, and only on first real use inside
# `_ocr_image` / `_get_easyocr_reader` below — EasyOCR in particular pulls in
# PyTorch (~2GB on disk); a deployment that never OCRs a page, or that only
# ever runs Tesseract, should never pay that import cost.

try:
    import docx as _docx  # python-docx
except ImportError:
    _docx = None

# A page with fewer extractable characters than this is treated as a scan and
# sent to OCR instead — most text PDFs clear this easily, so only genuinely
# image-only pages pay the OCR cost.
OCR_MIN_CHARS_PER_PAGE = 20

# ── OCR engine selection ────────────────────────────────────────────────────
# Both engines are open-source and non-Chinese-origin (PaddleOCR, Chinese/
# Baidu, ruled out on origin alone; Surya, Datalab/USA, ruled out separately
# for not supporting Tamil at all). An external Tamil benchmark (Gupta et al.
# 2025, arXiv:2507.18264) suggested EasyOCR should out-accuracy Tesseract on
# Tamil -- but that was never verified against this project's own documents,
# so it was NOT trusted on its own. benchmark_ocr_engines.py in the repo root
# ran both through this exact `_ocr_image()` function on five representative
# SIS-style fixtures (clean Tamil, clean English, mixed Tamil+English, a
# table, a degraded scan). Result, reproduced twice:
#
#   engine      | maintainer          | license    | Tamil on THIS install
#   ------------|---------------------|------------|----------------------------
#   EasyOCR     | Jaided AI, Thailand | Apache-2.0 | FAILS on every fixture --
#   (1.7.2)     |                     |            | easyocr.Reader(['ta', ...])
#               |                     |            | raises RuntimeError: size
#               |                     |            | mismatch loading its own
#               |                     |            | shipped Tamil checkpoint
#               |                     |            | (143 vs 127 output classes).
#               |                     |            | Confirmed a known, still-
#               |                     |            | open upstream bug -- GitHub
#               |                     |            | JaidedAI/EasyOCR #1135 (Sep
#               |                     |            | 2023) and #1315 (Oct 2024),
#               |                     |            | fix PR #1333 still unmerged.
#               |                     |            | Reproduced with Tamil alone
#               |                     |            | too, so it is the shipped
#               |                     |            | model file, not a language-
#               |                     |            | combination issue.
#   Tesseract   | Google (US), OSS    | Apache-2.0 | WORKS. avg CER 0.135 / WER
#               |                     |            | 0.341 across the 5 fixtures
#               |                     |            | (clean English: exact match,
#               |                     |            | CER 0.016, WER 0.0). Main
#               |                     |            | error mode is vowel-sign
#               |                     |            | placement/dropping on Tamil
#               |                     |            | consonant clusters, and an
#               |                     |            | English acronym (ISD) inside
#               |                     |            | a Tamil line occasionally
#               |                     |            | misreads as noise -- usable,
#               |                     |            | not perfect. ~0.5s/page,
#               |                     |            | ~380MB RSS, zero failures.
#
# Tesseract is the current production default because it is functional and
# reliable in our tested environment -- not a claim that it is permanently the
# better engine. EasyOCR should be re-benchmarked with benchmark_ocr_engines.py
# if its Tamil checkpoint issue is fixed upstream, and again against real
# scanned SIS/government PDFs once some are available -- this fixture set is
# synthetic. The engine is selected by name in ONE place (`OCR_ENGINE`,
# overridable with the OCR_ENGINE env var without touching code) and called
# through ONE function (`_ocr_image`) -- swapping never touches `_ocr_page` or
# anything upstream of it.
OCR_ENGINE = os.environ.get("OCR_ENGINE", "tesseract")  # "tesseract" | "easyocr"
OCR_LANGS_EASYOCR = ["ta", "en"]
OCR_LANG_TESSERACT = "tam+eng"  # needs tesseract-ocr-tam (Linux) or the
                                  # Tamil language pack (Windows installer)

_easyocr_reader = None  # lazy singleton: import + model load is expensive, do it once


def _get_easyocr_reader():
    """Import and build the EasyOCR reader on first use only (see the module
    import comment above) -- never at import time, never unless selected."""
    global _easyocr_reader
    if _easyocr_reader is None:
        import easyocr  # may raise ImportError; caller handles it
        _easyocr_reader = easyocr.Reader(OCR_LANGS_EASYOCR, gpu=False)
    return _easyocr_reader


def _ocr_image(png_bytes: bytes) -> str:
    """OCR one rendered page image. The single entry point for every engine —
    this is the one function a future engine swap needs to touch. A missing
    package or binary is logged with the exact fix and the page is skipped
    (returns ""), never raised -- one bad/unconfigured page must not fail the
    whole ingestion run."""
    if OCR_ENGINE == "easyocr":
        try:
            reader = _get_easyocr_reader()
        except ImportError:
            logger.warning(
                "Scanned page skipped — OCR_ENGINE=easyocr but the 'easyocr' "
                "package is not installed: pip install easyocr (first use "
                "downloads its Tamil + English model weights)."
            )
            return ""
        lines = reader.readtext(png_bytes, detail=0, paragraph=True)
        return "\n".join(lines)

    if OCR_ENGINE == "tesseract":
        try:
            import pytesseract
            from PIL import Image
        except ImportError:
            logger.warning(
                "Scanned page skipped — OCR_ENGINE=tesseract but 'pytesseract' "
                "and/or 'pillow' is not installed: pip install pytesseract pillow."
            )
            return ""
        # No machine-specific path is hardcoded here. If the Tesseract binary
        # is not on PATH, point at it with the TESSERACT_CMD env var instead
        # of editing this file.
        tesseract_cmd = os.environ.get("TESSERACT_CMD")
        if tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
        img = Image.open(_io.BytesIO(png_bytes))
        try:
            return pytesseract.image_to_string(img, lang=OCR_LANG_TESSERACT)
        except pytesseract.TesseractNotFoundError:
            logger.warning(
                "Scanned page skipped — the Tesseract OCR binary itself was "
                "not found. Install it (`tesseract-ocr` + `tesseract-ocr-tam` "
                "on Linux; the UB-Mannheim build, with the Tamil language pack, "
                "on Windows: https://github.com/UB-Mannheim/tesseract/wiki), "
                "then either add it to PATH or set the TESSERACT_CMD "
                "environment variable to the full path of tesseract.exe / "
                "tesseract."
            )
            return ""

    raise ValueError(f"Unknown OCR_ENGINE: {OCR_ENGINE!r}")

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
    # "database_structure_reference.txt" deliberately NOT ingested: it's a
    # developer-facing schema reference (raw column/table names like
    # "survey_number_id", "survey_numbers") meant for engineers, not SIS
    # officers. When a question's words happened to match it ("survey
    # number"), it was retrieved as RAG context, and the model regurgitated
    # the literal identifier back -- "I'm sorry, but the survey number
    # survey_number is not found or not accessible" -- a nonsense answer the
    # officer could easily mistake for a real (wrong) one. Schema reference
    # belongs in this file and in code comments, never in the officer-facing
    # retrieval corpus.
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


def _ocr_page(page: "fitz.Page") -> str:
    """Render one PDF page to an image and OCR it via `_ocr_image`.

    ponytail: one page at a time, ~1-3s each on CPU — fine for the occasional
    scanned circular; add a process pool if a bulk backlog of scans shows up.
    """
    pix = page.get_pixmap(dpi=300)  # 300 DPI is the usual OCR sweet spot
    try:
        return _ocr_image(pix.tobytes("png"))
    except Exception as e:
        logger.warning(f"OCR failed on a page: {e}")
        return ""


def _extract_pdf_tables(page: "fitz.Page") -> list:
    """Pipe-delimited rows for every table PyMuPDF finds on the page.

    Uses PyMuPDF's built-in table finder — no extra dependency — in the same
    '|'-joined row shape the .docx table loader below already uses.
    """
    lines = []
    try:
        found = page.find_tables()
    except Exception:
        return lines
    for t_idx, tbl in enumerate(found.tables, start=1):
        try:
            rows = tbl.extract()
        except Exception:
            continue
        for r_idx, row in enumerate(rows, start=1):
            cells = [str(c).strip() if c else "" for c in row]
            if any(cells):
                lines.append(f"table {t_idx} row {r_idx}: " + " | ".join(cells))
    return lines


# Embedded per-page marker, stripped out of chunk text before storage; read
# back by `_page_info_for_chunks` the same way `_prefix_section_headings`
# reads '=== SECTION ===' headings below.
_PDF_PAGE_RE = re.compile(r"<<<PAGE (\d+)(\|OCR)?>>>\n?")


def load_document(file_path: Path) -> str:
    """
    Load document content from file
    """
    try:
        if file_path.suffix.lower() == ".pdf":
            if fitz is None:
                logger.error(f"Skipping {file_path.name}: PyMuPDF not installed "
                             f"(pip install pymupdf) — PDF ingestion unavailable")
                return ""
            parts = []
            page_count = 0
            ocr_pages = 0
            pdf = fitz.open(str(file_path))
            try:
                for i, page in enumerate(pdf, start=1):
                    page_count = i
                    text = (page.get_text() or "").strip()
                    used_ocr = False
                    # Rule 1 & 2: only pages with too little extractable text
                    # go to OCR — a normal text page never pays that cost.
                    if len(text) < OCR_MIN_CHARS_PER_PAGE:
                        ocr_text = _ocr_page(page).strip()
                        if ocr_text:
                            text = ocr_text
                            used_ocr = True
                            ocr_pages += 1
                    table_lines = _extract_pdf_tables(page)
                    if table_lines:
                        text = (text + "\n" + "\n".join(table_lines)).strip()
                    marker = f"<<<PAGE {i}{'|OCR' if used_ocr else ''}>>>"
                    parts.append(f"{marker}\n{text}" if text else marker)
            finally:
                pdf.close()
            content = "\n\n".join(parts)
            content = fix_tamil_reordering(content)
            logger.info(f"Loaded PDF document: {file_path.name} "
                        f"({len(content)} chars, {page_count} pages, "
                        f"{ocr_pages} via OCR)")
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

def _page_info_for_chunks(content: str, chunks: list) -> list:
    """Return (page_number, used_ocr) for each chunk, read off the embedded
    '<<<PAGE N>>>' / '<<<PAGE N|OCR>>>' markers `load_document` left in a
    PDF's text. Same "find the chunk's offset, take the marker in force
    before it" approach as `_prefix_section_headings` above, just answering a
    page number instead of prepending a heading.

    Non-PDF content has no markers, so every entry comes back (None, False)
    and the caller falls back to its old chunk-index numbering unchanged.
    """
    markers = [(m.start(), int(m.group(1)), bool(m.group(2)))
               for m in _PDF_PAGE_RE.finditer(content)]
    if not markers:
        return [(None, False)] * len(chunks)

    out = []
    cursor = 0
    for chunk in chunks:
        pos = content.find(chunk[:120], cursor)
        if pos == -1:
            pos = cursor
        cursor = max(cursor, pos + 1)
        owning = (markers[0][1], markers[0][2])
        for start, page_no, used_ocr in markers:
            if start <= pos:
                owning = (page_no, used_ocr)
            else:
                break
        out.append(owning)
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

        # Real PDF page numbers (and whether a chunk's page was OCR'd), read
        # off the markers load_document embedded. (None, False) per chunk for
        # every other file type -- page_number then falls back to chunk index,
        # exactly as before this pipeline carried PDF page numbers at all.
        page_info = _page_info_for_chunks(content, chunks)

        # Prepare chunks with metadata
        for i, chunk in enumerate(chunks):
            # Stable across runs: the store upserts ON CONFLICT (chunk_id), so a
            # random suffix here made every re-ingest insert a second copy of
            # each chunk instead of replacing it.
            chunk_id = f"{doc_name}_{i}"

            page_no, used_ocr = page_info[i]
            stored_chunk = _PDF_PAGE_RE.sub("", chunk).strip() if page_no is not None else chunk

            chunk_metadata = {
                "document_name": doc_name,
                "section": f"chunk_{i}" + ("_ocr" if used_ocr else ""),
                "category": metadata["category"],
                "source": metadata["source"],
                "language": metadata["language"],
                "page_number": page_no if page_no is not None else i + 1,
                "total_chunks": len(chunks)
            }

            all_chunks.append({
                "id": chunk_id,
                "content": stored_chunk,
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
