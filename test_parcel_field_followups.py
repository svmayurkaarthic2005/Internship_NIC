"""urban_parcel_register columns the survey projection drops.

`build_app_tables.py` carries a parcel's geography, area, patta number and land
type into `survey_numbers` / `sub_divisions` and nothing else. A pointed
question about soil / irrigation / tax / the double-crop, partition, priority,
assessed and cultivable flags / old survey number / Form 6-8 / door / street
code used to route to `survey_detail` and get the generic parcel card, which
silently omits the field -- a confident non-answer. And as a bare follow-up
after a survey answer it lost the survey reference entirely and fell to the LLM.

This checks:
  1. `_asked_untracked_parcel_field` recognises each dropped column and does NOT
     fire on a field the projection DOES carry (patta, area, land type, status).
  2. `followup_context.classify` keeps the survey reference for the bare
     follow-up form.
  3. `_untracked_parcel_field_answer` names the field and points at what is held.

No Ollama, no database.

    python test_parcel_field_followups.py
"""
from __future__ import annotations

import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.services import followup_context as fctx
from backend.services.chatbot import (
    _asked_untracked_parcel_field, _untracked_parcel_field_answer,
)

# (fresh question, expected untracked key or None, bare follow-up form)
CASES = [
    ("what is the soil type of survey 5", "soil_type", "what is the soil type"),
    ("what is the primary soil type for survey 5", "soil_type", "primary soil type?"),
    ("what is the irrigation source of survey 5", "irrigation_source",
     "what is the irrigation source"),
    ("is survey 5 double crop", "double_crop", "is it double crop"),
    ("what is the partition indicator for survey 5", "partition_indicator",
     "what is the partition indicator"),
    ("is survey 5 a government priority parcel", "government_priority",
     "is it a government priority parcel"),
    ("what is the tax rate code for survey 5", "tax_rate", "what is the tax rate"),
    ("what is the tax per hectare for survey 5", "tax_per_hectare",
     "what is the tax per hectare"),
    ("what is the total tax on survey 5", "total_tax", "what is the total tax"),
    ("what is the municipal tax for survey 5", "municipal_tax",
     "what is the municipal tax"),
    ("what is the land use code of survey 5", "land_use", "what is the land use code"),
    ("what is the extent unit for survey 5", "extent_breakdown",
     "what is the extent unit"),
    ("what is the old survey number for survey 5", "old_survey_number",
     "what is the old survey number"),
    ("what is the old subdivision number for survey 5", "old_subdivision_number",
     "what is the old subdivision number"),
    ("what is the form 6 number for survey 5", "form_number",
     "what is the form 6 number"),
    ("what is the form 8 number for survey 5", "form_number",
     "what is the form 8 number"),
    ("what is the relinquishment number for survey 5", "relinquishment_number",
     "what is the relinquishment number"),
    ("what is the assignment number for survey 5", "assignment_number",
     "what is the assignment number"),
    ("what is the alienation number for survey 5", "alienation_number",
     "what is the alienation number"),
    ("what is the acquisition number for survey 5", "acquisition_number",
     "what is the acquisition number"),
    ("is survey 5 assessed", "assessed_flag", "is it assessed"),
    ("is survey 5 cultivable", "cultivable_flag", "is it cultivable"),
    ("what is the door number for survey 5", "door_number", "what is the door number"),
    ("what is the new door number for survey 5", "door_number",
     "what is the new door number"),
    ("what is the street code for survey 5", "street_code", "what is the street code"),
    ("what is the coss block code for survey 5", "coss_block_code",
     "what is the coss block code"),
    ("what is the descriptive remark code for survey 5", "parcel_remarks",
     "what is the descriptive remark code"),
    # Tamil
    ("சர்வே 5 இன் மண் வகை என்ன", "soil_type", "மண் வகை என்ன"),
    ("சர்வே 5 இன் நீர்ப்பாசன ஆதாரம் என்ன", "irrigation_source",
     "நீர்ப்பாசன ஆதாரம் என்ன"),
]

# Fields the projection DOES carry -> must NOT be deflected.
NEGATIVE = [
    "what is the patta number of survey 5",
    "what is the area of survey 5",
    "what is the extent of survey 5",
    "what is the land type of survey 5",
    "what is the status of survey 5",
    "how many sub-divisions does survey 5 have",
    "who owns survey 5",
    "what were the sis remarks",
    "what were the order remarks",
]

survey_ctx = fctx.FollowupContext(entity=fctx.ENTITY_SURVEY, survey_numbers=["5"],
                                  query_type="Survey Number Details",
                                  intent="survey_detail")


def main() -> int:
    failed = 0
    for fresh, want_key, followup in CASES:
        got = _asked_untracked_parcel_field(fresh.lower())
        k = fctx.classify(followup)
        r = fctx.resolve(followup, survey_ctx, "en")
        ans = _untracked_parcel_field_answer("5", want_key, False) if want_key else ""
        ok = (got == want_key
              and k != fctx.FOLLOWUP_NONE and r.resolved
              and "not held in your SIS register" in ans)
        print(f"{'PASS' if ok else 'FAIL'}  key={str(got):22s} fu={k:9s} "
              f"res={r.resolved!s:5s} | {fresh}")
        failed += not ok

    for m in NEGATIVE:
        got = _asked_untracked_parcel_field(m.lower())
        ok = got is None
        print(f"{'PASS' if ok else 'FAIL'}  key={str(got):22s} (want None) | {m}")
        failed += not ok

    print(f"\n{'ALL PASSED' if not failed else str(failed) + ' FAILURE(S)'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
