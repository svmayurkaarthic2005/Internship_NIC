"""Time limits, taken from the department documents and kept in one place.

Two DIFFERENT clocks, which the code used to run together ("15-day SLA" for every file):

1. Field-visit deadline (workflow_guide.txt, "15 WORKING DAY FIELD VISIT DEADLINE RULE"):
   an ISD or MERGE file must have its field visit completed within 15 working days of
   submission. Past that the file is marked OVERDUE. NISD has no field visit.

2. Service SLA (land_rules.txt, "SERVICE FEE AND SLA BY SERVICE CODE"), in working days
   from submission to completion:
       NISD  (0153)  15-20
       ISD   (0154)  30-35
       MERGE (0155)  15
   The documents give a range, not a point. This module never invents one: a file is
   "within" the SLA up to the lower figure, "in the SLA window" between the two, and is
   called past the SLA only after the UPPER figure.

`working_days_between` counts Monday-Friday; the register holds no holiday calendar, so
public holidays are not excluded and the answers say so.
"""
from datetime import date, timedelta
from typing import Optional, Tuple

SLA_WORKING_DAYS = {"NISD": (15, 20), "ISD": (30, 35), "MERGE": (15, 15)}
FIELD_VISIT_TYPES = ("ISD", "MERGE")
FIELD_VISIT_DEADLINE_WD = 15
OPEN_STATUSES = ("pending", "in_progress", "escalated")


def working_days_between(start: date, end: date) -> int:
    days, cur = 0, start
    while cur < end:
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            days += 1
    return days


def sla_state(app_type: Optional[str], age_wd: int) -> Tuple[str, int, int]:
    """("within" | "window" | "past", lower, upper) for a file `age_wd` working days old."""
    lo, hi = SLA_WORKING_DAYS.get((app_type or "").upper(), (15, 15))
    if age_wd <= lo:
        return "within", lo, hi
    if age_wd <= hi:
        return "window", lo, hi
    return "past", lo, hi


def field_visit_overdue(app_type: Optional[str], status: Optional[str], submitted: Optional[date],
                        today: date, visit_completed_on: Optional[date] = None) -> bool:
    """The documented overdue rule: an open ISD / MERGE file whose field visit is not
    completed more than 15 working days after submission. A completed visit stops the
    clock on the day it was done."""
    if (app_type or "").upper() not in FIELD_VISIT_TYPES or status not in OPEN_STATUSES or not submitted:
        return False
    end = min(visit_completed_on, today) if visit_completed_on else today
    return working_days_between(submitted, end) > FIELD_VISIT_DEADLINE_WD


def age_statement(app_no: str, app_type: Optional[str], status: Optional[str], submitted: Optional[date],
                  today: date, visit_completed_on: Optional[date] = None, tamil: bool = False) -> str:
    """How old a file is, against BOTH documented limits, saying which limit each is."""
    t = (app_type or "").upper() or "this"
    age = working_days_between(submitted, today)
    state, lo, hi = sla_state(app_type, age)
    rng = f"{lo}" if lo == hi else f"{lo}-{hi}"
    if tamil:
        head = (f"விண்ணப்பம் {app_no} ({t}) {submitted.isoformat()} அன்று சமர்ப்பிக்கப்பட்டது; "
                f"இப்போது {age} வேலை நாட்கள் ஆகிறது (சனி, ஞாயிறு தவிர; அரசு விடுமுறைகள் கணக்கிடப்படவில்லை). ")
        if state == "within":
            sla = f"{t} சேவை கால வரம்பு {rng} வேலை நாட்கள் — வரம்பிற்குள் உள்ளது. "
        elif state == "window":
            sla = f"{t} சேவை கால வரம்பு {rng} வேலை நாட்கள் — இப்போது அந்த வரம்பின் இடைவெளியில் உள்ளது ({hi}-ஐத் தாண்டினால் மட்டுமே வரம்பு மீறல்). "
        else:
            sla = f"{t} சேவை கால வரம்பின் உச்ச வரம்பு {hi} வேலை நாட்கள்; இது {age - hi} வேலை நாட்கள் மீறியுள்ளது. "
        fv = ""
        if (app_type or "").upper() in FIELD_VISIT_TYPES and status in OPEN_STATUSES:
            over = field_visit_overdue(app_type, status, submitted, today, visit_completed_on)
            if visit_completed_on:
                fv = f"கள ஆய்வு {visit_completed_on.isoformat()} அன்று முடிந்தது."
            elif over:
                fv = (f"கள ஆய்வு சமர்ப்பித்த 15 வேலை நாட்களுக்குள் முடிந்திருக்க வேண்டும்; அது {age - FIELD_VISIT_DEADLINE_WD} "
                      f"வேலை நாட்கள் முன்பே கடந்துவிட்டது, ஆய்வு முடிந்ததாகப் பதிவில்லை — எனவே தாமதம் (overdue).")
            else:
                fv = f"கள ஆய்வு காலக்கெடுவுக்கு (15 வேலை நாட்கள்) இன்னும் {FIELD_VISIT_DEADLINE_WD - age} வேலை நாட்கள் உள்ளன."
        return head + sla + fv
    head = (f"Application {app_no} ({t}) was submitted on {submitted.isoformat()} and is {age} working days old "
            f"(Monday-Friday; public holidays are not excluded). ")
    if state == "within":
        sla = f"The {t} service SLA is {rng} working days, so it is within the SLA. "
    elif state == "window":
        sla = (f"The {t} service SLA is {rng} working days; it is now inside that window "
               f"(the SLA is breached only after working day {hi}). ")
    else:
        sla = (f"The {t} service SLA is {rng} working days; it is {age - hi} working days past the upper "
               f"limit of {hi}. ")
    fv = ""
    if (app_type or "").upper() in FIELD_VISIT_TYPES and status in OPEN_STATUSES:
        if visit_completed_on:
            fv = f"Its field visit was completed on {visit_completed_on.isoformat()}."
        elif field_visit_overdue(app_type, status, submitted, today, None):
            fv = (f"Separately, its field visit had to be completed within {FIELD_VISIT_DEADLINE_WD} working days of "
                  f"submission; that deadline passed {age - FIELD_VISIT_DEADLINE_WD} working days ago and no completed "
                  f"visit is recorded, so it is marked overdue.")
        else:
            fv = (f"Its field visit deadline ({FIELD_VISIT_DEADLINE_WD} working days from submission) has "
                  f"{FIELD_VISIT_DEADLINE_WD - age} working days left.")
    return (head + sla + fv).strip()
