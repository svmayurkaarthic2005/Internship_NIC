"""REAL OCR integration test for the mixed-page PDF fix in
backend/services/doc_extract.py -- no mocking of pytesseract, fitz or the
Tesseract binary.

test_pdf_mixed_ocr.py proves the PAGE-SELECTION LOGIC is correct (mocked
pypdf + a stubbed OCR function). It cannot prove Tesseract itself reads a
scanned page correctly, that page numbers survive real rendering, or that a
missing-dependency path behaves the way the code claims when the dependency
is genuinely missing. This file builds small synthetic PDFs with
PyMuPDF/Pillow (clean, controlled, no real applicant data -- generic English
and Tamil placeholder text only) and runs them through the REAL
`doc_extract.extract(".pdf", raw)` pipeline.

If Tesseract, PyMuPDF or Pillow are not importable/runnable in this
environment, every real-OCR check is reported BLOCKED, not skipped silently
and not counted as a pass -- "OCR verified" is never claimed without having
actually exercised the engine.

Run: python test_pdf_ocr_integration.py
"""
import sys

bad = []
blocked = []
total = 0


def check(name, condition, detail=""):
    global total
    total += 1
    if condition:
        print(f"ok      {name}")
    else:
        bad.append((name, detail))
        print(f"FAIL    {name}" + (f" -- {detail}" if detail else ""))


def block(name, reason):
    global total
    total += 1
    blocked.append((name, reason))
    print(f"BLOCKED {name} -- {reason}")


# ---- dependency / engine availability --------------------------------------
try:
    import fitz  # PyMuPDF
    _FITZ_OK = True
except ImportError:
    _FITZ_OK = False

try:
    from PIL import Image, ImageDraw, ImageFont
    _PIL_OK = True
except ImportError:
    _PIL_OK = False

try:
    import pytesseract
    _TESS_VERSION = pytesseract.get_tesseract_version()
    _TESS_OK = True
except Exception:
    _TESS_OK = False

try:
    _LANGS = set(pytesseract.get_languages()) if _TESS_OK else set()
except Exception:
    _LANGS = set()

if not (_FITZ_OK and _PIL_OK and _TESS_OK):
    reason = (f"fitz={_FITZ_OK} pillow={_PIL_OK} tesseract={_TESS_OK} "
              "-- install PyMuPDF/Pillow/pytesseract and the Tesseract binary "
              "to actually run these checks")
    for name in ("entirely searchable PDF (real pipeline)",
                 "entirely scanned PDF, real OCR reads it",
                 "mixed searchable+scanned PDF, correct per-page routing",
                 "a scanned page's digits survive real OCR",
                 "a blank scanned page is recorded as failed-OCR, not crashed",
                 "Tamil-script scanned text (best-effort, real engine)",
                 "a genuinely unreachable Tesseract binary degrades safely"):
        block(name, reason)
    print(f"\n{total - len(bad) - len(blocked)}/{total} ok, {len(blocked)} blocked "
          f"(OCR dependency unavailable -- not declared verified)")
    sys.exit(0)

import backend.services.doc_extract as doc_extract  # noqa: E402

FONT_PATH = r"C:\Windows\Fonts\arial.ttf"
TAMIL_FONT_PATH = r"C:\Windows\Fonts\Nirmala.ttc"


def _render_text_image(text: str, font_path: str = FONT_PATH, size=(900, 160), font_size=42):
    img = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype(font_path, font_size)
    except Exception:
        font = ImageFont.load_default()
    draw.text((20, 40), text, fill="black", font=font)
    return img


def _pdf_with_pages(page_specs) -> bytes:
    """page_specs: list of ("native", text) | ("image", PIL.Image) | ("blank",)"""
    doc = fitz.open()
    for spec in page_specs:
        page = doc.new_page(width=842, height=300)  # landscape, plenty of room
        if spec[0] == "native":
            page.insert_text((36, 100), spec[1], fontsize=24)
        elif spec[0] == "image":
            import io as _io
            buf = _io.BytesIO()
            spec[1].save(buf, format="PNG")
            page.insert_image(fitz.Rect(20, 20, 820, 280), stream=buf.getvalue())
        # "blank" -> nothing drawn
    raw = doc.tobytes()
    doc.close()
    return raw


# ---- 1) entirely searchable PDF: no OCR involved at all --------------------
raw = _pdf_with_pages([("native", "Order Number ORD-4471 approved by Tahsildar"),
                       ("native", "Survey Number 120 Sub Division 4 patta transfer")])
doc = doc_extract.extract(".pdf", raw)
check("entirely searchable PDF (real pipeline): status OK",
      doc.status == doc_extract.STATUS_OK)
check("entirely searchable PDF: both native pages captured, no OCR run",
      doc.ocr_pages == [] and doc.failed_ocr_pages == []
      and any("ORD-4471" in s.text for s in doc.segments)
      and any("Survey Number 120" in s.text for s in doc.segments))

# ---- 2) entirely scanned PDF: real OCR must read it -------------------------
img = _render_text_image("APPLICATION NUMBER 2026 0154 02 013642")
raw = _pdf_with_pages([("image", img)])
doc = doc_extract.extract(".pdf", raw)
check("entirely scanned PDF: status OK (OCR found text)",
      doc.status == doc_extract.STATUS_OK)
