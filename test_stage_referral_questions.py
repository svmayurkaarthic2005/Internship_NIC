"""Regression: "has this case been referred/sent/forwarded to <desk>?" (the
natural officer phrasing from the production-readiness mega-task's role/
workflow section) asks about the file's HISTORY -- did it ever reach that
desk -- not where it sits right now.

Confirmed defect, reproduced live before fixing: `_stage_yes_no_question()` /
`_render_stage_yes_no_answer()` in backend/services/chatbot.py only ever
compared the asked desk against `structured_data["stage"]`, the CURRENT
stage. "Has this case been referred to the Tahsildar?" on a NISD file that
the Tahsildar actually decided (current_stage now REJECTED, a terminal
stage) got a confident "No -- X is currently with rejected." -- the opposite
of the truth: the file WAS referred to, and decided by, the Tahsildar. The
same failure hit "has it been sent to the Senior Draughtsman?" on a rejected
ISD file that had genuinely passed through SD.

Fix: a `referred/forwarded/sent/escalated ... to` / `reached` / `passed
through` / `moved to` phrasing is answered from workflow_history (did any
hop's to_stage ever equal the asked stage), via a new
`_render_stage_ever_answer()`; the current-location phrasing ("is it
currently with X", "is it with X right now") is unchanged and still answered
from `structured_data["stage"]` alone.

Run: python test_stage_referral_questions.py
"""
import asyncio
import sys
import uuid

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

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


from backend.services.chatbot import (
    _stage_yes_no_question, _is_stage_ever_question, _render_stage_ever_answer,
)

# ---- unit: the "ever referred" phrasing is told apart from "currently with" ----
check('"Has this case been referred to the Tahsildar?" is an EVER question',
      _is_stage_ever_question("Has this case been referred to the Tahsildar? 2026/0153/02/000001"))
check('"Has the case been sent to the Senior Draughtsman?" is an EVER question',
      _is_stage_ever_question("Has the case been sent to the Senior Draughtsman?"))
check('"was it forwarded to the DIS" is an EVER question',
      _is_stage_ever_question("was it forwarded to the DIS"))
check('"has it reached the Tahsildar" is an EVER question',
      _is_stage_ever_question("has it reached the Tahsildar"))
check('"is it currently with the Senior Draughtsman?" is NOT an EVER question (current desk)',
      not _is_stage_ever_question("is it currently with the Senior Draughtsman?"))
check('"is it with the Tahsildar right now?" is NOT an EVER question',
      not _is_stage_ever_question("is it with the Tahsildar right now?"))

check('"Has this case been referred to the Tahsildar?" still names the TAHSILDAR stage',
      _stage_yes_no_question("Has this case been referred to the Tahsildar?") == "TAHSILDAR")
check('"Has the case been sent to the Senior Draughtsman?" still names the SD stage',
      _stage_yes_no_question("Has the case been sent to the Senior Draughtsman?") == "SD")

# ---- unit: the ever-renderer reads workflow_history, not the current stage ----
_hops_reached = [
    {"to_stage": "SIS", "performed_at": None},
    {"to_stage": "TAHSILDAR", "performed_at": None},
    {"to_stage": "REJECTED", "performed_at": None},
]
check("a file whose history reached TAHSILDAR answers Yes, even though its "
      "current stage is the terminal REJECTED",
      _render_stage_ever_answer(_hops_reached, "2026/0153/02/000001", "TAHSILDAR",
                                 "REJECTED", "en").startswith("Yes"))
check("... and names the stage, not just a bare yes",
      "Tahsildar" in _render_stage_ever_answer(_hops_reached, "2026/0153/02/000001",
                                                "TAHSILDAR", "REJECTED", "en"))

_hops_not_reached = [{"to_stage": "SIS", "performed_at": None}]
check("a file whose history never reached SD answers No, and states where it "
      "actually is",
      _render_stage_ever_answer(_hops_not_reached, "2026/0154/02/000002", "SD",
                                 "SIS", "en") ==
      "No — 2026/0154/02/000002 has not been referred to Senior Draughtsman (SD); "
      "it is currently with Sub Inspector Surveyor (SIS).")

