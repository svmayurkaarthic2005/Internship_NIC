"""oldowner_qa: old-owner (chitta_temp_old_owner) questions + comparison with the temp
sub-division record -- typos, Tamil, Tanglish. No DB, no LLM.  python test_oldowner_qa.py"""
from backend.services import oldowner_qa as q

CASES = [
    ("Who is the old owner for applcation 2026/0154/02/015395?", [q.NAME]),
    ("What is the owner name for applcation 2026/0154/02/015395?", []),  # the fuller owner handler answers it
    ("What is the owners patta no?", [q.PATTA]),
    ("Who is the owners father or husband name?", [q.RELATIVE]),
    ("What is the relashionship of the owner?", [q.RELATION]),
    ("What is the relative name?", [q.RELATIVE]),
    ("What is the owner numbr?", [q.OWNERNUM]),
    ("What is the survey no and sub divison no of the old owner?", [q.SURVEYSUB]),
    ("What is the stauts of the old owner record?", [q.STATUS]),
    ("Is the old owner male or female?", [q.SEX]),
    ("What is the relation code of the owner?", [q.RELCODE]),
    ("What are the land extent details extent u1 u2 and u3 for this applcation?", [q.EXTENT]),
    ("Are there any old owner detials avalable?", [q.EXISTS]),
    ("Give complete old owner detials", [q.FULL]),
    ("Compare the old owner patta no with the new patta no", [q.COMPARE]),
    ("Does the survay no match in both tables?", [q.COMPARE]),
    ("Does the owner record and subdivision record belong to the same district, taluk, town, ward and block?", [q.COMPARE]),
    ("Which fields are different between the chitta old owner record and the temp subdivision record?", [q.COMPARE]),
    ("old owner oda patta number enna", [q.PATTA]),
    ("பழைய உரிமையாளர் யார்?", [q.NAME]),
    ("உரிமையாளர் ஆணா பெண்ணா?", [q.SEX]),
    ("chitta owner recrd and subdivison recrd mismatch irukka", [q.COMPARE]),
    ("show my pending applications", []),
    ("who are the owners of survey 5", []),
]
bad = [(m, q.attributes(m), want) for m, want in CASES if q.attributes(m) != want]
for b in bad:
    print("FAIL", b)
print(f"{len(CASES) - len(bad)}/{len(CASES)} ok")
raise SystemExit(1 if bad else 0)
