"""\"Who am I?\" — the officer's own identity.

The officer is authenticated, so their name, designation, employee id and
jurisdiction are all on the request. Left to the LLM this answered "You are a
Sub Inspector Surveyor (SIS) officer of Tamil Nadu" — true of every user of the
system and of nobody in particular. This suite checks the direct handler that
replaced it.

Routing and rendering are pure string work, so those run with no DB. The
end-to-end case needs sis_chatbot_db and is skipped when it is unreachable.
"""
import asyncio
import re
import sys

# These suites print Tamil. On Windows the console is cp1252 and the
# first Tamil character raises UnicodeEncodeError, which killed the run
# before any result was reported. Same guard the other suites carry.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

SRC = "backend/services/chatbot.py"
START = '# ── "Who am I?"'
END = "# ── Format follow-ups"

failures = []


def check(name, got, want):
    ok = got == want
    if not ok:
        failures.append(f"{name}\n    expected: {want!r}\n    got:      {got!r}")
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")


def check_in(name, needles, got):
    missing = [n for n in needles if n not in got]
    if missing:
        failures.append(f"{name}\n    missing: {missing}\n    got: {got!r}")
    print(f"  {'PASS' if not missing else 'FAIL'}  {name}")


def _load_pure():
    """The detector and the jurisdiction formatter, without the import chain."""
    src = open(SRC, encoding="utf-8").read()
    block = src[src.index(START):src.index(END)]
    block = block[:block.index("async def _officer_identity_reply")]
    ns = {"re": re}
    exec(compile(block, SRC, "exec"), ns)
    return ns


M = _load_pure()

# ── 1. Routing ───────────────────────────────────────────────────────────────
print("\n[1] Routing — identity question vs. everything else")
ROUTING = [
    # the question itself, in every form an officer types it
    ("who am i?", True), ("who am I", True), ("who i am", True), ("whoami", True),
    ("what am i", True), ("which officer am i", True),
    ("what is my name", True), ("my name?", True), ("tell me my name", True),
    ("my full name", True),
    ("what is my designation", True), ("my post", True), ("my posting", True),
    ("what is my role", True), ("my rank", True), ("my position", True),
    ("my employee id", True), ("my emp id", True), ("my staff number", True),
    ("show my profile", True), ("my details", True), ("my information", True),
    ("about me", True), ("my identity", True),
    # Tamil / Tanglish
    ("நான் யார்", True), ("நான் யாரு", True), ("என் பெயர் என்ன", True),
    ("எனது பதவி", True), ("naan yaar", True), ("naan yaaru", True),
    ("en peyar enna", True), ("ennoda per", True), ("en padhavi", True),
    # NOT identity — these have their own handlers and must reach them
    ("what is my jurisdiction", False),
    ("what is my ward", False),
    ("my area", False),
    ("show my pending applications", False),
    ("how many applications are with me", False),
    ("what is the name for 2026/0154/28/001167", False),
    ("who signed 2026/0153/28/000254", False),
    ("role id for this application", False),
    ("user id of this application", False),
    ("who is the applicant", False),
    ("what is the applicant name", False),
    ("who submitted this application, csc or citizen", False),
    ("", False),
]
for msg, want in ROUTING:
    check(f"_is_officer_identity_question({msg!r})",
          M["_is_officer_identity_question"](msg), want)

# ── 2. Jurisdiction line ─────────────────────────────────────────────────────
print("\n[2] Jurisdiction line")
fmt = M["_format_jurisdiction_line"]
check("single ward with block", fmt({
    "district": {"name": "Thoothukudi", "code": "28"},
    "taluk": {"name": "Thoothukudi"},
    "towns": [{"name": "Thoothukudi",
               "wards": [{"ward_number": "002", "blocks": [{"block_number": "0015"}]}]}],
}), "Ward 002 (Block 0015), Thoothukudi town, Thoothukudi taluk, Thoothukudi district (28)")
check("ward without a block", fmt({
    "district": {"name": "Thoothukudi", "code": "28"},
    "taluk": {"name": "N/A"},
    "towns": [{"name": "", "wards": [{"ward_number": "103", "blocks": []}]}],
}), "Ward 103, Thoothukudi district (28)")
check("nothing assigned", fmt({}), "")

# ── 3. Facet — answer the question asked, not a personnel file ───────────────
print("\n[3] Facet routing — 'who am i' is a name and a post, nothing more")
facet = M["_identity_facet"]
for msg, want in [
    ("who am i?", "who"), ("whoami", "who"), ("what am i", "who"),
    ("what is my name", "name"), ("என் பெயர்", "name"), ("en peyar", "name"),
    ("what is my designation", "designation"), ("my post", "designation"),
    ("my role", "designation"), ("எனது பதவி", "designation"),
    ("my employee id", "employee_id"), ("my staff number", "employee_id"),
    ("show my profile", "profile"), ("my details", "profile"),
    ("my information", "profile"),
]:
    check(f"_identity_facet({msg!r})", facet(msg), want)

