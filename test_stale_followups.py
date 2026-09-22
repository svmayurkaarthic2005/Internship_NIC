"""Stale follow-ups WITHIN a conversation.

test_stale_state.py covers new sessions, other officers, expiry and per-turn state.
This covers the other half: a follow-up must resolve against what is on screen NOW,
not against what was on screen before the last change.

  1. ordinals: after ANY change to a list ("sort", "not ISD", "remove row 2", "only completed" ...)
     "the 2nd one" / "the last one" is the 2nd / last row of the list as it now stands
  2. topic switches: a follow-up after a different kind of answer does not reach back to the old list
  3. scope drift: a period / status / channel / sort / negation never outlives the question that set it
  4. refusals and empty results leave nothing behind to be picked up
  5. cursor ("next one") restarts on a new list

No LLM: every turn is timed and must not call a model.

    python test_stale_followups.py
"""
import asyncio, re, sys, time, uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from sqlalchemy import select, update
from backend.database import AsyncSessionLocal
from backend.models import ChatMessage, SISOfficer
from backend.services.chatbot import process_chat
from test_followup_context import officer_context

fails = []
NUM = re.compile(r"\d{4}/\d{4}/\d+/\d+")
plain = lambda h: re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", h or ""))


def check(ok, label, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"\n        {detail}" if not ok and detail else ""))
    if not ok:
        fails.append(label)


def displayed(r, prev):
    """The application numbers a response leaves on screen, in order (unchanged if it shows none)."""
    td = r.get("table_data") or {}
    rows = td.get("applications") or td.get("field_visits") or []
    nums = [x.get("application_number") for x in rows if isinstance(x, dict) and x.get("application_number")]
    if not nums:
        resp = r.get("response") or ""
        nums = list(dict.fromkeys(NUM.findall(resp)))
        if not nums or ("<table" not in resp and len(nums) < 2):
            return prev
    return list(dict.fromkeys(nums))


