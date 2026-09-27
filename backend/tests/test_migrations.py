"""Migrations are repeatable, models match the schema, and a failed migration never destroys data."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext

from houseagent.config import Settings
from houseagent.db import session as dbs
from houseagent.db.models import Base


def _settings(tmp_path: Path) -> Settings:
    s = Settings(env="test", data_dir=tmp_path / "data", start_background=False)
    s.ensure_dirs()
    return s


def test_upgrade_downgrade_upgrade_on_empty_db(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    cfg = dbs.alembic_config(s)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    engine = dbs.init_engine(s)
    try:
        assert dbs.current_revision(engine) == dbs.head_revision(s)
        with engine.connect() as conn:
            diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
        assert diff == [], f"models and migrations differ: {diff}"
    finally:
        dbs.dispose_engine()


def test_failed_migration_keeps_data_and_blocks_writes(tmp_path: Path, monkeypatch) -> None:
    s = _settings(tmp_path)
    dbs.init_engine(s)
    dbs.run_migrations(s)
    with closing(sqlite3.connect(s.database_path)) as conn:
        conn.execute(
            "INSERT INTO workspaces (id, name, created_at, updated_at) "
            "VALUES (1, 'keep-me', '2026-01-01', '2026-01-01')"
        )
        conn.execute("UPDATE alembic_version SET version_num = 'base_simulated'")
        conn.commit()
    dbs.dispose_engine()

    def boom(*_a, **_k):  # a broken upgrade script
        raise RuntimeError("migration exploded")

    monkeypatch.setattr(dbs, "current_revision", lambda _e: "0000_old")
    monkeypatch.setattr(dbs.command, "upgrade", boom)
    dbs.set_read_only(None)
    dbs.init_engine(s)
    try:
        dbs.run_migrations(s)
        assert dbs.read_only_reason() == "migration_failed"
        assert any(p.name.startswith("houseagent-pre-migration") for p in s.backups_dir.iterdir())
        with closing(sqlite3.connect(s.database_path)) as conn:
            assert conn.execute("SELECT name FROM workspaces").fetchone()[0] == "keep-me"
    finally:
        dbs.set_read_only(None)
        dbs.dispose_engine()
