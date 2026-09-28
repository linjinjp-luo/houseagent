"""FR-11 investment assessment and FR-12 AI services, end to end against a local mock of the AI APIs."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from houseagent.config import get_settings
from tests import mock_ai
from tests.conftest import Api, _free_port, drain, make_task

KEY = mock_ai.GOOD_KEY


@pytest.fixture(scope="module")
def ai_url() -> Iterator[str]:
    port = _free_port()
    srv = mock_ai.start(port)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True


@pytest.fixture(autouse=True)
def _reset_mock() -> None:
    mock_ai.MODE["value"] = "ok"
    mock_ai.SEEN.clear()


def _listing(api: Api, ext: str, price_man: float, area: float, **kw: Any) -> int:
    body = {
        "site_id": "homes",
        "source_url": f"https://www.homes.co.jp/mansion/b-{ext}/",
        "external_listing_id": ext,
        "property_type": "used_mansion",
        "prefecture": "11",
        "city": "11108",
        "address": "さいたま市南区別所3丁目",
        "price_man": price_man,
        "area_m2": area,
        "layout": "3LDK",
        "built_year": 1995,
        **kw,
    }
    return int(api.ok("POST", "/properties/manual-import", json=body)["listing_id"])


@pytest.fixture()
def world(server: Api) -> dict[str, Any]:
    """One target listing, three comparables (median 500,000 yen/m2), a rental profile and full inputs."""
    target = _listing(server, "T1", 2000, 60)
    for i, (p, a) in enumerate([(3000, 60), (2500, 50), (3500, 70)]):
        _listing(server, f"C{i}", p, a)
    profile = server.ok(
        "POST",
        "/investment-profiles",
        json={
            "name": "賃貸投資",
            "target_type": "rental",
            "thresholds": {"min_gross_yield_pct": 6, "min_net_yield_pct": 3},
            "assumptions": {
                "purchase_cost_rate_pct": 7,
                "sale_cost_rate_pct": 4,
                "vacancy_rate_pct": 5,
                "leasing_cost_months_per_year": 1,
            },
        },
    )
    server.ok(
        "PUT",
        f"/properties/{target}/investment-inputs",
        json={
            "monthly_rent_yen": {"value": 120000, "source": "近隣の募集事例（自分で調査）"},
            "management_fee_monthly_yen": {"value": 12000, "source": "販売図面"},
            "repair_reserve_monthly_yen": {"value": 10000, "source": "販売図面"},
            "property_tax_annual_yen": {"value": 80000, "source": "仲介会社の回答"},
            "insurance_annual_yen": {"value": 20000, "source": "保険見積"},
            "interior_condition": {"value": "average", "source": "内見"},
        },
    )
    return {"target": target, "profile": profile}


def _enable(api: Api, ai_url: str, ptype: str = "openai", **extra: Any) -> dict[str, Any]:
    api.ok("PATCH", "/settings", json={"ai_enabled": True})
    base = ai_url + "/v1" if ptype != "anthropic" else ai_url
    body = {"provider_type": ptype, "base_url": base, "api_key": KEY, "activate": True, **extra}
    body.setdefault("model_id", {"openai": "gpt-5", "anthropic": "claude-opus-5", "compatible": "local-model"}[ptype])
    return api.ok("POST", "/ai/providers", json=body)


def _assess(api: Api, listing_id: int, **body: Any) -> dict[str, Any]:
    return api.ok("POST", f"/properties/{listing_id}/investment-assessments", json=body)


def test_ai_off_keeps_rules_and_core_features(server: Api, world: dict[str, Any]) -> None:
    a = _assess(server, world["target"])
    assert a["status"] == "rules_only" and a["ai_error"] == "ai_disabled"
    assert a["primary_label"] == a["rule_label"] == "rental_candidate"
    calc = a["calculations"]["values"]
    assert calc["gross_yield_pct"]["value"] == 7.2 and calc["comparable_unit_price_yen_m2"]["value"] == 500000
    assert a["sources"]["user_inputs"]["monthly_rent_yen"]["source"] == "近隣の募集事例（自分で調査）"
    assert a["sources"]["comparables"]["n"] == 3
    assert not mock_ai.SEEN  # nothing left the machine
    page = server.ok("GET", "/properties", params={"inv_label": "rental_candidate"})
    assert page["total"] == 1 and page["items"][0]["investment"]["label"] == "rental_candidate"
    assert server.ok("GET", "/properties")["total"] == 4


def test_provider_switch_needs_no_business_change(server: Api, world: dict[str, Any], ai_url: str) -> None:
    oa = _enable(server, ai_url, "openai")
    assert oa["key_state"] == "configured" and oa["key_last4"] == KEY[-4:] and oa["active"]
    t = server.ok("POST", f"/ai/providers/{oa['id']}/test")
    assert t["ok"] and t["checks"]["structured_output"] == "ok"
    a1 = _assess(server, world["target"])
    assert a1["ai_error"] is None, a1["ai_error"]
    assert a1["status"] == "ai" and a1["model_provider"] == "openai" and a1["confidence"] == "medium"
    assert a1["prompt_version"] and a1["rule_version"] == "1.0.0"
    an = _enable(
        server,
        ai_url,
        "anthropic",
        pricing={"input_per_mtok": 5, "output_per_mtok": 25, "currency": "USD", "updated_at": "2026-09"},
    )
    a2 = _assess(server, world["target"])
    assert a2["status"] == "ai" and a2["model_provider"] == "anthropic"
    assert a2["model_version"] == "claude-opus-5-20260901"
    # only permitted structured data was sent: no address, no key in the body
    sent = json.dumps([s["body"] for s in mock_ai.SEEN], ensure_ascii=False)
    assert "別所" not in sent and KEY not in sent
    usage = server.ok("GET", "/ai/usage")
    anth = [g for g in usage["groups"] if g["provider_config_id"] == an["id"] and g["purpose"] == "assessment"]
    assert anth[0]["calls"] == 1 and anth[0]["estimated_cost"] == pytest.approx((1200 * 5 + 300 * 25) / 1e6)
    # the earlier FR-09 listing summary uses the same gateway (and whichever provider is active)
    summary = server.ok("POST", f"/properties/{world['target']}/ai-summary")
    assert summary["summary"] and summary["provider"] == "anthropic" and summary["ai_generated"]


def test_key_never_leaves_secure_storage(server: Api, world: dict[str, Any], ai_url: str) -> None:
    cfg = _enable(server, ai_url, "openai")
    _assess(server, world["target"])
    listing = server.ok("GET", "/ai/providers")
    assert KEY not in json.dumps(listing) and listing["items"][0]["key_last4"] == KEY[-4:]
    server.ok("POST", "/export")
    s = get_settings()
    from houseagent.services import backup

    backup.create_backup(s, "test")
    secret = KEY.encode()
    leaks = []
    for root in (s.data_dir,):
        for f in Path(root).rglob("*"):
            if f.is_file() and "secrets" not in f.parts and secret in f.read_bytes():
                leaks.append(str(f))
    assert leaks == []
    assert (s.config_dir / "secrets" / f"ai_provider_{cfg['id']}.dpapi").exists()
    # removing the key leaves the configuration without a usable key
    after = server.ok("DELETE", f"/ai/providers/{cfg['id']}/secret")
    assert after["key_state"] == "missing" and after["key_last4"] is None
    a = _assess(server, world["target"])
    assert a["status"] == "rules_only" and a["ai_error"] == "ai_key_missing"


def test_endpoint_checks(server: Api, ai_url: str) -> None:
    server.ok("PATCH", "/settings", json={"ai_enabled": True})

    def err(body: dict[str, Any]) -> str:
        return str(server.err("POST", "/ai/providers", json=body).json()["message_key"])

    assert err({"provider_type": "compatible", "base_url": "http://ai.example.com/v1"}) == "error.ai_url_https_required"
    assert err({"provider_type": "openai", "base_url": "https://u:p@api.example.com/v1"}) == "error.ai_url_invalid"
    assert err({"provider_type": "compatible", "model_id": "m"}) == "error.ai_url_required"
    assert err({"provider_type": "nope"}) == "error.ai_provider_unknown"
    ok = server.ok(
        "POST",
        "/ai/providers",
        json={"provider_type": "compatible", "base_url": "https://gw.example.com/v1", "model_id": "m", "api_key": KEY},
    )
    assert ok["key_state"] == "configured"
    # pointing the configuration at another host drops the key: it is never sent to an unconfirmed target
    moved = server.ok("POST", "/ai/providers", json={"id": ok["id"], "base_url": "https://other.example.com/v1"})
    assert moved["key_state"] == "missing"
    # redirects are not followed
    cfg = _enable(server, ai_url, "openai")
    mock_ai.MODE["value"] = "redirect"
    t = server.ok("POST", f"/ai/providers/{cfg['id']}/test")
    assert not t["ok"] and t["code"] == "ai_redirect_blocked"
    assert not any("steal" in json.dumps(s) for s in mock_ai.SEEN[1:])


def test_wrong_key_and_bad_output(server: Api, world: dict[str, Any], ai_url: str) -> None:
    cfg = _enable(server, ai_url, "anthropic", api_key="sk-ant-wrongwrongwrongwrong1234")
    t = server.ok("POST", f"/ai/providers/{cfg['id']}/test")
    assert t["code"] == "ai_auth" and t["provider"]["last_test_status"] == "ai_auth"
    server.ok("POST", "/ai/providers", json={"id": cfg["id"], "api_key": KEY})
    mock_ai.MODE["value"] = "fabricate"
    a = _assess(server, world["target"])
    # an invented rent is never stored as a formal assessment
    assert a["status"] == "ai_rejected" and a["ai_error"] == "ai_output_invalid:untraceable_number"
    assert a["primary_label"] == a["rule_label"] and a["confidence"] is None
    mock_ai.MODE["value"] = "not_json"
    b = _assess(server, world["target"])
    assert b["status"] == "rules_only" and b["ai_error"] == "ai_bad_response"


def test_insufficient_data_does_not_call_ai(server: Api, world: dict[str, Any], ai_url: str) -> None:
    _enable(server, ai_url, "openai")
    server.ok("PUT", f"/properties/{world['target']}/investment-inputs", json={"monthly_rent_yen": None})
    a = _assess(server, world["target"])
    assert a["primary_label"] == "insufficient_data" and "monthly_rent_yen" in a["missing_fields"]
    assert a["ai_error"] == "insufficient_data" and not mock_ai.SEEN
    assert any(s["key"] == "next.monthly_rent_yen" for s in a["calculations"]["next_steps"])


def test_limits_batch_and_cancel(server: Api, world: dict[str, Any], ai_url: str) -> None:
    cfg = _enable(server, ai_url, "openai", limits={"daily_call_limit": 1, "batch_max": 2, "concurrency": 1})
    assert _assess(server, world["target"])["status"] == "ai"
    limited = _assess(server, world["target"])
    assert limited["status"] == "rules_only" and limited["ai_error"] == "ai_daily_limit"
    ids = [world["target"]] + [x["id"] for x in server.ok("GET", "/properties")["items"]]
    too_many = server.err("POST", "/properties/investment-assessments:batch", json={"listing_ids": ids})
    assert too_many.json()["message_key"] == "error.ai_batch_too_large"
    est = server.ok(
        "POST", "/properties/investment-assessments:batch", json={"listing_ids": ids[:2], "estimate_only": True}
    )
    assert est["total"] == 2 and est["remaining_calls_today"] == 0 and est["batch_max"] == 2
    server.ok("POST", "/ai/providers", json={"id": cfg["id"], "limits": {"daily_call_limit": 50}})
    server.ok("PUT", f"/properties/{world['target']}/favorite", json={"status": "watching"})
    job = server.ok("POST", "/properties/investment-assessments:batch", json={"scope": "favorites"})
    assert job["total"] == 1 and job["trigger"] == "favorites"
    for _ in range(100):
        cur = server.ok("GET", "/investment-batches/current")
        if cur["status"] != "running":
            break
        time.sleep(0.1)
    assert cur["status"] == "completed" and cur["done"] == 1


def test_override_history_and_stale(server: Api, world: dict[str, Any]) -> None:
    a = _assess(server, world["target"])
    bad = server.err(
        "PATCH", f"/investment-assessments/{a['id']}/override", json={"user_label": "low_value", "reason": " "}
    )
    assert bad.json()["message_key"] == "error.override_reason_required"
    o = server.ok(
        "PATCH",
        f"/investment-assessments/{a['id']}/override",
        json={"user_label": "low_value", "user_tags": ["holding_cost_high"], "reason": "大規模修繕が近い"},
    )
    assert o["final_label"] == "low_value" and o["primary_label"] == "rental_candidate"
    assert o["overrides"][0]["reason"] == "大規模修繕が近い"
    listed = server.ok("GET", "/properties", params={"inv_label": "low_value"})
    assert listed["items"][0]["investment"]["overridden"] is True
    hist = server.ok("GET", f"/properties/{world['target']}/investment-assessments")
    assert hist["items"][0]["stale"] is False
    server.ok(
        "PUT",
        f"/properties/{world['target']}/investment-inputs",
        json={"monthly_rent_yen": {"value": 90000, "source": "再調査"}},
    )
    assert server.ok("GET", f"/properties/{world['target']}/investment-assessments")["items"][0]["stale"] is True
    b = _assess(server, world["target"])
    hist = server.ok("GET", f"/properties/{world['target']}/investment-assessments")["items"]
    assert [h["id"] for h in hist] == [b["id"], a["id"]] and hist[1]["final_label"] == "low_value"
    assert b["primary_label"] == "low_value" and b["input_snapshot_hash"] != a["input_snapshot_hash"]


def test_rent_listing_is_not_assessed(server: Api) -> None:
    lid = _listing(server, "R1", 9.5, 30, property_type="rent_apartment")
    server.ok("POST", "/investment-profiles", json={"name": "p"})
    r = server.err("POST", f"/properties/{lid}/investment-assessments", json={})
    assert r.json()["message_key"] == "error.investment_buy_only"


def test_assess_after_run_is_off_by_default_and_works(server: Api, ready_account: dict[str, Any]) -> None:
    server.ok("POST", "/investment-profiles", json={"name": "既定"})
    plain = make_task(server, ready_account["id"], name="plain")
    assert plain["assess_after_run"] is False
    task = make_task(server, ready_account["id"], name="assess", assess_after_run=True)
    server.ok("POST", f"/search-tasks/{task['id']}/run")
    drain()
    for _ in range(100):
        cur = server.ok("GET", "/investment-batches/current")
        if cur and cur["status"] != "running":
            break
        time.sleep(0.1)
    assert cur["trigger"] == "after_run" and cur["done"] > 0
    assessed = server.ok("GET", "/properties", params={"inv_label": "insufficient_data,owner_candidate,low_value"})
    assert assessed["total"] > 0
