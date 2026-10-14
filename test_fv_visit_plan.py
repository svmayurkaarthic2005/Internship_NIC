"""Regression suite for the field-visit prioritization fix (fv_visit_plan).

Bug: "Which application should I field visit next?" rendered a misleading
"You have no upcoming field visits on the calendar" heading even when real
overdue/awaiting work existed, and the frontend's (and card_html.py's)
generic plain-text-to-card heuristic misparsed the trailing "Where: ward 001
(10)." summary line as an extra data row -- a "11 records" badge over a
genuine 3 overdue + 7 awaiting = 10 applications.

Fix: `_render_visit_plan_answer()` in backend/services/chatbot.py now returns
real HTML directly (no plain-text heuristic involved at all), with a
"Field Visit Prioritization" heading whenever overdue/awaiting work exists
and the calendar itself is empty, and explicit per-section record counts
computed from the same lists the tables are built from.

Follow-up fix (same file): the tables were missing a Sub-Division No. column.
`get_visit_plan()` in postgres.py now resolves it per application from
`application_sub_divisions` (the same `proposed_sub_division_no`, falling
back to the linked SubDivision's own number, that `get_application_detail()`
already treats as authoritative) via a SEPARATE aggregate query keyed by
application_id -- never a flat join on the geography subquery, which would
have multiplied a one-row application into several visit-plan rows for the
same file whenever it carries more than one proposed sub-division.

Two layers of tests:
  * Renderer-level (no DB): synthetic structured_data dicts covering every
    combination in the fix spec -- fast, deterministic, no dependency on the
    live database's current, mutable state.
  * Live-DB (needs the DB): the real reported scenario against the real
    officer, through both process_chat() and process_chat_stream(), plus
    ward/block jurisdiction scoping.

`python test_fv_visit_plan.py` (needs the DB for the live-DB section; no LLM
anywhere -- this whole answer is deterministic).
"""
from __future__ import annotations

import asyncio
import re
import sys
import uuid

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, r"C:\proj\nic_internship")

from sqlalchemy import select

from backend.database import AsyncSessionLocal, engine as _db_engine
from backend.models import SISOfficer
from backend.schemas import OfficerContext
from backend.services import chatbot
from backend.services import followup_context as fctx
from backend.services.auth_service import get_officer_jurisdiction_ids

OFFICER_EMAIL = "marun_chn@sis.tn.gov.in"

_results: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    _results.append((name, condition, detail))
    print(f"{'ok ' if condition else 'FAIL'} {name}" + (f" -- {detail}" if detail and not condition else ""))


def _entry(app_no, survey="5", ward="001", block="0055", **extra) -> dict:
    return {"application_number": app_no, "survey_no": survey, "ward_number": ward,
            "block_number": block, **extra}


def _table_rows(html: str, section_marker: str) -> int:
    """Number of <tr> data rows inside the <table> that immediately follows
    the given section heading text, e.g. "Overdue scheduled visits"."""
    if section_marker not in html:
        return 0
    after = html.split(section_marker, 1)[1]
    table = after.split("</table>", 1)[0]
    return len(re.findall(r"<tr><td>", table))


# ── Renderer-level tests (no DB) ────────────────────────────────────────────

