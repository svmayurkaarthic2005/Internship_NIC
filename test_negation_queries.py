"""Negation: what "not / except / without / everything but / don't" must do.

  - a negated type or status must never return the thing it negates
  - "not descending" is ascending; "don't sort" restores the original order;
    "reverse" flips the last sort; "not by date, sort by survey" sorts by survey
  - "not the first one" / "except the second" drop a row (they do not pick it)
  - "not this one" / "none of them" ask; the model is never asked to guess
  - the same holds over a field-visit table, in the rows on screen
  - a column can still be taken away ("not along ward")

No LLM: every turn is timed and must be answered without a model call.

    python test_negation_queries.py            # DB, no LLM
    python test_negation_queries.py --routing  # pure functions only
"""
import asyncio, re, sys, time, uuid
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import backend.services.followup_context as fctx
from backend.services.chatbot import _rewrite_negated_types, _parse_negation, _is_mutation_request

fails = []


def check(ok, label, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"\n        {detail}" if not ok and detail else ""))
    if not ok:
        fails.append(label)


print("── one meaning, many ways to say it")
for q in ["not ISD", "except ISD", "excluding ISD", "without ISD", "other than ISD", "apart from ISD", "no ISD please",
          "dont show ISD", "don't show ISD", "do not show ISD", "everything but ISD", "all but ISD", "show all except ISD",
          "exclude ISD", "hide ISD", "skip ISD", "leave out ISD", "minus ISD", "besides ISD",
          "not ISD ones", "not ISD ones please", "dont show ISD ones", "exclude ISD ones please", "show everything except ISD ones"]:
    n = fctx.negation_normalise(q)
    check(n.lower().strip() == "not isd", f"{q!r} -> 'not ISD'", repr(n))
for q, want in [("not descending", "sort ascending"), ("not ascending", "sort descending"),
                ("not newest first", "sort ascending"), ("not oldest first", "sort descending"),
                ("not by date sort by survey number", "sort by survey number"),
                ("dont sort", "sort by submission date ascending"), ("no sorting", "sort by submission date ascending"),
                ("undo the sort", "sort by submission date ascending"), ("original order", "sort by submission date ascending"),
                ("dont sort by date", "sort by submission date ascending"), ("sort by date descending", "sort by date descending")]:
    got = fctx.normalise_sort_negation(q)[0]
    check(got == want, f"sort: {q!r}", repr(got))
check(fctx.normalise_sort_negation("dont sort")[1] is True, "'dont sort' is a reset")
ctx_desc = fctx.FollowupContext(entity=fctx.ENTITY_APPLICATION_LIST, application_numbers=["A/1"],
                                filters={"sort_by": "ward_number", "sort_dir": "desc"})
check(fctx.normalise_sort_negation("reverse the order", ctx_desc)[0] == "sort by ward ascending", "reverse flips the recorded sort")
for q in ["not this one", "no not that", "the other one", "none of them", "other than these", "not it"]:
    check(fctx.is_negated_ref(q), f"negated reference: {q!r}")
for q, want in [("applications not ISD", "show NISD and MERGE applications"),
                ("show applications except NISD", "show ISD and MERGE applications"),
                ("everything but ISD", "show NISD and MERGE applications"),
                ("approved applications not ISD", "show approved NISD and MERGE applications"),
                ("applications not from CSC", "show applications from citizen and Sub-Registrar"),
                ("applications except citizen", "show applications from CSC and Sub-Registrar"),
                ("show non-isd applications", "show non-isd applications"),
                ("status of 2022/0154/28/000156 not ISD", "status of 2022/0154/28/000156 not ISD")]:
    got = _rewrite_negated_types(fctx.negation_normalise(q))
    check(got == want, f"rewrite: {q!r}", repr(got))
check(_parse_negation("field visits except completed")[1] == {"completed"}, "visit status negation parsed")
check(not _is_mutation_request("not ISD") and not _is_mutation_request("dont show completed"), "negation is not a mutation")

