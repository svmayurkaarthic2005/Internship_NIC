"""Owner (urban_natham_chitta_owner) field questions as implicit follow-ups.

After a joint-owner or survey-ownership answer the officer asks about one of
the register's owner columns -- gender, relationship / relative name, Aadhaar,
ownership share -- without naming the survey again. Those fragments carried
none of the follow-up cues, so `followup_context.classify()` returned NONE, the
survey reference was dropped, and the question fell to the LLM, which invented
a value. This locks in that they keep the reference instead.

No Ollama, no database: this is the pure classification / resolution layer.

    python test_owner_field_followups.py            # classification + resolution
    python test_owner_field_followups.py --routing  # same thing (no DB either way)
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

S = fctx.FOLLOWUP_SINGULAR
A = fctx.FOLLOWUP_LIST_AGGREGATE

# (fragment, expected classify() kind). Every one of these must resolve against
# a survey context and against a single-application context.
CASES = [
    ("who are the joint owners", S),
    ("what is the ownership share", S),
    ("what is the owner's share", S),
    ("is the owner male or female", S),
    ("what is the gender", S),
    ("what is the owner's gender", S),
    ("what is the father's name", S),
    ("what is the relative's name", S),
    ("what is the relationship", S),
    ("what is his relationship", S),
    ("what is the aadhaar number", S),
    ("who is the wife", S),
    ("who is the husband", S),
    ("what is the patta number", S),
    ("what is the extent", S),
    ("how many owners are there", A),
    # Tamil / Tanglish
    ("பங்கு என்ன", S),
    ("பாலினம் என்ன", S),
    ("உறவுமுறை என்ன", S),
    ("ஆதார் எண் என்ன", S),
    ("pangu enna", S),
    ("paalinam enna", S),
    ("aadhaar enna", S),
]

# Fragments that must STAY out of this layer.
NEGATIVE = [
    # plural pointer -> _rescope_list_followup owns it
    ("what is their share", fctx.FOLLOWUP_NONE),
    # names its own subject ("my")
    ("how many owners does my survey have", fctx.FOLLOWUP_NONE),
    ("show me all my joint owner applications", fctx.FOLLOWUP_NONE),
]

survey_ctx = fctx.FollowupContext(entity=fctx.ENTITY_SURVEY, survey_numbers=["5"],
                                  query_type="Survey Ownership", intent="survey_owners")
app_ctx = fctx.FollowupContext(entity=fctx.ENTITY_APPLICATION,
                               application_numbers=["2026/0154/28/001280"],
                               query_type="Joint Ownership Check",
                               intent="joint_owner_check")


def main() -> int:
    failed = 0
    for frag, want in CASES:
        got = fctx.classify(frag)
        ok = got == want
        rs = fctx.resolve(frag, survey_ctx, "en")
        ra = fctx.resolve(frag, app_ctx, "en")
        ok = ok and rs.resolved and ra.resolved
        # a survey follow-up must actually carry the survey number forward
        if want == S:
            ok = ok and survey_ctx.survey_numbers == (rs.context.survey_numbers
                                                      if rs.context else [])
        print(f"{'PASS' if ok else 'FAIL'}  classify={got:9s} "
              f"svc={rs.resolved!s:5s} app={ra.resolved!s:5s} | {frag}")
        failed += not ok

    for frag, want in NEGATIVE:
        got = fctx.classify(frag)
        ok = got == want
        print(f"{'PASS' if ok else 'FAIL'}  classify={got:9s} (expected {want}) | {frag}")
        failed += not ok

    # ---- _owner_detail_line: projected relationship / gender must surface ----
    from backend.services.chatbot import _owner_detail_line
    row = {"name": "Ramalakshmi", "name_tamil": "ராமலெட்சுமி",
           "sub_division": "Survey Level", "ownership_share": 50.0,
           "ownership_type": "joint", "relative_name": "Natesan",
           "relationship": "s/o", "gender": "F", "aadhaar_last4": "1234",
           "mobile": None, "address": None}
    checks = [
        ("what is the owner's relationship", "s/o Natesan"),
        ("who is the father of the owner", "s/o Natesan"),
        ("what is the gender of the owner", "Female"),
        ("what is the owner's aadhaar", "1234"),
        ("just list the owners", None),  # no field asked -> no extras
    ]
    for q, needle in checks:
        line = _owner_detail_line(row, q)
        if needle is None:
            ok = "—" in line and line.count("—") == 1  # only the base dash
        else:
            ok = needle in line
        print(f"{'PASS' if ok else 'FAIL'}  render | {q!r} -> {line}")
        failed += not ok

    print(f"\n{'ALL PASSED' if not failed else str(failed) + ' FAILURE(S)'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
