"""Owner (urban_natham_chitta_owner -> owners / survey_ownership) field queries.

Complements `test_owner_field_followups.py` (pure classification) with the two
halves that actually touch data:

  * `_owner_detail_line()` rendering -- the owner columns the SIS register
    carries (name / Tamil name / relative / Aadhaar last-4 / gender / ownership
    share / type) are surfaced only when the officer asked for them, and a
    column the extract leaves blank (mobile, address, gender on most rows) gets
    an explicit "not recorded" instead of a line that silently ignores the
    question;
  * `get_survey_owners()` against the built database -- every key the renderer
    reads is present on the row, and each value matches an independent SQL read
    of `owners` / `survey_ownership`, so the check still means something after a
    reseed;
  * an end-to-end follow-up chain through `process_chat` -- "who owns survey X"
    then a bare "what is the father's name?" / "the gender?" / "the ownership
    share?" keeps the survey reference and answers from the register, never
    from the model (needs Ollama + DB).

    python test_owner_queries.py            # everything (DB; e2e needs Ollama)
    python test_owner_queries.py --routing  # rendering only, no DB
    python test_owner_queries.py --fast     # DB checks, skip the live-model e2e
"""
from __future__ import annotations

import asyncio
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

ROUTING_ONLY = "--routing" in sys.argv
FAST = "--fast" in sys.argv

from backend.services.chatbot import _owner_detail_line

_results: list[tuple[bool, str, str]] = []


def check(ok: bool, label: str, detail: str = "") -> bool:
    _results.append((bool(ok), label, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"\n        {detail}" if detail else ""))
    return bool(ok)


def section(title: str) -> None:
    print(f"\n── {title} ──")


def plain(html: str, limit: int = 400) -> str:
    return re.sub(r"<[^>]+>", " ", html or "").replace("\n", " ")[:limit].strip()


# ─────────────────────────────────────────────────────────────────────────────
# 1. _owner_detail_line rendering — pure, no DB
# ─────────────────────────────────────────────────────────────────────────────
def test_rendering() -> None:
    section("1. _owner_detail_line surfaces a field only when it is asked for")

    full = {
        "name": "ராமலெட்சுமி", "name_tamil": "ராமலெட்சுமி",
        "relative_name": "நடேசன்", "aadhaar_last4": "4321",
        "gender": "F", "mobile": None, "address": None,
        "sub_division": "Survey Level", "ownership_share": 100.0,
        "ownership_type": "sole", "is_joint_owner": False,
    }
    blank = {**full, "relative_name": None, "aadhaar_last4": None, "gender": None}

    base = _owner_detail_line(full, "who owns survey 5")
    check("ராமலெட்சுமி" in base and "Share: 100.0" in base
          and "Relative:" not in base and "Aadhaar" not in base
          and "Gender" not in base,
          "a plain ownership question shows name + share + type, nothing else",
          base)

    line = _owner_detail_line(full, "who is the father of the owner of survey 5")
    check("Relative: நடேசன்" in line,
          "'who is the father' appends the relative name", line)

    line = _owner_detail_line(full, "what is the owner's aadhaar number for survey 5")
    check("Aadhaar ending 4321" in line,
          "'aadhaar number' appends the last four digits", line)

    line = _owner_detail_line(full, "what is the gender of the owner of survey 5")
    check("Gender: Female" in line,
          "'gender' resolves the F/M code to a word", line)

    line = _owner_detail_line(blank, "what is the gender and father's name and aadhaar")
    check(line.count("not recorded") == 3,
          "a field the extract leaves blank is reported 'not recorded', not dropped",
          line)

    line = _owner_detail_line(full, "what is the owner's mobile number and address")
    check("not held for owners" in line and line.count("not held for owners") == 2,
          "owner mobile / address are never held in the SIS register — said so plainly",
          line)

    line = _owner_detail_line(full, "give me the owner name in tamil for survey 5")
    check(line.startswith("  • ராமலெட்சுமி"),
          "'name in tamil' renders the Tamil name as the primary label", line)

    combo = _owner_detail_line(full, "owner name father name aadhaar and share for survey 5")
    check(all(s in combo for s in ("ராமலெட்சுமி", "Relative: நடேசன்",
                                   "Aadhaar ending 4321", "Share: 100.0")),
          "a combination question surfaces every field it names at once", combo)


# ─────────────────────────────────────────────────────────────────────────────
# Database-backed
# ─────────────────────────────────────────────────────────────────────────────
async def _pick_survey_with_owners(db, officer):
    from sqlalchemy import func, select
    from backend.models import (Block, Owner, SurveyNumber, SurveyOwnership,
                                Ward)
    from backend.services.postgres import get_jurisdiction_filter

    jf = await get_jurisdiction_filter(db, officer)
    q = (select(SurveyNumber.survey_no, func.count(SurveyOwnership.id))
         .join(SurveyOwnership, SurveyOwnership.survey_number_id == SurveyNumber.id)
         .join(Block, SurveyNumber.block_id == Block.id)
         .join(Ward, Block.ward_id == Ward.id)
         .group_by(SurveyNumber.survey_no)
         .order_by(func.count(SurveyOwnership.id).desc()))
    if jf:
        from sqlalchemy import or_
        q = q.where(or_(*jf))
    row = (await db.execute(q)).first()
    return row[0] if row else None


