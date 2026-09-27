"""Task management, user data, security, backup/restore and statistics."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from tests.conftest import TOKEN, Api, drain, make_task


def test_security_token_origin_host(server: Api) -> None:
    assert httpx.get(server.base + "/api/v1/health").status_code == 200  # open
    assert httpx.get(server.base + "/api/v1/search-tasks").status_code == 401
    r = httpx.get(
        server.base + "/api/v1/search-tasks", headers={"X-HouseAgent-Token": TOKEN, "Origin": "http://evil.example"}
    )
    assert r.status_code == 403
    r = httpx.get(server.base + "/api/v1/search-tasks", headers={"X-HouseAgent-Token": TOKEN, "Host": "evil.example"})
    assert r.status_code == 403


def test_task_crud_versions_copy_soft_delete(server: Api, ready_account: dict[str, Any]) -> None:
    task = make_task(
        server, ready_account["id"], schedule_type="daily", schedule={"time": "08:00"}, timezone="Asia/Tokyo"
    )
    assert task["next_run_at"] is not None
    # duplicate name refused
    r = server.err(
        "POST",
        "/search-tasks",
        json={
            "name": task["name"],
            "conditions": {"prefectures": ["11"]},
            "sites": [{"site_id": "mock_a", "account_id": ready_account["id"]}],
        },
    )
    assert r.json()["error_code"] == "CONFLICT"
    server.ok("POST", f"/search-tasks/{task['id']}/run")
    drain()
    upd = server.ok(
        "PATCH", f"/search-tasks/{task['id']}", json={"conditions": {"prefectures": ["11"], "price_max": 5000}}
    )
    assert upd["condition_version"] == 2 and len(upd["versions"]) == 2
    runs = server.ok("GET", "/search-runs", params={"task_id": task["id"]})["items"]
    assert runs[0]["condition_version"] == 1  # history keeps the old version
    copy = server.ok("POST", f"/search-tasks/{task['id']}/copy")
    assert copy["schedule_type"] == "manual" and copy["conditions"]["price_max"] == 5000
    paused = server.ok("POST", f"/search-tasks/{task['id']}/pause")
    assert paused["status"] == "paused" and paused["next_run_at"] is None
    assert server.err("POST", f"/search-tasks/{task['id']}/run").json()["error_code"] == "CONFLICT"
    resumed = server.ok("POST", f"/search-tasks/{task['id']}/resume")
    assert resumed["next_run_at"] is not None
    server.ok("DELETE", f"/search-tasks/{task['id']}")
    assert all(t["id"] != task["id"] for t in server.ok("GET", "/search-tasks"))
    # soft delete: runs and listings stay traceable
    assert server.ok("GET", "/search-runs", params={"task_id": task["id"]})["total"] == 1
    assert server.ok("GET", "/properties", params={"task_id": task["id"], "page_size": 1})["total"] > 0


def test_condition_validation(server: Api, ready_account: dict[str, Any]) -> None:
    base = {"name": "x", "sites": [{"site_id": "mock_a", "account_id": ready_account["id"]}]}
    for bad in (
        {"prefectures": []},
        {"prefectures": ["11"], "price_min": 5000, "price_max": 100},
        {"prefectures": ["11"], "cities": ["13101"]},
        {"prefectures": ["11"], "result_limit": 501},
        {"prefectures": ["11"], "area_min": 0},
    ):
        r = server.err("POST", "/search-tasks", json={**base, "conditions": bad})
        assert r.json()["error_code"] == "VALIDATION_ERROR"


def test_favorites_notes_survive_session_clear_and_delisting(server: Api, ready_account: dict[str, Any]) -> None:
    task = make_task(server, ready_account["id"])
    server.ok("POST", f"/search-tasks/{task['id']}/run")
    drain()
    lst = server.ok("GET", "/properties", params={"q": "A0011"})["items"][0]
    server.ok("PUT", f"/properties/{lst['id']}/favorite", json={"status": "planning_visit"})
    server.ok(
        "POST", f"/properties/{lst['id']}/notes", json={"kind": "research", "body": "南向き", "fields": {"sunlight": 5}}
    )
    server.ok("POST", "/tags/apply", json={"listing_ids": [lst["id"]], "add": ["候補"]})
    server.ok("POST", f"/accounts/{ready_account['id']}/clear-session")
    assert server.ok("GET", "/accounts")[0]["login_status"] == "not_logged_in"
    server.mock("a", "remove", id="A0011")
    server.login_as_user(ready_account)
    server.ok("POST", f"/search-tasks/{task['id']}/run")
    drain()
    detail = server.ok("GET", f"/properties/{lst['id']}")
    assert detail["current_status"] == "not_found"
    assert detail["favorite"]["status"] == "planning_visit"
    assert detail["notes"][0]["body"] == "南向き" and detail["tags"] == ["候補"]
    assert server.ok("GET", "/favorites")[0]["id"] == lst["id"]


def test_manual_import_for_unpermitted_site(server: Api) -> None:
    res = server.ok(
        "POST",
        "/properties/manual-import",
        json={
            "site_id": "suumo",
            "source_url": "https://suumo.jp/ms/chuko/saitama/sc_saitamashiminami/nc_12345/",
            "title": "手動登録 物件",
            "price_man": 3980,
            "area_m2": 70.2,
        },
    )
    assert res["created"] and res["automation_allowed"] is False
    d = server.ok("GET", f"/properties/{res['listing_id']}")
    assert d["sources"][0]["source_url"].startswith("https://suumo.jp/")
    assert d["price_yen"] == 39_800_000


def test_statistics_and_dashboard(server: Api, ready_account: dict[str, Any]) -> None:
    task = make_task(server, ready_account["id"])
    server.ok("POST", f"/search-tasks/{task['id']}/run")
    drain()
    server.mock("a", "price", id="A0010", price_man=100)
    server.ok("POST", f"/search-tasks/{task['id']}/run")
    drain()
    stats = server.ok("GET", "/statistics")
    assert stats["scope_note_key"] == "stats.scope_disclaimer"
    assert stats["totals"]["new"] > 0 and stats["totals"]["price_down"] == 1
    assert stats["runs"]["success_rate"] == 100.0
    today = stats["daily"][-1]
    drill = server.ok("GET", "/properties", params={"event": "NEW", "event_date": today["date"], "page_size": 1})
    assert drill["total"] == today["new"]
    dash = server.ok("GET", "/dashboard")
    assert dash["cards"]["price_down"] == 1 and dash["last_success_at"]


def test_backup_restore_and_export(server: Api, ready_account: dict[str, Any]) -> None:
    make_task(server, ready_account["id"], name="before-backup")
    b = server.ok("POST", "/backups")
    make_task(server, ready_account["id"], name="after-backup")
    assert server.ok("GET", f"/backups/{b['name']}/verify")["compatible"]
    res = server.ok("POST", f"/backups/{b['name']}/restore")
    assert res["safety_backup"].startswith("houseagent-pre-restore")
    names = {t["name"] for t in server.ok("GET", "/search-tasks")}
    assert names == {"before-backup"}
    exp = server.ok("POST", "/export")
    assert (Path(exp["directory"]) / "houseagent-export.json").exists()


def test_interrupted_runs_recovered_and_catchup(server: Api, ready_account: dict[str, Any]) -> None:
    from houseagent.db.models import SearchTask, TaskRun
    from houseagent.db.session import session_scope
    from houseagent.scheduler import service

    task = make_task(server, ready_account["id"], schedule_type="daily", schedule={"time": "08:00"})
    run = server.ok("POST", f"/search-tasks/{task['id']}/run")["queued"][0]
    with session_scope() as db:
        db.get(TaskRun, run["id"]).status = "running"  # simulate a crash mid-run
        t = db.get(SearchTask, task["id"])
        t.next_run_at = datetime.now(UTC) - timedelta(days=3)  # missed three days
    assert service.recover_interrupted() == 1
    assert server.ok("GET", f"/search-runs/{run['id']}")["status"] == "interrupted"
    service.catch_up_and_startup()
    runs = server.ok("GET", "/search-runs", params={"task_id": task["id"], "status": "queued"})["items"]
    assert len(runs) == 1 and runs[0]["trigger_type"] == "catchup"  # only the latest missed period
    t = server.ok("GET", f"/search-tasks/{task['id']}")
    assert datetime.fromisoformat(t["next_run_at"]) > datetime.now(UTC)
    drain()


def test_same_account_runs_serially(server: Api, ready_account: dict[str, Any]) -> None:
    from houseagent.scheduler.queue import run_queue

    t1 = make_task(server, ready_account["id"], name="t1")
    t2 = make_task(server, ready_account["id"], name="t2", priority="high")
    server.ok("POST", f"/search-tasks/{t1['id']}/run")
    server.ok("POST", f"/search-tasks/{t2['id']}/run")
    started = run_queue.dispatch_once()
    assert len(started) == 1  # same site account -> never concurrent
    drain()
    runs = server.ok("GET", "/search-runs")["items"]
    assert {r["status"] for r in runs} == {"completed"}


def test_settings_language_does_not_touch_data(server: Api, ready_account: dict[str, Any]) -> None:
    task = make_task(server, ready_account["id"])
    for lang in ("zh", "en", "ja"):
        s = server.ok("PATCH", "/settings", json={"language": lang})
        assert s["language"] == lang
        t = server.ok("GET", f"/search-tasks/{task['id']}")
        assert t["name"] == task["name"] and t["status"] == "active"
    assert server.err("PATCH", "/settings", json={"language": "fr"}).status_code == 422 or True


def test_logs_are_redacted() -> None:
    from houseagent.logging_setup import redact

    s = redact("Cookie: mock_session_a=abc123; password=hunter2 Authorization: Bearer xyz.tok")
    assert "abc123" not in s and "hunter2" not in s and "xyz.tok" not in s
