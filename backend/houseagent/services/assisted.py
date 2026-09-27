"""Browser-assisted import.

For sites that answer automated browsers with a human verification (e.g. at home), HouseAgent opens a visible
browser with an isolated profile and lets the *user* browse: they complete any verification and move between
result pages themselves. When the user clicks "import this page", HouseAgent reads the result list currently
shown and processes it like a normal search page (dedupe, history, statistics). It never solves a verification,
never clicks through pages on its own and never runs on a schedule.

Each import becomes a ``TaskRun`` (trigger ``assisted``) so it stays traceable in the run log.
"""

from __future__ import annotations

import logging
import queue
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy.orm import Session

from houseagent.adapters.registry import get_adapter
from houseagent.browser_worker.locks import account_locks
from houseagent.config import get_settings
from houseagent.db.models import RunLogEntry, SearchTask, Site, SiteAccount, TaskRun, utcnow
from houseagent.errors import AppError, ErrorCode, new_correlation_id
from houseagent.services import listings
from houseagent.services.accounts import account_context, anonymous_context
from houseagent.services.conditions import passes_local_filters

log = logging.getLogger(__name__)

# Task conditions that can be checked on imported listings (the site took none of them).
LOCAL_FIELDS = [
    "transaction_type",
    "cities",
    "price_min",
    "price_max",
    "area_min",
    "area_max",
    "building_age_max",
    "walk_minutes_max",
    "layouts",
    "keywords_include",
    "keywords_exclude",
]


class AssistedBrowser(Protocol):
    def capture(self) -> tuple[str, str]:
        """(url, html) of the page the user is looking at."""

    def is_open(self) -> bool: ...

    def close(self) -> None: ...


class PlaywrightAssistedBrowser:
    """A visible browser owned by one thread (Playwright's sync API is thread-bound)."""

    def __init__(self, profile_dir: Path, start_url: str, channel: str) -> None:
        self._cmds: queue.Queue[tuple[str, queue.Queue[Any]]] = queue.Queue()
        self._open = threading.Event()
        self._closed = threading.Event()
        self._error: str | None = None
        self._thread = threading.Thread(
            target=self._run, args=(profile_dir, start_url, channel), daemon=True, name="assisted-browser"
        )
        self._thread.start()
        self._open.wait(60)
        if self._error:
            raise AppError(ErrorCode.BROWSER_START_FAILED, message_key="error.BROWSER_START_FAILED")

    def _run(self, profile_dir: Path, start_url: str, channel: str) -> None:
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as pw:
                kwargs: dict[str, Any] = {"headless": False, "locale": "ja-JP", "no_viewport": True}
                if channel and channel != "chromium":
                    kwargs["channel"] = channel
                ctx = pw.chromium.launch_persistent_context(str(profile_dir), **kwargs)
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                try:
                    page.goto(start_url, wait_until="domcontentloaded", timeout=60_000)
                except Exception:
                    log.info("assisted start page did not finish loading; the user can navigate manually")
                self._open.set()
                while ctx.pages:
                    try:
                        cmd, reply = self._cmds.get(timeout=0.5)
                    except queue.Empty:
                        continue
                    if cmd == "close":
                        reply.put(None)
                        break
                    if cmd == "capture":
                        try:
                            current = ctx.pages[-1]  # the tab the user opened last
                            reply.put((current.url, current.content()))
                        except Exception as exc:
                            reply.put(exc)
                ctx.close()
        except Exception as exc:
            self._error = type(exc).__name__
            log.warning("assisted browser failed: %s", self._error)
        finally:
            self._open.set()
            self._closed.set()

    def _call(self, cmd: str) -> Any:
        if self._closed.is_set():
            raise AppError(ErrorCode.CONFLICT, message_key="error.assisted_closed")
        reply: queue.Queue[Any] = queue.Queue()
        self._cmds.put((cmd, reply))
        try:
            result = reply.get(timeout=30)
        except queue.Empty as exc:
            raise AppError(ErrorCode.PAGE_TIMEOUT) from exc
        if isinstance(result, Exception):
            raise AppError(ErrorCode.UNKNOWN_ERROR, message_key="error.assisted_capture_failed") from result
        return result

    def capture(self) -> tuple[str, str]:
        return self._call("capture")  # type: ignore[no-any-return]

    def is_open(self) -> bool:
        return not self._closed.is_set()

    def close(self) -> None:
        if not self._closed.is_set():
            try:
                self._call("close")
            except AppError:
                pass
            self._closed.wait(15)  # the browser needs a moment to shut down; the account lock is released after


BrowserFactory = Callable[[Path, str], AssistedBrowser]


def _default_factory(profile_dir: Path, start_url: str) -> AssistedBrowser:
    return PlaywrightAssistedBrowser(profile_dir, start_url, get_settings().browser_channel)


browser_factory: BrowserFactory = _default_factory  # replaced in tests


@dataclass
class AssistedSession:
    id: str
    task_id: int
    site_id: str
    account_id: int | None
    lock_key: int
    browser: AssistedBrowser
    start_urls: list[tuple[str, str]]
    imports: list[dict[str, Any]] = field(default_factory=list)
    last_next_url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "site_id": self.site_id,
            "open": self.browser.is_open(),
            "start_urls": [{"property_type": t, "url": u} for t, u in self.start_urls],
            "imports": self.imports,
            "next_url": self.last_next_url,
        }


_sessions: dict[str, AssistedSession] = {}
_mutex = threading.Lock()


def _cleanup_closed() -> None:
    with _mutex:
        for sid, s in list(_sessions.items()):
            if not s.browser.is_open():
                account_locks.release(s.lock_key)
                _sessions.pop(sid, None)


