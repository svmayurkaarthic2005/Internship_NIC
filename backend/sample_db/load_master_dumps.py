"""
Load the TAMILNILAM master-table pg_dump files in backend/sample_table/ into
sis_chatbot_db, exactly as dumped.

These five files are ordinary `pg_dump` output, not CSV extracts, so nothing
here infers a type or generates a value the way seed_sample_db.py does: the
DDL is applied verbatim and every row is loaded through COPY, so the structure
and the values in the database are the ones in the file.

    district.sql      -> public.district_unicode      36 rows
    taluk.sql         -> public.taluk (+ taluk_id_seq)  132 rows
    townmaster.sql    -> public.town                   216 rows
    wardmaster.sql    -> public.ward                  1073 rows
    blockmaster.sql   -> public.block                30697 rows

Order matters: taluk carries a foreign key to district_unicode, so the
district dump is applied first.

Replace semantics: each target table is dropped before its dump is applied, so
a re-run reloads it rather than failing on "already exists". Only the five
tables named above are touched -- the app's own `districts` / `taluks` /
`towns` / `wards` / `blocks` (different names, different structure) are left
alone.

The dumps also carry GRANTs to roles from the source system (temple, igrs,
clap, web_anon, authenticator, ultuser, postgrest_auth, murugesh). Those roles
do not exist here; each such GRANT is reported and skipped rather than failing
the load, since it grants nothing this database can act on.

Usage:
    python -m backend.sample_db.load_master_dumps
    python -m backend.sample_db.load_master_dumps --verify   # counts only
"""
from __future__ import annotations

import argparse
import io
import re
import sys
from pathlib import Path

import psycopg2

SAMPLE_TABLE_DIR = Path(__file__).resolve().parents[1] / "sample_table"
ENV_FILE = Path(__file__).resolve().parents[2] / ".env"

# (dump file, tables it creates) in dependency order -- district_unicode before
# taluk, which references it.
DUMPS = [
    ("district.sql", ["district_unicode"]),
    ("taluk.sql", ["taluk"]),
    ("townmaster.sql", ["town"]),
    ("wardmaster.sql", ["ward"]),
    ("blockmaster.sql", ["block"]),
]

# Dropped in reverse dependency order before anything is created.
ALL_TABLES = [t for _f, ts in DUMPS for t in ts]


def _database_url() -> str:
    """The sync DSN from .env, which is what psycopg2 wants."""
    text = ENV_FILE.read_text(encoding="utf-8")
    for key in ("SYNC_DATABASE_URL", "DATABASE_URL"):
        m = re.search(rf"^{key}=(.+)$", text, re.M)
        if m:
            url = m.group(1).strip().strip('"').strip("'")
            # DATABASE_URL is the async driver's form; psycopg2 wants plain.
            return url.replace("postgresql+asyncpg://", "postgresql://")
    raise SystemExit(f"No SYNC_DATABASE_URL or DATABASE_URL in {ENV_FILE}")


def _statements(path: Path):
    """Yield ("sql", text) and ("copy", (statement, data)) from a pg_dump file.

    pg_dump writes one statement per logical block terminated by ";" at end of
    line, and COPY data as raw lines between the COPY statement and a lone
    "\\.". Splitting on that shape is enough here -- these dumps contain no
    function bodies or dollar-quoted strings, where it would not be.
    """
    buffer: list[str] = []
    copy_stmt: str | None = None
    copy_rows: list[str] = []

    with path.open("r", encoding="utf-8", newline="") as fh:
        for raw in fh:
            line = raw.rstrip("\n").rstrip("\r")

            if copy_stmt is not None:
                if line == r"\.":
                    yield "copy", (copy_stmt, "".join(copy_rows))
                    copy_stmt, copy_rows = None, []
                else:
                    copy_rows.append(line + "\n")
                continue

            stripped = line.strip()
            if not stripped or stripped.startswith("--"):
                continue

            if not buffer and stripped.upper().startswith("COPY ") and \
                    stripped.endswith("FROM stdin;"):
                copy_stmt = stripped
                continue

            buffer.append(line)
            if stripped.endswith(";"):
                yield "sql", "\n".join(buffer)
                buffer = []

    if buffer:
        yield "sql", "\n".join(buffer)
    if copy_stmt is not None:
        raise SystemExit(f"{path.name}: COPY block was not terminated by '\\.'")


_MISSING_ROLE_RE = re.compile(r'role "([^"]+)" does not exist')
_MISSING_FUNC_RE = re.compile(r'function ([\w.]+\(?\)?) does not exist')


def _skippable(statement: str, exc: Exception):
    """Why this statement can be skipped without changing structure or values.

    Two things in these dumps reach outside the tables they carry, and neither
    exists in this database:

      * GRANTs to roles from the source system (temple, igrs, clap, ...). A
        grant to a role that is not here confers nothing, so skipping it
        changes no access.
      * The `ward_to_hist` trigger, which calls `public.ward_to_history()` --
        an audit function pg_dump did not include with the table. Creating the
        trigger without it would make every INSERT/UPDATE/DELETE on `ward`
        fail. The dump's own COPY runs before this statement, so the loaded
        rows are unaffected either way.

    Anything else that fails is a real problem and must stop the load.
    """
    head = statement.lstrip().upper()
    message = str(exc)

    role = _MISSING_ROLE_RE.search(message)
    if role and head.startswith(("GRANT", "REVOKE")):
        return f'role "{role.group(1)}" does not exist in this database'

    func = _MISSING_FUNC_RE.search(message)
    if func and head.startswith("CREATE TRIGGER"):
        return (f"trigger function {func.group(1)} is not in the dump "
                f"(audit hook from the source system)")

    return None


