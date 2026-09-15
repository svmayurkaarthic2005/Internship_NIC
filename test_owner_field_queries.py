"""Owner-record field questions -- ration card, voter ID, PIN code, occupation
code, and the rest of the natham-chitta / IGRS owner columns.

`build_app_tables.py` projects only a handful of owner columns into the ORM
`owners` / `survey_ownership` rows (name, Tamil name, relative/father name,
Aadhaar last-4, ownership share). The rest of `urban_natham_chitta_owner` /
`nisd_transfer_igrs_owner` -- `ration_card_number`, `epic_no`, `pin_code`,
`occupation_code`, `assignment_number`, `own_num`, `rel_num`, `user_no`,
`door_number` -- is dropped, and is blank in the source too.

Before the fix, a pointed question about one of those was swallowed by the
`survey_owners` intent, which dumped every owner on the parcel and never
acknowledged the field asked -- a confident non-answer. Now `chatbot.py`
answers it deterministically (no LLM), naming the field and saying it is not in
the register.

Every expectation is recomputed from the database, so the suite still means
something after a reseed.

    python test_owner_field_queries.py            # routing + answers (DB, no LLM)
    python test_owner_field_queries.py --routing  # intent routing only, no DB
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
import uuid
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from sqlalchemy import select, text

from backend.database import AsyncSessionLocal
from backend.models import Application, SISOfficer
from backend.services.chatbot import (
    _asked_untracked_owner_field,
    process_chat,
    process_chat_stream,
)
from backend.services.rag import parse_intent
from test_followup_context import officer_context

ROUTING_ONLY = "--routing" in sys.argv

_results: list[tuple[bool, str, str]] = []


def check(ok: bool, label: str, detail: str = "") -> bool:
    _results.append((bool(ok), label, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"\n        {detail}" if detail else ""))
    return bool(ok)


def section(title: str) -> None:
    print(f"\n── {title} ──")


def plain(html: str, limit: int = 400) -> str:
    return re.sub(r"<[^>]+>", " ", html or "").replace("\n", " ")[:limit].strip()


async def ask(db, officer, turns):
    sid = str(uuid.uuid4())
    history: list = []
    result = None
    for q in turns:
        result = await process_chat(q, sid, officer, db, list(history))
        history += [{"role": "user", "content": q},
                    {"role": "assistant", "content": result.get("response")}]
    return result


async def ask_stream(db, officer, message):
    parts: list[str] = []
    async for raw in process_chat_stream(db=db, message=message, officer=officer,
                                         session_id=str(uuid.uuid4()), chat_history=[]):
        for line in raw.decode("utf-8").splitlines():
            if line.startswith("data: "):
                payload = json.loads(line[6:])
                if payload.get("content"):
                    parts.append(payload["content"])
    return plain("".join(parts))


# The owner columns that carry NO data in either layer and are not projected.
_ABSENT_FIELD_CUES = {
    "ration_card_number": ["ration card number of owner in {a}",
                           "what is the ration card number for {a}",
                           "owner ration card {a}"],
    "voter_id_number": ["voter id of the owner for application {a}",
                        "epic number for {a}",
                        "owner's election id in {a}"],
    "pin_code": ["pin code of the owner in {a}", "owner pincode {a}"],
    "cin_no": ["cin number of the owner of {a}",
               "what is the CIN of the owner for {a}",
               "owner's citizen identification number in {a}"],
    "occupation_code": ["occupation code of owner {a}",
                        "what is the owner's occupation code for {a}"],
    "relation_code": ["relation code for {a}", "relationship code of owner in {a}"],
    "owner_no": ["owner number in {a}", "owner serial number for {a}"],
    "door_number": ["door number for application {a}", "owner house number {a}"],
    "assignment_number": ["assignment number for {a}"],
    "user_number": ["user number for {a}", "user_no of the owner row {a}"],
    # transfer-owner extract columns, empty in source, unprojected
    "owner_status": ["owner status for {a}", "what is the owner's status in {a}"],
    "uds_details": ["uds details for {a}", "undivided share details for {a}"],
    "extent": ["extent of the owner in {a}", "owner's extent for {a}"],
}

_TAMIL_ABSENT = [
    ("ration_card_number", "{a} உரிமையாளரின் ரேஷன் கார்டு எண் என்ன?"),
    ("voter_id_number", "{a} வாக்காளர் அடையாள எண் என்ன?"),
    ("pin_code", "{a} உரிமையாளரின் பின் கோடு என்ன?"),
]


# ─────────────────────────────────────────────────────────────────────────────
# 1. Pure classification -- the cue detector, no DB
# ─────────────────────────────────────────────────────────────────────────────
def test_classification() -> None:
    section("1. _asked_untracked_owner_field cue detection")
    for key, templates in _ABSENT_FIELD_CUES.items():
        for t in templates:
            msg = t.format(a="2023/0153/28/000367").lower()
            got = _asked_untracked_owner_field(msg)
            check(got == key, f"{msg!r} -> {key}", f"got {got!r}")
    for key, t in _TAMIL_ABSENT:
        got = _asked_untracked_owner_field(t.format(a="x").lower())
        check(got == key, f"[ta] {t} -> {key}", f"got {got!r}")

    # Must NOT fire on questions the register can answer.
    for msg in ("what is the patta number for 2023/0153/28/000367",
                "who is the owner of 2023/0153/28/000367",
                "what is the applicant gender for 2023/0153/28/000367",
                "ward code of 2023/0153/28/000367",
                "what is the serial number of 2023/0153/28/000367",
                "what is the can number",
                "what is the applicant mobile number"):
        got = _asked_untracked_owner_field(msg)
        check(got is None, f"no false positive: {msg!r}", f"got {got!r}")

    section("1b. Typo-tolerant fallback (unseen misspellings)")
    for msg, key in (
        ("raton card number of the owner in x", "ration_card_number"),
        ("occuption code of the owner x", "occupation_code"),
        ("reltion code for x", "relation_code"),
        ("citizen identificaton number of the owner x", "cin_no"),
    ):
        got = _asked_untracked_owner_field(msg)
        check(got == key, f"typo {msg!r} -> {key}", f"got {got!r}")

    section("1c. Unknown owner attributes (in no land record at all)")
    from backend.services.chatbot import _asked_unknown_owner_attr
    for msg, want in (
        ("owner's caste for 2023/0153/28/000367", "caste"),
        ("owner religion in 2023/0153/28/000367", "religion"),
        ("owner's email for 2023/0153/28/000367", "email address"),
        ("age of the owner in 2023/0153/28/000367", "age"),
    ):
        got = _asked_unknown_owner_attr(msg)
        check(got == want, f"{msg!r} -> {want}", f"got {got!r}")
    for msg in ("what is the owner name for 2023/0153/28/000367",
                "applicant email for 2023/0153/28/000367",
                "who is the owner of 2023/0153/28/000367"):
        got = _asked_unknown_owner_attr(msg)
        check(got is None, f"no false positive: {msg!r}", f"got {got!r}")


# ─────────────────────────────────────────────────────────────────────────────
# 2. Answers -- deterministic, no LLM, from a real application
# ─────────────────────────────────────────────────────────────────────────────
async def test_answers(db) -> None:
    section("2. Deterministic answers for absent owner fields")

    off = (await db.execute(
        select(SISOfficer).where(SISOfficer.email == "csenthil@sis.tn.gov.in")
    )).scalar_one()
    octx = await officer_context(db, off)

    app = (await db.execute(
        select(Application)
        .where(Application.assigned_officer_id == octx.officer_id,
               Application.application_type == "NISD")
        .order_by(Application.application_number)
        .limit(1)
    )).scalar_one()
    a_no = app.application_number
    print(f"   (using {a_no})")

    _REFUSAL = "not held in your sis register"
    _DUMP = "owners for survey no"

    for key, templates in _ABSENT_FIELD_CUES.items():
        for t in templates:
            q = t.format(a=a_no)
            r = await ask(db, octx, [q])
            ans = plain(r.get("response")).lower()
            check(_REFUSAL in ans and _DUMP not in ans,
                  f"{q!r}", f"intent={r.get('intent')} :: {ans[:150]}")

    section("2b. Same answer on the streaming path")
    for key in ("ration_card_number", "voter_id_number", "pin_code"):
        q = _ABSENT_FIELD_CUES[key][0].format(a=a_no)
        ans = (await ask_stream(db, octx, q)).lower()
        check(_REFUSAL in ans and _DUMP not in ans,
              f"[stream] {q!r}", ans[:150])

    section("2c. Tamil phrasing")
    for key, t in _TAMIL_ABSENT:
        q = t.format(a=a_no)
        r = await ask(db, octx, [q])
        ans = plain(r.get("response"))
        check("SIS பதிவேட்டில் இல்லை" in ans and "Owners for Survey" not in ans,
              f"[ta] {q!r}", ans[:160])

    section("2d. Previous-message reference resolves, then refuses honestly")
    r = await ask(db, octx, [f"show me application {a_no}",
                             "what is the ration card number of the owner?"])
    ans = plain(r.get("response")).lower()
    check(_REFUSAL in ans and a_no.lower() in ans and _DUMP not in ans,
          "show <app> -> 'ration card number of the owner?'", ans[:170])

    section("2e. Fields the register DOES carry still answer")
    checks = [
        (f"patta number for {a_no}", "patta"),
        (f"town code of {a_no}", "urban unit code"),
        (f"ward code of {a_no}", "ward code"),
        (f"last updated datetime of {a_no}", "last updated"),
    ]
    for q, needle in checks:
        r = await ask(db, octx, [q])
        ans = plain(r.get("response")).lower()
        check(needle in ans and _REFUSAL not in ans, f"{q!r}", ans[:150])

    section("2f. Misspelled field names still get the deterministic answer")
    for q in (f"raton card number of the owner in {a_no}",
              f"occuption code of the owner {a_no}",
              f"reltion code for {a_no}"):
        r = await ask(db, octx, [q])
        ans = plain(r.get("response")).lower()
        check(_REFUSAL in ans and _DUMP not in ans, f"{q!r}", ans[:140])

    section("2g. Personal attributes no land record holds -> not a dump")
    for q in (f"owner's caste for {a_no}", f"owner's religion in {a_no}",
              f"owner's email for {a_no}", f"age of the owner in {a_no}"):
        r = await ask(db, octx, [q])
        ans = plain(r.get("response")).lower()
        check("not held" in ans and "land-mutation register" in ans
              and _DUMP not in ans, f"{q!r}", ans[:150])


# ─────────────────────────────────────────────────────────────────────────────
# 3. The data premise -- the columns really are empty / unprojected
# ─────────────────────────────────────────────────────────────────────────────
async def test_data_premise(db) -> None:
    section("3. Source columns carry nothing usable (why the refusal is right)")
    # Genuinely NULL everywhere.
    for col in ("occupation_code", "ration_card_number", "epic_no",
                "assignment_number", "user_no"):
        n = (await db.execute(text(
            f'select count("{col}") from urban_natham_chitta_owner'))).scalar()
        check(n == 0, f"urban_natham_chitta_owner.{col} is entirely NULL", f"got {n}")
    # Present but pure garbage ('0' on every row).
    for col in ("pin_code", "cin_no"):
        n = (await db.execute(text(
            f"select count(nullif(\"{col}\",'0')) from urban_natham_chitta_owner"))).scalar()
        check(n == 0, f"urban_natham_chitta_owner.{col} is '0' on every populated row",
              f"got {n} real values")

    # transfer-owner extract columns behind owner_status / uds_details / extent.
    for col in ("owner_status", "uds_details", "extent"):
        n = (await db.execute(text(
            f'select count("{col}") from nisd_transfer_return_owner'))).scalar()
        check(n == 0, f"nisd_transfer_return_owner.{col} is entirely NULL", f"got {n}")

    # None of these owner fields is queryable through get_survey_owners' row
    # shape (postgres.get_survey_owners._row), which is what the chatbot reads.
    from backend.services.postgres import get_survey_owners
    sn = (await db.execute(text(
        "select survey_no from survey_numbers limit 1"))).scalar()
    owners = (await get_survey_owners(db, sn)).get("owners", [])
    row_keys = set(owners[0].keys()) if owners else set()
    # gender / mobile / address ARE exposed now (owners.sex is real data; mobile
    # and address are carried as keys so the renderer can say "not recorded"
    # rather than ignore the question) -- the genuinely unprojected columns:
    for absent in ("ration_card_number", "voter_id_number", "pin_code",
                   "occupation_code", "door_number", "assignment_number"):
        check(absent not in row_keys,
              f"get_survey_owners row does not expose {absent}")


async def main() -> None:
    if ROUTING_ONLY:
        test_classification()
    else:
        test_classification()
        async with AsyncSessionLocal() as db:
            await test_data_premise(db)
            await test_answers(db)

    passed = sum(1 for ok, _, _ in _results if ok)
    total = len(_results)
    print(f"\n{'=' * 68}\n{passed}/{total} checks passed")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    asyncio.run(main())
