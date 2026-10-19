"""Backward compatible schema upgrades for existing databases.

``create_all`` adds new tables but never alters existing ones, so columns
introduced after the first release are added here with plain
``ALTER TABLE ... ADD COLUMN`` statements (SQLite and PostgreSQL both accept
this form). Every addition has a default, so existing rows stay valid and
nothing is rewritten or deleted.
"""
from __future__ import annotations

import logging

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

log = logging.getLogger("lantern.migrations")

# (table, column, DDL type and default)
ADDED_COLUMNS: list[tuple[str, str, str]] = [
    ("organizations", "kind", "VARCHAR(20) DEFAULT 'insurer'"),
    ("users", "mfa_secret", "VARCHAR(64)"),
    ("users", "mfa_enabled", "BOOLEAN DEFAULT 0"),
    ("users", "password_changed_at", "DATETIME"),
    ("user_sessions", "user_agent", "VARCHAR(200)"),
    ("user_sessions", "mfa_passed", "BOOLEAN DEFAULT 0"),
    ("policies", "version", "INTEGER DEFAULT 1"),
    ("policies", "supersedes_policy_id", "VARCHAR(36)"),
    ("policies", "endorsements", "JSON DEFAULT '[]'"),
    ("gap_analyses", "job_id", "VARCHAR(36)"),
    ("gap_analyses", "mode", "VARCHAR(20)"),
    ("gap_analyses", "models", "JSON DEFAULT '{}'"),
    ("gap_analyses", "pipeline_version", "VARCHAR(20)"),
    ("customer_profiles", "bank_org_id", "VARCHAR(36)"),
    ("customer_profiles", "bank_customer_ref", "VARCHAR(64)"),
]


def upgrade(engine: Engine) -> list[str]:
    applied: list[str] = []
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    with engine.begin() as conn:
        for table, column, ddl in ADDED_COLUMNS:
            if table not in tables:
                continue
            existing = {c["name"] for c in insp.get_columns(table)}
            if column in existing:
                continue
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
            applied.append(f"{table}.{column}")
    if applied:
        log.info("Schema upgraded: %s", ", ".join(applied))
    return applied
