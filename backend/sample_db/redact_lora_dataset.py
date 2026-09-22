"""
Redact real, changing government records out of a generated LoRA/QLoRA
dataset -- application numbers and CAN numbers -- while keeping the
surrounding phrasing/structure/terminology the fine-tune is actually meant to
teach. Real records belong to PostgreSQL/RAG at inference time; a fine-tune
that memorizes today's application numbers as if they were permanent facts is
the exact anti-pattern this exists to remove.

Pure text processing over already-generated JSONL -- no DB, no LLM, so it
costs nothing to re-run after changing the patterns below.

Usage:
    python -m backend.sample_db.redact_lora_dataset IN.jsonl [IN2.jsonl ...] --out OUT.jsonl
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# "2026/0154/28/001280" -> "2026/0154/28/000000". Year, service code (which
# NISD/ISD/MERGE it is) and district are static domain structure worth
# keeping; the serial is the part that identifies one specific real file.
_APP_NO_RE = re.compile(r"\b(\d{4}/(?:0153|0154|0155)/\d{2}/)\d{6}\b")

# A bare 12 or 15 digit run is a CAN number (see CLAUDE.md's CAN_LENGTHS) --
# masked to the same length so "how many digits" answers stay correct.
_CAN_RE = re.compile(r"(?<!\d)(\d{12}|\d{15})(?!\d)")

# An Indian mobile number -- PII, not just a "changing record", so this one
# matters even more than the application number. Applicant mobiles surface in
# `applicant_mobile` field-projection follow-ups and the details card.
_MOBILE_RE = re.compile(r"(?<!\d)([6-9]\d{9})(?!\d)")


def redact(text: str) -> str:
    text = _APP_NO_RE.sub(lambda m: m.group(1) + "0" * 6, text)
    text = _CAN_RE.sub(lambda m: "0" * len(m.group(1)), text)
    text = _MOBILE_RE.sub("9000000000", text)
    return text


def redact_example(ex: dict) -> dict:
    for msg in ex.get("messages", []):
        if msg.get("role") == "assistant":
            msg["content"] = redact(msg["content"])
        elif msg.get("role") == "user":
            # A follow-up chain's own user turns can echo a real number back
            # ("Sub-Registrar" replies don't, but a positional "for 2026/...
            # what is X" phrasing could) -- redact those too for consistency.
            msg["content"] = redact(msg["content"])
    return ex


def main(inputs: list[Path], out_path: Path) -> int:
    total = 0
    with out_path.open("w", encoding="utf-8") as out:
        for path in inputs:
            if not path.exists():
                print(f"skip (not found): {path}")
                continue
            with path.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    ex = redact_example(json.loads(line))
                    out.write(json.dumps(ex, ensure_ascii=False) + "\n")
                    total += 1
    print(f"Wrote {total} redacted examples to {out_path}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("inputs", type=Path, nargs="+")
    parser.add_argument("--out", type=Path, default=Path("lora_dataset_final.jsonl"))
    args = parser.parse_args()
    raise SystemExit(main(args.inputs, args.out))
