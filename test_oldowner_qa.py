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
    ("husband and relationship", [q.RELATIVE, q.RELATION]),
    ("what is the husband and relationship?", [q.RELATIVE, q.RELATION]),
    ("உறவினர் பெயர் என்ன?", [q.RELATIVE]),
    ("கணவர் மற்றும் உறவு என்ன?", [q.RELATIVE, q.RELATION]),
    ("show my pending applications", []),
    ("who are the owners of survey 5", []),
]
bad = [(m, q.attributes(m), want) for m, want in CASES if q.attributes(m) != want]
for b in bad:
    print("FAIL", b)
print(f"{len(CASES) - len(bad)}/{len(CASES)} attribute cases ok")

# ── Unit tests for answer() formatting ─────────────────────────────────────
r1 = {"row_id": 1, "owner_name": "ஆர்.சுப்பிரமணி", "owner_ename": None, "relation": "...", "relative_name": "-", "relative_ename": None}
r2 = {"row_id": 2, "owner_name": "குஞ்சுமணி", "owner_ename": None, "relation": "...", "relative_name": "-", "relative_ename": None}
r3 = {"row_id": 3, "owner_name": "சண்பக வள்ளி", "owner_ename": None, "relation": "மனைவி", "relative_name": "சண்முக ரெட்டி", "relative_ename": None}
r4 = {"row_id": 4, "owner_name": "வெங்கட்ராமன்", "owner_ename": None, "relation": "...", "relative_name": "-", "relative_ename": None}
multi_owners = [r1, r2, r3, r4]
single_owner = [r3]

# Test 1: Multi-owner single field (relative name)
ans_single = q.answer("what is the relative name?", [q.RELATIVE], "2026/0154/02/012547", multi_owners, [], False)
assert "Father / husband (relative) name" in ans_single, f"Expected field label in header: {ans_single}"
assert "• Owner 1 — ஆர்.சுப்பிரமணி: not recorded" in ans_single, f"Missing Owner 1 line: {ans_single}"
assert "• Owner 3 — சண்பக வள்ளி: சண்முக ரெட்டி" in ans_single, f"Missing Owner 3 line: {ans_single}"
assert ans_single.count("Father / husband") == 1, f"Field label should only appear in header, not repeated per owner: {ans_single}"

# Test 2: Multi-owner multi field (husband and relationship)
ans_multi = q.answer("husband and relationship", [q.RELATIVE, q.RELATION], "2026/0154/02/012547", multi_owners, [], False)
assert "Owner 1 — ஆர்.சுப்பிரமணி:" in ans_multi, f"Missing section header Owner 1: {ans_multi}"
assert "Owner 3 — சண்பக வள்ளி:" in ans_multi, f"Missing section header Owner 3: {ans_multi}"
assert "• Relationship: மனைவி" in ans_multi, f"Missing relationship for Owner 3: {ans_multi}"
# Section headers must NOT have a bullet so plainToCard() parses them as table sections
assert "• Owner 1" not in ans_multi, f"Section header must not have a bullet: {ans_multi}"

# Test 3: Multi-owner single field in Tamil
ans_ta = q.answer("உறவினர் பெயர் என்ன?", [q.RELATIVE], "2026/0154/02/012547", multi_owners, [], True)
assert "தந்தை / கணவர் (உறவினர்) பெயர்" in ans_ta, f"Expected Tamil header: {ans_ta}"
assert "• உரிமையாளர் 1 — ஆர்.சுப்பிரமணி: பதிவு செய்யப்படவில்லை" in ans_ta, f"Expected Tamil Owner 1: {ans_ta}"
assert "• உரிமையாளர் 3 — சண்பக வள்ளி: சண்முக ரெட்டி" in ans_ta, f"Expected Tamil Owner 3: {ans_ta}"

# Test 4: Single owner single field
ans_so = q.answer("what is the relative name?", [q.RELATIVE], "2026/0154/02/000001", single_owner, [], False)
assert ans_so == "Father / husband (relative) name: சண்முக ரெட்டி  [2026/0154/02/000001]", f"Unexpected single-owner format: {ans_so}"

# Test 5: Single owner multi field
ans_so_multi = q.answer("husband and relationship", [q.RELATIVE, q.RELATION], "2026/0154/02/000001", single_owner, [], False)
assert "Owner 1" not in ans_so_multi, f"Single owner should not have Owner 1 label: {ans_so_multi}"
assert "• Father / husband (relative) name: சண்முக ரெட்டி" in ans_so_multi
assert "• Relationship: மனைவி" in ans_so_multi

# Test 6: "relation code of the NEW owner" must defer -- never answer from
# THIS (old-owner) table just because "relation"/"relation code" is a bare
# cue it otherwise claims. A bug: it used to answer confidently from the
# wrong owner's row (OLD instead of NEW).
for _msg in ("what is the relation code of the new owner",
             "relationship of the new owner's record",
             "share of the new owner",
             "extent of the new owner"):
    _attrs = q.attributes(_msg)
    assert _attrs == [], f'"{_msg}" must defer (return []), not claim the OLD-owner table: {_attrs}'

# "compare the new owner and the old owner" must still work -- the "new
# owner" guard must not swallow a genuine comparison question.
_cmp_attrs = q.attributes("compare the new owner's survey number with the old owner's survey number")
assert _cmp_attrs == [q.COMPARE], f"comparison involving 'new owner' must still be claimed: {_cmp_attrs}"

print("All answer() format tests passed successfully!")
raise SystemExit(1 if bad else 0)

