"""Scheduling service: startup recovery, catch-up, periodic triggers, reminders and daily backup.

Rules (spec 4.4 / 15.5):
* Interrupted ``running`` runs become ``interrupted`` at startup; the user decides whether to rerun.
* Missed periods while the PC was off: only the most recent one is run, marked ``catchup``.
* ``startup`` tasks run at most once per launch and respect a minimum interval.
* ``reminder`` tasks never access a site - they create a todo notification.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import select

from houseagent.config import get_settings
from houseagent.db.models import RunLogEntry, SearchTask, TaskRun, utcnow
from houseagent.db.session import read_only_reason, session_scope
from houseagent.errors import AppError, ErrorCode
from houseagent.scheduler.queue import run_queue
from houseagent.services import backup, notifications
from houseagent.services import settings_service as app_settings
from houseagent.services.accounts import refresh_all_logins
from houseagent.services.tasks import recompute_next_run

log = logging.getLogger(__name__)

PERIODIC = ("daily", "weekly", "interval", "reminder")


def recover_interrupted() -> int:
    n = 0
    with session_scope() as db:
        for run in db.execute(select(TaskRun).where(TaskRun.status == "running")).scalars():
            run.status = "interrupted"
            run.error_code = ErrorCode.INTERRUPTED.value
            run.finished_at = utcnow()
            db.add(RunLogEntry(run_id=run.id, level="action_required", message_key="error.INTERRUPTED", params={}))
            notifications.add(
                db,
                "run_interrupted",
                "notice.run_interrupted",
                task_id=run.task_id,
                run_id=run.id,
                params={"run_no": run.run_no},
            )
            n += 1
    return n


def _fire(task: SearchTask, trigger: str) -> None:
    with session_scope() as db:
        t = db.get(SearchTask, task.id)
        if t is None:
            return
        if t.schedule_type == "reminder":
            notifications.add(db, "reminder", "notice.reminder", task_id=t.id, params={"task": t.name})
            return
        skipped: dict[str, str] = {}
        try:
            run_queue.enqueue_task(db, t, trigger, skipped=skipped)
        except AppError as exc:
            log.info("scheduled task %s not queued: %s", t.id, exc.code)
            return
        # Only permission problems need the user; sites without an adapter are simply skipped.
        blocked = [sid for sid, reason in skipped.items() if reason in ("browser_automation", "rules_check_required")]
        if blocked:
            notifications.add(
                db,
                "permission_required",
                "notice.permission_required",
                task_id=t.id,
                params={"task": t.name, "sites": blocked},
            )


def catch_up_and_startup() -> None:
    now = datetime.now(UTC)
    with session_scope() as db:
        tasks = (
            db.execute(select(SearchTask).where(SearchTask.deleted_at.is_(None), SearchTask.status == "active"))
            .scalars()
            .all()
        )
        due_catchup, due_startup = [], []
        for t in tasks:
            if t.schedule_type in PERIODIC and t.next_run_at and t.next_run_at <= now:
                due_catchup.append(t)
                recompute_next_run(t, now)  # all other missed periods are skipped
            elif t.schedule_type in PERIODIC and t.next_run_at is None:
                recompute_next_run(t, now)
            if t.schedule_type == "startup":
                min_hours = float((t.schedule or {}).get("min_interval_hours") or 0)
                last = t.last_success_at
                if last is None or last <= now - timedelta(hours=min_hours):
                    due_startup.append(t)
                    t.last_startup_run_at = now
    for t in due_catchup:
        _fire(t, "catchup" if t.schedule_type != "reminder" else "reminder")
    for t in due_startup:
        _fire(t, "startup")


def tick() -> None:
    if read_only_reason():
        return
    now = datetime.now(UTC)
    with session_scope() as db:
        due = (
            db.execute(
                select(SearchTask).where(
                    SearchTask.deleted_at.is_(None),
                    SearchTask.status == "active",
                    SearchTask.schedule_type.in_(PERIODIC),
                    SearchTask.next_run_at.is_not(None),
                    SearchTask.next_run_at <= now,
                )
            )
            .scalars()
            .all()
        )
        for t in due:
            recompute_next_run(t, now)
    for t in due:
        _fire(t, t.schedule_type)


def daily_backup() -> None:
    settings = get_settings()
    with session_scope() as db:
        enabled = bool(app_settings.get(db, "backup_auto_daily"))
        keep = int(app_settings.get(db, "backup_keep") or 7)
    if not enabled or not backup.daily_backup_due(settings):
        return
    try:
        backup.create_backup(settings, "daily")
        backup.prune_backups(settings, keep)
    except Exception:
        log.exception("daily backup failed")


class SchedulerService:
    def __init__(self) -> None:
        self._sched: BackgroundScheduler | None = None

    def start(self) -> None:
        recover_interrupted()
        catch_up_and_startup()
        run_queue.start()
        self._sched = BackgroundScheduler(timezone="UTC")
        self._sched.add_job(tick, "interval", seconds=30, id="tick", max_instances=1, coalesce=True)
        self._sched.add_job(
            daily_backup,
            "interval",
            hours=1,
            id="backup",
            max_instances=1,
            coalesce=True,
            next_run_time=datetime.now(UTC) + timedelta(seconds=20),
        )
        from houseagent.services.site_rules import recheck_accepted_sites

        self._sched.add_job(
            recheck_accepted_sites,
            "interval",
            hours=24,
            id="rules_recheck",
            max_instances=1,
            coalesce=True,
            next_run_time=datetime.now(UTC) + timedelta(minutes=2),
        )
        self._sched.add_job(refresh_all_logins, "interval", hours=3, id="login_refresh", max_instances=1, coalesce=True)
        self._sched.start()

    def shutdown(self) -> None:
        if self._sched is not None:
            self._sched.shutdown(wait=False)
            self._sched = None
        run_queue.shutdown()


scheduler_service = SchedulerService()
