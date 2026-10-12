"""Regression: `OfficerContext.officer_stage` (backend/schemas.py) must map
every documented role correctly and fail CLOSED on an unrecognised one --
never silently grant the SIS desk's queue to a role nobody ever confirmed is
SIS.

Two confirmed defects, found by reading the property body against the
`current_stage` CHECK constraint (`'SIS','SD','DIS','TAHSILDAR','COMPLETED',
'REJECTED'`) in backend/models.py:

1. No ZDT branch at all. A "Zonal Level Tahsildar" or "ZDT" designation fell
   to the final `else` and got "SIS" -- CLAUDE.md's own workflow-role table
   maps ZDT/HQDT to the TAHSILDAR stage (the same desk the NISD chain's
   Tahsildar sits at), and `chatbot.py`'s own `_WFH_STAGE_ALIASES` already
   treats "zdt" as an alias for "TAHSILDAR" elsewhere -- `officer_stage` was
   the one place that had not caught up.
2. The plain "Tahsildar" branch returned the string "Tahsildar" (mixed
   case) -- every `Application.current_stage == officer.officer_stage`
   filter in chatbot.py/postgres.py (~15 call sites) compares against the
   DB's stored value, which the CHECK constraint fixes as the UPPERCASE
   'TAHSILDAR'. A real Tahsildar officer's own "my pending applications"
   query was therefore silently empty, always -- case mismatch, not jurisdiction.
3. Any OTHER unrecognised designation (a typo, a future role, no
   designation at all) fell to "SIS" -- granting that officer the SIS
   desk's queue by default rather than refusing to guess. Both the no-
   designation and the catch-all branches now return "UNASSIGNED_STAGE", a
   value outside the CHECK constraint, so every stage-filtered query fails
   closed to an empty result instead of a stage the officer was never shown
   to hold. No caller needed to change: the comparison stays a plain string
   equality, which simply never matches a stored row for that sentinel.

The live seed data currently has exactly one designation
("Sub Inspector Surveyor") across every officer, so none of this has any
blast radius against the real accounts today -- this is a latent code gap
being closed, not an observed live exploit. Confirmed by querying the DB
directly in this file (not assumed).

No DB write, no LLM. Run: python test_officer_stage_mapping.py
"""
import sys

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


from backend.schemas import OfficerContext
import uuid

_BASE = dict(officer_id=uuid.uuid4(), employee_id="E1", name="Test Officer",
             email="t@example.com", jurisdiction_type="ward",
             jurisdiction_name="001", jurisdiction_ids=[])

# Every value `officer_stage` can return must be one `current_stage` can
# actually hold, or the explicit fail-closed sentinel -- never anything else.
_VALID_CURRENT_STAGES = {"SIS", "SD", "DIS", "TAHSILDAR", "COMPLETED", "REJECTED"}
_FAIL_CLOSED_SENTINEL = "UNASSIGNED_STAGE"


def stage_for(designation):
    return OfficerContext(designation=designation, **_BASE).officer_stage


# ---- documented designations resolve to the stage CLAUDE.md's role table names ----
check('"Sub Inspector Surveyor" -> SIS', stage_for("Sub Inspector Surveyor") == "SIS")
check('"SIS Officer" -> SIS', stage_for("SIS Officer") == "SIS")
check('"Senior Draughtsman" -> SD', stage_for("Senior Draughtsman") == "SD")
check('"SD Officer" -> SD', stage_for("SD Officer") == "SD")
check('"Deputy Inspector Surveyor" -> DIS', stage_for("Deputy Inspector Surveyor") == "DIS")
check('"DIS Officer" -> DIS', stage_for("DIS Officer") == "DIS")
check('"Tahsildar" -> the UPPERCASE value current_stage actually stores',
      stage_for("Tahsildar") == "TAHSILDAR", stage_for("Tahsildar"))
check('"Zonal Level Tahsildar" -> TAHSILDAR (CLAUDE.md: ZDT/HQDT share the Tahsildar desk)',
      stage_for("Zonal Level Tahsildar") == "TAHSILDAR")
check('"ZDT" -> TAHSILDAR', stage_for("ZDT") == "TAHSILDAR")
check('"zdt" (lowercase) -> TAHSILDAR', stage_for("zdt") == "TAHSILDAR")

# ---- a genuinely unrecognised designation fails closed, never defaults to SIS ----
check('an empty designation ("") fails closed, not "SIS"',
      stage_for("") == _FAIL_CLOSED_SENTINEL, stage_for(""))
check('no designation at all (None) fails closed, not "SIS"',
      OfficerContext(designation=None, **_BASE).officer_stage == _FAIL_CLOSED_SENTINEL)
check('a typo/garbage designation ("Office Boy") fails closed, not "SIS"',
      stage_for("Office Boy") == _FAIL_CLOSED_SENTINEL, stage_for("Office Boy"))
check('a future/unmapped role ("RDO Officer") fails closed, not "SIS"',
      stage_for("RDO Officer") == _FAIL_CLOSED_SENTINEL, stage_for("RDO Officer"))

# ---- the fail-closed sentinel can never collide with a real stage ----
check("the fail-closed sentinel is not one of current_stage's own valid values "
      "(so a stage-filtered query matches zero rows, not a real desk's queue)",
      _FAIL_CLOSED_SENTINEL not in _VALID_CURRENT_STAGES)

# ---- every documented-role result is itself a real current_stage value ----
for desig in ("Sub Inspector Surveyor", "Senior Draughtsman", "Deputy Inspector Surveyor",
              "Tahsildar", "Zonal Level Tahsildar", "ZDT"):
    s = stage_for(desig)
    check(f'"{desig}" resolves to a value current_stage can actually hold ({s!r})',
          s in _VALID_CURRENT_STAGES)

print(f"{total - len(bad)}/{total} ok")
sys.exit(1 if bad else 0)