async def main():
    async with AsyncSessionLocal() as db:
        o = (await db.execute(select(SISOfficer).where(SISOfficer.email.like("csen%")))).scalars().first()
        off = await officer_context(db, o)

        async def convo(turns, sid=None):
            sid, hist, out = sid or str(uuid.uuid4()), [], []
            for q in turns:
                t0 = time.time()
                r = await process_chat(q, sid, off, db, list(hist))
                out.append((r, time.time() - t0))
                hist += [{"role": "user", "content": q}, {"role": "assistant", "content": plain(r.get("response"))}]
            return out

        BASE = "show my applications from CSC"
        print("── 1. ordinals follow the list as it now stands (applications)")
        for mod in ["sort by date descending", "not ISD", "remove row 2", "only NISD", "along with district",
                    "sort by survey number", "not descending", "dont sort", "exclude the first one",
                    "how many are approved", "which is oldest", "not the first one"]:
            out = await convo([BASE, mod, "the 2nd one"])
            shown = displayed(out[1][0], displayed(out[0][0], []))
            (r3, dt) = out[2]
            if len(shown) < 2:
                continue
            check(shown[1] in (r3.get("response") or "") and dt < 8,
                  f"{mod!r} then 'the 2nd one' = row 2 of what is shown ({shown[1][-6:]})", plain(r3.get("response"))[:100])
            out = await convo([BASE, mod, "the last one"])
            shown = displayed(out[1][0], displayed(out[0][0], []))
            check(shown[-1] in (out[2][0].get("response") or ""), f"{mod!r} then 'the last one' = last row shown",
                  plain(out[2][0].get("response"))[:100])

        print("── 1b. ordinals follow the list as it now stands (field-visit table)")
        for mod in ["sort by date descending", "only completed", "remove second row", "not completed", "show along district",
                    "latest first", "not the first one", "not descending", "how many are completed", "which is the oldest"]:
            out = await convo(["show field visit", mod, "the 2nd one"])
            shown = displayed(out[1][0], displayed(out[0][0], []))
            if len(shown) < 2:
                continue
            check(shown[1] in (out[2][0].get("response") or ""), f"visits: {mod!r} then 'the 2nd one' = row 2 shown ({shown[1][-6:]})",
                  plain(out[2][0].get("response"))[:100])

        print("── 2. a different kind of answer does not reach back to the old list")
        out = await convo([BASE, "show field visit", "how many of them are approved?"])
        check("of those 29" not in plain(out[2][0].get("response")) and "CSC" not in plain(out[2][0].get("response")),
              "list -> visits -> 'how many approved' is not about the old application list", plain(out[2][0].get("response"))[:110])
        out = await convo(["show field visit", BASE, "how many are completed"])
        check(out[2][0].get("intent") != "fv_followup_count", "visits -> applications -> 'how many completed' is not a visit count",
              str(out[2][0].get("intent")))
        first = list(dict.fromkeys(NUM.findall(out[1][0].get("response") or "")))[0]
        out = await convo([BASE, f"details of {first}", "which is oldest"])
        check("oldest of those" not in plain(out[2][0].get("response")), "list -> one application -> 'which is oldest' does not rank the old list",
              plain(out[2][0].get("response"))[:110])
        out = await convo([BASE, f"details of {first}", "how many of them are approved"])
        check("of those 29" not in plain(out[2][0].get("response")), "list -> one application -> 'how many of them' does not count the old list",
              plain(out[2][0].get("response"))[:110])

        print("── 3. scope never outlives the question that set it")
        full = list(dict.fromkeys(NUM.findall((await convo(["show field visit"]))[0][0].get("response") or "")))
        for setup in ["field visits in 2025", "field visits not ISD", "field visits except completed", "field visits latest first"]:
            out = await convo([setup, "show field visit"])
            check(list(dict.fromkeys(NUM.findall(out[1][0].get("response") or ""))) == sorted(full) or
                  set(NUM.findall(out[1][0].get("response") or "")) == set(full),
                  f"{setup!r} then a fresh 'show field visit' shows every visit")
        out = await convo(["show field visit", "only completed", "show field visit"])
        check(set(NUM.findall(out[2][0].get("response") or "")) == set(full), "visits -> only completed -> fresh 'show field visit' is unfiltered")
        out = await convo(["show field visit", "not completed", "show field visit"])
        check(set(NUM.findall(out[2][0].get("response") or "")) == set(full), "visits -> not completed -> fresh 'show field visit' is unfiltered")
        base_nums = list(dict.fromkeys(NUM.findall((await convo([BASE]))[0][0].get("response") or "")))
        out = await convo([BASE, "sort by date descending", BASE])
        check(list(dict.fromkeys(NUM.findall(out[2][0].get("response") or ""))) == base_nums, "sorted list -> fresh list is back in default order")
        out = await convo([BASE, "not ISD", "how many ISD applications do I have"])
        check("not ISD" not in plain(out[2][0].get("response")) and "of those" not in plain(out[2][0].get("response")),
              "'not ISD' does not leak into a fresh 'how many ISD applications'", plain(out[2][0].get("response"))[:100])
        default = set(NUM.findall((await convo(["show my applications"]))[0][0].get("response") or ""))
        out = await convo(["show approved applications", "show my applications"])
        check(set(NUM.findall(out[1][0].get("response") or "")) == default, "an 'approved' scope does not outlive its question")
        out = await convo(["show my applications", "sort by date descending", "show my applications"])
        check(set(NUM.findall(out[2][0].get("response") or "")) == default, "a sort does not outlive its question")
        out = await convo(["applications in june 2026", "how many ISD applications do I have"])
        check("june" not in plain(out[1][0].get("response")).lower(), "a month scope does not leak into the next fresh question")

        print("── 3b. counts are over the rows on screen now")
        for turns, want in [([BASE, "remove row 2", "how many are approved"], f"of those {len(base_nums) - 1} "),
                            ([BASE, "only NISD", "how many are approved"], "of those 26 "),
                            (["show field visit", "only completed", "how many are ISD"], "2 of those 2 "),
                            (["show field visit", "remove second row", "how many are completed"], "1 of those 2 "),
                            (["field visits in january 2025", "how many are completed"], "1 of those 1 ")]:
            out = await convo(turns)
            check(want in plain(out[-1][0].get("response")) + " ", f"{' -> '.join(turns[1:])!r}: {want.strip()}",
                  plain(out[-1][0].get("response"))[:100])

        print("── 3c. the typo map is per turn")
        from backend.services.chatbot import _RAW_BY_FIXED
        await convo(["random words"])
        await convo(["show field visit"])
        check(not (_RAW_BY_FIXED.get() or {}), "no typo mapping survives into the next turn", str(_RAW_BY_FIXED.get()))

        print("── 4. refusals and empty results leave nothing behind")
        out = await convo([BASE, "details of 2022/0154/28/000156", "what is its status"])
        check("2022/0154/28/000156" not in (out[2][0].get("response") or "") or "outside" in plain(out[2][0].get("response")),
              "a refused application number is not carried into the next turn", plain(out[2][0].get("response"))[:110])
        out = await convo(["show field visit", "field visits in 2019", "sort by date descending"])
        check(not NUM.findall(out[2][0].get("response") or ""), "empty visit table -> sort shows nothing, not the earlier 3",
              plain(out[2][0].get("response"))[:110])
        out = await convo(["show field visit", "field visits in 2019", "the 2nd one"])
        check(not NUM.findall(out[2][0].get("response") or ""), "empty visit table -> 'the 2nd one' does not pick from the earlier table",
              plain(out[2][0].get("response"))[:110])
        out = await convo([BASE, "show merge applications", "the 2nd one"])
        check("29" not in plain(out[2][0].get("response")) and not (set(NUM.findall(out[2][0].get("response") or "")) & set(base_nums)),
              "empty application list -> 'the 2nd one' does not pick from the earlier list", plain(out[2][0].get("response"))[:110])

        print("── 5. 'next one' restarts on a new list")
        out = await convo([BASE, "next one", "next one", "show field visit", "next one"])
        visit_nums = list(dict.fromkeys(NUM.findall(out[3][0].get("response") or "")))
        got = out[4][0].get("response") or ""
        check(visit_nums and visit_nums[0] in got, "after a new list 'next one' starts at that list's first row", plain(got)[:110])

        print("── 6. a context older than the expiry window is not used (visit table)")
        sid = str(uuid.uuid4())
        await convo(["show field visit"], sid=sid)
        await db.execute(update(ChatMessage).where(ChatMessage.session_id == sid)
                         .values(created_at=datetime.now(timezone.utc) - timedelta(hours=6)))
        await db.commit()
        r, dt = (await convo(["what about the third"], sid=sid))[0]
        check("nothing has been shown" in plain(r.get("response")), "an expired visit table is not resolved against",
              plain(r.get("response"))[:110])


asyncio.run(main())
print("\nFAILED:", fails if fails else "none")
sys.exit(1 if fails else 0)
