"""
Workflow-knowledge Q&A pairs for the LoRA dataset, hand-authored directly from
`backend/documents/survey_manual.txt` and
`backend/documents/tamilnilam_urban_services_and_districts.txt` -- not a live
Ollama call. `build_lora_dataset.py` deliberately excludes the routing bank's
`workflow_knowledge`/`general_query` questions because generating their
answers live would teach the model to imitate whatever llama3.1:8b happens to
say, ungrounded. These pairs sidestep that: the answer text is lifted from
the document itself, so it is exactly what the RAG/agent fallback is SUPPOSED
to retrieve and say, with nothing invented.

Pure text, no DB, no LLM -- costs nothing to re-run.

Usage:
    python -m backend.sample_db.build_lora_dataset_workflow [--out FILE]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SYSTEM_PROMPT = (
    "You are the SIS AI Assistant for Tamil Nadu Sub Inspector Surveyor "
    "officers. Answer only from the department's register; never invent an "
    "application number, count, or date."
)

# (question, answer) -- answers are lifted/paraphrased directly from the two
# corpus documents, never invented.
PAIRS: list[tuple[str, str]] = [
    # ── SIS role & workflow chain ───────────────────────────────────────
    ("What does an SIS officer do in the urban jurisdiction?",
     "Sub Inspector Surveyors (SIS) operate exclusively in Urban Jurisdictions "
     "(Municipalities & Corporations). Core duties: physical field inspection "
     "and cadastral verification for Town Survey Numbers (TSLR); verifying "
     "boundaries, sub-divisions, encroachments and land type; and preparing "
     "inspection reports and measurement sketches for ISD, Merge and "
     "Demarcation applications."),
    ("What happens after SIS verifies an ISD application?",
     "A verified ISD or Merge application is forwarded to the Senior "
     "Draughtsman (SD) for the sketch, then to the Deputy Inspector Surveyor "
     "(DIS); after DIS approval it goes to the Tahsildar, who applies the "
     "DSC and generates the patta transfer order."),
    ("What happens after SIS verifies a NISD application?",
     "A verified NISD application is forwarded directly to the Zonal Level "
     "Tahsildar (ZDT), who holds the DSC key and approves it -- no SD or DIS "
     "step, since NISD involves no sub-division."),
    ("What is the urban application number format?",
     "YYYY/URBAN_SERVICE_CODE/DISTRICT_CODE/SEQUENCE -- for example "
     "2026/0154/02/000001 is an ISD application filed in Chennai (district "
     "code 02) in 2026."),
    ("What is service code 0155?",
     "0155 is MERGE -- verification of merged plots and their total area."),
    ("What is the difference between 0153 and 0154?",
     "0153 (NISD) is a direct patta transfer verification with no new "
     "sub-division. 0154 (ISD) involves a sub-division and requires a "
     "mandatory field inspection and sub-division sketch."),

    # ── Sub-division numbering ──────────────────────────────────────────
    ("How are sub-division numbers assigned to a survey number?",
     "First-level sub-division uses sequential numbers (145/1, 145/2, "
     "145/3). A further split of one of those uses letters (145/1A, "
     "145/1B). A third level, if needed, uses numbers again (145/1A1, "
     "145/1A2). Sub-division depth beyond 3 levels is discouraged."),
    ("What does a temporary subdivision number like 3/T1 mean?",
     "When an ISD or MERGE application is processed, the Deputy Inspector "
     "Surveyor (DIS) assigns a temporary number to each proposed parcel "
     "before final numbers are confirmed. The part before /T is the "
     "existing subdivision being split (0 if the parent survey has none "
     "yet); T stands for Temporary; the number after T is a sequence "
     "counter (1, 2, 3...) for each new parcel."),
    ("Does the temporary subdivision number become the final one?",
     "Not always. The first temporary parcel (T1) usually retains the "
     "existing subdivision number as its final one; later parcels (T2, T3, "
     "...) receive new sequential final numbers. Temporary numbers only "
     "become final after DIS approval, and a rejected application keeps its "
     "temporary numbers forever -- it never receives final ones."),
    ("Are temporary subdivision numbers assigned to NISD applications?",
     "No. Temporary numbers are only assigned to ISD (0154) and MERGE "
     "(0155) applications, since only those involve a sub-division."),
    ("Must the areas of all sub-divisions add up to the original survey area?",
     "Yes -- the sum of every sub-division's area must equal the original "
     "survey's area, within a tolerance of about ±0.5% for survey "
     "measurement. A variance beyond that tolerance requires a re-survey "
     "and DIS approval to explain it."),

    # ── Merge rules ──────────────────────────────────────────────────────
    ("Which survey numbers can be merged?",
     "Survey numbers can be merged only when all of these hold: the same "
     "owner (or all joint owners agree), the parcels are geographically "
     "adjacent with a shared boundary, they are in the same block/ward/town, "
     "they share the same land classification, and none of them carries an "
     "encumbrance, litigation or encroachment."),
    ("Can survey numbers from different blocks be merged?",
     "No -- all surveys being merged must belong to the same block, ward "
     "and town. Surveys from different blocks cannot be merged."),
    ("Can I merge two survey numbers with different owners?",
     "No -- common ownership is required. All the survey numbers being "
     "merged must have the same patta holder, or every joint owner must "
     "agree to the merge."),
    ("What happens to the survey numbers after a merge is approved?",
     "The old survey numbers are marked as merged, a new survey number is "
     "created (typically the lowest original number with an 'M' suffix, or "
     "the lowest number retained with the others marked as merged into it), "
     "and a single patta is issued for the combined area."),

    # ── Encroachment ─────────────────────────────────────────────────────
    ("What counts as encroachment on a survey number?",
     "Unauthorized occupation of survey land -- construction beyond the "
     "approved boundary, encroachment by a neighbouring survey, or a survey "
     "encroaching onto (or being encroached on by) public land or a road."),
    ("What happens when SIS detects encroachment during a field visit?",
     "The application is flagged for encroachment. The flag prevents "
     "automatic approval and requires DIS and Tahsildar review, and the "
     "timeline typically extends by 15-30 days while it is resolved."),
    ("What steps does SIS follow to check for encroachment?",
     "Document review of the survey sketch and prior visit reports; a "
     "physical site visit with GPS measurement of the boundary; checking "
     "boundary stones; verifying the neighbouring survey's boundary; "
     "photographing and recording GPS coordinates of every corner; and "
     "preparing a report with a sketch of the encroachment and a "
     "recommendation."),

    # ── Litigation ───────────────────────────────────────────────────────
    ("What happens if litigation is found on a survey number?",
     "The application is marked with a litigation flag, its status changes "
     "to 'On Hold - Litigation', and it cannot proceed until the court stay "
     "is vacated, judgment favours the applicant, or the parties settle. No "
     "processing timeline applies while it is on hold."),
    ("How does SIS check for litigation on a survey number?",
     "By checking the Encumbrance Certificate for litigation remarks or "
     "case references, checking the revenue records and Tahsildar remarks "
     "for a litigation flag, asking at the field visit about any known "
     "dispute, and cross-checking court records for an active stay order."),
    ("Does an application resume automatically once litigation is cleared?",
     "It resumes only after the litigation is actually cleared (stay "
     "vacated, judgment given, or settlement reached) -- and a fresh field "
     "visit may be required at that point before it proceeds further."),

    # Tamil
    ("ISD விண்ணப்பம் சரிபார்த்த பிறகு என்ன நடக்கும்?",
     "சரிபார்க்கப்பட்ட ISD அல்லது Merge விண்ணப்பம் மூத்த வரைவாளர் (SD) இடம் "
     "ஓவியத்திற்காக அனுப்பப்படும், பின்னர் துணை ஆய்வாளர் கள அளவையாளர் (DIS) "
     "இடம்; DIS ஒப்புதலுக்குப் பிறகு அது தாசில்தாரிடம் செல்லும், அவர் DSC-ஐப் "
     "பயன்படுத்தி பட்டா மாற்றல் உத்தரவை உருவாக்குவார்."),
    ("எந்த சர்வே எண்களை இணைக்க முடியும்?",
     "ஒரே உரிமையாளர், அருகிலுள்ள எல்லை, ஒரே பிளாக்/வார்டு/நகரம், ஒரே நில "
     "வகைப்பாடு, மற்றும் எந்த வழக்கு/அடமானம்/ஆக்கிரமிப்பும் இல்லாத போது "
     "மட்டுமே சர்வே எண்களை இணைக்க முடியும்."),
]


def main(out_path: Path) -> int:
    examples = []
    for q, a in PAIRS:
        examples.append({
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": q},
                {"role": "assistant", "content": a},
            ],
            "meta": {"source": "workflow_corpus"},
        })
    with out_path.open("w", encoding="utf-8") as f:
        for ex in examples:
            f.write(json.dumps(ex, ensure_ascii=False) + "\n")
    print(f"Wrote {len(examples)} examples to {out_path}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=Path("lora_dataset_workflow.jsonl"))
    args = parser.parse_args()
    raise SystemExit(main(args.out))
