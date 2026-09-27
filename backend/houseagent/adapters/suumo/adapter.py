"""SUUMO adapter (used condos, used houses, land).

Only runs after the user accepted the automatic rules check (robots.txt + terms of use) for personal,
non-commercial use. Behaviour is deliberately polite: search-result pages only (never detail pages, images or
inquiry forms), 100 results per page, at least ``MIN_PAGE_INTERVAL_S`` between requests and a page cap.
Search results are public, so no SUUMO login is needed. All SUUMO-specific markup knowledge stays here.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterator
from html import unescape
from typing import Any
from urllib.parse import urlencode

from houseagent.adapters.base import (
    SUMMARY_FIELDS,
    AccountContext,
    Capabilities,
    LoginState,
    NormalizedSource,
    RulesInfo,
    RunContext,
    SearchPage,
    SiteAdapter,
)
from houseagent.browser_worker.session import PageResult, PlaywrightSession, open_session
from houseagent.errors import AdapterError, ErrorCode
from houseagent.regions import CITIES

BASE = "https://suumo.jp"
LIST_PATH = "/jj/bukken/ichiran/JJ010FJ001/"
MIN_PAGE_INTERVAL_S = 5.0
MAX_PAGES_PER_QUERY = 10
PER_PAGE = 100

BS = {"used_mansion": "011", "used_house": "021", "land": "030"}
BS_TO_TYPE = {v: k for k, v in BS.items()}
PRICE_STEPS = [
    500,
    1000,
    1500,
    2000,
    2500,
    3000,
    3500,
    4000,
    4500,
    5000,
    5500,
    6000,
    6500,
    7000,
    7500,
    8000,
    9000,
    10000,
    12000,
]
AREA_MIN_STEPS = [20, 30, 40, 50, 60, 70, 80, 90, 100]
AREA_MAX_STEPS = [40, 50, 60, 70, 80, 90, 100]
WALK_STEPS = [1, 3, 5, 7, 10, 15, 20]
LAYOUT_TO_MD = {
    "1R": "0",
    "1K": "1",
    "1DK": "1",
    "1LDK": "1",
    "2K": "2",
    "2DK": "2",
    "2LDK": "2",
    "3K": "3",
    "3DK": "3",
    "3LDK": "3",
    "4K": "4",
    "4DK": "4",
    "4LDK": "4",
    "5LDK+": "5",
}
UNLIMITED = "9999999"


def area_code(pref: str) -> str:
    """SUUMO region code (ar) for a JIS prefecture code."""
    n = int(pref)
    if n == 1:
        return "010"
    if n <= 7:
        return "020"
    if n <= 14:
        return "030"
    if n <= 20:
        return "040"
    if n <= 24:
        return "050"
    if n <= 30:
        return "060"
    if n <= 35:
        return "070"
    if n <= 39:
        return "080"
    return "090"


def _floor_step(value: float, steps: list[int]) -> int | None:
    lower = [s for s in steps if s <= value]
    return lower[-1] if lower else None


def _ceil_step(value: float, steps: list[int]) -> int | None:
    upper = [s for s in steps if s >= value]
    return upper[0] if upper else None


def parse_price_man(text: str | None) -> int | None:
    """'2599万円' / '1億2000万円' / '1億円' / '3980万円～4200万円' -> 万円 (lower bound); '未定' -> None."""
    if not text:
        return None
    m = re.search(r"(?:(\d+)億)?\s*(?:(\d[\d,]*(?:\.\d+)?)万)?円", text.replace("，", ","))
    if not m or not (m.group(1) or m.group(2)):
        return None
    oku = int(m.group(1)) if m.group(1) else 0
    man = float(m.group(2).replace(",", "")) if m.group(2) else 0.0
    return int(oku * 10000 + man)


def parse_area(text: str | None) -> float | None:
    if not text:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)\s*m", text)
    return float(m.group(1)) if m else None


def parse_layout(text: str | None) -> str | None:
    """'3LDK+S（納戸）' / '３ＳＬＤＫ' / 'ワンルーム' / '４ＤＫ' -> unified layout (S = storage room is ignored)."""
    if not text:
        return None
    t = unicodedata.normalize("NFKC", text).upper().replace(" ", "")
    if t.startswith("ワンルーム"):
        return "1R"
    t = re.sub(r"^(\d)S(?=LDK|DK|K)", r"\1", t)  # 3SLDK -> 3LDK
    m = re.match(r"(\d)(LDK|DK|K|R)", t)
    if not m:
        return None
    n, kind = int(m.group(1)), m.group(2)
    if n >= 5:
        return "5LDK+"
    layout = f"{n}{kind}"
    return layout if layout in LAYOUT_TO_MD else None


def parse_year(text: str | None) -> int | None:
    m = re.search(r"(\d{4})年", text or "")
    return int(m.group(1)) if m else None


def parse_station(text: str | None) -> tuple[str | None, int | None]:
    if not text:
        return None, None
    st = re.search(r"「([^」]+)」", text)
    walk = re.search(r"徒歩\s*(\d+)\s*分", text)
    return (st.group(1) if st else None), (int(walk.group(1)) if walk and "バス" not in text else None)


def city_from_address(address: str | None, pref: str | None) -> str | None:
    if not address:
        return None
    best: tuple[int, str] | None = None
    for code, pcode, ja, _zh, _en in CITIES:
        if pref and pcode != pref:
            continue
        if ja in address and (best is None or len(ja) > best[0]):
            best = (len(ja), code)
    return best[1] if best else None


_UNIT_START = re.compile(r'<div class="property_unit(?: property_unit--[a-z0-9_-]+)?\s*">')
_DT_DD = re.compile(r"<dt[^>]*>(.*?)</dt>\s*<dd[^>]*>(.*?)</dd>", re.S)
_TAGS = re.compile(r"<[^>]+>")


def _clean(fragment: str) -> str:
    return re.sub(r"\s+", " ", unescape(_TAGS.sub("", fragment))).strip()


class SuumoAdapter(SiteAdapter):
    site_id = "suumo"
    display_name = "SUUMO"
    base_url = BASE

    def get_capabilities(self) -> Capabilities:
        fields: dict[str, Any] = {
            "transaction_type": "supported",
            "prefectures": "supported",
            "cities": "supported",
            "stations": "unsupported",
            "price_min": "supported",
            "price_max": "supported",
            "area_min": "supported",
            "area_max": "supported",
            "building_age_max": "local_filter",
            "walk_minutes_max": "supported",
            "layouts": "supported",
            "keywords_include": "local_filter",
            "keywords_exclude": "local_filter",
            "sort_order": "local_filter",
            "result_limit": "supported",
        }
        return Capabilities(
            site_id=self.site_id,
            property_types=list(BS),
            fields=fields,
            retention_fields=list(SUMMARY_FIELDS),
            login_required=False,
            max_pages=MAX_PAGES_PER_QUERY,
            notes_key="site.suumo.notes",
            approximate_fields=["price_min", "price_max", "area_min", "area_max", "walk_minutes_max", "layouts"],
            min_page_interval_s=MIN_PAGE_INTERVAL_S,
            deals=["buy", "rent"],  # rentals: manual import / site link only for now
            links={"buy": f"{BASE}/ms/chuko/", "rent": f"{BASE}/chintai/"},
        )

    def rules_info(self) -> RulesInfo:
        return RulesInfo(
            robots_url=f"{BASE}/robots.txt",
            terms_urls=["https://cdn.p.recruit.co.jp/terms/suu-t-1003/index.html"],
            paths=[(f"{LIST_PATH}?ar=030&bs={bs}&ta=11&pc={PER_PAGE}", f"search_{t}") for t, bs in BS.items()],
        )

    # ------------------------------------------------------------------------------------------ login

    def check_login(self, account: AccountContext) -> LoginState:
        # Search results are public; a SUUMO login is optional and not verified automatically.
        return "manual_action_required"

    def open_login_page(self, account: AccountContext) -> None:
        s = open_session(account.profile_dir, BASE, 60, headless=False)
        try:
            s.goto(BASE + "/")
            if isinstance(s, PlaywrightSession):
                s.wait_until_closed(15 * 60, account.stop_event)
        finally:
            s.close()

    # ------------------------------------------------------------------------------------------ search

    def build_queries(self, cond: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
        """One query per (property type, prefecture): (bs, pref, params)."""
        types = self.automated_types(cond)
        queries = []
        for ptype in types:
            bs = BS[ptype]
            for pref in cond.get("prefectures") or []:
                params: dict[str, Any] = {"ar": area_code(pref), "bs": bs, "ta": pref, "pc": PER_PAGE}
                cities = [c for c in cond.get("cities") or [] if c.startswith(pref)]
                if cities:
                    params["sc"] = cities
                if cond.get("price_min") is not None:
                    kb = _floor_step(cond["price_min"], PRICE_STEPS)
                    if kb:
                        params["kb"] = kb
                if cond.get("price_max") is not None:
                    params["kt"] = _ceil_step(cond["price_max"], PRICE_STEPS) or UNLIMITED
                if bs == "011":  # 専有面積 filters exist on the condo search
                    if cond.get("area_min") is not None:
                        mb = _floor_step(cond["area_min"], AREA_MIN_STEPS)
                        if mb:
                            params["mb"] = mb
                    if cond.get("area_max") is not None:
                        params["mt"] = _ceil_step(cond["area_max"], AREA_MAX_STEPS) or UNLIMITED
                if bs in ("011", "021") and cond.get("layouts"):
                    params["md"] = sorted({LAYOUT_TO_MD[x] for x in cond["layouts"] if x in LAYOUT_TO_MD})
                if cond.get("walk_minutes_max") is not None:
                    params["et"] = _ceil_step(cond["walk_minutes_max"], WALK_STEPS) or UNLIMITED
                queries.append((bs, pref, params))
        return queries

    @staticmethod
    def list_url(params: dict[str, Any]) -> str:
        return f"{LIST_PATH}?{urlencode(params, doseq=True)}"

    def execute_search(
        self, account: AccountContext, conditions: dict[str, Any], ctx: RunContext
    ) -> Iterator[SearchPage]:
        queries = self.build_queries(conditions)
        seen = 0
        page_no = 0
        first_request = True
        with open_session(account.profile_dir, BASE, ctx.page_timeout_s) as s:
            for qi, (bs, pref, params) in enumerate(queries):
                url: str | None = self.list_url(params)
                pages_in_query = 0
                while url:
                    if not first_request:
                        ctx.wait(MIN_PAGE_INTERVAL_S)
                    first_request = False
                    ctx.checkpoint()
                    r = s.goto(url)
                    items, total, next_href = self._parse_page(r, bs, pref)
                    page_no += 1
                    pages_in_query += 1
                    seen += len(items)
                    truncated = seen >= ctx.result_limit or pages_in_query >= MAX_PAGES_PER_QUERY
                    more_here = next_href is not None and not truncated
                    last_query = qi == len(queries) - 1
                    yield SearchPage(
                        page_no=page_no,
                        items=items,
                        has_next=more_here or not last_query,
                        complete=not (truncated and next_href is not None),
                        total_hint=total if len(queries) == 1 else None,
                    )
                    if seen >= ctx.result_limit:
                        return
                    url = next_href if more_here else None

    def _parse_page(self, r: PageResult, bs: str, pref: str) -> tuple[list[NormalizedSource], int | None, str | None]:
        if r.status in (403, 429):
            raise AdapterError(ErrorCode.RATE_LIMITED, f"http {r.status}")
        if r.status >= 500:
            raise AdapterError(ErrorCode.NETWORK_ERROR, f"http {r.status}")
        text = r.text
        if re.search(r"captcha|recaptcha|ロボットではありません", text, re.I):
            raise AdapterError(ErrorCode.CAPTCHA_REQUIRED)
        raws, has_list = self._parse_units(text)
        hit = re.search(r'pagination_set-hit">\s*([\d,]+)', text)
        empty = "該当する物件がありません" in text or "条件に該当する物件はありません" in text
        if not has_list and not hit and not empty:
            raise AdapterError(
                ErrorCode.PAGE_CHANGED, "result list not found", diagnostic={"url": r.url, "length": len(text)}
            )
        nxt = re.search(r'<p class="pagination-parts"><a href="([^"]+)">\s*次へ', text)
        total = int(hit.group(1).replace(",", "")) if hit else (0 if empty else None)
        items = [self.normalize_listing({**raw, "bs": bs, "pref": pref}) for raw in raws]
        return items, total, unescape(nxt.group(1)) if nxt else None

    def _parse_units(self, text: str) -> tuple[list[dict[str, Any]], bool]:
        starts = [m.start() for m in _UNIT_START.finditer(text)]
        raws: list[dict[str, Any]] = []
        for i, start in enumerate(starts):
            chunk = text[start : starts[i + 1] if i + 1 < len(starts) else len(text)]
            link = re.search(r'property_unit-title">\s*<a href="([^"]+)"[^>]*>(.*?)</a>', chunk, re.S)
            fields = {_clean(k): _clean(v) for k, v in _DT_DD.findall(chunk)}
            raws.append(
                {
                    "href": link.group(1) if link else None,
                    "link_text": _clean(link.group(2)) if link else None,
                    "fields": fields,
                }
            )
        return raws, bool(starts) or 'id="js-bukkenList"' in text

    def parse_result_list(self, content: str) -> tuple[list[dict[str, Any]], bool]:
        raws, has_list = self._parse_units(content)
        if not has_list:
            raise AdapterError(ErrorCode.PAGE_CHANGED)
        return raws, "次へ" in content

    def normalize_listing(self, raw: dict[str, Any]) -> NormalizedSource:
        href = raw.get("href")
        m = re.search(r"nc_(\d+)", href or "")
        if not href or not m:
            raise AdapterError(ErrorCode.PAGE_CHANGED, "missing listing identifier")
        f: dict[str, str] = raw.get("fields") or {}
        bs = raw.get("bs", "011")
        ptype = BS_TO_TYPE.get(bs)
        station, walk = parse_station(f.get("沿線・駅"))
        land = parse_area(f.get("土地面積"))
        if ptype == "used_mansion":
            area = parse_area(f.get("専有面積"))
        elif ptype == "used_house":
            area = parse_area(f.get("建物面積"))
        else:
            area = land
        name = f.get("物件名") or raw.get("link_text")
        price = parse_price_man(f.get("販売価格") or f.get("価格"))
        return NormalizedSource(
            external_id=m.group(1),
            url=BASE + href if href.startswith("/") else href,
            title=name,
            property_type=ptype,
            prefecture=raw.get("pref"),
            city=city_from_address(f.get("所在地"), raw.get("pref")),
            address=f.get("所在地"),
            building_name=f.get("物件名") if ptype == "used_mansion" else None,
            price_yen=price * 10_000 if price is not None else None,
            area_m2=area,
            land_area_m2=land,
            layout=parse_layout(f.get("間取り")),
            built_year=parse_year(f.get("築年月")),
            station=station,
            walk_minutes=walk,
            raw_enums={"bs": bs, "price_text": f.get("販売価格"), "layout_text": f.get("間取り")},
        )

    def get_property_url(self, external_id: str) -> str:
        return f"{BASE}/{external_id.lstrip('/')}"
