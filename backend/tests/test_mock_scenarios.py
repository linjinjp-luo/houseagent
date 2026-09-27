"""The nine mock-site acceptance scenarios (spec 15.8) plus related acceptance items (spec 11)."""

from __future__ import annotations

from typing import Any

from tests.conftest import Api, drain, make_task


def _run(api: Api, task_id: int) -> dict[str, Any]:
    queued = api.ok("POST", f"/search-tasks/{task_id}/run")["queued"]
    assert queued, "nothing queued"
    drain()
    return api.ok("GET", f"/search-runs/{queued[0]['id']}")


def test_login_success_and_multipage_import(server: Api, ready_account: dict[str, Any]) -> None:
    acc = server.ok("POST", f"/accounts/{ready_account['id']}/check-login")
    assert acc["login_status"] == "valid"
    task = make_task(server, ready_account["id"])
    run = _run(server, task["id"])
    assert run["status"] == "completed", run
    assert run["pages"] >= 2  # multi-page results
    assert run["result_count"] == run["new_count"] > 20
    props = server.ok("GET", "/properties", params={"page_size": 200})
    assert props["total"] == run["result_count"]
    # trigger, condition version and account alias are traceable
    assert run["trigger_type"] == "manual" and run["condition_version"] == 1 and run["account_alias"] == "個人A"


def test_repeat_search_creates_no_duplicates(server: Api, ready_account: dict[str, Any]) -> None:
    task = make_task(server, ready_account["id"])
    first = _run(server, task["id"])
    second = _run(server, task["id"])
    assert second["status"] == "completed"
    assert second["new_count"] == 0 and second["changed_count"] == 0
    assert server.ok("GET", "/properties", params={"page_size": 200})["total"] == first["result_count"]


def test_login_expired_pauses_and_needs_user(server: Api, ready_account: dict[str, Any]) -> None:
    task = make_task(server, ready_account["id"])
    server.mock("a", "scenario", scenario="login_expired")
    run = _run(server, task["id"])
    assert run["status"] == "paused" and run["error_code"] == "AUTH_REQUIRED"
    assert run["retry_count"] == 0  # never retried automatically
    acc = server.ok("GET", "/accounts")[0]
    assert acc["login_status"] == "relogin_required"
    kinds = {n["kind"] for n in server.ok("GET", "/notifications")}
    assert "login_required" in kinds


def test_no_results(server: Api, ready_account: dict[str, Any]) -> None:
    task = make_task(server, ready_account["id"])
    server.mock("a", "scenario", scenario="no_results")
    run = _run(server, task["id"])
    assert run["status"] == "completed" and run["result_count"] == 0


def test_price_change_history_and_not_sold(server: Api, ready_account: dict[str, Any]) -> None:
    task = make_task(server, ready_account["id"])
    _run(server, task["id"])
    server.mock("a", "price", id="A0001", price_man=1234)
    server.mock("a", "remove", id="A0008")
    server.mock("a", "availability", id="A0003", available="false")
    run = _run(server, task["id"])
    assert run["changed_count"] >= 2 and run["not_found_count"] == 1
    found = server.ok("GET", "/properties", params={"q": "A0001"})["items"]
    lst = found[0]
    history = server.ok("GET", f"/properties/{lst['id']}/history")
    types = [e["event_type"] for e in history["events"]]
    assert types[0] == "NEW" and types[-1] in ("PRICE_DOWN", "PRICE_UP")
    assert history["events"][-1]["new_price_yen"] == 12_340_000
    assert len(history["snapshots"]) == 2  # snapshot only on new + change
    gone = server.ok("GET", "/properties", params={"q": "A0008"})["items"][0]
    # Missing from results is NOT_FOUND ("not observed"), never "sold" / deleted
    assert gone["current_status"] == "not_found"
    assert gone["sources"][0]["observation_status"] == "not_found"
    ended = server.ok("GET", "/properties", params={"q": "A0003"})["items"][0]
    assert ended["current_status"] == "unavailable"


