"""Run queue (spec 4.5 / 15.5).

* One browser run per site account at a time; others wait.
* At most ``max_parallel`` (default 2) runs across different sites.
* Manual runs go to the front; then high / normal / low; FIFO within the same priority.
* Queued runs live in the database, so they survive a restart.
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from houseagent.adapters.registry import get_adapter
from houseagent.browser_worker.locks import account_locks
from houseagent.db.models import RunLogEntry, SearchTask, Site, TaskRun, utcnow
from houseagent.db.session import session_scope
from houseagent.errors import AppError, ErrorCode
from houseagent.scheduler.runner import execute_run
from houseagent.services.accounts import close_login_window_by_id, is_allowed
from houseagent.services.site_assist import assisted_available

log = logging.getLogger(__name__)

PRIORITY_RANK = {"manual": 0, "high": 1, "normal": 2, "low": 3}


def _run_no(run: TaskRun) -> str:
    return f"R{datetime.now(UTC):%Y%m%d}-{run.id:06d}"


class RunQueue:
    def __init__(self, max_parallel: int = 2) -> None:
        self.max_parallel = max_parallel
        self._pool: ThreadPoolExecutor | None = None
        self._running: dict[int, threading.Event] = {}  # run_id -> cancel flag
        self._mutex = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._accepting = True
        self._thread: threading.Thread | None = None

    # ---------------------------------------------------------------------------------------- enqueue

    def wake(self) -> None:
        self._wake.set()

    def enqueue_task(
        self,
        db: Session,
        task: SearchTask,
        trigger_type: str,
        closed_login_windows: list[str] | None = None,
        skipped: dict[str, str] | None = None,
    ) -> list[TaskRun]:
        """Queue one run per target site. closed_login_windows (out) receives aliases whose window was closed."""
        if task.deleted_at is not None:
            raise AppError(ErrorCode.NOT_FOUND)
        if task.status != "active":
            raise AppError(ErrorCode.CONFLICT, {"status": task.status}, message_key="error.task_not_active")
        if not self._accepting:
            raise AppError(ErrorCode.CONFLICT, message_key="error.shutting_down")
        manual = trigger_type == "manual"
        blocked: dict[str, str] = {}
        runs: list[TaskRun] = []
        conditions = next(
            (dict(v.condition_json) for v in task.versions if v.version == task.condition_current_version), {}
        )
        for link in task.sites:
            site = db.get(Site, link.site_id)
            adapter = get_adapter(link.site_id)
            # Order matters: tell the user the most fundamental reason first.
            caps = adapter.get_capabilities()
            if not caps.automation_available:
                blocked[link.site_id] = (
                    "assisted_only" if assisted_available(site, adapter) else "adapter_not_implemented"
                )
                continue
            if not adapter.automated_types(conditions):
                blocked[link.site_id] = "deal_not_automated"
                continue
            if site is None or not is_allowed(site, "browser_automation"):
                # Sites with published rules get an automatic check the user can accept.
                needs_check = site is not None and adapter.rules_info() is not None
                blocked[link.site_id] = "rules_check_required" if needs_check else "browser_automation"
                continue
            dup = db.execute(
                select(TaskRun.id).where(
                    TaskRun.task_id == task.id,
                    TaskRun.site_id == link.site_id,
                    TaskRun.status.in_(("queued", "running")),
                )
            ).first()
            if dup:
                continue
            run = TaskRun(
                run_no=f"tmp-{utcnow().timestamp()}-{link.site_id}",
                task_id=task.id,
                site_id=link.site_id,
                account_id=link.account_id,
                account_alias=link.account.account_alias if link.account else None,
                condition_version=task.condition_current_version,
                trigger_type=trigger_type,
                priority_rank=PRIORITY_RANK["manual"] if manual else PRIORITY_RANK[task.priority],
            )
            db.add(run)
            db.flush()
            run.run_no = _run_no(run)
            runs.append(run)
        if blocked and not runs and manual:
            # Unauthorised automated searches can never start (acceptance item).
            reasons = set(blocked.values())
            key = (
                "error.assisted_only"
                if reasons == {"assisted_only"}
                else "error.deal_not_automated"
                if reasons == {"deal_not_automated"}
                else "error.adapter_not_implemented"
                if reasons <= {"adapter_not_implemented", "deal_not_automated"}
                else "error.rules_check_required"
                if reasons == {"rules_check_required"}
                else "error.PERMISSION_BLOCKED"
            )
            raise AppError(ErrorCode.PERMISSION_BLOCKED, {"sites": list(blocked), "reasons": blocked}, message_key=key)
        if skipped is not None:
            skipped.update(blocked)
        if manual:
            # The user explicitly asked to search now: close any login window holding the account.
            # Scheduled runs never do this - the user might be in the middle of signing in.
            for run in runs:
                if run.account_id and account_locks.holder(run.account_id) == "login_window":
                    if close_login_window_by_id(run.account_id):
                        if closed_login_windows is not None:
                            closed_login_windows.append(run.account_alias or str(run.account_id))
                        db.add(
                            RunLogEntry(
                                run_id=run.id,
                                level="info",
                                message_key="log.login_window_closed",
                                params={"alias": run.account_alias},
                            )
                        )
        db.flush()
        self._wake.set()
        return runs

    def cancel(self, db: Session, run: TaskRun, reason: str = "user") -> None:
        if run.status == "queued":
            run.status = "cancelled"
            run.error_code = ErrorCode.CANCELLED.value
            run.cancel_reason = reason
            run.finished_at = utcnow()
        elif run.status == "running":
            run.cancel_requested = True
            run.cancel_reason = reason
            with self._mutex:
                flag = self._running.get(run.id)
            if flag is not None:
                flag.set()
        else:
            raise AppError(ErrorCode.CONFLICT, {"status": run.status}, message_key="error.run_not_cancellable")
        db.flush()

    # ---------------------------------------------------------------------------------------- dispatch

    def running_ids(self) -> list[int]:
        with self._mutex:
            return list(self._running)

    def dispatch_once(self) -> list[int]:
        """Start every run that is allowed to start now. Returns started run ids."""
        started: list[int] = []
        if not self._accepting:
            return started
        now = utcnow()
        with session_scope() as db:
            candidates = (
                db.execute(
                    select(TaskRun)
                    .where(TaskRun.status == "queued", or_(TaskRun.not_before.is_(None), TaskRun.not_before <= now))
                    .order_by(TaskRun.priority_rank, TaskRun.queued_at, TaskRun.id)
                )
                .scalars()
                .all()
            )
            plan = [(r.id, r.account_id, r.site_id, r.wait_reason) for r in candidates]
        waiting: dict[int, str] = {}
        for run_id, account_id, site_id, current_reason in plan:
            with self._mutex:
                full = len(self._running) >= self.max_parallel
            lock_key = account_id if account_id is not None else -abs(hash(site_id))
            if full:
                reason = "max_parallel"
            elif not account_locks.try_acquire(lock_key, f"run:{run_id}"):
                holder = account_locks.holder(lock_key) or ""
                reason = "account_login_window" if holder == "login_window" else "account_busy"
            else:
                reason = ""
            if reason:
                if reason != current_reason:
                    waiting[run_id] = reason
                continue
            flag = threading.Event()
            with self._mutex:
                self._running[run_id] = flag
            started.append(run_id)
            self._submit(run_id, lock_key, flag)
        if waiting:
            self._record_wait_reasons(waiting)
        return started

    @staticmethod
    def _record_wait_reasons(waiting: dict[int, str]) -> None:
        """Tell the user why a queued run has not started (only written when the reason changes)."""
        try:
            with session_scope() as db:
                for r in db.execute(select(TaskRun).where(TaskRun.id.in_(list(waiting)))).scalars():
                    if r.status == "queued":
                        r.wait_reason = waiting[r.id]
        except Exception:
            log.warning("could not record wait reasons")

    def _submit(self, run_id: int, lock_key: int, flag: threading.Event) -> None:
        if self._pool is None:
            self._pool = ThreadPoolExecutor(max_workers=self.max_parallel, thread_name_prefix="run")
        self._pool.submit(self._execute, run_id, lock_key, flag)

    def _execute(self, run_id: int, lock_key: int, flag: threading.Event) -> None:
        try:
            execute_run(run_id, flag.is_set)
        except Exception:
            log.exception("run %s crashed", run_id)
        finally:
            account_locks.release(lock_key)
            with self._mutex:
                self._running.pop(run_id, None)
            self._wake.set()

    def run_until_idle(self, timeout_s: float = 60.0) -> None:
        """Synchronously drain the queue (tests). Waits for retries whose delay has elapsed."""
        import time

        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            started = self.dispatch_once()
            with self._mutex:
                busy = bool(self._running)
            if not started and not busy:
                with session_scope() as db:
                    pending = db.execute(
                        select(TaskRun.id).where(
                            TaskRun.status == "queued",
                            or_(TaskRun.not_before.is_(None), TaskRun.not_before <= utcnow()),
                        )
                    ).first()
                if not pending:
                    return
            time.sleep(0.05)
        raise TimeoutError("queue did not become idle")

    # ---------------------------------------------------------------------------------------- lifecycle

    def start(self) -> None:
        self._stop.clear()
        self._accepting = True
        self._thread = threading.Thread(target=self._loop, name="run-dispatcher", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.dispatch_once()
            except Exception:
                log.exception("dispatcher error")
            self._wake.wait(timeout=2.0)
            self._wake.clear()

    def shutdown(self, wait_s: float = 20.0) -> None:
        """Stop accepting work, ask running runs to stop at a safe point, then wait."""
        self._accepting = False
        self._stop.set()
        self._wake.set()
        with self._mutex:
            flags = list(self._running.values())
            ids = list(self._running)
        if ids:
            try:
                with session_scope() as db:
                    for r in db.execute(select(TaskRun).where(TaskRun.id.in_(ids))).scalars():
                        r.cancel_requested = True
                        r.cancel_reason = "app_shutdown"
            except Exception:
                log.warning("could not record shutdown cancellation")
        for f in flags:
            f.set()
        if self._pool is not None:
            self._pool.shutdown(wait=True, cancel_futures=False)
            self._pool = None

    def status(self) -> dict[str, Any]:
        with self._mutex:
            return {"running": list(self._running), "max_parallel": self.max_parallel, "accepting": self._accepting}


run_queue = RunQueue()
