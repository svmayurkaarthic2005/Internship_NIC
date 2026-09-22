"""Remarks written on an application's workflow steps, and rejection reasons.

"is there any remark in them?" after a list, "remarks of <app>", "why were they rejected?" -- answered from
the workflow history of exactly the applications on screen / named. Expectations come from SQL on the
register (layer 2), independent of the handler. No LLM.

    python test_workflow_remarks.py
"""
import asyncio, json, random, re, sys, uuid
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import psycopg2
from sqlalchemy import select
from backend.database import AsyncSessionLocal
from backend.models import SISOfficer
from backend.sample_db.dbconn import conn_params
from backend.services import chatbot, rag
from backend.services.chatbot import process_chat, _asked_workflow_remarks
from test_followup_context import officer_context
from test_typo_followups import Stub, damage

fails = []
NUM = re.compile(r"\d{4}/\d{4}/\d+/\d+")
plain = lambda h: re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", h or ""))
NOREM = ("", "-", "--", "---", "na", "n/a", "nil", "none", "null")


def check(ok, label, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"\n        {detail}" if not ok and detail else ""))
    if not ok:
        fails.append(label)


print("── what counts as a remark question")
for q, want in [("is there any remark in them", "remarks"), ("any remarks?", "remarks"), ("show their remarks", "remarks"),
                ("remarks of 2022/0153/28/001148", "remarks"), ("why were they rejected", "reason"),
                ("what is the reason for rejection", "reason"), ("any comments", "remarks"),
                ("what are the sis remarks", None), ("order remarks of 2022/0153/28/001148", None),
                ("show pending applications", None), ("how many are approved", None)]:
    check(_asked_workflow_remarks(q) == want, f"{q!r} -> {want}", str(_asked_workflow_remarks(q)))


