"""
Check the citizen identifiers in sis_chatbot_db against the rules in
identifiers.py.

Read-only. Covers both layers:

  * layer 1 -- every `aadhaar_number` seeded into the CSV-shaped tables is a
    12-digit, Verhoeff-valid UIDAI number;
  * layer 2 -- every application's `can_number` has the length its
    `submission_channel` mandates (CSC 15 digits, citizen 12), the channel is
    always set, and `applicants.aadhaar_last4` is four digits.

Run from the project root:
    python -m backend.sample_db.verify_identifiers
"""
from __future__ import annotations

import sys
from pathlib import Path

# Names in the extracts are Tamil, and a Windows console defaults to cp1252,
# which cannot encode them. Same guard as backend/main.py.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from sqlalchemy import create_engine, text

from backend.sample_db.build_app_tables import DB_URL
from backend.sample_db.identifiers import CAN_LENGTH, aadhaar_valid, can_valid

# Every extract that carries an aadhaar_number column.
AADHAAR_TABLES = [
    "urban_natham_chitta_owner", "nisd_transfer_new_owner",
    "nisd_transfer_old_owner", "nisd_transfer_return_owner",
    "nisd_transfer_igrs_owner", "urban_temp_subdivision_owner",
]

failures: list[str] = []


def check(ok: bool, message: str) -> None:
    print(f"  {'ok  ' if ok else 'FAIL'}  {message}")
    if not ok:
        failures.append(message)


def main() -> int:
    engine = create_engine(DB_URL, future=True)
    with engine.connect() as cx:
        print("aadhaar (layer 1 -- CSV-shaped tables)")
        for table in AADHAAR_TABLES:
            rows = cx.execute(text(
                f"SELECT aadhaar_number FROM {table} WHERE aadhaar_number IS NOT NULL")
            ).scalars().all()
            bad = [a for a in rows if not aadhaar_valid(a)]
            check(not bad, f"{table}: {len(rows)} numbers, {len(bad)} malformed"
                           + (f" e.g. {bad[:3]}" if bad else ""))

        print("\ncan (layer 2 -- applications)")
        apps = cx.execute(text(
            "SELECT application_number, submission_channel, can_number "
            "FROM applications")).all()
        check(bool(apps), f"{len(apps)} applications projected")

        no_channel = [a[0] for a in apps if a[1] not in CAN_LENGTH]
        check(not no_channel,
              f"every application has a known channel ({len(no_channel)} without)"
              + (f" e.g. {no_channel[:3]}" if no_channel else ""))

        for channel, want in CAN_LENGTH.items():
            group = [a for a in apps if a[1] == channel]
            bad = [(a[0], a[2]) for a in group if a[2] and not can_valid(a[2], channel)]
            check(not bad,
                  f"{channel}: {len(group)} applications, "
                  f"{sum(1 for a in group if a[2])} with a CAN, all {want} digits"
                  + (f" -- offenders {bad[:3]}" if bad else ""))

        missing = [a[0] for a in apps if not a[2]]
        print(f"  note  {len(missing)} applications carry no CAN")

        print("\naadhaar (layer 2 -- applicants)")
        last4 = cx.execute(text("SELECT aadhaar_last4 FROM applicants")).scalars().all()
        bad = [v for v in last4 if v is not None and not (len(v) == 4 and v.isdigit())]
        check(not bad, f"{len(last4)} applicants, "
                       f"{sum(1 for v in last4 if v)} with aadhaar_last4, "
                       f"{len(bad)} malformed")

    print()
    if failures:
        print(f"FAILED -- {len(failures)} check(s)")
        return 1
    print("all identifier checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