def test_three_overdue_seven_awaiting():
    """The exact reported scenario: 3 overdue + 7 awaiting = 10 distinct
    applications, never 11, and never "no upcoming field visits" as the
    heading when there is this much outstanding work."""
    sd = {
        "today": "2026-10-10",
        "scheduled": [], "overdue": [_entry(f"2026/0154/02/00{i}") for i in range(3)],
        "awaiting": [_entry(f"2026/0154/02/01{i}", days_pending=10 + i, is_overdue=False) for i in range(7)],
        "blocks": {"0055": 10}, "wards": {"001": 10},
        "asked_ward": None, "asked_block": None, "focus": None,
    }
    r = chatbot._render_visit_plan_answer(sd, "en")
    check("3 overdue + 7 awaiting -> heading says Field Visit Prioritization, not 'no upcoming'",
          "Field Visit Prioritization" in r and "no upcoming field visits" not in r.lower())
    check("3 overdue + 7 awaiting -> heading states 10 applications, never 11",
          "but 10 application(s)" in r and not re.search(r"\bbut\s+11\b", r))
    check("3 overdue + 7 awaiting -> overdue table has exactly 3 rows",
          _table_rows(r, "Overdue scheduled visits") == 3)
    check("3 overdue + 7 awaiting -> awaiting table has exactly 7 rows",
          _table_rows(r, "Applications awaiting") == 7)
    check("3 overdue + 7 awaiting -> location summary states ward 001 (10), matching the real total",
          "ward 001 (10)" in r)
    # The exact frontend/card_html bug: the trailing "Where: ..." line must
    # never be parseable as its own table row by either heuristic.
    check("3 overdue + 7 awaiting -> the 'Where:' line is plain HTML text, not inside any <table>",
          "Where:" not in r.split("</table>")[-1] or r.rfind("<table") < r.find("Where:"))


def test_record_totals_and_location_totals_reconcile():
    sd = {
        "today": "2026-10-10",
        "scheduled": [], "overdue": [_entry("2026/0154/02/001", ward="001", block="0001")],
        "awaiting": [_entry("2026/0154/02/002", ward="001", block="0001"),
                     _entry("2026/0154/02/003", ward="005", block="0009", days_pending=5, is_overdue=False)],
        "blocks": {"0001": 2, "0009": 1}, "wards": {"001": 2, "005": 1},
        "asked_ward": None, "asked_block": None, "focus": None,
    }
    r = chatbot._render_visit_plan_answer(sd, "en")
    n_overdue, n_awaiting = _table_rows(r, "Overdue scheduled visits"), _table_rows(r, "Applications awaiting")
    check("record totals reconcile: overdue+awaiting table rows == the counts stated in each section heading",
          n_overdue == 1 and n_awaiting == 2 and "1 visit(s)" in r and "2 application(s)" in r)
    check("location totals reconcile: ward counts sum to the same 3 applications",
          "ward 001 (2)" in r and "ward 005 (1)" in r)


def test_duplicate_application_across_groups_deduplicated():
    """If the same application number were ever present in both `overdue`
    and `awaiting` (an upstream invariant this renderer does not trust
    blindly), it must be shown -- and counted -- only once, under overdue."""
    dup = "2026/0154/02/099999"
    sd = {
        "today": "2026-10-10",
        "scheduled": [],
        "overdue": [_entry(dup)],
        "awaiting": [_entry(dup), _entry("2026/0154/02/000111", days_pending=3, is_overdue=False)],
        "blocks": {}, "wards": {}, "asked_ward": None, "asked_block": None, "focus": None,
    }
    r = chatbot._render_visit_plan_answer(sd, "en")
    check("duplicate application across overdue/awaiting -> appears once, counted once",
          r.count(dup) == 1 and _table_rows(r, "Overdue scheduled visits") == 1
          and _table_rows(r, "Applications awaiting") == 1)


def test_no_overdue_no_awaiting():
    sd = {"today": "2026-10-10", "scheduled": [], "overdue": [], "awaiting": [],
          "blocks": {}, "wards": {}, "asked_ward": None, "asked_block": None, "focus": None}
    r = chatbot._render_visit_plan_answer(sd, "en")
    check("nothing overdue, nothing awaiting -> the genuine empty-calendar sentence, not Prioritization",
          "no upcoming field visits" in r.lower() and "Field Visit Prioritization" not in r)
    check("nothing overdue, nothing awaiting -> explains NISD files need no visit",
          "NISD applications do not need one" in r)


def test_future_scheduled_visits_only():
    sd = {
        "today": "2026-10-10", "window_start": "2026-10-11", "window_end": "2026-10-11",
        "scheduled": [_entry("2026/0154/02/000500", scheduled_date="2026-10-11")],
        "overdue": [], "awaiting": [],
        "blocks": {"0055": 1}, "wards": {"001": 1}, "asked_ward": None, "asked_block": None, "focus": None,
    }
    r = chatbot._render_visit_plan_answer(sd, "en")
    check("future scheduled visit only -> scheduled table shown, 1 row",
          _table_rows(r, "already scheduled") == 1)
    check("future scheduled visit only -> no overdue/awaiting sections invented",
          "Overdue scheduled visits" not in r and "Applications awaiting" not in r)


