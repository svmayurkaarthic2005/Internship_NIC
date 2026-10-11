"""Regression suite for SIS Chatbot: Output Structure requests (the brief's Section A).

Covers three defects found while probing "Show my pending ISD applications, including
application number, service code, submission date and status." followed by format
follow-ups:

1. **Population-widening on a column-listed first message (FIXED).** The seed question
   itself -- an ordinary listing whose sentence names its own columns ("...including
   application number, service code, submission date and status.") -- was split by
   `_big_plan()` into FOUR separate unscoped questions ("my pending ISD applications",
   "including application number", "service code", "submission date and status"), each
   answered independently over the officer's WHOLE jurisdiction instead of staying one
   scoped 8-row ISD listing. Every later "add/remove a column" follow-up then operated on
   that wrong, widened population (8 -> 17, even 44, application numbers across answers)
   -- the exact violation the brief's "formatting alone must never change the underlying
   records, counts, status filters, jurisdiction, service-code scope" rule forbids.
   Root cause: `_BARE_COLUMN_NOUN_RE` (the allow-list that stops a column-listed sentence
   from being split) was missing "service code", couldn't match a trailing "A and B"
   conjunction ("submission date and status"), and the list lead-in word "including" was
   never stripped from a split field, so "including application number" failed the
   allow-list check too. Fixed: `_BARE_COLUMN_NOUN_RE` gained "service code"/"channel";
   a new `_is_bare_column_field()` also accepts a plain "A and B" of two allow-listed
   nouns; "including"/"include" joined the existing lead-in-word strip list.

2. **"Restore the original format" fell to the LLM/agent fallback (FIXED).**
   `followup_context._COLUMN_RESET_RE` already recognised "show the full table again" /
   "reset the columns" but required the literal word "table", not "format", and had no
   "restore" verb at all. Fixed: the regex now also matches "restore ... (original/
   previous/default) format/table/view/columns", narrow enough that "restore the
   application to pending" (a mutation request) still does NOT match.

3. **A pure JSON/CSV/Markdown-table-only format instruction produced LLM garbage or a
   timeout (FIXED).** "Return valid JSON only." / "Return CSV with a header row." name no
   application, field or filter word, so they fell to `general_query` -> the agent layer,
   which (nothing to call a tool for) either timed out after the full
   `AGENT_TIMEOUT_SECONDS` wait or wrote its own answer-system-prompt back as a
   nonsensical, self-referential refusal ("You are not the SIS assistant for the officer
   who asked the question...") -- CLAUDE.md's documented "agent copies its own system
   prompt back" failure, here in the final-answer step rather than a tool argument. Fixed:
   a new deterministic `_FORMAT_UNSUPPORTED_RE` / `format_unsupported` special-scope kind
   answers instantly and honestly that this architecture renders a table (with column/
   sort/group control) and cannot yet return raw JSON, CSV or a bare Markdown table --
   never reaching the LLM for this shape of message at all.

Needs the live DB + Ollama for the end-to-end checks (the agent-fallback case legitimately
needs a model call to prove it no longer reaches one); the routing-only checks need
neither:
    python test_output_format_requests.py            # everything
    python test_output_format_requests.py --routing   # routing/regex only, no DB/LLM
"""
from __future__ import annotations
import asyncio, os, re, sys

cand = os.getcwd()
ROOT = cand
for _ in range(5):
    if os.path.isdir(os.path.join(cand, "backend")):
        ROOT = cand
        break
    cand = os.path.dirname(cand)
sys.path.insert(0, ROOT)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from backend.services import chatbot as c
from backend.services import followup_context as fc

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


SEED = ("Show my pending ISD applications, including application number, "
        "service code, submission date and status.")

# ---- 1. the seed question must stay ONE scoped listing, never split ----
check('seed listing-with-columns sentence is not split into sub-questions',
      c._big_plan(SEED) is None, str(c._big_plan(SEED)))
for f in ("service code", "submission date and status"):
    check(f'"{f}" recognised as a bare column field (not a fresh question)', c._is_bare_column_field(f))
# "including application number" is handled one step earlier -- `_big_plan`
# strips the "including"/"include" lead-in from a split field BEFORE this
# check ever sees it (that strip is what `_big_plan(SEED) is None` above
# already proves end to end) -- `_is_bare_column_field` itself is never
# asked to parse a lead-in word away.
check('"IGRS owner name" (a real question, not a column) is NOT a bare column field',
      not c._is_bare_column_field("IGRS owner name"))

