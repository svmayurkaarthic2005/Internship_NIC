"""
Drop the app's `districts` and `taluks` and run on the master tables instead.

The two pairs described the same places twice:

    districts (1 row)  vs  district_unicode (36)   -- same district 28
    taluks    (1 row)  vs  taluk            (132)  -- same taluk 28/01

Column by column, the app's tables held nothing the masters do not: only
`district_code` + `name`, and `taluk_code` + `name`. Their one real
contribution was the surrogate UUID identity that every foreign key and ~65
`District.id` / `Taluk.id` / `Taluk.district_id` / `Town.taluk_id` references
are built on. So rather than rewrite those onto composite natural keys, the
identity is added TO the masters and the app's two tables are dropped.

Columns added (nothing dumped is altered or removed):

    district_unicode   app_uid
    taluk              app_uid, district_uid -> district_unicode.app_uid

`app_uid` is md5 of the natural key, so it is DETERMINISTIC: reloading a dump
regenerates exactly the same UUIDs and the referring columns keep pointing at
the right rows. That is what makes load_master_dumps.py safe to re-run.

Towns, wards and blocks are deliberately left alone -- this is only the two
pairs that were compared.

Idempotent. Run from the project root:
    python -m backend.sample_db.adopt_master_district_taluk
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import psycopg2

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"

DISTRICT_UID = "md5('district:' || trim(district_code))::uuid"
TALUK_UID = "md5('taluk:' || trim(district_code) || ':' || trim(taluk_code))::uuid"

# (table, column, referenced table) for the app-side keys that must end up
# pointing at the masters.
APP_FOREIGN_KEYS = [
    ("towns", "taluk_id", "taluk"),
    ("officer_jurisdictions", "district_id", "district_unicode"),
    ("officer_jurisdictions", "taluk_id", "taluk"),
]


def database_url() -> str:
    text = ENV_FILE.read_text(encoding="utf-8")
    for key in ("SYNC_DATABASE_URL", "DATABASE_URL"):
        m = re.search(rf"^{key}=(.+)$", text, re.M)
        if m:
            return m.group(1).strip().strip('"').strip("'").replace(
                "postgresql+asyncpg://", "postgresql://")
    raise SystemExit(f"No SYNC_DATABASE_URL or DATABASE_URL in {ENV_FILE}")


def _exists(cur, table: str) -> bool:
    cur.execute("SELECT to_regclass(%s)", (f"public.{table}",))
    return cur.fetchone()[0] is not None


def add_app_columns(cur) -> None:
    """Give district_unicode and taluk the surrogate identity the app runs on.

    Idempotent, and called again after every dump reload -- reloading drops the
    table and takes these columns with it.
    """
    cur.execute("ALTER TABLE public.district_unicode ADD COLUMN IF NOT EXISTS app_uid uuid")
    cur.execute("ALTER TABLE public.taluk ADD COLUMN IF NOT EXISTS app_uid uuid")
    cur.execute("ALTER TABLE public.taluk ADD COLUMN IF NOT EXISTS district_uid uuid")

    cur.execute(f"UPDATE public.district_unicode SET app_uid = {DISTRICT_UID}")
    cur.execute(f"UPDATE public.taluk SET app_uid = {TALUK_UID}")
    # A taluk's district UUID is computed from the taluk's own district_code,
    # so it needs no join; it is left NULL only if that district is genuinely
    # absent from district_unicode.
    cur.execute(f"""
        UPDATE public.taluk t SET district_uid =
               md5('district:' || trim(t.district_code))::uuid
         WHERE EXISTS (SELECT 1 FROM public.district_unicode d
                        WHERE d.app_uid = md5('district:' || trim(t.district_code))::uuid)""")

    for table in ("district_unicode", "taluk"):
        cur.execute(f"ALTER TABLE public.{table} ALTER COLUMN app_uid SET NOT NULL")
        # app_uid is the ORM's primary key and the target of every app foreign
        # key, so the database has to enforce uniqueness too.
        cur.execute(f"""
            DO $$ BEGIN
              IF NOT EXISTS (SELECT 1 FROM pg_constraint
                              WHERE conname = '{table}_app_uid_key') THEN
                ALTER TABLE public.{table}
                  ADD CONSTRAINT {table}_app_uid_key UNIQUE (app_uid);
              END IF;
            END $$""")
    cur.execute("""
        DO $$ BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_constraint
                          WHERE conname = 'taluk_district_uid_fkey') THEN
            ALTER TABLE public.taluk ADD CONSTRAINT taluk_district_uid_fkey
              FOREIGN KEY (district_uid) REFERENCES public.district_unicode(app_uid);
          END IF;
        END $$""")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_taluk_district_uid "
                "ON public.taluk (district_uid)")


def drop_legacy_foreign_keys(cur) -> list:
    """Drop the FKs that still point at the app's own districts/taluks.

    They have to go BEFORE the values are remapped: the moment
    `towns.taluk_id` is set to a master UUID, a constraint still pointing at
    `taluks` rejects the row. Dropping `taluks`/`districts` later with CASCADE
    would remove these too, but by then the update has already been refused.

    Looked up rather than named literally, so a differently-named constraint
    from an older schema is still found.
    """
    cur.execute("""
        SELECT con.conname, cl.relname
          FROM pg_constraint con
          JOIN pg_class cl  ON cl.oid = con.conrelid
          JOIN pg_class ref ON ref.oid = con.confrelid
          JOIN pg_namespace n ON n.oid = cl.relnamespace
         WHERE con.contype = 'f' AND n.nspname = 'public'
           AND ref.relname IN ('districts', 'taluks')""")
    dropped = []
    for conname, table in cur.fetchall():
        cur.execute(f'ALTER TABLE public."{table}" DROP CONSTRAINT "{conname}"')
        dropped.append(f"{table}.{conname}")
    return dropped


def restore_app_foreign_keys(cur) -> None:
    """(Re-)point towns / officer_jurisdictions at the masters.

    Needed after a dump reload as well: reloading drops the master table, which
    takes the referring constraints with it. The column VALUES survive, because
    app_uid is deterministic -- only the constraints need putting back.
    """
    for table, column, target in APP_FOREIGN_KEYS:
        if not _exists(cur, table):
            continue
        name = f"{table}_{column}_master_fkey"
        cur.execute(f"""
            DO $$ BEGIN
              IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = '{name}') THEN
                ALTER TABLE public.{table} ADD CONSTRAINT {name}
                  FOREIGN KEY ({column}) REFERENCES public.{target}(app_uid);
              END IF;
            END $$""")


def migrate(conn) -> int:
    cur = conn.cursor()

    if not (_exists(cur, "district_unicode") and _exists(cur, "taluk")):
        raise SystemExit(
            "The master tables are not loaded. Run first:\n"
            "  python -m backend.sample_db.load_master_dumps")

    print("adding the surrogate identity to the master tables")
    add_app_columns(cur)
    cur.execute("SELECT count(*) FROM public.district_unicode WHERE app_uid IS NOT NULL")
    print(f"    district_unicode.app_uid set on {cur.fetchone()[0]} rows")
    cur.execute("SELECT count(*) FROM public.taluk WHERE district_uid IS NOT NULL")
    print(f"    taluk.district_uid resolved on {cur.fetchone()[0]} rows")

    if not (_exists(cur, "districts") or _exists(cur, "taluks")):
        print("\napp tables already dropped -- constraints refreshed, nothing else to do")
        restore_app_foreign_keys(cur)
        return 0

    # Translate every app-side reference from the old surrogate UUID to the
    # master's, resolved through the CODES -- so no row changes which district
    # or taluk it belongs to.
    print("\nremapping the app's references")
    cur.execute(f"""
        CREATE TEMP TABLE _dt_map ON COMMIT DROP AS
        SELECT d.id AS old_district, md5('district:' || trim(d.district_code))::uuid AS new_district,
               t.id AS old_taluk,
               md5('taluk:' || trim(d.district_code) || ':' || trim(t.taluk_code))::uuid AS new_taluk
          FROM taluks t JOIN districts d ON d.id = t.district_id""")

    cur.execute("""SELECT count(*) FROM _dt_map m
                    WHERE NOT EXISTS (SELECT 1 FROM public.taluk k WHERE k.app_uid = m.new_taluk)
                       OR NOT EXISTS (SELECT 1 FROM public.district_unicode d
                                       WHERE d.app_uid = m.new_district)""")
    missing = cur.fetchone()[0]
    if missing:
        raise SystemExit(
            f"{missing} app district/taluk row(s) have no matching master row -- "
            f"aborted, nothing changed.")

    for ref in drop_legacy_foreign_keys(cur):
        print(f"    dropped old constraint {ref}")

    for table, column, src, dst in [
        ("towns", "taluk_id", "old_taluk", "new_taluk"),
        ("officer_jurisdictions", "taluk_id", "old_taluk", "new_taluk"),
        ("officer_jurisdictions", "district_id", "old_district", "new_district"),
    ]:
        if not _exists(cur, table):
            continue
        cur.execute(f"""
            UPDATE public.{table} x SET {column} = m.{dst}
              FROM (SELECT DISTINCT {src}, {dst} FROM _dt_map) m
             WHERE x.{column} = m.{src}""")
        print(f"    {table}.{column}: {cur.rowcount} row(s)")

    # CASCADE clears the old foreign-key constraints on towns and
    # officer_jurisdictions; it does not touch their columns, which now hold
    # master UUIDs.
    print("\ndropping the app's duplicate tables")
    for table in ("taluks", "districts"):
        if _exists(cur, table):
            cur.execute(f"DROP TABLE public.{table} CASCADE")
            print(f"    dropped {table}")

    restore_app_foreign_keys(cur)
    print("    foreign keys re-pointed at the masters")
    return 0


def verify(conn) -> bool:
    cur = conn.cursor()
    ok = True
    print("\nverification")
    for table in ("districts", "taluks"):
        gone = not _exists(cur, table)
        ok &= gone
        print(f"    {table:<10} {'dropped' if gone else 'STILL PRESENT'}")
    cur.execute("""SELECT count(*) FROM public.towns x
                    LEFT JOIN public.taluk t ON t.app_uid = x.taluk_id
                    WHERE t.app_uid IS NULL""")
    orphan_towns = cur.fetchone()[0]
    ok &= orphan_towns == 0
    print(f"    towns whose taluk_id resolves in `taluk`: "
          f"{'all' if orphan_towns == 0 else f'{orphan_towns} DO NOT'}")
    cur.execute("""SELECT count(*) FROM public.officer_jurisdictions j
                    WHERE (j.district_id IS NOT NULL AND NOT EXISTS
                             (SELECT 1 FROM public.district_unicode d WHERE d.app_uid = j.district_id))
                       OR (j.taluk_id IS NOT NULL AND NOT EXISTS
                             (SELECT 1 FROM public.taluk t WHERE t.app_uid = j.taluk_id))""")
    bad = cur.fetchone()[0]
    ok &= bad == 0
    print(f"    officer_jurisdictions resolving to a master row: "
          f"{'all' if bad == 0 else f'{bad} DO NOT'}")
    return ok


def main() -> int:
    conn = psycopg2.connect(database_url())
    conn.autocommit = False
    try:
        rc = migrate(conn)
        if not verify(conn):
            conn.rollback()
            print("\nverification FAILED -- rolled back, nothing changed.")
            return 1
        conn.commit()
        print("\nDone. districts/taluks removed; the app now runs on "
              "district_unicode/taluk.")
        return rc
    except Exception:
        conn.rollback()
        print("\nrolled back -- nothing changed.")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
