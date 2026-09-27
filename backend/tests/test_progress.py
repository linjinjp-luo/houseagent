"""Run progress, wait reasons and clear failure results."""

from __future__ import annotations

from typing import Any

from houseagent.browser_worker.locks import account_locks
from houseagent.scheduler.queue import run_queue
from houseagent.scheduler.runner import page_progress
from tests.conftest import Api, drain, make_task


def test_page_progress_formula() -> None:
    assert page_progress(0, 50, 1) == 10
    assert page_progress(25, 50, 2) == 52
    assert page_progress(80, 50, 4) == 95  # capped
    assert page_progress(0, 0, 1) == 95  # site reported no results
    assert page_progress(20, None, 1) == 20  # unknown total: by page count
    assert page_progress(200, None, 30) == 90


def test_completed_run_reports_full_progress_and_site_total(server: Api, ready_account: dict[str, Any]) -> None:
    task = make_task(server, ready_account["id"])
    run = server.ok("POST", f"/search-tasks/{task['id']}/run")["queued"][0]
    assert run["progress_stage"] == "queued" and run["progress_pct"] == 0
    drain()
    done = server.ok("GET", f"/search-runs/{run['id']}")
    assert done["status"] == "completed"
    assert done["progress_stage"] == "done" and done["progress_pct"] == 100
    assert done["expected_total"] == done["result_count"] > 0
    t = server.ok("GET", f"/search-tasks/{task['id']}")
    assert t["last_runs"][0]["id"] == run["id"] and t["last_runs"][0]["progress_pct"] == 100


def test_queued_run_explains_it_waits_for_open_login_window(server: Api, ready_account: dict[str, Any]) -> None:
    task = make_task(server, ready_account["id"])
    assert account_locks.try_acquire(ready_account["id"], "login_window")  # the user's login window is open
    try:
        run = server.ok("POST", f"/search-tasks/{task['id']}/run")["queued"][0]
        run_queue.dispatch_once()
        waiting = server.ok("GET", f"/search-runs/{run['id']}")
        assert waiting["status"] == "queued" and waiting["wait_reason"] == "account_login_window"
        dash = server.ok("GET", "/dashboard")
        assert dash["queue"][0]["wait_reason"] == "account_login_window"
    finally:
        account_locks.release(ready_account["id"])
    drain()
    done = server.ok("GET", f"/search-runs/{run['id']}")
    assert done["status"] == "completed" and done["wait_reason"] is None


def test_site_without_automated_search_is_refused_with_clear_reason(server: Api) -> None:
    from houseagent.adapters import registry
    from houseagent.adapters.base import Capabilities
    from houseagent.adapters.mock.adapter import MockSiteAdapter

    class NoAutomation(MockSiteAdapter):
        def get_capabilities(self) -> Capabilities:
            caps = super().get_capabilities()
            caps.automation_available = False
            return caps

    original = registry.get_adapter("mock_b")
    registry.register(NoAutomation("b"))
    try:
        acc = server.ok(
            "POST", "/accounts", json={"site_id": "mock_b", "account_alias": "b", "auto_search_enabled": True}
        )
        task = make_task(server, acc["id"], site_id="mock_b")
        body = server.err("POST", f"/search-tasks/{task['id']}/run").json()
        assert body["error_code"] == "PERMISSION_BLOCKED"
        assert body["message_key"] == "error.adapter_not_implemented"
        assert body["details"]["reasons"] == {"mock_b": "adapter_not_implemented"}
    finally:
        registry.register(original)


def test_failed_run_carries_specific_reason(server: Api, ready_account: dict[str, Any]) -> None:
    task = make_task(server, ready_account["id"])
    server.mock("a", "scenario", scenario="login_expired")
    run = server.ok("POST", f"/search-tasks/{task['id']}/run")["queued"][0]
    drain()
    r = server.ok("GET", f"/search-runs/{run['id']}")
    assert r["status"] == "paused" and r["error_code"] == "AUTH_REQUIRED"
    assert r["error_detail"] == "login_expired" and r["progress_stage"] == "done"


def test_manual_run_closes_open_login_window(server: Api, ready_account: dict[str, Any]) -> None:
    import threading

    from houseagent.services.accounts import register_login_stop

    task = make_task(server, ready_account["id"])
    assert account_locks.try_acquire(ready_account["id"], "login_window")
    stop = threading.Event()
    register_login_stop(ready_account["id"], stop)

    def login_window() -> None:  # stands in for the visible browser: closes when asked
        stop.wait(10)
        account_locks.release(ready_account["id"])

    t = threading.Thread(target=login_window)
    t.start()
    res = server.ok("POST", f"/search-tasks/{task['id']}/run")
    assert res["closed_login_windows"] == ["個人A"] and stop.is_set()
    t.join(5)
    drain()
    run = server.ok("GET", f"/search-runs/{res['queued'][0]['id']}")
    assert run["status"] == "completed"
    assert any(e["message_key"] == "log.login_window_closed" for e in run["logs"])


def test_scheduled_run_does_not_close_login_window(server: Api, ready_account: dict[str, Any]) -> None:
    import threading

    from houseagent.db.models import SearchTask
    from houseagent.db.session import session_scope
    from houseagent.services.accounts import register_login_stop

    task = make_task(server, ready_account["id"])
    assert account_locks.try_acquire(ready_account["id"], "login_window")
    stop = threading.Event()
    register_login_stop(ready_account["id"], stop)
    try:
        with session_scope() as db:
            run_queue.enqueue_task(db, db.get(SearchTask, task["id"]), "daily")
        assert not stop.is_set()  # the user might be mid sign-in
    finally:
        account_locks.release(ready_account["id"])
    drain()
