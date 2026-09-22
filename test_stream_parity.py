"""process_chat and process_chat_stream are two copies of one pipeline. Every deterministic
answer must be the same through both -- the features added to one must not be missing from
the other (field-visit tables, negation, sort negation, counts, deictics, stale scope).

    python test_stream_parity.py                # the conversations below (no LLM)
    python test_stream_parity.py --corpus 300   # + N single questions from the test_questions*.txt files (LLM stubbed)
"""
import asyncio, json, re, sys, uuid
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from sqlalchemy import select
from backend.database import AsyncSessionLocal
from backend.models import SISOfficer
from backend.services import chatbot, rag
from test_followup_context import officer_context

fails = []
B = "show my applications from CSC"
CONVOS = [
    [B, "not ISD", "how many are approved"], [B, "no ISD please", "the 2nd one"], [B, "dont show NISD"],
    [B, "not descending"], [B, "dont sort"], [B, "sort by date descending", "reverse the order"],
    [B, "not by date sort by survey number"], [B, "not the first one"], [B, "remove row 2", "the 2nd one"],
    [B, "not this one"], [B, "none of them"], [B, "other than these"], [B, "not along ward"],
    ["show field visit", "show along district"], ["show field visit", "sort by date descending"],
    ["show field visit", "latest first", "the 2nd one"], ["show field visit", "only completed"],
    ["show field visit", "remove second row"], ["show field visit", "not completed"],
    ["show field visit", "how many of them are completed?"], ["show field visit", "how many are unscheduled?"],
    ["show field visit", "which is the oldest"], ["show field visit", "not in 2025"],
    ["show field visit", "not ISD"], ["field visits in 2025"], ["field visits in january 2025", "how many are completed"],
    ["field visits in 2019", "how many of them are completed?"], ["field visits in 2019", "the 2nd one"],
    ["show field visit", "what about this"], ["show field visit", "and that one"], ["show field visit", "field visit of this"],
    ["show field visit", "display first row in field visit"], ["show field visit", "what about the third"],
    ["what about this"], ["and that one?"], ["field visit of this"],
    ["unscheduled application", "what is it"], ["show field visit", "what is it"],
    ["applications not ISD"], ["applications except NISD"], ["everything but ISD"], ["applications not from CSC"],
    ["field visits not ISD"], ["field visits except completed"], ["poda"], ["asdfgh"], ["ok"], ["no"], ["yes"],
    ["show field visit", "how many field visits are completed"],
    ["what is the IP address of 2022/0154/28/000156"], ["do any of my applications share the same IP?"],
]


def flat(t):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t or "")).strip()


async def stream(db, off, q, sid, hist):
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


def corpus(n):
    import glob, random
    qs = []
    for f in sorted(glob.glob(str(Path(__file__).resolve().parent / "test_questions*.txt"))):
        for line in open(f, encoding="utf-8", errors="ignore"):
            q = re.sub(r"^\s*\d+[\.\)]\s*", "", line.strip())
            if 6 <= len(q) <= 160 and not q.startswith(("=", "#", "SECTION", "---", "[", "TOPIC", "TEST")):
                qs.append(q)
    random.seed(7)
    return random.sample(sorted(set(qs)), min(n, len(set(qs))))


async def main():
    if "--corpus" in sys.argv:
        rag.llm = Stub()
        global CONVOS
        CONVOS = [[q] for q in corpus(int(sys.argv[sys.argv.index("--corpus") + 1]))]
    async with AsyncSessionLocal() as db:
        o = (await db.execute(select(SISOfficer).where(SISOfficer.email.like("csen%")))).scalars().first()
        off = await officer_context(db, o)
        same = 0
        for turns in CONVOS:
            sa, sb, ha, hb = str(uuid.uuid4()), str(uuid.uuid4()), [], []
            for q in turns:
                a = (await chatbot.process_chat(q, sa, off, db, list(ha))).get("response") or ""
                b = await stream(db, off, q, sb, list(hb))
                ha += [{"role": "user", "content": q}, {"role": "assistant", "content": flat(a)}]
                hb += [{"role": "user", "content": q}, {"role": "assistant", "content": flat(b)}]
                ok = flat(a) == flat(b)
                same += ok
                if not ok:
                    fails.append(f"{turns} @ {q!r}")
                    print(f"  FAIL  {' -> '.join(turns)!r} @ {q!r}\n        non-stream: {flat(a)[:140]}\n        stream:     {flat(b)[:140]}")
        print(f"  {same} turns identical through both paths")


asyncio.run(main())
print("\nFAILED:", fails if fails else "none")
sys.exit(1 if fails else 0)
