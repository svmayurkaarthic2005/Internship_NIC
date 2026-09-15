"""District codes must mean the same district everywhere.

Why this exists
---------------
A district code is carried in the third segment of every application number
(2026/0154/**28**/001167), and four separate places turn that code into a name:

    backend/config.py               DISTRICT_CODE_MAP  (+ the Tamil map)
    backend/utils/helpers.py        TAMIL_NADU_DISTRICTS  (the query-layer fallback)
    frontend/js/table_renderer.js   DISTRICT_CODES        (what the officer reads)
    backend/documents/district_codes.txt                  (the RAG corpus)

...and a fifth, `district_unicode`, which is the department's own master loaded
verbatim from the TAMILNILAM district.sql dump and is the authority on what a
code MEANS.

They had drifted. Codes 34 / 35 / 37 were rotated in three of the four against
the master: the dump reads 34 = Tenkasi, 35 = Chengalpattu, 37 = Ranipet, while
config, the frontend and the reference document read Chengalpattu / Ranipet /
Tenkasi. That is not a spelling variant. It puts another district's name on a
land record, and it does it silently -- the field is populated, just wrong. It
went unnoticed because every seeded application is district 28, which all five
sources agree on.

The two rules
-------------
1. **Identity is the master's.** Whatever the dump says a code means, every
   other source must agree. Checked tolerantly, because the master's spelling is
   raw departmental data entry and inconsistent with itself ("VILUPPURAM",
   "The Nilgiris", "Mailaduthurai").
2. **Spelling is the app's, and it is one spelling.** The four code-side sources
   must be character-for-character identical, so an officer never sees the same
   district written two ways depending on which screen they are on.

Run:
    python test_district_codes.py            # everything (needs the database)
    python test_district_codes.py --no-db    # the four code-side sources only
"""
from __future__ import annotations

import asyncio
import io
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FAILURES: list[str] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}")
    if detail and not ok:
        print(f"        {detail}")
    if not ok:
        FAILURES.append(label)


def section(title: str) -> None:
    print(f"\n{title}\n" + "-" * len(title))


# ── the four code-side sources ──────────────────────────────────────────────
def load_config() -> dict:
    from backend.config import DISTRICT_CODE_MAP
    return {str(k).zfill(2): str(v) for k, v in DISTRICT_CODE_MAP.items()}


def load_helpers() -> dict:
    from backend.utils.helpers import TAMIL_NADU_DISTRICTS
    return {str(k).zfill(2): str(v["name"]) for k, v in TAMIL_NADU_DISTRICTS.items()}


def load_frontend() -> dict:
    js = io.open(ROOT / "frontend" / "js" / "table_renderer.js", encoding="utf-8").read()
    # The district table is the first block of "NN": "Name" pairs in the file.
    block = re.search(r"DISTRICT_CODES\s*=\s*\{(.*?)\}", js, re.S)
    body = block.group(1) if block else js
    return dict(re.findall(r"['\"](\d{2})['\"]\s*:\s*['\"]([^'\"]+)['\"]", body))


