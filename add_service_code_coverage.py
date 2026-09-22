"""
Fill the service-code coverage gap for the 27 non-core TAMILNILAM urban codes
(everything except 0153/0154/0155, which are already heavily represented).

Ground truth comes straight from backend/utils/helpers.py's SIS_URBAN_SERVICES
+ describe_service_code() -- the exact function the deterministic
service_code_lookup handler calls in chatbot.py -- so every generated answer
is byte-identical to what the live app would say. Nothing here is invented.

Deliberately NOT one template repeated 27 times: that would teach a single
memorized string, not the lookup skill. Each code gets 3 examples pulled from
a rotating pool of 6 differently-shaped questions (plain "what is", "what does
X mean", Tamil, Tanglish, digits-only, workload-framed), rotated by code index
so the same template doesn't land on adjacent codes, and the officer is
rotated too. Train and validation draw from disjoint template pools so
validation genuinely tests generalization instead of re-seeing a train phrasing.

District codes (01-38) are deliberately left OUT of this pass: that table
already lives in backend/documents/district_codes.txt for RAG to retrieve,
and baking all 38 rows into LoRA weights would be the same memorization risk
this script exists to avoid. The officer's own district (28/Thoothukudi) is
already taught via the 12 existing jurisdiction_summary examples.

Usage:
    python add_service_code_coverage.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from backend.utils.helpers import (SIS_URBAN_SERVICES, SIS_HANDLED_SERVICE_CODES,
                                    describe_service_code)

TRAIN = Path("train_augmented.jsonl")
VAL = Path("validation.jsonl")
OFFICERS = ["csenthil@sis.tn.gov.in", "msivakumar@sis.tn.gov.in",
            "muthulakshmis@sis.tn.gov.in"]
SYSTEM = ("You are the SIS AI Assistant for Tamil Nadu Sub Inspector Surveyor "
          "officers. Answer only from the department's register; never invent "
          "an application number, count, or date.")

NON_CORE = [c for c in SIS_URBAN_SERVICES if c not in SIS_HANDLED_SERVICE_CODES]

# Each template takes the code in whatever form it wants to exercise
# normalize_service_code() with -- padded, bare digits, embedded in a sentence.
TRAIN_TEMPLATES = [
    lambda c: f"what is service code {c}?",
    lambda c: f"what does {c} mean?",
    lambda c: f"{c} என்றால் என்ன?",
    lambda c: f"{c} pathi solunga",
    lambda c: f"do I have any {c} applications in my ward?",
    lambda c: f"explain code {int(c)}",  # bare digits, no leading zero
]

VAL_TEMPLATES = [
    lambda c: f"service code {c} means what",
    lambda c: f"{c} enna",
    lambda c: f"can you tell me about {c}",
]


def make_example(code: str, question: str, officer: str, source: str) -> dict:
    answer = describe_service_code(code, is_tamil=False)
    return {
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": question},
            {"role": "assistant", "content": answer},
        ],
        "meta": {
            "officer": officer,
            "intent": "service_code_lookup",
            "source": source,
            "group": "non_core_service_code",
        },
    }


def append(path: Path, rows: list[dict]) -> None:
    with path.open("a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def main():
    train_rows, val_rows = [], []
    for i, code in enumerate(NON_CORE):
        for j in range(3):
            tmpl = TRAIN_TEMPLATES[(i + j) % len(TRAIN_TEMPLATES)]
            officer = OFFICERS[(i + j) % len(OFFICERS)]
            train_rows.append(make_example(code, tmpl(code), officer,
                                            "service_code_coverage"))
        val_tmpl = VAL_TEMPLATES[i % len(VAL_TEMPLATES)]
        val_officer = OFFICERS[i % len(OFFICERS)]
        val_rows.append(make_example(code, val_tmpl(code), val_officer,
                                      "service_code_coverage"))

    append(TRAIN, train_rows)
    append(VAL, val_rows)
    print(f"added {len(train_rows)} train example(s), {len(val_rows)} "
          f"validation example(s) covering {len(NON_CORE)} non-core codes")


if __name__ == "__main__":
    main()
