"""Executes one queued run (spec 6.3 / FR-03 / 15.5 / 15.9).

PENDING -> permission & login checks -> RUNNING -> COMPLETED / PAUSED / FAILED / CANCELLED.
Every page of results is written in its own transaction; a cancelled run keeps what was already saved.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.exc import DatabaseError, OperationalError, SQLAlchemyError

from houseagent.adapters.base import RunContext
from houseagent.adapters.registry import get_adapter
from houseagent.config import get_settings
from houseagent.db.models import ListingSource, RunLogEntry, RunResult, SearchTask, Site, SiteAccount, TaskRun, utcnow
from houseagent.db.session import session_scope, set_read_only
from houseagent.errors import NEEDS_USER, AdapterError, ErrorCode, new_correlation_id
from houseagent.services import listings, notifications
from houseagent.services import settings_service as app_settings
from houseagent.services.accounts import account_context, anonymous_context, is_allowed
from houseagent.services.conditions import passes_local_filters

log = logging.getLogger(__name__)

MAX_CONSECUTIVE_FAILURES = 3


class _Preflight(Exception):
    def __init__(self, code: ErrorCode, detail: str = "") -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def run_log(run_id: int, level: str, key: str, params: dict[str, Any] | None = None) -> None:
    with session_scope() as db:
        db.add(RunLogEntry(run_id=run_id, level=level, message_key=key, params=params or {}))


def set_progress(run_id: int, stage: str, pct: int, **extra: Any) -> None:
    """Record the live stage / percentage the UI polls."""
    with session_scope() as db:
        r = db.get(TaskRun, run_id)
        if r is None:
            return
        r.progress_stage = stage
        r.progress_pct = max(0, min(100, pct))
        for k, v in extra.items():
            setattr(r, k, v)


def page_progress(raw_seen: int, target: int | None, page_no: int) -> int:
    """10-95 % while searching: by the site's reported total when known, otherwise by page count."""
    if target is not None:
        return 95 if target <= 0 else 10 + int(85 * min(1.0, raw_seen / target))
    return min(90, 10 + page_no * 10)


