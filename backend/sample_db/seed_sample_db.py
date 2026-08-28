"""
Load sis_chatbot_db from the extracts in backend/sample_table/.

Both the *structure* and the *content* come from the CSVs: the column list of
each table is taken from the CSV header (see schema_builder.py) and every row
is inserted verbatim. Nothing is invented, with one deliberate exception --
the `aadhaar_number` columns arrive empty in the extracts (the department
strips them before export), so a synthetic, checksum-valid number is generated
for each owner. Those are the only fabricated values in the database, and
`--no-aadhaar` turns even those off.

Values are preserved as they appear. A placeholder such as "-" is a real value
in these extracts and is kept for text columns; it only becomes NULL where the
target column is a date, timestamp or number that cannot hold it.

Run:  python backend/sample_db/seed_sample_db.py [--no-aadhaar]
"""
from __future__ import annotations

import argparse
import csv
import re
import sys

# Names in the extracts are Tamil, and a Windows console defaults to cp1252,
# which cannot encode them. Same guard as backend/main.py.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import psycopg2
from psycopg2.extras import execute_values

from identifiers import aadhaar_for
from schema_builder import (SAMPLE_TABLE_DIR, TABLE_NAMES, build_ddl,
                            read_header, table_specs)

# The signature/document columns hold base64 blobs far past the default cap.
csv.field_size_limit(1 << 30)

DB_NAME = "sis_chatbot_db"
CONN = dict(host="127.0.0.1", port=5432, user="postgres", password="Mayur@2005")

BATCH = 2000


# --- synthetic Aadhaar ----------------------------------------------------
# UIDAI numbers are 12 digits, never start with 0 or 1, and carry a Verhoeff
# check digit. Generating them properly means the seeded values behave like
# the real thing for any format or checksum validation downstream. The rules
# live in identifiers.py so the ORM projection derives the same numbers.

_IDENTITY_COLS = ("owner_name_english", "owner_name_tamil", "applicant_name")


def _identity(row: dict, table: str) -> str:
    for c in _IDENTITY_COLS:
        v = (row.get(c) or "").strip()
        if v and v != "-":
            return v
    # No name on the row -- fall back to whatever makes it unique.
    parts = [table] + [str(row.get(c) or "") for c in
                       ("application_id", "patta_number", "owner_no", "own_num")]
    return "|".join(parts)


# --- value handling -------------------------------------------------------

_NON_TEXT = re.compile(r"^(DATE|TIMESTAMP|TIMESTAMPTZ|NUMERIC|INTEGER)")


def clean(value: str | None, pg: str) -> str | None:
    """Preserve the extract's value; NULL only what the column cannot hold."""
    if value is None:
        return None
    v = value.strip()
    if v == "":
        return None
    if _NON_TEXT.match(pg):
        # "-", "NULL" and friends stand in for "no value" in numeric/date
        # columns; Postgres would reject them.
        if v in ("-", "--", "NULL", "null", "N/A", "NA", "#"):
            return None
    return v


def load_table(cur, csv_name: str, table: str, cols: list[tuple[str, str]],
               fill_aadhaar: bool) -> tuple[int, int]:
    path = SAMPLE_TABLE_DIR / csv_name
    names = [c for c, _ in cols]
    types = dict(cols)
    header = read_header(path)
    if header != names:
        raise ValueError(f"{table}: header drifted from the schema: {header} != {names}")

    aadhaar_cols = [c for c in names if c == "aadhaar_number"] if fill_aadhaar else []
    stmt = f'INSERT INTO {table} ({", ".join(names)}) VALUES %s'

    total = generated = 0
    batch: list[tuple] = []
    with path.open(encoding="utf-8", errors="replace", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            rec = {c: clean(row.get(c), types[c]) for c in names}
            for c in aadhaar_cols:
                if rec[c] is None or rec[c] == "-":
                    rec[c] = aadhaar_for(_identity(row, table))
                    generated += 1
            batch.append(tuple(rec[c] for c in names))
            if len(batch) >= BATCH:
                execute_values(cur, stmt, batch, page_size=BATCH)
                total += len(batch)
                batch.clear()
    if batch:
        execute_values(cur, stmt, batch, page_size=BATCH)
        total += len(batch)
    return total, generated


# --- driver ---------------------------------------------------------------

def create_database() -> None:
    conn = psycopg2.connect(dbname="postgres", **CONN)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (DB_NAME,))
        if cur.fetchone():
            print(f"database {DB_NAME} already exists")
        else:
            cur.execute(f'CREATE DATABASE "{DB_NAME}"')
            print(f"created database {DB_NAME}")
    conn.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--no-aadhaar", action="store_true",
                    help="leave aadhaar_number as it is in the extracts (empty)")
    args = ap.parse_args()

    missing = [f for f in TABLE_NAMES if not (SAMPLE_TABLE_DIR / f).exists()]
    if missing:
        sys.exit(f"missing extracts in {SAMPLE_TABLE_DIR}:\n  " + "\n  ".join(missing))

    create_database()
    specs = table_specs()

    conn = psycopg2.connect(dbname=DB_NAME, **CONN)
    conn.autocommit = False
    with conn.cursor() as cur:
        cur.execute(build_ddl())
        print(f"created {len(specs)} tables")

        rows = aadhaar = 0
        for csv_name, table in TABLE_NAMES.items():
            n, gen = load_table(cur, csv_name, table, specs[table],
                                fill_aadhaar=not args.no_aadhaar)
            note = f"  (+{gen} aadhaar)" if gen else ""
            print(f"  {table:34s} {n:7d} rows{note}")
            rows += n
            aadhaar += gen
    conn.commit()
    conn.close()
    print(f"\ndone -- {DB_NAME} loaded from the extracts: {rows} rows, "
          f"{aadhaar} synthetic aadhaar numbers")


if __name__ == "__main__":
    main()
