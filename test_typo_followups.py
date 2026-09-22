"""A misspelled follow-up must be answered exactly like the correctly spelled one.

Differential test: for each canonical phrase (asked after the right context) every word of
4+ letters is damaged three ways -- a letter dropped, two letters swapped, a letter doubled, a letter hit on the wrong (neighbouring) key --
and the damaged phrase must give the same intent and the same answer as the canonical one.

    python test_typo_followups.py            # DB, no LLM
    python test_typo_followups.py --list     # print every failing (word -> typo)
    python test_typo_followups.py --seed 5   # a different random choice of which letters to damage
"""
import asyncio, random, re, sys, time, uuid
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from sqlalchemy import select
from backend.database import AsyncSessionLocal
from backend.models import SISOfficer
from backend.services import rag
from backend.services.chatbot import process_chat
from test_followup_context import officer_context


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


BASE = "show my applications from CSC"
FV = "show field visit"
PHRASES = [
    # (context question, canonical follow-up)
    (BASE, "not ISD"), (BASE, "except NISD"), (BASE, "everything but ISD"), (BASE, "dont show NISD"),
    (BASE, "sort by date descending"), (BASE, "not descending"), (BASE, "latest first"), (BASE, "oldest first"),
    (BASE, "reverse the order"), (BASE, "dont sort"), (BASE, "remove row 2"), (BASE, "not the first one"),
    (BASE, "along with district"), (BASE, "how many are approved"), (BASE, "which is oldest"),
    (BASE, "the second one"), (BASE, "the last one"), (BASE, "only NISD"), (BASE, "not this one"), (BASE, "none of them"),
    (FV, "only completed"), (FV, "how many of them are completed"), (FV, "how many are unscheduled"),
    (FV, "which is the oldest"), (FV, "not completed"), (FV, "sort by date descending"), (FV, "show along district"),
    (FV, "display first row in field visit"), (FV, "what about this"), (FV, "and that one"), (FV, "what is it"),
    (FV, "field visit of this"), (FV, "is it completed"), (FV, "remove second row"), (FV, "latest first"),
    (None, "field visits in 2025"), (None, "field visits except completed"), (None, "applications not ISD"),
    (None, "show pending applications"), (None, "show field visits with applicant name"),
]


def damage(word, rnd):
    out = []
    n = len(word)
    if n < 4 or not word.isalpha():
        return out
    i = rnd.randrange(1, n - 1)
    out.append(("drop", word[:i] + word[i + 1:]))
    j = rnd.randrange(1, n - 2) if n > 3 else 1
    out.append(("swap", word[:j] + word[j + 1] + word[j] + word[j + 2:]))
    k = rnd.randrange(1, n)
    out.append(("double", word[:k] + word[k] + word[k:]))
    near = {"a": "s", "s": "d", "d": "f", "e": "r", "r": "t", "t": "y", "o": "p", "i": "o", "u": "i", "n": "m",
            "m": "n", "l": "k", "c": "v", "h": "g", "p": "o", "w": "e", "y": "u", "g": "f", "f": "g", "v": "b", "b": "v", "k": "l"}
    q = rnd.randrange(1, n - 1)
    out.append(("key", word[:q] + near.get(word[q], word[q]) + word[q + 1:]))
    return [(k_, v) for k_, v in out if v != word]


plain = lambda h: re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", h or ""))


async def main():
    rag.llm = Stub()
    rnd = random.Random(int(sys.argv[sys.argv.index("--seed") + 1]) if "--seed" in sys.argv else 11)
    bad, total = [], 0
    async with AsyncSessionLocal() as db:
        o = (await db.execute(select(SISOfficer).where(SISOfficer.email.like("csen%")))).scalars().first()
        off = await officer_context(db, o)

        async def ask(ctx_q, q):
            sid, hist = str(uuid.uuid4()), []
            if ctx_q:
                r = await process_chat(ctx_q, sid, off, db, [])
                hist = [{"role": "user", "content": ctx_q}, {"role": "assistant", "content": plain(r.get("response"))}]
            r = await process_chat(q, sid, off, db, hist)
            return r.get("intent"), plain(r.get("response"))[:400]

        for ctx_q, canon in PHRASES:
            want = await ask(ctx_q, canon)
            words = canon.split()
            for wi, w in enumerate(words):
                for kind, typo in damage(w.lower(), rnd):
                    q = " ".join(words[:wi] + [typo] + words[wi + 1:])
                    total += 1
                    got = await ask(ctx_q, q)
                    if got != want:
                        bad.append((canon, w, kind, q, want, got))
    print(f"  {total - len(bad)}/{total} misspelled follow-ups answered exactly like the correct spelling")
    by_word = {}
    for canon, w, kind, q, want, got in bad:
        by_word.setdefault(w, []).append((kind, q, canon))
    for w, items in sorted(by_word.items(), key=lambda x: -len(x[1])):
        print(f"  MISSED  {w!r}: {len(items)} of {sum(1 for c, ww, *_ in [(b[0], b[1]) for b in bad] if ww == w)} typo(s)")
        if "--list" in sys.argv:
            for kind, q, canon in items[:6]:
                print(f"            {kind:6} {q!r}   (canonical {canon!r})")
    return bad


if __name__ == "__main__":
    bad = asyncio.run(main())
    sys.exit(1 if bad else 0)
