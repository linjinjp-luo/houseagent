"""Workspace settings (language, timezone, backups, AI, startup behaviour ...)."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from houseagent.db.models import AppSetting

DEFAULTS: dict[str, Any] = {
    "language": "ja",
    "timezone": "Asia/Tokyo",
    "date_format": "locale",
    "area_unit": "m2",
    "startup_open_browser": True,
    "tray_enabled": True,
    "close_behavior": "tray",  # tray | exit
    "allowed_window_start": "06:00",
    "allowed_window_end": "23:00",
    "network_retry_enabled": True,
    "backup_auto_daily": True,
    "backup_keep": 7,
    "notifications_in_app": True,
    "ai_enabled": False,
    "ai_provider": "anthropic",
    "ai_model": "claude-opus-5",
    "ai_allowed_fields": ["property_type", "city", "price_yen", "area_m2", "layout", "built_year", "events"],
    "ai_send_notes": False,
    "log_level": "INFO",
    "data_retention_days": 0,  # 0 = keep forever
    "onboarding_done": False,
}

LANGUAGES = ("ja", "zh", "en")


def get_all(db: Session, workspace_id: int = 1) -> dict[str, Any]:
    values = dict(DEFAULTS)
    for row in db.query(AppSetting).filter_by(workspace_id=workspace_id):
        values[row.key] = row.value
    return values


def get(db: Session, key: str, workspace_id: int = 1) -> Any:
    row = db.get(AppSetting, (workspace_id, key))
    return row.value if row else DEFAULTS.get(key)


def update(db: Session, changes: dict[str, Any], workspace_id: int = 1) -> dict[str, Any]:
    for key, value in changes.items():
        if key not in DEFAULTS:
            continue
        row = db.get(AppSetting, (workspace_id, key))
        if row is None:
            db.add(AppSetting(workspace_id=workspace_id, key=key, value=value))
        else:
            row.value = value
    db.flush()
    return get_all(db, workspace_id)
