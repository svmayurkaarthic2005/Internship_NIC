"""Turn an uploaded file into located text segments for the attachment store.

Supported: ``.txt``, ``.csv``, ``.pdf``, ``.docx``. The legacy binary ``.doc``
format is not (no safe pure-Python reader is bundled) -- callers ask the
officer to save it as .docx or PDF. Scanned / image-only PDFs stay
unsupported by design: there is no OCR here, and a page with no selectable
text comes back as ``no_extractable_text`` rather than as an empty answer.

Every unit of text carries the location it came from -- a PDF page, a DOCX
paragraph range or table, a CSV row range, a TXT line range. That location is
the *only* source a citation is ever rendered from, so the model cannot invent
one: see ``citation_label``.

The module is importable without the app (tests, tooling): the settings import
is guarded and falls back to the same defaults ``backend.config`` declares.
"""
from __future__ import annotations

import csv as _csv
import io as _io
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

try:
    from backend.config import settings as _settings
    MAX_DOC_PAGES = _settings.UPLOAD_MAX_DOC_PAGES
    EST_CHARS_PER_PAGE = _settings.UPLOAD_EST_CHARS_PER_PAGE
    MAX_CHARS = _settings.UPLOAD_MAX_DOC_CHARS
    MAX_CSV_ROWS = _settings.UPLOAD_MAX_CSV_ROWS
    MAX_CSV_COLUMNS = _settings.UPLOAD_MAX_CSV_COLUMNS
except Exception:  # keep the module importable outside the app
    MAX_DOC_PAGES = 15
    EST_CHARS_PER_PAGE = 1800
    MAX_CHARS = 20_000
    MAX_CSV_ROWS = 20_000
    MAX_CSV_COLUMNS = 200

TEXT_EXTS = {".txt", ".csv"}
RICH_EXTS = {".pdf", ".docx"}
SUPPORTED_EXTS = TEXT_EXTS | RICH_EXTS

# Extensions we recognise but cannot read — message shown to the officer.
UNSUPPORTED_HINT = {
    ".doc": ("The legacy .doc format can't be read. Save it as .docx or PDF, "
             "or paste the text into the chat."),
}

# Extraction status values, mirrored by ck_attachment_extraction_status.
STATUS_OK = "ok"
STATUS_NO_TEXT = "no_extractable_text"
STATUS_FAILED = "failed"

# Allowed MIME types per extension. The client's Content-Type is checked
# against this list but is never the deciding evidence — the content signature
# is (see `sniff_container`). Browsers send blanks and wrong values often
# enough that a strict Content-Type check would reject honest uploads.
ALLOWED_MIMES = {
    ".txt": {"text/plain", "application/octet-stream", ""},
    ".csv": {"text/csv", "application/csv", "text/plain",
             "application/vnd.ms-excel", "application/octet-stream", ""},
    ".pdf": {"application/pdf", "application/octet-stream", ""},
    ".docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document",
              "application/zip", "application/octet-stream", ""},
}


class ExtractionError(Exception):
    """A recognised file type that could not be turned into text."""


class UnsupportedFile(Exception):
    """A file type this assistant does not read at all."""


# ─────────────────────────────────────────────────────────────────────────────
# Data shapes
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Segment:
    """A piece of text plus where in the file it came from."""
    text: str
    location: Dict[str, Any]


@dataclass
class ExtractedDocument:
    status: str = STATUS_OK
    detail: str = ""
    segments: List[Segment] = field(default_factory=list)
    page_count: Optional[int] = None
    truncated: bool = False
    csv_headers: Optional[List[str]] = None
    csv_rows: Optional[List[Dict[str, str]]] = None   # row_number == index + 1

    @property
    def char_count(self) -> int:
        return sum(len(s.text) for s in self.segments)

    @property
    def has_text(self) -> bool:
        return any(s.text.strip() for s in self.segments)


# ─────────────────────────────────────────────────────────────────────────────
# Citations — rendered from stored metadata, never from model output
# ─────────────────────────────────────────────────────────────────────────────

def _rng(word_singular: str, word_plural: str, a: int, b: Optional[int]) -> str:
    if b is None or b <= a:
        return f"{word_singular} {a}"
    return f"{word_plural} {a}–{b}"