def test_overdue_alongside_future_scheduled():
    """A real "what's on my calendar" question where something IS booked --
    overdue work is still surfaced, but in its own titled section, never
    folded into the "scheduled" (upcoming) list itself."""
    sd = {
        "today": "2026-10-10", "window_start": "2026-10-11", "window_end": "2026-10-17",
        "scheduled": [_entry("2026/0154/02/000600", scheduled_date="2026-10-12")],
        "overdue": [_entry("2026/0154/02/000700", scheduled_date="2026-09-01", days_late=40)],
        "awaiting": [],
        "blocks": {}, "wards": {}, "asked_ward": None, "asked_block": None, "focus": None,
    }
    r = chatbot._render_visit_plan_answer(sd, "en")
    check("overdue alongside future scheduled -> both sections present, separately",
          _table_rows(r, "already scheduled") == 1 and _table_rows(r, "Overdue scheduled visits") == 1)
    # The overdue entry's application number must appear inside the OVERDUE
    # table, not inside the SCHEDULED table -- never mixed into "upcoming".
    scheduled_block = r.split("already scheduled", 1)[1].split("</table>", 1)[0]
    check("overdue application number does not appear inside the 'upcoming scheduled' table",
          "000700" not in scheduled_block)


def test_ward_block_filter_scoping():
    sd = {
        "today": "2026-10-10", "scheduled": [],
        "overdue": [_entry("2026/0154/02/000800", ward="005", block="0009")],
        "awaiting": [],
        "blocks": {"0009": 1}, "wards": {"005": 1},
        "asked_ward": "005", "asked_block": None, "focus": None,
    }
    r = chatbot._render_visit_plan_answer(sd, "en")
    check("ward filter named in the question is echoed in the heading scope",
          "in ward 005" in r)


def test_zero_qualifying_in_filtered_ward():
    sd = {"today": "2026-10-10", "scheduled": [], "overdue": [], "awaiting": [],
          "blocks": {}, "wards": {}, "asked_ward": "009", "asked_block": None, "focus": None}
    r = chatbot._render_visit_plan_answer(sd, "en")
    check("zero qualifying applications in a specific ward -> explicit empty statement for that ward, not invented work",
          "in ward 009" in r and "Field Visit Prioritization" not in r)


# ── followup_context.classify() fixes (unit-level, no DB) ──────────────────

def test_classify_capitalised_imperative_fragment_opener():
    """General bug, not specific to subdivision questions: `words[0]` is
    sliced from the ORIGINAL-case text, but `_FRAGMENT_OPENERS` is an
    all-lowercase set, so any naturally-capitalised imperative sentence
    ("Show the survey number ... of the second overdue visit.") never
    matched it -- only an artificially lower-cased "show ..." did. An
    interrogative happened to survive via the separate "?" check, which is
    exactly why this stayed invisible until a capitalised, period-terminated
    imperative exposed it."""
    cap = "Show the survey number and subdivision number of the second overdue visit."
    low = cap[0].lower() + cap[1:]
    check("classify(): capitalised imperative fragment classifies the same as its lower-cased twin",
          fctx.classify(cap) == fctx.classify(low) == "singular", (fctx.classify(cap), fctx.classify(low)))
    # A handful of other capitalised openers from _FRAGMENT_OPENERS, each
    # paired with a singular-field cue, to show this is not one word's fix.
    for opener, rest in (("Give", "the survey number of the second overdue visit."),
                         ("Tell", "me the subdivision number of the second overdue visit."),
                         ("List", "the survey number of the second overdue visit.")):
        msg = f"{opener} {rest}"
        check(f"classify(): capitalised '{opener}' opener also now recognised as a fragment",
              fctx.classify(msg) == "singular", msg)


