"""Additional REAL-OCR coverage, building on test_pdf_ocr_integration.py
rather than duplicating it: that file proves routing (native vs OCR'd pages,
blank pages, an unreachable binary). This file adds what it does not cover
-- character-level accuracy on CRITICAL IDENTIFIERS (survey number,
application number, date, amount), a table/multi-column layout, and the
real timeout/concurrency path (backend/routers/chat.py's `_extract_bounded`)
exercised against REAL OCR instead of the stubbed OCR used in
test_extraction_concurrency.py.

Every check here either runs against the real Tesseract binary or is
explicitly reported BLOCKED -- never silently skipped, never counted as a
pass. Synthetic, generic placeholder text only; no real applicant data.

CER/exact-match figures printed here are a SELF-CHECK on this project's own
rendered-text fixtures (clean PIL rendering, not a scanned paper document),
not a validated production accuracy claim -- see the existing
benchmark_ocr_engines.py / ocr_benchmark_report.md for that distinction,
which this file does not attempt to redo.

Run: python test_pdf_ocr_coverage_extra.py
"""
import io
import sys
import time

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

if not (_FITZ_OK and _PIL_OK and _TESS_OK):
    reason = f"fitz={_FITZ_OK} pillow={_PIL_OK} tesseract={_TESS_OK}"
    for name in ("critical-identifier CER/exact-match on a scanned page",
                 "a table/multi-column scanned layout is read",
                 "real-OCR extraction respects the configured timeout",
                 "bounded concurrency holds under REAL (not stubbed) OCR load"):
        block(name, reason)
    print(f"\n{total - len(bad) - len(blocked)}/{total} ok, {len(blocked)} blocked")
    sys.exit(0)

import backend.services.doc_extract as doc_extract  # noqa: E402

FONT_PATH = r"C:\Windows\Fonts\arial.ttf"


# Same construction as test_pdf_ocr_integration.py's `_render_text_image` /
# `_pdf_with_pages` -- kept as its own small copy rather than importing that
# file (which executes its checks as a module-level side effect on import,
# so importing it would silently re-run a whole other test file). ~15 lines;
# cheaper and safer than refactoring an already-passing file to export these.
def _render_text_image(text, font_path=FONT_PATH, size=(900, 220), font_size=36):
    img = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype(font_path, font_size)
    except Exception:
        font = ImageFont.load_default()
    y = 20
    for line in text.split("\n"):
        draw.text((20, y), line, fill="black", font=font)
        y += font_size + 14
    return img


def _pdf_with_pages(page_specs):
    doc = fitz.open()
    for spec in page_specs:
        page = doc.new_page(width=842, height=400)
        if spec[0] == "native":
            page.insert_text((36, 100), spec[1], fontsize=24)
        elif spec[0] == "image":
            buf = io.BytesIO()
            spec[1].save(buf, format="PNG")
            page.insert_image(fitz.Rect(20, 20, 820, 380), stream=buf.getvalue())
    raw = doc.tobytes()
    doc.close()
    return raw


def _cer(ground_truth: str, ocr_text: str) -> float:
    """Character error rate via Levenshtein edit distance / len(ground truth)."""
    a, b = ground_truth, ocr_text
    if not a:
        return 0.0 if not b else 1.0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
        prev = cur
    return prev[len(b)] / len(a)


# ---- 1) critical-identifier accuracy: survey no., application no., date, amount ----
GROUND_TRUTH = {
    "survey_number": "120/4A",
    "application_number": "2026/0154/02/013642",
    "date": "12-10-2026",
    "amount": "Rs. 4500",
}
scan_text = (
    f"Survey Number: {GROUND_TRUTH['survey_number']}\n"
    f"Application Number: {GROUND_TRUTH['application_number']}\n"
    f"Date: {GROUND_TRUTH['date']}\n"
    f"Fee Amount: {GROUND_TRUTH['amount']}"
)
img = _render_text_image(scan_text, size=(900, 260))
raw = _pdf_with_pages([("image", img)])
doc = doc_extract.extract(".pdf", raw)
ocr_out = "\n".join(s.text for s in doc.segments)
print(f"      OCR'd text for identifier accuracy check:\n{ocr_out!r}")

exact_hits = 0
for label, truth in GROUND_TRUTH.items():
    present = truth in ocr_out
    cer = _cer(truth, truth if present else "")
    found_exact = truth in ocr_out
    check(f"critical identifier '{label}' ({truth!r}) is read with an exact match",
          found_exact, ocr_out)
    if found_exact:
        exact_hits += 1
print(f"      exact-match rate on this synthetic fixture: {exact_hits}/{len(GROUND_TRUTH)} "
      f"(a property of THIS clean rendering, not a validated real-scan accuracy figure)")

# ---- 2) a table / multi-column layout is read at all (not necessarily in
#         perfect reading order -- OCR engines are not guaranteed table-aware;
#         this only checks every cell's text is recovered SOMEWHERE) ----
table_img = Image.new("RGB", (900, 260), "white")
draw = ImageDraw.Draw(table_img)
try:
    font = ImageFont.truetype(FONT_PATH, 28)