def execute_run(run_id: int, is_cancelled: Any) -> None:
    settings = get_settings()
    # ---- start ----------------------------------------------------------------------------------------
    with session_scope() as db:
        run = db.get(TaskRun, run_id)
        if run is None or run.status != "queued":
            return
        run.status = "running"
        run.started_at = run.started_at or utcnow()
        run.attempt += 1
        run.correlation_id = run.correlation_id or new_correlation_id()
        run.progress_stage = "preflight"
        run.progress_pct = 2
        run.wait_reason = None
        task = db.get(SearchTask, run.task_id)
        assert task is not None
        task.last_run_at = utcnow()
        site_id, account_id = run.site_id, run.account_id
        condition = next((dict(v.condition_json) for v in task.versions if v.version == run.condition_version), {})
        retention_allowed = False
    run_log(run_id, "info", "log.run_started", {"attempt": run.attempt})

    adapter = get_adapter(site_id)
    ctx = RunContext(
        run_id=run_id,
        run_no=run.run_no,
        condition_version=run.condition_version,
        result_limit=int(condition.get("result_limit") or 100),
        page_timeout_s=settings.page_timeout_s,
        is_cancelled=is_cancelled,
        log=lambda lvl, key, p: run_log(run_id, lvl, key, p),
    )
    try:
        # ---- preflight: permission, task, account, login ----------------------------------------------
        with session_scope() as db:
            task = db.get(SearchTask, run.task_id)
            site = db.get(Site, site_id)
            assert task is not None and site is not None
            if task.deleted_at is not None:
                raise AdapterError(ErrorCode.CANCELLED, "task deleted")
            if site.adapter_status != "ok":
                raise _Preflight(ErrorCode.PAGE_CHANGED, "adapter_stopped")
            if not is_allowed(site, "browser_automation"):
                raise _Preflight(ErrorCode.PERMISSION_BLOCKED, "browser_automation")
            retention_allowed = is_allowed(site, "data_retention")
            caps0 = adapter.get_capabilities()
            if not caps0.automation_available:
                raise _Preflight(ErrorCode.PERMISSION_BLOCKED, "adapter_not_implemented")
            acc = db.get(SiteAccount, account_id) if account_id else None
            if acc is not None and acc.deleted_at is not None:
                acc = None
            if acc is None and caps0.login_required:
                raise _Preflight(ErrorCode.AUTH_REQUIRED, "no_account")
            if acc is not None and not acc.auto_search_enabled:
                raise _Preflight(ErrorCode.PERMISSION_BLOCKED, "account_auto_search_disabled")
            if acc is not None:
                user_confirmed = acc.login_status == "valid"
                actx = account_context(acc)
            else:
                # The site's search is public: use a separate anonymous profile for this site.
                user_confirmed = False
                actx = anonymous_context(site_id)
        ctx.checkpoint()
        if caps0.login_required:
            set_progress(run_id, "login_check", 5)
            state = adapter.check_login(actx)
            if state == "manual_action_required" and user_confirmed:
                state = "valid"  # the site cannot be checked automatically; the user confirmed their sign-in
            with session_scope() as db:
                acc = db.get(SiteAccount, account_id)
                assert acc is not None
                acc.last_checked_at = utcnow()
                acc.login_status = "valid" if state == "valid" else "relogin_required"
            if state != "valid":
                raise _Preflight(ErrorCode.AUTH_REQUIRED, "login_" + state)
        set_progress(run_id, "searching", 10)

        validation = adapter.validate_conditions(
            {k: v for k, v in condition.items() if k not in ("region_logic", "price_includes_fees")}
        )
        caps = adapter.get_capabilities()
        with session_scope() as db:
            r = db.get(TaskRun, run_id)
            assert r is not None
            r.unsupported_conditions = validation.unsupported
        if validation.unsupported:
            run_log(run_id, "warning", "log.conditions_unsupported", {"fields": validation.unsupported})
        if validation.local_filters:
            run_log(run_id, "info", "log.local_filters", {"fields": validation.local_filters})

        # ---- search -----------------------------------------------------------------------------------
        stored = 0
        raw_seen = 0
        complete = True
        # Coarse site filters (e.g. 500万円 steps) were sent as a superset; enforce the exact values here.
        local_fields = list(
            dict.fromkeys(validation.local_filters + [f for f in caps.approximate_fields if f in validation.submit])
        )
        if condition.get("price_includes_fees"):
            # Sites filter on rent alone; rent + management fee can only be compared here.
            local_fields += [f for f in ("price_min", "price_max") if f not in local_fields]
        for page in adapter.execute_search(actx, condition, ctx):
            raw_seen += len(page.items)
            target = min(ctx.result_limit, page.total_hint) if page.total_hint is not None else None
            kept = [i for i in page.items if passes_local_filters(i, condition, local_fields)]
            skipped = len(page.items) - len(kept)
            room = max(ctx.result_limit - stored, 0)
            if len(kept) > room:
                kept, complete = kept[:room], False
            complete = complete and page.complete
            try:
                with session_scope() as db:
                    r = db.get(TaskRun, run_id)
                    assert r is not None
                    counts = listings.ingest_items(
                        db,
                        r,
                        kept,
                        retention_fields=caps.retention_fields,
                        retention_allowed=retention_allowed,
                        rank_offset=stored,
                    )
                    r.pages = page.page_no
                    r.result_count += counts.results
                    r.new_count += counts.new
                    r.changed_count += counts.changed
                    r.skipped_count += counts.skipped + skipped
                    stored += counts.results
                    r.progress_stage = "searching" if page.has_next else "saving"
                    r.progress_pct = page_progress(raw_seen, target, page.page_no)
                    if page.total_hint is not None:
                        r.expected_total = page.total_hint
            except SQLAlchemyError as exc:
                raise _DbFailure(exc) from exc
            run_log(
                run_id, "info", "log.page_done", {"page": page.page_no, "items": len(page.items), "kept": len(kept)}
            )
            if stored >= ctx.result_limit:
                complete = complete and not page.has_next
                break
            ctx.checkpoint()

        # ---- finish -----------------------------------------------------------------------------------
        set_progress(run_id, "finishing", 97)
        with session_scope() as db:
            r = db.get(TaskRun, run_id)
            task = db.get(SearchTask, r.task_id) if r else None
            assert r is not None and task is not None
            if complete:
                r.not_found_count = listings.mark_not_found(db, r)
            r.status = "completed"
            r.finished_at = utcnow()
            r.error_code = None
            r.progress_stage = "done"
            r.progress_pct = 100
            task.last_success_at = r.finished_at
            task.consecutive_failures = 0
            assess = (task.id, task.assess_profile_id) if task.assess_after_run else None
            to_assess: list[int] = []
            if assess:
                # FR-11 (default off): new and changed listings of this run
                to_assess = list(
                    db.execute(
                        select(ListingSource.listing_id)
                        .join(RunResult, RunResult.source_id == ListingSource.id)
                        .where(RunResult.run_id == run_id, or_(RunResult.is_new, RunResult.is_changed))
                    ).scalars()
                )
        run_log(run_id, "info", "log.run_completed", {"complete": complete})
        if assess and to_assess:
            from houseagent.investment import batch as investment_batch

            investment_batch.after_run(assess[0], sorted(set(to_assess)), assess[1])
    except _DbFailure as exc:
        log.error("database error in run %s: %s", run_id, type(exc.original).__name__)
        if isinstance(exc.original, DatabaseError) and not isinstance(exc.original, OperationalError):
            set_read_only("database_error")
        _fail(run_id, ErrorCode.DATABASE_ERROR, "database")
    except _Preflight as exc:
        _handle_error(run_id, AdapterError(exc.code, exc.detail), settings)
    except AdapterError as exc:
        _handle_error(run_id, exc, settings)
    except Exception as exc:  # noqa: BLE001 - every failure must end in a recorded state
        log.exception("unexpected error in run %s", run_id)
        _handle_error(run_id, adapter.classify_error(exc), settings)
    finally:
        try:
            adapter.close(ctx)
        except Exception:
            log.warning("adapter close failed for run %s", run_id)


