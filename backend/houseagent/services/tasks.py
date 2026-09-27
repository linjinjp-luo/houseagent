"""Search task management: create / edit (versioned conditions) / copy / pause / resume / soft delete."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from houseagent.adapters.registry import get_adapter
from houseagent.db.models import (
    SearchConditionVersion,
    SearchTask,
    SearchTaskSite,
    SiteAccount,
    TaskRun,
    utcnow,
)
from houseagent.errors import AppError, ErrorCode
from houseagent.scheduler.schedule import SCHEDULE_TYPES, next_run_after
from houseagent.services.conditions import SearchConditions


class TaskSiteIn(BaseModel):
    site_id: str
    account_id: int | None = None
    site_specific_config: dict[str, Any] = Field(default_factory=dict)


class TaskIn(BaseModel):
    name: str
    description: str | None = None
    status: str = "active"
    priority: str = "normal"
    schedule_type: str = "manual"
    schedule: dict[str, Any] = Field(default_factory=dict)
    timezone: str = "Asia/Tokyo"
    conditions: SearchConditions
    sites: list[TaskSiteIn]
    acknowledge_unsupported: bool = False

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be empty")
        return v

    @field_validator("status")
    @classmethod
    def _status(cls, v: str) -> str:
        if v not in ("draft", "active", "paused"):
            raise ValueError("invalid status")
        return v

    @field_validator("priority")
    @classmethod
    def _prio(cls, v: str) -> str:
        if v not in ("high", "normal", "low"):
            raise ValueError("invalid priority")
        return v

    @field_validator("schedule_type")
    @classmethod
    def _stype(cls, v: str) -> str:
        if v not in SCHEDULE_TYPES:
            raise ValueError("invalid schedule_type")
        return v

    @field_validator("sites")
    @classmethod
    def _sites(cls, v: list[TaskSiteIn]) -> list[TaskSiteIn]:
        if not v:
            raise ValueError("at least one target site is required")
        ids = [s.site_id for s in v]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate site")
        return v


class TaskPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    status: str | None = None
    priority: str | None = None
    schedule_type: str | None = None
    schedule: dict[str, Any] | None = None
    timezone: str | None = None
    conditions: SearchConditions | None = None
    sites: list[TaskSiteIn] | None = None
    acknowledge_unsupported: bool = False


def validate_for_sites(conditions: SearchConditions, site_ids: list[str]) -> dict[str, Any]:
    cond = conditions.model_dump(exclude={"region_logic", "price_includes_fees"})
    out: dict[str, Any] = {}
    for sid in site_ids:
        try:
            adapter = get_adapter(sid)
        except KeyError as exc:
            raise AppError(ErrorCode.VALIDATION_ERROR, {"field": "sites", "site_id": sid}) from exc
        caps = adapter.get_capabilities()
        if conditions.deal_type not in caps.deals:
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                {"field": "sites", "site_id": sid, "deal": conditions.deal_type},
                message_key="error.site_deal_mismatch",
            )
        if not caps.automation_available:
            # Nothing is sent to the site automatically, so there is nothing to acknowledge.
            out[sid] = {
                "submit": {},
                "local_filters": [],
                "unsupported": [],
                "manual_only": True,
                "messages": [{"key": "condition.manual_only", "params": {}}],
            }
            continue
        out[sid] = adapter.validate_conditions(cond).to_dict()
    return out


def _check_name_unique(db: Session, name: str, exclude_id: int | None = None) -> None:
    q = select(SearchTask.id).where(SearchTask.name == name, SearchTask.deleted_at.is_(None))
    if exclude_id:
        q = q.where(SearchTask.id != exclude_id)
    if db.execute(q).first():
        raise AppError(ErrorCode.CONFLICT, {"field": "name"}, message_key="error.task_name_duplicate")


def _check_accounts(db: Session, sites: list[TaskSiteIn]) -> None:
    for s in sites:
        if s.account_id is None:
            continue
        acc = db.get(SiteAccount, s.account_id)
        if acc is None or acc.deleted_at is not None or acc.site_id != s.site_id:
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                {"field": "account_id", "site_id": s.site_id},
                message_key="error.account_site_mismatch",
            )


def _require_ack(validation: dict[str, Any], ack: bool) -> None:
    unsupported = {sid: v["unsupported"] for sid, v in validation.items() if v["unsupported"]}
    if unsupported and not ack:
        raise AppError(ErrorCode.CONDITION_UNSUPPORTED, {"unsupported": unsupported})


def _set_sites(task: SearchTask, sites: list[TaskSiteIn]) -> None:
    task.sites.clear()
    for s in sites:
        task.sites.append(
            SearchTaskSite(site_id=s.site_id, account_id=s.account_id, site_specific_config=s.site_specific_config)
        )


def recompute_next_run(task: SearchTask, after: datetime | None = None) -> None:
    if task.status != "active" or task.deleted_at is not None:
        task.next_run_at = None
        return
    task.next_run_at = next_run_after(
        task.schedule_type, task.schedule or {}, task.timezone, after or datetime.now(UTC)
    )


def create_task(db: Session, data: TaskIn) -> tuple[SearchTask, dict[str, Any]]:
    _check_name_unique(db, data.name)
    _check_accounts(db, data.sites)
    validation = validate_for_sites(data.conditions, [s.site_id for s in data.sites])
    _require_ack(validation, data.acknowledge_unsupported)
    task = SearchTask(
        name=data.name,
        description=data.description,
        status=data.status,
        priority=data.priority,
        schedule_type=data.schedule_type,
        schedule=data.schedule,
        timezone=data.timezone,
        condition_current_version=1,
    )
    _set_sites(task, data.sites)
    task.versions.append(SearchConditionVersion(version=1, condition_json=data.conditions.model_dump()))
    db.add(task)
    db.flush()
    recompute_next_run(task)
    return task, validation


def update_task(db: Session, task: SearchTask, patch: TaskPatch) -> tuple[SearchTask, dict[str, Any]]:
    if task.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND)
    fields = patch.model_dump(exclude_unset=True, exclude={"conditions", "sites", "acknowledge_unsupported"})
    if "name" in fields:
        name = (fields["name"] or "").strip()
        if not name:
            raise AppError(ErrorCode.VALIDATION_ERROR, {"field": "name"})
        _check_name_unique(db, name, task.id)
        fields["name"] = name
    if "schedule_type" in fields and fields["schedule_type"] not in SCHEDULE_TYPES:
        raise AppError(ErrorCode.VALIDATION_ERROR, {"field": "schedule_type"})
    sites = (
        patch.sites
        if patch.sites is not None
        else [
            TaskSiteIn(site_id=s.site_id, account_id=s.account_id, site_specific_config=s.site_specific_config)
            for s in task.sites
        ]
    )
    if patch.sites is not None:
        if not patch.sites:
            raise AppError(ErrorCode.VALIDATION_ERROR, {"field": "sites"})
        _check_accounts(db, patch.sites)
    conditions = patch.conditions or SearchConditions(**current_conditions(task))
    validation = validate_for_sites(conditions, [s.site_id for s in sites])
    if patch.conditions is not None or patch.sites is not None:
        _require_ack(validation, patch.acknowledge_unsupported)
    for k, v in fields.items():
        setattr(task, k, v)
    if patch.sites is not None:
        _set_sites(task, patch.sites)
    if patch.conditions is not None:
        new_json = patch.conditions.model_dump()
        if new_json != current_conditions(task):
            # Every change is a new version; runs already recorded keep pointing at the old one.
            new_version = task.condition_current_version + 1
            task.versions.append(SearchConditionVersion(version=new_version, condition_json=new_json))
            task.condition_current_version = new_version
    db.flush()
    recompute_next_run(task)
    return task, validation


def current_conditions(task: SearchTask) -> dict[str, Any]:
    for v in task.versions:
        if v.version == task.condition_current_version:
            return dict(v.condition_json)
    return {}


def copy_task(db: Session, task: SearchTask) -> SearchTask:
    base = f"{task.name} (copy)"
    name, n = base, 2
    while db.execute(select(SearchTask.id).where(SearchTask.name == name, SearchTask.deleted_at.is_(None))).first():
        name = f"{base} {n}"
        n += 1
    # Schedule is deliberately not copied to avoid accidental duplicate runs.
    clone = SearchTask(
        name=name,
        description=task.description,
        status="active",
        priority=task.priority,
        schedule_type="manual",
        schedule={},
        timezone=task.timezone,
        condition_current_version=1,
    )
    clone.sites = [
        SearchTaskSite(
            site_id=s.site_id, account_id=s.account_id, site_specific_config=dict(s.site_specific_config or {})
        )
        for s in task.sites
    ]
    clone.versions.append(SearchConditionVersion(version=1, condition_json=current_conditions(task)))
    db.add(clone)
    db.flush()
    return clone


def pause_task(db: Session, task: SearchTask, reason: str = "user") -> None:
    task.status = "paused"
    task.paused_reason = reason
    task.next_run_at = None
    db.flush()


def resume_task(db: Session, task: SearchTask) -> None:
    task.status = "active"
    task.paused_reason = None
    task.consecutive_failures = 0
    recompute_next_run(task)
    db.flush()


def delete_task(db: Session, task: SearchTask) -> None:
    """Soft delete - run history and listing traceability are kept."""
    task.deleted_at = utcnow()
    task.status = "paused"
    task.next_run_at = None
    for run in db.query(TaskRun).filter(TaskRun.task_id == task.id, TaskRun.status == "queued"):
        run.status = "cancelled"
        run.error_code = ErrorCode.CANCELLED.value
        run.cancel_reason = "task_deleted"
        run.finished_at = utcnow()
    db.flush()


def get_task(db: Session, task_id: int) -> SearchTask:
    task = db.get(SearchTask, task_id)
    if task is None or task.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, {"task_id": task_id})
    return task


def _latest_runs_per_site(db: Session, task_id: int) -> list[TaskRun]:
    latest = (
        select(TaskRun.site_id, func.max(TaskRun.id).label("rid"))
        .where(TaskRun.task_id == task_id)
        .group_by(TaskRun.site_id)
    ).subquery()
    return list(
        db.execute(select(TaskRun).join(latest, TaskRun.id == latest.c.rid).order_by(TaskRun.site_id)).scalars()
    )


def _run_brief(r: TaskRun) -> dict[str, Any]:
    return {
        "id": r.id,
        "run_no": r.run_no,
        "site_id": r.site_id,
        "status": r.status,
        "error_code": r.error_code,
        "error_detail": r.error_detail,
        "result_count": r.result_count,
        "new_count": r.new_count,
        "changed_count": r.changed_count,
        "finished_at": r.finished_at,
        "progress_stage": r.progress_stage,
        "progress_pct": r.progress_pct,
        "wait_reason": r.wait_reason,
        "expected_total": r.expected_total,
    }


def task_to_dict(db: Session, task: SearchTask, include_versions: bool = False) -> dict[str, Any]:
    last = db.execute(
        select(TaskRun).where(TaskRun.task_id == task.id).order_by(TaskRun.id.desc()).limit(1)
    ).scalar_one_or_none()
    active_runs = db.execute(
        select(func.count())
        .select_from(TaskRun)
        .where(TaskRun.task_id == task.id, TaskRun.status.in_(("queued", "running")))
    ).scalar_one()
    d: dict[str, Any] = {
        "id": task.id,
        "name": task.name,
        "description": task.description,
        "status": task.status,
        "priority": task.priority,
        "schedule_type": task.schedule_type,
        "schedule": task.schedule or {},
        "timezone": task.timezone,
        "condition_version": task.condition_current_version,
        "conditions": current_conditions(task),
        "sites": [
            {
                "site_id": s.site_id,
                "account_id": s.account_id,
                "account_alias": s.account.account_alias if s.account else None,
                "site_specific_config": s.site_specific_config or {},
            }
            for s in task.sites
        ],
        "next_run_at": task.next_run_at,
        "last_run_at": task.last_run_at,
        "last_success_at": task.last_success_at,
        "consecutive_failures": task.consecutive_failures,
        "paused_reason": task.paused_reason,
        "active_runs": active_runs,
        "last_run": None
        if last is None
        else {
            "id": last.id,
            "run_no": last.run_no,
            "status": last.status,
            "error_code": last.error_code,
            "result_count": last.result_count,
            "new_count": last.new_count,
            "changed_count": last.changed_count,
            "finished_at": last.finished_at,
            "site_id": last.site_id,
            "progress_stage": last.progress_stage,
            "progress_pct": last.progress_pct,
            "wait_reason": last.wait_reason,
            "error_detail": last.error_detail,
        },
        # Latest run per target site: a multi-site task shows progress / result for each site.
        "last_runs": [_run_brief(r) for r in _latest_runs_per_site(db, task.id)],
        "created_at": task.created_at,
        "updated_at": task.updated_at,
    }
    if include_versions:
        d["versions"] = [
            {"version": v.version, "conditions": v.condition_json, "created_at": v.created_at} for v in task.versions
        ]
    return d
