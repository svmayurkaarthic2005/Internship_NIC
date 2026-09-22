"""The field-visit table takes the same modifications as the application table:
extra columns, sort / order by / descending, status and row filters, row removal,
year and month ranges -- and follow-ups stay a VISIT table, inside the rows shown.

Expectations are computed from get_field_visits(), not from constants. No LLM: every
turn below must be answered without a model call (each is timed).

    python test_fv_table_followups.py
"""
import asyncio, re, sys, time, uuid
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from sqlalchemy import select
from backend.database import AsyncSessionLocal
from backend.models import SISOfficer
from backend.services.chatbot import process_chat, _single_period_range
from backend.services.postgres import get_field_visits
from test_followup_context import officer_context

fails = []
APP = re.compile(r"<td>(\d{4}/\d{4}/\d+/\d+)</td>")


def check(ok, label, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"\n        {detail}" if not ok and detail else ""))
    if not ok:
        fails.append(label)


print("── single-period ranges")
from datetime import date
for q, want in [("field visits in 2025", (date(2025, 1, 1), date(2025, 12, 31))),
                ("field visits in january 2025", (date(2025, 1, 1), date(2025, 1, 31))),
                ("visits in feb 2024", (date(2024, 2, 1), date(2024, 2, 29))),
                ("2022/0154/28/000156 visit", (None, None)),
                ("visit on 2023-10-30", (None, None))]:
    check(_single_period_range(q) == want, f"range({q!r})", str(_single_period_range(q)))


