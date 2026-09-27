"""Browser-assisted import (at home). A fake browser stands in for the visible window the user drives."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from houseagent.adapters.athome.adapter import AthomeAdapter
from houseagent.browser_worker.locks import account_locks
from houseagent.services import assisted
from tests.conftest import Api, make_task


def _card(nid: str, kind: str, label: str, price: str, rows: dict[str, str]) -> str:
    cells = "".join(
        f'<div class="property-detail-table__block"><strong>{k}</strong><span>{v}</span></div>' for k, v in rows.items()
    )
    return (
        f'<div class="card-box open"><div class="card-box-open"><a href="/{kind}/{nid}/?DOWN=1&amp;sref=list">'
        f'<ul class="icon-list"><li class="square-icon fwb square-icon--blue">{label}</li></ul>'
        f'<div class="title-wrap__title-text">サンプル{nid}</div>'
        f'<div class="property-price">{price}<span class="fs14">万円</span></div>'
        f'<div class="property-detail-table">{cells}</div></a></div></div>'
    )


RESULT = (
    "<span>該当物件数 <b>42</b> 件</span>"
    + _card(
        "1111111111",
        "kodate",
        "中古一戸建て",
        "3,480",
        {
            "間取り": "４ＬＤＫ",
            "築年月": "2005年3月（築21年）",
            "土地面積": "120.5m²",
            "建物面積": "98.2m²",
            "所在地": "さいたま市南区別所",
            "交通": "ＪＲ埼京線 「武蔵浦和」駅 徒歩9分",
        },
    )
    + _card(
        "2222222222",
        "kodate",
        "中古一戸建て",
        "9,800",
        {
            "間取り": "５ＬＤＫ",
            "築年月": "2015年1月",
            "土地面積": "200m²",
            "建物面積": "130m²",
            "所在地": "さいたま市南区沼影",
            "交通": "ＪＲ埼京線 「武蔵浦和」駅 徒歩5分",
        },
    )
    + '<div class="pagination"><ul class="pagination__list">'
    '<li class="pagination__list-item pagination__list-item--current"><a href="/kodate/chuko/saitama/list/">1</a></li>'
    '<li class="pagination__list-item"><a href="/kodate/chuko/saitama/list/page2/">2</a></li></ul></div>'
)
VERIFY = "<title>認証にご協力ください。</title><script>initGeetest({})</script>"


class FakeBrowser:
    def __init__(self, pages: list[tuple[str, str]]) -> None:
        self.pages = pages
        self.opened = True

    def capture(self) -> tuple[str, str]:
        return self.pages.pop(0) if len(self.pages) > 1 else self.pages[0]

    def is_open(self) -> bool:
        return self.opened

    def close(self) -> None:
        self.opened = False


@pytest.fixture()
def fake_browser(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[FakeBrowser]]:
    made: list[FakeBrowser] = []
    url = "https://www.athome.co.jp/kodate/chuko/saitama/list/"

    def factory(profile_dir: Path, start_url: str) -> FakeBrowser:
        b = FakeBrowser([(url, VERIFY), (url, RESULT)])
        made.append(b)
        return b

    monkeypatch.setattr(assisted, "browser_factory", factory)
    yield made
    for sid in list(assisted._sessions):
        assisted.close(sid)


def _task(api: Api, **conds: Any) -> dict[str, Any]:
    return make_task(
        api,
        None,
        site_id="athome",
        name="at home",
        conditions={"prefectures": ["11"], "transaction_type": ["used_house"], **conds},
    )


def test_run_now_points_to_assisted_import(server: Api) -> None:
    task = _task(server)
    body = server.err("POST", f"/search-tasks/{task['id']}/run").json()
    assert body["message_key"] == "error.assisted_only"
    assert body["details"]["reasons"] == {"athome": "assisted_only"}


def test_assisted_import_flow(server: Api, fake_browser: list[FakeBrowser]) -> None:
    task = _task(server, price_max=5000)
    s = server.ok("POST", f"/search-tasks/{task['id']}/assisted", json={"site_id": "athome"})
    assert s["open"] and s["start_urls"][0]["url"].endswith("/kodate/chuko/saitama/list/")
    # the user has not completed the verification yet: nothing is read, clear message
    first = server.err("POST", f"/assisted/{s['id']}/import").json()
    assert first["message_key"] == "error.assisted_verification_pending"
    # after the user completed it, the result list is imported (task conditions filter locally)
    res = server.ok("POST", f"/assisted/{s['id']}/import")
    assert res["items"] == 2 and res["kept"] == 1 and res["new"] == 1 and res["total"] == 42
    assert res["next_url"] == "https://www.athome.co.jp/kodate/chuko/saitama/list/page2/"
    run = server.ok("GET", f"/search-runs/{res['run_id']}")
    assert run["trigger_type"] == "assisted" and run["status"] == "completed" and run["skipped_count"] == 1
    item = server.ok("GET", "/properties", params={"site_id": "athome"})["items"][0]
    src = item["sources"][0]
    assert src["external_listing_id"] == "1111111111" and src["price_yen"] == 34_800_000
    assert src["layout"] == "4LDK" and src["prefecture"] == "11" and src["city"] == "11108"
    assert src["walk_minutes"] == 9 and src["source_url"] == "https://www.athome.co.jp/kodate/1111111111/"
    # importing the same page again adds nothing new
    again = server.ok("POST", f"/assisted/{s['id']}/import")
    assert again["new"] == 0 and again["kept"] == 1
    # starting again while open reuses the same window
    assert server.ok("POST", f"/search-tasks/{task['id']}/assisted", json={"site_id": "athome"})["id"] == s["id"]
    server.ok("POST", f"/assisted/{s['id']}/close")
    assert fake_browser[0].opened is False
    assert not any(account_locks.holder(k) == "assisted" for k in list(account_locks._held))


def test_parser_on_synthetic_page() -> None:
    page = AthomeAdapter().parse_assisted_page("https://www.athome.co.jp/kodate/chuko/saitama/list/", RESULT)
    assert [i.external_id for i in page.items] == ["1111111111", "2222222222"]
    assert page.items[1].layout == "5LDK+" and page.items[0].land_area_m2 == 120.5
    assert AthomeAdapter().parse_assisted_page("u", VERIFY).verification


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("３ＳＬＤＫ", "3LDK"),
        ("3LDK+S（納戸）", "3LDK"),
        ("ワンルーム", "1R"),
        ("３Ｋ", "3K"),
        ("４ＤＫ", "4DK"),
        ("６ＬＤＫ", "5LDK+"),
        ("2LDK", "2LDK"),
        ("-", None),
    ],
)
def test_layout_normalisation(text: str, expected: str | None) -> None:
    from houseagent.adapters.suumo.adapter import parse_layout

    assert parse_layout(text) == expected


def test_land_and_condo_cards() -> None:
    land = (
        '<div class="card-box-open"><a href="/tochi/3900000001/?DOWN=1">'
        '<li class="square-icon fwb square-icon--green">建築条件付き土地</li>'
        '<div class="title-wrap__title-text">分譲地サンプル</div>'
        '<div class="property-price">2,890<span>万</span><span>円</span>・2,960<span>万</span><span>円</span></div>'
        "<strong>土地面積</strong><span><span>172.49m²</span><span>(52.17坪)</span></span>"
        "<strong>交通</strong><span>ＪＲ京浜東北線 「北浦和」駅 バス11分 「市営アパート」 停歩10分</span></a></div>"
    )
    condo = (
        '<div class="card-box-open"><a href="/mansion/1162062526/?DOWN=1">'
        '<li class="square-icon fwb square-icon--blue">中古マンション</li>'
        '<div class="title-wrap__title-text">サンプル壱番館 ４階 ２Ｋ</div>'
        '<div class="property-price">148<span>万円</span></div>'
        "<strong>専有面積</strong><span>29.47m²</span><strong>階建</strong><span>5階建 / 4階</span>"
        "<strong>間取り</strong><span>２Ｋ</span>"
        "<strong>交通</strong><span>東武越生線 「東毛呂」駅 徒歩8分</span></a></div>"
    )
    p = AthomeAdapter().parse_assisted_page("https://www.athome.co.jp/tochi/saitama/list/", land + condo)
    lnd, cnd = p.items
    assert lnd.property_type == "land" and lnd.price_yen == 28_900_000 and lnd.area_m2 == 172.49
    assert lnd.walk_minutes is None and lnd.prefecture == "11"  # bus access is not a walk time
    assert cnd.property_type == "used_mansion" and cnd.floor == 4 and cnd.layout == "2K" and cnd.walk_minutes == 8
