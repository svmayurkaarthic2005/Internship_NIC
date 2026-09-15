"""Format follow-ups: "in table", "as bullets", "in short", "as json", ...

A bare format request names no subject, so it must be answered from the answer
already on screen, not sent to the LLM. This suite checks three things:

  1. Routing  — which messages are format follow-ups and which stay real queries
  2. Parsing  — the three shapes a previous answer arrives in
  3. Rendering — every format, over field-pairs, multi-column listings and
                 unusable prose

No DB, no Ollama: the handler is pure string work, so the suite runs instantly.
Loaded by source so it does not drag in the whole chatbot import chain.
"""
import ast
import re
import sys
from typing import Optional

# These suites print Tamil. On Windows the console is cp1252 and the
# first Tamil character raises UnicodeEncodeError, which killed the run
# before any result was reported. Same guard the other suites carry.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

SRC = "backend/services/chatbot.py"
START = "# ── Format follow-ups"
END = '# ── Bare date-scope follow-ups ("last month")'


def _load():
    src = open(SRC, encoding="utf-8").read()
    block = src[src.index(START):src.index(END)]
    ns = {"re": re, "Optional": Optional}
    exec(compile(block, SRC, "exec"), ns)
    return ns


M = _load()

DETAIL_BULLETS = (
    "Details for application 2026/0154/28/001167:\n"
    "• **Application Type**: ISD\n"
    "• **Status**: pending"
)
SINGLE_FIELD = "The Application Type for 2026/0154/28/001167 is: ISD"
HTML_LISTING = (
    "<div class='table-intro'>Found 2 application(s):</div>"
    "<table class='data-table'><thead><tr><th>Application No.</th><th>Type</th>"
    "<th>Status</th></tr></thead><tbody>"
    "<tr><td>2026/0154/28/001167</td><td>ISD</td><td>Pending</td></tr>"
    "<tr><td>2026/0153/28/001854</td><td>NISD</td><td>Pending</td></tr>"
    "</tbody></table>"
)
PROSE = "I'd be happy to help you with your question about survey applications."

failures = []


def check(name, got, want):
    ok = got == want
    if not ok:
        failures.append(f"{name}\n    expected: {want!r}\n    got:      {got!r}")
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")


def check_in(name, needles, got):
    missing = [n for n in needles if n not in got]
    if missing:
        failures.append(f"{name}\n    missing: {missing}\n    got: {got!r}")
    print(f"  {'PASS' if not missing else 'FAIL'}  {name}")


def hist(*contents):
    return [{"role": "assistant", "content": c} for c in contents]


# ── 1. Routing ───────────────────────────────────────────────────────────────
print("\n[1] Routing — format follow-up vs. real query")
ROUTING = [
    # bare format follow-ups
    ("in table", "table"), ("in a table", "table"), ("as table", "table"),
    ("table", "table"), ("table format", "table"), ("show it in table", "table"),
    ("put it in tabular form", "table"), ("அட்டவணையாக", "table"),
    ("table la podu", "table"),
    ("in bullets", "bullets"), ("as bullet points", "bullets"),
    ("in points", "bullets"), ("point wise", "bullets"),
    ("give me as a list", "bullets"), ("பட்டியலாக", "bullets"),
    ("numbered list", "numbered"), ("as numbered", "numbered"),
    ("step by step", "numbered"),
    ("in a paragraph", "prose"), ("as a sentence", "prose"),
    ("in plain text", "prose"), ("one line", "prose"),
    ("in short", "short"), ("briefly", "short"), ("summarize", "short"),
    ("சுருக்கமாக", "short"),
    ("as json", "json"), ("json format", "json"), ("key value", "json"),
    # real queries that merely contain a format word — must NOT be intercepted
    ("pending applications in a table", None),
    ("list pending applications", None),
    ("how many isd in table for last month", None),
    ("show me the short applications in ward 002", None),
    ("what is the status", None),
    ("2026/0154/28/001167 show only application type n status", None),
    ("give me the list of overdue applications in my ward", None),
    ("", None),
]
for msg, want in ROUTING:
    check(f'_detect_reformat_request({msg!r})', M["_detect_reformat_request"](msg), want)

# ── 2. Parsing ───────────────────────────────────────────────────────────────
print("\n[2] Parsing — the shapes a previous answer arrives in")
p = M["_parse_previous_answer"](DETAIL_BULLETS)
check("bullets -> caption", p["caption"], "Details for application 2026/0154/28/001167")
check("bullets -> rows", p["rows"], [["Application Type", "ISD"], ["Status", "pending"]])

