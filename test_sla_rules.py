"""Time limits: the code must say what the department documents say, at every boundary.

1. backend/utils/sla.py equals land_rules.txt (service SLA) and workflow_guide.txt (field visit)
2. service SLA state at ISD 29/30/31/34/35/36, NISD 14/15/16/19/20/21, MERGE 14/15/16 working days
3. field-visit overdue at 14/15/16 working days; NISD never; a completed visit stops the clock
4. the sentence names the limit it used (never a universal "15-day SLA")
5. the stored flag equals the rule for every application

python test_sla_rules.py
"""
import asyncio
import logging
import re
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
logging.disable(logging.CRITICAL)

from sqlalchemy import select

from backend.database import AsyncSessionLocal, engine
from backend.models import Application, FieldVisit, SISOfficer
from backend.sample_db.build_lora_dataset import build_officer_context, _flatten_html
from backend.services import chatbot, overdue_refresh
from backend.utils import sla
from backend.utils.helpers import SIS_SERVICE_CODE_DETAIL

FAILS = []
DOCS = Path("backend/documents")


def check(ok, label, detail=""):
    if not ok:
        FAILS.append(f"{label}: {detail[:200]}")


def add_wd(start: date, n: int) -> date:
    d, k = start, 0
    while k < n:
        d += timedelta(days=1)
        if d.weekday() < 5:
            k += 1
    return d


MON = date(2026, 1, 5)  # a Monday


