"""Backup, restore, export and data deletion (spec NFR-09 / 10.3 / 15.10).

Normal backups contain the business database only - never browser sessions.
"""

from __future__ import annotations

import csv
import json
import logging
import shutil
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from houseagent.config import Settings
from houseagent.db import session as dbs
from houseagent.db.models import (
    AIProviderConfig,
    Favorite,
    InvestmentAssessment,
    InvestmentInput,
    InvestmentOverride,
    InvestmentProfile,
    Listing,
    ListingEvent,
    ListingSnapshot,
    ListingSource,
    Note,
    SearchConditionVersion,
    SearchTask,
    Tag,
)
from houseagent.errors import AppError, ErrorCode

log = logging.getLogger(__name__)


def list_backups(settings: Settings) -> list[dict[str, Any]]:
    out = []
    for p in sorted(settings.backups_dir.glob("houseagent-*.db"), key=lambda x: x.stat().st_mtime, reverse=True):
        out.append(
            {"name": p.name, "size": p.stat().st_size, "created_at": datetime.fromtimestamp(p.stat().st_mtime, UTC)}
        )
    return out


def create_backup(settings: Settings, label: str = "manual") -> dict[str, Any]:
    with dbs.db_lock:
        path = dbs.backup_file_copy(settings.database_path, settings.backups_dir, label)
    return {"name": path.name, "size": path.stat().st_size}


def prune_backups(settings: Settings, keep: int) -> int:
    keep = max(int(keep), 1)
    files = sorted(settings.backups_dir.glob("houseagent-*.db"), key=lambda x: x.stat().st_mtime, reverse=True)
    auto = [f for f in files if "-daily-" in f.name or "-manual-" in f.name]
    removed = 0
    for f in auto[keep:]:
        f.unlink(missing_ok=True)
        removed += 1
    return removed


def daily_backup_due(settings: Settings) -> bool:
    newest = [b for b in list_backups(settings) if "-daily-" in b["name"]]
    return not newest or newest[0]["created_at"] < datetime.now(UTC) - timedelta(hours=23)


def _backup_path(settings: Settings, name: str) -> Path:
    p = (settings.backups_dir / name).resolve()
    if p.parent != settings.backups_dir.resolve() or not p.name.startswith("houseagent-") or not p.exists():
        raise AppError(ErrorCode.NOT_FOUND, {"backup": name})
    return p


def verify_backup(settings: Settings, name: str) -> dict[str, Any]:
    """Integrity check + schema version must not be newer than this program understands."""
    p = _backup_path(settings, name)
    with closing(sqlite3.connect(p)) as conn:
        ok = conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        try:
            rev = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        except sqlite3.DatabaseError:
            rev = None
    head = dbs.head_revision(settings)
    from alembic.script import ScriptDirectory

    known = {r.revision for r in ScriptDirectory.from_config(dbs.alembic_config(settings)).walk_revisions()}
    version = rev[0] if rev else None
    return {
        "name": name,
        "integrity_ok": ok,
        "schema_version": version,
        "head": head,
        "compatible": bool(ok and version in known),
    }


def restore_backup(settings: Settings, name: str) -> dict[str, Any]:
    info = verify_backup(settings, name)
    if not info["compatible"]:
        raise AppError(ErrorCode.VALIDATION_ERROR, info, message_key="error.backup_incompatible")
    src = _backup_path(settings, name)
    with dbs.db_lock:
        safety = dbs.backup_file_copy(settings.database_path, settings.backups_dir, "pre-restore")
        dbs.set_read_only("restoring")
        try:
            dbs.dispose_engine()
            for suffix in ("-wal", "-shm"):
                Path(str(settings.database_path) + suffix).unlink(missing_ok=True)
            shutil.copy2(src, settings.database_path)
            dbs.init_engine(settings)
            dbs.run_migrations(settings)  # restored older schema is upgraded to head
        finally:
            if dbs.read_only_reason() == "restoring":
                dbs.set_read_only(None)
    return {"restored": name, "safety_backup": safety.name}


# --------------------------------------------------------------------------------------------- export


def export_all(db: Session, settings: Settings) -> dict[str, Any]:
    """User-owned data as JSON plus a CSV listing index. Browser sessions are never exported."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = settings.exports_dir / f"export-{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    def rows(model: Any) -> list[dict[str, Any]]:
        cols = [c.name for c in model.__table__.columns]
        return [{c: getattr(o, c) for c in cols} for o in db.execute(select(model)).scalars()]

    data = {
        "exported_at": datetime.now(UTC).isoformat(),
        "search_tasks": rows(SearchTask),
        "search_condition_versions": rows(SearchConditionVersion),
        "listings": rows(Listing),
        "listing_sources": rows(ListingSource),
        "listing_snapshots": rows(ListingSnapshot),
        "listing_events": rows(ListingEvent),
        "favorites": rows(Favorite),
        "notes": rows(Note),
        "tags": rows(Tag),
        # FR-11: the user's standards, inputs, assessments and corrections
        "investment_profiles": rows(InvestmentProfile),
        "investment_inputs": rows(InvestmentInput),
        "investment_assessments": rows(InvestmentAssessment),
        "investment_overrides": rows(InvestmentOverride),
        # FR-12: non-sensitive AI settings only (a key reference and last four characters, never the key)
        "ai_provider_configs": rows(AIProviderConfig),
    }
    (out_dir / "houseagent-export.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
    )
    with (out_dir / "listing-sources.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "listing_id",
                "site_id",
                "external_listing_id",
                "source_url",
                "title",
                "price_yen",
                "area_m2",
                "layout",
                "address",
                "observation_status",
                "first_seen_at",
                "last_seen_at",
            ]
        )
        for s in db.execute(select(ListingSource)).scalars():
            w.writerow(
                [
                    s.listing_id,
                    s.site_id,
                    s.external_listing_id,
                    s.source_url,
                    s.title,
                    s.price_yen,
                    s.area_m2,
                    s.layout,
                    s.address,
                    s.observation_status,
                    s.first_seen_at,
                    s.last_seen_at,
                ]
            )
    return {"directory": str(out_dir), "files": ["houseagent-export.json", "listing-sources.csv"]}


def delete_all_user_data(settings: Settings) -> None:
    """Wipe the database (after a safety backup) and every browser profile. Program files are untouched."""
    with dbs.db_lock:
        dbs.backup_file_copy(settings.database_path, settings.backups_dir, "pre-delete")
        dbs.dispose_engine()
        for suffix in ("", "-wal", "-shm"):
            Path(str(settings.database_path) + suffix).unlink(missing_ok=True)
        shutil.rmtree(settings.profiles_dir, ignore_errors=True)
        settings.ensure_dirs()
        dbs.init_engine(settings)
        dbs.run_migrations(settings)
