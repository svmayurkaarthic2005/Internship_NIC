"""
Create tables from the CSVs in backend/sample_db_new/ and load them into
sis_chatbot_db, alongside (not instead of) the existing sample_table tables.

sample_db_new holds a newer batch of extracts: 16 files that share a name with
one already loaded from backend/sample_table/, plus 9 new tables this database
has never seen (chitta_temp_old_owner, correction_modification_flow,
f_line_service_*, file_attachment, sd_attachment, tslr_sketch_urban,
uareg_modification, uchitta_natham_modification). Several of the shared names
carry far fewer rows here than the live table does (application_workflow: 704
vs 288087) -- a smaller snapshot, not a superset -- so this script does NOT
touch the existing `*_demo` tables. Table names here drop the `_demo` suffix
(`appl_log_urban_demo.csv` -> `appl_log_urban`), which is what keeps every one
of them from colliding with the table already sitting under the `_demo` name.

The five `.sql` files here (district/taluk/town/ward/block master dumps) are
byte-identical copies of the ones already loaded live via load_master_dumps.py
-- re-running them would try to CREATE TABLE district_unicode etc. a second
time and collide with the masters the whole app depends on, for no new data.
They are deliberately skipped.

Loads into the `SISchatbot` database (a separate, empty database on the same
server) -- not `sis_chatbot_db`, which is what `.env` points the running app
at. Keeping this data in its own database is the same non-interference reason
the table names drop `_demo`: nothing here is meant to touch what the app
reads.

Run from the project root:
    python -m backend.sample_db_new.load_sample_db_new
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import psycopg2
from psycopg2.extras import execute_values

from backend.sample_db.dbconn import conn_params
from backend.sample_db.schema_builder import pg_type, read_header

# The signature/attachment columns hold base64 blobs far past the default cap.
csv.field_size_limit(1 << 30)

HERE = Path(__file__).resolve().parent
BATCH = 2000
TARGET_DB = "SISchatbot"

# Columns that hold large base64/free-text payloads that schema_builder's own
# type rules don't know about yet (they were written for the sample_table
# extracts, which never carried a base64 attachment or these column names).
# Measured against every row of every file here -- a preflight pass comparing
# each column's actual max value length against the VARCHAR size schema_builder
# would have assigned it -- rather than guessed:
#   sketch_file 334,900 chars, attachmentvalue 1,224,752, file 245,860 --
#   base64 payloads, need TEXT.
#   owner_name/relative_name 125 (Tamil script), dro_remarks 179,
#   zdt_remarks 186, attachmentname 111, appl_address_cur 116,
#   appl_address_perm 68, approval_file_no 77 -- all overflow VARCHAR(60).
EXTRA_TEXT_COLS = {
    "sketch_file", "sketch_file_dis", "attachmentvalue", "file",
    "owner_name", "relative_name", "attachmentname", "approval_file_no",
}


def _pg_type(column: str) -> str:
    if column in EXTRA_TEXT_COLS:
        return "TEXT"
    low = column.lower()
    # Free-text fields grow unpredictably across a data pull; any column
    # whose name says "remarks" or "address" gets TEXT rather than betting a
    # fixed VARCHAR size on this one sample being the longest it ever is.
    if "remark" in low or "address" in low:
        return "TEXT"
    return pg_type(column)


def _table_name(csv_path: Path) -> str:
    """appl_log_urban_demo.csv -> appl_log_urban -- the _demo suffix dropped
    so nothing here collides with the table already loaded under that name."""
    stem = csv_path.stem
    return stem[:-5] if stem.endswith("_demo") else stem


def _clean(value: str | None, pg: str) -> str | None:
    if value is None:
        return None
    v = value.strip()
    if v == "":
        return None
    if pg in ("NUMERIC(14,2)", "INTEGER", "DATE", "TIMESTAMPTZ"):
        if v in ("-", "--", "NULL", "null", "N/A", "NA", "#"):
            return None
    return v


def load_csv(cur, path: Path) -> tuple[str, int]:
    table = _table_name(path)
    header = read_header(path)
    cols = [(c, _pg_type(c)) for c in header]
    types = dict(cols)

    ddl = [f"DROP TABLE IF EXISTS {table} CASCADE;",
          f"CREATE TABLE {table} (", "    row_id BIGSERIAL PRIMARY KEY"]
    ddl[-1] += ",\n" + ",\n".join(f"    {c} {t}" for c, t in cols)
    ddl.append(");")
    cur.execute("\n".join(ddl))

    names = [c for c, _ in cols]
    stmt = f'INSERT INTO {table} ({", ".join(names)}) VALUES %s'
    total = 0
    batch: list[tuple] = []
    with path.open(encoding="utf-8", errors="replace", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            rec = tuple(_clean(row.get(c), types[c]) for c in names)
            batch.append(rec)
            if len(batch) >= BATCH:
                execute_values(cur, stmt, batch, page_size=BATCH)
                total += len(batch)
                batch.clear()
    if batch:
        execute_values(cur, stmt, batch, page_size=BATCH)
        total += len(batch)
    return table, total


def main() -> None:
    csv_files = sorted(HERE.glob("*.csv"))
    if not csv_files:
        sys.exit(f"no CSV files found in {HERE}")

    skipped_sql = sorted(p.name for p in HERE.glob("*.sql"))
    if skipped_sql:
        print("skipping master dumps (already loaded live, see module docstring):")
        for name in skipped_sql:
            print(f"  - {name}")
        print()

    conn = psycopg2.connect(**conn_params(TARGET_DB))
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            for path in csv_files:
                table, n = load_csv(cur, path)
                print(f"  {table:52s} {n:7d} rows  (from {path.name})")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    print(f"\ndone -- {len(csv_files)} tables created and loaded into {TARGET_DB}")


if __name__ == "__main__":
    main()
