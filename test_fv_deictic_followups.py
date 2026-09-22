"""Field-visit follow-ups and bare "this / that" references -- no LLM, no guessing.

  "display first row in field visit"  -> that row's VISIT, never its details card
  "what about the third"              -> the third visit, from the rows on screen
  "what about this" / "and that one"  -> asks which one (several in view), or what
                                         to know about it (one in view), or says
                                         nothing is in view (fresh conversation)
  NISD                                -> "needs no field visit", not "not scheduled yet"

Expectations come from get_field_visits() / the register, not from constants.

    python test_fv_deictic_followups.py            # DB, no LLM
    python test_fv_deictic_followups.py --routing  # classification only
"""
import asyncio, re, sys, time, uuid
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import backend.services.followup_context as fctx

fails = []


def check(ok, label, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"\n        {detail}" if not ok and detail else ""))
    if not ok:
        fails.append(label)


print("── classification")
for m, want in [("what about this", "singular"), ("and that one", "singular"), ("this one", "singular"),
                ("field visit of this", "singular"), ("is it completed", "singular"),
                ("idhu enna", "singular"), ("இது பற்றி", "singular"),
                ("what about the third", "singular"),
                ("what about the weather", "none"), ("how many ISD applications", "none")]:
    check(fctx.classify(m) == want, f"classify({m!r}) == {want}", fctx.classify(m))
one = fctx.FollowupContext(entity=fctx.ENTITY_APPLICATION, application_numbers=["2022/0153/28/001405"])
three = fctx.FollowupContext(entity=fctx.ENTITY_APPLICATION_LIST, application_numbers=["A/1", "A/2", "A/3"],
                             filters={"about_visit": True})
r = fctx.resolve("what about this", one, "en")
check(r.ambiguous and "What would you like to know about 2022/0153/28/001405" in r.clarification, "one in view: asks what to know")
r = fctx.resolve("what about this", three, "en")
check(r.ambiguous and "Which one" in r.clarification, "three in view: asks which one")
r = fctx.resolve("display first row in field visit", three, "en")
check(r.application_number == "A/1" and r.about_visit, "'first row in field visit' -> A/1, about the visit")
r = fctx.resolve("what about the third", three, "en")
check(r.application_number == "A/3" and r.about_visit, "a field-visit list makes a bare 'third' about visits")
fv_sd = {"field_visits": [{"application_number": "A/1"}, {"application_number": "A/2"}], "count": 2}
c = fctx.build_context("field_visits", fv_sd)
check(c is not None and c.entity == fctx.ENTITY_APPLICATION_LIST and c.application_numbers == ["A/1", "A/2"]
      and c.filters.get("about_visit"), "a field-visit summary is recorded as a list about visits")

if "--routing" not in sys.argv:
    from sqlalchemy import select
    from backend.database import AsyncSessionLocal
    from backend.models import SISOfficer
    from backend.services.chatbot import process_chat
    from backend.services.postgres import get_field_visits
    from test_followup_context import officer_context

    async def run():
        async with AsyncSessionLocal() as db:
            o = (await db.execute(select(SISOfficer).where(SISOfficer.email.like("csen%")))).scalars().first()
            off = await officer_context(db, o)
            visits = (await get_field_visits(db, off))["field_visits"]
            check(len(visits) >= 3, f"officer has {len(visits)} field visits", "need 3")

            async def convo(turns):
                sid, hist, out = str(uuid.uuid4()), [], []
                for q in turns:
                    t0 = time.time()
                    r = await process_chat(q, sid, off, db, list(hist))
                    t = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", r.get("response") or ""))
                    out.append((t, time.time() - t0, r.get("intent")))
                    hist += [{"role": "user", "content": q}, {"role": "assistant", "content": t}]
                return out

            print("── after the summary: rows resolve to their own visit")
            out = await convo(["show field visit", "display first row in field visit",
                               "display second row in field visit", "what about the third"])
            for i, (t, dt, it) in enumerate(out[1:], start=0):
                v = visits[i]
                n, st, day = v["application_number"], v["status"], (v.get("field_visit_date") or "")[:10]
                if st == "completed":
                    ok = f"the field visit for {n} was completed on {day}" in t
                elif day:
                    ok = n in t and "scheduled for" in t
                else:
                    ok = n in t and "no field visit is scheduled" in t
                check(ok and "Field Visits Summary" not in t and dt < 8, f"row {i + 1} ({st}) -> its own visit", t[:150])

            print("── after the summary: this / that asks which one")
            out = await convo(["show field visit", "what about this", "and that one", "when is that scheduled"])
            for t, dt, it in out[1:]:
                check("Which one do you mean" in t and dt < 8, "asks which", t[:120])

            print("── after ONE application: this / that asks what to know")
            nisd = "2022/0153/28/001405"
            out = await convo([f"show application {nisd}", "what about this", "and that one"])
            for t, dt, it in out[1:]:
                check(f"What would you like to know about {nisd}" in t and dt < 8, "asks what to know", t[:120])
            out = await convo([f"show application {nisd}", "field visit of this", "display first row in field visit"])
            for t, dt, it in out[1:]:
                check("NISD needs no field visit" in t and "Here are the details" not in t,
                      "NISD -> needs no field visit (not 'not scheduled yet')", t[:120])

            print("── a listing of one, then row references")
            out = await convo(["show my citizen applications", "display second row in field visit",
                               "display first row in field visit"])
            check("no number 2" in out[1][0], "row 2 of 1 -> says there is no number 2", out[1][0][:100])
            check("NISD needs no field visit" in out[2][0] and "Here are the details" not in out[2][0],
                  "row 1 in field visit -> the visit, not the details card", out[2][0][:100])

            print("── fresh conversation: nothing in view, nothing invented")
            for q in ["what about this", "and that one?", "field visit of this", "what about the second one",
                      "is it completed"]:
                (t, dt, it), = await convo([q])
                check(dt < 8 and "Field Visits Summary" not in t and
                      ("nothing has been shown" in t or "don't have an application or list in view" in t
                       or "not sure" in t or "could not understand" in t),
                      f"fresh {q!r} -> clarification, no LLM", f"{dt:.1f}s {t[:110]}")

    asyncio.run(run())

print("\nFAILED:", fails if fails else "none")
sys.exit(1 if fails else 0)
