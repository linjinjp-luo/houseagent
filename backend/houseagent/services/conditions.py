"""Unified search condition dictionary (spec 15.4) and local secondary filtering."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from houseagent.adapters.base import NormalizedSource
from houseagent.regions import CITY_TO_PREF, DEALS, LAYOUTS, PREFECTURE_CODES, PROPERTY_TYPES, SORT_ORDERS, TYPE_TO_DEAL

RESULT_LIMIT_DEFAULT = 100
RESULT_LIMIT_MAX = 500


class Station(BaseModel):
    line_code: str | None = None
    station_code: str | None = None
    name: str


class SearchConditions(BaseModel):
    deal_type: str = "buy"  # buy | rent - one deal type per task
    transaction_type: list[str] = Field(default_factory=list)
    prefectures: list[str] = Field(default_factory=list)
    cities: list[str] = Field(default_factory=list)
    stations: list[Station] = Field(default_factory=list)
    # buy: sale price in 万円; rent: monthly rent in 万円/月 (decimals allowed, e.g. 8.5)
    price_min: float | None = None
    price_max: float | None = None
    price_includes_fees: bool = False  # rent: compare rent + management fee
    area_min: float | None = None  # m2
    area_max: float | None = None
    building_age_max: int | None = None  # years
    walk_minutes_max: int | None = None
    layouts: list[str] = Field(default_factory=list)
    keywords_include: list[str] = Field(default_factory=list)
    keywords_exclude: list[str] = Field(default_factory=list)
    sort_order: str | None = None
    result_limit: int = RESULT_LIMIT_DEFAULT
    region_logic: str = "OR"  # how region conditions combine; shown to the user

    @field_validator("transaction_type")
    @classmethod
    def _types(cls, v: list[str]) -> list[str]:
        bad = [t for t in v if t not in PROPERTY_TYPES]
        if bad:
            raise ValueError(f"unknown transaction_type: {bad}")
        return list(dict.fromkeys(v))

    @field_validator("prefectures")
    @classmethod
    def _prefs(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("at least one prefecture is required")
        bad = [p for p in v if p not in PREFECTURE_CODES]
        if bad:
            raise ValueError(f"unknown prefecture code: {bad}")
        return list(dict.fromkeys(v))

    @field_validator("layouts")
    @classmethod
    def _layouts(cls, v: list[str]) -> list[str]:
        bad = [x for x in v if x not in LAYOUTS]
        if bad:
            raise ValueError(f"unknown layout: {bad}")
        return list(dict.fromkeys(v))

    @field_validator("keywords_include", "keywords_exclude")
    @classmethod
    def _keywords(cls, v: list[str]) -> list[str]:
        # Trim and de-duplicate. Never translated before being sent to a site.
        return list(dict.fromkeys(k.strip() for k in v if k and k.strip()))

    @field_validator("building_age_max", "walk_minutes_max")
    @classmethod
    def _non_negative(cls, v: int | None) -> int | None:
        if v is not None and v < 0:
            raise ValueError("must be >= 0")
        return v

    @field_validator("price_min", "price_max")
    @classmethod
    def _price(cls, v: float | None) -> float | None:
        if v is None:
            return None
        if v < 0:
            raise ValueError("must be >= 0")
        return round(v, 1)

    @field_validator("deal_type")
    @classmethod
    def _deal(cls, v: str) -> str:
        if v not in DEALS:
            raise ValueError("deal_type must be buy or rent")
        return v

    @field_validator("area_min", "area_max")
    @classmethod
    def _area(cls, v: float | None) -> float | None:
        if v is None:
            return None
        if v <= 0:
            raise ValueError("must be > 0")
        return round(v, 1)

    @field_validator("sort_order")
    @classmethod
    def _sort(cls, v: str | None) -> str | None:
        if v is not None and v not in SORT_ORDERS:
            raise ValueError("unknown sort_order")
        return v

    @field_validator("result_limit")
    @classmethod
    def _limit(cls, v: int) -> int:
        if v < 1 or v > RESULT_LIMIT_MAX:
            raise ValueError(f"result_limit must be 1..{RESULT_LIMIT_MAX}")
        return v

    @model_validator(mode="after")
    def _cross(self) -> SearchConditions:
        wrong = [t for t in self.transaction_type if TYPE_TO_DEAL.get(t) != self.deal_type]
        if wrong:
            raise ValueError(f"property types {wrong} do not belong to deal_type {self.deal_type}")
        if self.price_min is not None and self.price_max is not None and self.price_max < self.price_min:
            raise ValueError("price_max must not be lower than price_min")
        if self.area_min is not None and self.area_max is not None and self.area_max < self.area_min:
            raise ValueError("area_max must not be lower than area_min")
        bad = [c for c in self.cities if CITY_TO_PREF.get(c) not in self.prefectures]
        if bad:
            raise ValueError(f"cities must belong to the selected prefectures: {bad}")
        return self


def passes_local_filters(item: NormalizedSource, cond: dict[str, Any], fields: list[str]) -> bool:
    """Apply the conditions the site could not take (capability ``local_filter``)."""
    text = " ".join(filter(None, [item.title, item.address, item.building_name]))
    for f in fields:
        v = cond.get(f)
        if v in (None, "", []):
            continue
        if f == "transaction_type" and item.property_type not in v:
            return False
        if f == "cities" and item.city not in v:
            return False
        if f == "prefectures" and item.prefecture not in v:
            return False
        if f in ("price_min", "price_max"):
            price = item.price_yen
            if price is not None and cond.get("price_includes_fees") and item.management_fee_yen:
                price += item.management_fee_yen  # rent + 管理費・共益費
            if price is None:
                return False
            if f == "price_min" and price < v * 10_000:
                return False
            if f == "price_max" and price > v * 10_000:
                return False
        if f == "area_min" and (item.area_m2 is None or item.area_m2 < v):
            return False
        if f == "area_max" and (item.area_m2 is None or item.area_m2 > v):
            return False
        if f == "building_age_max" and item.property_type != "land":
            if item.built_year is None or item.built_year < datetime.now().year - v:
                return False
        if f == "walk_minutes_max" and (item.walk_minutes is None or item.walk_minutes > v):
            return False
        if f == "layouts" and item.layout not in v:
            return False
        if f == "keywords_include" and not all(k in text for k in v):
            return False
        if f == "keywords_exclude" and any(k in text for k in v):
            return False
    return True


def local_sort(items: list[NormalizedSource], order: str | None) -> list[NormalizedSource]:
    if order == "price_asc":
        return sorted(items, key=lambda i: (i.price_yen is None, i.price_yen or 0))
    if order == "price_desc":
        return sorted(items, key=lambda i: -(i.price_yen or 0))
    if order == "area_desc":
        return sorted(items, key=lambda i: -(i.area_m2 or 0))
    return items
