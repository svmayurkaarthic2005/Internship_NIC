"""subdivision_qa: typo / Tamil / Tanglish questions map to the right plot fields.
No DB, no LLM.  python test_subdivision_qa.py"""
from backend.services.chatbot import subdivision_qa as q

CASES = [
    ("what is the temp sub div no?", [q.TEMP_SUB]),
    ("new sub divison number", [q.NEW_SUB]),
    ("what is the new pata number?", [q.NEW_PATTA]),
    ("existing patta no", [q.EXIST_PATTA]),
    ("intha application oda pazhaya patta number enna", [q.EXIST_PATTA]),
    ("idhoda total area evlo sq m", [q.AREA_SQM]),
    ("plot numbr and aria", [q.PLOT_NO, q.AREA]),
    ("tax rate evlo", [q.TAX_RATE]),
    ("intha apln group sub div nmbr", [q.G_SUB]),
    ("மண் வகை என்ன", [q.SOIL_P, q.SOIL_S]),
    ("புதிய பட்டா எண் என்ன?", [q.NEW_PATTA]),
    ("தற்காலிக உட்பிரிவு எண்", [q.TEMP_SUB]),
]
bad = [(m, q.attributes(m), want) for m, want in CASES if q.attributes(m) != want]
for b in bad:
    print("FAIL", b)
assert not q.claims(q.attributes("what is the status")), "plain status stays with the field handlers"
print(f"{len(CASES) - len(bad)}/{len(CASES)} ok")
raise SystemExit(1 if bad else 0)
