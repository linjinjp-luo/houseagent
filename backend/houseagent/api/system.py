"""Health, settings, i18n, regions, dashboard, notifications, statistics, backups and export."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from houseagent import __version__, regions
from houseagent.config import get_settings
from houseagent.db.models import Notification, utcnow
from houseagent.db.session import get_db, read_only_reason
from houseagent.errors import AppError, ErrorCode
from houseagent.runtime import state as runtime
from houseagent.scheduler.queue import run_queue
from houseagent.services import backup, notifications, statistics
from houseagent.services import settings_service as app_settings

router = APIRouter()
log = logging.getLogger("houseagent.i18n")


@router.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "version": __version__, "read_only": read_only_reason(), "started_at": runtime.started_at}


@router.post("/system/shutdown")
def shutdown() -> dict[str, Any]:
    r"""Graceful exit (used by scripts\stop.ps1): new runs are refused, running ones stop at a safe point."""
    if runtime.request_shutdown is None:
        raise AppError(ErrorCode.CONFLICT, message_key="error.shutting_down")
    runtime.request_shutdown()
    return {"shutting_down": True}


@router.get("/system")
def system_info(db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    s = get_settings()
    return {
        "version": __version__,
        "data_dir": str(s.data_dir),
        "database": str(s.database_path),
        "profiles_dir": str(s.profiles_dir),
        "logs_dir": str(s.logs_dir),
        "backups_dir": str(s.backups_dir),
        "port": runtime.port,
        "read_only": read_only_reason(),
        "browser_engine": s.browser_engine,
        "queue": run_queue.status(),
        "env": s.env,
    }


# ---- settings -------------------------------------------------------------------------------------------


@router.get("/settings")
def get_settings_api(db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    return app_settings.get_all(db)


@router.patch("/settings")
def patch_settings(changes: dict[str, Any], db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    if "language" in changes and changes["language"] not in app_settings.LANGUAGES:
        raise AppError(ErrorCode.VALIDATION_ERROR, {"field": "language"})
    if "backup_keep" in changes:
        try:
            if int(changes["backup_keep"]) < 1:
                raise ValueError
        except (TypeError, ValueError) as exc:
            raise AppError(ErrorCode.VALIDATION_ERROR, {"field": "backup_keep"}) from exc
    if "timezone" in changes:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(str(changes["timezone"]))
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise AppError(ErrorCode.VALIDATION_ERROR, {"field": "timezone"}) from exc
    if "log_level" in changes:
        logging.getLogger().setLevel(str(changes["log_level"]).upper())
    return app_settings.update(db, changes)


class MissingKeys(BaseModel):
    language: str
    keys: list[str]


@router.post("/i18n/missing")
def report_missing(body: MissingKeys) -> dict[str, Any]:
    for k in body.keys[:200]:
        log.warning("missing translation key [%s]: %s", body.language, k[:200])
    return {"recorded": len(body.keys[:200])}


@router.get("/regions")
def get_regions() -> dict[str, Any]:
    return regions.as_dict()


# ---- dashboard / notifications / statistics -------------------------------------------------------------


@router.get("/dashboard")
def get_dashboard(db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    return statistics.dashboard(db, app_settings.get(db, "timezone"))


@router.get("/notifications")
def list_notifications(
    include_resolved: bool = False, db: Session = Depends(get_db, scope="function")
) -> list[dict[str, Any]]:
    q = select(Notification).order_by(Notification.created_at.desc()).limit(200)
    if not include_resolved:
        q = q.where(Notification.resolved_at.is_(None))
    return [notifications.to_dict(n) for n in db.execute(q).scalars()]


@router.post("/notifications/{nid}/resolve")
def resolve_notification(nid: int, db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    n = db.get(Notification, nid)
    if n is None:
        raise AppError(ErrorCode.NOT_FOUND)
    n.resolved_at = utcnow()
    return notifications.to_dict(n)


@router.get("/statistics")
def get_statistics(
    date_from: date | None = None,
    date_to: date | None = None,
    task_id: int | None = None,
    site_id: str | None = None,
    prefecture: str | None = None,
    city: str | None = None,
    property_type: str | None = None,
    deal_type: str = "buy",
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    if deal_type not in ("buy", "rent"):
        raise AppError(ErrorCode.VALIDATION_ERROR, {"field": "deal_type"})
    if date_from and date_to and date_from > date_to:
        raise AppError(ErrorCode.VALIDATION_ERROR, {"field": "date_from"})
    return statistics.compute(
        db,
        date_from=date_from,
        date_to=date_to,
        task_id=task_id,
        site_id=site_id,
        prefecture=prefecture,
        city=city,
        property_type=property_type,
        deal_type=deal_type,
        tz_name=app_settings.get(db, "timezone"),
    )


# ---- backups / export / delete --------------------------------------------------------------------------


@router.get("/backups")
def get_backups() -> list[dict[str, Any]]:
    return backup.list_backups(get_settings())


@router.post("/backups")
def post_backup(db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    s = get_settings()
    result = backup.create_backup(s, "manual")
    backup.prune_backups(s, int(app_settings.get(db, "backup_keep") or 7))
    return result


@router.get("/backups/{name}/verify")
def verify(name: str) -> dict[str, Any]:
    return backup.verify_backup(get_settings(), name)


@router.post("/backups/{name}/restore")
def restore(name: str) -> dict[str, Any]:
    if run_queue.running_ids():
        raise AppError(ErrorCode.CONFLICT, message_key="error.runs_in_progress")
    return backup.restore_backup(get_settings(), name)


@router.post("/export")
def export(db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    return backup.export_all(db, get_settings())


class DeleteAllIn(BaseModel):
    confirm: str


@router.post("/data/delete-all")
def delete_all(body: DeleteAllIn) -> dict[str, Any]:
    if body.confirm != "DELETE":
        raise AppError(ErrorCode.VALIDATION_ERROR, {"field": "confirm"})
    if run_queue.running_ids():
        raise AppError(ErrorCode.CONFLICT, message_key="error.runs_in_progress")
    backup.delete_all_user_data(get_settings())
    from houseagent.db.session import session_scope
    from houseagent.services.seed import seed

    with session_scope() as db:
        seed(db, include_mock=get_settings().enable_mock_site)
    return {"deleted": True}