p = M["_parse_previous_answer"](SINGLE_FIELD)
check("single field -> rows", p["rows"], [["Application Type", "ISD"]])

p = M["_parse_previous_answer"](HTML_LISTING)
check("html -> caption", p["caption"], "Found 2 application(s)")
check("html -> headers", p["headers"], ["Application No.", "Type", "Status"])
check("html -> rows", p["rows"],
      [["2026/0154/28/001167", "ISD", "Pending"],
       ["2026/0153/28/001854", "NISD", "Pending"]])

check("prose -> unusable", M["_parse_previous_answer"](PROSE)["rows"], [])
check("empty -> unusable", M["_parse_previous_answer"]("")["rows"], [])

# ── 3. Rendering, per format ─────────────────────────────────────────────────
print("\n[3] Rendering — field pairs")
r = M["_reformat_previous_answer"]
check_in("pairs -> table", ["<table class='data-table'>", "<th>Field</th>",
                           "<strong>Application Type</strong>", "<td>ISD</td>",
                           "<td>pending</td>", "table-intro"],
         r(hist(DETAIL_BULLETS), "table", "en"))
check("pairs -> bullets", r(hist(DETAIL_BULLETS), "bullets", "en"),
      "Details for application 2026/0154/28/001167:\n"
      "• **Application Type**: ISD\n• **Status**: pending")
check("pairs -> numbered", r(hist(DETAIL_BULLETS), "numbered", "en"),
      "Details for application 2026/0154/28/001167:\n"
      "1. **Application Type**: ISD\n2. **Status**: pending")
check("pairs -> short", r(hist(DETAIL_BULLETS), "short", "en"),
      "Details for application 2026/0154/28/001167 — Application Type: ISD; Status: pending.")
check("pairs -> prose", r(hist(DETAIL_BULLETS), "prose", "en"),
      "Details for application 2026/0154/28/001167: application type is ISD "
      "and status is pending.")
check_in("pairs -> json", ['```json', '"application_type": "ISD"', '"status": "pending"'],
         r(hist(DETAIL_BULLETS), "json", "en"))

print("\n[4] Rendering — a multi-column listing (HTML table already on screen)")
check_in("listing -> table keeps columns",
         ["<th>Application No.</th>", "<th>Type</th>", "<th>Status</th>",
          "<td>NISD</td>"], r(hist(HTML_LISTING), "table", "en"))
check("listing -> bullets uses headers", r(hist(HTML_LISTING), "bullets", "en"),
      "Found 2 application(s):\n"
      "• Application No.: 2026/0154/28/001167, Type: ISD, Status: Pending\n"
      "• Application No.: 2026/0153/28/001854, Type: NISD, Status: Pending")
check_in("listing -> json is a row array",
         ['"Application No.": "2026/0154/28/001167"', '"Type": "NISD"'],
         r(hist(HTML_LISTING), "json", "en"))

print("\n[5] Single-field answer, and the unusable cases")
check("single field -> table", "<td>ISD</td>" in r(hist(SINGLE_FIELD), "table", "en"), True)
check("prose previous -> cannot reshape", r(hist(PROSE), "table", "en"), "")
check("no history -> cannot reshape", r([], "table", "en"), "")
check_in("cannot-reformat wording (had previous)",
         ["can't reshape my last reply into a table"],
         M["_cannot_reformat_reply"]("table", "en", True))
check_in("cannot-reformat wording (no previous)",
         ["no previous answer to show as a table"],
         M["_cannot_reformat_reply"]("table", "en", False))
check("ambiguous formats fall through", sorted(M["_AMBIGUOUS_REFORMATS"]),
      ["bullets", "short"])

print("\n[6] Tamil / Tanglish rendering")
check_in("ta -> table headers", ["விவரம்", "மதிப்பு"],
         r(hist(DETAIL_BULLETS), "table", "ta"))
check_in("ta -> cannot reformat", ["அட்டவணையாக"],
         M["_cannot_reformat_reply"]("table", "ta", True))

print("\n[7] Call sites are wired in both chat paths")
whole = open(SRC, encoding="utf-8").read()
check("wired twice", whole.count("_detect_reformat_request(message)"), 2)
check("module parses", bool(ast.parse(whole)), True)

print("\n" + "=" * 70)
if failures:
    print(f"{len(failures)} FAILURE(S):\n")
    for f in failures:
        print("  - " + f)
    sys.exit(1)
print("All format follow-up checks passed.")