def test_reappeared(server: Api, ready_account: dict[str, Any]) -> None:
    task = make_task(server, ready_account["id"])
    _run(server, task["id"])
    server.mock("a", "availability", id="A0005", available="false")
    _run(server, task["id"])
    server.mock("a", "availability", id="A0005", available="true")
    _run(server, task["id"])
    lst = server.ok("GET", "/properties", params={"q": "A0005"})["items"][0]
    types = [e["event_type"] for e in server.ok("GET", f"/properties/{lst['id']}/history")["events"]]
    assert types == ["NEW", "UNAVAILABLE", "REAPPEARED"]


def test_duplicate_listing_in_results(server: Api, ready_account: dict[str, Any]) -> None:
    task = make_task(server, ready_account["id"])
    server.mock("a", "scenario", scenario="duplicates")
    run = _run(server, task["id"])
    assert run["status"] == "completed" and run["skipped_count"] >= 2
    assert server.ok("GET", "/properties", params={"page_size": 200})["total"] == run["result_count"]


def test_page_timeout_retries_then_fails(server: Api, ready_account: dict[str, Any]) -> None:
    task = make_task(server, ready_account["id"])
    server.mock("a", "scenario", scenario="timeout", delay_s=3)
    run = _run(server, task["id"])
    assert run["error_code"] == "PAGE_TIMEOUT" and run["status"] == "failed"
    assert run["retry_count"] == 2  # two retries, then give up
    assert any(e["message_key"] == "log.retry_scheduled" for e in run["logs"])
    server.mock("a", "scenario", scenario="normal")


def test_structure_change_stops_adapter(server: Api, ready_account: dict[str, Any]) -> None:
    task = make_task(server, ready_account["id"])
    server.mock("a", "scenario", scenario="structure_changed")
    run = _run(server, task["id"])
    assert run["status"] == "failed" and run["error_code"] == "PAGE_CHANGED"
    site = server.ok("GET", "/sites/mock_a")
    assert site["adapter_status"] == "stopped" and site["adapter_diagnostic"]
    server.mock("a", "scenario", scenario="normal")
    blocked = _run(server, task["id"])
    assert blocked["error_code"] == "PAGE_CHANGED"  # stays stopped until the user re-enables
    server.ok("POST", "/sites/mock_a/adapter/reenable")
    assert _run(server, task["id"])["status"] == "completed"


def test_captcha_requires_manual_action(server: Api, ready_account: dict[str, Any]) -> None:
    task = make_task(server, ready_account["id"])
    server.mock("a", "scenario", scenario="captcha")
    run = _run(server, task["id"])
    assert run["status"] == "paused" and run["error_code"] == "CAPTCHA_REQUIRED" and run["retry_count"] == 0
    # remembered for the site (offers browser-assisted import on real sites; never on local mock sites)
    site = server.ok("GET", "/sites/mock_a")
    assert site["verification_detected_at"] and site["verification_probe"]["source"] == "run"
    assert site["capabilities"]["assisted"] is False


def test_rate_limited_is_not_retried(server: Api, ready_account: dict[str, Any]) -> None:
    task = make_task(server, ready_account["id"])
    server.mock("a", "scenario", scenario="rate_limited")
    run = _run(server, task["id"])
    assert run["error_code"] == "RATE_LIMITED" and run["retry_count"] == 0


def test_three_consecutive_failures_pause_task(server: Api, ready_account: dict[str, Any]) -> None:
    task = make_task(server, ready_account["id"])
    server.mock("a", "scenario", scenario="captcha")
    for _ in range(3):
        _run(server, task["id"])
    t = server.ok("GET", f"/search-tasks/{task['id']}")
    assert t["status"] == "paused" and t["paused_reason"] == "consecutive_failures"


