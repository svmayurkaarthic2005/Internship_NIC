"""One-time setup: make layer 1 (the CSV-shaped tables) announce its own changes.

Installs a trigger on every table `build_app_tables.py` reads from. Each fires
`pg_notify('app_tables_stale', <table name>)` after any INSERT/UPDATE/DELETE
-- a statement-level trigger, so one `UPDATE ... WHERE ...` in pgAdmin sends one
notification, not one per row. `watch_rebuild.py` listens for it and reruns the
projection. Dev-only: nothing in the running FastAPI app depends on this, and
production has no layer 1 to edit by hand (see CLAUDE.md).

Also installs a second trigger, on `urban_application_log` only: a BEFORE
UPDATE that stamps `last_updated_datetime = now()` on every edit. An
application that spans several parcels has one row per parcel there (CLAUDE.md:
1211 rows over 1139 ids), and `build_app_tables.py` picks ONE of those rows per
application -- the one with the latest `last_updated_datetime`, tied-broken by
`application_date`. Editing `application_status` (or any other column) on a
row in pgAdmin does NOT change that timestamp, so on a multi-parcel
application the edit can lose the tie to a sibling row and silently not show
up after rebuild -- not a timing problem, a wrong-row-picked problem. Stamping
the timestamp on every edit makes the row you just touched always win.

    python -m backend.sample_db.install_autorebuild_triggers   # idempotent
"""
from __future__ import annotations

import psycopg2

from backend.sample_db.dbconn import conn_params

# every FROM in build_app_tables.py, minus district_unicode/taluk (the layer-0
# masters, loaded by a separate script and not something pgAdmin edits by hand)
SOURCE_TABLES = (
    "urban_application_log",
    "application_workflow_action",
    "urban_parcel_register",
    "urban_natham_chitta_owner",
    "nisd_transfer_igrs_owner",
    "urban_temp_subdivision_parcel",
    "urban_temp_subdivision_owner",
)

FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION notify_app_tables_stale() RETURNS trigger AS $$
BEGIN
    PERFORM pg_notify('app_tables_stale', TG_TABLE_NAME);
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;
"""

# Only fires when the edit itself didn't already set a newer timestamp, so a
# script that intentionally backdates a row (the seed data, `generate_user_test_fixtures.py`,
# a future test fixture) is left alone -- this only rescues the common case of
# a plain `UPDATE ... SET application_status = ...` in pgAdmin that never
# touches the timestamp column at all.
TOUCH_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION touch_urban_application_log() RETURNS trigger AS $$
BEGIN
    IF NEW.last_updated_datetime IS NOT DISTINCT FROM OLD.last_updated_datetime THEN
        NEW.last_updated_datetime := now();
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


def main() -> None:
    conn = psycopg2.connect(**conn_params())
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(FUNCTION_SQL)
    for table in SOURCE_TABLES:
        trigger = f"trg_{table}_stale"
        cur.execute(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")
        cur.execute(f"""
            CREATE TRIGGER {trigger}
            AFTER INSERT OR UPDATE OR DELETE ON {table}
            FOR EACH STATEMENT EXECUTE FUNCTION notify_app_tables_stale()
        """)
        print(f"  trigger installed: {table}")

    cur.execute(TOUCH_FUNCTION_SQL)
    cur.execute("DROP TRIGGER IF EXISTS trg_urban_application_log_touch ON urban_application_log")
    cur.execute("""
        CREATE TRIGGER trg_urban_application_log_touch
        BEFORE UPDATE ON urban_application_log
        FOR EACH ROW EXECUTE FUNCTION touch_urban_application_log()
    """)
    print("  trigger installed: urban_application_log (auto-timestamps edits,"
          " so a multi-parcel application's edited row always wins the rebuild)")

    print(f"\n{len(SOURCE_TABLES)} tables now notify 'app_tables_stale' on change.")
    print("Run `python -m backend.sample_db.watch_rebuild` to act on it.")


if __name__ == "__main__":
    main()
