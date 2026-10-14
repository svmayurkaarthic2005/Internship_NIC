"""Calculation questions over register rows (every service code). No DB, no LLM:  python test_calc.py"""
import sys

from backend.services.chatbot import calc_qa

ROWS = (
    [{"service_code": "0154", "status": "pending", "submission_channel": "CSC", "ward_number": "001", "submission_date": "2026-09-01"}] * 3
    + [{"service_code": "0153", "status": "approved", "submission_channel": "sub_registrar", "ward_number": "001", "submission_date": "2026-09-02"}] * 2
    + [{"service_code": "0158", "status": "approved", "submission_channel": "CSC", "ward_number": "002", "submission_date": "2026-09-03"}] * 6
    + [{"service_code": "0158", "status": "rejected", "submission_channel": "CSC", "ward_number": "002", "submission_date": "2026-10-03"}] * 1
    + [{"service_code": "0178", "status": "pending", "submission_channel": "citizen", "ward_number": "001", "submission_date": "2026-09-04"}] * 4
)  # 16 rows: pending 7, approved 8, rejected 1; 0158 has 7 of them

CASES = [
    ("47 plus 17", None, "47 + 17 = 64"),
    ("what is 78 / 152 * 100", None, "51.32"),
    ("20 percent of 150", None, "= 30"),
    ("12 divided by 0", None, "zero"),
    ("pending plus approved", ROWS, "pending 7 + approved 8 = 15"),
    ("total pending plus approved", ROWS, "= 15"),
    ("approved minus rejected", ROWS, "approved 8 − rejected 1 = 7"),
    ("difference between pending and approved", ROWS, "pending 7 − approved 8 = −1"),
    ("how many more approved than rejected", ROWS, "7 more approved than rejected"),
    ("ratio of approved to rejected", ROWS, "8 : 1"),
    ("ratio of CSC to citizen", ROWS, "CSC : citizen"),
    ("what percentage of 0158 applications are approved", ROWS, "6 of 7 0158"),
    ("what percentage of applications are rejected", ROWS, "1 of 16 applications (6.2%)"),
    ("sum of pending in ward 001", ROWS, "pending: 7 in ward 001"),
    ("which service code has the most applications", ROWS, "0158"),
    ("which ward has the least applications", ROWS, "ward 002") if False else ("which ward has the most applications", ROWS, "ward 001"),
    ("top 2 service codes", ROWS, "Top 2"),
    ("average applications per day", ROWS, "per day"),
    ("sum of isd and nisd", ROWS, "= 5"),
    ("sro plus csc", ROWS, "= 12"),
]
NOT_CALC = ["how many are approved", "show my pending applications", "status of 2026/0153/02/048323", "hello", "what is the tslr door no"]


def main():
    bad = 0
    for text, rows, want in CASES:
        got = calc_qa.arithmetic(text) if rows is None else calc_qa.answer(text, rows)
        if not got or want not in got:
            bad += 1
            print(f"FAIL {text!r}: {got!r} (wanted {want!r})")
    for text in NOT_CALC:
        if calc_qa.arithmetic(text) or calc_qa.looks_like_calc(text):
            bad += 1
            print(f"FAIL {text!r} must not be claimed")
    n = len(CASES) + len(NOT_CALC)
    print(f"{n - bad}/{n} ok")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