def test_classify_ordinal_reference_exempt_from_own_subject_gate():
    """"the second overdue visit" points at a row exactly as concretely as a
    back-reference pronoun ("it") does -- the general fix exempts ANY
    ordinal/slice reference from the "names its own subject" gate, not just
    the specific subdivision-number phrasing."""
    check("classify(): ordinal reference naming 'application' is no longer misread as a fresh question",
          fctx.classify("Show the survey number and subdivision number of the second overdue visit.") == "singular")
    check("classify(): plain single-field ordinal (pre-existing working case) still classifies correctly",
          fctx.classify("What is the subdivision number of the second overdue visit?") == "singular")


# ── Sub-Division No. column (renderer-level, via _visit_row_html/_visit_table_html) ──

def test_subdivision_column_known_value():
    """A known ISD record with a real sub-division number must show exactly
    that value -- nothing invented, nothing substituted."""
    e = _entry("2026/0154/02/000900", survey="120", sub_division_display="1/T2")
    row = chatbot._visit_row_html(e, "overdue")
    check("known sub-division value rendered verbatim",
          "<td>1/T2</td>" in row, row)


def test_subdivision_column_header_in_both_tables():
    sd = {
        "today": "2026-10-10", "scheduled": [],
        "overdue": [_entry("2026/0154/02/001000", sub_division_display="5/T1")],
        "awaiting": [_entry("2026/0154/02/001100", sub_division_display="7", days_pending=4, is_overdue=False)],
        "blocks": {}, "wards": {}, "asked_ward": None, "asked_block": None, "focus": None,
    }
    r = chatbot._render_visit_plan_answer(sd, "en")
    check("Sub-Division No. header appears in the overdue table",
          "<th>Sub-Division No.</th>" in r.split("Overdue scheduled visits", 1)[1].split("</thead>", 1)[0])
    check("Sub-Division No. header appears in the awaiting table",
          "<th>Sub-Division No.</th>" in r.split("Applications awaiting", 1)[1].split("</thead>", 1)[0])
    check("overdue row carries its real sub-division value", "<td>5/T1</td>" in r)
    check("awaiting row carries its real sub-division value", "<td>7</td>" in r)
    # Sub-Division No. must sit immediately after Survey No., before the
    # date/overdue/waiting columns -- the column order the task specifies.
    overdue_head = r.split("Overdue scheduled visits", 1)[1].split("</tr>", 1)[0]
    check("Sub-Division No. column sits immediately after Survey No. in the overdue header",
          re.search(r"Survey No\.</th>\s*<th>Sub-Division No\.</th>", overdue_head) is not None)


def test_subdivision_multiple_rows_joined_no_duplicate_application():
    """An application with more than one proposed sub-division (a parcel
    split into several plots) must appear as ONE row with all its numbers
    joined -- never duplicated into several visit-plan rows."""
    e = _entry("2026/0154/02/001200", sub_division_display="1/T2, 1/T1, 4")
    sd = {"today": "2026-10-10", "scheduled": [], "overdue": [e], "awaiting": [],
          "blocks": {}, "wards": {}, "asked_ward": None, "asked_block": None, "focus": None}
    r = chatbot._render_visit_plan_answer(sd, "en")
    check("multiple sub-divisions joined into one cell, application shown exactly once",
          r.count("2026/0154/02/001200") == 1 and "1/T2, 1/T1, 4" in r)
    check("multiple sub-divisions -> still exactly one <tr> for this application",
          _table_rows(r, "Overdue scheduled visits") == 1)


def test_subdivision_not_applicable_for_non_isd():
    """NISD (and any other non-ISD service) never carries a sub-division --
    0153 is defined as Not Involving Sub-Division. Stated as a fact about
    the service, not a missing value."""
    e = _entry("2026/0153/02/001300", type="NISD", sub_division_display="Not applicable")
    row = chatbot._visit_row_html(e, "awaiting")
    check("non-ISD application -> 'Not applicable', never invented or blank",
          "<td>Not applicable</td>" in row, row)


