"""Regression suite: a word-count ceiling on the answer ("answer in 20 words",
"explain within 25 words", "in not more than 15 words", "20 words or less")
must be treated as a brevity request (the shortest honest answer already on
hand), never as a request for MORE detail, and must never be misrouted away
from the question it is attached to.

Two separate bugs, both reproduced live before fixing:

1. **chatbot.py: word-limit phrases were read as detail requests.**
   "explain within 25 words" carries the bare trigger word "explain"
   (`_DETAIL_REQUEST_TRIGGER_WORDS`), and nothing recognised "within 25
   words" as a brevity cue, so `_is_detail_request()` returned True and the
   officer got the FULL 177-word bulleted card for a 25-word ceiling --
   exactly backwards. Fixed with `_WORD_LIMIT_RE` (chatbot.py), wired into
   `_is_explain_request()` / `_is_detail_request()` the same way
   `_BRIEF_REQUEST_PHRASES_RE` already is. The register's answers are fixed,
   deterministic text -- never truncated or LLM-rewritten to an exact length,
   which risks cutting a fact mid-sentence -- so a word-count ceiling is
   satisfied by the shortest existing honest answer (the brief one-liner),
   not a literal N-word cut.

2. **rag.py: "words" was typo-matched to "wards" in the comparison parser.**
   "what is ISD? in not more than 15 words" was answered as a WARD
   comparison with neither side named ("compare ward X and ward Y" -- X and
   Y both blank) -- the exact same word/ward collision CLAUDE.md already
   documents for the follow-up typo corrector (`followup_context.py`'s
   `_NEVER_CORRECT`), here independently in `rag.py`'s comparison parser:
   `_tok_index(tokens, ("ward", "wards"))` typo-matched the token "words"
   (edit distance 1). Fixed by adding "words" to `rag._NEVER_TYPO`, the same
   mechanism that already protects "last"/"most"/... from this class of
   false match.

Needs the live DB for the end-to-end checks (deterministic text only, no LLM
call on this path); the routing-only checks need neither:
    python test_word_limit_requests.py            # everything
    python test_word_limit_requests.py --routing  # no DB
"""
from __future__ import annotations
import asyncio, os, re, sys

cand = os.getcwd()
ROOT = cand
for _ in range(5):
    if os.path.isdir(os.path.join(cand, "backend")):
        ROOT = cand
        break
    cand = os.path.dirname(cand)
sys.path.insert(0, ROOT)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from backend.services import chatbot as c
from backend.services.rag import parse_intent, parse_comparison_query

bad = []
total = 0


def check(name, condition, detail=""):
    global total
    total += 1
    if condition:
        print(f"ok   {name}")
    else:
        bad.append((name, detail))
        print(f"FAIL {name}" + (f" -- {detail}" if detail else ""))


# ---- Unit: word-limit phrases are brevity requests, never detail requests ----
WORD_LIMIT_PHRASES = (
    "answer in 20 words", "explain within 25 words", "in not more than 15 words",
    "not more than 15 words", "20 words or less", "15 words or fewer",
    "explain very briefly in 10 words",
)
for msg in WORD_LIMIT_PHRASES:
    full = f"{msg} what is ISD?"
    check(f'"{msg}" recognised as an explain follow-up', c._is_explain_request(msg))
    check(f'"{msg}" is NOT read as a request for MORE detail', not c._is_detail_request(full))

# ---- Unit: "words" is never typo-matched to "ward"/"wards" in the comparison parser ----
check('"what is ISD? in not more than 15 words" is NOT read as a ward comparison',
      parse_intent("what is ISD? in not more than 15 words") != "compare_applications",
      parse_intent("what is ISD? in not more than 15 words"))
check('"what is ISD? in not more than 15 words" routes to service_code_lookup',
      parse_intent("what is ISD? in not more than 15 words") == "service_code_lookup")

# A genuine ward comparison must still work (the fenced-typo addition must
# not make "words" swallow real "ward(s)" mentions the other way around --
# it only protects the token "words" itself, so this is a sanity check that
# the fence didn't overcorrect).
check('"compare ward 102 and ward 103" still recognised as a ward comparison',
      (parse_comparison_query("compare ward 102 and ward 103") or {}).get("kind") == "ward")
