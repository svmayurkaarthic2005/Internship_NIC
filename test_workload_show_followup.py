"""Regression suite: "What is my workload?" -> "show" must display the
applications the workload just counted, not refuse with "nothing has been
shown in this conversation yet".

Root cause: `get_officer_workload()` (backend/services/postgres.py) returns
pure aggregate counts (`total_active_all_codes`, `by_service_code`) -- no
application numbers -- so the turn never had a FollowupContext for "show" to
resolve against. "show" fell into `_is_orphan_list_followup()` /
`_is_bare_fragment_without_context()` (backend/services/chatbot.py), which
answered with `_orphan_followup_reply()`'s "nothing has been shown" text --
confidently wrong, since a summary of exactly these records WAS just given.

Fix:
1. `postgres.get_officer_workload_applications()` -- reruns the SAME two
   business rules `get_officer_workload()` counts by (ISD/NISD at the
   officer's own stage with an active status; every other code's pending
   register rows) and returns the rows instead of the totals. A generic
   "pending applications" re-query was deliberately NOT used: that query is
   never stage-pinned for any code, while the workload's own ISD/NISD count
   IS pinned to the officer's current desk -- so it would show a different
   set of ISD/NISD files than the workload just counted.
2. `chatbot._WORKLOAD_SHOW_RE` / `chatbot._workload_show_followup()` --
   recognises a bare "show" / "show applications" / "list" / "display"
   (optionally "me"/"us"/"my"/"them"/"it"/"applications"/"files") right after
   a turn whose re-parsed intent is `officer_workload`, and answers from (1)
   instead of falling into the orphan-fragment check. Wired into BOTH
   `process_chat()` and `process_chat_stream()`, ahead of the orphan check,
   so streaming and non-streaming behave the same way.
3. Along the way, a real bug surfaudit: `postgres.py` defines
   `ACTIVE_STATUSES` twice (a list, then later in the file a tuple that wins
   at import time) and `get_officer_applications()`'s `status` handling only
   special-cases `list`, so passing the tuple directly raised an asyncpg
   `DataError` (silently swallowed by that function's own try/except, which
   meant the ISD/NISD rows went missing from the "show" answer without any
   visible error). Fixed by wrapping in `list(...)`, the same way the one
   other `status=list(ACTIVE_STATUSES)` call site in the file already does.

Needs the live DB (+ Ollama is not required -- these are deterministic,
HTML-table answers, no LLM call on this path) for the end-to-end checks; the
routing-only checks need neither:
    python test_workload_show_followup.py            # everything
    python test_workload_show_followup.py --routing  # no DB
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


# ---- Unit: the bare-show regex recognises the required variants, nothing wider ----
SHOULD_MATCH = ("show", "Show", "show applications", "show my applications",
                "list", "list applications", "display", "display them",
                "show them", "give me the applications", "show it")
for msg in SHOULD_MATCH:
    check(f'"{msg}" recognised as a bare show/list/display request', bool(c._WORKLOAD_SHOW_RE.match(msg)))

SHOULD_NOT_MATCH = ("show applications in ward 102", "show my pending applications from CSC",
                    "what is my workload", "show the overdue ones", "list applications submitted in June")
for msg in SHOULD_NOT_MATCH:
    check(f'"{msg}" is NOT treated as a bare show (it names its own scope)',
          not c._WORKLOAD_SHOW_RE.match(msg))


async def _e2e():
    from sqlalchemy import select
    from backend.database import AsyncSessionLocal
    from backend.models import SISOfficer
    from backend.schemas import OfficerContext
    from backend.services.auth_service import get_officer_jurisdiction_ids

    async def make_ctx(email):
        async with AsyncSessionLocal() as db:
            res = await db.execute(select(SISOfficer).where(SISOfficer.email == email))
            officer = res.scalar_one()
            jur = await get_officer_jurisdiction_ids(officer.id, db)
            ids = (jur["district_ids"] + jur["taluk_ids"] + jur["town_ids"] + jur["ward_ids"] + jur["block_ids"])
            return OfficerContext(
                officer_id=officer.id, employee_id=officer.employee_id, name=officer.name,
                email=officer.email, designation=officer.designation,
                jurisdiction_type=jur["jurisdiction_type"], jurisdiction_name=jur["jurisdiction_name"],
                jurisdiction_ids=[i for i in ids if i])

    ctx = await make_ctx("marun_chn@sis.tn.gov.in")

    async def turn(msg, hist, sid, officer=ctx):
        async with AsyncSessionLocal() as db:
            r = await c.process_chat(db=db, message=msg, officer=officer, chat_history=hist, session_id=sid)
        resp = (r.get("response") if isinstance(r, dict) else r) or ""
        hist.append({"role": "user", "content": msg})
        hist.append({"role": "assistant", "content": resp})
        return r

    import uuid

    # 1) The exact failing conversation from the bug report.
    sid = str(uuid.uuid4())
    hist = []
    r1 = await turn("What is my workload?", hist, sid)
    check('turn 1: "What is my workload?" routes to officer_workload',
          r1.get("intent") == "officer_workload", r1.get("intent"))
    m = re.search(r"workload:\s*(\d+)\s*active", r1.get("response") or "", re.IGNORECASE)
    check("turn 1: the workload total is parseable", bool(m), r1.get("response"))
    workload_total = int(m.group(1)) if m else -1

    r2 = await turn("show", hist, sid)
    check('turn 2: "show" no longer gives the orphan-fragment refusal',
          "nothing has" not in (r2.get("response") or "").lower(), r2.get("response")[:200])
    check('turn 2: "show" reaches the listing handler (all_service_code_applications)',
          r2.get("intent") == "all_service_code_applications", r2.get("intent"))
    shown = set(re.findall(r"\d{4}/\d{3,4}/\d{1,3}/\d+", r2.get("response") or ""))
    check("turn 2: the number of applications shown matches the workload total exactly",
          len(shown) == workload_total, f"workload said {workload_total}, table shows {len(shown)}")

    # 2) Sensible variants.
    for variant in ("show applications", "list", "display"):
        sid_v = str(uuid.uuid4())
        hist_v = []
        rv1 = await turn("What is my workload?", hist_v, sid_v)
        mv = re.search(r"workload:\s*(\d+)\s*active", rv1.get("response") or "", re.IGNORECASE)
        wv = int(mv.group(1)) if mv else -1
        rv2 = await turn(variant, hist_v, sid_v)
        shown_v = set(re.findall(r"\d{4}/\d{3,4}/\d{1,3}/\d+", rv2.get("response") or ""))
        check(f'"{variant}" after a workload summary shows exactly the {wv} counted applications',
              len(shown_v) == wv, f"got {len(shown_v)}")

    # 3) Jurisdiction / authorization: a second, DIFFERENT officer sharing part
    #    of the same jurisdiction legitimately sees some of the same files
    #    (the seed assigns several officers to the same ward -- CLAUDE.md
    #    documents this), so overlap alone proves nothing. What must hold is
    #    that the query is fail-closed: an officer context with NO
    #    jurisdiction at all gets zero rows, never another officer's data by
    #    some fallback/default path.
    from backend.schemas import OfficerContext as _OC
    _no_jur_ctx = _OC(
        officer_id=ctx.officer_id, employee_id=ctx.employee_id, name=ctx.name,
        email=ctx.email, designation=ctx.designation,
        jurisdiction_type=ctx.jurisdiction_type, jurisdiction_name=ctx.jurisdiction_name,
        jurisdiction_ids=[])
    from backend.services.postgres import get_officer_workload_applications
    async with AsyncSessionLocal() as db:
        _no_jur_data = await get_officer_workload_applications(db, _no_jur_ctx)
    check("an officer context with no jurisdiction gets zero applications (fails closed, "
          "never falls back to someone else's data)",
          _no_jur_data.get("count") == 0 and not _no_jur_data.get("applications"),
          _no_jur_data)

    # And a genuinely different officer (different jurisdiction_ids) gets a
    # row set that is independently re-derived -- not frozen to whatever the
    # first officer happened to see.
    try:
        ctx2 = await make_ctx("arun_m@sis.tn.gov.in")
    except Exception:
        ctx2 = None
    if ctx2 and ctx2.jurisdiction_ids != ctx.jurisdiction_ids:
        sid3 = str(uuid.uuid4())
        hist3 = []
        r3a = await turn("What is my workload?", hist3, sid3, officer=ctx2)
        m3 = re.search(r"workload:\s*(\d+)\s*active", r3a.get("response") or "", re.IGNORECASE)
        w3 = int(m3.group(1)) if m3 else -1
        r3 = await turn("show", hist3, sid3, officer=ctx2)
        shown3 = set(re.findall(r"\d{4}/\d{3,4}/\d{1,3}/\d+", r3.get("response") or ""))
        check("a second officer's 'show' is scoped to THEIR OWN workload total, "
              "re-derived independently of the first officer's result",
              len(shown3) == w3, f"officer2 workload said {w3}, table showed {len(shown3)}")
    else:
        print("skip  second-officer re-derivation check (no officer with a different jurisdiction found)")

    # 4) A standalone "show" with no preceding workload (or any list) turn
    #    must still ask for clarification, not invent a listing.
    sid4 = str(uuid.uuid4())
    r4 = await turn("show", [], sid4)
    check("a standalone 'show' with no prior context still asks for clarification",
          "nothing has" in (r4.get("response") or "").lower(), r4.get("response")[:200])

    # 5) A fully-specified question right after a workload summary is a fresh
    #    question, not swallowed by the workload-show hook.
    sid5 = str(uuid.uuid4())
    hist5 = []
    await turn("What is my workload?", hist5, sid5)
    r5 = await turn("show my overdue applications", hist5, sid5)
    check("a fully-specified follow-up ('show my overdue applications') is not misrouted "
          "to the bare workload-show hook",
          r5.get("intent") != "all_service_code_applications" or "overdue" in (r5.get("response") or "").lower(),
          r5.get("intent"))

    # 6) Streaming and non-streaming agree.
    import json as _json
    sid6 = str(uuid.uuid4())
    hist6 = []
    await turn("What is my workload?", hist6, sid6)
    full = ""
    async with AsyncSessionLocal() as db:
        async for chunk in c.process_chat_stream(db=db, message="show", officer=ctx,
                                                  chat_history=hist6, session_id=sid6):
            s = chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk
            for line in s.splitlines():
                if line.startswith("data: ") and line != "data: [DONE]":
                    try:
                        full += _json.loads(line[6:]).get("content", "")
                    except Exception:
                        pass
    shown_stream = set(re.findall(r"\d{4}/\d{3,4}/\d{1,3}/\d+", full))
    check("streaming path: 'show' after a workload summary shows the same applications "
          "as the non-streaming path",
          shown_stream == shown, f"non-stream={len(shown)} stream={len(shown_stream)}")


if "--routing" not in sys.argv:
    asyncio.run(_e2e())
for name, detail in bad:
    print(f"FAIL {name}" + (f" -- {detail}" if detail else ""))
print(f"{total - len(bad)}/{total} ok")
sys.exit(1 if bad else 0)