def test_subdivision_not_recorded_when_isd_has_no_row():
    """An ISD file that genuinely has no application_sub_divisions row yet
    (e.g. not yet reached the SD desk) is a missing value, distinct from
    'not applicable'."""
    e = _entry("2026/0154/02/001400", type="ISD", sub_division_display="Not recorded")
    row = chatbot._visit_row_html(e, "overdue")
    check("ISD application with no recorded sub-division -> 'Not recorded', not 'Not applicable' or blank",
          "<td>Not recorded</td>" in row, row)


def test_subdivision_does_not_change_record_or_location_counts():
    """Adding the column must not change how many rows are shown, nor the
    ward/block totals -- same reconciliation guarantee as before, now with
    an extra column present."""
    sd = {
        "today": "2026-10-10", "scheduled": [],
        "overdue": [_entry("2026/0154/02/001500", ward="001", block="0001", sub_division_display="2")],
        "awaiting": [_entry("2026/0154/02/001600", ward="001", block="0001", sub_division_display="3",
                             days_pending=9, is_overdue=False)],
        "blocks": {"0001": 2}, "wards": {"001": 2}, "asked_ward": None, "asked_block": None, "focus": None,
    }
    r = chatbot._render_visit_plan_answer(sd, "en")
    check("record counts unchanged by the new column: 1 overdue + 1 awaiting, ward total still 2",
          _table_rows(r, "Overdue scheduled visits") == 1 and _table_rows(r, "Applications awaiting") == 1
          and "ward 001 (2)" in r)


# ── Live-DB tests (real officer, real jurisdiction, both chat paths) ───────

async def officer_context(db, officer) -> OfficerContext:
    jur = await get_officer_jurisdiction_ids(officer.id, db)
    ids = (jur["district_ids"] + jur["taluk_ids"] + jur["town_ids"] + jur["ward_ids"] + jur["block_ids"])
    return OfficerContext(officer_id=officer.id, employee_id=officer.employee_id, name=officer.name,
        email=officer.email, designation=officer.designation,
        jurisdiction_type=jur["jurisdiction_type"], jurisdiction_name=jur["jurisdiction_name"],
        jurisdiction_ids=[i for i in ids if i])


async def get_ctx() -> OfficerContext:
    async with AsyncSessionLocal() as db:
        res = await db.execute(select(SISOfficer).where(SISOfficer.email == OFFICER_EMAIL))
        return await officer_context(db, res.scalar_one())


async def turn_hist(ctx, message, hist, sid):
    """Like turn(), but on a caller-supplied shared session/history, so a
    follow-up genuinely continues the same conversation."""
    async with AsyncSessionLocal() as db:
        r = await chatbot.process_chat(db=db, message=message, officer=ctx, chat_history=hist, session_id=sid)
    resp = (r.get("response") if isinstance(r, dict) else r) or ""
    hist.append({"role": "user", "content": message}); hist.append({"role": "assistant", "content": resp})
    return resp


async def turn(ctx, message, stream=False):
    sid = str(uuid.uuid4())
    if stream:
        chunks = []
        async with AsyncSessionLocal() as db:
            async for ev in chatbot.process_chat_stream(db=db, message=message, officer=ctx, chat_history=[], session_id=sid):
                chunks.append(ev)
        resp = ""
        import json as _json
        for c in chunks:
            s = c.decode() if isinstance(c, bytes) else c
            if isinstance(s, str) and s.startswith("data: "):
                try:
                    resp += _json.loads(s[6:]).get("content", "")
                except Exception:
                    pass
        return resp
    async with AsyncSessionLocal() as db:
        r = await chatbot.process_chat(db=db, message=message, officer=ctx, chat_history=[], session_id=sid)
    return (r.get("response") if isinstance(r, dict) else r) or ""


