"""Benchmark: EasyOCR vs Tesseract on representative SIS-style pages.

Plugs into the SAME abstraction backend/ingest.py uses for its scanned-page
OCR fallback -- this script does not reimplement OCR calling, it only sets
`backend.ingest.OCR_ENGINE` and times/measures the existing `_ocr_image()`
call. The ingestion pipeline itself is untouched by this file.

Fixtures (five categories, generated on first run into --fixtures-dir and
reused after): clean Tamil (wording pulled verbatim from this project's own
backend/documents/faq_tamil.txt -- no external/copyrighted text), clean
English, a Tamil+English SIS application header (the realistic case -- most
pages this chatbot ingests mix both scripts), a parcel-register-style table,
and a degraded/low-resolution+noisy render of the mixed page. Each fixture
carries its own known ground truth (it is the text we asked PIL to draw), so
CER/WER below are real measured numbers, not placeholders.

Rendered with Windows' bundled Nirmala UI font (Tamil + Latin in one face) --
no font file is bundled or downloaded. These are SYNTHETIC renders: no scan
noise, skew, staple marks, stamps or handwriting a real scanned government
document carries. Drop real scanned SIS/government PDFs or images into
--fixtures-dir (each with an optional matching <name>.txt ground truth file)
to benchmark on the real thing -- that result is the one worth trusting, not
this proxy.

Each engine runs its whole fixture pass in its own subprocess, so EasyOCR's
PyTorch allocations (which never shrink back down) cannot pollute Tesseract's
memory reading, and a crash in one engine does not take down the other's run.

Usage:
    python benchmark_ocr_engines.py
    python benchmark_ocr_engines.py --engines easyocr
    python benchmark_ocr_engines.py --fixtures-dir path/to/real_scans
    python benchmark_ocr_engines.py --no-warmup
"""
from __future__ import annotations

import argparse
import io
import multiprocessing
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent))

FIXTURES_DEFAULT_DIR = Path(__file__).parent / "ocr_fixtures"
REPORT_PATH = Path(__file__).parent / "ocr_benchmark_report.md"

# ── Candidate fonts (Tamil + Latin in one face preferred) ──────────────────
_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\Nirmala.ttc",               # Windows 8+ (pan-Indic UI font)
    r"C:\Windows\Fonts\latha.ttf",                  # older Windows Tamil font (no Latin)
    "/usr/share/fonts/truetype/noto/NotoSansTamil-Regular.ttf",
    "/usr/share/fonts/truetype/lohit-tamil/Lohit-Tamil.ttf",
]


def _find_font() -> Optional[str]:
    for path in _FONT_CANDIDATES:
        if Path(path).exists():
            return path
    return None


# ── Ground-truth fixtures ───────────────────────────────────────────────────
# Tamil wording is lifted verbatim from backend/documents/faq_tamil.txt (this
# project's own corpus) rather than invented, so it is grammatically real.
CLEAN_TAMIL = (
    "நில எல்லைத் தொடர்ச்சி, பரப்பளவுக் கணக்கீடு மற்றும் அண்டை\n"
    "நிலங்களுடனான இணக்கம் உள்ளிட்ட அனைத்து சரிபார்ப்புகளும்\n"
    "மேற்கொள்ளப்படும். குறைபாடுகள் ஏதும் இல்லையெனில் இணைப்பு\n"
    "செயல்முறை தொடர அனுமதிக்கப்படும்."
)

CLEAN_ENGLISH = (
    "The Sub Inspector Surveyor verifies survey boundaries, confirms\n"
    "ownership documents, and forwards the application to the Senior\n"
    "Draughtsman for sub-division sketch preparation before the file\n"
    "moves to the Zonal Level Tahsildar for final approval."
)

MIXED = (
    "Application Number: 2025/0154/28/000042\n"
    "விண்ணப்ப வகை: ISD (Involving Sub-Division)\n"
    "Survey Number: 1345/2A      Ward: 102\n"
    "நிலையான நிலை: Pending - SIS அலுவலகத்தில்\n"
    "பரிசீலனையில் உள்ளது."
)

