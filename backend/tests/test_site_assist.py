"""Browser-assisted import for any site: mode switch, verification detection, user-added sites, generic reader."""

from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest

from houseagent.adapters.generic.extract import extract_listings
from houseagent.adapters.registry import get_adapter
from houseagent.db.models import Site
from houseagent.services import assisted, site_assist
from tests.conftest import Api, make_task


def _room(bk: str, rent: str, fee: str, layout: str, area: str) -> str:
    return (
        f'<tbody data-detailurl="/detail/bk-{bk}/"><tr><td>1階</td><td><span>{rent}</span>万円<br>{fee}</td>'
        f"<td>なし<br>1ヶ月</td><td>{layout}<br>{area}m²</td>"
        f'<td><a href="/detail/bk-{bk}/">詳細を見る</a></td></tr></tbody>'
    )


def _building(name: str, address: str, access: str, built: str, rooms: str) -> str:
    return (
        f'<section class="cassette"><h2><span>賃貸マンション</span>{name}</h2>'
        f"<table><tr><th>住所</th><td>{address} 周辺地図</td><th>交通</th><td><ul><li>{access}</li></ul></td></tr>"
        f"<tr><th>築年</th><td>{built}</td></tr></table>"
        "<table><thead><tr><th>階</th><th>家賃<br>管理費</th><th>敷金<br>礼金</th><th>間取り<br>専有面積</th></tr></thead>"
        f"{rooms}</table></section>"
    )


# CHINTAI-like: buildings with a shared header, one row per room, numbered pagination
RENT_PAGE = (
    "<p>検索結果 1,234件</p>"
    + _building(
        "サンプルハイツ",
        "埼玉県さいたま市南区別所3丁目",
        "埼京線/武蔵浦和駅 徒歩9分",
        "2009年12月（築16年）",
        _room("A000000001", "6.4", "5,000円", "1K", "24.92") + _room("A000000002", "7.1", "5,000円", "1LDK", "30.5"),
    )
    + _building(
        "レジデンス南",
        "埼玉県さいたま市南区沼影1丁目",
        "埼京線/武蔵浦和駅 徒歩4分",
        "2020年3月",
        _room("B000000001", "12.8", "10,000円", "2LDK", "55.1"),
    )
    + '<ul class="pager"><li class="current"><span>1</span></li><li><a href="/saitama/list/page2/">2</a></li></ul>'
)


def test_generic_reader_on_grouped_rent_page() -> None:
    page = extract_listings("https://www.example-chintai.jp/saitama/list/", RENT_PAGE, [])
    assert [i.external_id for i in page.items] == ["bk-A000000001", "bk-A000000002", "bk-B000000001"]
    first, second, third = page.items
    assert first.price_yen == 64_000 and first.layout == "1K" and first.area_m2 == 24.92
    # the building header is shared by its rooms
    assert second.address == "埼玉県さいたま市南区別所3丁目" and second.city == "11108" and second.prefecture == "11"
    assert second.walk_minutes == 9 and second.built_year == 2009 and "サンプルハイツ" in (second.title or "")
    assert third.property_type == "rent_apartment" and third.station == "武蔵浦和"
    assert page.total == 1234 and page.next_url == "https://www.example-chintai.jp/saitama/list/page2/"
    assert page.is_result_list and not page.verification


def test_generic_reader_recognises_verification_and_non_lists() -> None:
    v = extract_listings("https://x.example.jp/", "<title>認証にご協力ください</title><div id='captcha'></div>", [])
    assert v.verification and not v.is_result_list
    top = extract_listings("https://x.example.jp/", "<h1>トップ</h1><a href='/about/'>会社概要</a>", [])
    assert not top.verification and not top.is_result_list


class FakeBrowser:
    def __init__(self, url: str, html: str) -> None:
        self.url, self.html, self.opened = url, html, True

    def capture(self) -> tuple[str, str]:
        return self.url, self.html

    def is_open(self) -> bool:
        return self.opened

    def close(self) -> None:
        self.opened = False