async def main():
    # 1. the code equals the documents
    land = (DOCS / "land_rules.txt").read_text(encoding="utf-8")
    for code, t in (("0153", "NISD"), ("0154", "ISD"), ("0155", "MERGE")):
        m = re.search(rf"- {code} \(.*?SLA:\s*(\d+)(?:-(\d+))?\s+working days", land, re.S)
        check(m is not None, f"land_rules.txt states an SLA for {code}")
        if m:
            lo, hi = int(m.group(1)), int(m.group(2) or m.group(1))
            check(sla.SLA_WORKING_DAYS[t] == (lo, hi), f"sla.py {t} equals land_rules.txt", f"{sla.SLA_WORKING_DAYS[t]} vs {(lo, hi)}")
            text = SIS_SERVICE_CODE_DETAIL[code]["sla_days"]
            check(text.startswith(f"{lo}-{hi}" if lo != hi else f"{lo} "), f"helpers.py {t} SLA text equals land_rules.txt", text)
    wf = (DOCS / "workflow_guide.txt").read_text(encoding="utf-8")
    check("15 WORKING DAY FIELD VISIT DEADLINE RULE" in wf and sla.FIELD_VISIT_DEADLINE_WD == 15,
          "the 15 working day field-visit rule is in workflow_guide.txt")

    # 2. service SLA boundaries
    for t, cases in {"ISD": [(29, "within"), (30, "within"), (31, "window"), (34, "window"), (35, "window"), (36, "past")],
                     "NISD": [(14, "within"), (15, "within"), (16, "window"), (19, "window"), (20, "window"), (21, "past")],
                     "MERGE": [(14, "within"), (15, "within"), (16, "past")]}.items():
        for age, want in cases:
            got = sla.sla_state(t, age)[0]
            check(got == want, f"SLA {t} at {age} working days", f"{got} != {want}")

    # 3. field-visit overdue boundaries
    for t in ("ISD", "MERGE"):
        for age, want in ((14, False), (15, False), (16, True)):
            check(sla.field_visit_overdue(t, "pending", MON, add_wd(MON, age)) == want, f"{t} field visit at {age} wd")
    check(not sla.field_visit_overdue("NISD", "pending", MON, add_wd(MON, 60)), "NISD has no field-visit deadline")
    check(not sla.field_visit_overdue("ISD", "approved", MON, add_wd(MON, 60)), "a decided file is not overdue")
    check(not sla.field_visit_overdue("ISD", "pending", MON, add_wd(MON, 60), visit_completed_on=add_wd(MON, 10)),
          "a visit completed on day 10 stops the clock")
    check(sla.field_visit_overdue("ISD", "pending", MON, add_wd(MON, 60), visit_completed_on=add_wd(MON, 20)),
          "a visit completed on day 20 was late")

    # 4. what the sentence says
    def stmt(t, age, status="pending", tamil=False, done=None):
        return sla.age_statement("X/1", t, status, MON, add_wd(MON, age), done, tamil)
    for t, age, needle in [("ISD", 29, "within the SLA"), ("ISD", 31, "inside that window"), ("ISD", 36, "1 working days past the upper limit of 35"),
                           ("NISD", 20, "inside that window"), ("NISD", 21, "1 working days past the upper limit of 20"),
                           ("MERGE", 16, "1 working days past the upper limit of 15")]:
        out = stmt(t, age)
        check(needle in out, f"{t} at {age}: says {needle!r}", out)
        check("15-day SLA" not in out, f"{t} at {age}: no universal 15-day SLA", out)
    check("30-35" in stmt("ISD", 29) and "15-20" in stmt("NISD", 10), "the sentence names the type's own range")
    check("field visit" not in stmt("NISD", 40).lower(), "NISD sentence has no field-visit clause")
    check("marked overdue" in stmt("ISD", 20) and "marked overdue" not in stmt("ISD", 10), "ISD field-visit clause flips at day 15")
    check(stmt("ISD", 40, tamil=True).count("15") >= 1 and "வேலை நாட்கள்" in stmt("ISD", 40, tamil=True), "Tamil sentence carries the limits")

    # 5. the stored flag equals the rule for every application, and chat answers agree
    async with AsyncSessionLocal() as db:
        await overdue_refresh.refresh_overdue_flags(db)
        done = {}
        for aid, when in (await db.execute(select(FieldVisit.application_id, FieldVisit.scheduled_date)
                                           .where(FieldVisit.status == "completed"))).all():
            done[aid] = min(when, done.get(aid, when)) if when else done.get(aid)
        today = date.today()
        wrong = []
        for a in (await db.execute(select(Application))).scalars().all():
            if a.is_overdue != sla.field_visit_overdue(a.application_type, a.current_status, a.submission_date, today, done.get(a.id)):
                wrong.append(a.application_number)
        check(not wrong, "stored is_overdue equals the documented rule", str(wrong[:3]))

        from backend.services import rag

        class Stub:
            temperature = 0.1
            def bind(self, **k): raise RuntimeError("stub")
            def bind_tools(self, *a, **k): raise RuntimeError("stub")
            async def ainvoke(self, *a, **k):
                class R: content = "[[LLM]]"
                return R()
        rag.llm = Stub()
        row = (await db.execute(select(SISOfficer).where(SISOfficer.email == "msivakumar@sis.tn.gov.in"))).scalars().first()
        ctx = await build_officer_context(db, row)
        for no, needle in (("2026/0154/28/001197", "30-35"), ("2026/0153/28/001839", "15-20")):
            r = await chatbot.process_chat(f"how long has {no} been pending", str(uuid.uuid4()), ctx, db, chat_history=[])
            t = _flatten_html(r.get("response") or "")
            check(needle in t and "15-day SLA" not in t, f"chat: {no} pending age uses {needle}", t[:160])
        r = await chatbot.process_chat("show overdue applications", str(uuid.uuid4()), ctx, db, chat_history=[])
        t = _flatten_html(r.get("response") or "") + str(r.get("table_data"))
        flagged = (await db.execute(select(Application.application_number).where(
            Application.is_overdue == True, Application.assigned_officer_id == row.id))).scalars().all()  # noqa: E712
        check(all(n in t for n in flagged), "overdue list carries every flagged application of the officer", str(flagged))
    await engine.dispose()
    print("ALL PASSED" if not FAILS else f"FAILED ({len(FAILS)}):\n  - " + "\n  - ".join(FAILS[:30]))
    return 0 if not FAILS else 1

raise SystemExit(asyncio.run(main()))