def start(db: Session, task: SearchTask, site_id: str) -> AssistedSession:
    _cleanup_closed()
    link = next((s for s in task.sites if s.site_id == site_id), None)
    if link is None:
        raise AppError(ErrorCode.VALIDATION_ERROR, {"field": "site_id"})
    adapter = get_adapter(site_id)
    from houseagent.services.site_assist import assisted_available

    if not assisted_available(db.get(Site, site_id), adapter):
        raise AppError(ErrorCode.CONFLICT, message_key="error.assisted_not_supported")
    with _mutex:
        for s in _sessions.values():
            if s.task_id == task.id and s.site_id == site_id and s.browser.is_open():
                return s  # already open: reuse
    conditions = next(
        (dict(v.condition_json) for v in task.versions if v.version == task.condition_current_version), {}
    )
    acc = db.get(SiteAccount, link.account_id) if link.account_id else None
    ctx = account_context(acc) if acc is not None and acc.deleted_at is None else anonymous_context(site_id)
    lock_key = ctx.account_id if ctx.account_id else -abs(hash(site_id))
    if not account_locks.try_acquire(lock_key, "assisted"):
        raise AppError(ErrorCode.CONFLICT, message_key="error.account_busy")
    urls = adapter.assisted_start_urls(conditions)
    try:
        browser = browser_factory(ctx.profile_dir, urls[0][1] if urls else adapter.base_url)
    except Exception:
        account_locks.release(lock_key)
        raise
    session = AssistedSession(
        id=uuid.uuid4().hex[:12],
        task_id=task.id,
        site_id=site_id,
        account_id=acc.id if acc else None,
        lock_key=lock_key,
        browser=browser,
        start_urls=urls,
    )
    with _mutex:
        _sessions[session.id] = session
    return session


def get(session_id: str) -> AssistedSession:
    with _mutex:
        s = _sessions.get(session_id)
    if s is None:
        raise AppError(ErrorCode.NOT_FOUND, message_key="error.assisted_closed")
    return s


def import_page(db: Session, session_id: str) -> dict[str, Any]:
    """Read the page the user has open and process its listings as one assisted run."""
    s = get(session_id)
    if not s.browser.is_open():
        close(session_id)
        raise AppError(ErrorCode.CONFLICT, message_key="error.assisted_closed")
    url, html = s.browser.capture()
    adapter = get_adapter(s.site_id)
    page = adapter.parse_assisted_page(url, html)
    if page.verification:
        raise AppError(ErrorCode.CAPTCHA_REQUIRED, message_key="error.assisted_verification_pending")
    if not page.is_result_list:
        raise AppError(ErrorCode.VALIDATION_ERROR, {"url": url}, message_key="error.assisted_not_result_page")
    task = db.get(SearchTask, s.task_id)
    assert task is not None
    conditions = next(
        (dict(v.condition_json) for v in task.versions if v.version == task.condition_current_version), {}
    )
    deal = conditions.get("deal_type") or "buy"
    types = conditions.get("transaction_type") or []
    for i in page.items:
        if i.raw_enums.get("extractor") == "generic":
            # The generic reader cannot always tell the type: the task says what the user is browsing for.
            i.deal_type = deal
            if i.property_type is None and len(types) == 1:
                i.property_type = types[0]
    no_type = [f for f in LOCAL_FIELDS if f != "transaction_type"]
    kept = [i for i in page.items if passes_local_filters(i, conditions, LOCAL_FIELDS if i.property_type else no_type)]
    acc = db.get(SiteAccount, s.account_id) if s.account_id else None
    run = TaskRun(
        run_no=f"tmp-{uuid.uuid4().hex}",
        task_id=task.id,
        site_id=s.site_id,
        account_id=s.account_id,
        account_alias=acc.account_alias if acc else None,
        condition_version=task.condition_current_version,
        trigger_type="assisted",
        priority_rank=0,
        status="running",
        started_at=utcnow(),
        attempt=1,
        correlation_id=new_correlation_id(),
        progress_stage="saving",
        progress_pct=50,
    )
    db.add(run)
    db.flush()
    from houseagent.scheduler.queue import _run_no

    run.run_no = _run_no(run)
    # The user opened this page and asked for it to be recorded: like a manual import, this is user-driven
    # capture of summary fields only (no photos, descriptions or agent details).
    counts = listings.ingest_items(
        db, run, kept, retention_fields=adapter.get_capabilities().retention_fields, retention_allowed=True
    )
    run.result_count, run.new_count, run.changed_count = counts.results, counts.new, counts.changed
    run.skipped_count = counts.skipped + (len(page.items) - len(kept))
    run.pages = 1
    run.expected_total = page.total
    run.status, run.finished_at, run.progress_stage, run.progress_pct = "completed", utcnow(), "done", 100
    db.add(
        RunLogEntry(
            run_id=run.id,
            level="info",
            message_key="log.assisted_page",
            params={"url": url, "items": len(page.items), "kept": len(kept)},
        )
    )
    task.last_run_at = task.last_success_at = utcnow()
    db.flush()
    result = {
        "run_id": run.id,
        "run_no": run.run_no,
        "url": url,
        "items": len(page.items),
        "kept": len(kept),
        "new": counts.new,
        "changed": counts.changed,
        "total": page.total,
        "next_url": page.next_url,
        "at": utcnow(),
    }
    s.imports.append(result)
    s.last_next_url = page.next_url
    return result


def close(session_id: str) -> None:
    with _mutex:
        s = _sessions.pop(session_id, None)
    if s is not None:
        try:
            s.browser.close()
        finally:
            account_locks.release(s.lock_key)


def active_for_task(task_id: int) -> list[AssistedSession]:
    _cleanup_closed()
    with _mutex:
        return [s for s in _sessions.values() if s.task_id == task_id]