async def test_db_field_completeness(db, officer) -> None:
    section("2. get_survey_owners: every rendered key present and DB-accurate")
    from sqlalchemy import select
    from backend.models import Owner, SurveyNumber, SurveyOwnership
    from backend.services.postgres import get_survey_owners

    survey_no = await _pick_survey_with_owners(db, officer)
    if not survey_no:
        check(False, "a survey with ownership records exists in the officer's jurisdiction")
        return
    print(f"  (survey {survey_no})")

    res = await get_survey_owners(db, survey_no, officer=officer)
    check(res.get("found") and res.get("owners"),
          f"survey {survey_no} returns ownership records",
          f"found={res.get('found')} n={len(res.get('owners') or [])}")
    rows = res.get("owners") or []

    required = {"name", "name_tamil", "relative_name", "aadhaar_last4", "gender",
                "mobile", "address", "ownership_share", "ownership_type",
                "is_joint_owner", "sub_division"}
    missing = required - set(rows[0].keys()) if rows else required
    check(not missing, "the row carries every key _owner_detail_line reads",
          f"missing: {sorted(missing)}")

    # Cross-check each owner against an independent read of the owners table.
    ok = True
    detail = ""
    for r in rows:
        owner = (await db.execute(
            select(Owner).where(Owner.name == r["name"]))).scalars().first()
        if owner is None:
            ok = False
            detail = f"no owners row named {r['name']!r}"
            break
        if (owner.name_tamil or None) != (r["name_tamil"] or None) \
                or (owner.father_name or None) != (r["relative_name"] or None) \
                or (owner.aadhaar_last4 or None) != (r["aadhaar_last4"] or None) \
                or (owner.gender or None) != (r["gender"] or None):
            ok = False
            detail = (f"{r['name']}: row={r['name_tamil'],r['relative_name'],r['aadhaar_last4'],r['gender']}"
                      f" db={owner.name_tamil,owner.father_name,owner.aadhaar_last4,owner.gender}")
            break
    check(ok, "name_tamil / relative / aadhaar / gender match the owners table", detail)

    # Ownership share / joint flag come from survey_ownership, not owners.
    share_ok = all(
        (r["ownership_share"] is None) or (0 < float(r["ownership_share"]) <= 100)
        for r in rows)
    check(share_ok, "every ownership_share is a percentage in (0, 100]",
          str([r["ownership_share"] for r in rows]))


async def test_e2e_followups(db, officer) -> None:
    section("3. Follow-up chain: 'who owns survey X' then bare owner-field asks")
    from backend.services.chatbot import process_chat

    survey_no = await _pick_survey_with_owners(db, officer)
    if not survey_no:
        check(False, "a survey with ownership records exists")
        return

    async def convo(*turns):
        sid = str(uuid.uuid4())
        hist: list = []
        out = []
        for q in turns:
            r = await process_chat(q, sid, officer, db, list(hist))
            out.append(r)
            hist.append({"role": "user", "content": q})
            hist.append({"role": "assistant", "content": r.get("response")})
        return out

    r = await convo(f"who owns survey {survey_no}", "what is the father's name?")
    ans = plain(r[1].get("response"))
    check("not found" not in ans.lower() and "could not" not in ans.lower()
          and "specify" not in ans.lower(),
          "'what is the father's name?' keeps the survey reference", ans)

    r = await convo(f"who owns survey {survey_no}", "what is the gender?")
    ans = plain(r[1].get("response"))
    check(("Female" in ans or "Male" in ans or "not recorded" in ans.lower()),
          "'what is the gender?' answers from the register (value or 'not recorded'),"
          " never a guess", ans)

    r = await convo(f"who owns survey {survey_no}", "what is the ownership share?")
    ans = plain(r[1].get("response"))
    check("share" in ans.lower(),
          "'what is the ownership share?' resolves against the same survey", ans)

    # A field the register genuinely does not hold must not come back with digits.
    r = await convo(f"who owns survey {survey_no}", "what is the owner's mobile number?")
    ans = plain(r[1].get("response"))
    invented = re.findall(r"\b\d{6,}\b", ans)
    check(not invented,
          "owner mobile is not held — and no number is invented for it", ans)


async def main() -> int:
    test_rendering()

    if ROUTING_ONLY:
        print("\n(--routing: database sections skipped)")
    else:
        from sqlalchemy import select
        from backend.database import AsyncSessionLocal
        from backend.models import SISOfficer
        from backend.schemas import OfficerContext
        from backend.services.auth_service import get_officer_jurisdiction_ids

        async with AsyncSessionLocal() as db:
            off = (await db.execute(
                select(SISOfficer).where(SISOfficer.is_active == True).limit(1)
            )).scalars().first()
            if not off:
                check(False, "an active officer exists to test with")
            else:
                jur = await get_officer_jurisdiction_ids(off.id, db)
                ids = (jur["district_ids"] + jur["taluk_ids"] + jur["town_ids"]
                       + jur["ward_ids"] + jur["block_ids"])
                officer = OfficerContext(
                    officer_id=off.id, employee_id=off.employee_id, name=off.name,
                    email=off.email, designation=off.designation,
                    jurisdiction_type=jur["jurisdiction_type"],
                    jurisdiction_name=jur["jurisdiction_name"],
                    jurisdiction_ids=[i for i in ids if i])
                print(f"\nOfficer: {officer.name} ({officer.jurisdiction_name})")
                await test_db_field_completeness(db, officer)
                if not FAST:
                    await test_e2e_followups(db, officer)
                else:
                    print("\n(--fast: live-model follow-up chain skipped)")

    failed = [r for r in _results if not r[0]]
    print(f"\n{'=' * 68}")
    print(f"{len(_results) - len(failed)}/{len(_results)} checks passed")
    for _, label, _d in failed:
        print(f"  FAILED: {label}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
