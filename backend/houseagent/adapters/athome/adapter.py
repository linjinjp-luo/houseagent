"""at home (アットホーム) adapter - browser-assisted import only.

at home answers automated browsers with a human-verification page (GeeTest CAPTCHA, HTTP 405). HouseAgent never
solves or bypasses it, so there is no automated search. Instead the user browses in a visible window (completing
any verification themselves) and HouseAgent reads the result list the user has open when they click "import".
Only summary fields are read; photos, descriptions and agent details are not.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from html import unescape
from typing import Any

from houseagent.adapters.base import (
    CONDITION_FIELDS,
    SUMMARY_FIELDS,
    AccountContext,
    AssistedPage,
    Capabilities,
    LoginState,
    NormalizedSource,
    RunContext,
    SearchPage,
    SiteAdapter,
)
from houseagent.adapters.suumo.adapter import (
    city_from_address,
    parse_area,
    parse_layout,
    parse_price_man,
    parse_station,
    parse_year,
)
from houseagent.browser_worker.session import PlaywrightSession, open_session
from houseagent.errors import AdapterError, ErrorCode
from houseagent.regions import PREFECTURES

BASE = "https://www.athome.co.jp"
# property type -> (URL path prefix of the list, path segment of detail links)
TYPE_PATHS = {
    "used_house": ("kodate/chuko", "kodate"),
    "used_mansion": ("mansion/chuko", "mansion"),
    "land": ("tochi", "tochi"),
}
DETAIL_TO_TYPE = {"kodate": "used_house", "mansion": "used_mansion", "tochi": "land"}
LABEL_TO_TYPE = {
    "中古一戸建て": "used_house",
    "中古マンション": "used_mansion",
    "土地": "land",
    "売地": "land",
    "建築条件付き土地": "land",
}

_NG = re.compile(r' _ng[a-z]+-[A-Za-z0-9-]+=""')
_TAGS = re.compile(r"<[^>]+>")
# Every listing type (house, condo, land) wraps its card body in this element.
_CARD = re.compile(r'<div class="card-box-open">')
_ROW = re.compile(r"<strong>\s*([^<]+?)\s*</strong>\s*<span[^>]*>(.*?)</span>", re.S)


def _clean(fragment: str) -> str:
    return re.sub(r"\s+", " ", unescape(_TAGS.sub("", fragment))).strip()


def pref_slug(code: str) -> str:
    """at home uses lower-case romaji prefecture names in its URLs (e.g. saitama, tokyo)."""
    for pcode, _ja, _zh, en in PREFECTURES:
        if pcode == code:
            return en.lower()
    return "tokyo"


def pref_code_from_url(url: str) -> str | None:
    """/kodate/chuko/saitama/list/ -> "11" (at home addresses often omit the prefecture)."""
    for pcode, _ja, _zh, en in PREFECTURES:
        if f"/{en.lower()}/" in url:
            return pcode
    return None


def pref_code_from_address(address: str | None) -> str | None:
    if not address:
        return None
    for pcode, ja, _zh, _en in PREFECTURES:
        if address.startswith(ja) or address.startswith(ja.rstrip("都府県")):
            return pcode
    return None


class AthomeAdapter(SiteAdapter):
    site_id = "athome"
    display_name = "at home"
    base_url = BASE

    def get_capabilities(self) -> Capabilities:
        return Capabilities(
            site_id=self.site_id,
            property_types=[],  # no automated search
            fields={f: "unsupported" for f in CONDITION_FIELDS},
            retention_fields=list(SUMMARY_FIELDS),
            login_required=False,
            automation_available=False,
            assisted=True,
            deals=["buy"],
            links={"buy": f"{BASE}/mansion/chuko/"},
            notes_key="site.athome.notes",
        )

    # ------------------------------------------------------------------------------------------ assisted

    def assisted_start_urls(self, conditions: dict[str, Any]) -> list[tuple[str, str]]:
        types = conditions.get("transaction_type") or list(TYPE_PATHS)
        prefs = conditions.get("prefectures") or ["13"]
        return [(t, f"{BASE}/{TYPE_PATHS[t][0]}/{pref_slug(p)}/list/") for t in types if t in TYPE_PATHS for p in prefs]

    def parse_assisted_page(self, url: str, html: str) -> AssistedPage:
        text = _NG.sub("", html)
        if "initGeetest" in text or "認証にご協力ください" in text:
            return AssistedPage(items=[], verification=True, is_result_list=False)
        starts = [m.start() for m in _CARD.finditer(text)]
        hit = re.search(r"該当物件数\s*(?:<[^>]+>\s*)*([\d,]+)\s*(?:<[^>]+>\s*)*件", text)
        if not starts:
            empty = "該当する物件がありません" in text or (hit is not None and hit.group(1) == "0")
            return AssistedPage(items=[], total=0 if empty else None, is_result_list=empty)
        items: list[NormalizedSource] = []
        seen: set[str] = set()
        for i, start in enumerate(starts):
            chunk = text[start : starts[i + 1] if i + 1 < len(starts) else len(text)]
            item = self._parse_card(chunk, pref_code_from_url(url))
            if item is not None and item.external_id not in seen:
                seen.add(item.external_id)
                items.append(item)
        nxt = self._next_url(text, url)
        return AssistedPage(items=items, total=int(hit.group(1).replace(",", "")) if hit else None, next_url=nxt)

    def _parse_card(self, chunk: str, url_pref: str | None = None) -> NormalizedSource | None:
        link = re.search(r'href="(/(kodate|mansion|tochi)/(\d+)/[^"]*)"', chunk)
        if not link:
            return None
        ptype = DETAIL_TO_TYPE[link.group(2)]
        label = re.search(r'square-icon[^"]*">\s*([^<]+?)\s*<', chunk)
        if label and label.group(1) in LABEL_TO_TYPE:
            ptype = LABEL_TO_TYPE[label.group(1)]
        title = re.search(r'<div class="title-wrap__title-text">([^<]+)</div>', chunk)
        price = re.search(r'class="property-price">\s*(.*?)</div>', chunk, re.S)
        rows = {_clean(k): _clean(v) for k, v in _ROW.findall(chunk)}
        address = rows.get("所在地")
        pref = pref_code_from_address(address) or url_pref
        station, walk = parse_station(rows.get("交通"))
        land = parse_area(rows.get("土地面積"))
        area = parse_area(rows.get("専有面積") or rows.get("建物面積")) or (land if ptype == "land" else None)
        # condo rows look like "4階建 / 4階" (building floors / this unit's floor); titles often contain "4階"
        floor = re.search(r"/\s*(\d+)\s*階\s*$", rows.get("階建", "")) or re.search(
            r"(\d+)\s*階(?!建)", title.group(1) if (title and ptype == "used_mansion") else ""
        )
        price_man = parse_price_man(_clean(price.group(1)) if price else None)
        return NormalizedSource(
            external_id=link.group(3),
            url=BASE + f"/{link.group(2)}/{link.group(3)}/",
            title=_clean(title.group(1)) if title else None,
            property_type=ptype,
            prefecture=pref,
            city=city_from_address(address, pref),
            address=address,
            price_yen=price_man * 10_000 if price_man is not None else None,
            area_m2=area,
            land_area_m2=land,
            layout=parse_layout(rows.get("間取り")),
            floor=int(floor.group(1)) if floor else None,
            built_year=parse_year(rows.get("築年月")),
            station=station,
            walk_minutes=walk,
            raw_enums={"type_label": label.group(1) if label else None},
        )

    @staticmethod
    def _next_url(text: str, current: str) -> str | None:
        pages = re.findall(r'<li class="pagination__list-item[^"]*">\s*<a href="([^"]+)">\s*(\d+)\s*</a>', text)
        cur = re.search(r'pagination__list-item--current">\s*<a href="[^"]*">\s*(\d+)', text)
        if not pages or not cur:
            return None
        want = int(cur.group(1)) + 1
        for href, n in pages:
            if int(n) == want:
                return BASE + unescape(href) if href.startswith("/") else unescape(href)
        return None

    # ------------------------------------------------------------------------------------------ no automation

    def check_login(self, account: AccountContext) -> LoginState:
        return "manual_action_required"

    def open_login_page(self, account: AccountContext) -> None:
        s = open_session(account.profile_dir, BASE, 60, headless=False)
        try:
            s.goto(BASE + "/")
            if isinstance(s, PlaywrightSession):
                s.wait_until_closed(15 * 60, account.stop_event)
        finally:
            s.close()

    def execute_search(
        self, account: AccountContext, conditions: dict[str, Any], ctx: RunContext
    ) -> Iterator[SearchPage]:
        raise AdapterError(ErrorCode.PERMISSION_BLOCKED, "assisted_only")
        yield  # pragma: no cover

    def parse_result_list(self, content: str) -> tuple[list[dict[str, Any]], bool]:
        raise AdapterError(ErrorCode.PAGE_CHANGED, "use parse_assisted_page")

    def normalize_listing(self, raw: dict[str, Any]) -> NormalizedSource:
        raise AdapterError(ErrorCode.PAGE_CHANGED, "use parse_assisted_page")

    def get_property_url(self, external_id: str) -> str:
        return f"{BASE}/{external_id.lstrip('/')}"