async def main():
    rag.llm = Stub()
    cur = psycopg2.connect(**conn_params()).cursor()

    def truth(nums):
        cur.execute("""SELECT a.application_number, a.current_status, w.remarks, w.rejection_reason, w.to_stage
                       FROM applications a LEFT JOIN workflow_history w ON w.application_id = a.id
                       WHERE a.application_number = ANY(%s) ORDER BY a.application_number, w.performed_at""", (nums,))
        by = {}
        for n, st, rem, rej, to in cur.fetchall():
            d = by.setdefault(n, {"status": st, "remarks": [], "reason": None})
            if (rem or "").strip().lower() not in NOREM:
                d["remarks"].append(rem.strip())
            if (rej or "").strip().lower() not in NOREM:
                d["reason"] = rej.strip()
            elif st == "rejected" and to == "REJECTED" and (rem or "").strip().lower() not in NOREM:
                d["reason"] = rem.strip()
        return by

    async with AsyncSessionLocal() as db:
        o = (await db.execute(select(SISOfficer).where(SISOfficer.email.like("csen%")))).scalars().first()
        off = await officer_context(db, o)

        async def ask(turns):
            sid, hist, out = str(uuid.uuid4()), [], []
            for q in turns:
                r = await process_chat(q, sid, off, db, list(hist))
                out.append((r.get("response") or "", r.get("intent")))
                hist += [{"role": "user", "content": q}, {"role": "assistant", "content": plain(r.get("response"))}]
            return out

        BASE = "show me all rejected applications"
        base = (await ask([BASE]))[0][0]
        nums = list(dict.fromkeys(NUM.findall(base)))
        T = truth(nums)
        n_rem = sum(1 for n in nums if T[n]["remarks"])
        n_rsn = sum(1 for n in nums if T[n]["reason"])
        check(len(nums) >= 10, f"base list: {len(nums)} rejected applications")

        print("── a list on screen: are there any remarks in them?")
        for q in ["is there any remark in them", "any remarks?", "what are the remarks", "show their remarks",
                  "do they have any comments", "is thre any remrk in them", "is there any remark in these applicatins"]:
            h, it = (await ask([BASE, q]))[1]
            check(it == "workflow_remarks" and f"on {n_rem} of {len(nums)}" in plain(h), f"{q!r}: {n_rem} of {len(nums)}",
                  f"{it} {plain(h)[:100]}")
        h, it = (await ask([BASE, "is there any remark in them"]))[1]
        check(set(NUM.findall(h)) == set(nums), "every application on screen has a row (a remark, or 'no remark recorded')")
        for n in nums[:6]:
            if T[n]["remarks"]:
                check(T[n]["remarks"][-1][:30] in plain(h), f"{n[-6:]}: its latest remark is shown verbatim")

        print("── why were they rejected")
        for q in ["why were they rejected", "what is the reason for rejection", "reasons for the rejection"]:
            h, it = (await ask([BASE, q]))[1]
            check(it == "workflow_remarks" and f"for {n_rsn} of {len(nums)} rejected" in plain(h), f"{q!r}", plain(h)[:110])

        print("── one application, named or picked")
        a = nums[0]
        for turns in ([f"remarks of {a}"], [f"is there any remark in {a}"], [BASE, "remarks of the first one"],
                      [BASE, "does the 1st one have remarks"]):
            h, it = (await ask(turns))[-1]
            want = T[a]["remarks"]
            ok = it == "workflow_remarks" and (all(r[:25] in plain(h) for r in want) if want else "No remark" in plain(h))
            check(ok, f"{turns[-1]!r}", plain(h)[:120])
        h, it = (await ask([f"why was {a} rejected"]))[0]
        check(bool(T[a]["reason"]) and T[a]["reason"][:25] in plain(h), "'why was <app> rejected' gives its reason", plain(h)[:120])

        print("── not this feature's business, and not other officers' files")
        h, it = (await ask([f"what are the sis remarks of {a}"]))[0]
        check(it != "workflow_remarks", "'sis remarks' stays with its own handler", str(it))
        h, it = (await ask(["any remarks?"]))[0]
        check(it != "workflow_remarks", "no list and no application: not answered from nowhere", str(it))
        h, it = (await ask(["remarks of 2022/0154/28/000156"]))[0]      # another officer's ward
        check("Desk" not in h and ("outside" in plain(h).lower() or "not in your jurisdiction" in plain(h)),
              "another officer's application is refused, not described", plain(h)[:120])
        h, it = (await ask([BASE, "show field visit", "any remarks?"]))[2]
        check(it != "workflow_remarks" or "rejected" not in plain(h).lower(),
              "after a visit table, remarks do not reach back to the old list", str(it))

        print("── the stream path answers the same")

        async def stream(q, sid, hist):
            parts = []
            async for chunk in chatbot.process_chat_stream(q, sid, off, db, chat_history=hist):
                for line in chunk.decode("utf-8", "replace").splitlines():
                    if line.startswith("data:"):
                        try:
                            ev = json.loads(line[5:].strip())
                        except ValueError:
                            continue
                        if isinstance(ev, dict) and ev.get("content"):
                            parts.append(str(ev["content"]))
            return "".join(parts)

        sid = str(uuid.uuid4())
        b = await stream(BASE, sid, [])
        h = await stream("is there any remark in them", sid, [{"role": "user", "content": BASE}, {"role": "assistant", "content": plain(b)}])
        h2 = (await ask([BASE, "is there any remark in them"]))[1][0]
        check(plain(h) == plain(h2), "stream == non-stream", plain(h)[:100])

        print("── with a spelling slip in every word")
        rnd, total, bad = random.Random(5), 0, 0
        for canon in ["is there any remark in them", "show their remarks", "why were they rejected"]:
            words = canon.split()
            want = (await ask([BASE, canon]))[1]
            for wi, w in enumerate(words):
                for kind, typo in damage(w.lower(), rnd):
                    q = " ".join(words[:wi] + [typo] + words[wi + 1:])
                    total += 1
                    got = (await ask([BASE, q]))[1]
                    if plain(got[0]) != plain(want[0]):
                        bad += 1
                        print(f"        missed [{kind}] {q!r}")
        check(bad == 0, f"{total - bad}/{total} misspelled versions answered like the correct spelling")


asyncio.run(main())
print("\nFAILED:", fails if fails else "none")
sys.exit(1 if fails else 0)
