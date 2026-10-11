"""Every service code is treated alike unless the question names ISD/NISD.

"Show my pending applications" used to answer from `applications`, which holds
only ISD/NISD, so an officer with 47 pending files saw 17. The same officer was
told 57, 36 or 152 applications depending on how the question was phrased.

    python test_register_equal_treatment.py            # routing + database
    python test_register_equal_treatment.py --routing  # routing only, no database

Expected numbers are recomputed from `service_register_entries`, so this still
means something after a reseed.
"""
import asyncio
import logging
import re
import sys
import uuid

logging.disable(logging.CRITICAL)

from backend.services.rag import parse_intent

REGISTER = "all_service_code_applications"

ROUTING = [
    # generic questions -> the whole register
    ("show my pending applications", REGISTER),
    ("how many pending applications do I have", REGISTER),
    ("show my applications", REGISTER),
    ("list approved applications", REGISTER),
    ("show rejected applications", REGISTER),
    ("show applications in June", REGISTER),
    ("pending applications in ward 001", REGISTER),
    ("pending applications between 2026-06-01 and 2026-06-30", REGISTER),
    ("show first 5 pending applications", REGISTER),
    ("my CSC applications", REGISTER),
    ("pending apps", REGISTER),
    ("நிலுவையில் உள்ள விண்ணப்பங்கள்", REGISTER),
    # questions that name ISD/NISD or something only they carry stay detailed
    ("show my pending ISD applications", "pending_applications"),
    ("show NISD applications", "nisd_applications"),
    ("overdue applications", "overdue_applications"),
    ("escalated applications", "pending_applications"),
    ("pending applications for survey 5", "pending_applications"),
    ("show pending applications along with applicant name", "pending_applications"),
]


def check_routing() -> int:
    bad = 0
    for message, want in ROUTING:
        got = parse_intent(message)
        if got != want:
            bad += 1
            print(f"  FAIL routing: {message!r} -> {got} (wanted {want})")
    got = parse_intent("last month", prev_intent=REGISTER)
    if got != REGISTER:
        bad += 1
        print(f"  FAIL routing: bare date after a register list -> {got}")

    # A bare display command immediately after a workload summary must open the
    # same all-service-code register, rather than being rejected as an orphan fragment.
    for message in ("show", "list", "display", "please show", "காட்டு"):
        got = parse_intent(message, prev_intent="officer_workload")
        if got != REGISTER:
            bad += 1
            print(f"  FAIL bare workload follow-up: {message!r} -> {got} (wanted {REGISTER})")
    print(f"routing: {len(ROUTING) + 1 - bad}/{len(ROUTING) + 1} ok")
    return bad


def _text(html: str) -> str:
    return re.sub(r"<[^>]+>", " ", html or "")


async def check_database() -> int:
    from sqlalchemy import func, select
    from backend.database import AsyncSessionLocal
    from backend.models import ServiceRegisterEntry as SRE, SISOfficer
    from backend.schemas import OfficerContext
    from backend.services import chatbot
    from backend.services.auth_service import get_officer_jurisdiction_ids

    bad = 0

    def expect(label, got, want):
        nonlocal bad
        if got != want:
            bad += 1
            print(f"  FAIL {label}: got {got!r}, wanted {want!r}")
        else:
            print(f"  ok   {label}: {got}")

    async with AsyncSessionLocal() as db:
        officer = (await db.execute(select(SISOfficer).order_by(SISOfficer.employee_id))).scalars().first()
        jur = await get_officer_jurisdiction_ids(officer.id, db)
        ids = [i for i in (jur["district_ids"] + jur["taluk_ids"] + jur["town_ids"]
                           + jur["ward_ids"] + jur["block_ids"]) if i]
        ctx = OfficerContext(
            officer_id=officer.id, employee_id=officer.employee_id, name=officer.name,
            email=officer.email, designation=officer.designation,
            jurisdiction_type=jur["jurisdiction_type"], jurisdiction_name=jur["jurisdiction_name"],
            jurisdiction_ids=ids)

        async def n_register(*where):
            return await db.scalar(select(func.count()).select_from(SRE)
                                   .where(SRE.ward_id.in_(ids), *where)) or 0

        pending = await n_register(SRE.status == "pending")
        approved = await n_register(SRE.status == "approved")
        total = await n_register()
        codes = {c for (c,) in (await db.execute(
            select(SRE.service_code).where(SRE.ward_id.in_(ids), SRE.status == "pending").distinct())).all()}
        other_codes = codes - {"0153", "0154"}

    async def ask(message, history=None, sid=None):
        async with AsyncSessionLocal() as db:
            return await chatbot.process_chat(message, sid or str(uuid.uuid4()), ctx, db, history or [])

    r = await ask("show my pending applications")
    text = _text(r["response"])
    shown = set(re.findall(r"/(\d{4})/", text))
    expect("pending listing covers every service code that has pending files", shown, codes)
    expect("pending listing row count", len(re.findall(r"<tr><td>", r["response"])), pending)
    if not other_codes:
        print("  note: no pending file outside ISD/NISD in this data; the check above is weaker")

    r = await ask("how many pending applications do I have")
    expect("pending count answer", int(re.search(r"Found\s+(\d+)", _text(r["response"])).group(1)), pending)

    r = await ask("compare pending and approved")
    m = re.search(r"pending (\d+), approved (\d+)", r["response"])
    expect("pending vs approved", (int(m.group(1)), int(m.group(2))) if m else None, (pending, approved))

    r = await ask("how many applications do I have")
    expect("total applications", int(re.search(r"(\d+) applications in total", r["response"]).group(1)), total)

    r = await ask("applications per year")
    expect("per-year total", int(re.search(r"total (\d+)", r["response"]).group(1)), total)

    # a follow-up counts the rows on screen, not just the ISD/NISD ones
    sid = str(uuid.uuid4())
    async with AsyncSessionLocal() as db:
        sid = str((await chatbot.create_chat_session(db, str(officer.id))).id)
    first = await ask("show my pending applications", sid=sid)
    history = [{"role": "user", "content": "show my pending applications"},
               {"role": "assistant", "content": first["response"]}]
    r = await ask("how many of them are citizen?", history, sid)
    m = re.search(r"of those (\d+) applications", r["response"])
    expect("follow-up denominator is the full list", int(m.group(1)) if m else None, pending)

    r = await ask("show my pending ISD applications")
    expect("ISD-specific question keeps the ISD/NISD handler", r["intent"], "pending_applications")
    print(f"database: {bad} failure(s)")
    return bad


if __name__ == "__main__":
    failures = check_routing()
    if "--routing" not in sys.argv:
        failures += asyncio.run(check_database())
    sys.exit(1 if failures else 0)