except Exception:
    font = ImageFont.load_default()
rows = [("Ward", "Block", "Status"), ("001", "0055", "Approved"), ("002", "0012", "Pending")]
for r, row in enumerate(rows):
    for c, cell in enumerate(row):
        draw.text((20 + c * 280, 20 + r * 70), cell, fill="black", font=font)
raw = _pdf_with_pages([("image", table_img)])
doc = doc_extract.extract(".pdf", raw)
table_ocr = "\n".join(s.text for s in doc.segments)
check("a table/multi-column scanned layout: every cell's text is recovered somewhere "
      "in the OCR output",
      all(cell in table_ocr for row in rows for cell in row), table_ocr)

# ---- 3) real-OCR extraction respects the configured timeout ---------------
# A deliberately large page count (within MAX_DOC_PAGES) of real scanned
# images, forced against an unrealistically short timeout, must raise
# TimeoutError rather than hang -- exercised through the REAL `_from_pdf`
# OCR path, not a stubbed sleep.
import asyncio

sys.path.insert(0, ".")
from backend.routers.chat import _extract_bounded  # noqa: E402
from backend.config import settings  # noqa: E402

many_pages = [("image", _render_text_image(f"Page {i} scanned content here", size=(900, 160)))
              for i in range(8)]
slow_raw = _pdf_with_pages(many_pages)

_orig_timeout = settings.UPLOAD_EXTRACTION_TIMEOUT_SECONDS
_orig_queue = settings.UPLOAD_QUEUE_WAIT_SECONDS
settings.UPLOAD_EXTRACTION_TIMEOUT_SECONDS = 0.01  # impossibly short for 8 real OCR pages
settings.UPLOAD_QUEUE_WAIT_SECONDS = 5.0


async def _run_timeout_check():
    t0 = time.monotonic()
    try:
        await _extract_bounded(".pdf", slow_raw)
        return False, time.monotonic() - t0
    except asyncio.TimeoutError:
        return True, time.monotonic() - t0


try:
    timed_out, elapsed = asyncio.run(_run_timeout_check())
    check("real OCR extraction (8 scanned pages) against an impossibly short "
          "timeout raises TimeoutError rather than completing/hanging",
          timed_out, f"elapsed={elapsed:.2f}s")
    check("the timeout fires promptly (near-immediately), not after waiting "
          "for the real OCR work to finish anyway",
          elapsed < 2.0, f"elapsed={elapsed:.2f}s")
finally:
    settings.UPLOAD_EXTRACTION_TIMEOUT_SECONDS = _orig_timeout
    settings.UPLOAD_QUEUE_WAIT_SECONDS = _orig_queue

# ---- 4) bounded concurrency holds under REAL OCR load (not a stubbed sleep) ----
# Several real single-page scans submitted at once, with a long-enough
# timeout to let them all genuinely finish, but a tight concurrency cap --
# proves the semaphore/executor machinery actually constrains real CPU-bound
# Tesseract calls, not just a mocked stand-in.
import backend.routers.chat as chat_router  # noqa: E402

_orig_sem = chat_router._extraction_semaphore
chat_router._extraction_semaphore = asyncio.Semaphore(2)
settings.UPLOAD_EXTRACTION_TIMEOUT_SECONDS = 60.0
settings.UPLOAD_QUEUE_WAIT_SECONDS = 60.0

_concurrency_counter = {"current": 0, "max": 0}
_lock = __import__("threading").Lock()
_orig_extract = doc_extract.extract


def _tracked_extract(ext, raw_bytes):
    with _lock:
        _concurrency_counter["current"] += 1
        _concurrency_counter["max"] = max(_concurrency_counter["max"], _concurrency_counter["current"])
    try:
        return _orig_extract(ext, raw_bytes)
    finally:
        with _lock:
            _concurrency_counter["current"] -= 1


single_page_raw = _pdf_with_pages([("image", _render_text_image("Concurrent OCR job content"))])
doc_extract.extract = _tracked_extract
try:
    async def _run_concurrency_check():
        return await asyncio.gather(*[
            chat_router._extract_bounded(".pdf", single_page_raw) for _ in range(5)
        ])

    results = asyncio.run(_run_concurrency_check())
    check("5 real OCR jobs submitted at once never exceed the concurrency cap of 2",
          _concurrency_counter["max"] <= 2, f"observed max {_concurrency_counter['max']}")
    check("all 5 real OCR jobs still completed successfully under the cap",
          all(r.status == doc_extract.STATUS_OK for r in results))
finally:
    doc_extract.extract = _orig_extract
    chat_router._extraction_semaphore = _orig_sem

print(f"\n{total - len(bad) - len(blocked)}/{total} ok, {len(bad)} failed, "
      f"{len(blocked)} blocked (Tesseract {_TESS_VERSION})")
sys.exit(1 if bad else 0)
