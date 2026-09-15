"""\"What is my ward / block / taluk / district?\"

Each of these asks for ONE value, not the whole jurisdiction card. Three things
have to line up: parse_intent must send it to jurisdiction_summary (the words
"ward" and "block" are also application-record field names and used to steal
it into application_status), detect_jurisdiction_focus must name the level, and
build_html_response must answer with that value alone.

Pure string work — no DB, no Ollama. langchain_ollama / pydantic-settings are
stubbed so the parser can be imported from a bare interpreter.
"""
import sys as _sys
if hasattr(_sys.stdout, "reconfigure"):
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    _sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import re
import sys
import types

sys.path.insert(0, ".")


def _stub_imports():
    def stub(name, attrs=()):
        m = types.ModuleType(name)
        for a in attrs:
            setattr(m, a, type(a, (), {"__init__": lambda self, *a, **k: None}))
        sys.modules[name] = m
        return m

    stub("langchain_ollama", ["ChatOllama"])
    st = stub("structlog")
    st.get_logger = lambda *a, **k: types.SimpleNamespace(
        info=lambda *a, **k: None, warning=lambda *a, **k: None,
        error=lambda *a, **k: None, debug=lambda *a, **k: None)
    st.configure = lambda *a, **k: None
    st.stdlib = types.SimpleNamespace(
        LoggerFactory=lambda *a, **k: None, BoundLogger=object,
        add_log_level=None, PositionalArgumentsFormatter=lambda *a, **k: None,
        filter_by_level=None)
    st.processors = types.SimpleNamespace(
        TimeStamper=lambda *a, **k: None, StackInfoRenderer=lambda *a, **k: None,
        format_exc_info=None, UnicodeDecoder=lambda *a, **k: None,
        JSONRenderer=lambda *a, **k: None, KeyValueRenderer=lambda *a, **k: None,
        add_log_level=None)
    st.dev = types.SimpleNamespace(ConsoleRenderer=lambda *a, **k: None)
    stub("backend.services.pgvector_store").similarity_search = lambda *a, **k: []

    cfg_src = open("backend/config.py", encoding="utf-8").read()
    maps = {}
    for name in ("DISTRICT_CODE_MAP", "DISTRICT_NAME_MAP"):
        m = re.search(rf"^{name}\s*=\s*\{{.*?^\}}", cfg_src, re.S | re.M)
        if m:
            exec(m.group(0), maps)

    class _S:
        def __getattr__(self, k):
            return {"OLLAMA_BASE_URL": "http://localhost:11434",
                    "LLM_MODEL": "llama3.1:8b",
                    "EMBEDDING_MODEL": "nomic-embed-text",
                    "ENVIRONMENT": "test"}.get(k, 0.0)

    cfg = stub("backend.config")
    cfg.settings = _S()
    cfg.DISTRICT_CODE_MAP = maps.get("DISTRICT_CODE_MAP", {})
    cfg.DISTRICT_NAME_MAP = maps.get("DISTRICT_NAME_MAP", {})


try:
    from backend.services.rag import (parse_intent, detect_jurisdiction_focus,
                                      build_html_response)
except ModuleNotFoundError:
    _stub_imports()
    from backend.services.rag import (parse_intent, detect_jurisdiction_focus,
                                      build_html_response)

failures = []


def check(name, got, want):
    ok = got == want
    if not ok:
        failures.append(f"{name}\n    expected: {want!r}\n    got:      {got!r}")
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")


# ── 1. Routing + focus, English ──────────────────────────────────────────────
print("\n[1] English — routes to jurisdiction_summary with the right focus")
ENGLISH = [
    ("what is block i cover", "block"),
    ("what block i cover", "block"),
    ("what blocks do i cover", "block"),
    ("which blocks do i cover", "block"),
    ("blocks i cover", "block"),
    ("my block", "block"),
    ("what is my block", "block"),
    ("what is my taluk", "taluk"),
    ("which taluk am i in", "taluk"),
    ("what taluk do i work in", "taluk"),
    ("what is my district", "district"),
    ("which district am i in", "district"),
    ("what district do i cover", "district"),
    ("what is my ward", "ward"),
    ("which ward do i cover", "ward"),
    ("i cover which ward", "ward"),
    ("what is my town", "town"),
    # The whole card, not one level
    ("what is my jurisdiction", None),
    ("what is my area", None),
]
for q, focus in ENGLISH:
    check(f"intent  {q!r}", parse_intent(q), "jurisdiction_summary")
    check(f"focus   {q!r}", detect_jurisdiction_focus(q), focus)

