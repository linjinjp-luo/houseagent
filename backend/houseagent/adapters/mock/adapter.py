"""Adapter for the built-in mock property sites - the formal V1.0 acceptance target."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlencode

from houseagent.adapters.base import (
    SUMMARY_FIELDS,
    AccountContext,
    Capabilities,
    LoginState,
    NormalizedSource,
    RunContext,
    SearchPage,
    SiteAdapter,
)
from houseagent.browser_worker.session import BrowserSession, PageResult, open_session
from houseagent.errors import AdapterError, ErrorCode
from houseagent.runtime import state as runtime


class _ResultParser(HTMLParser):
    """Collects ``li.ha-item`` records and their ``span.ha-*`` fields."""

    def __init__(self) -> None:
        super().__init__()
        self.has_results_list = False
        self.items: list[dict[str, Any]] = []
        self.next_href: str | None = None
        self.login_form = False
        self.captcha = False
        self.total: int | None = None
        self._current: dict[str, Any] | None = None
        self._field: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        cls = a.get("class") or ""
        if tag == "ul" and a.get("id") == "results":
            self.has_results_list = True
        elif tag == "form" and a.get("id") == "login-form":
            self.login_form = True
        elif tag == "div" and a.get("id") == "ha-count":
            raw = a.get("data-total")
            self.total = int(raw) if raw and raw.isdigit() else None
        elif tag == "form" and a.get("id") == "captcha":
            self.captcha = True
        elif tag == "li" and "ha-item" in cls:
            self._current = {"id": a.get("data-id")}
        elif tag == "a" and "ha-next" in cls:
            self.next_href = a.get("href")
        elif self._current is not None and tag == "a" and "ha-link" in cls:
            self._current["href"] = a.get("href")
            self._field = "title"
        elif self._current is not None and tag == "span" and cls.startswith("ha-"):
            self._field = cls[3:]

    def handle_endtag(self, tag: str) -> None:
        if tag in ("span", "a"):
            self._field = None
        elif tag == "li" and self._current is not None:
            self.items.append(self._current)
            self._current = None

    def handle_data(self, data: str) -> None:
        if self._current is not None and self._field:
            self._current[self._field] = self._current.get(self._field, "") + data


def _num(v: Any, kind: type = float) -> Any:
    if v in (None, "", "None"):
        return None
    try:
        return kind(float(v)) if kind is int else kind(v)
    except (TypeError, ValueError):
        return None


class MockSiteAdapter(SiteAdapter):
    def __init__(self, variant: str) -> None:
        self.variant = variant
        self.site_id = f"mock_{variant}"
        self.display_name = "モック不動産A" if variant == "a" else "モック不動産B"
        self.base_url = f"/mock-site/{variant}"

    # ---------------------------------------------------------------------------------------- capability

    def get_capabilities(self) -> Capabilities:
        full = self.variant == "a"
        fields: dict[str, Any] = {
            "transaction_type": "supported",
            "prefectures": "supported",
            "cities": "supported" if full else "local_filter",
            "stations": "unsupported",
            "price_min": "supported",
            "price_max": "supported",
            "area_min": "supported" if full else "local_filter",
            "area_max": "supported" if full else "local_filter",
            "building_age_max": "supported" if full else "local_filter",
            "walk_minutes_max": "local_filter",
            "layouts": "supported" if full else "local_filter",
            "keywords_include": "supported" if full else "local_filter",
            "keywords_exclude": "local_filter",
            "sort_order": "local_filter",
            "result_limit": "supported",
        }
        rent_types = ["rent_apartment", "rent_house"] if full else []
        return Capabilities(
            site_id=self.site_id,
            property_types=["used_mansion", "used_house", "land", *rent_types],
            fields=fields,
            retention_fields=list(SUMMARY_FIELDS),
            max_pages=25,
            deals=["buy", "rent"] if full else ["buy"],
            links={
                "buy": f"{self.base_url}/search?type=used_mansion,used_house,land",
                **({"rent": f"{self.base_url}/search?type=rent_apartment,rent_house"} if full else {}),
            },
        )

    # ---------------------------------------------------------------------------------------- session

    def _session(self, account: AccountContext, timeout_s: float = 20.0, headless: bool = True) -> BrowserSession:
        return open_session(account.profile_dir, runtime.origin, timeout_s, headless=headless)

    def check_login(self, account: AccountContext) -> LoginState:
        with self._session(account) as s:
            r = s.goto(f"{self.base_url}/api/session")
        m = re.search(r"\{.*\}", r.text, re.S)
        try:
            data = json.loads(m.group(0)) if m else {}
        except ValueError:
            return "unknown"
        return "valid" if data.get("logged_in") else "expired"

    def open_login_page(self, account: AccountContext) -> None:
        from houseagent.browser_worker.session import PlaywrightSession

        s = self._session(account, timeout_s=60, headless=False)
        try:
            s.goto(f"{self.base_url}/login")
            if isinstance(s, PlaywrightSession):
                s.wait_until_closed(15 * 60, account.stop_event)
        finally:
            s.close()

    # ---------------------------------------------------------------------------------------- search

    def build_query(self, submit: dict[str, Any], page: int) -> str:
        q: dict[str, Any] = {"page": page}
        if submit.get("transaction_type"):
            q["type"] = ",".join(submit["transaction_type"])
        if submit.get("prefectures"):
            q["pref"] = ",".join(submit["prefectures"])
        if submit.get("cities"):
            q["city"] = ",".join(submit["cities"])
        if submit.get("price_min") is not None:
            q["pmin"] = submit["price_min"]
        if submit.get("price_max") is not None:
            q["pmax"] = submit["price_max"]
        if submit.get("area_min") is not None:
            q["amin"] = submit["area_min"]
        if submit.get("area_max") is not None:
            q["amax"] = submit["area_max"]
        if submit.get("layouts"):
            q["layout"] = ",".join(submit["layouts"])
        if submit.get("building_age_max") is not None:
            q["age"] = submit["building_age_max"]
        if submit.get("keywords_include"):
            q["kw"] = ",".join(submit["keywords_include"])
        return f"{self.base_url}/search?{urlencode(q)}"

    def _check_page_state(self, r: PageResult, parser: _ResultParser) -> None:
        if r.status == 429:
            raise AdapterError(ErrorCode.RATE_LIMITED)
        if r.status >= 500:
            raise AdapterError(ErrorCode.NETWORK_ERROR, f"http {r.status}")
        if parser.login_form:
            raise AdapterError(ErrorCode.AUTH_REQUIRED)
        if parser.captcha:
            raise AdapterError(ErrorCode.CAPTCHA_REQUIRED)
        if not parser.has_results_list:
            raise AdapterError(
                ErrorCode.PAGE_CHANGED, "result list not found", diagnostic={"url": r.url, "length": len(r.text)}
            )

    def execute_search(
        self, account: AccountContext, conditions: dict[str, Any], ctx: RunContext
    ) -> Iterator[SearchPage]:
        submit = self.validate_conditions(conditions).submit
        submit["transaction_type"] = self.automated_types(conditions)  # only this task's deal type
        caps = self.get_capabilities()
        seen = 0
        with self._session(account, timeout_s=ctx.page_timeout_s) as s:
            page_no = 1
            url: str | None = self.build_query(submit, page_no)
            while url:
                ctx.checkpoint()
                r = s.goto(url)
                parser = self._parse(r.text)
                self._check_page_state(r, parser)
                items = [self.normalize_listing(raw) for raw in parser.items]
                seen += len(items)
                truncated = seen >= ctx.result_limit or page_no >= caps.max_pages
                has_next = parser.next_href is not None and not truncated
                yield SearchPage(
                    page_no=page_no,
                    items=items,
                    has_next=has_next,
                    complete=not (truncated and parser.next_href is not None),
                    total_hint=parser.total,
                )
                if not has_next:
                    break
                page_no += 1
                url = parser.next_href

    def _parse(self, content: str) -> _ResultParser:
        p = _ResultParser()
        p.feed(content)
        return p

    def parse_result_list(self, content: str) -> tuple[list[dict[str, Any]], bool]:
        p = self._parse(content)
        if not p.has_results_list:
            raise AdapterError(ErrorCode.PAGE_CHANGED)
        return p.items, p.next_href is not None

    def normalize_listing(self, raw: dict[str, Any]) -> NormalizedSource:
        ext = (raw.get("id") or "").strip()
        href = raw.get("href")
        if not ext or not href:
            # Never submit a record with an incomplete key identifier.
            raise AdapterError(ErrorCode.PAGE_CHANGED, "missing listing identifier")
        price_man = _num(raw.get("price"))  # float: rents are e.g. 8.5万円
        ptype = (raw.get("type") or "").strip() or None
        return NormalizedSource(
            external_id=ext,
            url=href,
            deal_type="rent" if (ptype or "").startswith("rent_") else "buy",
            management_fee_yen=_num(raw.get("mgmt"), int),
            deposit_yen=_num(raw.get("deposit"), int),
            key_money_yen=_num(raw.get("key"), int),
            title=(raw.get("title") or "").strip() or None,
            property_type=ptype,
            prefecture=(raw.get("pref") or "").strip() or None,
            city=(raw.get("city") or "").strip() or None,
            address=(raw.get("address") or "").strip() or None,
            building_name=(raw.get("building") or "").strip() or None,
            price_yen=round(price_man * 10_000) if price_man is not None else None,
            area_m2=_num(raw.get("area")),
            land_area_m2=_num(raw.get("land")),
            layout=(raw.get("layout") or "").strip() or None,
            floor=_num(raw.get("floor"), int),
            built_year=_num(raw.get("built"), int) or None,
            station=(raw.get("station") or "").strip() or None,
            walk_minutes=_num(raw.get("walk"), int),
            availability="unavailable" if (raw.get("status") or "").strip() == "ended" else "available",
            raw_enums={"type": raw.get("type"), "status": raw.get("status"), "price_man": raw.get("price")},
        )

    def get_property_url(self, external_id: str) -> str:
        return f"{self.base_url}/property/{external_id}"
