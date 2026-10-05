"""Self-check for backend/ingest.py's PDF page-number tracking through chunking.

Does not touch fitz/easyocr/pytesseract or the database -- it only proves that
a '<<<PAGE N>>>' / '<<<PAGE N|OCR>>>' marker (what load_document embeds per
PDF page) survives RecursiveCharacterTextSplitter and is read back correctly
by _page_info_for_chunks, with the marker itself stripped from stored text.
Run: python test_ingest_pdf_pages.py
"""
from backend.ingest import chunk_document, _page_info_for_chunks, _PDF_PAGE_RE

# Three synthetic "pages", each padded well past chunk_size (2000 chars) so
# each actually becomes its own chunk, like a real government-document page
# does -- a page short enough to share a chunk with its neighbour is a real,
# accepted case too (see the merge note in _page_info_for_chunks' docstring),
# just not what this check is proving.
content = (
    "<<<PAGE 1>>>\n" + ("Survey manual clause about boundary disputes. " * 80) +
    "\n\n<<<PAGE 2>>>\n" + ("Field inspection checklist item for ward officers. " * 80) +
    "\n\n<<<PAGE 3|OCR>>>\n" + ("Scanned circular text recovered via OCR fallback. " * 80)
)

chunks = chunk_document(content, "test.pdf")
assert chunks, "chunker produced no chunks"

page_info = _page_info_for_chunks(content, chunks)
assert len(page_info) == len(chunks)

# Every chunk must resolve to a real page number, markers must be gone from
# the stored text, and the OCR flag must land only on page 3's chunk(s).
seen_pages = set()
for chunk, (page_no, used_ocr) in zip(chunks, page_info):
    assert page_no is not None, "PDF content must never fall back to chunk-index numbering"
    # ingest_documents() strips the marker before storing -- mirror that here.
    stored = _PDF_PAGE_RE.sub("", chunk).strip()
    assert not _PDF_PAGE_RE.search(stored), f"page marker survives stripping: {stored[:60]!r}"
    seen_pages.add(page_no)
    if used_ocr:
        assert page_no == 3, f"OCR flag attached to the wrong page: {page_no}"

assert seen_pages == {1, 2, 3}, f"expected pages 1-3, got {seen_pages}"
assert any(used_ocr for _, used_ocr in page_info), "page 3's OCR flag was lost"

# Non-PDF content (no markers) must fall back exactly as before this change.
plain_chunks = chunk_document("Plain text with no page markers at all.", "test.txt")
plain_info = _page_info_for_chunks("Plain text with no page markers at all.", plain_chunks)
assert all(p == (None, False) for p in plain_info), "non-PDF content must not report a page"

print(f"OK: {len(chunks)} PDF chunks across pages {sorted(seen_pages)}, "
      f"{len(plain_chunks)} plain chunk(s) correctly unmarked")