@pytest.fixture()
def fake_browser(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(
        assisted,
        "browser_factory",
        lambda profile_dir, start_url: FakeBrowser("https://www.example-chintai.jp/saitama/list/", RENT_PAGE),
    )
    yield
    for sid in list(assisted._sessions):
        assisted.close(sid)


def test_user_added_site_end_to_end(server: Api, fake_browser: None) -> None:
    bad = server.err("POST", "/sites", json={"name": "x", "links": {"rent": "ftp://nope"}}).json()
    assert bad["message_key"] == "error.invalid_url"
    none = server.err("POST", "/sites", json={"name": "x", "links": {}}).json()
    assert none["message_key"] == "error.custom_site_link_required"
    site = server.ok(
        "POST", "/sites", json={"name": "サンプル賃貸", "links": {"rent": "https://www.example-chintai.jp/saitama/"}}
    )
    assert site["id"] == "custom_example_chintai_jp" and site["is_custom"] and site["assisted_mode"] == "on"
    assert site["capabilities"]["assisted"] and not site["capabilities"]["automation_available"]
    assert site["capabilities"]["deals"] == ["rent"] and site["base_url"] == "https://www.example-chintai.jp"
    task = make_task(
        server,
        None,
        site_id=site["id"],
        name="賃貸 南区",
        conditions={
            "deal_type": "rent",
            "prefectures": ["11"],
            "transaction_type": ["rent_apartment"],
            "price_max": 10,
        },
    )
    assert server.err("POST", f"/search-tasks/{task['id']}/run").json()["message_key"] == "error.assisted_only"
    s = server.ok("POST", f"/search-tasks/{task['id']}/assisted", json={"site_id": site["id"]})
    assert s["start_urls"][0]["url"] == "https://www.example-chintai.jp/saitama/"
    res = server.ok("POST", f"/assisted/{s['id']}/import")
    # 12.8万円 is above the task's 10万円 limit
    assert res["items"] == 3 and res["kept"] == 2 and res["new"] == 2 and res["total"] == 1234
    item = server.ok("GET", "/properties", params={"site_id": site["id"]})["items"][0]
    assert item["deal_type"] == "rent" and item["sources"][0]["property_type"] == "rent_apartment"
    server.ok("POST", f"/assisted/{s['id']}/close")
    # rename / remove: the history stays, the site leaves the list
    assert server.ok("PATCH", f"/sites/{site['id']}", json={"name": "改名"})["name"] == "改名"
    assert server.err("DELETE", "/sites/homes").json()["message_key"] == "error.not_custom_site"
    server.ok("DELETE", f"/sites/{site['id']}")
    assert site["id"] not in [x["id"] for x in server.ok("GET", "/sites")]
    assert server.ok("GET", "/properties", params={"site_id": site["id"]})["total"] == 2


def test_assisted_mode_switch_for_registered_site(server: Api) -> None:
    homes = server.ok("GET", "/sites/homes")
    assert homes["assisted_mode"] == "auto" and homes["capabilities"]["assisted"] is False
    task = make_task(server, None, site_id="homes", name="HOME'S", conditions={"prefectures": ["11"]})
    blocked = server.err("POST", f"/search-tasks/{task['id']}/run").json()
    assert blocked["message_key"] == "error.adapter_not_implemented"
    assert server.ok("PUT", "/sites/homes/assisted", json={"mode": "on"})["capabilities"]["assisted"] is True
    assert server.err("POST", f"/search-tasks/{task['id']}/run").json()["message_key"] == "error.assisted_only"
    assert server.ok("PUT", "/sites/homes/assisted", json={"mode": "off"})["capabilities"]["assisted"] is False
    server.err("PUT", "/sites/homes/assisted", json={"mode": "maybe"})
    # at home has a dedicated reader: offered in auto mode
    athome = server.ok("GET", "/sites/athome")
    assert athome["capabilities"]["assisted"] and athome["capabilities"]["dedicated_assisted"]


def test_probe_detects_verification_and_auto_mode() -> None:
    site = Site(id="homes", name="homes", base_url="https://www.homes.co.jp", adapter="homes", assisted_mode="auto")
    adapter = get_adapter("homes")
    assert not site_assist.assisted_available(site, adapter)

    def blocked(req: httpx.Request) -> httpx.Response:
        return httpx.Response(405, text="<html>認証にご協力ください。</html>")

    r = site_assist.probe(site, adapter, httpx.Client(transport=httpx.MockTransport(blocked)))
    assert r["detected"] and r["results"][0]["signal"] == "HTTP 405"
    assert site.verification_detected_at is not None and site_assist.assisted_available(site, adapter)

    def clean(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html><h1>中古マンション</h1></html>")

    r = site_assist.probe(site, adapter, httpx.Client(transport=httpx.MockTransport(clean)))
    assert not r["detected"] and r["reachable"] and site.verification_detected_at is None
    assert not site_assist.assisted_available(site, adapter)
    site.assisted_mode = "on"
    assert site_assist.assisted_available(site, adapter)
