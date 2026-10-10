"""Regression suite for stale-application-context bugs: a fresh question (a new topic,
a listing, or an unrelated field) must never inherit an application number that only
appears because it was mentioned earlier in the conversation -- in an assistant card,
a listing table, or an officer's own prior turn. `python test_context_regression.py`
(needs the DB + Ollama; no LLM-free fast path, since several cases need the agent/RAG
fallback to prove it stays silent rather than hallucinating).
"""
from __future__ import annotations

import asyncio
import sys
import uuid

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, r"C:\proj\nic_internship")

from sqlalchemy import select

from backend.database import AsyncSessionLocal
from backend.models import SISOfficer
from backend.schemas import OfficerContext
from backend.services import chatbot
from backend.services.auth_service import get_officer_jurisdiction_ids

OFFICER_EMAIL = "marun_chn@sis.tn.gov.in"
APP_0178 = "2026/0178/02/000267"   # F-Line, register-only (no owner table)
APP_ISD = "2026/0154/02/012547"    # ISD, has a chitta old-owner record (4 rows)

_results: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    _results.append((name, condition, detail))
    print(f"{'ok ' if condition else 'FAIL'} {name}" + (f" -- {detail}" if detail and not condition else ""))


async def officer_context(db, officer) -> OfficerContext:
    jur = await get_officer_jurisdiction_ids(officer.id, db)
    ids = (jur["district_ids"] + jur["taluk_ids"] + jur["town_ids"]
           + jur["ward_ids"] + jur["block_ids"])
    return OfficerContext(
        officer_id=officer.id, employee_id=officer.employee_id, name=officer.name,
        email=officer.email, designation=officer.designation,
        jurisdiction_type=jur["jurisdiction_type"], jurisdiction_name=jur["jurisdiction_name"],
        jurisdiction_ids=[i for i in ids if i])


async def get_ctx() -> OfficerContext:
    async with AsyncSessionLocal() as db:
        res = await db.execute(select(SISOfficer).where(SISOfficer.email == OFFICER_EMAIL))
        return await officer_context(db, res.scalar_one())


async def turn(ctx, message, chat_history, sid):
    async with AsyncSessionLocal() as db:
        r = await chatbot.process_chat(db=db, message=message, officer=ctx, chat_history=chat_history, session_id=sid)
    resp = r.get("response") if isinstance(r, dict) else r
    chat_history.append({"role": "user", "content": message})
    chat_history.append({"role": "assistant", "content": resp})
    return resp


def is_card(resp: str) -> bool:
    return "data-table-card" in (resp or "")


def is_listing(resp: str) -> bool:
    """A multi-row application LISTING, not just any card -- a single focused answer
    (one field, or one application's full details) also renders inside a <table>
    in this UI, so that alone is not evidence of "the whole list again"."""
    return "application(s)" in (resp or "").lower() or "Application No." in (resp or "")


