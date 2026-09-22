"""Stale state: nothing from one conversation, officer, or turn may leak into another.

A. a NEW session (empty history) answers every follow-up-shaped message exactly as a
   pristine conversation would, even right after another session left a list behind
B. another officer's session never shows this officer's records (application numbers)
C. per-turn module state (sort override, typo map) does not bleed into the next turn
D. a refusal / an off-topic turn leaves no half-context behind
E. a context older than the expiry window is not used

python test_stale_state.py            # process_chat, no LLM (stubbed)
python test_stale_state.py --stream   # process_chat_stream
"""
import asyncio
import logging
import re
import sys
import uuid
from datetime import datetime, timedelta, timezone

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
logging.disable(logging.CRITICAL)

from sqlalchemy import select, update

from backend.database import AsyncSessionLocal, engine
from backend.models import ChatMessage, SISOfficer
from backend.sample_db.build_lora_dataset import build_officer_context, _flatten_html
from backend.services import chatbot, rag

FAILS = []
APP = re.compile(r"\b20\d\d/0\d{3}/\d+/\d+\b")


class Stub:
    temperature = 0.1
    def bind(self, **k): raise RuntimeError("stub")
    def bind_tools(self, *a, **k): raise RuntimeError("stub")
    async def ainvoke(self, *a, **k):
        class R: content = "[[LLM]]"
        return R()
    async def astream(self, *a, **k):
        class R: content = "[[LLM]]"
        yield R()


def check(ok, label, detail=""):
    if not ok:
        FAILS.append(f"{label}: {detail[:200]}")


async def new_session(db, officer_row):
    s = await chatbot.create_chat_session(db, str(officer_row.id))
    return str(s.id)


STREAM = "--stream" in sys.argv


async def say(db, ctx, sid, msg, hist=None):
    if STREAM:
        import json
        parts = []
        async for chunk in chatbot.process_chat_stream(msg, sid, ctx, db, chat_history=hist or []):
            for line in chunk.decode("utf-8", "replace").splitlines():
                if line.startswith("data:"):
                    try:
                        ev = json.loads(line[5:].strip())
                    except ValueError:
                        continue
                    if isinstance(ev, dict):
                        for k in ("content", "table_data"):
                            if ev.get(k):
                                parts.append(str(ev[k]))
        txt = " ".join(parts)
        return None, re.sub(r"\s+", " ", _flatten_html(txt)).strip(), {"response": txt, "table_data": txt}
    r = await chatbot.process_chat(msg, sid, ctx, db, chat_history=hist or [])
    return r.get("intent"), re.sub(r"\s+", " ", _flatten_html(r.get("response") or "")).strip(), r


FOLLOWUPS = ["how many of them are approved", "the 2nd one", "its status", "sort by date", "only isd", "and merge",
             "the next one", "last month", "which is oldest", "add ward column", "remove the first one",
             "their fees", "how many", "show details", "yes", "what about it", "and the fee for it",
             "பழையது எது", "pazhaiyadhu edhu", "avatroda status sollu"]


async def officer(db, name):
    row = (await db.execute(select(SISOfficer).where(SISOfficer.email == f"{name}@sis.tn.gov.in"))).scalars().first()
    return row, await build_officer_context(db, row)