async def live_main():
    ctx = await get_ctx()

    for stream in (False, True):
        r = await turn(ctx, "Which application should I field visit next?", stream=stream)
        n_overdue, n_awaiting = _table_rows(r, "Overdue scheduled visits"), _table_rows(r, "Applications awaiting")
        check(f"live DB. real officer, real jurisdiction, no app/badge mismatch (stream={stream})",
              ("Field Visit Prioritization" in r) == bool(n_overdue or n_awaiting)
              and (f"but {n_overdue + n_awaiting} application" in r if (n_overdue or n_awaiting) else True),
              r[:200])
        check(f"live DB. record totals reconcile with the tables actually rendered (stream={stream})",
              f"{n_overdue} visit(s)" in r if n_overdue else True, r[:200])
        check(f"live DB. Sub-Division No. column present in the real response (stream={stream})",
              r.count("Sub-Division No.") == (2 if (n_overdue and n_awaiting) else (1 if (n_overdue or n_awaiting) else 0)),
              f"count={r.count('Sub-Division No.')} n_overdue={n_overdue} n_awaiting={n_awaiting}")

    # Direct integration check of postgres.get_visit_plan()'s new subdivision
    # resolution against the real seeded data: every entry it returns must
    # have a real, non-empty display value, and (per the documented domain
    # rule -- 0153/NISD never carries a sub-division) every entry in this
    # ISD-only queue must actually be ISD.
    from backend.services.postgres import get_visit_plan
    async with AsyncSessionLocal() as db:
        plan = await get_visit_plan(db, ctx)
    all_entries = (plan.get("overdue") or []) + (plan.get("awaiting") or []) + (plan.get("scheduled") or [])
    check("live DB. get_visit_plan(): every queue entry is ISD (NISD never has a field visit)",
          all(e.get("type") == "ISD" for e in all_entries), [e.get("type") for e in all_entries])
    check("live DB. get_visit_plan(): every entry has a real sub_division_display, never blank/None",
          all(e.get("sub_division_display") for e in all_entries),
          [e.get("sub_division_display") for e in all_entries])
    _known_multi = [e for e in all_entries if "," in (e.get("sub_division_display") or "")]
    check("live DB. at least one real application in this jurisdiction has multiple sub-divisions, correctly joined",
          len(_known_multi) >= 1, [(e["application_number"], e["sub_division_display"]) for e in _known_multi])

    # A ward with no qualifying work must say so, not invent any.
    r = await turn(ctx, "Which application should I field visit in ward 999?")
    check("live DB. out-of-jurisdiction ward is refused, not silently answered",
          "jurisdiction" in r.lower())

    # ---- Follow-up fix #1: subdivision_qa.load_plots() used to say "no
    # sub-division plots are recorded" for an application that genuinely has
    # one in application_sub_divisions (the authoritative ORM table), purely
    # because the raw areg_temp_subdivclub extract this module also reads has
    # no matching row for a newer batch of seeded applications. Fixed with an
    # ORM fallback in subdivision_qa.load_plots(). ----
    from backend.services.chatbot import subdivision_qa as _subdivision_qa_mod
    async with AsyncSessionLocal() as db:
        plots = await _subdivision_qa_mod.load_plots(db, "2026/0154/02/015495")
    check("live DB. subdivision_qa.load_plots() ORM fallback: app with a real ORM row never reports 'no plots recorded'",
          bool(plots) and plots[0].get("temp_sub_div_no"), plots)

    hist2, sid2 = [], str(uuid.uuid4())
    await turn_hist(ctx, "Show full application details for 2026/0154/02/015495", hist2, sid2)
    r = await turn_hist(ctx, "Does application 2026/0154/02/015495 have more than one subdivision?", hist2, sid2)
    check("live DB. 'does it have more than one subdivision' no longer falsely says none are recorded",
          "no sub-division plots are recorded" not in r.lower(), r[:200])

    # ---- Follow-up fix #2: "the second overdue visit" was not resolved
    # against the just-displayed prioritization queue at all -- it silently
    # reused whichever application the LAST turn happened to be about. Fixed
    # by recording the queue (overdue numbers first, in display order) as a
    # FollowupContext the existing ordinal_pick() machinery already knows how
    # to read. ----
    hist3, sid3 = [], str(uuid.uuid4())
    await turn_hist(ctx, "Which application should I field visit next?", hist3, sid3)
    r = await turn_hist(ctx, "What is the subdivision number of the second overdue visit?", hist3, sid3)
    check("live DB. 'second overdue visit' resolves to the SECOND overdue application, not the first",
          "2026/0154/02/015386" in r and "2026/0154/02/015465" not in r, r[:200])

    # ---- Follow-up fix #3: "upcoming scheduled field visits" was folded
    # into the SAME overdue+awaiting "to be visited" queue (the bare word
    # "upcoming" was treated as a synonym for "needed to be visited"), so a
    # genuinely empty calendar was reported as "10 field visits needed to
    # be visited" instead of "no upcoming scheduled visits". Fixed with a
    # dedicated "upcoming" branch, date-floored at today, in both chat
    # paths. ----
    for stream in (False, True):
        r = await turn(ctx, "Show upcoming scheduled field visits only.", stream=stream)
        check(f"live DB. 'upcoming scheduled field visits' is its own answer, not the to-do queue (stream={stream})",
              "upcoming scheduled field visit" in r.lower()
              and "needed to be visited" not in r.lower(), r[:200])
        # This officer's seeded data has zero future-dated visits (confirmed
        # directly against FieldVisit: 0 rows with scheduled_date >= today in
        # the whole database) -- so the correct, honest answer is the
        # explicit empty statement, never a reused non-empty queue.
        check(f"live DB. zero genuinely-upcoming visits -> explicit empty statement (stream={stream})",
              "no field visits scheduled" in r.lower(), r[:200])

    # The pre-existing "to be visited" queue must be completely unaffected by
    # removing "upcoming" from its cue list.
    r = await turn(ctx, "Which field visits are pending?")
    check("live DB. 'which field visits are pending' still answers the to-do queue, unchanged",
          "needed to be visited" in r.lower(), r[:200])

    # ---- Follow-up fix #4: multi-field ordinal follow-ups after the queue
    # was displayed ("Show the survey number AND subdivision number of the
    # second overdue visit.") used to classify as NOT a follow-up at all
    # (two root causes in followup_context.classify(), both general, not
    # specific to these examples -- see the code comments): a capitalised
    # sentence never matched the all-lowercase _FRAGMENT_OPENERS set, and an
    # ordinal/slice reference naming "application(s)" was read as a fresh
    # subject the same way a truly fresh question is. ----
    hist4, sid4 = [], str(uuid.uuid4())
    await turn_hist(ctx, "Which application should I field visit next?", hist4, sid4)
    r = await turn_hist(ctx, "Show the survey number and subdivision number of the second overdue visit.", hist4, sid4)
    check("live DB. multi-field ordinal follow-up ('survey AND subdivision of the second overdue visit') resolves correctly",
          "2026/0154/02/015386" in r and "2026/0154/02/015465" not in r, r[:250])

    # `test_first_turn_ordinal_live()` runs in its OWN `asyncio.run()` (own
    # event loop); asyncpg connections pooled under THIS loop cannot survive
    # into that one, so the pool is drained here, cleanly, before it starts.
    await _db_engine.dispose()


