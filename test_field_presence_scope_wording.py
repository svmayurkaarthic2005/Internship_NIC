"""Regression for a garbled-sentence bug in `_render_field_presence`'s standalone
(register-wide / awaiting-visit / overdue-visit / service-code) answers.

`_standalone_field_presence_answer` passed a full noun phrase as `scope_label_en`
("the applications in your jurisdiction", "the 0158 (...) applications", "the
applications awaiting a visit", "the overdue visits"). `_render_field_presence`'s
three sentence templates all then spliced the row COUNT and the literal word
"application(s)" in right after that phrase, producing e.g.:

    "13 of the applications in your jurisdiction 126 application(s) have a
     scheduled field visit: (106 not applicable for this field.)"

-- grammatically broken, and silently swallowed the fact that this also meant
the "pending"/"ISD" part of "which of my pending ISD applications have a field
visit scheduled" was never scoped by this code path at all (the whole
jurisdiction's 126 rows were checked, not the 8 pending ISD ones) -- a separate,
larger scoping gap this repro surfaced but does not fix.

Fix: `scope_label_en` is now always a bare DETERMINER ("the") and a new
`scope_suffix_en` parameter carries the trailing qualifier, appended AFTER the
literal "application(s)" instead of mixed in before the count. Tamil wording is
untouched (its templates were not broken the same way).

Needs the live DB (field-presence data) + an officer context, no LLM:
    python test_field_presence_scope_wording.py
"""
from __future__ import annotations
import asyncio, os, sys

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

from sqlalchemy import select
from backend.database import AsyncSessionLocal
from backend.models import SISOfficer
from backend.schemas import OfficerContext
from backend.services import chatbot
from backend.services.auth_service import get_officer_jurisdiction_ids

OFFICER_EMAIL = "marun_chn@sis.tn.gov.in"
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


async def get_ctx():
    async with AsyncSessionLocal() as db:
        res = await db.execute(select(SISOfficer).where(SISOfficer.email == OFFICER_EMAIL))
        officer = res.scalar_one()
        jur = await get_officer_jurisdiction_ids(officer.id, db)
        ids = (jur["district_ids"] + jur["taluk_ids"] + jur["town_ids"] + jur["ward_ids"] + jur["block_ids"])
        return OfficerContext(
            officer_id=officer.id, employee_id=officer.employee_id, name=officer.name,
            email=officer.email, designation=officer.designation,
            jurisdiction_type=jur["jurisdiction_type"], jurisdiction_name=jur["jurisdiction_name"],
            jurisdiction_ids=[i for i in ids if i])


async def ask(ctx, message, sid):
    async with AsyncSessionLocal() as db:
        r = await chatbot.process_chat(db=db, message=message, officer=ctx, chat_history=[], session_id=sid)
    return (r.get("response") if isinstance(r, dict) else r) or ""


# The exact broken shape must never recur: a count immediately followed by the
# literal word "application(s)" immediately followed by another digit (the
# fingerprint of the old bug splicing a second number in right after it).
import re
_BROKEN_SHAPE_RE = re.compile(r"application\(s\)\s+\d")


async def main():
    ctx = await get_ctx()

    r1 = await ask(ctx, "which applications have a scheduled field visit?",
                   "a1111111-1111-1111-1111-111111111111")
    check('register-wide: no garbled "application(s) <digit>" shape', not _BROKEN_SHAPE_RE.search(r1), r1[:160])
    check('register-wide: reads "of the N application(s) in your jurisdiction"',
          re.search(r"of the \d+ application\(s\) in your jurisdiction", r1) is not None, r1[:160])

    r2 = await ask(ctx, "which service code 0158 applications have no subdivision recorded?",
                   "a2222222-2222-2222-2222-222222222222")
    check('service-code-scoped: no garbled shape', not _BROKEN_SHAPE_RE.search(r2), r2[:160])
    check('service-code-scoped: reads "application(s) under 0158"',
          "application(s) under 0158" in r2, r2[:160])

    r3 = await ask(ctx, "which applications awaiting a visit have no subdivision recorded?",
                   "a3333333-3333-3333-3333-333333333333")
    check('awaiting-visit-scoped: no garbled shape', not _BROKEN_SHAPE_RE.search(r3), r3[:160])
    check('awaiting-visit-scoped: reads "application(s) awaiting a visit"',
          "application(s) awaiting a visit" in r3, r3[:160])

    # Defect B: status ("pending") and ISD/NISD type named in the message were
    # silently dropped by the register-wide branch -- "which of my pending ISD
    # applications have a field visit scheduled" was answered over the WHOLE
    # jurisdiction (126 rows) instead of the pending-ISD ones actually named.
    r4 = await ask(ctx, "which of my pending ISD applications have a field visit scheduled",
                   "a4444444-4444-4444-4444-444444444444")
    check('status+type-scoped: no garbled shape', not _BROKEN_SHAPE_RE.search(r4), r4[:160])
    check('status+type-scoped: names "pending ISD" in the scope, not the whole jurisdiction unscoped',
          "pending ISD" in r4, r4[:200])
    _scope_n = re.search(r"of the (\d+) application\(s\)", r4)
    check('status+type-scoped: scoped population is far smaller than the whole jurisdiction (126)',
          bool(_scope_n) and int(_scope_n.group(1)) < 50, r4[:200])

    print(f"{total - len(bad)}/{total} ok")
    sys.exit(1 if bad else 0)


asyncio.run(main())
