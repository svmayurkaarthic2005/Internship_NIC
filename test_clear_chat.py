"""Regression tests for clearing a conversation and for field-visit rule polarity.

    python test_clear_chat.py            # everything (DB, Node; no LLM)
    python test_clear_chat.py --routing  # no database

1. Clear command -> `action="clear_chat"` on both chat paths.
2. The frontend lifecycle (`test_clear_chat_frontend.js`, run under Node): New
   Chat during a stream, stale responses, session-creation failures / timeout /
   retry, double clicks, late session replies. Skipped when `node` is missing.
3. Context isolation after a clear, against the live database: the old
   application cannot come back through the follow-up context, the history
   endpoint, or an answer; the rows stay for audit; another officer sees
   nothing; ordinary questions still work.
4. Field-visit rule polarity: a negation flips the answer only when it governs
   the field-visit requirement. Answers are checked against SIS_URBAN_SERVICES.

Writes only chat_sessions / chat_messages rows (as any chat turn does); never
deletes anything.
"""
from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from backend.services import chatbot  # noqa: E402
from backend.utils.helpers import SIS_URBAN_SERVICES  # noqa: E402

_results: list[tuple[str, str, str]] = []   # (name, PASS/FAIL/SKIP, detail)


def check(name, ok, detail=""):
    _results.append((name, "PASS" if ok else "FAIL", detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  -- {detail}"))


def skip(name, why):
    _results.append((name, "SKIP", why))
    print(f"  SKIP  {name}  -- {why}")


# ---------------------------------------------------------------------------
# 1. clear-command detection (no DB)
# ---------------------------------------------------------------------------

def test_clear_detection():
    print("\n[1] clear-command detection")
    for m in ("clear", "clear chat", "clear the conversation", "reset", "new chat", "cls", "அழி",
              "would you mind clearing the conversation?"):
        check(f"{m!r} is a clear command", chatbot._contentless_message(m) == "clear")
    for m in ("clear my pending applications", "delete application 2026/0154/28/001280",
              "what is ISD?", "which applications were approved"):
        check(f"{m!r} is not a clear command", chatbot._contentless_message(m) != "clear")


# ---------------------------------------------------------------------------
# 2. frontend lifecycle (Node harness)
# ---------------------------------------------------------------------------

def test_frontend():
    print("\n[2] frontend lifecycle (node test_clear_chat_frontend.js)")
    js = (ROOT / "frontend/js/chat.js").read_text(encoding="utf-8")
    check("chat.js acts on the streamed clear_chat action", "parsed.action === 'clear_chat'" in js)
    node = shutil.which("node")
    if not node:
        skip("frontend lifecycle harness", "node is not installed")
        return
    syn = subprocess.run([node, "--check", str(ROOT / "frontend/js/chat.js")], capture_output=True, text=True)
    check("chat.js parses (node --check)", syn.returncode == 0, syn.stderr[-300:])
    r = subprocess.run([node, str(ROOT / "test_clear_chat_frontend.js")], capture_output=True,
                       text=True, encoding="utf-8", timeout=120)
    tail = (r.stdout or "").strip().splitlines()[-1:] or [""]
    fails = [ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip().startswith("FAIL")]
    check(f"frontend harness: {tail[0]}", r.returncode == 0 and not fails, "; ".join(fails)[:600] or r.stderr[-300:])


# ---------------------------------------------------------------------------
# 4. field-visit polarity (no DB)
# ---------------------------------------------------------------------------

def _codes(wants: bool) -> list[str]:
    return sorted(c for c, i in SIS_URBAN_SERVICES.items() if bool(i.get("requires_field_visit")) == wants)


def test_field_visit_polarity():
    print("\n[4] field-visit rule polarity")
    topic = chatbot._igrs_can_rule_topic
    cases = [
        ("Which service codes require a field visit?", "fv_needed_catalog"),
        ("Which service codes do not require a field visit?", "fv_not_needed_catalog"),
        ("Which service codes require a field visit without prior approval?", "fv_needed_catalog"),
        ("Which codes do not need a field inspection?", "fv_not_needed_catalog"),
        ("Which codes require a field inspection?", "fv_needed_catalog"),
        ("which service codes need no field visit", "fv_not_needed_catalog"),
        ("which service codes can be done without a field visit", "fv_not_needed_catalog"),
        ("which service codes don't need a field visit", "fv_not_needed_catalog"),
        # unrelated negations must not flip the answer
        ("which service codes require a field visit, not just a document check?", "fv_needed_catalog"),
        ("which service codes require a field visit if no sketch is available?", "fv_needed_catalog"),
        ("which types of applications need a field visit without any delay", "fv_needed"),
        ("which types of applications don't need a field visit", "fv_not_needed"),
        ("why is a field visit not needed for NISD", "fv_not_needed"),
        # Tamil / Tanglish
        ("எந்த சேவைக்கு கள ஆய்வு தேவை", "fv_needed"),
        ("எந்த சேவைக்கு கள ஆய்வு தேவையில்லை", "fv_not_needed"),
        ("entha service ku field visit venum", "fv_needed"),
        ("entha service ku field visit vendam", "fv_not_needed"),
    ]
    for q, want in cases:
        got = topic(q)
        check(f"{q!r} -> {want}", got == want, f"got {got}")

    # "Which ISD/NISD applications require a field visit?" asks about the
    # officer's applications, not the rule -- it must not become a catalog
    # answer of either polarity.
    q = "Which ISD/NISD applications require a field visit?"
    check(f"{q!r} is not a rule-catalog question", topic(q) not in ("fv_needed_catalog", "fv_not_needed_catalog"),
          f"got {topic(q)}")

    # The answers, checked against the catalogue rather than a hardcoded list.
    yes, no = _codes(True), _codes(False)
    for t, codes, other in (("fv_needed_catalog", yes, no), ("fv_not_needed_catalog", no, yes)):
        ans = chatbot._igrs_can_rule_answer(t, "en")
        listed = re.findall(r"\b(01\d\d)\b \(", ans)
        check(f"{t} lists exactly the catalogue's codes ({len(codes)})", listed == codes, f"{listed} vs {codes}")
        check(f"{t} lists none of the opposite codes", not set(listed) & set(other))
        ans_ta = chatbot._igrs_can_rule_answer(t, "ta")
        check(f"{t} (Tamil) gives the same count", ans_ta.startswith(f"{len(codes)} "))
    check("the two catalogue answers cover all codes", len(yes) + len(no) == len(SIS_URBAN_SERVICES))
    without_approval = chatbot._igrs_can_rule_answer(
        topic("Which service codes require a field visit without prior approval?"), "en")
    check("'... without prior approval' is answered with the codes that DO need a visit",
          without_approval.startswith(f"{len(yes)} service codes require a field visit"), without_approval[:120])


# ---------------------------------------------------------------------------
# 1b + 3. live: the clear action and context isolation
# ---------------------------------------------------------------------------

OFFICER_A = "marun_chn@sis.tn.gov.in"
OFFICER_B = "arun_m@sis.tn.gov.in"
_APP_RE = re.compile(r"\b\d{4}/\d{4}/\d{2}/\d{6}\b")


async def _ctx(email):
    from sqlalchemy import select
    from backend.database import AsyncSessionLocal
    from backend.models import SISOfficer
    from backend.schemas import OfficerContext
    from backend.services.auth_service import get_officer_jurisdiction_ids
    async with AsyncSessionLocal() as db:
        o = (await db.execute(select(SISOfficer).where(SISOfficer.email == email))).scalar_one()
        jur = await get_officer_jurisdiction_ids(o.id, db)
        ids = jur["district_ids"] + jur["taluk_ids"] + jur["town_ids"] + jur["ward_ids"] + jur["block_ids"]
        return OfficerContext(officer_id=o.id, employee_id=o.employee_id, name=o.name, email=o.email,
                              designation=o.designation, jurisdiction_type=jur["jurisdiction_type"],
                              jurisdiction_name=jur["jurisdiction_name"], jurisdiction_ids=[i for i in ids if i])


async def _turn(ctx, sid, msg, history=None):
    from backend.database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        return await chatbot.process_chat(db=db, message=msg, officer=ctx, chat_history=history or [], session_id=sid)


async def _turn_stream(ctx, sid, msg):
    from backend.database import AsyncSessionLocal
    frames = []
    async with AsyncSessionLocal() as db:
        async for ev in chatbot.process_chat_stream(db=db, message=msg, officer=ctx, chat_history=[], session_id=sid):
            s = ev.decode() if isinstance(ev, bytes) else ev
            if isinstance(s, str) and s.startswith("data: "):
                try:
                    frames.append(json.loads(s[6:]))
                except ValueError:
                    pass
    return frames


async def _new_session(ctx) -> str:
    from backend.database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        return str((await chatbot.create_chat_session(db, str(ctx.officer_id))).id)


async def _rows(sid) -> int:
    from sqlalchemy import select, func
    from backend.database import AsyncSessionLocal
    from backend.models import ChatMessage
    async with AsyncSessionLocal() as db:
        return (await db.execute(select(func.count()).select_from(ChatMessage)
                                 .where(ChatMessage.session_id == sid))).scalar_one()


async def _fctx(sid, officer_id, message="when was it submitted?"):
    from backend.database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        return await chatbot._load_followup_context(db, sid, [], message, officer_id=officer_id)


async def _history_endpoint(sid, ctx):
    from backend.database import AsyncSessionLocal
    from backend.routers.chat import get_chat_session_history
    async with AsyncSessionLocal() as db:
        r = await get_chat_session_history(session_id=sid, limit=100, db=db, current_officer=ctx)
    return (r.data if hasattr(r, "data") else r["data"])["messages"]


async def live():
    print("\n[1b] the clear action on both chat paths")
    a = await _ctx(OFFICER_A)
    b = await _ctx(OFFICER_B)
    r = await _turn(a, await _new_session(a), "clear")
    check("process_chat('clear') returns action=clear_chat", r.get("action") == "clear_chat", str(r)[:200])
    frames = await _turn_stream(a, await _new_session(a), "clear the conversation")
    check("process_chat_stream('clear the conversation') streams action=clear_chat",
          any(f.get("action") == "clear_chat" for f in frames), str(frames)[:200])

    print("\n[3] context isolation after a clear (same session, as an API client would keep it)")
    sid = await _new_session(a)
    r1 = await _turn(a, sid, "show my pending applications")
    apps = _APP_RE.findall(r1.get("response") or "") or _APP_RE.findall(json.dumps(r1.get("table_data") or {}, default=str))
    if not apps:
        skip("context isolation", f"officer {OFFICER_A} has no pending applications to talk about")
        return
    app = apps[0]
    hist = [{"role": "user", "content": "show my pending applications"},
            {"role": "assistant", "content": r1.get("response") or ""}]
    r2 = await _turn(a, sid, f"status of {app}", hist)
    hist += [{"role": "user", "content": f"status of {app}"}, {"role": "assistant", "content": r2.get("response") or ""}]
    before = await _fctx(sid, a.officer_id)
    check("before the clear, the follow-up context carries the application",
          before is not None and app in (before.application_numbers or []), str(before)[:200])
    check("before the clear, the history endpoint returns the conversation", len(await _history_endpoint(sid, a)) >= 4)
    rows_before = await _rows(sid)

    rc = await _turn(a, sid, "clear", hist)
    check("the clear turn returns action=clear_chat", rc.get("action") == "clear_chat")
    check("after the clear, no follow-up context is left", await _fctx(sid, a.officer_id) is None)
    check("after the clear, the history endpoint restores nothing", await _history_endpoint(sid, a) == [])
    check("the clear deleted no rows (kept for audit)", await _rows(sid) == rows_before,
          f"{rows_before} -> {await _rows(sid)}")
    from backend.database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        recent = await chatbot._recent_answers(db, sid, [], 5)
    check("the back-reference reader sees no pre-clear answer", recent == [], str(recent)[:200])

    r3 = await _turn(a, sid, "what is ISD?")
    check("'what is ISD?' is answered after the clear", re.search(r"sub-?division", r3.get("response") or "", re.I),
          (r3.get("response") or "")[:200])
    check("'what is ISD?' does not leak the old application", app not in (r3.get("response") or ""))
    r4 = await _turn(a, sid, "when was it submitted?")
    check("a bare follow-up after the clear does not resolve to the old application",
          app not in (r4.get("response") or "") + json.dumps(r4.get("table_data") or {}, default=str),
          (r4.get("response") or "")[:200])
    ctx_after = await _fctx(sid, a.officer_id)
    check("the new conversation's context never names the old application",
          ctx_after is None or app not in (ctx_after.application_numbers or []), str(ctx_after)[:200])

    print("\n[3b] another officer")
    check("officer B gets no follow-up context from officer A's session", await _fctx(sid, b.officer_id) is None)
    check("officer B gets no history from officer A's session", await _history_endpoint(sid, b) == [])

    print("\n[3c] ordinary queries after a clear")
    r5 = await _turn(a, sid, "show my pending applications")
    check("a listing still works after the clear", bool(_APP_RE.search((r5.get("response") or "") +
                                                                        json.dumps(r5.get("table_data") or {}, default=str))),
          (r5.get("response") or "")[:200])
    check("the new listing becomes the follow-up context", (await _fctx(sid, a.officer_id, "which is oldest?")) is not None)


def main():
    routing_only = "--routing" in sys.argv
    test_clear_detection()
    test_field_visit_polarity()
    test_frontend()
    if routing_only:
        skip("live DB sections", "--routing")
    else:
        try:
            asyncio.run(live())
        except (OSError, ConnectionError) as exc:
            skip("live DB sections", f"database unavailable: {exc}")
    counts = {k: sum(1 for _, s, _ in _results if s == k) for k in ("PASS", "FAIL", "SKIP")}
    print(f"\n{counts['PASS']} passed, {counts['FAIL']} failed, {counts['SKIP']} skipped")
    sys.exit(1 if counts["FAIL"] else 0)


if __name__ == "__main__":
    main()
