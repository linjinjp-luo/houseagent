"""Deterministic fake listings for the two mock property sites (A and B).

Site B republishes some of A's properties under its own IDs and prices so cross-site matching can be
exercised. Nothing here is real data.
"""

from __future__ import annotations

import random
from dataclasses import asdict, dataclass

_AREAS = [
    # city code, pref, town prefix, station, building prefix
    ("11108", "11", "さいたま市南区別所", "武蔵浦和", "パークハウス武蔵浦和"),
    ("11108", "11", "さいたま市南区沼影", "武蔵浦和", "ライオンズ武蔵浦和"),
    ("11108", "11", "さいたま市南区白幡", "中浦和", "グランシティ中浦和"),
    ("11107", "11", "さいたま市浦和区高砂", "浦和", "浦和タワーレジデンス"),
    ("11107", "11", "さいたま市浦和区岸町", "浦和", "ブリリア浦和"),
    ("11103", "11", "さいたま市大宮区桜木町", "大宮", "大宮スカイハイツ"),
    ("11203", "11", "川口市本町", "川口", "川口リバーテラス"),
    ("11224", "11", "戸田市上戸田", "戸田公園", "戸田公園フォレスト"),
    ("13119", "13", "板橋区成増", "成増", "成増ガーデンヒルズ"),
    ("13120", "13", "練馬区光が丘", "光が丘", "光が丘パークタウン"),
]


@dataclass
class MockProperty:
    id: str
    property_type: str
    prefecture: str
    city: str
    address: str
    building_name: str | None
    price_man: float  # buy: sale price; rent: monthly rent (万円)
    area_m2: float
    land_area_m2: float | None
    layout: str | None
    floor: int | None
    built_year: int
    station: str
    walk_minutes: int
    available: bool = True
    listed_order: int = 0
    management_fee_yen: int | None = None  # rent only
    deposit_yen: int | None = None
    key_money_yen: int | None = None

    @property
    def title(self) -> str:
        label = {
            "used_mansion": "中古マンション",
            "used_house": "中古戸建",
            "land": "土地",
            "rent_apartment": "賃貸マンション",
            "rent_house": "賃貸一戸建て",
        }[self.property_type]
        name = self.building_name or self.address
        return f"{label} {name}"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["title"] = self.title
        return d


def _make(rng: random.Random, pid: str, order: int) -> MockProperty:
    city, pref, town, station, bprefix = rng.choice(_AREAS)
    ptype = rng.choices(["used_mansion", "used_house", "land"], weights=[6, 3, 1])[0]
    chome = rng.randint(1, 6)
    address = f"{town}{chome}丁目"
    built = rng.randint(1985, 2022)
    if ptype == "used_mansion":
        area = round(rng.uniform(45, 95), 1)
        layout = rng.choice(["1LDK", "2LDK", "3LDK", "3LDK", "4LDK"])
        floor = rng.randint(1, 20)
        building = f"{bprefix}{rng.choice(['', ' 弐番館', ' イースト', ' ウエスト'])}".strip()
        land = None
        price = int(area * rng.uniform(45, 85))
    elif ptype == "used_house":
        area = round(rng.uniform(80, 130), 1)
        land = round(rng.uniform(90, 180), 1)
        layout = rng.choice(["3LDK", "4LDK", "4LDK", "5LDK+"])
        floor, building = None, None
        price = int(land * rng.uniform(30, 55))
    else:
        land = round(rng.uniform(80, 200), 1)
        area, layout, floor, building = land, None, None, None
        price = int(land * rng.uniform(25, 45))
        built = 0
    return MockProperty(
        id=pid,
        property_type=ptype,
        prefecture=pref,
        city=city,
        address=address,
        building_name=building,
        price_man=max(price, 900),
        area_m2=area,
        land_area_m2=land,
        layout=layout,
        floor=floor,
        built_year=built,
        station=station,
        walk_minutes=rng.randint(2, 22),
        listed_order=order,
    )


def _make_rental(rng: random.Random, pid: str, order: int) -> MockProperty:
    city, pref, town, station, bprefix = rng.choice(_AREAS)
    ptype = rng.choices(["rent_apartment", "rent_house"], weights=[5, 1])[0]
    layout = rng.choice(["1K", "1LDK", "2LDK", "2LDK", "3LDK"] if ptype == "rent_apartment" else ["3LDK", "4LDK"])
    area = round(rng.uniform(22, 70) if ptype == "rent_apartment" else rng.uniform(70, 110), 1)
    rent = round(area * rng.uniform(0.12, 0.2), 1)  # 万円/月
    return MockProperty(
        id=pid,
        property_type=ptype,
        prefecture=pref,
        city=city,
        address=f"{town}{rng.randint(1, 6)}丁目",
        building_name=f"{bprefix}{rng.choice(['', ' 賃貸棟', ' レジデンス'])}".strip()
        if ptype == "rent_apartment"
        else None,
        price_man=rent,
        area_m2=area,
        land_area_m2=None,
        layout=layout,
        floor=rng.randint(1, 12) if ptype == "rent_apartment" else None,
        built_year=rng.randint(1990, 2023),
        station=station,
        walk_minutes=rng.randint(2, 20),
        listed_order=order,
        management_fee_yen=rng.choice([0, 3000, 5000, 8000, 10000]),
        deposit_yen=int(rent * 10_000 * rng.choice([0, 1, 1, 2])),
        key_money_yen=int(rent * 10_000 * rng.choice([0, 0, 1])),
    )


def generate(variant: str) -> list[MockProperty]:
    rng = random.Random(20260923)
    base = [_make(rng, f"A{i:04d}", i) for i in range(1, 73)]
    if variant == "a":
        rent_rng = random.Random(4242)
        return base + [_make_rental(rent_rng, f"R{i:04d}", 200 + i) for i in range(1, 41)]
    # Site B: republishes every 5th property of A (same building/area/floor/year, own ID, slightly other price)
    rng_b = random.Random(7)
    props: list[MockProperty] = []
    for i, p in enumerate(base[::5], start=1):
        props.append(
            MockProperty(
                **{
                    **asdict(p),
                    "id": f"B-{9000 + i}",
                    "price_man": p.price_man + rng_b.choice([-100, -50, 0, 30, 80]),
                    "listed_order": i,
                }
            )
        )
    for j in range(1, 16):
        q = _make(rng_b, f"B-{8000 + j}", 100 + j)
        props.append(q)
    return props
