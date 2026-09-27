"""Automatic rules check, anonymous (no-account) search, and the SUUMO result parser.

Nothing here contacts a real website: the rules check runs against the mock site's own fictional
robots.txt / terms, and the SUUMO parser is exercised on a synthetic page with SUUMO's markup structure.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from houseagent.adapters import registry
from houseagent.adapters.base import Capabilities, RulesInfo
from houseagent.adapters.mock.adapter import MockSiteAdapter
from houseagent.adapters.suumo import adapter as suumo
from houseagent.browser_worker.session import PageResult
from houseagent.errors import AdapterError
from houseagent.runtime import state as runtime
from tests.conftest import Api, drain, make_task


class PublicRulesMockB(MockSiteAdapter):
    """Mock site B behaving like a public site with published rules (no login needed)."""

    def __init__(self) -> None:
        super().__init__("b")

    def get_capabilities(self) -> Capabilities:
        caps = super().get_capabilities()
        caps.login_required = False
        return caps

    def rules_info(self) -> RulesInfo:
        o = runtime.origin
        return RulesInfo(
            robots_url=f"{o}/mock-site/b/robots.txt",
            terms_urls=[f"{o}/mock-site/b/terms"],
            paths=[("/mock-site/b/search?pref=11", "search_used_mansion")],
        )

    def build_query(self, submit: dict[str, Any], page: int) -> str:
        return super().build_query(submit, page) + "&public=1"


@pytest.fixture()
def public_b(server: Api) -> Iterator[None]:
    original = registry.get_adapter("mock_b")
    registry.register(PublicRulesMockB())
    server.ok("PUT", "/sites/mock_b/permissions/browser_automation", json={"status": "unknown"})
    try:
        yield
    finally:
        registry.register(original)


def test_rules_check_warns_then_user_can_continue(server: Api, public_b: None) -> None:
    task = make_task(server, None, site_id="mock_b", name="public B")  # no account: site needs no login
    refused = server.err("POST", f"/search-tasks/{task['id']}/run").json()
    assert refused["message_key"] == "error.rules_check_required"
    assert refused["details"]["reasons"] == {"mock_b": "rules_check_required"}

    check = server.ok("POST", "/sites/mock_b/rules-check")
    assert check["status"] == "warning"
    assert check["robots_findings"] == [
        {"path": "/mock-site/b/search?pref=11", "purpose": "search_used_mansion", "allowed": False}
    ]
    cats = {f["category"] for f in check["terms_findings"]}
    assert {"automation", "commercial", "private_use"} <= cats
    assert any("スクレイピング" in f["snippet"] for f in check["terms_findings"])

    site = server.ok("POST", f"/sites/mock_b/rules-check/{check['id']}/accept")
    perm = site["permissions"]["browser_automation"]
    assert perm["status"] == "allowed" and perm["source"].startswith("rules_check:")
    assert site["permissions"]["commercial_use"]["status"] == "denied"
    assert server.ok("GET", "/sites/mock_b/rules-check")["accepted_at"]

    run = server.ok("POST", f"/search-tasks/{task['id']}/run")["queued"][0]
    drain()
    done = server.ok("GET", f"/search-runs/{run['id']}")
    assert done["status"] == "completed" and done["result_count"] > 0  # searched anonymously, no account


def test_rules_changed_revokes_permission(server: Api, public_b: None) -> None:
    from datetime import timedelta

    from houseagent.db.models import SiteRuleCheck, utcnow
    from houseagent.db.session import session_scope
    from houseagent.services import site_rules

    check = server.ok("POST", "/sites/mock_b/rules-check")
    server.ok("POST", f"/sites/mock_b/rules-check/{check['id']}/accept")
    with session_scope() as db:  # pretend the accepted check is old and the rules changed since
        c = db.get(SiteRuleCheck, check["id"])
        c.checked_at = utcnow() - timedelta(days=8)
        c.content_hash = "old-fingerprint"
    site_rules.recheck_accepted_sites()
    site = server.ok("GET", "/sites/mock_b")
    assert site["permissions"]["browser_automation"]["status"] == "unknown"
    assert "rules_changed" in {n["kind"] for n in server.ok("GET", "/notifications")}


def test_login_required_site_still_needs_account(server: Api) -> None:
    task = make_task(server, None, name="no account A")  # mock A requires login
    run = server.ok("POST", f"/search-tasks/{task['id']}/run")["queued"][0]
    drain()
    r = server.ok("GET", f"/search-runs/{run['id']}")
    assert r["error_code"] == "AUTH_REQUIRED" and r["error_detail"] == "no_account"


# ------------------------------------------------------------------------------------------ SUUMO parser


def _unit(nc: str, path: str, fields: dict[str, str], osusume: bool = False) -> str:
    dls = "".join(f"<dl><dt>{k}</dt><dd>{v}</dd></dl>" for k, v in fields.items())
    cls = "property_unit property_unit--osusume" if osusume else "property_unit "
    return (
        f'<div class="{cls}"><div class="property_unit-header"><h2 class="property_unit-title">'
        f'<a href="{path}nc_{nc}/" target="_blank">広告コピー</a></h2></div>'
        f'<div class="dottable dottable--cassette">{dls}</div></div>'
    )


SUUMO_PAGE = (
    '<div id="js-bukkenList" class="property_unit_group">'
    + _unit(
        "21490834",
        "/ms/chuko/saitama/sc_saitamashiminami/",
        {
            "物件名": "サンプル武蔵浦和",
            "販売価格": '<span class="dottable-value">1億2000万円</span>',
            "所在地": "埼玉県さいたま市南区別所１",
            "沿線・駅": "ＪＲ埼京線「武蔵浦和」徒歩7分",
            "専有面積": "71.2m<sup>2</sup>（21.53坪）（壁芯）",
            "間取り": "3LDK+S（納戸）",
            "築年月": "2004年3月",
        },
        osusume=True,
    )
    + _unit(
        "21578118",
        "/ms/chuko/saitama/sc_saitamashiminami/",
        {
            "物件名": "サンプル南浦和",
            "販売価格": "3980万円～4280万円",
            "所在地": "埼玉県さいたま市南区南浦和２",
            "沿線・駅": "ＪＲ京浜東北線「南浦和」バス10分",
            "専有面積": "60.1m2",
            "間取り": "2LDK",
            "築年月": "1995年1月",
        },
    )
    + '</div><div class="pagination_set"><div class="pagination_set-hit">132<span>件</span></div>'
    '<p class="pagination-parts">'
    '<a href="/jj/bukken/ichiran/JJ010FJ001/?ar=030&amp;bs=011&amp;ta=11&amp;page=2">次へ</a></p>'
    "</div>"
)


def test_suumo_parser_reads_summary_fields() -> None:
    a = suumo.SuumoAdapter()
    items, total, nxt = a._parse_page(PageResult(url="u", status=200, text=SUUMO_PAGE), "011", "11")
    assert total == 132 and nxt == "/jj/bukken/ichiran/JJ010FJ001/?ar=030&bs=011&ta=11&page=2"
    first, second = items
    assert first.external_id == "21490834" and first.url.startswith("https://suumo.jp/ms/chuko/")
    assert first.price_yen == 120_000_000 and first.area_m2 == 71.2 and first.layout == "3LDK"
    assert first.city == "11108" and first.built_year == 2004 and first.property_type == "used_mansion"
    assert (first.station, first.walk_minutes) == ("武蔵浦和", 7)
    assert second.price_yen == 39_800_000 and second.walk_minutes is None  # bus access is not a walk time


def test_suumo_page_states() -> None:
    a = suumo.SuumoAdapter()
    with pytest.raises(AdapterError) as e:
        a._parse_page(PageResult(url="u", status=200, text="<html><body>redesigned</body></html>"), "011", "11")
    assert e.value.code.value == "PAGE_CHANGED"
    with pytest.raises(AdapterError) as e:
        a._parse_page(PageResult(url="u", status=429, text=""), "011", "11")
    assert e.value.code.value == "RATE_LIMITED"
    items, total, nxt = a._parse_page(
        PageResult(url="u", status=200, text="<p>該当する物件がありません</p>"), "011", "11"
    )
    assert items == [] and total == 0 and nxt is None


def test_suumo_query_building_snaps_to_site_steps() -> None:
    q = suumo.SuumoAdapter().build_queries(
        {
            "prefectures": ["11", "13"],
            "cities": ["11108"],
            "transaction_type": ["used_mansion", "land"],
            "price_min": 1200,
            "price_max": 4200,
            "area_min": 55,
            "layouts": ["3LDK", "2LDK"],
            "walk_minutes_max": 12,
        }
    )
    assert len(q) == 4  # 2 types x 2 prefectures
    bs, pref, params = q[0]
    assert (bs, pref) == ("011", "11")
    assert params["sc"] == ["11108"] and params["kb"] == 1000 and params["kt"] == 4500
    assert params["mb"] == 50 and params["md"] == ["2", "3"] and params["et"] == 15 and params["ar"] == "030"
    land = [p for b, _, p in q if b == "030"][0]
    assert "mb" not in land and "md" not in land  # condo-only filters are applied locally for land
