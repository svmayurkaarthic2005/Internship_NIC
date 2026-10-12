"""Regression: a PDF mixing searchable pages with scanned/image-only pages
must OCR only the pages that actually need it, not be judged document-wide.

Root cause (backend/services/doc_extract.py, `_from_pdf`): OCR used to run
only when pypdf found NO text ANYWHERE in the whole file (`if not
doc.has_text`). A single selectable page made every image-only page in the
same file come back silent -- no segment, no citation, nothing -- while the
document as a whole still reported STATUS_OK, so an officer asking about
something that was only on the scanned page got "I could not find this in
the uploaded document" with no hint a page had been skipped.

Fix: each page's own native-text length is checked against
OCR_MIN_CHARS_PER_PAGE (mirrors the identical rule already used for the
offline corpus in backend/ingest.py), and only the pages under that
threshold are sent to OCR. Pages that needed OCR and got nothing are
recorded separately from pages that were never a problem.

No real PDF bytes, fitz or Tesseract needed -- `pypdf` is faked at the import
site (`from pypdf import PdfReader`, inside the function) and
`_ocr_pdf_pages` is monkeypatched directly, since it is doc_extract's own
single OCR entry point for this path.

Run: python test_pdf_mixed_ocr.py
"""
import sys
import types

bad = []
total = 0


def check(name, condition, detail=""):
    global total
    total += 1
    if condition:
        print(f"ok   {name}")
    else:
        bad.append((name, detail))
        print(f"FAIL {name}" + (f" -- {detail}" if detail else ""))


class _FakePage:
    def __init__(self, text):
        self._text = text

    def extract_text(self):
        return self._text


class _FakeReader:
    def __init__(self, pages_text):
        self.pages = [_FakePage(t) for t in pages_text]
        self.is_encrypted = False


def _install_fake_pypdf(pages_text):
    fake_pypdf = types.ModuleType("pypdf")
    fake_pypdf.PdfReader = lambda _stream: _FakeReader(pages_text)
    sys.modules["pypdf"] = fake_pypdf


import backend.services.doc_extract as doc_extract  # noqa: E402

LONG_TEXT = "Order approving the patta transfer for survey number 120. " * 5  # >> 20 chars


def test_mixed_document(ocr_by_page):
    """3 pages: 0 has long native text, 1 is image-only (no native text,
    OCR succeeds), 2 is image-only (no native text, OCR also fails)."""
    _install_fake_pypdf([LONG_TEXT, "", ""])
    doc_extract._ocr_pdf_pages = lambda raw, indices: {
        i: ocr_by_page.get(i, "") for i in indices
    }
    return doc_extract._from_pdf(b"%PDF-fake")


doc = test_mixed_document({1: "Recovered via OCR: approved by Tahsildar on 2024."})

check("page 1 (native text) kept as a normal page segment",
      any(s.location.get("page") == 1 and "Order approving" in s.text for s in doc.segments))
check("page 2 (image-only, OCR succeeded) is present and cited as page 2",
      any(s.location.get("page") == 2 and "Recovered via OCR" in s.text for s in doc.segments))
check("page 2 is recorded as OCR'd", doc.ocr_pages == [2])
check("page 3 (image-only, OCR also found nothing) produced no segment",
      not any(s.location.get("page") == 3 for s in doc.segments))
check("page 3 is recorded as a failed-OCR page, not silently dropped",
      doc.failed_ocr_pages == [3])
check("the document as a whole is still STATUS_OK (pages 1-2 have real text)",
      doc.status == doc_extract.STATUS_OK)
check("page 1's untouched native text was never sent to OCR",
      1 not in doc.ocr_pages and 1 not in doc.failed_ocr_pages)
check("the detail string names the OCR and failed pages so the officer is told",
      "2" in doc.detail and "3" in doc.detail)

# A page with SHORT (but real) native text, where OCR finds nothing better,
# must keep the short native text rather than discarding it.
_install_fake_pypdf(["short"])
doc_extract._ocr_pdf_pages = lambda raw, indices: {}
doc2 = doc_extract._from_pdf(b"%PDF-fake")
check("a short-but-real native page survives when OCR adds nothing",
      any(s.text == "short" for s in doc2.segments))
check("a kept short native page is not falsely marked as OCR'd", doc2.ocr_pages == [])

# All pages scanned, OCR unavailable/empty everywhere -> the whole-document
# no-text refusal, unchanged behaviour.
_install_fake_pypdf(["", ""])
doc_extract._ocr_pdf_pages = lambda raw, indices: {}
doc3 = doc_extract._from_pdf(b"%PDF-fake")
check("an entirely blank/unreadable PDF still reports STATUS_NO_TEXT",
      doc3.status == doc_extract.STATUS_NO_TEXT)
check("both pages are recorded as failed", doc3.failed_ocr_pages == [1, 2])

# A fully native (non-scanned) document never calls OCR at all.
_calls = []
_install_fake_pypdf([LONG_TEXT, LONG_TEXT])
doc_extract._ocr_pdf_pages = lambda raw, indices: (_calls.append(indices) or {})
doc4 = doc_extract._from_pdf(b"%PDF-fake")
check("an all-native PDF never invokes OCR at all", _calls == [])
check("both native pages are kept", len(doc4.segments) == 2)

print(f"{total - len(bad)}/{total} ok")
sys.exit(1 if bad else 0)