# ── 2. Routing + focus, Tamil and Tanglish ───────────────────────────────────
print("\n[2] Tamil / Tanglish — these fell through to the raw LLM")
TAMIL = [
    ("என் மாவட்டம் என்ன", "district"),
    ("எனது மாவட்டம்", "district"),
    ("என் தாலுகா என்ன", "taluk"),
    ("என் வார்டு என்ன", "ward"),
    ("எனது வார்டு எது", "ward"),
    ("என் பிளாக் என்ன", "block"),
    ("en district enna", "district"),
    ("en taluk enna", "taluk"),
    ("en ward enna", "ward"),
    ("ennoda ward", "ward"),
    ("naan enna ward", "ward"),
    ("my ward enna", "ward"),
    ("என் அதிகார வரம்பு", None),
]
for q, focus in TAMIL:
    check(f"intent  {q!r}", parse_intent(q), "jurisdiction_summary")
    check(f"focus   {q!r}", detect_jurisdiction_focus(q), focus)

# ── 3. Not stolen from the intents that own them ─────────────────────────────
print("\n[3] Work-item questions keep their own intent")
NEGATIVE = [
    ("show my pending applications", "pending_applications"),
    ("my pending applications in ward 002", "pending_applications"),
    ("list applications in my ward", "pending_applications"),
    ("என் நிலுவை விண்ணப்பங்கள்", "pending_applications"),
    ("en pending applications", "pending_applications"),
    ("what is the ward for 2026/0154/28/001167", "application_status"),
    ("show survey numbers in my ward", "survey_detail"),
]
for q, want in NEGATIVE:
    check(f"intent  {q!r}", parse_intent(q), want)

# ── 4. Rendering — one value, not the whole card ─────────────────────────────
print("\n[4] Rendering — the focused answer names just that level")


def _sd(focus, blocks=(("0015",),)):
    return {"query_type": "Jurisdiction Summary", "jurisdiction": {
        "district": {"name": "Thoothukudi", "code": "28"},
        "taluk": {"name": "Thoothukudi"},
        "towns": [{"name": "Thoothukudi", "wards": [
            {"ward_number": "002",
             "blocks": [{"block_number": b} for b in blocks[0]]}]}],
        "survey_count": 90, "active_applications": 3, "focus": focus}}


check("block", build_html_response(_sd("block"), "en"),
      "<div class='table-intro'>Your block is <strong>0015</strong>.</div>")
check("taluk", build_html_response(_sd("taluk"), "en"),
      "<div class='table-intro'>Your taluk is <strong>Thoothukudi</strong>.</div>")
check("district", build_html_response(_sd("district"), "en"),
      "<div class='table-intro'>Your district is <strong>Thoothukudi (28)</strong>.</div>")
check("ward", build_html_response(_sd("ward"), "en"),
      "<div class='table-intro'>Your ward is <strong>002</strong>.</div>")
check("two blocks pluralise", build_html_response(_sd("block", (("0015", "0016"),)), "en"),
      "<div class='table-intro'>Your blocks are <strong>0015, 0016</strong>.</div>")
check("nothing assigned", build_html_response(_sd("block", ((),)), "en"),
      "<div class='table-intro'>No block is assigned to you.</div>")
check("tamil block", build_html_response(_sd("block"), "ta"),
      "<div class='table-intro'>உங்கள் தொகுதி: <strong>0015</strong></div>")
check("no focus -> full card",
      "<table class='data-table'>" in build_html_response(_sd(None), "en"), True)

print("\n" + "=" * 70)
if failures:
    print(f"{len(failures)} FAILURE(S):\n")
    for f in failures:
        print("  - " + f)
    sys.exit(1)
print("All jurisdiction-question checks passed.")
