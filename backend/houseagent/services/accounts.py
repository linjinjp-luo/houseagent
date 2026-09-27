"""Sites, permissions and site accounts (spec 4.2 / FR-01 / 9.6)."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from houseagent.adapters.base import AccountContext
from houseagent.adapters.registry import get_adapter
from houseagent.browser_worker import profiles
from houseagent.browser_worker.locks import account_locks
from houseagent.config import get_settings
from houseagent.db.models import (
    PERMISSION_TYPES,
    SearchTask,
    SearchTaskSite,
    Site,
    SiteAccount,
    SitePermission,
    TaskRun,
    utcnow,
)
from houseagent.db.session import session_scope
from houseagent.errors import AdapterError, AppError, ErrorCode
from houseagent.services import notifications

log = logging.getLogger(__name__)

LOGIN_STATE_TO_STATUS = {
    "valid": "valid",
    "expired": "relogin_required",
    "unknown": "check_failed",
    "manual_action_required": "check_failed",
}

# account_id -> "opening" | "open" | "closed" | "failed:<code>"
login_windows: dict[int, str] = {}
# account_id -> event that closes the open login window
_login_stops: dict[int, threading.Event] = {}


def permission_map(site: Site) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for p in site.permissions:
        out[p.permission_type] = {"status": p.status, "source": p.source, "reviewed_at": p.reviewed_at, "note": p.note}
    for t in PERMISSION_TYPES:
        out.setdefault(t, {"status": "unknown", "source": None, "reviewed_at": None, "note": None})
    return out


def is_allowed(site: Site, permission_type: str) -> bool:
    return permission_map(site)[permission_type]["status"] == "allowed"


def site_to_dict(db: Session, site: Site) -> dict[str, Any]:
    adapter = get_adapter(site.id)
    accounts = db.execute(
        select(func.count())
        .select_from(SiteAccount)
        .where(SiteAccount.site_id == site.id, SiteAccount.deleted_at.is_(None))
    ).scalar_one()
    from houseagent.services.site_assist import assisted_available

    caps = adapter.get_capabilities().to_dict()
    caps["dedicated_assisted"] = caps["assisted"]
    caps["assisted"] = assisted_available(site, adapter)
    return {
        "id": site.id,
        "name": site.name,
        "base_url": site.base_url,
        "enabled": site.enabled,
        "adapter_status": site.adapter_status,
        "adapter_diagnostic": site.adapter_diagnostic,
        "permissions": permission_map(site),
        "capabilities": caps,
        "assisted_mode": site.assisted_mode,
        "verification_detected_at": site.verification_detected_at,
        "verification_probe": site.verification_probe,
        "is_custom": site.custom_config is not None,
        "account_count": accounts,
        "is_mock": site.id.startswith("mock_"),
        "rules_check_available": adapter.rules_info() is not None,
    }


def update_permission(db: Session, site_id: str, ptype: str, status: str, source: str | None, note: str | None) -> None:
    if ptype not in PERMISSION_TYPES or status not in ("allowed", "denied", "unknown"):
        raise AppError(ErrorCode.VALIDATION_ERROR, {"field": "permission"})
    if status == "allowed" and not (source and source.strip()):
        # An "allowed" status must always cite its rule source / authorization document.
        raise AppError(ErrorCode.VALIDATION_ERROR, {"field": "source"}, message_key="error.permission_source_required")
    perm = db.execute(
        select(SitePermission).where(SitePermission.site_id == site_id, SitePermission.permission_type == ptype)
    ).scalar_one_or_none()
    if perm is None:
        perm = SitePermission(site_id=site_id, permission_type=ptype)
        db.add(perm)
    perm.status = status
    perm.source = source
    perm.note = note
    perm.reviewed_at = utcnow()
    db.flush()


def get_account(db: Session, account_id: int) -> SiteAccount:
    acc = db.get(SiteAccount, account_id)
    if acc is None or acc.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, {"account_id": account_id})
    return acc


def account_context(acc: SiteAccount) -> AccountContext:
    return AccountContext(
        account_id=acc.id, site_id=acc.site_id, alias=acc.account_alias, profile_dir=Path(acc.profile_path)
    )


def anonymous_context(site_id: str) -> AccountContext:
    """Isolated profile for sites whose search needs no login (never shared with a signed-in account)."""
    path = get_settings().profiles_dir / site_id / "_anonymous"
    if not path.exists():
        profiles.create_profile_dir(path)
    return AccountContext(account_id=0, site_id=site_id, alias="(anonymous)", profile_dir=path)


def create_account(db: Session, site_id: str, alias: str, note: str | None, auto_search: bool) -> SiteAccount:
    site = db.get(Site, site_id)
    if site is None or not site.enabled:
        raise AppError(ErrorCode.VALIDATION_ERROR, {"field": "site_id"})
    alias = alias.strip()
    if not alias:
        raise AppError(ErrorCode.VALIDATION_ERROR, {"field": "account_alias"})
    acc = SiteAccount(
        site_id=site_id,
        account_alias=alias,
        note=note,
        auto_search_enabled=auto_search,
        profile_path=f"pending-{utcnow().timestamp()}",
    )
    db.add(acc)
    db.flush()
    path = profiles.profile_dir_for(get_settings().profiles_dir, site_id, acc.id, alias)
    # Two accounts may never share a profile directory (unique constraint + id-based path).
    acc.profile_path = str(path)
    profiles.create_profile_dir(path)
    db.flush()
    return acc


def affected_tasks(db: Session, account_id: int) -> list[dict[str, Any]]:
    rows = db.execute(
        select(SearchTask)
        .join(SearchTaskSite, SearchTaskSite.task_id == SearchTask.id)
        .where(SearchTaskSite.account_id == account_id, SearchTask.deleted_at.is_(None))
    ).scalars()
    return [{"id": t.id, "name": t.name, "status": t.status} for t in rows]


def account_to_dict(db: Session, acc: SiteAccount) -> dict[str, Any]:
    return {
        "id": acc.id,
        "site_id": acc.site_id,
        "site_name": acc.site.name if acc.site else acc.site_id,
        "account_alias": acc.account_alias,
        "profile_path": acc.profile_path,
        "login_status": acc.login_status,
        "auto_search_enabled": acc.auto_search_enabled,
        "last_checked_at": acc.last_checked_at,
        "note": acc.note,
        "permissions": permission_map(acc.site),
        "task_count": len(affected_tasks(db, acc.id)),
        "busy": account_locks.is_locked(acc.id),
        "login_window": login_windows.get(acc.id),
        "created_at": acc.created_at,
    }


def check_login(db: Session, acc: SiteAccount) -> str:
    if not account_locks.try_acquire(acc.id, "login_check"):
        raise AppError(ErrorCode.CONFLICT, message_key="error.account_busy")
    try:
        state = get_adapter(acc.site_id).check_login(account_context(acc))
        previous = acc.login_status
        acc.login_status = LOGIN_STATE_TO_STATUS[state]
        if state == "expired" and previous == "not_logged_in":
            acc.login_status = "not_logged_in"  # never signed in, so nothing has expired
    except AdapterError as exc:
        log.info("login check failed for account %s: %s", acc.id, exc.code)
        acc.login_status = "check_failed"
    finally:
        account_locks.release(acc.id)
    acc.last_checked_at = utcnow()
    if acc.login_status == "valid":
        notifications.resolve_for_account(db, acc.id)
    db.flush()
    return acc.login_status


def confirm_manual_login(db: Session, acc: SiteAccount) -> None:
    """For sites whose session cannot be verified automatically, the user confirms they logged in."""
    acc.login_status = "valid"
    acc.last_checked_at = utcnow()
    notifications.resolve_for_account(db, acc.id)
    db.flush()


def open_login_window(acc: SiteAccount) -> None:
    """Open the isolated, visible browser in the background. The user logs in themselves."""
    if not account_locks.try_acquire(acc.id, "login_window"):
        raise AppError(ErrorCode.CONFLICT, message_key="error.account_busy")
    ctx = account_context(acc)
    ctx.stop_event = threading.Event()
    _login_stops[acc.id] = ctx.stop_event
    adapter = get_adapter(acc.site_id)
    login_windows[acc.id] = "open"

    def _run() -> None:
        try:
            adapter.open_login_page(ctx)
            login_windows[acc.id] = "closed"
        except AdapterError as exc:
            login_windows[acc.id] = f"failed:{exc.code.value}"
        except Exception:
            log.exception("login window failed")
            login_windows[acc.id] = f"failed:{ErrorCode.BROWSER_START_FAILED.value}"
        finally:
            _login_stops.pop(acc.id, None)
            account_locks.release(acc.id)
            from houseagent.scheduler.queue import run_queue

            run_queue.wake()  # a search may be waiting for this account

    threading.Thread(target=_run, name=f"login-{acc.id}", daemon=True).start()


def close_login_window(acc: SiteAccount) -> bool:
    """Close the login window opened by HouseAgent; queued searches for this account can then start."""
    return close_login_window_by_id(acc.id)


def close_login_window_by_id(account_id: int) -> bool:
    """Closing saves the session in the profile, so a search can reuse it right after."""
    stop = _login_stops.get(account_id)
    if stop is None:
        return False
    stop.set()
    return True


def register_login_stop(account_id: int, stop: threading.Event) -> None:
    """Tests only: pretend a login window is open for this account."""
    _login_stops[account_id] = stop


def clear_session(db: Session, acc: SiteAccount) -> None:
    """Delete only the local browser profile. Tasks, listings, favorites and notes stay."""
    if account_locks.is_locked(acc.id):
        raise AppError(ErrorCode.CONFLICT, message_key="error.account_busy")
    profiles.clear_profile(Path(acc.profile_path))
    acc.login_status = "not_logged_in"
    acc.last_checked_at = utcnow()
    db.flush()


def delete_account(db: Session, acc: SiteAccount, action: str, rebind_to: int | None) -> None:
    """action: 'rebind' (move tasks to another account) | 'pause' (pause tasks) | 'session_only'."""
    if account_locks.is_locked(acc.id):
        raise AppError(ErrorCode.CONFLICT, message_key="error.account_busy")
    if action == "session_only":
        clear_session(db, acc)
        return
    links = db.execute(select(SearchTaskSite).where(SearchTaskSite.account_id == acc.id)).scalars().all()
    if action == "rebind":
        target = get_account(db, rebind_to or -1)
        if target.site_id != acc.site_id or target.id == acc.id:
            raise AppError(ErrorCode.VALIDATION_ERROR, {"field": "rebind_to"})
        for link in links:
            link.account_id = target.id
    elif action == "pause":
        from houseagent.services.tasks import pause_task

        for link in links:
            task = db.get(SearchTask, link.task_id)
            if task and task.deleted_at is None:
                pause_task(db, task, "account_deleted")
            link.account_id = None
    else:
        raise AppError(ErrorCode.VALIDATION_ERROR, {"field": "action"})
    for run in db.execute(select(TaskRun).where(TaskRun.account_id == acc.id, TaskRun.status == "queued")).scalars():
        run.status = "cancelled"
        run.error_code = ErrorCode.CANCELLED.value
        run.cancel_reason = "account_deleted"
        run.finished_at = utcnow()
    profiles.clear_profile(Path(acc.profile_path))
    acc.deleted_at = utcnow()
    acc.auto_search_enabled = False
    db.flush()


def refresh_all_logins() -> None:
    """Background: re-check sessions that were valid, so expiring logins surface on the dashboard."""
    with session_scope() as db:
        ids = [
            a.id
            for a in db.execute(
                select(SiteAccount).where(SiteAccount.deleted_at.is_(None), SiteAccount.login_status == "valid")
            ).scalars()
        ]
    for aid in ids:
        try:
            with session_scope() as db:
                acc = db.get(SiteAccount, aid)
                if acc is not None and acc.site_id.startswith("mock_"):
                    status = check_login(db, acc)
                    if status != "valid":
                        notifications.add(
                            db,
                            "login_required",
                            "notice.login_required",
                            site_id=acc.site_id,
                            account_id=acc.id,
                            params={"alias": acc.account_alias},
                        )
        except AppError:
            continue