class _DbFailure(Exception):
    def __init__(self, original: SQLAlchemyError) -> None:
        super().__init__(type(original).__name__)
        self.original = original


def _fail(run_id: int, code: ErrorCode, detail: str) -> None:
    try:
        with session_scope() as db:
            r = db.get(TaskRun, run_id)
            if r is None:
                return
            r.status = "failed"
            r.error_code = code.value
            r.error_detail = detail
            r.finished_at = utcnow()
            r.progress_stage = "done"
    except SQLAlchemyError:
        log.error("could not record failure of run %s", run_id)


def _handle_error(run_id: int, err: AdapterError, settings: Any) -> None:
    code = err.code
    detail = str(err)[:300]
    with session_scope() as db:
        r = db.get(TaskRun, run_id)
        if r is None:
            return
        task = db.get(SearchTask, r.task_id)
        assert task is not None
        retry_enabled = bool(app_settings.get(db, "network_retry_enabled"))
        delays = list(settings.retry_delays)

        if code == ErrorCode.CANCELLED:
            r.status = "cancelled"
            r.error_code = code.value
            r.cancel_reason = r.cancel_reason or "user"
            r.finished_at = utcnow()
            r.progress_stage = "done"
            db.add(RunLogEntry(run_id=run_id, level="info", message_key="log.run_cancelled", params={}))
            return

        if err.retryable and retry_enabled and r.retry_count < len(delays):
            delay = delays[r.retry_count]
            r.retry_count += 1
            r.status = "queued"
            r.error_code = code.value
            r.not_before = utcnow() + timedelta(seconds=delay)
            r.progress_stage = "queued"
            r.wait_reason = "retry_backoff"
            db.add(
                RunLogEntry(
                    run_id=run_id,
                    level="warning",
                    message_key="log.retry_scheduled",
                    params={"code": code.value, "delay_s": delay, "retry": r.retry_count},
                )
            )
            return

        r.error_code = code.value
        r.error_detail = detail
        r.finished_at = utcnow()
        r.progress_stage = "done"
        params = {"task": task.name, "run_no": r.run_no, "alias": r.account_alias}
        if code in NEEDS_USER:
            r.status = "paused"
            kind = {
                ErrorCode.AUTH_REQUIRED: "login_required",
                ErrorCode.CAPTCHA_REQUIRED: "captcha",
                ErrorCode.RATE_LIMITED: "rate_limited",
                ErrorCode.PERMISSION_BLOCKED: "permission_required",
            }[code]
            db.add(
                RunLogEntry(
                    run_id=run_id, level="action_required", message_key=f"error.{code.value}", params={"detail": detail}
                )
            )
            notifications.add(
                db,
                kind,
                f"notice.{kind}",
                task_id=task.id,
                run_id=r.id,
                site_id=r.site_id,
                account_id=r.account_id if code == ErrorCode.AUTH_REQUIRED else None,
                params=params,
            )
            if code == ErrorCode.CAPTCHA_REQUIRED:
                # Offer browser-assisted import for this site from now on (auto mode); never solve it here.
                from houseagent.services.site_assist import mark_verification

                site_row = db.get(Site, r.site_id)
                if site_row is not None:
                    mark_verification(site_row, "run", None, detail)
            if code == ErrorCode.AUTH_REQUIRED and r.account_id:
                acc = db.get(SiteAccount, r.account_id)
                if acc is not None:
                    acc.login_status = "relogin_required"
        else:
            r.status = "failed"
            db.add(
                RunLogEntry(
                    run_id=run_id,
                    level="failure",
                    message_key=f"error.{code.value}",
                    params={"detail": detail, "correlation_id": r.correlation_id},
                )
            )
            if code == ErrorCode.PAGE_CHANGED and detail != "adapter_stopped":
                site = db.get(Site, r.site_id)
                if site is not None:
                    site.adapter_status = "stopped"
                    site.adapter_diagnostic = {
                        "run_no": r.run_no,
                        "at": utcnow().isoformat(),
                        **{k: str(v) for k, v in err.diagnostic.items()},
                    }
                notifications.add(
                    db,
                    "page_changed",
                    "notice.page_changed",
                    task_id=None,
                    site_id=r.site_id,
                    run_id=r.id,
                    params=params,
                )

        if code != ErrorCode.PERMISSION_BLOCKED:
            task.consecutive_failures += 1
        if task.consecutive_failures >= MAX_CONSECUTIVE_FAILURES and task.status == "active":
            task.status = "paused"
            task.paused_reason = "consecutive_failures"
            task.next_run_at = None
            notifications.add(db, "task_paused", "notice.task_paused", task_id=task.id, run_id=r.id, params=params)