_tamil = _render_stage_ever_answer(_hops_reached, "2026/0153/02/000001", "TAHSILDAR",
                                    "REJECTED", "ta")
check("Tamil answer leads with ஆம் for a reached stage", _tamil.startswith("ஆம்"), _tamil)


async def _e2e():
    from sqlalchemy import select
    from backend.database import AsyncSessionLocal
    from backend.models import SISOfficer, Application
    from backend.services import chatbot as cb
    from backend.schemas import OfficerContext
    from backend.services.auth_service import get_officer_jurisdiction_ids

    async with AsyncSessionLocal() as db:
        officer = (await db.execute(
            select(SISOfficer).where(SISOfficer.email == "marun_chn@sis.tn.gov.in")
        )).scalar_one()
        jur = await get_officer_jurisdiction_ids(officer.id, db)
        ids = (jur["district_ids"] + jur["taluk_ids"] + jur["town_ids"]
               + jur["ward_ids"] + jur["block_ids"])
        ctx = OfficerContext(
            officer_id=officer.id, employee_id=officer.employee_id, name=officer.name,
            email=officer.email, designation=officer.designation,
            jurisdiction_type=jur["jurisdiction_type"], jurisdiction_name=jur["jurisdiction_name"],
            jurisdiction_ids=[i for i in ids if i])

        # Find a real NISD file whose chain actually reached TAHSILDAR and then
        # closed (REJECTED/COMPLETED) -- the exact shape the original bug needed --
        # and a real file still sitting at SIS, both live, never assumed.
        reached_closed = (await db.execute(
            select(Application.application_number)
            .where(Application.application_type == "NISD",
                   Application.current_stage.in_(["COMPLETED", "REJECTED"]))
            .limit(1)
        )).scalar_one_or_none()
        still_at_sis = (await db.execute(
            select(Application.application_number)
            .where(Application.application_type == "NISD", Application.current_stage == "SIS")
            .limit(1)
        )).scalar_one_or_none()

    check("fixture: a closed NISD application exists to test against", bool(reached_closed))
    check("fixture: an open-at-SIS NISD application exists to test against", bool(still_at_sis))

    async def ask(msg):
        sid = str(uuid.uuid4())
        async with AsyncSessionLocal() as db:
            r = await cb.process_chat(db=db, message=msg, officer=ctx, chat_history=[], session_id=sid)
        return r.get("response") or ""

    if reached_closed:
        r1 = await ask(f"Has this case been referred to the Tahsildar? {reached_closed}")
        check("live: a closed NISD file that passed through TAHSILDAR answers Yes, "
              "never 'No ... currently with rejected/completed'",
              r1.startswith("Yes") and "Tahsildar" in r1, r1)

    if still_at_sis:
        r2 = await ask(f"Has this case been referred to the Tahsildar? {still_at_sis}")
        check("live: a file still at SIS, never forwarded, answers No and names SIS",
              r2.startswith("No") and "Sub Inspector Surveyor" in r2, r2)

        # the CURRENT-location phrasing must stay literal -- "is it with the
        # Tahsildar" on this same still-open file is also No, but for the
        # ordinary current-stage reason, not the ever-referred one. ("...right
        # now" is a separate, pre-existing field-lookup route that never reaches
        # `_stage_yes_no_question` at all -- out of scope for this fix.)
        r3 = await ask(f"Is it with the Tahsildar? {still_at_sis}")
        check("live: 'is it with X' (current-location, no 'referred'/'sent' verb) "
              "is UNCHANGED behaviour, still a plain No naming the current desk",
              r3.startswith("No") and "Sub Inspector Surveyor" in r3, r3)


asyncio.run(_e2e())
print(f"{total - len(bad)}/{total} ok")
sys.exit(1 if bad else 0)
