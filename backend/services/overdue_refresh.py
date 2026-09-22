"""Keep `applications.is_overdue` true to today's date.

The flag was computed once, on the day the projection was built, so every day after that
it drifted: an ISD file pending for 94 days was "overdue" in the field-visit answer and
absent from "show overdue applications". Overdue is a fact about TODAY, so it is
re-derived here -- at start-up and on a timer -- outside any chat turn (a chat turn is
read-only, see readonly_guard).

Rule (workflow_guide.txt), implemented in backend/utils/sla.py: an open ISD or MERGE
application whose field visit has not been completed is overdue once more than 15 working
days have passed since submission. A completed visit stops the clock at the visit date.
This is the FIELD-VISIT deadline; the service SLA (ISD 30-35, NISD 15-20) is a separate
limit and is never used to set this flag.
"""
import asyncio
from datetime import date

from sqlalchemy import select, update

from backend.models import Application, FieldVisit
from backend.utils.logger import get_logger
from backend.utils.sla import field_visit_overdue

logger = get_logger(__name__)

REFRESH_EVERY_SECONDS = 6 * 3600


async def refresh_overdue_flags(db, today: date | None = None) -> int:
    """Recompute the flag for every application; returns how many changed."""
    today = today or date.today()
    apps = (await db.execute(
        select(Application.id, Application.application_type, Application.submission_date,
               Application.current_status, Application.is_overdue))).all()
    done = {}
    for app_id, when in (await db.execute(
            select(FieldVisit.application_id, FieldVisit.scheduled_date)
            .where(FieldVisit.status == "completed"))).all():
        if when and (app_id not in done or when < done[app_id]):
            done[app_id] = when
    turn_on, turn_off = [], []
    for app_id, atype, sub, status, current in apps:
        want = field_visit_overdue(atype, status, sub, today, done.get(app_id))
        if want and not current:
            turn_on.append(app_id)
        elif not want and current:
            turn_off.append(app_id)
    for ids, value in ((turn_on, True), (turn_off, False)):
        if ids:
            await db.execute(update(Application).where(Application.id.in_(ids)).values(is_overdue=value))
    await db.commit()
    changed = len(turn_on) + len(turn_off)
    logger.info(f"overdue flags refreshed: {len(turn_on)} on, {len(turn_off)} off")
    return changed


async def refresh_forever(session_factory) -> None:
    while True:
        try:
            async with session_factory() as db:
                await refresh_overdue_flags(db)
        except Exception as exc:  # never take the server down for a housekeeping job
            logger.error(f"overdue refresh failed: {exc}")
        await asyncio.sleep(REFRESH_EVERY_SECONDS)
