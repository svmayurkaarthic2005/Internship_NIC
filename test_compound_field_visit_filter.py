"""Regression for a compound-request routing bug: "Show my pending ISD applications,
count them, and tell me which have a field visit scheduled." split correctly into
three clauses (the big-message planner), and the first two answered fine, but the
third -- "tell me which have a field visit scheduled" -- fell to the generic
ambiguous-singular clarification ("That answer covered 8 field visits (...). Which
one do you mean? Give the application number...") instead of filtering the 8 carried
applications by field-visit-scheduled presence.

Root cause: `followup_context.parse_field_presence_request()` (Mode A: filter a
CARRIED list by a per-row field's presence) required a "show/list/display/give/
filter" verb before its field-cue/polarity checks ever ran. "tell me which have a
field visit scheduled" names neither, so it was never claimed here and fell through
to the generic ambiguous-list resolver instead.

Fix: `_PRESENCE_LISTING_RE` gained a "which [of them] have/has" alternative -- on its
own a sufficient trigger for this filter shape, since "which have X" already names
the filter directly without needing "show"/"applications" in the same breath.

Needs the live DB (field-visit data), no LLM:
    python test_compound_field_visit_filter.py
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


# The unit-level repro: the parser must now claim this shape.
check('"tell me which have a field visit scheduled" -> (field_visit_scheduled, True)',
      fc.parse_field_presence_request("tell me which have a field visit scheduled")
      == ("field_visit_scheduled", True))
check('"which have a subdivision recorded" -> (subdivision, True)',
      fc.parse_field_presence_request("which have a subdivision recorded") == ("subdivision", True))
check('"which of them have no workflow remarks" -> (workflow_remarks, False)',
      fc.parse_field_presence_request("which of them have no workflow remarks") == ("workflow_remarks", False))

# Must NOT be swept up by the new "which have" alternative: no known field cue,
# or not even the right shape at all.
for msg in ("which of them have an IGRS number", "what have you done",
            "which officer has jurisdiction over ward 5", "which one do you mean"):
    check(f'{msg!r} still unclaimed (no false positive)', fc.parse_field_presence_request(msg) is None)


async def _e2e():
    from sqlalchemy import select
    from backend.database import AsyncSessionLocal
    from backend.models import SISOfficer
    from backend.schemas import OfficerContext
    from backend.services import chatbot
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

    async with AsyncSessionLocal() as db:
        r = await chatbot.process_chat(
            db=db, officer=ctx, chat_history=[], session_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            message="Show my pending ISD applications, count them, and tell me which have a field visit scheduled.")
    resp = (r.get("response") if isinstance(r, dict) else r) or ""
    check('e2e: clause 3 is NOT the dead-end "which one do you mean?" clarification',
          "Which one do you mean?" not in resp, resp[-200:])
    check('e2e: clause 3 reads as a filtered-list answer ("N of those 8 application(s) have...")',
          re.search(r"of those 8 application\(s\) have a scheduled field visit", resp) is not None, resp[-300:])


asyncio.run(_e2e())
print(f"{total - len(bad)}/{total} ok")
sys.exit(1 if bad else 0)
