"""Regression: role-name typo tolerance for "Sub Inspector Surveyor" (SIS)
and "Deputy Inspector Surveyor" (DIS) in the glossary trigger
(backend/services/chatbot.py's `_GLOSSARY_RE` / `_GLOSSARY_TERMS` /
`_GLOSSARY_ALIAS`), part of the production-readiness mega-task's typo/noise
section.

Confirmed defect, reproduced live: "who is the deputy inspecter" (a one-
letter typo on "inspector", plus dropping "surveyor" -- both completely
ordinary ways an officer actually types this) did not match
`_GLOSSARY_TERMS`'s literal-phrase-only alternative
`deputy\\s+inspector\\s+surveyor` at all. Unlike every other role in this
glossary (ZDT/Tahsildar had misspellings added as literal alternatives
earlier this session; "draughtsman"/"draftsman" already covers the spelling
variant), "deputy inspector surveyor" and "sub inspector surveyor" required
the exact phrase, so the question fell through to a completely different,
unrelated handler (officer-directory) and answered: "The Deputy Inspector
Surveyor (DIS) is not part of your jurisdiction. You can only see records
for Sub-Inspector Surveyor (SIS) officers." -- a confusing, wrong answer to
a basic "what is DIS" role-definition question that named no application,
officer or jurisdiction scope at all.

Fix: `_GLOSSARY_TERMS` widened to `(?:sub|deputy)\\s+inspect(?:or|er)
(?:\\s+survey(?:or|er))?`, tolerating the "inspector"/"inspecter" and
"surveyor"/"surveyer" spellings and treating "surveyor" as optional (an
officer saying "the deputy inspector" bare is still clearly asking about
DIS). `_GLOSSARY_ALIAS` is populated for every resulting combination by a
small loop rather than hand-enumerated, so the alias table can't drift out
of sync with the regex the way a hand-written list eventually would.

Run: python test_dis_sis_glossary_typos.py
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


# ---- unit: the widened glossary regex + alias resolve every combination ----
from backend.services.chatbot import _GLOSSARY_RE, _GLOSSARY_ALIAS, _GLOSSARY

_CASES = [
    ("who is the deputy inspecter", "dis"),           # typo + no "surveyor" -- the reported defect
    ("who is the deputy inspector", "dis"),           # correct spelling, no "surveyor" -- must still resolve
    ("what is a deputy inspector surveyor", "dis"),
    ("what is a deputy inspecter surveyer", "dis"),
    ("who is the sub inspecter", "sis"),
    ("who is the sub inspector surveyer", "sis"),
    ("what is a sub inspector surveyor", "sis"),
]
for msg, expected in _CASES:
    m = _GLOSSARY_RE.match(msg.strip())
    key = re.sub(r"[\s-]", "", m.group(1).lower()) if m else None
    resolved = _GLOSSARY_ALIAS.get(key, key) if key else None
    check(f'"{msg}" resolves to the "{expected}" glossary entry',
          m is not None and resolved == expected, f"matched={key!r} resolved={resolved!r}")
    if resolved:
        check(f'... and "{resolved}" is an actual glossary key', resolved in _GLOSSARY)

# A genuine officer-directory question (names a specific officer or asks to
# LIST officers) must still NOT be swallowed by the widened glossary trigger.
check('"list all deputy inspector officers in my jurisdiction" is NOT '
      "claimed by the glossary (it is a listing, not a definition request)",
      _GLOSSARY_RE.match("list all deputy inspector officers in my jurisdiction".strip()) is None)


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
        return strip_tags(r.get("response") or "")

    r1 = await ask("who is the deputy inspecter")
    check("live: the typo'd question names the Deputy Inspector Surveyor "
          "role, never a jurisdiction refusal",
          "Deputy Inspector Surveyor" in r1 and "jurisdiction" not in r1.lower(), r1)

    r2 = await ask("who is the sub inspecter")
    check("live: the SIS typo question names the Sub Inspector Surveyor role",
          "Sub Inspector Surveyor" in r2, r2)


asyncio.run(_e2e())
print(f"{total - len(bad)}/{total} ok")
sys.exit(1 if bad else 0)