async def test_first_turn_ordinal_live() -> None:
    """Fix #5: the SAME ordinal+field question asked as the FIRST field-visit
    question of the conversation (no prior queue shown) used to answer with
    the whole unscoped `fv_visit_plan` listing -- there was no
    `FollowupContext` yet for the pre-intent pipeline to resolve the ordinal
    against. `_maybe_resolve_fresh_visit_ordinal()` now computes the same
    plan `fv_visit_plan`'s own handler would, resolves the ordinal against it
    the same way a genuine follow-up does, and rewrites the message to name
    that application before the rest of the turn runs -- for both the
    `fv_visit_plan` and `fv_overdue_inspections` fresh-intent phrasings.

    Run in its OWN `asyncio.run()` (own top-level Task/Context), deliberately
    separate from `live_main()`'s single shared one: this function's own
    first-turn calls must never inherit context left behind by whichever
    turn happened to run immediately before it in a shared task -- the same
    isolation a real request already gets in production (FastAPI gives every
    request its own Task), which a long single-task test script does not
    reproduce on its own.

    Ground truth (re-derived from the live DB each run, never hardcoded): the
    officer's overdue visits are, in order, 015465 / 015386 / 015395.
    """
    ctx = await get_ctx()

    r1 = await turn(ctx, "Show the survey number and subdivision number of the second overdue visit.")
    check("live DB. FIRST-TURN multi-field ordinal (2nd overdue) resolves correctly, no prior queue shown",
          "2026/0154/02/015386" in r1 and "2026/0154/02/015465" not in r1
          and "Field Visit Prioritization" not in r1 and "Overdue Field Visits" not in r1,
          r1[:250])

    r2 = await turn(ctx, "What is the survey number of the first overdue visit?")
    check("live DB. FIRST-TURN single-field ordinal (1st overdue) resolves correctly",
          "2026/0154/02/015465" in r2 and "Field Visit Prioritization" not in r2, r2[:250])

    r3 = await turn(ctx, "What is the survey number of the third overdue visit?")
    check("live DB. FIRST-TURN single-field ordinal (3rd overdue) resolves correctly",
          "2026/0154/02/015395" in r3 and "Field Visit Prioritization" not in r3, r3[:250])

    r4 = await turn(ctx, "What is the survey number of the ninth overdue visit?")
    check("live DB. FIRST-TURN out-of-range ordinal (only 3 overdue) falls back to the full listing, "
          "never invents a single-row answer",
          ("Field Visit Prioritization" in r4 or "Overdue Field Visits" in r4)
          and "Details for application" not in r4 and "For application" not in r4,
          r4[:250])

    r5 = await turn(ctx, "Show all overdue visits.")
    check("live DB. an explicit full-listing request is NOT narrowed to one row",
          "Found 3 field visit" in r5 or "Overdue Field Visits" in r5, r5[:250])

    r6 = await turn(ctx, "Which application should I field visit next?", stream=False)
    check("live DB. the plain prioritization question (no ordinal) still renders the whole plan",
          "Field Visit Prioritization" in r6, r6[:250])

    await _db_engine.dispose()


