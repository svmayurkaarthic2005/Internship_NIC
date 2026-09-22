"""Service code questions — "what is 0153?", "which service code is <app>?".

A bare code used to fall through to the LLM, which answered "The service code
is 0153." — the question restated. Every urban code has an official name in
SIS_URBAN_SERVICES, so the meaning is a lookup, not a generation.

    python test_service_code_queries.py            # routing + answers (no LLM)
    python test_service_code_queries.py --routing  # routing only
"""
import sys

sys.path.insert(0, ".")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:                                            # noqa: BLE001
    pass

from backend.services.rag import parse_intent                # noqa: E402
from backend.services.chatbot import (                            # noqa: E402
    _service_code_lookup_answer, _unidentified_number_answer,
)
from backend.utils.helpers import (                          # noqa: E402
    SIS_URBAN_SERVICES, describe_service_code, find_service_codes,
    normalize_service_code, service_code_one_liner,
)

_APP = "2026/0154/28/001280"

ROUTING = [
    # A code named on its own is a definition question.
    ("what is 0153?", "service_code_lookup"),
    ("what is 0154", "service_code_lookup"),
    ("what does 0155 mean", "service_code_lookup"),
    ("0153 meaning", "service_code_lookup"),
    ("explain 0158", "service_code_lookup"),
    ("what is service code 0156", "service_code_lookup"),
    ("what is 0169", "service_code_lookup"),
    ("0153", "service_code_lookup"),
    ("0154?", "service_code_lookup"),
    ("0154 endral enna", "service_code_lookup"),
    ("how many service codes start with 016", "service_code_lookup"),
    ("list service codes in 015", "service_code_lookup"),
    # The application number carries a code of its own, so a code question
    # about a file is a field lookup on that file.
    (f"what is the service code of {_APP}", "application_status"),
    (f"which service code is {_APP}?", "application_status"),
    # Questions that were already right and must stay so.
    ("show 0153 applications", "nisd_applications"),
    ("how many 0154 applications do I have", "isd_applications"),
    ("what is my jurisdiction", "jurisdiction_summary"),
    (f"status of {_APP}", "application_status"),
    ("pending applications", "pending_applications"),
    ("Is there any fee difference between ISD and NISD?", "fee_lookup"),
    # A number nobody labelled is not assumed to be a service code: 0015 is a
    # block number here, and could be a typo for anything.
    ("what is 0015", "unidentified_number"),
    ("0015", "unidentified_number"),
    ("what does 12345 mean", "unidentified_number"),
    ("what is 9999", "unidentified_number"),
    # ...but if the officer SAYS service code, "there is no such code" is a
    # real answer to the question they asked.
    ("what is service code 0015", "service_code_lookup"),
]

ANSWERS = [
    # (question, substrings the answer must carry)
    ("what is 0153?", ["0153", "NISD", "Not Involving Subdivision",
                       "no field visit", "as recorded in the register", "Tahsildar"]),
    ("what is 0154?", ["0154", "ISD", "Involving Subdivision",
                       "Senior Draughtsman", "as recorded in the register", "required"]),
    ("what is 0155?", ["0155", "MERGE", "Merge Subdivisions"]),
    # A code outside the register is named, and said to be outside it.
    ("what does 0161 mean?", ["0161", "Street Master",
                              "not one of the three application types"]),
    ("what is 0169", ["0169", "Govt to Private", "no 0169 applications"]),
    # A prefix lists what it matches, and does not invent a count.
    ("how many service codes start with 016", ["0160", "0169", "9 service codes"]),
    # A number the officer CALLED a service code: no such code, and no
    # 30-row table dumped after it.
    ("what is service code 0015", ["no urban service code", "0153", "0155"]),
]


def check(cond, label, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}")
    if not cond and detail:
        print(f"        {detail}")
    return bool(cond)


def main() -> int:
    routing_only = "--routing" in sys.argv
    ok = True

    print("\n1. every urban service code is describable")
    for code in SIS_URBAN_SERVICES:
        text = describe_service_code(code)
        ok &= check(bool(text) and code in text and SIS_URBAN_SERVICES[code]["short"] in text,
                    f"describe_service_code({code})", repr(text))
        ok &= check(bool(service_code_one_liner(code)), f"one-liner({code})")

    print("\n2. code extraction")
    for text, want in [
        ("what is 0153", ["0153"]),
        ("153", ["0153"]),
        ("0153 and 0161", ["0153", "0161"]),
        ("ward 0015 block 0015", []),          # not service codes
        ("2026", []),                          # a year is not a code
    ]:
        got = find_service_codes(text)
        ok &= check(got == want, f"find_service_codes({text!r})", f"got {got}, want {want}")
    ok &= check(normalize_service_code("154") == "0154", "normalize '154'")
    ok &= check(normalize_service_code("0015") is None, "normalize '0015' -> None")

    print("\n3. routing")
    for q, want in ROUTING:
        got = parse_intent(q)
        ok &= check(got == want, f"{q!r}", f"got {got}, want {want}")

    if routing_only:
        print("\n(--routing: answers skipped)")
    else:
        print("\n4. answers")
        for q, needles in ANSWERS:
            answer = _service_code_lookup_answer(q)
            missing = [n for n in needles if n not in answer]
            ok &= check(not missing, f"{q!r}", f"missing {missing} in: {answer[:200]}")
        # Tamil answers the same question in Tamil.
        ta = _service_code_lookup_answer("0154 endral enna", is_tamil=True)
        ok &= check("சேவை குறியீடு" in ta and "0154" in ta, "Tamil answer", ta[:120])

        print("\n5. a number that is not recognised is not guessed at")
        for q, num in [("what is 0015", "0015"), ("0015", "0015"),
                       ("what does 12345 mean", "12345")]:
            ans = _unidentified_number_answer(q)
            ok &= check(
                num in ans and "could not identify" in ans
                and "service code" in ans and "ward" in ans,
                f"{q!r}", ans[:160])
            ok &= check("NISD" not in ans and "Involving" not in ans,
                        f"{q!r} claims no meaning", ans[:160])
        ta_u = _unidentified_number_answer("0015 enna", is_tamil=True)
        ok &= check("தெரியவில்லை" in ta_u and "0015" in ta_u, "Tamil answer", ta_u[:120])

    print("\n" + "=" * 70)
    print("ALL PASS" if ok else "FAILURES ABOVE")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