# ---- 2. "restore the original format" is a column reset, not a mutation/LLM fallback ----
for msg in ("restore the original format", "restore the previous format.", "restore it to the original table"):
    check(f'"{msg}" recognised as a column reset', bool(fc._COLUMN_RESET_RE.search(msg.lower())))
check('"restore the application to pending" is NOT a column reset (it is a mutation request)',
      not fc._COLUMN_RESET_RE.search("restore the application to pending"))

# ---- 3. pure JSON/CSV/Markdown-table-only requests never reach the LLM ----
FORMAT_UNSUPPORTED_CASES = (
    "Return valid JSON only.",
    "Return JSON only, followed by an explanation outside the JSON.",
    "Return CSV with a header row.",
    "Return a Markdown table with no text before or after it.",
    "Show the result in a format I can paste into Excel.",
    "Return valid JSON with comments and trailing commas.",
    "Return CSV but put each record on a separate JSON line.",
)
for msg in FORMAT_UNSUPPORTED_CASES:
    check(f'"{msg}" recognised as an unsupported-format request', c._special_scope_kind(msg) == "format_unsupported")
for msg in ("Restore the original format.", "what is 0154?", "show my pending applications",
            "Return the application to its previous status."):
    check(f'"{msg}" NOT misclassified as an unsupported-format request',
          c._special_scope_kind(msg) != "format_unsupported")


async def _e2e():
    import time
    from sqlalchemy import select
    from backend.database import AsyncSessionLocal
    from backend.models import SISOfficer
    from backend.schemas import OfficerContext
    from backend.services.auth_service import get_officer_jurisdiction_ids

    async with AsyncSessionLocal() as db:
        res = await db.execute(select(SISOfficer).where(SISOfficer.email == "marun_chn@sis.tn.gov.in"))
        officer = res.scalar_one()
        jur = await get_officer_jurisdiction_ids(officer.id, db)
        ids = (jur["district_ids"] + jur["taluk_ids"] + jur["town_ids"] + jur["ward_ids"] + jur["block_ids"])
        ctx = OfficerContext(
            officer_id=officer.id, employee_id=officer.employee_id, name=officer.name,
            email=officer.email, designation=officer.designation,
            jurisdiction_type=jur["jurisdiction_type"], jurisdiction_name=jur["jurisdiction_name"],
            jurisdiction_ids=[i for i in ids if i])

    async def turn(msg, hist, sid):
        async with AsyncSessionLocal() as db:
            r = await c.process_chat(db=db, message=msg, officer=ctx, chat_history=hist, session_id=sid)
        resp = (r.get("response") if isinstance(r, dict) else r) or ""
        hist.append({"role": "user", "content": msg})
        hist.append({"role": "assistant", "content": resp})
        return resp

    import uuid
    sid = str(uuid.uuid4())
    hist = []
    r1 = await turn(SEED, hist, sid)
    apps1 = set(re.findall(r"\d{4}/\d{3,4}/\d{1,3}/\d+", r1))
    check("e2e: seed question returns exactly 8 pending ISD applications", len(apps1) == 8, f"got {len(apps1)}")

    r2 = await turn("Return only application number and status.", hist, sid)
    apps2 = set(re.findall(r"\d{4}/\d{3,4}/\d{1,3}/\d+", r2))
    check("e2e: narrowing to 2 columns does not change the population",
          apps2 == apps1, f"turn1={len(apps1)} turn2={len(apps2)} new={apps2 - apps1}")

    r3 = await turn("Restore the original format.", hist, sid)
    apps3 = set(re.findall(r"\d{4}/\d{3,4}/\d{1,3}/\d+", r3))
    check("e2e: restore keeps the same 8 applications", apps3 == apps1, f"got {len(apps3)}")
    check("e2e: restore brings back the default (8-column) listing, not the narrowed one",
          "Sub-Division" in r3 or "Sub-Divisions" in r3 or "subdivisions" in r3.lower(), r3[:200])

    t0 = time.monotonic()
    r4 = await turn("Return valid JSON only.", hist, sid)
    dt = time.monotonic() - t0
    check("e2e: JSON-only request answers fast (no LLM/agent round-trip)", dt < 5.0, f"{dt:.1f}s")
    check("e2e: JSON-only request states the real limitation, not a self-referential refusal",
          "cannot" in r4.lower() and "json" in r4.lower() and "not the sis assistant" not in r4.lower(), r4[:200])


if "--routing" not in sys.argv:
    asyncio.run(_e2e())
for name, detail in bad:
    print(f"FAIL {name}" + (f" -- {detail}" if detail else ""))
print(f"{total - len(bad)}/{total} ok")
sys.exit(1 if bad else 0)