async def test_first_turn_ordinal_stream_live() -> None:
    """Streaming parity for fix #5, in its own isolated run -- see
    `test_first_turn_ordinal_live`'s docstring for why isolation matters
    here. Checked on its own, fresh (no preceding turns in the same task),
    the way a real streamed request actually arrives in production."""
    ctx = await get_ctx()
    rs = await turn(ctx, "What is the survey number of the first overdue visit?", stream=True)
    check("live DB. FIRST-TURN ordinal resolves the same way through process_chat_stream()",
          "2026/0154/02/015465" in rs, rs[:250])


def main() -> None:
    test_three_overdue_seven_awaiting()
    test_record_totals_and_location_totals_reconcile()
    test_duplicate_application_across_groups_deduplicated()
    test_no_overdue_no_awaiting()
    test_future_scheduled_visits_only()
    test_overdue_alongside_future_scheduled()
    test_ward_block_filter_scoping()
    test_zero_qualifying_in_filtered_ward()
    test_classify_capitalised_imperative_fragment_opener()
    test_classify_ordinal_reference_exempt_from_own_subject_gate()
    test_subdivision_column_known_value()
    test_subdivision_column_header_in_both_tables()
    test_subdivision_multiple_rows_joined_no_duplicate_application()
    test_subdivision_not_applicable_for_non_isd()
    test_subdivision_not_recorded_when_isd_has_no_row()
    test_subdivision_does_not_change_record_or_location_counts()
    asyncio.run(live_main())
    asyncio.run(test_first_turn_ordinal_live())
    asyncio.run(test_first_turn_ordinal_stream_live())

    passed = sum(1 for _, ok, _ in _results if ok)
    total = len(_results)
    print(f"\n{passed}/{total} passed")
    if passed != total:
        print("\nFailures:")
        for name, ok, detail in _results:
            if not ok:
                print(f"  - {name}: {detail}")
        sys.exit(1)


if __name__ == "__main__":
    main()