def load_document() -> dict:
    txt = io.open(ROOT / "backend" / "documents" / "district_codes.txt",
                  encoding="utf-8").read()
    out = {}
    for line in txt.splitlines():
        m = re.match(r"^\s*(\d{2})\s+(\S.*?)\s*$", line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


# ── comparing a name to the master's ────────────────────────────────────────
# The master's spelling is not a style guide, so identity is compared through a
# normaliser that absorbs the transliteration differences a Tamil place name
# legitimately has -- and nothing more. It must still tell Tenkasi, Chengalpattu
# and Ranipet apart, which is the whole point.
# Keyed on the NORMALISED form, so the entry is what `norm` produces, not what
# the officer typed. The one pair the normaliser cannot bridge on its own: the
# master drops the glide vowel ("Mailaduthurai"), the app spells it in full
# ("Mayiladuthurai").
_ALIASES = {
    "mailadutura": "mayiladutura",
}


def norm(name: str) -> str:
    n = (name or "").strip().lower()
    n = re.sub(r"^the\s+", "", n)
    n = re.sub(r"[^a-z]", "", n)
    n = n.replace("th", "t")          # Thirupattur / Tirupathur
    n = re.sub(r"(.)\1+", r"\1", n)   # Villupuram / Viluppuram
    n = re.sub(r"i$", "", n)          # Sivaganga / Sivagangai
    return _ALIASES.get(n, n)


def main(use_db: bool) -> int:
    sources = {
        "config.py DISTRICT_CODE_MAP": load_config(),
        "helpers.py TAMIL_NADU_DISTRICTS": load_helpers(),
        "frontend table_renderer.js": load_frontend(),
        "documents/district_codes.txt": load_document(),
    }

    section("1. The four code-side sources carry the same codes")
    ref_name, ref = next(iter(sources.items()))
    for name, m in sources.items():
        check(len(m) == 38, f"{name} has all 38 codes", f"has {len(m)}")
        missing = sorted(set(ref) - set(m))
        check(not missing, f"{name} is missing no code", ", ".join(missing))

    section("2. ...and spell every one of them identically")
    for code in sorted(ref):
        spellings = {name: m.get(code) for name, m in sources.items()}
        distinct = {v for v in spellings.values() if v}
        check(len(distinct) == 1, f"code {code} is spelled one way",
              " | ".join(f"{n}={v!r}" for n, v in spellings.items()))

    section("3. The rotation that started this: 34 / 35 / 37")
    # Spelled out so the regression is named, not merely implied by section 4.
    for code, want in (("34", "Tenkasi"), ("35", "Chengalpattu"),
                       ("37", "Ranipet")):
        for name, m in sources.items():
            check(m.get(code) == want, f"{name}: {code} = {want}",
                  f"got {m.get(code)!r}")

    if not use_db:
        print("\n(--no-db: the department master was not consulted)")
        return report()

    section("4. Identity matches the department's master (district_unicode)")
    from sqlalchemy import select

    from backend.database import AsyncSessionLocal
    from backend.models import District, SISOfficer
    from backend.schemas import OfficerContext
    from backend.services import postgres as pg
    from backend.services.auth_service import get_officer_jurisdiction_ids
    from backend.services.chatbot import _build_table_data

    # Both database reads happen in ONE asyncio.run. A second one closes the
    # first loop, and the engine's pooled connections are bound to it -- the
    # next query dies inside asyncpg with "'NoneType' object has no attribute
    # 'send'", which reads like a driver fault and is really two event loops.
    async def load_all() -> tuple:
        async with AsyncSessionLocal() as db:
            rows = (await db.execute(select(District))).scalars().all()
            master = {str(d.district_code).zfill(2): d.name for d in rows}

            officer = (await db.execute(select(SISOfficer))).scalars().first()
            table_rows: list = []
            if officer:
                j = await get_officer_jurisdiction_ids(officer.id, db)
                ids = (j["district_ids"] + j["taluk_ids"] + j["town_ids"]
                       + j["ward_ids"] + j["block_ids"])
                ctx = OfficerContext(
                    officer_id=officer.id, employee_id=officer.employee_id,
                    name=officer.name, email=officer.email,
                    designation=officer.designation,
                    jurisdiction_type=j["jurisdiction_type"],
                    jurisdiction_name=j["jurisdiction_name"],
                    jurisdiction_ids=[i for i in ids if i])
                sd = await pg.get_officer_applications(db, ctx)
                td = _build_table_data("pending_applications",
                                       "show my applications",
                                       str(officer.id), sd) or {}
                table_rows = td.get("applications") or []
            return master, table_rows

    master, table_payload_rows = asyncio.run(load_all())
    check(bool(master), "the master is readable", f"{len(master)} rows")
    for code in sorted(master):
        for name, m in sources.items():
            here = m.get(code)
            check(here is not None and norm(here) == norm(master[code]),
                  f"code {code} means {master[code]!r} in {name}",
                  f"master={master[code]!r} vs {name}={here!r} "
                  f"({norm(master[code])} vs {norm(here or '')})")

    # The master is short two districts; that is a fact about the data, and the
    # reference document says so rather than leaving an officer to wonder.
    absent = sorted(set(ref) - set(master))
    check(absent == ["17", "33"],
          "exactly codes 17 and 33 have no row in the master", str(absent))
    doc = io.open(ROOT / "backend" / "documents" / "district_codes.txt",
                  encoding="utf-8").read()
    check("17" in doc and "33" in doc and "36 districts" in doc,
          "the reference document records that the master holds only 36")

    section("5. The District column reaches the screen")
    # The value was right in the query layer and still rendered "N/A" on every
    # row: `_build_table_data` copies taluk / town / ward / block into the table
    # payload and did not copy the district, so the renderer read a key that was
    # never sent. Checking the query layer alone is what let it through -- this
    # follows the value all the way to the payload the frontend is handed.
    rows = table_payload_rows
    check(bool(rows), "the listing builds a table payload", f"{len(rows)} rows")
    if rows:
        blank = [r for r in rows
                 if str(r.get("district_name")) in ("N/A", "None", "", "null")]
        check(not blank,
              "every row carries a district name, not 'N/A'",
              f"{len(blank)} of {len(rows)} rows blank")
        # ...and it is a district the master actually knows.
        names = {r.get("district_name") for r in rows}
        check(all(any(norm(n) == norm(m) for m in master.values()) for n in names),
              "...and each is a district the master carries", str(sorted(names)))

    return report()


def report() -> int:
    print("\n" + "=" * 68)
    if FAILURES:
        print(f"{len(FAILURES)} FAILED:")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("ALL PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main(use_db="--no-db" not in sys.argv))
