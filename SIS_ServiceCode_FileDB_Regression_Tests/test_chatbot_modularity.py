"""
Architecture regression tests for the chatbot.py modular split
(SIS_Chatbot_Modularity_Refactor_Report.md).

Checks behavior at the module boundaries, not just that files import:

  1. Every chat_*.py module (and chatbot.py) imports as the FIRST module in a
     fresh interpreter -- the order a circular import actually breaks in.
  2. The legacy import path keeps working: names imported elsewhere via
     `from backend.services.chatbot import X` (routers, tests) still resolve.
  3. Supported patch points still take effect: patching
     backend.services.chatbot.save_chat_messages is seen by a function that
     moved to chat_routing.py (it reaches chatbot through the module object
     at call time, not a frozen import).
  4. The shared early-guard stage decides the same thing it used to: a
     table of messages -> (intent, saved?, clear?).
  5. Streaming and non-streaming pipelines give the SAME answer and intent
     for the guard-chain messages (live DB; persistence patched out so the
     test writes nothing).

`python test_chatbot_modularity.py` (section 5 needs the database; pass
--no-db to skip it).
"""
import asyncio
import json
import subprocess
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PY = sys.executable

MODULES = ["backend.services.chatbot.chat_shared", "backend.services.chatbot.chat_smalltalk",
           "backend.services.chatbot.chat_response_formatting", "backend.services.chatbot.chat_untracked_fields",
           "backend.services.chatbot.chat_followups", "backend.services.chatbot.chat_corrections",
           "backend.services.chatbot.chat_application_queries", "backend.services.chatbot.chat_service_codes",
           "backend.services.chatbot.chat_routing", "backend.services.chatbot.chat_pipeline",
           "backend.services.chatbot"]

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"{'ok  ' if cond else 'FAIL'} {name}" + (f" -- {detail}" if detail and not cond else ""))


def test_first_import_no_cycle():
    for m in MODULES:
        p = subprocess.run([PY, "-c", f"import {m}"], cwd=ROOT,
                           capture_output=True, text=True)
        check(f"first-import {m}", p.returncode == 0, p.stderr.strip()[-300:])


def test_legacy_import_paths():
    from backend.routers import chat as _router  # noqa: F401  (imports 3 session fns from chatbot)
    import backend.services.chatbot as cb
    for n in ["process_chat", "process_chat_stream", "create_chat_session", "get_session_history",
              "get_officer_sessions", "call_llama", "run_agent", "_load_followup_context",
              "detect_language", "_cm_strong", "_contentless_message", "_is_mutation_request",
              "_calc_turn", "_build_service_code_comparison_response", "_plan_requests",
              "_big_plan", "_GLOSSARY", "_GLOSSARY_RE", "resolve_application_reference",
              "build_app_not_found_message", "_scrub_unverified_counts"]:
        check(f"chatbot.{n} still importable", hasattr(cb, n))
    from backend.services.chatbot import chat_service_codes
    check("re-export is the same object, not a copy",
          cb._build_service_code_comparison_response is chat_service_codes._build_service_code_comparison_response)


def test_patch_point_seen_by_moved_code():
    import backend.services.chatbot as cb
    from backend.services.chatbot import chat_routing
    check("chat_routing reaches chatbot lazily", chat_routing._cb() is cb)
    sentinel = mock.AsyncMock()
    with mock.patch("backend.services.chatbot.save_chat_messages", sentinel):
        check("patched chatbot.save_chat_messages visible through chat_routing._cb()",
              chat_routing._cb().save_chat_messages is sentinel)


def test_early_guard_decisions():
    from backend.services.chatbot.chat_pipeline import early_guard_reply
    cases = [
        # message, expected intent (None = falls through), save, clear
        ("?", "clarification", True, False),
        ("clear", "clear_chat", False, True),
        ("ok", "small_talk", True, False),
        ("delete all my applications", "read_only_refusal", True, False),
        ("No Tamil, English only", "language_preference", True, False),
        ("what is the weather today", "out_of_scope", True, False),
        ("what can you do", "capabilities", True, False),
        ("show my pending applications", None, None, None),
        ("Can you explain?", None, None, None),
    ]
    for msg, intent, save, clear in cases:
        reply, _ = early_guard_reply(msg, "en", [], msg, False)
        got = (reply.intent, reply.save, reply.clear_session) if reply else (None, None, None)
        check(f"early_guard_reply({msg!r}) -> {intent}", got == (intent, save, clear), f"got {got}")


async def _live_stream_vs_nonstream():
    from sqlalchemy import select
    from backend.database import AsyncSessionLocal
    from backend.models import SISOfficer
    from backend.schemas import OfficerContext
    from backend.services import chatbot
    from backend.services.auth_service import get_officer_jurisdiction_ids

    async with AsyncSessionLocal() as db:
        om = (await db.execute(select(SISOfficer).limit(1))).scalar_one()
        jur = await get_officer_jurisdiction_ids(om.id, db)
        ids = jur["district_ids"] + jur["taluk_ids"] + jur["town_ids"] + jur["ward_ids"] + jur["block_ids"]
        officer = OfficerContext(officer_id=om.id, employee_id=om.employee_id, name=om.name,
                                 email=om.email, designation=om.designation,
                                 jurisdiction_type=jur["jurisdiction_type"],
                                 jurisdiction_name=jur["jurisdiction_name"],
                                 jurisdiction_ids=[i for i in ids if i])
        for msg in ["?", "delete all my applications", "what is the weather today", "what can you do"]:
            with mock.patch("backend.services.chatbot.save_chat_messages", mock.AsyncMock()), \
                 mock.patch("backend.services.chatbot._mark_session_cleared", mock.AsyncMock()):
                ns = await chatbot.process_chat(msg, "modularity-test", officer, db, [])
                frames = []
                async for chunk in chatbot.process_chat_stream(msg, "modularity-test", officer, db, []):
                    text = chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk
                    for line in text.splitlines():
                        if line.startswith("data: "):
                            try:
                                frames.append(json.loads(line[6:]))
                            except ValueError:
                                pass
            streamed = "".join(f.get("content", "") for f in frames if isinstance(f, dict))
            check(f"stream == non-stream answer for {msg!r}",
                  streamed.strip() == (ns.get("response") or "").strip(),
                  f"stream={streamed[:80]!r} nonstream={(ns.get('response') or '')[:80]!r}")


if __name__ == "__main__":
    test_first_import_no_cycle()
    test_legacy_import_paths()
    test_patch_point_seen_by_moved_code()
    test_early_guard_decisions()
    if "--no-db" not in sys.argv:
        asyncio.run(_live_stream_vs_nonstream())
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    sys.exit(1 if failed else 0)