def citation_label(filename: str, location: Dict[str, Any]) -> str:
    """Render the inline citation for a stored location.

    Returns '' when the location says nothing usable — the caller then gives no
    citation at all rather than a vague one.
    """
    if not location:
        return ""
    kind = location.get("kind")
    if kind == "page":
        page = location.get("page")
        if not page:
            return ""
        return f"{filename}, {_rng('page', 'pages', page, location.get('page_end'))}"
    if kind == "paragraphs":
        start = location.get("start")
        if not start:
            return ""
        return (f"{filename}, "
                f"{_rng('paragraph', 'paragraphs', start, location.get('end'))}")
    if kind == "table":
        table = location.get("table")
        if not table:
            return ""
        label = f"{filename}, table {table}"
        rs, re_ = location.get("row_start"), location.get("row_end")
        if rs:
            label += f" ({_rng('row', 'rows', rs, re_)})"
        return label
    if kind == "rows":
        start = location.get("start")
        if not start:
            return ""
        return f"{filename}, {_rng('row', 'rows', start, location.get('end'))}"
    if kind == "lines":
        start = location.get("start")
        if not start:
            return ""
        return f"{filename}, {_rng('line', 'lines', start, location.get('end'))}"
    return ""


# ─────────────────────────────────────────────────────────────────────────────
# Content-signature validation
# ─────────────────────────────────────────────────────────────────────────────

_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"   # legacy .doc / .xls container
_ZIP_MAGIC = b"PK\x03\x04"
_PDF_MAGIC = b"%PDF-"


def sniff_container(raw: bytes) -> str:
    """Classify the bytes by signature: 'pdf' | 'zip' | 'ole' | 'text' | 'binary'."""
    head = raw[:2048]
    if head.startswith(_PDF_MAGIC) or head[:1024].find(_PDF_MAGIC) != -1 and head.startswith(b"%"):
        return "pdf"
    if head.startswith(_ZIP_MAGIC):
        return "zip"
    if head.startswith(_OLE_MAGIC):
        return "ole"
    if b"\x00" in head:
        return "binary"
    for enc in ("utf-8-sig", "utf-16", "latin-1"):
        try:
            head.decode(enc)
            return "text"
        except UnicodeDecodeError:
            continue
    return "binary"


def normalise_extension(filename: str) -> str:
    name = (filename or "").strip()
    return ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""


def validate_signature(ext: str, raw: bytes, declared_mime: str = "") -> None:
    """Reject a file whose bytes do not match the extension it claims.

    The filename and the browser's Content-Type are both client-supplied, so
    neither decides anything on its own. A .txt holding a ZIP, or a .pdf that
    is really a Word binary, stops here.
    """
    if not raw:
        raise ExtractionError("The file is empty.")
    if ext in UNSUPPORTED_HINT:
        raise UnsupportedFile(UNSUPPORTED_HINT[ext])
    if ext not in SUPPORTED_EXTS:
        raise UnsupportedFile(
            "Unsupported file type. Upload a .txt, .csv, .pdf or .docx file. "
            "Images are not read — this assistant has no OCR and no vision.")
    kind = sniff_container(raw)
    if kind == "ole":
        raise UnsupportedFile(UNSUPPORTED_HINT[".doc"])
    if ext == ".pdf" and kind != "pdf":
        raise ExtractionError("This file is not a PDF, whatever its name says.")
    if ext == ".docx" and kind != "zip":
        raise ExtractionError("This file is not a .docx document, whatever its name says.")
    if ext in TEXT_EXTS and kind != "text":
        raise ExtractionError(
            f"A {ext} file must be plain text; these bytes are a "
            f"{'PDF' if kind == 'pdf' else 'binary'} file.")
    allowed = ALLOWED_MIMES.get(ext)
    mime = (declared_mime or "").split(";")[0].strip().lower()
    if allowed is not None and mime and mime not in allowed:
        # Content already matched the signature check above; the mismatch is
        # logged as a mismatch, not treated as authority. Wrong browser MIME
        # types are common, so this only rejects an actively contradictory one.
        if (mime.startswith("image/") or mime.startswith("video/")
                or mime.startswith("audio/")):
            raise ExtractionError(
                "Images and media files are not supported — this assistant "
                "reads text only, and does not do OCR.")


# ─────────────────────────────────────────────────────────────────────────────
# Extraction
# ─────────────────────────────────────────────────────────────────────────────

def extract(ext: str, raw: bytes) -> ExtractedDocument:
    """Extract located text. Raises ExtractionError / UnsupportedFile."""
    ext = (ext or "").lower()
    if ext in UNSUPPORTED_HINT:
        raise UnsupportedFile(UNSUPPORTED_HINT[ext])
    if ext == ".csv":
        doc = _from_csv(raw)
    elif ext == ".txt":
        doc = _from_txt(raw)
    elif ext == ".pdf":
        doc = _from_pdf(raw)
    elif ext == ".docx":
        doc = _from_docx(raw)
    else:
        raise UnsupportedFile(
            "Unsupported file type. Upload a .txt, .csv, .pdf or .docx file.")
    return _apply_char_budget(doc)


