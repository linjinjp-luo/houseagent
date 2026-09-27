"""Installed site adapters. New sites are added here, each in its own directory (spec NFR-06)."""

from __future__ import annotations

from houseagent.adapters.athome.adapter import AthomeAdapter
from houseagent.adapters.base import SiteAdapter
from houseagent.adapters.manual.adapter import REGISTERED_SITES, ManualOnlyAdapter
from houseagent.adapters.mock.adapter import MockSiteAdapter
from houseagent.adapters.suumo.adapter import SuumoAdapter

_ADAPTERS: dict[str, SiteAdapter] = {}


def _build() -> dict[str, SiteAdapter]:
    items: list[SiteAdapter] = [MockSiteAdapter("a"), MockSiteAdapter("b"), SuumoAdapter()]
    items += [AthomeAdapter()]
    items += [ManualOnlyAdapter(*spec) for spec in REGISTERED_SITES]
    return {a.site_id: a for a in items}


def get_adapter(site_id: str) -> SiteAdapter:
    if not _ADAPTERS:
        _ADAPTERS.update(_build())
    return _ADAPTERS[site_id]


def all_adapters() -> list[SiteAdapter]:
    if not _ADAPTERS:
        _ADAPTERS.update(_build())
    return list(_ADAPTERS.values())


def register(adapter: SiteAdapter) -> None:
    """Used by tests to plug in fakes."""
    if not _ADAPTERS:
        _ADAPTERS.update(_build())
    _ADAPTERS[adapter.site_id] = adapter
