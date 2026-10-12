"""Regression: District Revenue Officer (DRO) and escalation-process
questions -- four confirmed, reproduced defects found while auditing the
documented workflow roles (workflow_guide.txt's "ESCALATION PROCESS"
section: Level 1/2/3, DRO notified at Level 3 / 45 days overdue).

1. "Who gets notified at Level 3 escalation, 45 days overdue?" was split by
   `_plan_requests`'s bare-comma `_REQ_SPLIT` into two bogus fragments
   ("Who gets notified at Level 3 escalation" / "45 days overdue?") and
   answered TWICE, both wrong: the first half (no "overdue" context) as a
   19-row pending-applications dump, the second half as an ambiguous
   "which application do you mean?" follow-up. Root cause:
   `_plan_requests`'s "request without a verb" fallback
   (backend/services/chatbot.py) had no guard against a trailing clause
   that starts with a bare number -- a strong signal it is a continuation
   of the previous clause, never a request of its own.
2. Even as one whole sentence, "escalat[ion]" + no application number +
   no word "application" routed to `escalation_check` (rag.py) -- the
   "which of MY applications are near the deadline" feature -- instead of
   being recognised as a question about the documented PROCESS itself.
3. "What happens to a DRO application?" was read as an ambiguous spelling
   of "SRO" (Sub-Registrar, a submission channel) and asked "CSC, SRO or
   Citizen?" -- "dro" was a curated one-edit typo of "sro" in
   `_CHANNEL_CLARIFY_VARIANTS` (rag.py) with no awareness that DRO is
   itself a real, documented role.
4. "who is the dro" had no deterministic answer and reached the LLM
   fallback, which answered about the Tahsildar/ZDT instead -- a
   confident, fluent answer to a question about a different role. DRO was
   simply missing from `_GLOSSARY` (chatbot.py), unlike every other role
   in workflow_guide.txt.

Fixes: a numeric-leading-clause guard in `_plan_requests`; a process-vs-
feature guard (`_asks_escalation_process`) in both `escalation_check` gates
in rag.py, plus an earlier, higher-priority "who gets notified" guard in
`parse_intent` itself; removing "dro" from the SRO typo-variant list; a new
`_GLOSSARY["dro"]` entry (plus "district revenue officer" / "who is X"
glossary-trigger support).

Reproduced live against the real chatbot before fixing (verified, not
assumed) and re-verified after.

Run: python test_dro_escalation_questions.py
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


# ---- unit: the comma-splitter no longer tears this question in two ----
from backend.services.chatbot import _plan_requests, _GLOSSARY, _GLOSSARY_RE

check('"Who gets notified at Level 3 escalation, 45 days overdue?" is no '
      "longer split into two bogus fragments",
      _plan_requests("Who gets notified at Level 3 escalation, 45 days overdue?") is None,
      _plan_requests("Who gets notified at Level 3 escalation, 45 days overdue?"))
check('a genuine two-question request ("what is the sla for isd and ... nisd") is UNCHANGED',
      _plan_requests("what is the sla for isd and what is the sla for nisd") is not None)

# ---- unit: parse_intent routes the process question away from both the
#      escalation_check feature and the generic application-number ask ----
from backend.services.rag import parse_intent, ambiguous_channel_clarification

check('"Who gets notified at Level 3 escalation, 45 days overdue?" does not '
      "route to escalation_check (the 'my applications near deadline' feature)",
      parse_intent("Who gets notified at Level 3 escalation, 45 days overdue?") != "escalation_check")
check('... and does not route to application_status (asking for a number) either',
      parse_intent("Who gets notified at Level 3 escalation, 45 days overdue?") != "application_status")
check('"which applications are approaching the escalation threshold" (the '
      "real feature) is UNCHANGED",
      parse_intent("which applications are approaching the escalation threshold") == "escalation_check")
check('"show my escalated applications" (a status word, not a process '
      "question) still reaches a listing, not general_query",
      parse_intent("show my escalated applications") != "general_query")

# ---- unit: DRO is no longer an ambiguous "sro" typo ----
check('"What happens to a DRO application?" is no longer read as an '
      "ambiguous SRO spelling",
      not ambiguous_channel_clarification("What happens to a DRO application?"))
check('a genuine SRO typo ("seo applications") still asks for clarification',
      ambiguous_channel_clarification("show seo applications"))

# ---- unit: DRO is grounded in the glossary ----
check('"dro" is in the glossary', "dro" in _GLOSSARY)
check("the DRO glossary entry names Level 3 / 45 days and says it is NOT "
      "part of the ISD/NISD approval chain",
      "Level 3" in _GLOSSARY["dro"][0] and "45" in _GLOSSARY["dro"][0]
      and "not part of" in _GLOSSARY["dro"][0].lower())
check('"who is the dro" matches the glossary trigger',
      bool(_GLOSSARY_RE.match("who is the dro")))
check('"who is zdt" still matches too (the widened trigger does not break '
      "the existing terms)",
      bool(_GLOSSARY_RE.match("who is zdt")))


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

    r1 = await ask("who is the dro")
    resp1 = r1.get("response") or ""
    check("live: 'who is the dro' names the District Revenue Officer and "
          "Level 3 escalation, not the Tahsildar",
          "District Revenue Officer" in resp1 and "Level 3" in resp1, resp1)

    r2 = await ask("What happens to a DRO application?")
    resp2 = r2.get("response") or ""
    check("live: a DRO question is never asked as a channel clarification "
          "(CSC/SRO/Citizen)",
          "CSC" not in resp2 or "Sub-Registrar" not in resp2, resp2)

    r3 = await ask("Who gets notified at Level 3 escalation, 45 days overdue?")
    resp3 = re.sub("<[^>]+>", " ", r3.get("response") or "")
    check("live: the escalation-process question is answered once, "
          "coherently -- not split into two mismatched fragments",
          "1." not in resp3[:5] and "Application No." not in resp3, resp3[:300])


asyncio.run(_e2e())
print(f"{total - len(bad)}/{total} ok")
sys.exit(1 if bad else 0)