async def main():
    ctx = await get_ctx()

    # 1-3: single-app context, three follow-ups in sequence
    sid = str(uuid.uuid4())
    hist = []
    r1 = await turn(ctx, f"{APP_0178} details", hist, sid)
    check("1a. details gives a card", is_card(r1))
    r2 = await turn(ctx, "what is its ward?", hist, sid)
    check("1. 'what is its ward?' -> field answer, no card", not is_card(r2) and "ward" in r2.lower(), r2[:150])
    r3 = await turn(ctx, "what is today's date?", hist, sid)
    check("2. 'what is today's date?' -> date, independent of app", APP_0178 not in r3 and not is_card(r3), r3[:150])
    r4 = await turn(ctx, "what is TSLR?", hist, sid)
    check("3. 'what is TSLR?' -> no stale app context", APP_0178 not in r4 and not is_card(r4), r4[:150])

    # 4: fresh SRO listing, no prior turn
    sid = str(uuid.uuid4())
    r = await turn(ctx, "what are applications from SRO?", [], sid)
    check("4. 'applications from SRO' -> listing, not 'specify number'",
          is_listing(r) and "specify" not in r.lower(), r[:150])

    # 5-6: pending list, then an unrelated question, then an explicit different app
    sid = str(uuid.uuid4())
    hist = []
    r_list = await turn(ctx, "show pending applications", hist, sid)
    r = await turn(ctx, "what is TSLR?", hist, sid)
    check("5. after pending list, 'what is TSLR?' -> no list/card inherited",
          not is_listing(r) and not is_card(r), r[:150])
    r = await turn(ctx, f"what is the status of {APP_ISD}?", hist, sid)
    check("6. after pending list, explicit app status -> that app only",
          APP_ISD in r and "status" in r.lower(), r[:150])

    # 7: field question on a register-only app -> field answer, no full card
    sid = str(uuid.uuid4())
    r = await turn(ctx, f"what is the relative name of the owner {APP_0178}?", [], sid)
    check("7. field question on register-only app -> no full card", not is_card(r), r[:150])

    # 8: single-app context then bare field follow-up
    sid = str(uuid.uuid4())
    hist = []
    await turn(ctx, f"what is the relative name of the owner of {APP_ISD}?", hist, sid)
    r = await turn(ctx, "patta?", hist, sid)
    check("8. bare field follow-up after single app -> resolves that app", APP_ISD in r, r[:150])

    # 9: multi-app list then an ambiguous bare field question -> must ask, not guess row 1
    sid = str(uuid.uuid4())
    hist = []
    await turn(ctx, "show pending applications", hist, sid)
    r = await turn(ctx, "what is the relative name?", hist, sid)
    check("9. ambiguous field after a list -> clarifies, doesn't guess",
          not is_card(r) and ("which" in r.lower() or "specify" in r.lower() or "one" in r.lower()), r[:150])

    # 10-12: channel listings
    for channel, word in (("CSC", "csc"), ("citizen", "citizen"), ("SRO", "sub-registrar")):
        sid = str(uuid.uuid4())
        r = await turn(ctx, f"what are applications from {channel}?", [], sid)
        check(f"1x. '{channel}' listing", is_listing(r) and word in r.lower(), r[:150])

    # 13-14: typo tolerance vs. false-positive "slow"
    sid = str(uuid.uuid4())
    r_typo = await turn(ctx, "shwo applications", [], sid)
    sid = str(uuid.uuid4())
    r_clean = await turn(ctx, "show applications", [], sid)
    check("13. 'shwo applications' behaves like 'show applications'",
          is_listing(r_typo) == is_listing(r_clean), r_typo[:150])
    sid = str(uuid.uuid4())
    r = await turn(ctx, "why is my application slow?", [], sid)
    check("14. 'why is my application slow?' -> not a listing", not is_listing(r), r[:150])

    # 16: ward-scoped list
    sid = str(uuid.uuid4())
    r = await turn(ctx, "what is in ward 001?", [], sid)
    check("16. 'what is in ward 001?' -> ward-scoped", "001" in r, r[:150])

    # 17: year vs ward confusion
    sid = str(uuid.uuid4())
    r = await turn(ctx, "how many applications did I approve in 2024 in my ward?", [], sid)
    check("17. year 2024 read as a year, turn completes", bool(r), r[:150])

    # 18: application number's own survey field, not a derived survey/ward/block guess
    sid = str(uuid.uuid4())
    r = await turn(ctx, f"what is the survey number in application {APP_ISD}?", [], sid)
    check("18. survey number of the NAMED application", "7" in r and APP_ISD in r, r[:150])

    # 18a-c: "include rejected" widening must never be hijacked into application_status,
    # whether the base listing had a channel or not, and must not resolve to a stale app.
    sid = str(uuid.uuid4())
    hist = []
    await turn(ctx, "what r applications from sro", hist, sid)
    r = await turn(ctx, "include reject too", hist, sid)
    check("18a. 'include reject too' after SRO listing -> SRO rejected, not app-number ask",
          "Sub-Registrar" in r and "specify the application" not in r.lower(), r[:200])
    sid = str(uuid.uuid4())
    hist = []
    await turn(ctx, "show pending applications", hist, sid)
    r = await turn(ctx, "include reject too", hist, sid)
    check("18b. 'include reject too' after a channel-less listing -> all rejected, no hijack",
          is_listing(r) and "specify the application" not in r.lower(), r[:200])
    sid = str(uuid.uuid4())
    hist = []
    await turn(ctx, f"{APP_ISD} details", hist, sid)
    r = await turn(ctx, "show rejected applications", hist, sid)
    # APP_ISD is itself rejected, so it may legitimately appear as ONE row of the
    # listing -- what matters is that this is the full register-wide list (not a
    # single-application lookup collapsed back onto just that one file).
    check("18c. 'show rejected applications' after a single-app card -> the rejected LIST, not a single-app lookup",
          is_listing(r) and "Found" in r and "application(s)" in r, r[:200])

    # 20-22: ordinal follow-ups on a listing
    sid = str(uuid.uuid4())
    hist = []
    r_list = await turn(ctx, "show applications", hist, sid)
    r2nd = await turn(ctx, "what is the second one?", hist, sid)
    check("20. 'the second one' resolves a row, not a repeat of the list",
          not is_listing(r2nd), r2nd[:150])
    sid = str(uuid.uuid4())
    hist = []
    await turn(ctx, "show applications", hist, sid)
    r3rd = await turn(ctx, "what is the third one?", hist, sid)
    check("21. 'the third one' resolves a row", not is_listing(r3rd), r3rd[:150])
    sid = str(uuid.uuid4())
    hist = []
    await turn(ctx, "show applications", hist, sid)
    r = await turn(ctx, "what is its status?", hist, sid)
    check("22. 'its status' after a multi-row list -> clarifies, doesn't guess",
          "which" in r.lower() or "specify" in r.lower() or "one" in r.lower(), r[:150])

    # 23-24: single app context, then a fresh listing request
    sid = str(uuid.uuid4())
    hist = []
    await turn(ctx, f"{APP_ISD} details", hist, sid)
    r = await turn(ctx, "show SRO applications", hist, sid)
    check("23. after single app, 'show SRO applications' -> fresh list", is_listing(r) and not is_card(r), r[:150])
    sid = str(uuid.uuid4())
    hist = []
    await turn(ctx, f"{APP_ISD} details", hist, sid)
    r = await turn(ctx, "show pending applications", hist, sid)
    check("24. after single app, 'show pending applications' -> fresh list", is_listing(r), r[:150])

    # 28: old-owner multi-row single-field -- owner identity preserved, not repeated bare labels
    sid = str(uuid.uuid4())
    r = await turn(ctx, f"what is the relative name of the owner of {APP_ISD}?", [], sid)
    check("28. multi-owner single-field keeps owner identity", "Owner 1" in r and "Owner 2" in r, r[:200])

    # 29: unknown register-only field -> not-recorded/clarification, never the full card
    sid = str(uuid.uuid4())
    r = await turn(ctx, f"what is the relative name of the owner {APP_0178}?", [], sid)
    check("29. unknown register-only field -> no full card", not is_card(r), r[:150])

    # 30: a listing naming many application numbers, then a fresh conceptual question
    sid = str(uuid.uuid4())
    hist = []
    r_list = await turn(ctx, "show ISD applications", hist, sid)
    r = await turn(ctx, "what is TSLR?", hist, sid)
    check("30. after a 20-number listing, 'what is TSLR?' -> no stale resolution",
          not is_card(r) and not is_listing(r), r[:150])

    passed = sum(1 for _, ok, _ in _results if ok)
    total = len(_results)
    print(f"\n{passed}/{total} passed")
    if passed != total:
        print("\nFailures:")
        for name, ok, detail in _results:
            if not ok:
                print(f"  - {name}: {detail}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
