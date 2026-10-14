"""Negated record questions: the positive question each one is rewritten to, and how the answer is shaped.
No DB, no LLM:  python test_negq.py"""
import sys

from backend.services.chatbot import negq

APP = "2026/0153/02/048323"
H = [{"role": "assistant", "content": f"details [{APP}]"}]
REWRITES = [
    # (message, history, expected rewrite or None, expected kind prefix)
    ("is there no new owner record", [], "new owner complete details", "existrec_neg:new owner"),
    ("does it have an igrs owner", [], "igrs everyfield", "existrec:igrs"),
    ("tslr sketch illaya", [], "tslr everyfield", "existrec_neg:tslr"),
    ("natham record irukka", [], "natham complete details", "existrec:natham"),
    (f"isnt there an sd attachment for {APP}", [], f"sd attachment complete details {APP}", "existrec_neg:sd attachment"),
    ("which tslr fields are not recorded", [], "tslr everyfield", "blank"),
    ("what is not recorded for this application", [], "application information everyfield", "blank"),
    ("which fields are blank in the parcel modification", [], "parcel modification everyfield", "blank"),
    ("who has not recommended it", [], "missing recommendation", "missrec"),
    ("which desks have not submitted yet", [], "missing recommendation", "missrec"),
    ("does the zdt patta not match the new owner patta", [H], None, "differ"),
    ("zdt patta match aagala", [H], None, "differ"),
    ("isnt the survey no same as the new owner", [H], None, "differ"),
    ("which fields did not change in the parcel modification", [], "parcel modification which fields changed", "same"),
    ("no remarks from dis?", [], None, "exist"),
    ("is there no merge id", [], None, "exist"),
    # untouched: a number is not a negation, lists / visits / documents / status scopes belong to other layers
    ("what is the tslr door no", [], None, None),
    ("door no and survey no of the tslr", [], None, None),
    ("which applications are not rejected", [], None, None),
    ("which documents are not submitted", [], None, None),
    ("is the field visit not scheduled", [], None, None),
    ("which fields did not change", [], None, None),          # no record named, none in the history: nothing to compare
    ("how many tslr records", [], None, None),
    ("what if there is no igrs owner", [], "igrs everyfield", "existrec_neg:igrs"),
    ("what if the challan is not paid", [], None, "exist"),
    ("sis recommend pannalana enna aagum", [], "missing recommendation", "missrec"),
    ("எந்த பட்டாக்களில் டிஜிட்டல் கையொப்பம் இல்லை?", [], None, None),
]

SHAPES = [
    # (kind, text, expected substring, expected absent substring)
    ("blank", "X:\n• Door: not recorded\n• Owner: A", "Door: not recorded", "Owner: A"),
    ("blank", "X:\n• Owner: A", "No field of this record is blank", None),
    ("blank", "No IGRS owner record is held for X.", "No IGRS owner record is held", None),
    ("same", "X:\n• ✔ Patta: 1  |  1\n• ✘ Name: A  |  B\n• (✔ match)", "✔ Patta", "✘ Name"),
    ("same", "X:\n• ✘ Name: A  |  B", "None of the fields stayed the same", "✘ Name"),
    ("existrec:igrs", "2026/0153/02/048323 (IGRS):\n• Door: not recorded", "Yes, the IGRS owner record is held", "Door"),
    ("existrec_neg:igrs", "2026/0153/02/048323 (IGRS):\n• Door: not recorded", "No, the IGRS owner record is held", None),
    ("existrec_neg:igrs", "No IGRS owner record is held for X.", "Correct.", None),
]
LEADS = [
    ("differ", "• ✘ Patta: 1  |  2", "Correct, they do not match"),
    ("differ", "• ✔ Patta: 1  |  1", "No, they match"),
    ("exist", "DIS — remarks: recommended  [X]", "No, it is recorded"),
    ("exist", "Merged application id: not recorded  [X]", "Correct, nothing is recorded"),
    ("exist", "Yes -- 2 TSLR sketch record(s) are held for X.", "No, 2 TSLR"),
]


PLANS = [
    ("which tslr fields are not recorded and which are same as the application",
     ["which tslr fields are not recorded", "which are same as the application (tslr)"]),
    ("what is the status of 2026/0153/02/048323 and who is the new owner",
     ["what is the status of 2026/0153/02/048323", "who is the new owner"]),
    ("compare 2026/0153/02/048323 and 2026/0154/02/015395 on status and applicant",
     ["status of 2026/0153/02/048323", "status of 2026/0154/02/015395", "applicant of 2026/0153/02/048323", "applicant of 2026/0154/02/015395"]),
    ("hi sir for 2026/0153/02/048323 tell me the status, who is the new owner, is there an igrs owner, then for 2026/0154/02/015395 challan, sro, merge id",
     ["the status of 2026/0153/02/048323", "who is the new owner of 2026/0153/02/048323", "is there an igrs owner of 2026/0153/02/048323",
      "challan of 2026/0154/02/015395", "sro of 2026/0154/02/015395", "merge id of 2026/0154/02/015395"]),
]


def main():
    bad = 0
    from backend.services.chatbot import _big_plan
    for msg, want in PLANS:
        got = _big_plan(msg)
        if got != want:
            bad += 1
            print(f"FAIL plan {msg[:50]!r}: {got}")
    for msg, hist, want, kind in REWRITES:
        got = negq.rewrite(msg, hist or H)
        k = negq.KIND.get()
        if msg.startswith(("what if", "sis recommend")) and not negq.HYP.get():
            bad += 1
            print(f"FAIL what-if not flagged: {msg!r}")
        ok = (kind is None and got is None) or (kind is not None and got is not None and k == kind and (want is None or got == want))
        if not ok:
            bad += 1
            print(f"FAIL rewrite {msg!r}: got {got!r} / {k!r}, wanted {want!r} / {kind!r}")
    for kind, text, has, hasnt in SHAPES:
        negq.KIND.set(kind)
        out = negq.shape(text, False)
        if has not in out or (hasnt and hasnt in out):
            bad += 1
            print(f"FAIL shape {kind}: {out!r}")
    for kind, text, has in LEADS:
        negq.KIND.set(kind)
        out = negq.lead(text, False)
        if has not in out:
            bad += 1
            print(f"FAIL lead {kind}: {out!r}")
    total = len(PLANS) + len(REWRITES) + len(SHAPES) + len(LEADS)
    print(f"{total - bad}/{total} ok")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
