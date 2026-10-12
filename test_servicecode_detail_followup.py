"""Regression suite: "what is ISD?" -> "in detailed" must return the full,
documented ISD explanation (meaning, example, outcome, workflow stages, fee,
SLA, ISD/NISD comparison) -- not a terse "I don't have a longer explanation
of it beyond that in the SIS documents." fallback.

Root cause: `_fullform_detail_term()` (backend/services/chatbot.py), written
for the narrow "full form of IGRS" -> "in detail" chain, decided whether a
"in detail"-shaped follow-up was ABOUT an acronym by scanning the most recent
user message for ANY bare occurrence of a known acronym token
(`_FULLFORM_TERM_RE.findall(...)`), not by checking that the message was
actually a "full form of X" / "what does X stand for" ASK. "what is ISD?" is
a `service_code_lookup` question that also happens to NAME "ISD" -- so it
matched too, and `_fullform_detail_term` claimed the "in detailed" follow-up
and answered with its own terse one-liner, well before intent routing (and
the already-correct `service_code_lookup` explain re-route in both
`process_chat()` and `process_chat_stream()`) ever got a chance to run.

Fix: `_fullform_detail_term()` now requires the previous user turn to match
`_fullform_term()` (i.e. the real `_FULLFORM_ASK_RE` shape: "full form of
X" / "what does X stand for" / "X full form"), the same check the ORIGINAL
"full form of X" turn itself used to answer. "what is ISD?" does not match
that shape, so it no longer hijacks "in detailed"; a genuine "full form of
IGRS" -> "in detail" chain is unaffected.

Needs the live DB (deterministic, HTML/plain-text answers -- an LLM
paraphrase pass runs on the brief one-liner only, time-boxed to 6s with a
safe fallback, so this suite does not depend on Ollama being reachable) for
the end-to-end checks; the routing-only checks need neither:
    python test_servicecode_detail_followup.py            # everything
    python test_servicecode_detail_followup.py --routing  # no DB
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


def is_detailed(resp: str) -> bool:
    """A real multi-point explanation, not the one-line brief answer or the
    old terse fullform fallback."""
    return "•" in resp and len(resp) > 300


HIST_ISD = [{"role": "user", "content": "what is ISD?"},
            {"role": "assistant", "content": "Service code 0154 = ISD (Involving Subdivision) — "
                                             "a patta transfer in which the parcel is split, so a "
                                             "field inspection and a sub-division sketch are "
                                             "required. (field visit required)"}]
HIST_FULLFORM_IGRS = [{"role": "user", "content": "full form of IGRS"},
                      {"role": "assistant", "content": "IGRS = Inspector General of Registration and Stamps."}]

# ---- Unit: _fullform_detail_term no longer hijacks "what is ISD?" -> "in detailed" ----
check('"in detailed" after "what is ISD?" is NOT claimed by the fullform-detail shortcut '
      '(so it reaches the richer service_code_lookup explain path instead)',
      c._fullform_detail_term("in detailed", HIST_ISD) is None,
      c._fullform_detail_term("in detailed", HIST_ISD))
check('"what is ISD?" by itself does not look like a fullform ASK',
      c._fullform_term("what is ISD?") is None)

# A genuine "full form of X" -> "in detail" chain must still work.
check('"in detail" after a genuine "full form of IGRS" IS still claimed by the fullform-detail '
      'shortcut (unchanged behaviour for its real, narrow purpose)',
      c._fullform_detail_term("in detail", HIST_FULLFORM_IGRS) == "igrs")

# ---- Unit: the detail-request phrasings all recognised as "want MORE detail" ----
DETAIL_FOLLOWUPS = (
    "in detail", "in detailed", "explain in detail", "explain in detailed",
    "explain more", "tell me in detail", "give detailed explanation",
    "explain the full workflow", "explain the workflow step by step",
    "describe the process completely", "what happens at each stage?",
    # "explanation" (the noun) and "thorough" weren't in the trigger
    # vocabulary at all -- neither the exact-phrase check nor the
    # typo-tolerant fallback (too large an edit distance from "explain")
    # caught them, so these fell through to general_query entirely.
    "give me a thorough explanation", "give me an in-depth explanation",
    "explian in detial",
)
for msg in DETAIL_FOLLOWUPS:
    full = f"{msg} what is ISD?"
    check(f'"{msg}" recognised as an explain follow-up', c._is_explain_request(msg))
    check(f'"{msg}" recognised as wanting MORE detail (not brief)', c._is_detail_request(full))

# ---- Unit: negated detail instructions must NOT be read as positive requests ----
# Root cause of this half of the bug: "don't explain in detail" contains the
# bare word "detail" (`_DETAIL_REQUEST_PHRASES`), so `_has_detail_phrase`
# matched it and `_is_detail_request` returned True -- exactly backwards.
NEGATED_DETAIL = (
    "don't explain in detail", "do not give me a detailed explanation",
    "no need for details", "not in detail please",
    # Short forms, no apostrophe, no "explain"/"don't" at all -- found by
    # sweeping short negation phrasings: "no detail" / "skip the detail" each
    # contain the bare word "detail" (`_DETAIL_REQUEST_PHRASES`) with nothing
    # else in the message, so `_has_detail_phrase` matched it as a POSITIVE
    # request and gave the full card to someone who just said they didn't
    # want it.
    "dont explain in detail", "no detail", "no details",
    "skip the detail", "skip details", "skip detail",
    # More complex phrasings swept afterward -- new negator verbs ("avoid",
    # "without"), idioms ("nothing too X", "no long X needed"), a
    # non-negator brevity word ("less detail", "minimal"), and a DOUBLE typo
    # on both the negator-adjacent verb and "detail" itself.
    "avoid detail", "avoid a detailed explanation", "please avoid a detailed answer",
    "nothing too detailed please", "without going into detail",
    "explain it without going into detail", "keep it short, not detailed",
    "briefly, not in detail", "keep it concise, not detailed",
    "slightly less detail", "no long explanation needed",
    "keep the explanation minimal", "dont explian in detial",
)
for msg in NEGATED_DETAIL:
    full = f"{msg} what is ISD?"
    check(f'"{msg}" is NOT read as a request for MORE detail', not c._is_detail_request(full))
    check(f'"{msg}" is still recognised as a re-explain request (just the brief form)',
          c._is_explain_request(msg))

# ---- Unit: _workflow_steps_answer("ALL", brief=True) must actually be brief ----
# "ALL" (the combined ISD+NISD workflow answer) was never itself a key of
# `_WORKFLOW_STEPS_BRIEF_TEXT` (only "ISD"/"NISD" are), so `brief=True` was
# silently ignored for it and it always returned the full chain.
_wf_full = c._workflow_steps_answer("ALL", False, brief=False)
_wf_brief = c._workflow_steps_answer("ALL", False, brief=True)
check('_workflow_steps_answer("ALL", brief=True) is shorter than the full chain',
      len(_wf_brief) < len(_wf_full) * 0.6, f"full={len(_wf_full)} brief={len(_wf_brief)}")
check('_workflow_steps_answer("ALL", brief=True) still names both ISD and NISD',
      "ISD" in _wf_brief and "NISD" in _wf_brief, _wf_brief)


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

    # 1) The exact bug-report conversation.
    sid = str(uuid.uuid4())
    hist = []
    await turn("what is ISD?", hist, sid)
    r2 = await turn("in detailed", hist, sid)
    resp2 = r2.get("response") or ""
    check('e2e: "in detailed" after "what is ISD?" no longer gives the terse fullform fallback',
          "I don't have a longer explanation" not in resp2, resp2[:200])
    check('e2e: "in detailed" gives the real detailed, multi-point answer', is_detailed(resp2), resp2[:200])
    check('e2e: the detailed answer still names ISD / 0154', "0154" in resp2 and "ISD" in resp2, resp2[:200])
    for must_have in ("Workflow", "SLA", "NISD"):
        check(f'e2e: the detailed answer mentions "{must_have}"', must_have in resp2)

    # 2) "What is service code 0154?" -> "explain in detail" preserves 0154.
    sid = str(uuid.uuid4())
    hist = []
    await turn("What is service code 0154?", hist, sid)
    r = await turn("explain in detail", hist, sid)
    resp = r.get("response") or ""
    check('e2e: "explain in detail" after "what is service code 0154?" is detailed and names 0154',
          is_detailed(resp) and "0154" in resp, resp[:200])

    # 3) NISD gets the correct, NISD-specific detailed explanation.
    sid = str(uuid.uuid4())
    hist = []
    await turn("What is NISD?", hist, sid)
    r = await turn("explain in detail", hist, sid)
    resp = r.get("response") or ""
    check('e2e: "explain in detail" after "What is NISD?" is detailed and names 0153/NISD',
          is_detailed(resp) and ("0153" in resp or "NISD" in resp), resp[:200])
    check('e2e: the NISD detailed answer does not claim a field visit is required',
          "no field visit" in resp.lower() or "does not require" in resp.lower(), resp[:300])

    # 4) "Explain the workflow step by step" covers the supported stages.
    sid = str(uuid.uuid4())
    hist = []
    await turn("what is ISD?", hist, sid)
    r = await turn("explain the workflow step by step", hist, sid)
    resp = r.get("response") or ""
    for stage in ("SIS", "Senior Draughtsman", "DIS", "Tahsildar"):
        check(f'e2e: the workflow-steps answer names the "{stage}" stage', stage in resp, resp[:400])

    # 4b) A brief/negated-detail follow-up on the COMBINED ISD+NISD ("ALL")
    # workflow answer must actually shorten it. Root cause found while
    # sweeping variants: `_workflow_steps_answer()`'s brief/full choice keyed
    # off `topic in _WORKFLOW_STEPS_BRIEF_TEXT`, and "ALL" itself was never a
    # key there (only "ISD"/"NISD" are) -- so `brief=True` was silently
    # ignored for the combined answer and it always returned the SAME full
    # 5+3-step chain, whatever was asked.
    r_brief_all = await turn("explain briefly", [{"role": "user", "content": "explain the workflow step by step"},
                                                 {"role": "assistant", "content": resp}], str(uuid.uuid4()))
    resp_brief_all = r_brief_all.get("response") or ""
    check('e2e: "explain briefly" after the combined ISD+NISD workflow answer is shorter than the full chain',
          len(resp_brief_all) < len(resp) * 0.6, f"full={len(resp)} brief={len(resp_brief_all)}")
    r_neg_all = await turn("don't explain in detail", [{"role": "user", "content": "explain the workflow step by step"},
                                                        {"role": "assistant", "content": resp}], str(uuid.uuid4()))
    resp_neg_all = r_neg_all.get("response") or ""
    check('e2e: "don\'t explain in detail" after the combined ISD+NISD workflow answer is also shorter',
          len(resp_neg_all) < len(resp) * 0.6, f"full={len(resp)} negated-detail={len(resp_neg_all)}")

    # 5) Repeated detail/brief/detail follow-ups preserve the service code.
    sid = str(uuid.uuid4())
    hist = []
    await turn("What is ISD?", hist, sid)
    await turn("in detailed", hist, sid)
    await turn("explain the workflow step by step", hist, sid)
    await turn("explain briefly", hist, sid)
    r_final = await turn("explain in detail again", hist, sid)
    resp_final = r_final.get("response") or ""
    check('e2e: after detail -> workflow -> brief -> detail again, the answer is still about ISD/0154',
          "0154" in resp_final or "ISD" in resp_final, resp_final[:200])

    # 5b) "what happens at each stage?" after "what is ISD?" gives the
    #     detailed, stage-by-stage answer, naming ISD/0154.
    sid = str(uuid.uuid4())
    hist = []
    await turn("what is ISD?", hist, sid)
    r_stage = await turn("what happens at each stage?", hist, sid)
    resp_stage = r_stage.get("response") or ""
    check('e2e: "what happens at each stage?" after "what is ISD?" is detailed and names ISD/0154',
          is_detailed(resp_stage) and ("0154" in resp_stage or "ISD" in resp_stage), resp_stage[:200])

    # 6) Negative format instructions must not trigger a detailed answer.
    for neg in ("don't explain in detail", "do not give me a detailed explanation",
                "no need for details", "not in detail please",
                "no detail", "no details", "skip the detail", "skip detail"):
        sid = str(uuid.uuid4())
        hist = []
        await turn("what is ISD?", hist, sid)
        r_neg = await turn(neg, hist, sid)
        resp_neg = r_neg.get("response") or ""
        check(f'e2e: "{neg}" does not produce the full bulleted explanation',
              not is_detailed(resp_neg), resp_neg[:200])

    # 7) "explain briefly" still returns the concise one-liner.
    sid = str(uuid.uuid4())
    hist = []
    await turn("what is ISD?", hist, sid)
    r_brief = await turn("explain briefly", hist, sid)
    resp_brief = r_brief.get("response") or ""
    check('e2e: "explain briefly" after "what is ISD?" is still concise, not the bulleted card',
          not is_detailed(resp_brief), resp_brief[:200])

    # 8) Tamil / Tanglish variant.
    sid = str(uuid.uuid4())
    hist = []
    await turn("ISD endral enna?", hist, sid)
    r_ta = await turn("vivaramaga sollu", hist, sid)
    resp_ta = r_ta.get("response") or ""
    check('e2e: Tanglish "vivaramaga sollu" after "ISD endral enna?" gives a detailed answer',
          is_detailed(resp_ta), resp_ta[:200])

    # 9) Streaming and non-streaming agree.
    import json as _json
    sid = str(uuid.uuid4())
    hist = []
    await turn("what is ISD?", hist, sid)
    full = ""
    async with AsyncSessionLocal() as db:
        async for chunk in c.process_chat_stream(db=db, message="in detailed", officer=ctx,
                                                  chat_history=hist, session_id=sid):
            s = chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk
            for line in s.splitlines():
                if line.startswith("data: ") and line != "data: [DONE]":
                    try:
                        full += _json.loads(line[6:]).get("content", "")
                    except Exception:
                        pass
    check('e2e: streaming path gives the same detailed answer for "in detailed"',
          is_detailed(full) and "0154" in full, full[:200])

    # 10) The original fullform chain ("full form of IGRS" -> "in detail")
    #     is unaffected by this fix.
    sid = str(uuid.uuid4())
    hist = []
    await turn("full form of IGRS", hist, sid)
    r_igrs = await turn("in detail", hist, sid)
    resp_igrs = r_igrs.get("response") or ""
    check('e2e: "full form of IGRS" -> "in detail" still works (unaffected by this fix)',
          "IGRS" in resp_igrs and len(resp_igrs) > 20, resp_igrs[:200])

    # 11) Ordinary application-listing requests are unaffected.
    sid = str(uuid.uuid4())
    hist = []
    r_list = await turn("Show my pending applications", hist, sid)
    check('e2e: an ordinary application listing is unaffected',
          r_list.get("intent") in ("pending_applications", "all_service_code_applications"),
          r_list.get("intent"))


if "--routing" not in sys.argv:
    asyncio.run(_e2e())
for name, detail in bad:
    print(f"FAIL {name}" + (f" -- {detail}" if detail else ""))
print(f"{total - len(bad)}/{total} ok")
sys.exit(1 if bad else 0)