def test_unpermitted_site_cannot_start(server: Api) -> None:
    acc = server.ok(
        "POST", "/accounts", json={"site_id": "suumo", "account_alias": "個人 SUUMO", "auto_search_enabled": True}
    )
    task = make_task(server, acc["id"], site_id="suumo")
    r = server.err("POST", f"/search-tasks/{task['id']}/run")
    assert r.json()["error_code"] == "PERMISSION_BLOCKED"


def test_account_auto_search_disabled_blocks_run(server: Api) -> None:
    acc = server.ok("POST", "/accounts", json={"site_id": "mock_a", "account_alias": "off"})
    server.login_as_user(acc)
    task = make_task(server, acc["id"])
    run = _run(server, task["id"])
    assert run["error_code"] == "PERMISSION_BLOCKED" and run["status"] == "paused"


def test_cancel_queued_run(server: Api, ready_account: dict[str, Any]) -> None:
    task = make_task(server, ready_account["id"])
    run = server.ok("POST", f"/search-tasks/{task['id']}/run")["queued"][0]
    cancelled = server.ok("POST", f"/search-runs/{run['id']}/cancel")
    assert cancelled["status"] == "cancelled"
    drain()
    assert server.ok("GET", f"/search-runs/{run['id']}")["result_count"] == 0


def test_local_secondary_filter_on_site_b(server: Api) -> None:
    acc = server.ok("POST", "/accounts", json={"site_id": "mock_b", "account_alias": "B", "auto_search_enabled": True})
    server.login_as_user(acc, "b")
    validation = server.ok(
        "POST",
        "/search-tasks/validate",
        json={
            "conditions": {"prefectures": ["11"], "layouts": ["3LDK"], "stations": [{"name": "浦和"}]},
            "site_ids": ["mock_b"],
        },
    )["mock_b"]
    assert "layouts" in validation["local_filters"] and "stations" in validation["unsupported"]
    # Saving with an unsupported condition requires explicit acknowledgement - never silently ignored.
    r = server.err(
        "POST",
        "/search-tasks",
        json={
            "name": "B",
            "conditions": {"prefectures": ["11"], "stations": [{"name": "浦和"}]},
            "sites": [{"site_id": "mock_b", "account_id": acc["id"]}],
        },
    )
    assert r.json()["error_code"] == "CONDITION_UNSUPPORTED"
    task = make_task(
        server, acc["id"], site_id="mock_b", name="B 3LDK", conditions={"prefectures": ["11"], "layouts": ["3LDK"]}
    )
    run = _run(server, task["id"])
    items = server.ok("GET", "/properties", params={"run_id": run["id"], "page_size": 200})["items"]
    assert items and all(i["layout"] == "3LDK" for i in items)
    assert run["skipped_count"] > 0


def test_cross_site_match_requires_confirmation(server: Api, ready_account: dict[str, Any]) -> None:
    acc_b = server.ok(
        "POST", "/accounts", json={"site_id": "mock_b", "account_alias": "B", "auto_search_enabled": True}
    )
    server.login_as_user(acc_b, "b")
    _run(server, make_task(server, ready_account["id"], name="A")["id"])
    _run(server, make_task(server, acc_b["id"], site_id="mock_b", name="B")["id"])
    cands = server.ok("GET", "/match-candidates")
    assert cands, "expected suspected cross-site duplicates"
    before = server.ok("GET", "/properties", params={"page_size": 1})["total"]
    merged = server.ok("POST", f"/match-candidates/{cands[0]['id']}/confirm")
    assert len(merged["site_ids"]) == 2
    assert server.ok("GET", "/properties", params={"page_size": 1})["total"] == before - 1
    # unlinking keeps both sources and their history
    src = next(s for s in merged["sources"] if s["site_id"] == "mock_b")
    split = server.ok("POST", f"/sources/{src['id']}/unlink")
    assert split["sources"][0]["id"] == src["id"]
    assert server.ok("GET", "/properties", params={"page_size": 1})["total"] == before