if "--routing" not in sys.argv:
    from sqlalchemy import select
    from backend.database import AsyncSessionLocal
    from backend.models import SISOfficer
    from backend.services.chatbot import process_chat
    from backend.services.postgres import get_field_visits
    from test_followup_context import officer_context

    NUM = re.compile(r"\d{4}/\d{4}/\d+/\d+")
    plain = lambda h: re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", h or ""))

    async def main():
        async with AsyncSessionLocal() as db:
            o = (await db.execute(select(SISOfficer).where(SISOfficer.email.like("csen%")))).scalars().first()
            off = await officer_context(db, o)

            async def convo(turns):
                sid, hist, out = str(uuid.uuid4()), [], []
                for q in turns:
                    t0 = time.time()
                    r = await process_chat(q, sid, off, db, list(hist))
                    out.append((r.get("response") or "", time.time() - t0, r.get("intent")))
                    hist += [{"role": "user", "content": q}, {"role": "assistant", "content": plain(r.get("response"))}]
                return out

            BASE = "show my applications from CSC"
            base = (await convo([BASE]))[0][0]
            nums = list(dict.fromkeys(NUM.findall(base)))
            T = len(nums)
            n_isd = sum(1 for n in nums if "/0154/" in n)
            check(T >= 10 and n_isd >= 1, f"base list: {T} applications, {n_isd} ISD")

            print("── a negated type over the list on screen")
            for q in ["not ISD", "except ISD", "no ISD please", "dont show ISD", "everything but ISD", "show all except ISD", "other than ISD"]:
                h, dt, it = (await convo([BASE, q]))[1]
                check(f"{T - n_isd} of those {T} application(s) are not ISD" in plain(h) and dt < 8, f"{q!r}", plain(h)[:110])

            print("── sorting, and the negation of sorting")
            for turns, want in [(["not descending"], "oldest first"), (["not ascending"], "newest first"),
                                (["not newest first"], "oldest first"), (["not oldest first"], "newest first"),
                                (["dont sort"], "original order"), (["no sorting"], "original order"),
                                (["undo the sort"], "original order"), (["original order"], "original order"),
                                (["dont sort by date"], "original order"),
                                (["not by date sort by survey number"], "survey number, ascending"),
                                (["sort by date descending", "reverse the order"], "oldest first"),
                                (["sort by date ascending", "reverse the order"], "newest first")]:
                out = await convo([BASE] + turns)
                h, dt, it = out[-1]
                check(want in plain(h) and dt < 8, f"{' -> '.join(turns)!r}", plain(h)[:110])

            print("── a negated row drops the row")
            for q in ["not the first one", "except the second", "everything except the first row", "remove row 2"]:
                h, dt, it = (await convo([BASE, q]))[1]
                check(f"Removed 1, {T - 1} left" in plain(h) and dt < 8, f"{q!r}", plain(h)[:100])

            print("── a negated reference asks, it never guesses")
            for q in ["not this one", "no not that", "the other one", "other than these"]:
                h, dt, it = (await convo([BASE, q]))[1]
                check("Which application do you mean" in plain(h) and dt < 8, f"{q!r}", plain(h)[:100])
            h, dt, it = (await convo([BASE, "none of them"]))[1]
            check("nothing selected" in plain(h) and dt < 8, "'none of them'", plain(h)[:100])

            print("── a column can still be taken away")
            h, dt, it = (await convo([BASE, "not along ward"]))[1]
            check("<th>Ward</th>" not in h and "Application No." in h, "'not along ward' drops the Ward column")

            print("── a negated type in a fresh question")
            pos = lambda q: set(NUM.findall(q))
            (h1, _, _), = await convo(["applications not ISD"])
            (h2, _, _), = await convo(["show NISD and MERGE applications"])
            check(pos(h1) == pos(h2) and pos(h1) and not any("/0154/" in n for n in pos(h1)), "'applications not ISD' = NISD and MERGE")
            (h1, _, _), = await convo(["applications except NISD"])
            check(pos(h1) and not any("/0153/" in n for n in pos(h1)), "'applications except NISD' has no NISD")
            (h1, _, _), = await convo(["everything but ISD"])
            check(pos(h1) and not any("/0154/" in n for n in pos(h1)), "fresh 'everything but ISD' has no ISD")
            (h1, _, _), = await convo(["applications not from CSC"])
            (h2, _, _), = await convo(["show applications from citizen and Sub-Registrar"])
            (h3, _, _), = await convo(["show my csc applications"])
            # everything that is not CSC: the citizen + Sub-Registrar files, and none of the CSC ones
            check(pos(h2) <= pos(h1) and not (pos(h1) & pos(h3)) and pos(h1),
                  "'applications not from CSC' has every citizen / Sub-Registrar file and no CSC one")
            (h1, dt, it), = await convo(["dont sort"])
            check("nothing has been shown" in plain(h1) and dt < 8, "fresh 'dont sort' has nothing to un-sort")

            print("── the same over a field-visit table")
            visits = (await get_field_visits(db, off))["field_visits"]
            nums_of = lambda vs: [v["application_number"] for v in vs]
            open_ = [v for v in visits if v["status"] != "completed"]
            for q in ["not completed", "except completed", "without the completed ones", "dont show completed",
                      "everything but completed"]:
                h, dt, it = (await convo(["show field visit", q]))[1]
                check(NUM.findall(h) == nums_of(open_) and it == "field_visits" and dt < 8, f"{q!r} -> the open visits",
                      str(NUM.findall(h)))
            h, dt, it = (await convo(["show field visit", "not ISD"]))[1]
            check(not NUM.findall(h) and dt < 8, "'not ISD' -> none left (all are ISD)", plain(h)[:100])
            # a visit with no date is not "in 2025" but has no date to be outside it either: the query returns dated rows only
            out25 = [v for v in visits if v.get("field_visit_date") and not v["field_visit_date"].startswith("2025")]
            h, dt, it = (await convo(["show field visit", "not in 2025"]))[1]
            check(sorted(NUM.findall(h)) == sorted(nums_of(out25)) and dt < 8, "'not in 2025' -> visits outside 2025", str(NUM.findall(h)))
            h, dt, it = (await convo(["show field visit", "not the first one"]))[1]
            check(NUM.findall(h) == nums_of(visits[1:]), "'not the first one' -> the table without row 1", str(NUM.findall(h)))
            (h, dt, it), = await convo(["field visits not ISD"])
            check(not NUM.findall(h), "fresh 'field visits not ISD' -> none")
            (h, dt, it), = await convo(["field visits except completed"])
            check(NUM.findall(h) == nums_of(open_), "fresh 'field visits except completed' -> the open visits")
            dated = sorted([v for v in visits if v.get("field_visit_date")], key=lambda v: v["field_visit_date"])
            h, dt, it = (await convo(["show field visit", "not descending"]))[1]
            check(NUM.findall(h) == nums_of(dated) + nums_of([v for v in visits if not v.get("field_visit_date")]),
                  "'not descending' -> ascending by date", str(NUM.findall(h)))

    asyncio.run(main())

print("\nFAILED:", fails if fails else "none")
sys.exit(1 if fails else 0)