def _apply_char_budget(doc: ExtractedDocument) -> ExtractedDocument:
    """Trim the extracted text to the configured ceiling, on segment order.

    Truncation is recorded so the officer is told the tail was not read — an
    answer drawn from a trimmed document must not read as an answer drawn from
    the whole of it.
    """
    kept: List[Segment] = []
    budget = MAX_CHARS
    for seg in doc.segments:
        if budget <= 0:
            doc.truncated = True
            break
        if len(seg.text) <= budget:
            kept.append(seg)
            budget -= len(seg.text)
        else:
            kept.append(Segment(seg.text[:budget], dict(seg.location, partial=True)))
            budget = 0
            doc.truncated = True
    doc.segments = kept
    return doc


def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-16", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


def _from_txt(raw: bytes) -> ExtractedDocument:
    text = _decode(raw)
    lines = text.splitlines()
    doc = ExtractedDocument()
    # One segment per ~40 lines keeps a line-range citation meaningful without
    # producing a segment per line.
    step = 40
    for start in range(0, len(lines), step):
        block = lines[start:start + step]
        body = "\n".join(block).strip()
        if not body:
            continue
        doc.segments.append(Segment(
            body, {"kind": "lines", "start": start + 1, "end": start + len(block)}))
    if not doc.has_text:
        doc.status = STATUS_NO_TEXT
        doc.detail = "The text file is empty."
    return doc


def _sniff_dialect(sample: str) -> Any:
    try:
        return _csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except Exception:
        return _csv.excel


# A cell that starts with one of these is a spreadsheet formula. It is kept
# verbatim as TEXT and never evaluated — not here, not in csv_ops, not by the
# LLM. (It is also the CSV-injection vector, which is the same defence.)
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t=", "\r=")


def is_formula_like(value: str) -> bool:
    v = (value or "").lstrip()
    if not v:
        return False
    if v[0] in ("=", "@"):
        return True
    # A leading +/- followed by a letter or '(' is a formula; -12.5 is a number.
    if v[0] in ("+", "-") and len(v) > 1 and (v[1].isalpha() or v[1] == "("):
        return True
    return False


def _from_csv(raw: bytes) -> ExtractedDocument:
    text = _decode(raw)
    if not text.strip():
        doc = ExtractedDocument(status=STATUS_NO_TEXT, detail="The CSV file is empty.")
        return doc
    dialect = _sniff_dialect(text[:8192])
    reader = _csv.reader(_io.StringIO(text), dialect)
    try:
        rows = list(reader)
    except Exception as e:
        raise ExtractionError(f"could not read the CSV ({e}).") from e
    rows = [r for r in rows if any((c or "").strip() for c in r)]
    if not rows:
        return ExtractedDocument(status=STATUS_NO_TEXT, detail="The CSV file has no rows.")

    headers = [(_h or "").strip() or f"column_{i + 1}" for i, _h in enumerate(rows[0])]
    if len(headers) > MAX_CSV_COLUMNS:
        raise ExtractionError(
            f"This CSV has {len(headers)} columns; the limit is {MAX_CSV_COLUMNS}.")
    # De-duplicate header names so a row dict never loses a column silently.
    seen: Dict[str, int] = {}
    for i, h in enumerate(headers):
        if h in seen:
            seen[h] += 1
            headers[i] = f"{h}_{seen[h]}"
        else:
            seen[h] = 1

    data_rows: List[Dict[str, str]] = []
    truncated = False
    for r in rows[1:]:
        if len(data_rows) >= MAX_CSV_ROWS:
            truncated = True
            break
        cells = [(c if c is not None else "") for c in r]
        cells += [""] * (len(headers) - len(cells))
        data_rows.append({h: str(cells[i]).strip() for i, h in enumerate(headers)})

    doc = ExtractedDocument(csv_headers=headers, csv_rows=data_rows, truncated=truncated)
    # Prose segments for retrieval: a header line plus a block of rows, so a
    # retrieved chunk cites "rows 18–24" and still shows what the columns mean.
    header_line = " | ".join(headers)
    step = 20
    for start in range(0, len(data_rows), step):
        block = data_rows[start:start + step]
        body_lines = [f"columns: {header_line}"]
        for j, row in enumerate(block):
            body_lines.append(
                f"row {start + j + 1}: "
                + " | ".join(f"{h}={row.get(h, '')}" for h in headers))
        doc.segments.append(Segment("\n".join(body_lines), {
            "kind": "rows", "start": start + 1, "end": start + len(block)}))
    if not data_rows:
        doc.segments.append(Segment(f"columns: {header_line}",
                                    {"kind": "rows", "start": 0, "end": 0}))
    return doc