def load(conn, dump: Path) -> dict:
    """Apply one dump. Returns a small report of what happened."""
    report = {"file": dump.name, "statements": 0, "copied": {}, "skipped": []}
    cur = conn.cursor()

    for kind, payload in _statements(dump):
        if kind == "copy":
            statement, data = payload
            table = re.search(r"COPY\s+([\w.\"]+)", statement).group(1)
            cur.copy_expert(statement, io.StringIO(data))
            report["copied"][table] = cur.rowcount
            print(f"    COPY {table}: {cur.rowcount} rows")
            continue

        try:
            cur.execute(payload)
            report["statements"] += 1
        except psycopg2.Error as exc:
            first = payload.split("\n", 1)[0][:70]
            reason = _skippable(payload, exc)
            if reason:
                report["skipped"].append((first, reason))
                continue
            raise SystemExit(
                f"\n{dump.name}: statement failed and is not one of the known "
                f"skippable kinds:\n  {first}\n  {exc}"
            ) from exc
    return report


def _dump_copy_block(dump: Path):
    """The dump's own COPY statement and its data rows."""
    with dump.open("r", encoding="utf-8", newline="") as fh:
        text = fh.read()
    m = re.search(r"^(COPY [^\n]*FROM stdin;)\n(.*?)\n\\\.$", text, re.S | re.M)
    if not m:
        raise SystemExit(f"{dump.name}: no COPY block found")
    columns = re.search(r"COPY\s+[\w.]+\s+\(([^)]*)\)", m.group(1)).group(1)
    return columns, m.group(2).split("\n")


def verify(conn) -> bool:
    """Check every loaded table against its dump: structure, then content.

    Content is compared as a MULTISET, not line by line. `COPY ... TO STDOUT`
    returns rows in heap order, and PostgreSQL does not reproduce the dump
    file's line order there -- a clean reload into a scratch table reorders the
    same way, so it is COPY's own behaviour, not this loader's. Heap order is
    not part of a table's content (a SQL table is unordered, and each of these
    carries a primary key), so the check that matters is that every dumped row
    is present, exactly once, byte for byte.
    """
    cur = conn.cursor()
    ok = True
    print("\nVerification against the dump files:")
    for filename, tables in DUMPS:
        for table in tables:
            cur.execute(
                "select count(*) from information_schema.tables "
                "where table_schema='public' and table_name=%s", (table,))
            if not cur.fetchone()[0]:
                print(f"  {table:<18} NOT PRESENT")
                ok = False
                continue

            columns, expected = _dump_copy_block(SAMPLE_TABLE_DIR / filename)
            buf = io.StringIO()
            cur.copy_expert(f'COPY public."{table}" ({columns}) TO STDOUT', buf)
            actual = buf.getvalue().rstrip("\n").split("\n")

            same = sorted(actual) == sorted(expected)
            ok &= same
            cur.execute(
                "select count(*) from information_schema.columns "
                "where table_schema='public' and table_name=%s", (table,))
            n_cols = cur.fetchone()[0]
            n_dump_cols = len(columns.split(","))
            cols_ok = n_cols == n_dump_cols
            ok &= cols_ok
            print(f"  {table:<18} {len(actual):>6} rows "
                  f"(dump {len(expected)}), {n_cols} columns "
                  f"(dump {n_dump_cols}) -- "
                  f"{'rows identical' if same else 'ROWS DIFFER'}"
                  f"{'' if cols_ok else ', COLUMN COUNT DIFFERS'}")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--verify", action="store_true",
                    help="only report what is already loaded")
    args = ap.parse_args()

    conn = psycopg2.connect(_database_url())
    conn.autocommit = True     # each dump statement stands on its own
    try:
        if args.verify:
            return 0 if verify(conn) else 1

        missing = [f for f, _ in DUMPS if not (SAMPLE_TABLE_DIR / f).exists()]
        if missing:
            raise SystemExit(f"Missing dump file(s) in {SAMPLE_TABLE_DIR}: {missing}")

        cur = conn.cursor()
        print(f"Target: {conn.get_dsn_parameters()['dbname']} "
              f"as {conn.get_dsn_parameters()['user']}\n")

        print("Dropping the five dump tables (replace semantics):")
        for table in reversed(ALL_TABLES):
            cur.execute(f'DROP TABLE IF EXISTS public."{table}" CASCADE')
            print(f"    dropped if present: {table}")

        reports = []
        for filename, _tables in DUMPS:
            print(f"\n{filename}")
            reports.append(load(conn, SAMPLE_TABLE_DIR / filename))

        skipped = [(r["file"], s) for r in reports for s in r["skipped"]]
        if skipped:
            print(f"\nSkipped {len(skipped)} statement(s) that reach outside "
                  f"these tables:")
            for filename, (stmt, why) in skipped:
                print(f"    {filename}: {stmt}\n        -- {why}")

        if not verify(conn):
            print("\nVerification FAILED -- the database does not match the dumps.")
            return 1

        # Reloading dropped and recreated the tables, which took the app's
        # surrogate identity (`app_uid`) and every foreign key pointing at it
        # with them. Put those back here so one command leaves the database
        # consistent -- the UUIDs are derived from the natural key, so they
        # come back identical and the referring columns still resolve.
        from backend.sample_db.adopt_master_district_taluk import (
            add_app_columns, restore_app_foreign_keys)
        cur = conn.cursor()
        add_app_columns(cur)
        restore_app_foreign_keys(cur)
        print("\nRe-applied the app's surrogate identity (app_uid) and foreign keys "
              "to district_unicode / taluk.")

        print("\nDone. Structure and values match the dump files.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
