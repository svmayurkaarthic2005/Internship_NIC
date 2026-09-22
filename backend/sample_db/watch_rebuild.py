"""Watch layer 1 for pgAdmin edits and rebuild the app tables automatically.

Requires `python -m backend.sample_db.install_autorebuild_triggers` once, first.
Then leave this running in its own terminal while you edit data in pgAdmin:
every INSERT/UPDATE/DELETE on a table `build_app_tables.py` reads from wakes
it up, and it reruns the projection a couple of seconds after things go quiet
-- one rebuild per batch of edits, not one per statement.

Dev-only, same as `build_app_tables.py` itself: production has no layer 1 to
hand-edit (see CLAUDE.md), so nothing here belongs in the running FastAPI app.

    python -m backend.sample_db.watch_rebuild
    python -m backend.sample_db.watch_rebuild --debounce 5   # wait longer before rebuilding

A rebuild itself takes a few seconds (it re-derives all 21 app tables from
scratch, not just the row you touched), so "real time" here means: no command
to remember, answered correctly on your very next chat message after that.
Each rebuild prints how long your edit took to land, so you can see the actual
lag on your machine.
"""
from __future__ import annotations

import argparse
import select
import sys
import time

import psycopg2
import psycopg2.extensions

from backend.sample_db import build_app_tables
from backend.sample_db.dbconn import conn_params

CHANNEL = "app_tables_stale"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--debounce", type=float, default=1.0,
                     help="seconds of quiet after the last edit before rebuilding (default 1)")
    args = ap.parse_args()

    conn = psycopg2.connect(**conn_params())
    conn.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)
    cur = conn.cursor()
    cur.execute(f"LISTEN {CHANNEL}")
    print(f"Watching for changes on layer 1 (debounce {args.debounce}s)... Ctrl+C to stop.")
    sys.stdout.flush()

    pending = False
    deadline = None
    first_change_at = None
    while True:
        timeout = None if not pending else max(0.0, deadline - time.monotonic())
        if select.select([conn], [], [], timeout) == ([], [], []):
            if pending and time.monotonic() >= deadline:
                _rebuild(time.monotonic() - first_change_at)
                pending = False
            continue
        conn.poll()
        while conn.notifies:
            n = conn.notifies.pop(0)
            print(f"  changed: {n.payload}")
            if not pending:
                first_change_at = time.monotonic()
            pending = True
            deadline = time.monotonic() + args.debounce


def _rebuild(waited: float) -> None:
    print("Rebuilding app tables...")
    started = time.monotonic()
    try:
        build_app_tables.main()
    except Exception as exc:  # a bad edit (orphaned FK, etc.) should not kill the watcher
        print(f"  rebuild failed: {exc}")
    else:
        took = time.monotonic() - started
        print(f"Rebuild done in {took:.1f}s -- your edit is live {waited + took:.1f}s after you made it.\n")
    sys.stdout.flush()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
