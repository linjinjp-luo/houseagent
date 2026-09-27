"""Site adapter contract (spec 6.2 / 15.8).

An adapter only knows how to operate one site's pages and parse its fields. Validation, uniqueness,
matching, change detection and history are done by the generic listing services - adapters never write
to the database.
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from houseagent.errors import AdapterError, ErrorCode

FieldSupport = Literal["supported", "local_filter", "unsupported"]
LoginState = Literal["valid", "expired", "unknown", "manual_action_required"]

# Unified condition fields (spec 15.4) an adapter must classify in its capability table.
CONDITION_FIELDS = (
    "transaction_type",
    "prefectures",
    "cities",
    "stations",
    "price_min",
    "price_max",
    "area_min",
    "area_max",
    "building_age_max",
    "walk_minutes_max",
    "layouts",
    "keywords_include",
    "keywords_exclude",
    "sort_order",
    "result_limit",
)

# Condition keys that describe the task itself rather than a site search parameter.
NON_SEARCH_KEYS = frozenset({"deal_type", "region_logic", "price_includes_fees"})

# Summary fields a source record may hold. Anything not permitted by the site's retention rules is dropped.
SUMMARY_FIELDS = (
    "deal_type",
    "management_fee_yen",
    "deposit_yen",
    "key_money_yen",
    "title",
    "property_type",
    "prefecture",
    "city",
    "address",
    "building_name",
    "price_yen",
    "area_m2",
    "land_area_m2",
    "layout",
    "floor",
    "built_year",
    "station",
    "walk_minutes",
)


@dataclass
class Capabilities:
    site_id: str
    property_types: list[str]
    fields: dict[str, FieldSupport]
    # Summary fields the site allows us to keep (data retention scope).
    retention_fields: list[str]
    login_required: bool = True
    max_pages: int = 25
    notes_key: str | None = None
    # False when the adapter has no verified automated-search implementation (runs are refused up front).
    automation_available: bool = True
    # Fields the site only accepts in coarse steps (e.g. 500万円): sent as a superset, then filtered exactly here.
    approximate_fields: list[str] = field(default_factory=list)
    # Politeness: minimum seconds between page requests to the site.
    min_page_interval_s: float = 0.0
    # Deal types the site publishes (buy / rent) - for manual import and site links - and where to search by hand.
    deals: list[str] = field(default_factory=lambda: ["buy"])
    links: dict[str, str] = field(default_factory=dict)
    # Browser-assisted import: the user drives a visible browser (and solves any verification);
    # HouseAgent only reads the result page the user has open when asked.
    assisted: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "site_id": self.site_id,
            "property_types": self.property_types,
            "fields": self.fields,
            "retention_fields": self.retention_fields,
            "login_required": self.login_required,
            "max_pages": self.max_pages,
            "notes_key": self.notes_key,
            "automation_available": self.automation_available,
            "approximate_fields": self.approximate_fields,
            "min_page_interval_s": self.min_page_interval_s,
            "deals": self.deals,
            "links": self.links,
            "assisted": self.assisted,
        }


@dataclass
class AssistedPage:
    """What an adapter read from the page the user has open."""

    items: list[NormalizedSource]
    total: int | None = None
    next_url: str | None = None
    # True when the page is the site's human verification (the user has to complete it first)
    verification: bool = False
    is_result_list: bool = True


@dataclass
class RulesInfo:
    """What the automatic rules check looks at for a site."""

    robots_url: str
    terms_urls: list[str]
    # Paths the adapter will request, with their purpose (checked against robots.txt).
    paths: list[tuple[str, str]]
    user_agent: str = "*"


@dataclass
class ConditionValidation:
    submit: dict[str, Any]
    local_filters: list[str]
    unsupported: list[str]
    messages: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "submit": self.submit,
            "local_filters": self.local_filters,
            "unsupported": self.unsupported,
            "messages": self.messages,
        }


@dataclass
class AccountContext:
    account_id: int
    site_id: str
    alias: str
    profile_dir: Path
    # Set to close a visible login window from the app (releases the account lock).
    stop_event: threading.Event | None = None


@dataclass
class RunContext:
    run_id: int
    run_no: str
    condition_version: int
    result_limit: int
    page_timeout_s: float
    is_cancelled: Callable[[], bool]
    log: Callable[[str, str, dict[str, Any]], None]

    def checkpoint(self) -> None:
        """Safe point: stop between page operations when the user cancelled."""
        if self.is_cancelled():
            raise AdapterError(ErrorCode.CANCELLED)

    def wait(self, seconds: float) -> None:
        """Polite pause between requests to a site; still reacts to cancellation within ~0.25 s."""
        import time

        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.checkpoint()
            time.sleep(min(0.25, max(0.0, deadline - time.monotonic())))


@dataclass
class NormalizedSource:
    external_id: str
    url: str
    deal_type: str = "buy"
    management_fee_yen: int | None = None
    deposit_yen: int | None = None
    key_money_yen: int | None = None
    title: str | None = None
    property_type: str | None = None
    prefecture: str | None = None
    city: str | None = None
    address: str | None = None
    building_name: str | None = None
    price_yen: int | None = None
    area_m2: float | None = None
    land_area_m2: float | None = None
    layout: str | None = None
    floor: int | None = None
    built_year: int | None = None
    station: str | None = None
    walk_minutes: int | None = None
    # available | unavailable (explicit evidence from the site that it is no longer offered)
    availability: str = "available"
    raw_enums: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in SUMMARY_FIELDS}


@dataclass
class SearchPage:
    page_no: int
    items: list[NormalizedSource]
    has_next: bool
    # True when the result list is known to be complete (no truncation by result_limit / max pages).
    complete: bool = True
    # Total number of results the site reports for the query, when it shows one (drives the progress %).
    total_hint: int | None = None


class SiteAdapter(ABC):
    site_id: str
    display_name: str
    base_url: str

    @abstractmethod
    def get_capabilities(self) -> Capabilities: ...

    @abstractmethod
    def check_login(self, account: AccountContext) -> LoginState: ...

    @abstractmethod
    def open_login_page(self, account: AccountContext) -> None:
        """Open a visible, isolated browser window. The user completes the login themselves."""

    def validate_conditions(self, conditions: dict[str, Any]) -> ConditionValidation:
        caps = self.get_capabilities()
        submit: dict[str, Any] = {}
        local: list[str] = []
        unsupported: list[str] = []
        messages: list[dict[str, Any]] = []
        for key, value in conditions.items():
            if value in (None, "", [], {}) or key in NON_SEARCH_KEYS:
                continue
            support = caps.fields.get(key, "unsupported")
            if support == "supported":
                submit[key] = value
            elif support == "local_filter":
                local.append(key)
                messages.append({"key": "condition.local_filter", "params": {"field": key}})
            else:
                unsupported.append(key)
                messages.append({"key": "condition.unsupported", "params": {"field": key}})
        types = conditions.get("transaction_type") or []
        bad_types = [t for t in types if t not in caps.property_types]
        if bad_types:
            unsupported.append("transaction_type")
            messages.append({"key": "condition.type_unsupported", "params": {"types": bad_types}})
        if not self.automated_types(conditions):
            if "transaction_type" not in unsupported:
                unsupported.append("transaction_type")
            messages.append(
                {"key": "condition.deal_not_automated", "params": {"deal": conditions.get("deal_type", "buy")}}
            )
        return ConditionValidation(submit=submit, local_filters=local, unsupported=unsupported, messages=messages)

    def automated_types(self, conditions: dict[str, Any]) -> list[str]:
        """Property types of the task that this adapter can search automatically."""
        from houseagent.regions import DEALS

        deal = conditions.get("deal_type") or "buy"
        wanted = conditions.get("transaction_type") or list(DEALS.get(deal, ()))
        return [t for t in wanted if t in self.get_capabilities().property_types]

    @abstractmethod
    def execute_search(
        self, account: AccountContext, conditions: dict[str, Any], ctx: RunContext
    ) -> Iterator[SearchPage]:
        """Yield result pages. Must call ``ctx.checkpoint()`` between page operations."""

    @abstractmethod
    def parse_result_list(self, content: str) -> tuple[list[dict[str, Any]], bool]:
        """Return (raw items, has_next). Raise PAGE_CHANGED rather than return incomplete identifiers."""

    @abstractmethod
    def normalize_listing(self, raw: dict[str, Any]) -> NormalizedSource: ...

    def classify_error(self, exc: BaseException, page_state: dict[str, Any] | None = None) -> AdapterError:
        if isinstance(exc, AdapterError):
            return exc
        name = type(exc).__name__.lower()
        if "timeout" in name:
            return AdapterError(ErrorCode.PAGE_TIMEOUT, str(exc))
        if "connect" in name or "network" in name:
            return AdapterError(ErrorCode.NETWORK_ERROR, str(exc))
        return AdapterError(ErrorCode.UNKNOWN_ERROR, type(exc).__name__)

    def get_property_url(self, external_id: str) -> str:
        return f"{self.base_url.rstrip('/')}/{external_id}"

    def assisted_start_urls(self, conditions: dict[str, Any]) -> list[tuple[str, str]]:
        """(property_type, URL) where the user should start browsing for this task (assisted import).

        Default: the site's search entry for the task's deal type; adapters with URL knowledge override it.
        """
        links = self.get_capabilities().links
        deal = conditions.get("deal_type") or "buy"
        url = links.get(deal) or next(iter(links.values()), None) or self.base_url
        types = conditions.get("transaction_type") or []
        return [(types[0] if len(types) == 1 else "", url)]

    def parse_assisted_page(self, url: str, html: str) -> AssistedPage:
        """Read the result list the user has open. Default: the generic card extractor (no per-site code)."""
        from houseagent.adapters.generic.extract import extract_listings

        return extract_listings(url, html, list(self.get_capabilities().property_types))

    def rules_info(self) -> RulesInfo | None:
        """Where to look up this site's robots.txt and terms. None for local/mock sites."""
        return None

    def close(self, ctx: RunContext | None = None) -> None:  # noqa: B027 - optional hook
        """Close pages and browser; releasing the account lock is done by the queue."""