_TABLE_HEADERS = ["Survey No", "Patta No", "Owner", "Area (sq.m)"]
_TABLE_ROWS = [
    ["1345/2A", "4521", "ரமேஷ் குமார்", "812.5"],
    ["1345/2B", "4522", "லட்சுமி தேவி", "640.0"],
    ["102/5", "1187", "முத்துலட்சுமி", "915.2"],
]
# Ground truth for a scanned table is plain reading-order text -- a scan has
# no PDF table object for PyMuPDF's native find_tables() to see (that path
# only helps a text-layer PDF); the OCR fallback just reads pixels. This is
# exactly what that path looks like.
TABLE_TEXT = "\n".join(
    [" ".join(_TABLE_HEADERS)] + [" ".join(row) for row in _TABLE_ROWS]
)


def _wrap_lines(text: str) -> List[str]:
    return text.split("\n")


def _render_page(lines: List[str], font_path: Optional[str],
                  size=(1240, 900), font_size: int = 34) -> "Any":
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("L", size, color=255)
    draw = ImageDraw.Draw(img)
    font = (ImageFont.truetype(font_path, font_size, index=0)
            if font_path else ImageFont.load_default())
    y = 60
    for line in lines:
        draw.text((60, y), line, fill=0, font=font)
        y += int(font_size * 1.6)
    return img


def _render_table(headers: List[str], rows: List[List[str]],
                   font_path: Optional[str], size=(1240, 500),
                   font_size: int = 30) -> "Any":
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("L", size, color=255)
    draw = ImageDraw.Draw(img)
    font = (ImageFont.truetype(font_path, font_size, index=0)
            if font_path else ImageFont.load_default())
    col_x = [60, 330, 560, 850]
    row_h = int(font_size * 1.9)
    y = 50
    for col, text in zip(col_x, headers):
        draw.text((col, y), text, fill=0, font=font)
    draw.line((40, y + row_h - 10, size[0] - 40, y + row_h - 10), fill=0, width=2)
    y += row_h
    for row in rows:
        for col, text in zip(col_x, row):
            draw.text((col, y), text, fill=0, font=font)
        y += row_h
    return img