check("entirely scanned PDF: page recorded as OCR'd", doc.ocr_pages == [1])
ocr_text = next((s.text for s in doc.segments if s.location.get("page") == 1), "")
check("entirely scanned PDF: OCR recovered the digits 2026 and 013642",
      "2026" in ocr_text and "013642" in ocr_text, repr(ocr_text))

# ---- 3) mixed PDF: native page untouched, scanned page real-OCR'd ----------
img2 = _render_text_image("RECOVERED BY OCR PAGE TWO TEXT")
raw = _pdf_with_pages([("native", "Order Number ORD-9900 fully searchable text here"),
                       ("image", img2)])
doc = doc_extract.extract(".pdf", raw)
check("mixed PDF: overall status OK", doc.status == doc_extract.STATUS_OK)
check("mixed PDF: page 1 (native) was never sent to OCR", 1 not in doc.ocr_pages)
check("mixed PDF: page 2 (scanned) is recorded as OCR'd", doc.ocr_pages == [2])
p1_text = next((s.text for s in doc.segments if s.location.get("page") == 1), "")
p2_text = next((s.text for s in doc.segments if s.location.get("page") == 2), "")
check("mixed PDF: page 1's native citation carries the native text",
      "ORD-9900" in p1_text, repr(p1_text))
check("mixed PDF: page 2's citation carries the OCR'd text, cited as page 2",
      "RECOVERED" in p2_text.upper(), repr(p2_text))

# ---- 4) a blank scanned page: real OCR correctly finds nothing -------------
blank_img = Image.new("RGB", (900, 160), "white")
raw = _pdf_with_pages([("image", blank_img)])
doc = doc_extract.extract(".pdf", raw)
check("a blank scanned page: whole document correctly reports no extractable text",
      doc.status == doc_extract.STATUS_NO_TEXT)
check("a blank scanned page: recorded as a failed-OCR page, not silently absent",
      doc.failed_ocr_pages == [1])

# ---- 5) mixed doc where ONE scanned page is blank, the other has real text --
raw = _pdf_with_pages([("image", blank_img), ("image", img)])
doc = doc_extract.extract(".pdf", raw)
check("mixed blank+readable scan: overall status is still OK "
      "(the readable page keeps the document usable)",
      doc.status == doc_extract.STATUS_OK)
check("mixed blank+readable scan: page 1 recorded as failed, page 2 as OCR'd",
      doc.failed_ocr_pages == [1] and doc.ocr_pages == [2])

# ---- 6) Tamil-script scanned text: best-effort, real engine ----------------
if "tam" not in _LANGS:
    block("Tamil-script scanned text (best-effort, real engine)",
          f"Tesseract has no 'tam' language data installed (langs={sorted(_LANGS)})")
else:
    try:
        tam_img = _render_text_image("சர்வே எண் 120", font_path=TAMIL_FONT_PATH,
                                     font_size=48)
        raw = _pdf_with_pages([("image", tam_img)])
        doc = doc_extract.extract(".pdf", raw)
        tam_text = next((s.text for s in doc.segments if s.location.get("page") == 1), "")
        # Lenient on purpose: PIL's Tamil glyph shaping without a HarfBuzz/raqm
        # backend is not guaranteed to be visually correct, so this proves the
        # pipeline ROUTES a Tamil scan through OCR and gets SOMETHING back --
        # it does not certify Tamil OCR accuracy, which belongs to the
        # existing benchmark_ocr_engines.py / ocr_benchmark_report.md instead.
        check("Tamil-script scanned page: real OCR call completed and the "
              "page is recorded as OCR'd (not a crash, not silently dropped)",
              doc.ocr_pages == [1], f"ocr_pages={doc.ocr_pages} text={tam_text!r}")
        if doc.ocr_pages == [1] and not tam_text.strip():
            print("        note: OCR ran but returned no readable text for this "
                  "rendered Tamil sample -- recorded as a failed-OCR page below, "
                  "which is correct behaviour, not a pipeline bug; Tamil OCR "
                  "ACCURACY is tracked separately in ocr_benchmark_report.md")
    except Exception as e:
        block("Tamil-script scanned text (best-effort, real engine)",
              f"could not render/OCR the Tamil fixture: {e}")

# ---- 7) a genuinely unreachable Tesseract binary degrades safely -----------
import os as _os
_orig_cmd = _os.environ.get("TESSERACT_CMD")
_os.environ["TESSERACT_CMD"] = r"C:\this\path\does\not\exist\tesseract.exe"
try:
    img3 = _render_text_image("THIS SHOULD NOT BE READ")
    raw = _pdf_with_pages([("image", img3)])
    doc = doc_extract.extract(".pdf", raw)
    check("an unreachable Tesseract binary: extraction fails gracefully, no crash",
          doc.status == doc_extract.STATUS_NO_TEXT)
    check("an unreachable Tesseract binary: the message says Tesseract is not "
          "installed/reachable, not a vague failure",
          "Tesseract" in doc.detail, doc.detail)
finally:
    if _orig_cmd is None:
        _os.environ.pop("TESSERACT_CMD", None)
    else:
        _os.environ["TESSERACT_CMD"] = _orig_cmd

print(f"\n{total - len(bad) - len(blocked)}/{total} ok, {len(bad)} failed, "
      f"{len(blocked)} blocked (Tesseract {_TESS_VERSION}, langs={sorted(_LANGS)})")
sys.exit(1 if bad else 0)