# ── 3b. 'my profile' then 'in table' reshapes the card ───────────────────────
print("\n[3b] 'my profile' then 'in table' reshapes the card")
# The sliced source annotates with typing names, so the exec namespace has
# to carry them. Until the UTF-8 guard above was added this suite died on
# a Tamil character before ever reaching here, which is why the missing
# name went unnoticed.
from typing import Any, Dict, List, Optional, Tuple
rf = {"re": re, "Optional": Optional, "List": List, "Dict": Dict,
      "Any": Any, "Tuple": Tuple}
_src = open(SRC, encoding="utf-8").read()
exec(compile(_src[_src.index("# \u2500\u2500 Format follow-ups"):
                  _src.index('# \u2500\u2500 Bare date-scope follow-ups ("last month")')],
             SRC, "exec"), rf)
PROFILE = (
    "Your profile:\n"
    "\u2022 **Name**: C Senthil\n"
    "\u2022 **Designation**: Sub Inspector Surveyor (SIS)\n"
    "\u2022 **Employee ID**: SIS001\n"
    "\u2022 **Jurisdiction**: Ward 002 (Block 0015), Thoothukudi district (28)"
)
parsed = rf["_parse_previous_answer"](PROFILE)
check("profile parses to 4 fields", len(parsed["rows"]), 4)
check_in("profile -> table", ["<table class='data-table'>", "<td>C Senthil</td>",
                             "<strong>Employee ID</strong>", "<td>SIS001</td>"],
         rf["_reformat_previous_answer"]([{"role": "assistant", "content": PROFILE}],
                                         "table", "en"))

# ── 4. Wiring ────────────────────────────────────────────────────────────────
print("\n[4] Wiring")
check("handled on both chat paths",
      _src.count("_is_officer_identity_question(message)"), 2)
check("answers from the officer record, not the LLM",
      "select(SISOfficer).where(SISOfficer.id == officer.officer_id)" in _src, True)

# ── 5. End-to-end against the real database (skipped if unreachable) ─────────
print("\n[5] End-to-end (needs sis_chatbot_db)")


async def _e2e():
    from backend.database import AsyncSessionLocal
    from backend.models import SISOfficer, OfficerJurisdiction
    from backend.schemas import OfficerContext
    from backend.services.chatbot import _officer_identity_reply
    from sqlalchemy import select

    async with AsyncSessionLocal() as db:
        officer_row = (await db.execute(
            select(SISOfficer).order_by(SISOfficer.employee_id).limit(1)
        )).scalar_one_or_none()
        if not officer_row:
            print("  SKIP  no officers seeded")
            return
        jur_ids = [j.id for j in (await db.execute(
            select(OfficerJurisdiction)
            .where(OfficerJurisdiction.officer_id == officer_row.id)
        )).scalars().all()]
        ctx = OfficerContext(
            officer_id=officer_row.id, employee_id=officer_row.employee_id,
            name=officer_row.name, email=officer_row.email,
            designation=officer_row.designation, jurisdiction_type="ward",
            jurisdiction_name="", jurisdiction_ids=jur_ids,
        )
        who = await _officer_identity_reply(db, ctx, "en", "who")
        print(f"\n    who am i  -> {who}")
        check_in("names the officer and the post", [officer_row.name, "Surveyor"], who)
        check("one line, no dump", "\n" in who, False)
        check("not the generic role answer",
              who.strip().startswith("You are a Sub Inspector Surveyor (SIS) officer"),
              False)

        for f, needle in (("name", officer_row.name),
                          ("designation", "Surveyor"),
                          ("employee_id", officer_row.employee_id)):
            one = await _officer_identity_reply(db, ctx, "en", f)
            print(f"    {f:<12}-> {one}")
            check_in(f"facet {f} answers only that", [needle], one)
            check(f"facet {f} is one line", "\n" in one, False)

        card = await _officer_identity_reply(db, ctx, "en", "profile")
        check_in("profile opens the full card",
                 ["Your profile:", "Employee ID", "Jurisdiction"], card)


try:
    asyncio.run(_e2e())
except Exception as ex:  # DB or deps unavailable — routing/render still covered
    print(f"  SKIP  {type(ex).__name__}: {ex}")

print("\n" + "=" * 70)
if failures:
    print(f"{len(failures)} FAILURE(S):\n")
    for f in failures:
        print("  - " + f)
    sys.exit(1)
print("All officer-identity checks passed.")
