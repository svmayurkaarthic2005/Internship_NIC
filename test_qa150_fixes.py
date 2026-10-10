"""Permanent regression suite for the defects found and fixed during the 150-question manual QA
sweep (2026-10-09): Q016, Q019, the workflow-history family (Q027-030, Q131-140), the F-Line
desk-less verify fields (Q144-146), service-code lookup/comparison/ranking (Q041-042, Q064-066,
Q073, Q083), date extremes (Q038-039), the trailing-punctuation projection bug (Q088), the
rejected-exclusion scoping bug (Q105, Q108), blocks-in-ward (Q107), jurisdiction yes/no (Q113),
IP comparison (Q122-123), composite payment fields (Q129), and the "same X but different Y"
big-message split (Q040).

`python test_qa150_fixes.py` (needs the DB; no LLM -- every assertion is on a deterministic
Python-rendered answer, never an LLM-generated one).
"""
from __future__ import annotations

import asyncio
import re
import sys
import uuid

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, r"C:\proj\nic_internship")

from sqlalchemy import select

from backend.database import AsyncSessionLocal
from backend.models import SISOfficer
from backend.schemas import OfficerContext
from backend.services import chatbot
from backend.services.auth_service import get_officer_jurisdiction_ids

OFFICER_EMAIL = "marun_chn@sis.tn.gov.in"
APP = "2026/0154/02/012547"           # ISD, rejected, 4 workflow hops, fee/challan/mode populated
APP_APPROVED = "2026/0154/02/013710"  # ISD, approved -> stage COMPLETED
FLINE_APP = "2026/0178/02/000309"     # SIS-verified (sis_verify_date, sis_username populated), DIS not verified
NO_TSLR_APP = APP                     # 0154, zero rows in tslr_sketch_urban

_results: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    _results.append((name, condition, detail))
    print(f"{'ok ' if condition else 'FAIL'} {name}" + (f" -- {detail}" if detail and not condition else ""))


async def officer_context(db, officer) -> OfficerContext:
    jur = await get_officer_jurisdiction_ids(officer.id, db)
    ids = (jur["district_ids"] + jur["taluk_ids"] + jur["town_ids"]
           + jur["ward_ids"] + jur["block_ids"])
    return OfficerContext(
        officer_id=officer.id, employee_id=officer.employee_id, name=officer.name,
        email=officer.email, designation=officer.designation,
        jurisdiction_type=jur["jurisdiction_type"], jurisdiction_name=jur["jurisdiction_name"],
        jurisdiction_ids=[i for i in ids if i])


async def get_ctx() -> OfficerContext:
    async with AsyncSessionLocal() as db:
        res = await db.execute(select(SISOfficer).where(SISOfficer.email == OFFICER_EMAIL))
        return await officer_context(db, res.scalar_one())


async def turn(ctx, message, chat_history, sid, stream=False):
    if stream:
        chunks = []
        async with AsyncSessionLocal() as db:
            async for ev in chatbot.process_chat_stream(db=db, message=message, officer=ctx,
                                                         chat_history=chat_history, session_id=sid):
                chunks.append(ev)
        resp = "".join(
            c.get("content", "") for c in chunks
            if isinstance(c, dict) and c.get("type") == "chunk"
        )
        if not resp:
            import json as _json
            for c in chunks:
                if isinstance(c, (bytes, str)):
                    s = c.decode() if isinstance(c, bytes) else c
                    if s.startswith("data: "):
                        try:
                            resp += _json.loads(s[6:]).get("content", "")
                        except Exception:
                            pass
    else:
        async with AsyncSessionLocal() as db:
            r = await chatbot.process_chat(db=db, message=message, officer=ctx,
                                           chat_history=chat_history, session_id=sid)
        resp = r.get("response") if isinstance(r, dict) else r
    chat_history.append({"role": "user", "content": message})
    chat_history.append({"role": "assistant", "content": resp})
    return resp or ""


