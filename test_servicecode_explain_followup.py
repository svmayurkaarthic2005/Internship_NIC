"""Regression suite: "explain briefly" / "explain in sentence" style follow-ups
after a service-code lookup must return a plain-prose sentence, never the
9-row bulleted field/details answer the frontend's plainToCard() renders as a
table.

Bug: "What is service code 0154?" -> "Explain briefly" -> "Explain in
sentence" returned the SAME full 9-bullet answer for the second and third
turns. Root causes, both in `_is_detail_request()` / `_is_explain_request()`
(backend/services/chatbot.py) and the typo corrector
(backend/services/followup_context.py):

1. `_is_detail_request()` treated the bare word "explain" as a request for
   MORE detail unless the message ALSO carried one of a fixed list of
   brevity words ("brief", "short", "summary", ...). "Explain in a
   sentence" / "Explain in one sentence" / "Explain in simple words" carry
   none of those, so they were read as detail requests and got the full
   bulleted card. Fixed with `_BRIEF_REQUEST_PHRASES_RE`, checked ahead of
   the detail-trigger-word fallback in both `_is_explain_request()` (so the
   re-route to `service_code_lookup` fires at all) and `_is_detail_request()`
   (so `brief=True` is chosen).
2. "Tell me in one sentence" carries no explain/describe/elaborate word at
   all, so `_is_explain_request()` returned False and the re-route never
   fired. Fixed by the same `_BRIEF_REQUEST_PHRASES_RE` check.
3. The per-turn typo corrector (`followup_context._correct_typos`) rewrote
   "words" to "wards" (one substitution, and "wards" is a domain cue word),
   so "Explain in simple words" became "Explain in simple wards" before the
   brevity-phrase regex ever saw it. Fixed by adding "words" to
   `_NEVER_CORRECT`.
4. A bare brevity follow-up with no "explain" word ("Summarise briefly") was
   intercepted earlier in the pipeline by the generic column/table reformat
   handler (`_detect_reformat_request` -> "short"), which tried to reshape
   the previous answer as a set of fields and failed ("I can't reshape my
   last reply ... it wasn't a set of fields") because a one-line prose
   answer has no fields to reshape. Fixed in `_reformat_previous_answer()`:
   when nothing tabular/bulleted is found and a plain-prose previous answer
   already exists, "short"/"prose" is satisfied by returning it unchanged.

Needs the live DB (+ Ollama for the LLM paraphrase, timeboxed to 6s and
falling back to the deterministic text) for the end-to-end checks; the
routing-only checks need neither:
    python test_servicecode_explain_followup.py            # everything
    python test_servicecode_explain_followup.py --routing  # no DB/LLM
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


# ---- Unit: brevity phrasings are recognised as "want LESS detail", not more ----
BRIEF_FOLLOWUPS = (
    "Explain briefly",
    "Explain in sentence",
    "Explain in a sentence",
    "Explain in one sentence",
    "Explain in simple words",
    "Summarise briefly",
    "Tell me in one sentence",
)
for msg in BRIEF_FOLLOWUPS:
    full = f"{msg} What is service code 0154?"  # the re-route appends the prior question
    check(f'"{msg}" recognised as an explain follow-up', c._is_explain_request(msg))
    check(f'"{msg}" is NOT read as a request for MORE detail', not c._is_detail_request(full),
          f"_is_detail_request({full!r}) was True")

# An explicit detail request must still ask for the full answer.
for msg in ("Explain in detail", "Explain more", "Tell me more"):
    full = f"{msg} What is service code 0154?"
    check(f'"{msg}" still recognised as a request for MORE detail', c._is_detail_request(full))

# ---- Unit: "words" must never be typo-corrected to the domain cue "wards" ----
check('"words" is in the never-correct list',
      "words" in fc._NEVER_CORRECT)
check('"Explain in simple words" survives the typo corrector unchanged',
      fc.correct_spelling("Explain in simple words") == "Explain in simple words",
      fc.correct_spelling("Explain in simple words"))


async def _e2e():
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

    def is_prose(resp: str) -> bool:
        return "•" not in resp and resp.count("\n") <= 1

    import uuid

    # 1) Every brevity phrasing after an ISD lookup must be prose, and must
    #    still name 0154 / ISD -- the context, not just the shape, must survive.
    for msg in BRIEF_FOLLOWUPS:
        sid = str(uuid.uuid4())
        hist = []
        await turn("What is service code 0154?", hist, sid)
        resp = await turn(msg, hist, sid)
        check(f'e2e: "{msg}" after an ISD lookup is prose, not a table',
              is_prose(resp), resp[:160])
        check(f'e2e: "{msg}" still names ISD / 0154',
              "0154" in resp or "ISD" in resp, resp[:160])

    # 2) The same behaviour after an NISD lookup.
    sid = str(uuid.uuid4())
    hist = []
    await turn("What is service code 0153?", hist, sid)
    resp = await turn("Explain briefly", hist, sid)
    check('e2e: "Explain briefly" after an NISD lookup is prose, not a table',
          is_prose(resp), resp[:160])
    check('e2e: "Explain briefly" after an NISD lookup still names NISD / 0153',
          "0153" in resp or "NISD" in resp, resp[:160])

    # 3) Repeated explanation follow-ups retain the correct service code
    #    through a type switch (ISD -> NISD) and a chained brevity request.
    sid = str(uuid.uuid4())
    hist = []
    await turn("What is ISD?", hist, sid)
    r_detail = await turn("Explain in more detail", hist, sid)
    check('e2e: "Explain in more detail" after "What is ISD?" gives the full answer',
          not is_prose(r_detail), r_detail[:160])
    check('e2e: the detailed answer still names ISD', "ISD" in r_detail, r_detail[:160])
    r_nisd = await turn("What about NISD?", hist, sid)
    check('e2e: "What about NISD?" switches the context to 0153/NISD',
          "0153" in r_nisd or "NISD" in r_nisd, r_nisd[:160])
    r_final = await turn("Explain that in one sentence", hist, sid)
    check('e2e: "Explain that in one sentence" is prose', is_prose(r_final), r_final[:200])
    check('e2e: "Explain that in one sentence" explains NISD, not the stale ISD context',
          "0153" in r_final or "NISD" in r_final, r_final[:200])

    # 4) Ordinary service-detail queries are untouched.
    sid = str(uuid.uuid4())
    hist = []
    r1 = await turn("What is service code 0154?", hist, sid)
    check('e2e: a fresh "what is service code X" question is unaffected',
          "0154" in r1 or "ISD" in r1, r1[:160])
    r2 = await turn("Explain in detail", hist, sid)
    check('e2e: "Explain in detail" (no brief cue) still gives the full bulleted answer',
          not is_prose(r2), r2[:160])

    # 5) Application-related follow-ups and existing routing are not broken.
    sid = str(uuid.uuid4())
    hist = []
    r_apps = await turn("Show my pending applications", hist, sid)
    check('e2e: an application listing is unaffected', "applicat" in r_apps.lower(), r_apps[:160])
    r_followup = await turn("explain briefly", hist, sid)
    check('e2e: "explain briefly" after a multi-row listing asks which one, rather than crashing or dumping a code card',
          "which" in r_followup.lower() or "number" in r_followup.lower(), r_followup[:200])


if "--routing" not in sys.argv:
    asyncio.run(_e2e())
for name, detail in bad:
    print(f"FAIL {name}" + (f" -- {detail}" if detail else ""))
print(f"{total - len(bad)}/{total} ok")
sys.exit(1 if bad else 0)
