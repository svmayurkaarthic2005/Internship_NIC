"""Regression: the workflow-history renderer's "TAHSILDAR" stage label used
one generic word ("Tahsildar") for both ISD and NISD, even though the
register's own data shows only NISD files ever reach that stage (SIS ->
TAHSILDAR -> COMPLETED/REJECTED, role 16 / "Forward To ZDT" in the source
extract) -- an ISD file is always closed at SD and never reaches a
Tahsildar stage at all. The chatbot's own conceptual answers (glossary,
"who approves") already correctly call NISD's approver the Zonal Level
Tahsildar; the workflow-history renderer was the one place that didn't.

No DB required for the unit check (`_wfh_desk_label_base` takes the
application number directly); a live DB cross-check against the actual
register is run separately if a DB is available.
"""
import sys

sys.path.insert(0, r"C:\proj\nic_internship")
from backend.services.chatbot import chat_smalltalk as cs

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"{'ok  ' if cond else 'FAIL'} {name}" + (f" -- {detail}" if detail and not cond else ""))


def test_nisd_tahsildar_stage_says_zonal_deputy_tahsildar():
    label = cs._wfh_desk_label_base("TAHSILDAR", "2026/0153/28/001405")
    check("NISD application number -> 'Zonal Deputy Tahsildar'", label == "Zonal Deputy Tahsildar", label)


def test_isd_tahsildar_stage_says_plain_tahsildar():
    label = cs._wfh_desk_label_base("TAHSILDAR", "2026/0154/28/001167")
    check("ISD application number -> plain 'Tahsildar' (unchanged)", label == "Tahsildar", label)


def test_no_app_no_falls_back_to_the_old_generic_label():
    label = cs._wfh_desk_label_base("TAHSILDAR")
    check("no app_no given -> generic 'Tahsildar' (backward compatible)", label == "Tahsildar", label)


def test_other_stages_are_unaffected_by_app_no():
    for app_no in ("2026/0153/28/001405", "2026/0154/28/001167", None):
        label = cs._wfh_desk_label_base("SD", app_no)
        check(f"SD stage unaffected (app_no={app_no!r})", label == "SD (Senior Draughtsman)", label)


if __name__ == "__main__":
    test_nisd_tahsildar_stage_says_zonal_deputy_tahsildar()
    test_isd_tahsildar_stage_says_plain_tahsildar()
    test_no_app_no_falls_back_to_the_old_generic_label()
    test_other_stages_are_unaffected_by_app_no()
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    sys.exit(1 if failed else 0)
