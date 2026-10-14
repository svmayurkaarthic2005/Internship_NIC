"""
Regression test: cross-application IP comparison must not crash.

Defect: two different features were both named `_ip_comparison_answer`.
  - cross-application comparison  (db, officer, message, ctx, is_tamil)
    "which applications share the same IP address?" / "do they share an IP?"
  - same-IP-as-one-file lookup    (db, officer, message, gate_app, chat_history, ta)
    "which applications share the same submission IP as <app>?"
In the original single-file chatbot.py the second definition silently
replaced the first, so both chat pipelines -- which call the cross-application
one with 5 arguments -- raised TypeError (missing 'ta') on every
cross-application IP question.

The database is replaced by synthetic fixtures (mocked get_ip_comparison /
get_applications_by_submission_ip), so no real application or citizen data
is involved and results are deterministic. The pipeline cases still need the
live database for the officer context and the earlier pipeline stages; pass
--no-db to run only the unit cases.
"""
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Clearly synthetic identifiers -- never real register data.
APP_A, APP_B, APP_C = "9999/0154/99/000001", "9999/0154/99/000002", "9999/0153/99/000003"
CROSS_FIXTURE = {
    "count": 3, "groups": [("10.236.251.40", [APP_A, APP_B])], "singles": {APP_C: "203.0.113.7"},
    "no_ip": [], "dropped": 0, "requested": 0,
}
SAME_AS_FIXTURE = {"ip": "10.236.251.40", "applications": [{"application_number": APP_B, "submission_ip": "10.236.251.40"}]}

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"{'ok  ' if cond else 'FAIL'} {name}" + (f" -- {detail}" if detail and not cond else ""))


async def unit_cases():
    from backend.services import chatbot, chat_application_queries, chat_untracked_fields
    officer = SimpleNamespace(officer_id="test", jurisdiction_ids=[])

    # 1. The name the pipelines call must accept the pipelines' 5 arguments.
    with mock.patch("backend.services.postgres.get_ip_comparison",
                    mock.AsyncMock(return_value=CROSS_FIXTURE)) as gic:
        try:
            text = await chatbot._ip_cross_comparison_answer(
                None, officer, "which applications share the same IP address?", None, False)
            err = None
        except Exception as e:  # noqa: BLE001 -- the defect under test IS an exception
            text, err = "", e
    check("cross-application comparison runs with the pipelines' 5 arguments", err is None, repr(err))
    check("...computes groups from the register query", gic.await_count == 1)
    check("...reports the shared IP group", f"{APP_A}, {APP_B}" in text and "share an IP address" in text, text)

    # 2. "do they share an IP?" with a list in view compares exactly that list.
    ctx = SimpleNamespace(application_numbers=[APP_A, APP_B])
    with mock.patch("backend.services.postgres.get_ip_comparison",
                    mock.AsyncMock(return_value={**CROSS_FIXTURE, "count": 2, "singles": {}})) as gic:
        text = await chatbot._ip_cross_comparison_answer(None, officer, "do they share the same IP address?", ctx, False)
    check("'they' compares the carried list", gic.await_args.args[2] == [APP_A, APP_B], str(gic.await_args))
    check("...and answers yes for one shared group", text.startswith("Yes"), text)

    # 3. Tamil rendering still works.
    with mock.patch("backend.services.postgres.get_ip_comparison",
                    mock.AsyncMock(return_value=CROSS_FIXTURE)):
        ta_text = await chatbot._ip_cross_comparison_answer(None, officer, "same ip address", None, True)
    check("Tamil cross-application answer", "IP" in ta_text and "பகிர்கின்றன" in ta_text, ta_text)

    # 4. The other feature (same IP as ONE named file) is unchanged and keeps its name.
    with mock.patch("backend.services.postgres.get_applications_by_submission_ip",
                    mock.AsyncMock(return_value=SAME_AS_FIXTURE)):
        same_as = await chat_application_queries._ip_comparison_answer(
            None, officer, f"which applications share the same submission ip as {APP_A}?", None, [], False)
    check("same-IP-as-one-file lookup still answers", same_as and APP_B in same_as and "same submission IP" in same_as,
          str(same_as))
    none_case = await chat_application_queries._ip_comparison_answer(
        None, officer, f"status of {APP_A}", None, [], False)
    check("same-IP-as-one-file lookup declines unrelated questions", none_case is None)

    # 5. A single application's IP answer is unaffected.
    one = chat_untracked_fields._ip_answer({"submission_ip": "10.236.251.40"}, APP_A, False)
    check("single-application IP answer unchanged", "internal network" in one and APP_A in one, one)

    # 6. Both features exist as distinct functions (no silent shadowing).
    check("the two features are distinct functions",
          chatbot._ip_cross_comparison_answer is chat_untracked_fields._ip_cross_comparison_answer
          and chatbot._ip_comparison_answer is chat_application_queries._ip_comparison_answer
          and chatbot._ip_cross_comparison_answer is not chatbot._ip_comparison_answer)


async def pipeline_cases():
    from sqlalchemy import select
    from backend.database import AsyncSessionLocal
    from backend.models import SISOfficer
    from backend.schemas import OfficerContext
    from backend.services import chatbot
    from backend.services.auth_service import get_officer_jurisdiction_ids

    msg = "Which applications share the same IP address?"
    async with AsyncSessionLocal() as db:
        om = (await db.execute(select(SISOfficer).limit(1))).scalar_one()
        jur = await get_officer_jurisdiction_ids(om.id, db)
        ids = jur["district_ids"] + jur["taluk_ids"] + jur["town_ids"] + jur["ward_ids"] + jur["block_ids"]
        officer = OfficerContext(officer_id=om.id, employee_id=om.employee_id, name=om.name, email=om.email,
                                 designation=om.designation, jurisdiction_type=jur["jurisdiction_type"],
                                 jurisdiction_name=jur["jurisdiction_name"], jurisdiction_ids=[i for i in ids if i])
        with mock.patch("backend.services.postgres.get_ip_comparison", mock.AsyncMock(return_value=CROSS_FIXTURE)), \
             mock.patch("backend.services.chatbot.save_chat_messages", mock.AsyncMock()):
            ns = await chatbot.process_chat(msg, "ip-compare-test", officer, db, [])
            frames = []
            async for chunk in chatbot.process_chat_stream(msg, "ip-compare-test", officer, db, []):
                for line in (chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk).splitlines():
                    if line.startswith("data: "):
                        try:
                            frames.append(json.loads(line[6:]))
                        except ValueError:
                            pass
    streamed = "".join(f.get("content", "") for f in frames if isinstance(f, dict))
    check("non-streaming pipeline answers the IP comparison",
          ns.get("intent") == "untracked_field" and f"{APP_A}, {APP_B}" in (ns.get("response") or ""),
          f"intent={ns.get('intent')} response={(ns.get('response') or '')[:160]!r}")
    check("streaming pipeline answers the IP comparison", f"{APP_A}, {APP_B}" in streamed, streamed[:160])


if __name__ == "__main__":
    asyncio.run(unit_cases())
    if "--no-db" not in sys.argv:
        asyncio.run(pipeline_cases())
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    sys.exit(1 if failed else 0)