async def main():
    async with AsyncSessionLocal() as db:
        o = (await db.execute(select(SISOfficer).where(SISOfficer.email.like("csen%")))).scalars().first()
        off = await officer_context(db, o)
        visits = (await get_field_visits(db, off))["field_visits"]
        by_date = sorted([v for v in visits if v.get("field_visit_date")], key=lambda v: v["field_visit_date"])
        undated = [v for v in visits if not v.get("field_visit_date")]
        nums = lambda vs: [v["application_number"] for v in vs]

        async def convo(turns):
            sid, hist, out = str(uuid.uuid4()), [], []
            for q in turns:
                t0 = time.time()
                r = await process_chat(q, sid, off, db, list(hist))
                out.append((r.get("response") or "", time.time() - t0, r.get("intent")))
                hist += [{"role": "user", "content": q}, {"role": "assistant", "content": re.sub(r"<[^>]+>", " ", r.get("response") or "")}]
            return out

        print("── extra columns keep the visit table")
        for q, col, cell in [("show along district", "District", "Thoothukudi"),
                             ("show applicant name also", "Applicant Name", visits[0].get("applicant_name")),
                             ("add ward and taluk", "Taluk", None)]:
            h, dt, it = (await convo(["show field visit", q]))[1]
            check(it == "field_visits" and col in h and "Scheduled Date" in h and "Status" in h
                  and (cell is None or str(cell) in h) and dt < 8 and nums(visits) == APP.findall(h),
                  f"{q!r} -> visit table + {col}", f"{it} {dt:.1f}s")

        print("── sort / order by / descending")
        want_desc = nums(by_date[::-1]) + nums(undated)
        want_asc = nums(by_date) + nums(undated)
        for q, want in [("sort by date descending", want_desc), ("latest first", want_desc),
                        ("order by scheduled date desc", want_desc), ("oldest first", want_asc),
                        ("sort by date ascending", want_asc)]:
            h, dt, it = (await convo(["show field visit", q]))[1]
            check(it == "field_visits" and APP.findall(h) == want and dt < 8, f"{q!r}", f"{it} got {[a[-6:] for a in APP.findall(h)]}")
        (h, dt, it), = await convo(["show field visits in descending order of date"])
        check(APP.findall(h) == want_desc, "fresh 'in descending order of date'")
        (h, dt, it), = await convo(["field visits latest first"])
        check(it == "field_visits" and APP.findall(h) == want_desc, "fresh 'latest first' is not an overdue question", it)

        print("── filters and row removal stay inside the rows shown")
        done = [v for v in visits if v["status"] == "completed"]
        out = await convo(["show field visit", "only completed"])
        check(out[1][2] == "field_visits" and APP.findall(out[1][0]) == nums(done) and out[1][1] < 8,
              "'only completed' -> the completed visits, as a table", f"{out[1][2]} {out[1][1]:.1f}s")
        out = await convo(["show field visit", "only completed", "sort by date descending"])
        check(APP.findall(out[2][0]) == nums(done[::-1]), "sort after a filter stays inside the filter",
              str([a[-6:] for a in APP.findall(out[2][0])]))
        out = await convo(["show field visit", "remove second row"])
        check(APP.findall(out[1][0]) == nums(visits[:1] + visits[2:]) and out[1][2] == "field_visits",
              "'remove second row' -> the same table without row 2", str(APP.findall(out[1][0])))

        print("── year and month ranges")
        for q, pred in [("field visits in 2025", lambda v: (v.get("field_visit_date") or "").startswith("2025")),
                        ("field visits in january 2025", lambda v: (v.get("field_visit_date") or "").startswith("2025-01")),
                        ("field visits in 2023", lambda v: (v.get("field_visit_date") or "").startswith("2023"))]:
            (h, dt, it), = await convo([q])
            check(APP.findall(h) == nums([v for v in visits if pred(v)]), f"{q!r}", f"{APP.findall(h)}")

        print("── 'what is it' after a one-row answer")
        out = await convo(["unscheduled application", "what is it"])
        un = undated[0]["application_number"] if undated else None
        check(un is None or (un in out[1][0] and "Here are the details" in out[1][0] and out[1][1] < 8),
              "'what is it' -> that application's details", out[1][0][:100])
        out = await convo(["show field visit", "what is it"])
        check("Which one do you mean" in re.sub(r"<[^>]+>", " ", out[1][0]), "'what is it' over three rows asks which")

        print("── counting over the visit table uses VISIT status, in the rows on screen")
        plain = lambda h: re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", h))
        n_done = sum(1 for v in visits if v["status"] == "completed")
        n_unsch = sum(1 for v in visits if v["status"] == "unscheduled")
        n_sched = sum(1 for v in visits if v["status"] in ("scheduled", "rescheduled"))
        n_open = sum(1 for v in visits if v["status"] != "completed")
        T = len(visits)
        for q, want in [("how many of them are completed?", f"{n_done} of those {T}"),
                        ("how many are unscheduled?", f"{n_unsch} of those {T}"),
                        ("how many are scheduled?", f"{n_sched} of those {T}"),
                        ("how many are pending", f"{n_open} of those {T}"),
                        ("how many are not completed", f"{n_open} of those {T}"),
                        ("how many of them are ISD?", f"{T} of those {T}"),
                        ("அவற்றில் எத்தனை முடிந்தவை?", f"அந்த {T} கள ஆய்வுகளில் {n_done}"),
                        ("evlo completed", f"அந்த {T} கள ஆய்வுகளில் {n_done}")]:
            out = await convo(["show field visit", q])
            check(want in plain(out[1][0]) and out[1][2] == "fv_followup_count" and out[1][1] < 8,
                  f"after the table: {q!r}", plain(out[1][0])[:120])
        out = await convo(["show completed field visits", "how many of them are completed?"])
        check(f"{n_done} of those {n_done}" in plain(out[1][0]), "after 'completed field visits': all of them are completed")
        y25 = [v for v in visits if (v.get("field_visit_date") or "").startswith("2025")]
        out = await convo(["field visits in 2025", "how many of them are completed?"])
        check(f"{sum(1 for v in y25 if v['status'] == 'completed')} of those {len(y25)}" in plain(out[1][0]),
              "date-filtered table: counted inside the year, not the whole register", plain(out[1][0])[:120])
        out = await convo(["field visits in 2019", "how many of them are completed?"])
        check("nothing to count" in plain(out[1][0]) and out[1][2] == "fv_followup_count",
              "empty table: says so, does not reach back to an older list", plain(out[1][0])[:120])
        out = await convo(["show field visit", "which is the oldest"])
        first = by_date[0]
        check(first["application_number"] in plain(out[1][0]) and first["field_visit_date"][:10] in plain(out[1][0]),
              "'which is the oldest' -> by visit date", plain(out[1][0])[:120])
        out = await convo(["show field visit", "how many field visits are completed"])
        check(out[1][2] == "completed_field_visits" or out[1][2] == "field_visits", "a message naming its own subject is a fresh question",
              out[1][2])


asyncio.run(main())
print("\nFAILED:", fails if fails else "none")
sys.exit(1 if fails else 0)