check('"which ward has more applications" still recognised as a ward comparison',
      (parse_comparison_query("which ward has more applications") or {}).get("kind") == "ward")


async def _e2e():
    from sqlalchemy import select
    from backend.database import AsyncSessionLocal
    from backend.models import SISOfficer
    from backend.schemas import OfficerContext
    from backend.services.auth_service import get_officer_jurisdiction_ids

    async with AsyncSessionLocal() as db:
        res = await db.execute(select(SISOfficer).where(SISOfficer.email == "marun_chn@sis.tn.gov.in"))
        officer = res.scalar_one()
        jur = await get_officer_jurisdiction_ids(officer.id, db)
        ids = (jur["district_ids"] + jur["taluk_ids"] + jur["town_ids"] + jur["ward_ids"] + jur["block_ids"])
        ctx = OfficerContext(
            officer_id=officer.id, employee_id=officer.employee_id, name=officer.name,
            email=officer.email, designation=officer.designation,
            jurisdiction_type=jur["jurisdiction_type"], jurisdiction_name=jur["jurisdiction_name"],
            jurisdiction_ids=[i for i in ids if i])

    async def turn(msg, hist, sid):
        async with AsyncSessionLocal() as db:
            r = await c.process_chat(db=db, message=msg, officer=ctx, chat_history=hist, session_id=sid)
        resp = (r.get("response") if isinstance(r, dict) else r) or ""
        hist.append({"role": "user", "content": msg})
        hist.append({"role": "assistant", "content": resp})
        return r

    import uuid

    # A one-line brief answer is roughly ~30 words; a full detailed answer is
    # 150+ words. A word-limit request must land near the brief size, never
    # the full one.
    for msg in ("what is ISD? answer in 20 words", "what is ISD? explain within 25 words",
                "what is ISD? in not more than 15 words",
                "what is ISD? explain very briefly in 10 words"):
        sid = str(uuid.uuid4())
        r = await turn(msg, [], sid)
        resp = r.get("response") or ""
        nwords = len(resp.split())
        check(f'e2e: "{msg}" routes to service_code_lookup',
              r.get("intent") == "service_code_lookup", r.get("intent"))
        check(f'e2e: "{msg}" gives a brief answer (<60 words), not the full card',
              nwords < 60, f"{nwords} words: {resp[:200]}")
        check(f'e2e: "{msg}" still names ISD / 0154', "0154" in resp or "ISD" in resp, resp[:200])

    # The follow-up form (word-limit named in a SEPARATE turn) must also work.
    sid = str(uuid.uuid4())
    hist = []
    await turn("what is ISD?", hist, sid)
    r = await turn("answer in 20 words", hist, sid)
    resp = r.get("response") or ""
    check('e2e: "answer in 20 words" as a follow-up reaches service_code_lookup, not general_query',
          r.get("intent") == "service_code_lookup", r.get("intent"))
    check('e2e: the follow-up answer is brief', len(resp.split()) < 60, resp[:200])

    # "very detailed" is unaffected -- still gives the full card.
    sid = str(uuid.uuid4())
    hist = []
    await turn("what is ISD?", hist, sid)
    r = await turn("very detailed please", hist, sid)
    resp = r.get("response") or ""
    check('e2e: "very detailed please" is unaffected by the word-limit fix (still full)',
          len(resp.split()) > 100, len(resp.split()))

    # A genuine ward comparison (live) must still work after the typo fence.
    sid = str(uuid.uuid4())
    r = await turn("compare ward 102 and ward 103", [], sid)
    check('e2e: "compare ward 102 and ward 103" still answers a real ward comparison',
          r.get("intent") == "compare_applications", r.get("intent"))

    # Ordinary application-listing requests remain unaffected.
    sid = str(uuid.uuid4())
    r = await turn("Show my pending applications", [], sid)
    check('e2e: an ordinary application listing is unaffected',
          r.get("intent") in ("pending_applications", "all_service_code_applications"),
          r.get("intent"))


if "--routing" not in sys.argv:
    asyncio.run(_e2e())
for name, detail in bad:
    print(f"FAIL {name}" + (f" -- {detail}" if detail else ""))
print(f"{total - len(bad)}/{total} ok")
sys.exit(1 if bad else 0)