async def main():
    rag.llm = Stub()
    async with AsyncSessionLocal() as db:
        row, ctx = await officer(db, "msivakumar")
        # oracle: each follow-up in a pristine conversation, BEFORE any list was ever shown
        oracle = {}
        for q in FOLLOWUPS:
            sid = await new_session(db, row)
            i, t, _ = await say(db, ctx, sid, q)
            oracle[q] = (i, t)

        # A. leave lists behind in other sessions, then ask in a new one
        for setup in ("show nisd applications", "details of 2026/0154/28/001197", "show applications in june 2026",
                      "applications from CSC", "what is isd", "show my field visits"):
            other = await new_session(db, row)
            await say(db, ctx, other, setup)
            for q in FOLLOWUPS:
                sid = await new_session(db, row)
                i, t, _ = await say(db, ctx, sid, q)
                check((i, t) == oracle[q], f"A after {setup!r} in another session: new session {q!r}",
                      f"{i}: {t[:80]} | want {oracle[q][0]}: {oracle[q][1][:80]}")

        # A2. same officer, page refresh: new session id, history replayed by the client
        other = await new_session(db, row)
        i, t, r = await say(db, ctx, other, "show nisd applications")
        hist = [{"role": "user", "content": "show nisd applications"}, {"role": "assistant", "content": r.get("response") or ""}]
        sid = await new_session(db, row)
        i, t, _ = await say(db, ctx, sid, "how many of them are approved", hist)
        check("of those" in t or "approved" in t, "A2 replayed history still answers a list follow-up", t)

        # B. a second officer must never see the first officer's application numbers
        row2, ctx2 = await officer(db, "muthulakshmis")
        mine = set()
        s1 = await new_session(db, row)
        _, t, r = await say(db, ctx, s1, "show nisd applications")
        mine |= set(APP.findall(t + str(r.get("table_data"))))
        s2 = await new_session(db, row2)
        for q in ["the 2nd one", "how many of them are approved", "its status", "the next one", "which is oldest"]:
            _, t2, r2 = await say(db, ctx2, s2, q)
            leaked = set(APP.findall(t2 + str(r2.get("table_data")))) & mine
            check(not leaked, f"B other officer sees {q!r}", str(list(leaked)[:2]))
        # ...even when it (maliciously) reuses the first officer's session id
        _, t3, r3 = await say(db, ctx2, s1, "the 2nd one")
        leaked = set(APP.findall(t3 + str(r3.get("table_data")))) & mine
        check(not leaked, "B reused foreign session id", str(list(leaked)[:2]))

        n_before = len((await db.execute(select(ChatMessage.id).where(ChatMessage.session_id == s1))).all())
        await say(db, ctx2, s1, "hello there")
        n_after = len((await db.execute(select(ChatMessage.id).where(ChatMessage.session_id == s1))).all())
        check(n_after == n_before, "B a foreign officer wrote into someone else's session", f"{n_before} -> {n_after}")

        # C. per-turn state: a sort clause / a typo fix must not colour the next turn
        s = await new_session(db, row)
        await say(db, ctx, s, "show nisd applications sorted by fee descending")
        _, t_after, r_after = await say(db, ctx, s, "show isd applications")
        s_fresh = await new_session(db, row)
        _, t_fresh, r_fresh = await say(db, ctx, s_fresh, "show isd applications")
        check(APP.findall(t_after + str(r_after.get("table_data"))) == APP.findall(t_fresh + str(r_fresh.get("table_data"))),
              "C a sort clause leaks into the next listing")
        s = await new_session(db, row)
        await say(db, ctx, s, "show nisd aplications")
        _, t_after, _ = await say(db, ctx, s, "what is isd")
        _, t_fresh, _ = await say(db, ctx, await new_session(db, row), "what is isd")
        check(t_after == t_fresh, "C a typo fix leaks into the next turn")

        # D. refusal / off-topic turns
        s = await new_session(db, row)
        await say(db, ctx, s, "show applications in ward 999")
        i, t, _ = await say(db, ctx, s, "how many of them are approved")
        i0, t0 = oracle["how many of them are approved"]
        check((i, t) == (i0, t0), "D a refused listing leaves context behind", f"{i}: {t[:80]}")
        s = await new_session(db, row)
        await say(db, ctx, s, "show nisd applications")
        await say(db, ctx, s, "what is the weather")
        i, t, _ = await say(db, ctx, s, "how many of them are approved")
        check("nisd" in t.lower() or "of those" in t.lower(), "D off-topic turn dropped the list context", t)

        # E. expiry: a list shown long ago is not what "the 2nd one" means now
        s = await new_session(db, row)
        await say(db, ctx, s, "show nisd applications")
        old = datetime.now(timezone.utc) - timedelta(hours=3)
        await db.execute(update(ChatMessage).where(ChatMessage.session_id == s).values(created_at=old))
        await db.commit()
        i, t, _ = await say(db, ctx, s, "the 2nd one")
        check(i in ("followup_clarification", "unknown_message") or "no list" in t.lower() or "nothing has been shown" in t.lower() or "do not know which" in t.lower(),
              "E an expired context still answers", f"{i}: {t[:80]}")
        s = await new_session(db, row)
        await say(db, ctx, s, "show nisd applications")
        i, t, _ = await say(db, ctx, s, "the 2nd one")
        check("details for" in t.lower(), "E a fresh context stopped working", f"{i}: {t[:80]}")
    # G. "clear" deep into a long conversation: carried out, stores nothing, and nothing said
    # before it can be continued in the same session
    async with AsyncSessionLocal() as db:
        row, ctx = await officer(db, "msivakumar")
        for word in ("clear", "wipe the conversation", "clear pannu"):
            s = await new_session(db, row)
            for i in range(24):
                await say(db, ctx, s, ["show nisd applications", "the 2nd one", "how many of them are approved", "hi"][i % 4])
            n0 = len((await db.execute(select(ChatMessage.id).where(ChatMessage.session_id == s))).all())
            i, t, r = await say(db, ctx, s, word)
            n1 = len((await db.execute(select(ChatMessage.id).where(ChatMessage.session_id == s))).all())
            check(n1 == n0 and (STREAM or r.get("action") == "clear_chat"), f"G {word!r} deep in a conversation", f"{n0}->{n1} {i}")
            i, t, _ = await say(db, ctx, s, "how many of them are approved")
            check("of those" not in t, f"G a follow-up after {word!r} still sees the old list", t)

    # F. turns that leave no context (a greeting, thanks, an off-topic question, a refusal)
    # between a list and its follow-up must not change what the follow-up means
    async with AsyncSessionLocal() as db:
        row, ctx = await officer(db, "msivakumar")
        for setup in ("show nisd applications", "show pending applications", "details of 2026/0154/28/001197"):
            for fu in ["how many of them are approved", "the 2nd one", "its status", "which is oldest", "sort by date",
                       "only isd", "the next one", "show details"]:
                s0 = await new_session(db, row)
                await say(db, ctx, s0, setup)
                base = (await say(db, ctx, s0, fu))[:2]
                for junk in ("hi", "thanks", "what is the weather", "who are you", "show applications in ward 999", "ok"):
                    s1 = await new_session(db, row)
                    await say(db, ctx, s1, setup)
                    await say(db, ctx, s1, junk)
                    got = (await say(db, ctx, s1, fu))[:2]
                    check(got == base, f"F {setup!r} -> {junk!r} -> {fu!r}", f"{got[0]}: {got[1][:70]} | base {base[0]}: {base[1][:70]}")
    await engine.dispose()
    print("ALL PASSED" if not FAILS else f"FAILED ({len(FAILS)}):\n  - " + "\n  - ".join(FAILS[:40]))
    return 0 if not FAILS else 1

raise SystemExit(asyncio.run(main()))
