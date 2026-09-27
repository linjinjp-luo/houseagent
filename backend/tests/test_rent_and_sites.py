"""Rental support and registered (manual-only) sites."""

from __future__ import annotations

from typing import Any

from tests.conftest import Api, drain, make_task

RENT = {"deal_type": "rent", "prefectures": ["11"], "result_limit": 200}


def test_rent_task_finds_only_rentals_with_fees(server: Api, ready_account: dict[str, Any]) -> None:
    task = make_task(
        server, ready_account["id"], name="rent", conditions={**RENT, "price_max": 10, "price_includes_fees": True}
    )
    run = server.ok("POST", f"/search-tasks/{task['id']}/run")["queued"][0]
    drain()
    r = server.ok("GET", f"/search-runs/{run['id']}")
    assert r["status"] == "completed" and r["result_count"] > 0
    items = server.ok("GET", "/properties", params={"run_id": run["id"], "page_size": 200})["items"]
    assert items and all(i["deal_type"] == "rent" and i["property_type"].startswith("rent_") for i in items)
    for i in items:  # rent + management fee within 10万円/月
        s = i["sources"][0]
        assert s["price_yen"] + (s["management_fee_yen"] or 0) <= 100_000
    assert any(i["sources"][0]["deposit_yen"] is not None for i in items)
    # buy and rent are kept apart in lists and statistics
    assert server.ok("GET", "/properties", params={"deal_type": "buy", "page_size": 1})["total"] == 0
    stats = server.ok("GET", "/statistics", params={"deal_type": "rent"})
    assert stats["totals"]["new"] == r["result_count"] and stats["price"]["distribution"][0]["bucket"] == "0-5"
    assert server.ok("GET", "/statistics")["totals"]["new"] == 0  # default is buy


def test_rent_change_is_tracked_as_price_change(server: Api, ready_account: dict[str, Any]) -> None:
    task = make_task(server, ready_account["id"], name="rent2", conditions=RENT)
    server.ok("POST", f"/search-tasks/{task['id']}/run")
    drain()
    first = server.ok("GET", "/properties", params={"deal_type": "rent", "page_size": 1})["items"][0]
    ext = first["sources"][0]["external_listing_id"]
    server.mock("a", "price", id=ext, price_man=1.5)
    server.ok("POST", f"/search-tasks/{task['id']}/run")
    drain()
    events = server.ok("GET", f"/properties/{first['id']}/history")["events"]
    assert events[-1]["event_type"] == "PRICE_DOWN" and events[-1]["new_price_yen"] == 15_000


def test_deal_validation(server: Api, ready_account: dict[str, Any]) -> None:
    base = {"name": "x", "sites": [{"site_id": "mock_a", "account_id": ready_account["id"]}]}
    r = server.err("POST", "/search-tasks", json={**base, "conditions": {**RENT, "transaction_type": ["used_mansion"]}})
    assert r.json()["error_code"] == "VALIDATION_ERROR"
    r = server.err(
        "POST",
        "/search-tasks",
        json={
            "name": "y",
            "conditions": {"prefectures": ["11"]},
            "sites": [{"site_id": "chintai", "account_id": None}],
        },
    )
    assert r.json()["message_key"] == "error.site_deal_mismatch"  # CHINTAI is rental only


def test_registered_sites_listed_with_links() -> None:
    from houseagent.adapters.registry import all_adapters

    caps = {a.site_id: a.get_capabilities() for a in all_adapters()}
    for sid in ("homes", "athome", "yahoo_realestate", "ouccino", "chintai", "eheya", "smocca"):
        assert sid in caps and not caps[sid].automation_available and caps[sid].links
    assert caps["suumo"].deals == ["buy", "rent"] and caps["homes"].deals == ["buy", "rent"]
    assert caps["chintai"].deals == ["rent"] and caps["athome"].deals == ["buy"]


def test_multi_site_task_runs_what_it_can_and_reports_skipped(server: Api, ready_account: dict[str, Any]) -> None:
    body = {
        "name": "multi",
        "conditions": {"prefectures": ["11"], "result_limit": 50},
        "sites": [{"site_id": "mock_a", "account_id": ready_account["id"]}, {"site_id": "homes", "account_id": None}],
    }
    task = server.ok("POST", "/search-tasks", json=body)
    assert task["validation"]["homes"]["manual_only"] is True  # nothing to acknowledge for manual-only sites
    res = server.ok("POST", f"/search-tasks/{task['id']}/run")
    assert [q["site_id"] for q in res["queued"]] == ["mock_a"]
    assert res["skipped"] == {"homes": "adapter_not_implemented"}
    drain()


def test_rent_on_suumo_is_refused_clearly(server: Api) -> None:
    task = make_task(server, None, site_id="suumo", name="suumo rent", conditions=RENT)
    body = server.err("POST", f"/search-tasks/{task['id']}/run").json()
    assert body["message_key"] == "error.deal_not_automated"


def test_manual_rent_import(server: Api) -> None:
    res = server.ok(
        "POST",
        "/properties/manual-import",
        json={
            "site_id": "chintai",
            "source_url": "https://www.chintai.net/detail/bk-000000000000000000000000/",
            "property_type": "rent_apartment",
            "title": "テスト賃貸",
            "price_man": 8.5,
            "management_fee_yen": 5000,
            "deposit_yen": 85000,
            "key_money_yen": 0,
        },
    )
    d = server.ok("GET", f"/properties/{res['listing_id']}")
    assert d["deal_type"] == "rent" and d["price_yen"] == 85_000
    assert d["sources"][0]["management_fee_yen"] == 5000 and d["sources"][0]["key_money_yen"] == 0
