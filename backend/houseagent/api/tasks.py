"""Search tasks and run logs."""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from houseagent.db.models import RunLogEntry, RunResult, SearchTask, TaskRun
from houseagent.db.session import get_db
from houseagent.errors import AppError, ErrorCode
from houseagent.scheduler.queue import run_queue
from houseagent.scheduler.schedule import next_run_after
from houseagent.services import settings_service as app_settings
from houseagent.services import tasks as svc
from houseagent.services.conditions import SearchConditions

router = APIRouter()


@router.get("/search-tasks")
def list_tasks(include_deleted: bool = False, db: Session = Depends(get_db, scope="function")) -> list[dict[str, Any]]:
    q = select(SearchTask).order_by(SearchTask.id)
    if not include_deleted:
        q = q.where(SearchTask.deleted_at.is_(None))
    return [svc.task_to_dict(db, t) for t in db.execute(q).scalars()]


@router.post("/search-tasks", status_code=201)
def create_task(body: svc.TaskIn, db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    task, validation = svc.create_task(db, body)
    return {**svc.task_to_dict(db, task), "validation": validation}


class ValidateIn(BaseModel):
    conditions: SearchConditions
    site_ids: list[str]


@router.post("/search-tasks/validate")
def validate(body: ValidateIn) -> dict[str, Any]:
    """Tell the user, before saving, which conditions are sent, filtered locally, or unsupported per site."""
    return svc.validate_for_sites(body.conditions, body.site_ids)


class SchedulePreviewIn(BaseModel):
    schedule_type: str
    schedule: dict[str, Any] = {}
    timezone: str = "Asia/Tokyo"
    count: int = 3


@router.post("/search-tasks/schedule-preview")
def schedule_preview(body: SchedulePreviewIn) -> list[datetime]:
    out: list[datetime] = []
    after = datetime.now(ZoneInfo(body.timezone))
    for _ in range(max(1, min(body.count, 10))):
        nxt = next_run_after(body.schedule_type, body.schedule, body.timezone, after)
        if nxt is None:
            break
        out.append(nxt)
        after = nxt
    return out


@router.get("/search-tasks/{task_id}")
def get_task(task_id: int, db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    task = svc.get_task(db, task_id)
    return svc.task_to_dict(db, task, include_versions=True)


@router.patch("/search-tasks/{task_id}")
def patch_task(task_id: int, body: svc.TaskPatch, db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    task, validation = svc.update_task(db, svc.get_task(db, task_id), body)
    return {**svc.task_to_dict(db, task, include_versions=True), "validation": validation}


@router.post("/search-tasks/{task_id}/copy", status_code=201)
def copy_task(task_id: int, db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    return svc.task_to_dict(db, svc.copy_task(db, svc.get_task(db, task_id)))


@router.post("/search-tasks/{task_id}/pause")
def pause_task(task_id: int, db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    task = svc.get_task(db, task_id)
    svc.pause_task(db, task)
    return svc.task_to_dict(db, task)


@router.post("/search-tasks/{task_id}/resume")
def resume_task(task_id: int, db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    task = svc.get_task(db, task_id)
    svc.resume_task(db, task)
    return svc.task_to_dict(db, task)


@router.delete("/search-tasks/{task_id}")
def delete_task(task_id: int, db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    svc.delete_task(db, svc.get_task(db, task_id))
    return {"deleted": True, "soft": True}


@router.post("/search-tasks/{task_id}/run")
def run_task(task_id: int, db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    task = svc.get_task(db, task_id)
    closed: list[str] = []
    skipped: dict[str, str] = {}
    runs = run_queue.enqueue_task(db, task, "manual", closed, skipped)
    # skipped: sites of a multi-site task that could not be searched automatically, with the reason
    return {"queued": [run_to_dict(r) for r in runs], "closed_login_windows": closed, "skipped": skipped}


# ---- runs -----------------------------------------------------------------------------------------------


def run_to_dict(r: TaskRun, task_name: str | None = None) -> dict[str, Any]:
    return {
        "id": r.id,
        "run_no": r.run_no,
        "task_id": r.task_id,
        "task_name": task_name,
        "site_id": r.site_id,
        "account_id": r.account_id,
        "account_alias": r.account_alias,
        "condition_version": r.condition_version,
        "trigger_type": r.trigger_type,
        "status": r.status,
        "queued_at": r.queued_at,
        "not_before": r.not_before,
        "started_at": r.started_at,
        "finished_at": r.finished_at,
        "attempt": r.attempt,
        "retry_count": r.retry_count,
        "error_code": r.error_code,
        "correlation_id": r.correlation_id,
        "cancel_requested": r.cancel_requested,
        "cancel_reason": r.cancel_reason,
        "result_count": r.result_count,
        "new_count": r.new_count,
        "changed_count": r.changed_count,
        "skipped_count": r.skipped_count,
        "not_found_count": r.not_found_count,
        "pages": r.pages,
        "unsupported_conditions": r.unsupported_conditions,
        "progress_stage": r.progress_stage,
        "progress_pct": r.progress_pct,
        "expected_total": r.expected_total,
        "wait_reason": r.wait_reason,
        "error_detail": r.error_detail,
    }


@router.get("/search-runs")
def list_runs(
    task_id: int | None = None,
    site_id: str | None = None,
    account_id: int | None = None,
    status: str | None = None,
    trigger_type: str | None = None,
    level: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    page: int = 1,
    page_size: int = 50,
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    tz = ZoneInfo(app_settings.get(db, "timezone"))
    q = select(TaskRun)
    if task_id:
        q = q.where(TaskRun.task_id == task_id)
    if site_id:
        q = q.where(TaskRun.site_id == site_id)
    if account_id:
        q = q.where(TaskRun.account_id == account_id)
    if status:
        q = q.where(TaskRun.status.in_(status.split(",")))
    if trigger_type:
        q = q.where(TaskRun.trigger_type == trigger_type)
    if level:
        q = q.where(TaskRun.id.in_(select(RunLogEntry.run_id).where(RunLogEntry.level == level)))
    if date_from:
        q = q.where(TaskRun.queued_at >= datetime.combine(date_from, time.min, tz))
    if date_to:
        q = q.where(TaskRun.queued_at < datetime.combine(date_to, time.max, tz))
    total = db.execute(select(func.count()).select_from(q.subquery())).scalar_one()
    page_size = max(1, min(page_size, 200))
    rows = db.execute(q.order_by(TaskRun.id.desc()).offset((max(page, 1) - 1) * page_size).limit(page_size)).scalars()
    names = {t.id: t.name for t in db.execute(select(SearchTask)).scalars()}
    return {"total": total, "items": [run_to_dict(r, names.get(r.task_id)) for r in rows]}


@router.get("/search-runs/{run_id}")
def get_run(run_id: int, db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    r = db.get(TaskRun, run_id)
    if r is None:
        raise AppError(ErrorCode.NOT_FOUND)
    task = db.get(SearchTask, r.task_id)
    version = (
        next((v.condition_json for v in task.versions if v.version == r.condition_version), None) if task else None
    )
    logs = db.execute(select(RunLogEntry).where(RunLogEntry.run_id == run_id).order_by(RunLogEntry.id)).scalars()
    results = db.execute(select(RunResult).where(RunResult.run_id == run_id).order_by(RunResult.rank)).scalars().all()
    return {
        **run_to_dict(r, task.name if task else None),
        "conditions": version,
        "logs": [
            {"level": e.level, "message_key": e.message_key, "params": e.params, "created_at": e.created_at}
            for e in logs
        ],
        "results": [
            {"source_id": x.source_id, "rank": x.rank, "is_new": x.is_new, "is_changed": x.is_changed} for x in results
        ],
    }


@router.post("/search-runs/{run_id}/cancel")
def cancel_run(run_id: int, db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    r = db.get(TaskRun, run_id)
    if r is None:
        raise AppError(ErrorCode.NOT_FOUND)
    run_queue.cancel(db, r)
    return run_to_dict(r)


@router.post("/search-runs/{run_id}/rerun")
def rerun(run_id: int, db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    """User-confirmed re-execution of an interrupted / failed / paused run (uses the task's current conditions)."""
    r = db.get(TaskRun, run_id)
    if r is None:
        raise AppError(ErrorCode.NOT_FOUND)
    task = svc.get_task(db, r.task_id)
    runs = run_queue.enqueue_task(db, task, "manual")
    return {"queued": [run_to_dict(x) for x in runs]}


# ---- browser-assisted import ----------------------------------------------------------------------------


class AssistedStartIn(BaseModel):
    site_id: str


@router.post("/search-tasks/{task_id}/assisted")
def start_assisted(
    task_id: int, body: AssistedStartIn, db: Session = Depends(get_db, scope="function")
) -> dict[str, Any]:
    """Open a visible browser for the user to browse the site (and complete any verification themselves)."""
    from houseagent.services import assisted

    return assisted.start(db, svc.get_task(db, task_id), body.site_id).to_dict()


@router.get("/assisted/{session_id}")
def get_assisted(session_id: str) -> dict[str, Any]:
    from houseagent.services import assisted

    return assisted.get(session_id).to_dict()


@router.post("/assisted/{session_id}/import")
def import_assisted_page(session_id: str, db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    """Read the result list the user has open right now."""
    from houseagent.services import assisted

    return assisted.import_page(db, session_id)


@router.post("/assisted/{session_id}/close")
def close_assisted(session_id: str) -> dict[str, Any]:
    from houseagent.services import assisted

    assisted.close(session_id)
    return {"closed": True}
