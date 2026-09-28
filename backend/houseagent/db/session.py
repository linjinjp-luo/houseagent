"""Engine / session management, migrations on startup and the read-only guard (spec 15.7)."""

from __future__ import annotations

import logging
import shutil
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from houseagent.config import Settings, program_dir

log = logging.getLogger(__name__)

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None
# Set when a migration failed or the database is being restored: all writes are refused.
_read_only_reason: str | None = None
# Serialises restore against normal use.
db_lock = threading.RLock()


def _on_connect(dbapi_conn: sqlite3.Connection, _record: Any) -> None:
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA journal_mode=WAL")  # crash-safe, readers don't block the writer
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA busy_timeout=10000")
    cur.close()


def init_engine(settings: Settings) -> Engine:
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    settings.database_dir.mkdir(parents=True, exist_ok=True)
    engine = create_engine(settings.database_url, connect_args={"check_same_thread": False, "timeout": 15})
    event.listen(engine, "connect", _on_connect)
    _engine = engine
    _SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    return engine


def get_engine() -> Engine:
    assert _engine is not None, "engine not initialised"
    return _engine


def dispose_engine() -> None:
    global _engine
    if _engine is not None:
        _engine.dispose()
        _engine = None


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope: commit on success, roll back everything on failure."""
    assert _SessionLocal is not None, "engine not initialised"
    session = _SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency."""
    with session_scope() as s:
        yield s


def read_only_reason() -> str | None:
    return _read_only_reason


def set_read_only(reason: str | None) -> None:
    global _read_only_reason
    _read_only_reason = reason


# --------------------------------------------------------------------------------------------- migrations


def alembic_config(settings: Settings) -> Config:
    base = program_dir()
    cfg = Config()
    cfg.set_main_option("script_location", str(base / "alembic"))
    cfg.set_main_option("sqlalchemy.url", settings.database_url)
    return cfg


def current_revision(engine: Engine) -> str | None:
    with engine.connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()


def head_revision(settings: Settings) -> str | None:
    return ScriptDirectory.from_config(alembic_config(settings)).get_current_head()


def backup_file_copy(src: Path, dest_dir: Path, label: str) -> Path:
    """Consistent copy of a live SQLite database using the online backup API."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    dest = dest_dir / f"houseagent-{label}-{stamp}.db"
    # closing(): sqlite3's own context manager only commits, it does not close (Windows keeps the file locked).
    with closing(sqlite3.connect(src)) as s, closing(sqlite3.connect(dest)) as d:
        s.backup(d)
    return dest


def run_migrations(settings: Settings) -> None:
    """Upgrade to head. Backs up first; on failure restores the original and enters read-only mode."""
    engine = get_engine()
    cfg = alembic_config(settings)
    head = head_revision(settings)
    current = current_revision(engine)
    if current == head:
        return
    db_path = settings.database_path
    backup: Path | None = None
    has_data = db_path.exists() and db_path.stat().st_size > 0 and current is not None
    if has_data:
        backup = backup_file_copy(db_path, settings.backups_dir, "pre-migration")
        log.info("database backed up before migration to %s", backup.name)
    try:
        with engine.connect() as conn:
            # Alembic rebuilds SQLite tables (copy -> DROP -> rename) for some changes. With foreign keys enforced,
            # dropping a table that other rows reference fails, so enforcement is off during the upgrade (the
            # pragma only takes effect outside a transaction) and integrity is verified before committing.
            conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
            conn.commit()
            try:
                with conn.begin():
                    cfg.attributes["connection"] = conn
                    command.upgrade(cfg, "head")
                    broken = conn.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
                    if broken:
                        raise RuntimeError(f"foreign key check failed after migration: {broken[:5]}")
            finally:
                conn.exec_driver_sql("PRAGMA foreign_keys=ON")
                conn.commit()
        log.info("database migrated %s -> %s", current, head)
    except Exception:
        log.exception("database migration failed; keeping original database and blocking writes")
        if backup is not None:
            dispose_engine()
            shutil.copy2(backup, db_path)
            init_engine(settings)
        set_read_only("migration_failed")