def _degrade(img: "Any") -> "Any":
    """Simulate a poor scan: downsample, blur, JPEG-recompress at low quality,
    then a slight rotation -- without pulling in numpy for synthetic noise."""
    from PIL import Image, ImageFilter
    w, h = img.size
    small = img.resize((w // 3, h // 3), Image.BILINEAR)
    img = small.resize((w, h), Image.BILINEAR)
    img = img.filter(ImageFilter.GaussianBlur(radius=1.1))
    buf = io.BytesIO()
    img.convert("L").save(buf, format="JPEG", quality=25)
    buf.seek(0)
    img = Image.open(buf).convert("L")
    img = img.rotate(1.6, expand=True, fillcolor=255)
    return img


def build_or_load_fixtures(fixtures_dir: Path) -> List[Dict[str, Any]]:
    """Each fixture: {"name", "png_bytes", "ground_truth" (optional), "note"}.

    A fixture is (re)generated only if its PNG is missing -- drop your own
    images (plus a same-name .txt) into the directory and they are picked up
    as-is, synthetic ones fill any gap.
    """
    fixtures_dir.mkdir(exist_ok=True)
    font_path = _find_font()
    if font_path is None:
        print("! No Tamil-capable font found (looked for Nirmala.ttc / Latha / "
              "Noto Sans Tamil / Lohit Tamil) -- Tamil fixtures will render as "
              "boxes. Point --fixtures-dir at real scans instead, or install "
              "one of those fonts.")

    specs = [
        ("clean_tamil", lambda: _render_page(_wrap_lines(CLEAN_TAMIL), font_path),
         CLEAN_TAMIL.replace("\n", " ")),
        ("clean_english", lambda: _render_page(_wrap_lines(CLEAN_ENGLISH), font_path),
         CLEAN_ENGLISH.replace("\n", " ")),
        ("mixed_tamil_english", lambda: _render_page(_wrap_lines(MIXED), font_path),
         MIXED.replace("\n", " ")),
        ("table", lambda: _render_table(_TABLE_HEADERS, _TABLE_ROWS, font_path),
         TABLE_TEXT.replace("\n", " ")),
    ]

    fixtures: List[Dict[str, Any]] = []
    mixed_png_path = fixtures_dir / "mixed_tamil_english.png"

    for name, builder, ground_truth in specs:
        png_path = fixtures_dir / f"{name}.png"
        if not png_path.exists():
            img = builder()
            img.convert("L").save(png_path)
        gt_path = fixtures_dir / f"{name}.txt"
        if not gt_path.exists():
            gt_path.write_text(ground_truth, encoding="utf-8")
        fixtures.append({
            "name": name,
            "png_bytes": png_path.read_bytes(),
            "ground_truth": gt_path.read_text(encoding="utf-8"),
        })

    # Degraded fixture is derived from the mixed page, same ground truth.
    degraded_path = fixtures_dir / "degraded_low_res.png"
    if not degraded_path.exists():
        from PIL import Image
        base = Image.open(mixed_png_path) if mixed_png_path.exists() else _render_page(
            _wrap_lines(MIXED), font_path)
        _degrade(base).save(degraded_path)
    degraded_gt_path = fixtures_dir / "degraded_low_res.txt"
    if not degraded_gt_path.exists():
        degraded_gt_path.write_text(MIXED.replace("\n", " "), encoding="utf-8")
    fixtures.append({
        "name": "degraded_low_res",
        "png_bytes": degraded_path.read_bytes(),
        "ground_truth": degraded_gt_path.read_text(encoding="utf-8"),
    })

    # Anything else dropped into the directory (real scans) rides along too,
    # ground truth optional -- those get side-by-side output, not CER/WER.
    known = {f["name"] for f in fixtures}
    for png_path in sorted(fixtures_dir.glob("*.png")):
        if png_path.stem in known:
            continue
        gt_path = png_path.with_suffix(".txt")
        fixtures.append({
            "name": png_path.stem,
            "png_bytes": png_path.read_bytes(),
            "ground_truth": gt_path.read_text(encoding="utf-8") if gt_path.exists() else None,
        })

    return fixtures


# ── CER / WER ────────────────────────────────────────────────────────────

def _edit_distance(a: List[str], b: List[str]) -> int:
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            curr[j] = min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost)
        prev = curr
    return prev[-1]


def cer(ref: str, hyp: str) -> Optional[float]:
    ref_chars = list(ref)
    if not ref_chars:
        return None
    return _edit_distance(ref_chars, list(hyp)) / len(ref_chars)


def wer(ref: str, hyp: str) -> Optional[float]:
    ref_words = ref.split()
    if not ref_words:
        return None
    return _edit_distance(ref_words, hyp.split()) / len(ref_words)


# ── Running one engine, isolated in its own process ─────────────────────────

def _engine_worker(engine: str, fixtures: List[Dict[str, Any]], warmup: bool,
                    q: "multiprocessing.Queue") -> None:
    try:
        import psutil
        proc = psutil.Process(os.getpid())
        have_psutil = True
    except ImportError:
        proc = None
        have_psutil = False

    import backend.ingest as ing
    ing.OCR_ENGINE = engine

    results = []
    load_seconds = None
    if warmup and fixtures:
        t0 = time.perf_counter()
        try:
            ing._ocr_image(fixtures[0]["png_bytes"])
        except Exception:
            pass
        load_seconds = time.perf_counter() - t0  # one-time model load + first page

    for fx in fixtures:
        t0 = time.perf_counter()
        err = None
        try:
            text = ing._ocr_image(fx["png_bytes"])
        except Exception as e:  # a bad page must not take the whole run down
            text, err = "", f"{type(e).__name__}: {e}"
        dt = time.perf_counter() - t0
        rss_mb = (proc.memory_info().rss / 1e6) if have_psutil else None
        results.append({
            "fixture": fx["name"], "engine": engine, "text": text,
            "seconds": dt, "rss_mb": rss_mb, "error": err,
            "empty": not text.strip(),
        })
    q.put({"results": results, "load_seconds": load_seconds})


def run_engine(engine: str, fixtures: List[Dict[str, Any]], warmup: bool) -> Dict[str, Any]:
    ctx = multiprocessing.get_context("spawn")
    q = ctx.Queue()
    p = ctx.Process(target=_engine_worker, args=(engine, fixtures, warmup, q))
    p.start()
    try:
        payload = q.get(timeout=1800)
    except Exception:
        payload = None
    p.join()
    if payload is None:
        return {
            "results": [{"fixture": f["name"], "engine": engine, "text": "",
                          "seconds": None, "rss_mb": None,
                          "error": f"subprocess exited {p.exitcode} with no result "
                                   f"(crash, OOM, or missing dependency)",
                          "empty": True} for f in fixtures],
            "load_seconds": None,
        }
    return payload


# ── Reporting ────────────────────────────────────────────────────────────

def _fmt(v, suffix="", digits=3):
    return f"{v:.{digits}f}{suffix}" if isinstance(v, float) else "-"


def report(fixtures: List[Dict[str, Any]], engine_payloads: Dict[str, Dict[str, Any]]) -> None:
    gt_by_name = {f["name"]: f["ground_truth"] for f in fixtures}
    lines_md: List[str] = ["# OCR engine benchmark: EasyOCR vs Tesseract\n"]

    print("\n" + "=" * 78)
    print(f"{'fixture':<22}{'engine':<12}{'CER':>8}{'WER':>8}{'sec':>8}{'RSS MB':>10}  status")
    print("-" * 78)

    per_engine_cer: Dict[str, List[float]] = {}
    per_engine_wer: Dict[str, List[float]] = {}
    per_engine_time: Dict[str, List[float]] = {}
    per_engine_rss: Dict[str, List[float]] = {}
    per_engine_failures: Dict[str, List[str]] = {}

    lines_md.append("| fixture | engine | CER | WER | seconds | RSS (MB) | status |")
    lines_md.append("|---|---|---|---|---|---|---|")

    for engine, payload in engine_payloads.items():
        load_s = payload.get("load_seconds")
        if load_s is not None:
            print(f"  [{engine}] warm-up / model-load call: {load_s:.2f}s "
                  f"(one-time cost, excluded from per-page timings below)")
        for r in payload["results"]:
            ref = gt_by_name.get(r["fixture"])
            c = cer(ref, r["text"]) if ref is not None else None
            w = wer(ref, r["text"]) if ref is not None else None
            status = "OK"
            if r["error"]:
                status = f"FAIL: {r['error']}"
                per_engine_failures.setdefault(engine, []).append(r["fixture"])
            elif r["empty"]:
                status = "FAIL: empty output"
                per_engine_failures.setdefault(engine, []).append(r["fixture"])

            print(f"{r['fixture']:<22}{engine:<12}{_fmt(c):>8}{_fmt(w):>8}"
                  f"{_fmt(r['seconds'], digits=2):>8}{_fmt(r['rss_mb'], digits=1):>10}  {status}")
            lines_md.append(
                f"| {r['fixture']} | {engine} | {_fmt(c)} | {_fmt(w)} | "
                f"{_fmt(r['seconds'], digits=2)} | {_fmt(r['rss_mb'], digits=1)} | {status} |")

            if c is not None and not r["error"]:
                per_engine_cer.setdefault(engine, []).append(c)
            if w is not None and not r["error"]:
                per_engine_wer.setdefault(engine, []).append(w)
            if r["seconds"] is not None:
                per_engine_time.setdefault(engine, []).append(r["seconds"])
            if r["rss_mb"] is not None:
                per_engine_rss.setdefault(engine, []).append(r["rss_mb"])

    print("=" * 78)
    lines_md.append("\n## Summary\n")
    lines_md.append("| engine | avg CER | avg WER | avg sec/page | peak RSS (MB) | failures |")
    lines_md.append("|---|---|---|---|---|---|")

    summary: Dict[str, Dict[str, Any]] = {}
    for engine in engine_payloads:
        cers = per_engine_cer.get(engine, [])
        wers = per_engine_wer.get(engine, [])
        times = per_engine_time.get(engine, [])
        rss = per_engine_rss.get(engine, [])
        fails = per_engine_failures.get(engine, [])
        avg_cer = sum(cers) / len(cers) if cers else None
        avg_wer = sum(wers) / len(wers) if wers else None
        avg_time = sum(times) / len(times) if times else None
        peak_rss = max(rss) if rss else None
        summary[engine] = {"avg_cer": avg_cer, "avg_wer": avg_wer,
                            "avg_time": avg_time, "peak_rss": peak_rss,
                            "failures": fails}
        print(f"{engine:<12} avg CER={_fmt(avg_cer)}  avg WER={_fmt(avg_wer)}  "
              f"avg sec/page={_fmt(avg_time, digits=2)}  peak RSS={_fmt(peak_rss, digits=1)} MB  "
              f"failures={fails or 'none'}")
        lines_md.append(f"| {engine} | {_fmt(avg_cer)} | {_fmt(avg_wer)} | "
                         f"{_fmt(avg_time, digits=2)} | {_fmt(peak_rss, digits=1)} | "
                         f"{', '.join(fails) if fails else 'none'} |")

    # Recommendation, computed from what actually ran -- never hardcoded.
    ranked = [e for e in summary if summary[e]["avg_wer"] is not None]
    verdict = "Inconclusive -- not enough fixtures produced a usable CER/WER."
    if len(ranked) >= 1:
        ranked.sort(key=lambda e: (len(summary[e]["failures"]), summary[e]["avg_wer"]))
        best = ranked[0]
        if len(ranked) > 1:
            other = ranked[1]
            wer_gap = (summary[other]["avg_wer"] or 0) - (summary[best]["avg_wer"] or 0)
            verdict = (
                f"**{best}** had the lower word error rate "
                f"({_fmt(summary[best]['avg_wer'])} vs {_fmt(summary[other]['avg_wer'])} "
                f"for {other}, {len(summary[best]['failures'])} vs "
                f"{len(summary[other]['failures'])} failures) on this fixture set."
            )
        else:
            verdict = f"Only **{best}** produced usable results on this fixture set."
    print("\n" + verdict)
    lines_md.append(f"\n**Verdict on this fixture set:** {verdict}\n")
    lines_md.append(
        "\nThis verdict is scoped to the fixtures actually run. Synthetic "
        "renders are a proxy for real scans (no noise, skew, stamps or "
        "handwriting) -- re-run with real scanned SIS/government PDFs in "
        "`--fixtures-dir` before treating this as final.\n"
    )

    REPORT_PATH.write_text("\n".join(lines_md), encoding="utf-8")
    print(f"\nFull report (incl. this table): {REPORT_PATH}")

    # Side-by-side raw text for manual read, every fixture, every engine.
    text_dump = fixtures_dir_dump(engine_payloads, gt_by_name)
    dump_path = Path(__file__).parent / "ocr_benchmark_outputs.txt"
    dump_path.write_text(text_dump, encoding="utf-8")
    print(f"Side-by-side raw OCR text (for manual read): {dump_path}")


def fixtures_dir_dump(engine_payloads, gt_by_name) -> str:
    out = []
    names = sorted({r["fixture"] for p in engine_payloads.values() for r in p["results"]})
    for name in names:
        out.append(f"\n{'#' * 70}\n# {name}")
        if gt_by_name.get(name):
            out.append(f"\n-- ground truth --\n{gt_by_name[name]}")
        for engine, payload in engine_payloads.items():
            r = next((r for r in payload["results"] if r["fixture"] == name), None)
            if r:
                out.append(f"\n-- {engine} output --\n{r['text'] or '(empty)'}")
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fixtures-dir", default=str(FIXTURES_DEFAULT_DIR))
    ap.add_argument("--engines", default="easyocr,tesseract",
                     help="comma-separated: easyocr,tesseract")
    ap.add_argument("--no-warmup", action="store_true",
                     help="skip the warm-up call (model-load cost then mixes into page 1's timing)")
    args = ap.parse_args()

    fixtures_dir = Path(args.fixtures_dir)
    fixtures = build_or_load_fixtures(fixtures_dir)
    print(f"{len(fixtures)} fixture(s) in {fixtures_dir}")

    engines = [e.strip() for e in args.engines.split(",") if e.strip()]
    engine_payloads = {}
    for engine in engines:
        print(f"\nRunning {engine} over {len(fixtures)} fixture(s) in an isolated process...")
        engine_payloads[engine] = run_engine(engine, fixtures, warmup=not args.no_warmup)

    report(fixtures, engine_payloads)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
