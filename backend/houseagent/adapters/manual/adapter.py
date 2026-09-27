"""Registered sites without an automated-search adapter yet.

They can be chosen in tasks, used for manual import and "open the site" links; automated runs are refused with a
clear reason. A real adapter (like SUUMO's) replaces one of these once its pages and rules have been reviewed.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from houseagent.adapters.base import (
    CONDITION_FIELDS,
    SUMMARY_FIELDS,
    AccountContext,
    Capabilities,
    LoginState,
    NormalizedSource,
    RunContext,
    SearchPage,
    SiteAdapter,
)
from houseagent.browser_worker.session import PlaywrightSession, open_session
from houseagent.errors import AdapterError, ErrorCode


class ManualOnlyAdapter(SiteAdapter):
    def __init__(self, site_id: str, name: str, base_url: str, links: dict[str, str]) -> None:
        self.site_id = site_id
        self.display_name = name
        self.base_url = base_url
        self._links = links

    def get_capabilities(self) -> Capabilities:
        return Capabilities(
            site_id=self.site_id,
            property_types=[],
            fields={f: "unsupported" for f in CONDITION_FIELDS},
            retention_fields=list(SUMMARY_FIELDS),
            login_required=False,
            automation_available=False,
            deals=list(self._links),
            links=dict(self._links),
        )

    def check_login(self, account: AccountContext) -> LoginState:
        return "manual_action_required"

    def open_login_page(self, account: AccountContext) -> None:
        s = open_session(account.profile_dir, self.base_url, 60, headless=False)
        try:
            s.goto(self.base_url + "/")
            if isinstance(s, PlaywrightSession):
                s.wait_until_closed(15 * 60, account.stop_event)
        finally:
            s.close()

    def execute_search(
        self, account: AccountContext, conditions: dict[str, Any], ctx: RunContext
    ) -> Iterator[SearchPage]:
        raise AdapterError(ErrorCode.PERMISSION_BLOCKED, "adapter_not_implemented")
        yield  # pragma: no cover

    def parse_result_list(self, content: str) -> tuple[list[dict[str, Any]], bool]:
        raise AdapterError(ErrorCode.PAGE_CHANGED, "no adapter")

    def normalize_listing(self, raw: dict[str, Any]) -> NormalizedSource:
        raise AdapterError(ErrorCode.PAGE_CHANGED, "no adapter")


# site_id, display name, base URL, {deal: search entry URL}
REGISTERED_SITES: list[tuple[str, str, str, dict[str, str]]] = [
    (
        "homes",
        "LIFULL HOME'S",
        "https://www.homes.co.jp",
        {"buy": "https://www.homes.co.jp/mansion/chuko/", "rent": "https://www.homes.co.jp/chintai/"},
    ),
    (
        "yahoo_realestate",
        "Yahoo!不動産",
        "https://realestate.yahoo.co.jp",
        {"buy": "https://realestate.yahoo.co.jp/used/mansion/"},
    ),
    ("ouccino", "O-uccino（オウチーノ）", "https://o-uccino.com", {"buy": "https://o-uccino.com/"}),
    ("chintai", "CHINTAI", "https://www.chintai.net", {"rent": "https://www.chintai.net/"}),
    ("eheya", "いい部屋ネット", "https://www.eheya.net", {"rent": "https://www.eheya.net/"}),
    ("smocca", "スモッカ（Smocca）", "https://smocca.jp", {"rent": "https://smocca.jp/"}),
]
