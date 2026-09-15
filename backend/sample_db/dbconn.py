"""
PostgreSQL connection parameters for the sample_db scripts.

These scripts run standalone (``python backend/sample_db/seed_sample_db.py``),
outside the FastAPI app, so they cannot import ``backend.config``. They still
must not carry a password in source: the credentials come from
``SYNC_DATABASE_URL`` in the environment, or from the ``.env`` at the repo root
when the variable is not exported.
"""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import unquote, urlparse

_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
_VARS = ("SYNC_DATABASE_URL", "DATABASE_URL")


def _from_env_file(name: str) -> str | None:
    if not _ENV_FILE.exists():
        return None
    for line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith(f"{name}=") and not line.startswith("#"):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def database_url() -> str:
    for name in _VARS:
        value = os.environ.get(name) or _from_env_file(name)
        if value:
            return value
    raise SystemExit(
        f"no database URL: set SYNC_DATABASE_URL in the environment or in "
        f"{_ENV_FILE} (see .env.example)")


def conn_params(database: str | None = None) -> dict:
    """psycopg2/asyncpg keyword arguments taken from the configured URL.

    ``database`` overrides the database name in the URL -- seeding connects to
    ``postgres`` first to issue CREATE DATABASE.
    """
    # postgresql+asyncpg://user:pass@host:port/db -> drop the driver suffix
    url = urlparse(database_url().replace("+asyncpg", "").replace("+psycopg2", ""))
    return dict(
        host=url.hostname or "127.0.0.1",
        port=url.port or 5432,
        user=unquote(url.username or "postgres"),
        password=unquote(url.password or ""),
        database=database or (url.path.lstrip("/") or "postgres"),
    )
