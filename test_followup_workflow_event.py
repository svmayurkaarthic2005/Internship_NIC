"""Regression: "what is ITS latest recorded workflow event?" asked right
after narrowing a register-wide listing down to ONE application (e.g. "the
second one" after "show me the oldest three") must answer from THAT file's
own workflow history -- never the generic ISD/NISD policy explanation, and
never a submission-date superlative over a one-row list.

Found live, in a long-context follow-up chain built for the production-
readiness mega-task's section 9 (long-context follow-up stress testing):

```
Show applications for service code 0158.
Exclude rejected applications.
Show me the oldest three.            -> 5-way tie, all 5 named
What about the second one?           -> narrows to one application
What is its latest recorded workflow event?
```

Three confirmed defects, each making the LAST question above answer
something other than what was asked:

1. `_workflow_steps_topic()` (backend/services/chatbot.py) ran very early in
   the pipeline and matched the bare word "workflow" with no awareness that
   an application was already in view, so it answered the generic "The
   workflow depends on the application type: ISD ... NISD ..." policy text
   -- for a file under service code 0158, which is neither. Fixed with a
   pronoun ("its"/"it's"/"this"/"that") + record-word ("latest"/"last"/
   "recorded"/"recent"/"current") guard that stands aside whenever an
   application number was actually named in the recent chat history.
2. Once that stood aside, the bare word "latest" in the question matched the
   NEWEST-submission-date superlative branch in `_scoped_list_answer()`,
   which (now narrowed to the single picked row) answered "The most recent
   of those 1 is X, submitted <submission date>" -- a real fact, but not an
   answer to a question about the WORKFLOW, not the submission date. Fixed
   by excluding `workflow`/`transition` wording from both the oldest- and
   newest-date superlative branches.
3. With both of those out of the way, the question fell through to
   `_scoped_list_answer()`'s generic fallback and answered "There is 1 CSC
   Modification / Anadeenam application(s) in that list." -- a bare count,
   still not the workflow. Fixed with a new `_single_app_workflow_answer()`
   helper, called from both the application_status-list-followup call sites
   (process_chat and process_chat_stream) whenever the resolved follow-up
   narrows to exactly one application AND the message is workflow-shaped
   (`_WF_CUE`): it tries `_workflow_history_answer` (ISD/NISD) then
   `_other_code_workflow` (every other service code) with that one
   application number, ahead of the generic list renderer.

Reproduced live against the real chatbot before fixing (verified, not
assumed) and re-verified after -- using a realistic `chat_history` that
accumulates like the real frontend's `chatStorage.getForAPI()` does, not an
empty list on every turn (an empty list on every turn was this session's own
first, incorrect repro and produced a DIFFERENT, unrelated false alarm about
"nothing has been shown yet" -- see the note in this file's test for why that
case is deliberately not asserted here).

Run: python test_followup_workflow_event.py
"""
import asyncio
import sys
import uuid
import re

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


def strip_tags(s):
    return re.sub(r"<[^>]+>", " ", s or "")


# ---- unit: _workflow_steps_topic stands aside for a record back-reference ----
from backend.services.chatbot import _workflow_steps_topic

_hist_with_app = [
    {"role": "user", "content": "show applications for service code 0158"},
    {"role": "assistant", "content": "Found 1 application(s): 2026/0158/02/000003 ..."},
]
check('"what is its latest recorded workflow event?" stands aside '
      "(returns None) when an application was recently named",
      _workflow_steps_topic("what is its latest recorded workflow event?", _hist_with_app) is None)
check("... but the SAME message with NO application in recent history still "
      "answers the generic policy explanation (no regression to the base case)",
      _workflow_steps_topic("what is its latest recorded workflow event?", []) == "ALL")
check('"what are the ISD workflow steps" (names ISD explicitly) is UNCHANGED '
      "even with an application in view",
      _workflow_steps_topic("what are the isd workflow steps", _hist_with_app) == "ISD")


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

    sid = str(uuid.uuid4())
    history = []

    async def ask(msg):
        async with AsyncSessionLocal() as db:
            r = await cb.process_chat(db=db, message=msg, officer=ctx, chat_history=history, session_id=sid)
        ans = r.get("response") or ""
        # Mirror the real frontend's chatStorage.getForAPI(): the growing
        # transcript is replayed on every subsequent turn, not re-sent empty.
        history.append({"role": "user", "content": msg})
        history.append({"role": "assistant", "content": strip_tags(ans)})
        return strip_tags(ans)

    r1 = await ask("Show applications for service code 0158.")
    check("live: a register-wide listing for 0158 is found", "0158" in r1 and "application" in r1.lower(), r1[:200])

    r2 = await ask("Exclude rejected applications.")
    check("live: the exclusion keeps the 0158 scope", "0158" in r2, r2[:200])

    r3 = await ask("Show me the oldest three.")
    check("live: a tied oldest-date answer names at least one application number",
          bool(re.search(r"\d{4}/\d{3,4}/\d{1,3}/\d+", r3)), r3)

    r4 = await ask("What about the second one?")
    # With a realistic, accumulating chat_history this must NOT be the
    # "nothing has been shown yet" false alarm -- that only happens when the
    # caller (wrongly) never replays history, which this test deliberately
    # does not do.
    check("live: 'the second one' resolves against the tied list just shown "
          "(never the 'nothing shown yet' false alarm)",
          "nothing has been shown" not in r4.lower()
          and bool(re.search(r"\d{4}/\d{3,4}/\d{1,3}/\d+", r4)), r4)

    r5 = await ask("What is its latest recorded workflow event?")
    check("live: answers a WORKFLOW HISTORY for the narrowed application -- "
          "never the generic 'workflow depends on the application type' policy text",
          "depends on the application type" not in r5, r5)
    check("live: ... and never a bare submission-date superlative "
          "('the most recent of those N is X, submitted ...')",
          "submitted" not in r5.lower() or "workflow" in r5.lower(), r5)
    check("live: ... and names an actual workflow step/desk, not just a bare count",
          bool(re.search(r"Surveyor|Draughtsman|Tahsildar|Inspector|Intake|Completed|Rejected|step", r5, re.I)), r5)


asyncio.run(_e2e())
print(f"{total - len(bad)}/{total} ok")
sys.exit(1 if bad else 0)