async def main():
    ctx = await get_ctx()

    # ---- Q016: rejected != completed stage ----
    r = await turn(ctx, f"Has {APP} reached the completed stage?", [], str(uuid.uuid4()))
    check("Q016. rejected file: 'reached completed stage?' -> No",
          "no" in r.lower() and "reject" in r.lower(), r[:150])
    r = await turn(ctx, f"Has {APP_APPROVED} reached the completed stage?", [], str(uuid.uuid4()))
    check("Q016b. approved file (stage COMPLETED): 'reached completed stage?' -> Yes",
          "yes" in r.lower(), r[:150])

    # ---- Q019: rejected file is not SLA-overdue ----
    r = await turn(ctx, f"Is {APP} marked overdue?", [], str(uuid.uuid4()))
    check("Q019. rejected file -> 'Overdue' does not apply, never an SLA-elapsed narrative",
          "overdue" in r.lower() and "does not apply" in r.lower() and "working days" not in r.lower(), r[:200])

    # ---- Workflow-history family (Q027-030, Q131-140), both chat paths ----
    for stream in (False, True):
        tag = "stream" if stream else "non-stream"
        r = await turn(ctx, f"On what date did the first workflow action occur for {APP}?", [], str(uuid.uuid4()), stream)
        check(f"Q027 [{tag}]. first workflow action = 2026-08-05", "2026-08-05" in r, r[:150])
        r = await turn(ctx, f"When did {APP} move from SIS to another stage?", [], str(uuid.uuid4()), stream)
        check(f"Q029 [{tag}]. SIS->SD transition names SD and the date", "sd" in r.lower() and "2026-09-22" in r, r[:150])
        r = await turn(ctx, f"Who performed the latest workflow action on {APP}?", [], str(uuid.uuid4()), stream)
        check(f"Q131 [{tag}]. names the desk (SD), not just a timestamp", "sd" in r.lower(), r[:150])
        r = await turn(ctx, f"What was the latest workflow remark on {APP}?", [], str(uuid.uuid4()), stream)
        check(f"Q132 [{tag}]. addresses 'remark' (all NULL -> says not recorded)",
              "remark" in r.lower() or "record" in r.lower(), r[:150])
        r = await turn(ctx, f"What action came immediately before the current stage for {APP}?", [], str(uuid.uuid4()), stream)
        check(f"Q135 [{tag}]. names SD as the prior stage", "sd" in r.lower(), r[:150])
        r = await turn(ctx, f"How many workflow entries exist for {APP}?", [], str(uuid.uuid4()), stream)
        check(f"Q137 [{tag}]. workflow entry count = 4", "4" in r, r[:150])
        r = await turn(ctx, f"What was the final workflow transition for {APP}?", [], str(uuid.uuid4()), stream)
        check(f"Q139 [{tag}]. answers from workflow_history, never misrouted to F-Line",
              "f-line" not in r.lower() and "0178" not in r, r[:200])
        r = await turn(ctx, f"On what date did {APP} reach its current stage?", [], str(uuid.uuid4()), stream)
        check(f"Q140 [{tag}]. reached-REJECTED date, not the submission date",
              "2026-09-2" in r and "2026-08-05" not in r, r[:150])

    # ---- Q028/Q134: no recommendation column, never redirected to NISD-only table ----
    r = await turn(ctx, f"What was the SIS recommendation for {APP}?", [], str(uuid.uuid4()))
    check("Q134. ISD file: SIS recommendation answered from workflow_history, not 'no full-field patta transfer record'",
          "full-field patta transfer" not in r.lower(), r[:200])

    # ---- Q144-146: F-Line desk-less verify fields ----
    r = await turn(ctx, f"Who verified the F-Line application {FLINE_APP}?", [], str(uuid.uuid4()))
    check("Q144. names the SIS verifier (chn_chandru)", "chn_chandru" in r, r[:150])
    r = await turn(ctx, f"What date was the F-Line {FLINE_APP} verified?", [], str(uuid.uuid4()))
    check("Q145. gives the SIS verify date", "2026-06-30" in r, r[:150])
    r = await turn(ctx, f"What is the F-Line transaction status for {FLINE_APP}?", [], str(uuid.uuid4()))
    check("Q146. reports NULL transaction_status as not recorded, not the NISD-only refusal",
          "full-field patta transfer" not in r.lower(), r[:200])

    # ---- Negative test: "transition" must never select F-Line logic on an unrelated app ----
    r = await turn(ctx, f"What is the transition history for {APP}?", [], str(uuid.uuid4()))
    check("Negative: bare 'transition' on an ISD app never triggers F-Line routing",
          "f-line" not in r.lower(), r[:200])

    # ---- Q041/042: service-code lookup via 'represent' ----
    r = await turn(ctx, "What service does code 0153 represent?", [], str(uuid.uuid4()))
    check("Q041. 0153 = NISD definition, not an application-number prompt",
          "nisd" in r.lower() or "not involving" in r.lower(), r[:150])
    r = await turn(ctx, "What service does code 0154 represent?", [], str(uuid.uuid4()))
    check("Q042. 0154 = ISD definition", "isd" in r.lower() or "involving" in r.lower(), r[:150])

    # ---- Q064-066: conceptual service-code comparison, not a file count ----
    r = await turn(ctx, "How is 0164 different from 0161?", [], str(uuid.uuid4()))
    check("Q064. names official service names, not a file-count comparison",
          "ulc" in r.lower() and "street master" in r.lower() and "files" not in r.lower(), r[:200])
    r = await turn(ctx, "What is the difference between 0156 and 0157?", [], str(uuid.uuid4()))
    check("Q066. 'with Sketch' vs 'Extract Only', not a file count",
          "sketch" in r.lower() and "files" not in r.lower(), r[:200])

    # ---- Q073/083: register-wide analytics, not an application-number prompt ----
    r = await turn(ctx, "Which service has the smallest non-zero count?", [], str(uuid.uuid4()))
    check("Q073. names a real smallest-count service code", "please specify" not in r.lower(), r[:150])
    r = await turn(ctx, "Which service has the highest rejection count?", [], str(uuid.uuid4()))
    check("Q083. rejection-count ranking (0153, 10), not a file-count ranking",
          "0153" in r and "10" in r, r[:150])

    # ---- Q038/039: date extremes ----
    r = await turn(ctx, "Which application in ward 001 was submitted most recently?", [], str(uuid.uuid4()))
    check("Q038. a real date is named, not a ward-count comparison",
          "2026-09-2" in r and "leads" not in r.lower(), r[:200])
    r = await turn(ctx, "What was the earliest submission date for 0158?", [], str(uuid.uuid4()))
    check("Q039. earliest 0158 date = 2026-01-01", "2026-01-01" in r, r[:150])

    # ---- Q040: compound "same...but different..." stays one question ----
    r = await turn(ctx, "Are there applications from the same month but different submission channels?", [], str(uuid.uuid4()))
    check("Q040. a coherent, non-garbled answer (no stray numbered fragment)",
          not r.strip().startswith("1.") and len(r) > 10, r[:200])

    # ---- Q088: trailing-period register-wide listing ----
    r = await turn(ctx, "Show approved applications submitted in 2026.", [], str(uuid.uuid4()))
    check("Q088. register-wide approved count (82), not a narrow ISD-only projection",
          "82" in r, r[:250])
    r2 = await turn(ctx, "Show approved applications submitted in 2026", [], str(uuid.uuid4()))
    check("Q088b. identical result with or without the trailing period", ("82" in r) == ("82" in r2), r2[:100])

    # ---- Q096: channel breakdown, not a status breakdown ----
    r = await turn(ctx, "How many 0158 files came through each channel?", [], str(uuid.uuid4()))
    check("Q096. channel breakdown (CSC), not rejected/non-rejected",
          "csc" in r.lower() and "rejected" not in r.lower(), r[:150])

    # ---- Q105/108: rejected-exclusion count scoped correctly ----
    r = await turn(ctx, "How many applications are in ward 001?", [], str(uuid.uuid4()))
    check("Q105. ward-scoped rejected-exclusion count = 24, not the global 26",
          "24 rejected" in r, r[:300])
    r = await turn(ctx, "How many applications are in block 0055?", [], str(uuid.uuid4()))
    check("Q108. block-scoped rejected-exclusion count = 6, not the global 26",
          "6 rejected" in r, r[:300])

    # ---- Q107: blocks in a ward ----
    r = await turn(ctx, "Which blocks in ward 001 contain applications?", [], str(uuid.uuid4()))
    check("Q107. names real block numbers, not a pending-count answer",
          "0055" in r and "0001" in r, r[:200])

    # ---- Q113: jurisdiction yes/no ----
    r = await turn(ctx, f"Does {APP} fall within my jurisdiction?", [], str(uuid.uuid4()))
    check("Q113. direct Yes, not a substituted 'type: ISD' answer", "yes" in r.lower()[:20], r[:150])
    r = await turn(ctx, "Does 2026/0154/02/999999 fall within my jurisdiction?", [], str(uuid.uuid4()))
    check("Q113b. a nonexistent number is refused as not-found, never confirmed/denied for jurisdiction",
          "does not exist" in r.lower(), r[:150])

    # ---- Q122/123: IP comparison, jurisdiction-scoped ----
    r = await turn(ctx, f"Which applications share the same submission IP as {APP}?", [], str(uuid.uuid4()))
    check("Q122. names other applications with the same IP (12 of them)",
          "117.218.227.225" in r and "012547" not in r.split(":")[-1][:20], r[:300])
    r = await turn(ctx, f"Which applications have a different submission IP from {APP}?", [], str(uuid.uuid4()))
    check("Q123. a real listing of other applications, not just echoing the app's own IP",
          "application(s) have" in r.lower(), r[:200])

    # ---- Q129: composite payment fields ----
    r = await turn(ctx, f"What payment information is available for {APP}?", [], str(uuid.uuid4()))
    check("Q129. all three payment fields reported together", "650" in r and "234" in r and "treasury" in r.lower(), r[:250])

    # ---- Mandatory: missing-TSLR-record must still never be hallucinated ----
    r = await turn(ctx, f"What is the TSLR challan date for {NO_TSLR_APP}?", [], str(uuid.uuid4()))
    check("Mandatory. ISD file with zero TSLR rows -> refused, no invented date",
          "no tslr" in r.lower() and not any(d in r for d in ("2026-0", "2025-", "2027-")), r[:200])

    # ---- Fresh-topic regressions (section 15's explicit list) ----
    sid = str(uuid.uuid4())
    hist = []
    await turn(ctx, f"{APP} details", hist, sid)
    r = await turn(ctx, "what is TSLR?", hist, sid)
    check("Fresh-topic. single app -> 'what is TSLR?' -> definition, not app details",
          APP not in r, r[:150])
    sid = str(uuid.uuid4())
    hist = []
    await turn(ctx, "show pending applications", hist, sid)
    r = await turn(ctx, "what is TSLR?", hist, sid)
    check("Fresh-topic. application list -> 'what is TSLR?' -> definition, not list continuation",
          "application(s)" not in r.lower(), r[:150])
    sid = str(uuid.uuid4())
    hist = []
    await turn(ctx, f"{APP} details", hist, sid)
    r = await turn(ctx, "what is its ward?", hist, sid)
    check("Fresh-topic. single app -> 'what is its ward?' -> ward only",
          "001" in r or "orakadam" in r.lower(), r[:150])

    # ---- Register-only (non-0153/0154) "status of <app number>" named directly,
    # not with a pronoun -- used to fall through _REG_FIELD_CUES to the full
    # ~20-row othercode_detail card instead of a short status line. ----
    for stream in (False, True):
        r = await turn(ctx, f"Status of {FLINE_APP}", [], str(uuid.uuid4()), stream=stream)
        check(f"Repeated-card fix. register-only 'Status of <app>' -> short answer, no full card (stream={stream})",
              "data-table-card" not in r and FLINE_APP in r, r[:200])

    # ---- "What is the payment information?" right after discussing ONE
    # application used to be read as a register-wide fee_lookup (ignoring the
    # active application in context) instead of that file's own fee /
    # challan / payment mode via _payment_composite_answer(). ----
    for stream in (False, True):
        sid = str(uuid.uuid4())
        hist = []
        await turn(ctx, f"Show full application details for {APP}", hist, sid, stream=stream)
        r = await turn(ctx, "What is the payment information?", hist, sid, stream=stream)
        check(f"Repeated-card fix. 'payment information' after one app -> that app's fee, not register-wide fee_lookup (stream={stream})",
              APP in r and "650" in r and "234" in r and "across every service code" not in r.lower(), r[:250])

    # ---- Workflow-history bare follow-ups (no literal "workflow" in the message)
    # used to fall through _WFH_CUE to a generic "could not find that particular
    # detail" card: "what was the latest action?" (no "workflow"), "how many
    # workflow EVENTS are there?" (not "entries"), and "who performed the LAST
    # action?" (which must still answer WHO, not get hijacked by the new bare
    # "last action" -> latest-transition alternative). ----
    sid = str(uuid.uuid4())
    hist = []
    await turn(ctx, f"{APP} workflow history", hist, sid)
    r = await turn(ctx, "what was the latest action?", hist, sid)
    check("Workflow-history fix. bare 'latest action' (no word 'workflow') -> transition answer, not generic card",
          "could not find" not in r.lower() and APP in r, r[:200])
    r = await turn(ctx, "how many workflow events are there?", hist, sid)
    check("Workflow-history fix. 'workflow EVENTS' (not 'entries') -> count answer",
          re.search(r"\bhas\s+\d+\s+workflow\s+entr", r) is not None, r[:200])
    r = await turn(ctx, "who performed the last action?", hist, sid)
    check("Workflow-history fix. 'who performed the LAST action' -> WHO answer, not hijacked by bare-latest cue",
          "desk performed" in r.lower(), r[:200])

    # ---- "least" is one edit from "last" (_correct_typos' generic fuzzy-fix
    # rewrote it before parse_intent ever saw it), so a ranking question about
    # the FEWEST applications was silently turned into "my most recent
    # application" -- an unrelated personal-history lookup. ----
    r = await turn(ctx, "which service code has the least applications?", [], str(uuid.uuid4()))
    check("Typo-guard fix. 'least applications' not corrected to 'last applications'",
          "most recent application" not in r.lower() and "fewest" in r.lower(), r[:200])

    # ---- Q102: "how many wards are in my jurisdiction?" names both a level
    # ("ward") and the whole-jurisdiction word ("jurisdiction"), which used to
    # make the whole-word override win and return the full card with no
    # number stated anywhere in it. A "how many" question about one level now
    # states that level's own count. ----
    r = await turn(ctx, "how many wards are in my jurisdiction?", [], str(uuid.uuid4()))
    check("Jurisdiction-count fix. 'how many wards' states the actual count",
          "you have" in r.lower() and re.search(r"\d+", re.sub(r"<[^>]+>", "", r)) is not None
          and "ward" in r.lower(), r[:200])
    r = await turn(ctx, "what wards are in my jurisdiction?", [], str(uuid.uuid4()))
    check("Jurisdiction-count fix. plain listing (no 'how many') still gets the full card, not just a number",
          "your jurisdiction" in r.lower(), r[:200])

    # ---- Priority 3, section 3: cross-session isolation. A different session
    # (fresh session_id, empty client-supplied chat_history) for the SAME
    # officer must not inherit an application discussed in another session --
    # and conversely, follow-up context must be read from the SERVER-SIDE
    # session record (chat_messages.structured_data), not trusted from an
    # empty/absent client chat_history array, which would otherwise make the
    # whole follow-up mechanism a client-side claim an officer could forge. ----
    sid_a = str(uuid.uuid4())
    await turn(ctx, f"Show full application details for {APP}", [], sid_a)
    sid_b = str(uuid.uuid4())
    r = await turn(ctx, "what is its ward?", [], sid_b)
    check("Cross-session isolation. different session_id + empty history -> no leak of A's application",
          APP not in r, r[:200])
    r = await turn(ctx, "what is its ward?", [], sid_a)
    check("Server-side session authority. SAME session_id, empty CLIENT history -> still resolves from server-stored context",
          APP in r and "001" in r.lower() or "orakadam" in r.lower(), r[:200])

    # ---- Final safety fix: bare date-only "what happened" narratives must
    # never reach the LLM/agent fallback, which invented specific application
    # numbers, workflow steps and "officer's notes" tied to a date with no
    # grounded query behind any of it. Refused deterministically instead
    # (_bare_date_narrative / "bare_date_narrative" special-scope kind). ----
    for stream in (False, True):
        # 1. Bare date-only, fresh session.
        r = await turn(ctx, "What happened on 2026-08-05?", [], str(uuid.uuid4()), stream=stream)
        check(f"Date-narrative fix. bare date, fresh session -> clarification, no invented app/event (stream={stream})",
              "date alone isn't enough" in r.lower() and not re.search(r"\b20\d{2}/\d{3,4}/\d+/\d+\b", r), r[:200])

        # 2. Bare date-only, no date at all ("that day") -- still refused, not answered from thin air.
        r = await turn(ctx, "Tell me what happened that day.", [], str(uuid.uuid4()), stream=stream)
        check(f"Date-narrative fix. 'that day' with no explicit date -> clarification (stream={stream})",
              "date alone isn't enough" in r.lower(), r[:200])

        # 4. Explicit application number + date -> untouched, answers from real workflow history.
        r = await turn(ctx, f"What happened to {APP} on 2026-08-05?", [], str(uuid.uuid4()), stream=stream)
        check(f"Date-narrative fix. explicit app + date -> real workflow answer, not refused (stream={stream})",
              "date alone isn't enough" not in r.lower() and APP in r, r[:200])

    # 3. Date-only question after discussing an application -- must NOT silently
    # inherit that application and invent facts about it.
    sid, hist = str(uuid.uuid4()), []
    await turn(ctx, f"Show full application details for {APP}", hist, sid)
    r = await turn(ctx, "What happened on 2026-08-05?", hist, sid)
    check("Date-narrative fix. after discussing an application, bare date-only does NOT inherit it",
          "date alone isn't enough" in r.lower() and APP not in r, r[:200])

    # Date-only question after comparing two applications -- same rule.
    sid2, hist2 = str(uuid.uuid4()), []
    await turn(ctx, f"Compare {APP} and {FLINE_APP}", hist2, sid2)
    r = await turn(ctx, "What happened on 2026-08-05?", hist2, sid2)
    check("Date-narrative fix. after comparing two applications, bare date-only is still refused",
          "date alone isn't enough" in r.lower() and APP not in r and FLINE_APP not in r, r[:200])

    # 5/6/7. Legitimate, well-defined date queries must be untouched.
    r = await turn(ctx, "Which applications were submitted on 2026-08-05?", [], str(uuid.uuid4()))
    check("Date-narrative fix. well-defined 'submitted on <date>' query untouched",
          "date alone isn't enough" not in r.lower(), r[:200])
    r = await turn(ctx, "What field visits happened on 2026-08-05?", [], str(uuid.uuid4()))
    check("Date-narrative fix. well-defined 'field visits on <date>' query untouched",
          "date alone isn't enough" not in r.lower(), r[:200])

    # 8. Out-of-range date -- still refused deterministically, not "no records found" from the LLM.
    r = await turn(ctx, "What happened on 2099-01-01?", [], str(uuid.uuid4()))
    check("Date-narrative fix. out-of-range date -> same deterministic refusal",
          "date alone isn't enough" in r.lower(), r[:200])

    # 9. Tamil and Tanglish variants.
    r = await turn(ctx, "2026-08-05 அன்று என்ன நடந்தது?", [], str(uuid.uuid4()))
    check("Date-narrative fix. Tamil bare date-only -> Tamil clarification, no fabricated note",
          "தேதி மட்டும் போதாது" in r, r[:200])
    r = await turn(ctx, "2026-08-05 la enna nadandhu irukku?", [], str(uuid.uuid4()))
    check("Date-narrative fix. Tanglish bare date-only -> clarification, no fabricated note",
          "தேதி மட்டும் போதாது" in r, r[:200])

    # 12. The clarification itself must never be mistaken for, or accidentally
    # trigger, a full application-details card.
    r = await turn(ctx, "What happened on 2026-08-05?", [], str(uuid.uuid4()))
    check("Date-narrative fix. clarification never renders as a data-table-card",
          "data-table-card" not in r, r[:200])

    # ---- Q085, exact original wording recovered from qa150_results_rerun.jsonl
    # (qid 85): "How many services currently have pending applications?" used
    # to answer with the application TOTAL (44) and a per-code breakdown, but
    # never stated the count of distinct service codes the question actually
    # asked for. Fixed via _asks_distinct_service_count() / the
    # distinct_services branch of _render_all_service_code_answer(), counted
    # from len(by_svc) -- the same per-code dict the existing breakdown
    # ("0153 (9), 0154 (10), ...") is built from, never a separate query or a
    # hardcoded list. ----
    for stream in (False, True):
        r = await turn(ctx, "How many services currently have pending applications?", [], str(uuid.uuid4()), stream=stream)
        check(f"Q085 fix. original wording states distinct service count AND application total separately (stream={stream})",
              re.search(r"<strong>6</strong>\s*service\s*code", r) is not None and "44 application" in r
              and "data-table-card" not in r, r[:250])

    # Paraphrases the task requires supporting.
    r = await turn(ctx, "How many service codes have pending files?", [], str(uuid.uuid4()))
    check("Q085 fix. paraphrase 'service codes ... pending files' -> same distinct count, not the 29-row guide table",
          re.search(r"<strong>6</strong>\s*service\s*code", r) is not None and "Service Codes Guide" not in r, r[:250])
    r = await turn(ctx, "How many services have pending applications?", [], str(uuid.uuid4()))
    check("Q085 fix. paraphrase 'How many services have pending applications?'",
          re.search(r"<strong>6</strong>\s*service\s*code", r) is not None, r[:250])

    # Must NOT hijack genuine application-count questions (no "service(s)"
    # named as the counted noun).
    r = await turn(ctx, "How many applications are pending?", [], str(uuid.uuid4()))
    check("Q085 fix. plain 'how many applications are pending' stays an application count, not a service count",
          "44" in r and "service code(s) currently have" not in r, r[:250])
    r = await turn(ctx, "How many applications does 0158 have?", [], str(uuid.uuid4()))
    check("Q085 fix. single-code application count untouched",
          "service code(s) currently have" not in r, r[:250])

    # Zero qualifying services -- answered explicitly as 0, from a real (empty)
    # query result, never silently folded into the generic empty-list wording.
    r = await turn(ctx, "How many services currently have pending applications submitted in 2099?", [], str(uuid.uuid4()))
    check("Q085 fix. zero qualifying services -> explicit '0', not a generic empty-list sentence",
          re.search(r"<strong>0</strong>\s*service\s*code", r) is not None and "0 application" in r, r[:250])

    # Tamil.
    r = await turn(ctx, "எத்தனை சேவை குறியீடுகளில் நிலுவையில் உள்ள விண்ணப்பங்கள் உள்ளன?", [], str(uuid.uuid4()))
    check("Q085 fix. Tamil wording states the distinct count",
          "6 சேவை" in r and "44" in r, r[:250])

    passed = sum(1 for _, ok, _ in _results if ok)
    total = len(_results)
    print(f"\n{passed}/{total} passed")
    if passed != total:
        print("\nFailures:")
        for name, ok, detail in _results:
            if not ok:
                print(f"  - {name}: {detail}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
