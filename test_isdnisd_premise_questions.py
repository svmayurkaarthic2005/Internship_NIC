"""Regression: a question or false claim naming both ISD and NISD must not
be swept into a full open-applications dump just because it contains the
word "and" -- `rag.parse_intent`'s ISD/NISD-pair detection had no gate
beyond "both words present", so two confirmed-live defects followed:

1. "Which service codes correspond to ISD and NISD?" (a catalog/definition
   question) answered with a 57-row application listing table instead of
   stating 0154=ISD / 0153=NISD.
2. "ZDT handles ISD and DIS handles NISD, correct?" (a FALSE premise -- it
   is backwards: DIS exists only in the ISD chain, ZDT only in NISD) got
   the same listing table at the rag.py layer; once that was fixed to fall
   through to general_query, the LLM then answered "Yes, that is correct"
   -- confidently agreeing with a wrong claim.

Fixes:
- `rag.parse_intent`: three separate (previously unguarded) `both_applications`
  return sites now stand aside for a "which ... code(s)" definitional
  question or a message ending in a verification tag ("correct?"/"right?"/
  "isn't it?"), falling through to `general_query` instead.
- `chatbot._workflow_steps_topic`: a NEW deterministic branch catches the
  verification-tag shape when it names a role from each chain (an
  ISD-chain role word plus "zdt") and returns the "ALL" side-by-side
  comparison -- grounded, no LLM, and self-correcting: DIS only appears
  under ISD, ZDT only under NISD, so the backwards claim is visibly wrong
  from the answer's own structure.

Reproduced live against the real chatbot before fixing (verified, not
assumed) and re-verified after.

Run: python test_isdnisd_premise_questions.py
"""
import asyncio
import sys
import uuid
import re

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


# ---- unit: rag.parse_intent no longer dumps a listing for these shapes ----
from backend.services.rag import parse_intent

check('"Which service codes correspond to ISD and NISD?" does not route to '
      "an application listing",
      parse_intent("Which service codes correspond to ISD and NISD?") != "both_applications",
      parse_intent("Which service codes correspond to ISD and NISD?"))
check('"ZDT handles ISD and DIS handles NISD, correct?" does not route to '
      "an application listing",
      parse_intent("ZDT handles ISD and DIS handles NISD, correct?") != "both_applications",
      parse_intent("ZDT handles ISD and DIS handles NISD, correct?"))
check('a genuine both-type listing request is UNCHANGED',
      parse_intent("Show both ISD and NISD applications") == "both_applications")
check('a genuine both-type count request is UNCHANGED',
      parse_intent("how many ISD and NISD applications do I have") == "both_applications")
check('"compare ISD and NISD" is UNCHANGED (its own comparison intent)',
      parse_intent("compare ISD and NISD") == "compare_applications")

# ---- unit: chatbot._workflow_steps_topic routes the false-premise claim to
#      the deterministic, self-correcting side-by-side comparison ----
from backend.services.chatbot import _workflow_steps_topic

check('"ZDT handles ISD and DIS handles NISD, correct?" routes to the full '
      "ISD+NISD comparison (ALL)",
      _workflow_steps_topic("ZDT handles ISD and DIS handles NISD, correct?") == "ALL")


async def _e2e():
    from sqlalchemy import select
    from backend.database import AsyncSessionLocal
    from backend.models import SISOfficer
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

    async def ask(msg):
        sid = str(uuid.uuid4())
        async with AsyncSessionLocal() as db:
            r = await cb.process_chat(db=db, message=msg, officer=ctx, chat_history=[], session_id=sid)
        return r

    r1 = await ask("Which service codes correspond to ISD and NISD?")
    resp1 = r1.get("response") or ""
    check("live: the code-mapping question states 0154 and 0153, not an "
          "application table",
          "0154" in resp1 and "0153" in resp1 and "Application No." not in resp1, resp1)

    r2 = await ask("ZDT handles ISD and DIS handles NISD, correct?")
    resp2 = re.sub("<[^>]+>", " ", r2.get("response") or "")
    check("live: the false-premise claim gets the deterministic side-by-side "
          "comparison naming DIS under ISD and Zonal Level Tahsildar under NISD "
          "(self-correcting), not a bare 'yes that is correct'",
          "DIS" in resp2 and "Zonal Level Tahsildar" in resp2
          and resp2.strip().lower() not in ("yes, that is correct.", "yes that is correct"),
          resp2)
    check("live: the false-premise answer never flatly agrees with the "
          "backwards claim",
          not re.match(r"^\s*yes,?\s+that'?s?\s+correct", resp2, re.IGNORECASE), resp2)


asyncio.run(_e2e())
print(f"{total - len(bad)}/{total} ok")
sys.exit(1 if bad else 0)
