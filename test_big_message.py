"""A message that holds many questions is split into its own questions (`_big_plan`). No DB, no LLM.
python test_big_message.py"""
from backend.services import chatbot as c

A, B = "2026/0153/02/048323", "2026/0153/02/049891"
CASES = [
    (f"For applcation {A} tell me the applcant name, mobile numbr, current adress, the temp sub div no, the existing patta no and when it was submited and what is the curent stauts",
     7, f"applcant name of {A}"),
    (f"Compare {A} and {B} : status, applicant name, challan no, payment mode, new owner, old owner and tell me which one was submited first",
     13, f"status of {A}"),
    (f"I have 3 questions: 1) who is the old owner of {A} 2) what is the ZDT patta no for the same applcation 3) any mismatches between the transfer and the new owner record, answer in tamil",
     3, f"who is the old owner of {A}"),
    (f"{B} IGRS owner name, IGRS form 6 no, patta no, share, relative, mobile and also is there any SD attachement or merge id for it",
     7, f"IGRS owner name of {B}"),
    (f"Pls give me pending applications in ward 001, how many are CSC, how many are ISD, how many NISD, the oldest one and the newest one", 5, "pending applications in ward 001"),
]
bad = []
for msg, n, first in CASES:
    plan = c._big_plan(msg)
    if not plan or len(plan) != n or plan[0] != first:
        bad.append((msg[:50], None if not plan else (len(plan), plan[:2]), (n, first)))
for short in ("show my pending applications", f"what is the status of {A}", "how many ISD and NISD applications do I have today please tell me",
              f"compare {A} and {B}"):
    if c._big_plan(short) is not None:
        bad.append((short, "should not split", None))
# a field qualifier carries over to the short fields after it
plan = c._big_plan(CASES[3][0])
if not plan or "IGRS share of" not in " ".join(plan):
    bad.append(("qualifier carry-over", plan, None))
for b in bad:
    print("FAIL", b)
print(f"{len(CASES) + 5 - len(bad)}/{len(CASES) + 5} ok")
raise SystemExit(1 if bad else 0)