def _from_pdf(raw: bytes) -> ExtractedDocument:
    try:
        from pypdf import PdfReader
    except ImportError as e:  # pragma: no cover - depends on environment
        raise ExtractionError("PDF support needs the 'pypdf' package installed.") from e
    try:
        reader = PdfReader(_io.BytesIO(raw))
        if getattr(reader, "is_encrypted", False):
            try:
                reader.decrypt("")
            except Exception:
                raise ExtractionError(
                    "This PDF is password-protected. Upload an unlocked copy.")
        page_count = len(reader.pages)
    except ExtractionError:
        raise
    except Exception as e:
        raise ExtractionError(f"could not read the PDF ({e}).") from e

    if page_count == 0:
        raise ExtractionError("This PDF has no pages.")
    if page_count > MAX_DOC_PAGES:
        raise ExtractionError(
            f"This PDF has {page_count} pages; the limit is {MAX_DOC_PAGES}. "
            f"Upload only the relevant pages.")

    doc = ExtractedDocument(page_count=page_count)
    for i, page in enumerate(reader.pages):
        try:
            body = (page.extract_text() or "").strip()
        except Exception:
            body = ""
        if body:
            doc.segments.append(Segment(body, {"kind": "page", "page": i + 1}))
    if not doc.has_text:
        doc.status = STATUS_NO_TEXT
        doc.detail = (
            "No selectable text found in this PDF — it looks like a scan or a "
            "set of page images. This assistant does not read images or run "
            "OCR. Upload a text PDF, or paste the text into the chat.")
    return doc


def _from_docx(raw: bytes) -> ExtractedDocument:
    try:
        import docx  # python-docx
    except ImportError as e:  # pragma: no cover - depends on environment
        raise ExtractionError("Word support needs the 'python-docx' package installed.") from e
    try:
        document = docx.Document(_io.BytesIO(raw))
    except Exception as e:
        raise ExtractionError(f"could not read the Word file ({e}).") from e

    doc = ExtractedDocument()
    # Paragraphs, numbered as the officer would count them (non-empty ones).
    para_no = 0
    run_start: Optional[int] = None
    buf: List[str] = []
    for p in document.paragraphs:
        t = (p.text or "").strip()
        if not t:
            continue
        para_no += 1
        if run_start is None:
            run_start = para_no
        buf.append(t)
        if sum(len(x) for x in buf) >= 900:
            doc.segments.append(Segment("\n".join(buf), {
                "kind": "paragraphs", "start": run_start, "end": para_no}))
            buf, run_start = [], None
    if buf and run_start is not None:
        doc.segments.append(Segment("\n".join(buf), {
            "kind": "paragraphs", "start": run_start, "end": para_no}))

    # Tables, each cited by its own number — "report.docx, table 2".
    for t_idx, table in enumerate(document.tables, start=1):
        lines: List[str] = []
        for r_idx, row in enumerate(table.rows, start=1):
            cells = [(c.text or "").strip() for c in row.cells]
            if any(cells):
                lines.append(f"row {r_idx}: " + " | ".join(cells))
        if lines:
            doc.segments.append(Segment("\n".join(lines), {
                "kind": "table", "table": t_idx,
                "row_start": 1, "row_end": len(lines)}))

    if not doc.has_text:
        doc.status = STATUS_NO_TEXT
        doc.detail = (
            "No text found in this Word document — it may contain only images. "
            "This assistant does not read images or run OCR.")
        return doc

    est_pages = max(1, -(-doc.char_count // EST_CHARS_PER_PAGE))
    doc.page_count = est_pages
    if est_pages > MAX_DOC_PAGES:
        raise ExtractionError(
            f"This Word document is about {est_pages} pages of text; the limit "
            f"is {MAX_DOC_PAGES}. Upload a shorter extract.")
    return doc


# ─────────────────────────────────────────────────────────────────────────────
# Backwards-compatible helper (kept: a few tools call it for a plain dump)
# ─────────────────────────────────────────────────────────────────────────────

def extract_text(ext: str, raw: bytes) -> str:
    """Plain-text dump, locations discarded. Prefer `extract`."""
    doc = extract(ext, raw)
    if doc.status == STATUS_NO_TEXT:
        raise ExtractionError(doc.detail or "No extractable text.")
    return "\n\n".join(s.text for s in doc.segments)
