"""Regression suite: a NEGATED sentence-format follow-up ("dont explain in
sentence") must give the full, detailed answer -- not a repeat of the
one-line answer the officer just said not to give.

Bug reproduced with:
    1. "What is service code 0154?"
    2. "Explain in sentence"
    3. "dont explain in sentence"
Step 3 returned the SAME one-line ISD answer as step 2: `_is_detail_request()`
matched the brevity phrase "in sentence" and never looked at the "dont" in
front of it, so the negated instruction was read as a repeat of the positive
one. Fixed with `_is_format_negation_request()` in `backend/services/
chatbot.py`, checked ahead of the brevity-phrase branches in both
`_is_detail_request()` (so the full answer is chosen) and
`_is_explain_request()` (so a negated phrase with no "explain"/"detail" word
of its own, e.g. "don't use sentence format", still re-routes to
`service_code_lookup` at all). `_BRIEF_REQUEST_PHRASES_RE` also gained a bare
"sentence/short/summary FORMAT" alternative (no leading "in") for that same
phrasing.

    python test_negated_format_followup.py            # everything (DB, no LLM)
    python test_negated_format_followup.py --routing  # classification only, no DB
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from backend.services import chatbot as c  # noqa: E402
from backend.services import followup_context as fc  # noqa: E402

bad: list[tuple[str, str]] = []
total = 0


def check(name, ok, detail=""):
    global total
    total += 1
    if ok:
        print(f"ok   {name}")
    else:
        bad.append((name, detail))
        print(f"FAIL {name}" + (f" -- {detail}" if detail else ""))


# ---------------------------------------------------------------------------
# 1. classification: negated format phrases flip detail=True, explain=True
# ---------------------------------------------------------------------------

NEGATED_FORMAT_PHRASES = (
    "dont explain in sentence",
    "don't explain in sentence",
    "don't explain in a sentence",
    "do not explain in one sentence",
    "not in sentence format",
    "don't use sentence format",
)

for msg in NEGATED_FORMAT_PHRASES:
    check(f'"{msg}" is recognised as an explain follow-up', c._is_explain_request(msg))
    check(f'"{msg}" asks for the FULL answer, not the one-liner', c._is_detail_request(msg))

# positive equivalents (no negation) must still ask for the brief answer
for msg in ("explain in sentence", "explain in a sentence", "explain in one sentence"):
    check(f'"{msg}" (no negation) still asks for the brief answer', not c._is_detail_request(msg))

# explicit positive detail requests, and the wording the brief of the pasted
# instructions calls out explicitly, are unaffected
for msg in ("explain in detail instead", "give the detailed explanation", "explain in detail",
            "explain more", "tell me more"):
    check(f'"{msg}" still asks for the FULL answer', c._is_detail_request(msg))

# a positive brevity request elsewhere in the repo's own suite must be untouched
for msg in ("explain briefly", "summarise briefly", "tell me in one sentence", "explain in simple words"):
    check(f'"{msg}" still asks for the brief answer', not c._is_detail_request(msg))

# "in detailed sentence" -- "sentence" names the FORM (prose, not the 9-row
# card); "detailed" there describes how thorough that prose should be, not a
# switch to the card. Before this, the bare word "detailed" (a detail-request
# trigger on its own) won and the officer got the full bulleted card for an
# explicit request to stay in sentence form.
for msg in ("in detailed sentence", "explain in detailed sentence", "in a detailed sentence",
            "give a detailed sentence", "detailed sentence please"):
    check(f'"{msg}" still asks for the brief (sentence-form) answer, not the card',
          not c._is_detail_request(msg))
    check(f'"{msg}" is recognised as an explain follow-up', c._is_explain_request(msg))

# the negated mirror: explicitly refusing a sentence asks for the full card
for msg in ("don't give me a detailed sentence", "not a detailed sentence, give me the full breakdown"):
    check(f'"{msg}" asks for the FULL card, not a sentence', c._is_detail_request(msg))

# a "don't"/"not" that negates something ELSE in the sentence must not flip
# the format decision -- this is still a plain request for a sentence.
UNRELATED_NEGATIONS = (
    "I don't understand ISD; explain it in a sentence",
    "I do not know what ISD means. Explain it in a sentence.",
    "Not sure what this means, explain in a sentence please",
)
for msg in UNRELATED_NEGATIONS:
    check(f'"{msg}" -- an unrelated "don\'t"/"not" is not read as negating the format',
          not c._is_detail_request(msg))
    check(f'"{msg}" is still recognised as an explain follow-up', c._is_explain_request(msg))

# Tanglish / Tamil-adjacent negated-format phrasing (postfix idiom, same as
# neg_scope.py's "ISD vendam")
for msg in ("explain sentence-a venda", "short-a beda sollunga"):
    check(f'"{msg}" (Tanglish postfix negation) asks for the FULL answer', c._is_detail_request(msg))


# ---------------------------------------------------------------------------
# 2. the typo corrector must not interfere (shared vocabulary with the
#    existing "explain in simple words" fix)
# ---------------------------------------------------------------------------

check('"dont explain in sentence" survives the typo corrector with its negator intact',
      "dont" in fc.correct_spelling("dont explain in sentence").lower()
      or "don't" in fc.correct_spelling("dont explain in sentence").lower(),
      fc.correct_spelling("dont explain in sentence"))


# ---------------------------------------------------------------------------
# 3. end-to-end, both chat paths, live DB
# ---------------------------------------------------------------------------

async def _ctx():
    from sqlalchemy import select
    from backend.database import AsyncSessionLocal
    from backend.models import SISOfficer
    from backend.schemas import OfficerContext
    from backend.services.auth_service import get_officer_jurisdiction_ids
    async with AsyncSessionLocal() as db:
        o = (await db.execute(select(SISOfficer).where(SISOfficer.email == "marun_chn@sis.tn.gov.in"))).scalar_one()
        jur = await get_officer_jurisdiction_ids(o.id, db)
        ids = jur["district_ids"] + jur["taluk_ids"] + jur["town_ids"] + jur["ward_ids"] + jur["block_ids"]
        return OfficerContext(officer_id=o.id, employee_id=o.employee_id, name=o.name, email=o.email,
                              designation=o.designation, jurisdiction_type=jur["jurisdiction_type"],
                              jurisdiction_name=jur["jurisdiction_name"], jurisdiction_ids=[i for i in ids if i])


async def _turn(ctx, msg, hist, sid):
    from backend.database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        r = await c.process_chat(db=db, message=msg, officer=ctx, chat_history=hist, session_id=sid)
    resp = (r.get("response") if isinstance(r, dict) else r) or ""
    hist.append({"role": "user", "content": msg})
    hist.append({"role": "assistant", "content": resp})
    return resp


async def _turn_stream(ctx, msg, hist, sid):
    import json as _json
    from backend.database import AsyncSessionLocal
    resp = ""
    async with AsyncSessionLocal() as db:
        async for ev in c.process_chat_stream(db=db, message=msg, officer=ctx, chat_history=hist, session_id=sid):
            s = ev.decode() if isinstance(ev, bytes) else ev
            if isinstance(s, str) and s.startswith("data: "):
                try:
                    resp += _json.loads(s[6:]).get("content", "")
                except ValueError:
                    pass
    hist.append({"role": "user", "content": msg})
    hist.append({"role": "assistant", "content": resp})
    return resp


def _is_prose(resp: str) -> bool:
    return "•" not in resp and resp.count("\n") <= 1


async def live():
    ctx = await _ctx()

    for label, turn in (("non-streaming", _turn), ("streaming", _turn_stream)):
        print(f"\n[{label}]")
        sid = str(uuid.uuid4())
        hist = []
        r1 = await turn(ctx, "What is service code 0154?", hist, sid)
        r2 = await turn(ctx, "Explain in sentence", hist, sid)
        r3 = await turn(ctx, "dont explain in sentence", hist, sid)
        check(f'{label}: "Explain in sentence" is concise prose, not a table', _is_prose(r2), r2[:200])
        check(f'{label}: "dont explain in sentence" is NOT the same one-liner as step 2', r3 != r2, r3[:200])
        check(f'{label}: "dont explain in sentence" is the detailed, multi-point answer',
              not _is_prose(r3) and len(r3) > len(r2) * 2, f"len(r2)={len(r2)} len(r3)={len(r3)}")
        check(f'{label}: the detailed answer still names the right code (0154/ISD)',
              "0154" in r3 or "ISD" in r3, r3[:200])
        for field in ("Meaning", "Example", "Outcome"):
            check(f'{label}: the detailed answer carries the "{field}" point', field in r3, r3[:300])

        # format switching: briefly -> detail, same code throughout
        r4 = await turn(ctx, "explain briefly", hist, sid)
        check(f'{label}: "explain briefly" returns to the concise answer', _is_prose(r4), r4[:200])
        check(f'{label}: "explain briefly" still names the right code', "0154" in r4 or "ISD" in r4, r4[:200])
        r5 = await turn(ctx, "explain in detail", hist, sid)
        check(f'{label}: "explain in detail" gives the full explanation again', not _is_prose(r5), r5[:200])
        check(f'{label}: the officer never had to retype 0154', "0154" in r5 or "ISD" in r5, r5[:200])

        # NISD must behave the same way
        sid2 = str(uuid.uuid4())
        hist2 = []
        await turn(ctx, "What is service code 0153?", hist2, sid2)
        nb = await turn(ctx, "Explain in sentence", hist2, sid2)
        nd = await turn(ctx, "dont explain in sentence", hist2, sid2)
        check(f'{label}: NISD -- "dont explain in sentence" differs from the brief answer', nd != nb, nd[:200])
        check(f'{label}: NISD -- the detailed answer names 0153/NISD', "0153" in nd or "NISD" in nd, nd[:200])
        check(f'{label}: NISD -- the detailed answer is multi-point, not a one-liner', not _is_prose(nd), nd[:200])

        # the unrelated "don't" must still give a plain sentence, not the detailed card
        sid3 = str(uuid.uuid4())
        hist3 = []
        await turn(ctx, "What is service code 0154?", hist3, sid3)
        ru = await turn(ctx, "I don't understand ISD; explain it in a sentence", hist3, sid3)
        check(f'{label}: an unrelated "don\'t" elsewhere in the sentence still gives a plain sentence',
              _is_prose(ru), ru[:200])

    # table-rendering / normal data listings must be unaffected
    sid = str(uuid.uuid4())
    hist = []
    r_apps = await _turn(ctx, "Show my pending applications", hist, sid)
    check("ordinary listing requests are unaffected", "applicat" in r_apps.lower(), r_apps[:200])


def main():
    routing_only = "--routing" in sys.argv
    if not routing_only:
        try:
            asyncio.run(live())
        except (OSError, ConnectionError) as exc:
            print(f"SKIP live sections -- database unavailable: {exc}")
    for name, detail in bad:
        print(f"FAIL {name}" + (f" -- {detail}" if detail else ""))
    print(f"{total - len(bad)}/{total} ok")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
