"""Deterministic investment rules (spec 4.6 / 15.14). No AI and no database here.

Every amount and ratio is computed with ``Decimal`` from explicit inputs; an empty value is "missing", never 0.
Each calculation keeps its formula, unit and inputs so the UI can show how it was obtained, and the AI output
validator can check that every number the AI writes exists here.

The rules decide which primary labels are *possible*:

* ``resale_candidate``  - interior is old AND the price is sufficiently below comparable listings AND the
  resale profit and margin (after purchase, renovation, holding and sale costs) meet the user's thresholds plus
  the safety buffer AND no problem that renovation cannot fix is recorded.
* ``rental_candidate``  - a sourced rent estimate gives a gross yield at or above the user's minimum and the
  net yield (after running costs, vacancy and leasing costs) is computable and meets the minimum when set.
* ``owner_candidate``   - the price is not above comparables by more than the user accepts, the interior is
  livable (or its renovation fits the budget) and the user's area / layout / walk / commute wishes are met.
* ``low_value``         - with complete data the relevant paths fail for a reason other than building age.
* ``insufficient_data`` - key inputs are missing; the missing fields and next steps are listed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

RULE_VERSION = "1.0.0"

LABELS = ("resale_candidate", "rental_candidate", "owner_candidate", "low_value", "insufficient_data")
TAGS = (
    "price_below_area",
    "unit_price_high",
    "interior_old",
    "move_in_ready",
    "renovation_cost_unconfirmed",
    "renovation_possible_needs_calc",
    "rental_yield_good",
    "holding_cost_high",
    "near_station",
    "old_building",
    "old_seismic_standard",
    "repair_risk",
    "low_liquidity",
    "data_insufficient",
)
TARGETS = ("any", "resale", "rental", "owner")
CONDITIONS = ("poor", "average", "good", "renovated")
# Problems renovation cannot fix (spec 4.6.1): any of these blocks the resale label and asks for investigation.
BLOCKING_RISKS = ("structure", "title_rights", "location", "noise", "sunlight", "incident")
RISK_FLAGS = (*BLOCKING_RISKS, "other")

# Per-property inputs the user (or a named third party) provides - never generated.
INPUT_FIELDS = {
    "monthly_rent_yen": "yen",
    "management_fee_monthly_yen": "yen",
    "repair_reserve_monthly_yen": "yen",
    "property_tax_annual_yen": "yen",
    "insurance_annual_yen": "yen",
    "renovation_budget_yen": "yen",
    "expected_sale_price_yen": "yen",
    "holding_months": "months",
    "interior_condition": "enum",
    "commute_minutes": "minutes",
    "comparable_unit_price_yen_m2": "yen_m2",
    "risk_flags": "list",
}
THRESHOLDS = {
    "min_gross_yield_pct": "pct",
    "min_net_yield_pct": "pct",
    "min_resale_profit_yen": "yen",
    "min_resale_margin_pct": "pct",
    "resale_safety_margin_pct": "pct",
    "min_discount_pct": "pct",
    "max_price_premium_pct": "pct",
    "max_price_yen": "yen",
    "max_unit_price_yen_m2": "yen_m2",
    "max_age_years": "years",
    "max_renovation_budget_yen": "yen",
    "max_holding_months": "months",
    "max_walk_minutes": "minutes",
}
ASSUMPTIONS = {
    "purchase_cost_rate_pct": "pct",
    "sale_cost_rate_pct": "pct",
    "vacancy_rate_pct": "pct",
    "leasing_cost_months_per_year": "months",
}
PREFERENCES = ("cities", "min_area_m2", "layouts", "max_commute_minutes")

NEAR_STATION_MIN = 7
FAR_STATION_MIN = 15
OLD_BUILDING_YEARS = 30
HOLDING_COST_HIGH_PCT = 30  # running costs above this share of the annual rent


@dataclass
class Calc:
    value: Decimal
    unit: str
    formula: str
    inputs: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        inputs = {k: _num(v) if isinstance(v, Decimal) else v for k, v in self.inputs.items()}
        return {"value": _num(self.value), "unit": self.unit, "formula": self.formula, "inputs": inputs}


@dataclass
class Path:
    """One judgement path (resale / rental / owner)."""

    outcome: str  # candidate | fail | insufficient | not_applicable
    reasons: list[dict[str, Any]] = field(default_factory=list)  # {"key", "params", "refs"}
    missing: list[str] = field(default_factory=list)
    low_value: bool = False  # a failure that supports "low value" (never building age alone)


@dataclass
class RuleResult:
    rule_label: str
    allowed_labels: list[str]
    tags: list[str]
    score: int
    calculations: dict[str, Calc]
    paths: dict[str, Path]
    missing: list[str]
    reasons: list[dict[str, Any]]
    risks: list[dict[str, Any]]
    next_steps: list[dict[str, Any]]
    assumptions: dict[str, Any]

    def calculations_dict(self) -> dict[str, Any]:
        return {k: c.to_dict() for k, c in self.calculations.items()}


def _num(v: Decimal) -> float | int:
    if v == v.to_integral_value():
        return int(v)
    return float(v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _dec(v: Any) -> Decimal | None:
    if v is None or v == "" or isinstance(v, bool):
        return None
    try:
        return Decimal(str(v))
    except ArithmeticError:
        return None


def _pct(v: Decimal) -> Decimal:
    return (v * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _yen(v: Decimal) -> Decimal:
    return v.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def evaluate(
    facts: dict[str, Any],
    inputs: dict[str, Any],
    comparable: dict[str, Any] | None,
    profile: dict[str, Any],
    now: datetime | None = None,
) -> RuleResult:
    """facts: listing summary fields; inputs: {field: value}; comparable: {"unit_price_yen_m2", "n"} or None;
    profile: {"target_type", "thresholds", "assumptions", "preferences"}."""
    year = (now or datetime.now()).year
    th: dict[str, Any] = {k: _dec(v) for k, v in (profile.get("thresholds") or {}).items() if k in THRESHOLDS}
    asm: dict[str, Any] = {k: _dec(v) for k, v in (profile.get("assumptions") or {}).items() if k in ASSUMPTIONS}
    prefs = profile.get("preferences") or {}
    target = profile.get("target_type") or "any"
    c: dict[str, Calc] = {}
    risks: list[dict[str, Any]] = []
    tags: list[str] = []

    price = _dec(facts.get("price_yen"))
    area = _dec(facts.get("area_m2"))
    built = _dec(facts.get("built_year"))
    walk = _dec(facts.get("walk_minutes"))
    val: dict[str, Any] = {
        k: _dec(inputs.get(k)) for k in INPUT_FIELDS if k not in ("interior_condition", "risk_flags")
    }
    condition = inputs.get("interior_condition") if inputs.get("interior_condition") in CONDITIONS else None
    flags = [f for f in (inputs.get("risk_flags") or []) if f in RISK_FLAGS]

    # ---- basic facts ------------------------------------------------------------------------------------
    if price is not None and area:
        c["unit_price_yen_m2"] = Calc(
            _yen(price / area), "yen_m2", "price_yen / area_m2", {"price_yen": price, "area_m2": area}
        )
    comp_unit = _dec(val.get("comparable_unit_price_yen_m2"))
    comp_n = None
    if comp_unit is None and comparable and comparable.get("unit_price_yen_m2"):
        comp_unit = _dec(comparable["unit_price_yen_m2"])
        comp_n = comparable.get("n")
    if comp_unit:
        c["comparable_unit_price_yen_m2"] = Calc(
            _yen(comp_unit),
            "yen_m2",
            "median(unit price of comparable listings)" if comp_n else "user input",
            {"n": comp_n} if comp_n else {},
        )
    if "unit_price_yen_m2" in c and comp_unit:
        diff = (c["unit_price_yen_m2"].value - comp_unit) / comp_unit
        c["price_vs_comparable_pct"] = Calc(
            _pct(diff),
            "pct",
            "(unit_price_yen_m2 - comparable_unit_price_yen_m2) / comparable_unit_price_yen_m2 × 100",
            {"unit_price_yen_m2": c["unit_price_yen_m2"].value, "comparable_unit_price_yen_m2": _yen(comp_unit)},
        )
    if built is not None:
        c["building_age_years"] = Calc(
            Decimal(year) - built,
            "years",
            "current_year - built_year",
            {
                "current_year": year,
                "built_year": built,
            },
        )

    # ---- costs ------------------------------------------------------------------------------------------
    monthly_fixed = None
    if val["management_fee_monthly_yen"] is not None and val["repair_reserve_monthly_yen"] is not None:
        monthly_fixed = val["management_fee_monthly_yen"] + val["repair_reserve_monthly_yen"]
    annual_fixed = None
    if (
        monthly_fixed is not None
        and val["property_tax_annual_yen"] is not None
        and val["insurance_annual_yen"] is not None
    ):
        annual_fixed = monthly_fixed * 12 + val["property_tax_annual_yen"] + val["insurance_annual_yen"]
        c["annual_running_costs_yen"] = Calc(
            _yen(annual_fixed),
            "yen",
            "(management_fee_monthly_yen + repair_reserve_monthly_yen) × 12 + property_tax_annual_yen"
            " + insurance_annual_yen",
            {
                k: val[k]
                for k in (
                    "management_fee_monthly_yen",
                    "repair_reserve_monthly_yen",
                    "property_tax_annual_yen",
                    "insurance_annual_yen",
                )
            },
        )
    purchase_costs = None
    if price is not None and asm.get("purchase_cost_rate_pct") is not None:
        purchase_costs = price * asm["purchase_cost_rate_pct"] / 100
        c["purchase_costs_yen"] = Calc(
            _yen(purchase_costs),
            "yen",
            "price_yen × purchase_cost_rate_pct / 100",
            {"price_yen": price, "purchase_cost_rate_pct": asm["purchase_cost_rate_pct"]},
        )

    # ---- rental -----------------------------------------------------------------------------------------
    rent = val["monthly_rent_yen"]
    if rent is not None and price:
        c["gross_yield_pct"] = Calc(
            _pct(rent * 12 / price),
            "pct",
            "monthly_rent_yen × 12 / price_yen × 100",
            {"monthly_rent_yen": rent, "price_yen": price},
        )
    net_needs = {
        "monthly_rent_yen": rent,
        "annual_running_costs_yen": annual_fixed,
        "profile.vacancy_rate_pct": asm.get("vacancy_rate_pct"),
        "profile.leasing_cost_months_per_year": asm.get("leasing_cost_months_per_year"),
        "profile.purchase_cost_rate_pct": asm.get("purchase_cost_rate_pct"),
    }
    if price and all(v is not None for v in net_needs.values()):
        assert rent is not None and annual_fixed is not None and purchase_costs is not None
        effective = rent * 12 * (1 - asm["vacancy_rate_pct"] / 100) - asm["leasing_cost_months_per_year"] * rent
        c["effective_annual_rent_yen"] = Calc(
            _yen(effective),
            "yen",
            "monthly_rent_yen × 12 × (1 - vacancy_rate_pct / 100) - leasing_cost_months_per_year × monthly_rent_yen",
            {
                "monthly_rent_yen": rent,
                "vacancy_rate_pct": asm["vacancy_rate_pct"],
                "leasing_cost_months_per_year": asm["leasing_cost_months_per_year"],
            },
        )
        c["net_yield_pct"] = Calc(
            _pct((effective - annual_fixed) / (price + purchase_costs)),
            "pct",
            "(effective_annual_rent_yen - annual_running_costs_yen) / (price_yen + purchase_costs_yen) × 100",
            {
                "effective_annual_rent_yen": _yen(effective),
                "annual_running_costs_yen": _yen(annual_fixed),
                "price_yen": price,
                "purchase_costs_yen": _yen(purchase_costs),
            },
        )
    if rent and annual_fixed is not None:
        ratio = annual_fixed / (rent * 12)
        c["running_cost_share_pct"] = Calc(
            _pct(ratio),
            "pct",
            "annual_running_costs_yen / (monthly_rent_yen × 12) × 100",
            {"annual_running_costs_yen": _yen(annual_fixed), "monthly_rent_yen": rent},
        )

    # ---- resale -----------------------------------------------------------------------------------------
    months = val["holding_months"] if val["holding_months"] is not None else th.get("max_holding_months")
    resale_needs = {
        "price_yen": price,
        "expected_sale_price_yen": val["expected_sale_price_yen"],
        "renovation_budget_yen": val["renovation_budget_yen"],
        "holding_months": months,
        "management_fee_monthly_yen": val["management_fee_monthly_yen"],
        "repair_reserve_monthly_yen": val["repair_reserve_monthly_yen"],
        "property_tax_annual_yen": val["property_tax_annual_yen"],
        "insurance_annual_yen": val["insurance_annual_yen"],
        "profile.purchase_cost_rate_pct": asm.get("purchase_cost_rate_pct"),
        "profile.sale_cost_rate_pct": asm.get("sale_cost_rate_pct"),
    }
    if all(v is not None for v in resale_needs.values()):
        assert price is not None and months is not None and purchase_costs is not None
        sale = val["expected_sale_price_yen"]
        reno = val["renovation_budget_yen"]
        assert sale is not None and reno is not None and monthly_fixed is not None
        tax_ins = val["property_tax_annual_yen"] + val["insurance_annual_yen"]
        holding = (monthly_fixed + tax_ins / 12) * months
        sale_costs = sale * asm["sale_cost_rate_pct"] / 100
        profit = sale - price - purchase_costs - reno - holding - sale_costs
        invested = price + purchase_costs + reno + holding
        c["holding_costs_yen"] = Calc(
            _yen(holding),
            "yen",
            "(management_fee_monthly_yen + repair_reserve_monthly_yen + (property_tax_annual_yen"
            " + insurance_annual_yen) / 12) × holding_months",
            {"holding_months": months},
        )
        c["sale_costs_yen"] = Calc(
            _yen(sale_costs),
            "yen",
            "expected_sale_price_yen × sale_cost_rate_pct / 100",
            {"expected_sale_price_yen": sale, "sale_cost_rate_pct": asm["sale_cost_rate_pct"]},
        )
        c["resale_profit_yen"] = Calc(
            _yen(profit),
            "yen",
            "expected_sale_price_yen - price_yen - purchase_costs_yen - renovation_budget_yen - holding_costs_yen"
            " - sale_costs_yen",
            {
                "expected_sale_price_yen": sale,
                "price_yen": price,
                "purchase_costs_yen": _yen(purchase_costs),
                "renovation_budget_yen": reno,
                "holding_costs_yen": _yen(holding),
                "sale_costs_yen": _yen(sale_costs),
            },
        )
        c["resale_margin_pct"] = Calc(
            _pct(profit / invested),
            "pct",
            "resale_profit_yen / (price_yen + purchase_costs_yen + renovation_budget_yen + holding_costs_yen) × 100",
            {"resale_profit_yen": _yen(profit), "invested_yen": _yen(invested)},
        )

    # ---- facts and risks --------------------------------------------------------------------------------
    premium = c["price_vs_comparable_pct"].value if "price_vs_comparable_pct" in c else None
    if premium is not None and premium <= -10:
        tags.append("price_below_area")
    if premium is not None and premium >= 10:
        tags.append("unit_price_high")
    if condition == "poor":
        tags.append("interior_old")
        if val["renovation_budget_yen"] is None:
            tags.append("renovation_cost_unconfirmed")
    if condition in ("good", "renovated"):
        tags.append("move_in_ready")
    if "gross_yield_pct" in c and th.get("min_gross_yield_pct") is not None:
        if c["gross_yield_pct"].value >= th["min_gross_yield_pct"]:
            tags.append("rental_yield_good")
    if "running_cost_share_pct" in c and c["running_cost_share_pct"].value > HOLDING_COST_HIGH_PCT:
        tags.append("holding_cost_high")
        risks.append({"key": "risk.holding_cost_high", "params": {}, "refs": ["running_cost_share_pct"]})
    if walk is not None and walk <= NEAR_STATION_MIN:
        tags.append("near_station")
    if walk is not None and walk > FAR_STATION_MIN:
        tags.append("low_liquidity")
        risks.append({"key": "risk.far_from_station", "params": {"minutes": _num(walk)}, "refs": ["walk_minutes"]})
    age = c["building_age_years"].value if "building_age_years" in c else None
    if age is not None and age >= OLD_BUILDING_YEARS:
        tags.append("old_building")
        risks.append({"key": "risk.old_building", "params": {"years": _num(age)}, "refs": ["building_age_years"]})
    if built is not None and built < 1982 and facts.get("property_type") != "land":
        tags.append("old_seismic_standard")
        risks.append({"key": "risk.old_seismic", "params": {"year": _num(built)}, "refs": ["built_year"]})
    if any(f in ("structure",) for f in flags) or ("old_seismic_standard" in tags and "old_building" in tags):
        tags.append("repair_risk")
    for f in flags:
        risks.append({"key": f"risk.flag.{f}", "params": {}, "refs": ["risk_flags"]})
    max_age = th.get("max_age_years")
    if max_age is not None and age is not None and age > max_age:
        risks.append(
            {
                "key": "risk.age_over_max",
                "params": {"years": _num(age), "max": _num(max_age)},
                "refs": ["building_age_years", "profile.max_age_years"],
            }
        )

    # ---- paths ------------------------------------------------------------------------------------------
    paths: dict[str, Path] = {}
    if target in ("any", "resale"):
        paths["resale"] = _resale_path(c, th, condition, flags, resale_needs, comp_unit, premium)
    if target in ("any", "rental"):
        paths["rental"] = _rental_path(c, th, rent, net_needs)
    if target in ("any", "owner"):
        paths["owner"] = _owner_path(c, th, prefs, facts, val, condition, premium, price)

    if target == "resale" and paths["resale"].outcome == "not_applicable":
        # The user only looks for renovation plays and this interior is not old: not worth it for that goal.
        paths["resale"].outcome, paths["resale"].low_value = "fail", True
    candidates = [p for p in ("resale", "rental", "owner") if p in paths and paths[p].outcome == "candidate"]
    label_of = {"resale": "resale_candidate", "rental": "rental_candidate", "owner": "owner_candidate"}
    applicable = [p for p in paths.values() if p.outcome != "not_applicable"]
    if price is None:
        rule_label = "insufficient_data"
    elif candidates:
        rule_label = label_of[candidates[0]]
    elif applicable and all(p.outcome == "fail" for p in applicable) and any(p.low_value for p in applicable):
        # Every path the user cares about was checked with complete data and failed (not on age alone).
        rule_label = "low_value"
    else:
        rule_label = "insufficient_data"

    missing = sorted({m for p in paths.values() for m in p.missing} | ({"price_yen"} if price is None else set()))
    if rule_label == "insufficient_data" and "data_insufficient" not in tags:
        tags.append("data_insufficient")
    if condition == "poor" and "resale" in paths and paths["resale"].outcome == "insufficient":
        tags.append("renovation_possible_needs_calc")

    reasons: list[dict[str, Any]] = []
    for name in ([candidates[0]] if candidates else []) + [p for p in paths if not candidates or p != candidates[0]]:
        reasons.extend(paths[name].reasons)
    next_steps = [{"key": f"next.{m.replace('profile.', 'profile_')}", "params": {}, "refs": []} for m in missing]
    if any(f in BLOCKING_RISKS for f in flags):
        next_steps.append({"key": "next.investigate_risks", "params": {}, "refs": ["risk_flags"]})

    score = _score(rule_label, c, th, tags, missing, risks)
    allowed = sorted({rule_label, "insufficient_data"}, key=LABELS.index)
    return RuleResult(
        rule_label=rule_label,
        allowed_labels=allowed,
        tags=[t for t in TAGS if t in tags],
        score=score,
        calculations=c,
        paths=paths,
        missing=missing,
        reasons=reasons[:5],
        risks=risks,
        next_steps=next_steps,
        assumptions={k: _num(v) for k, v in asm.items() if v is not None}
        | ({"holding_months_from_profile": _num(months)} if val["holding_months"] is None and months else {}),
    )


def _missing(needs: dict[str, Any]) -> list[str]:
    return [k for k, v in needs.items() if v is None]


def _resale_path(
    c: dict[str, Calc],
    th: dict[str, Any],
    condition: str | None,
    flags: list[str],
    needs: dict[str, Any],
    comp_unit: Decimal | None,
    premium: Decimal | None,
) -> Path:
    if condition is None:
        return Path("insufficient", missing=["interior_condition"])
    if condition != "poor":
        # "Old interior" is part of the definition; a good interior is not a renovation play.
        return Path(
            "not_applicable", reasons=[{"key": "reason.resale_not_old", "params": {}, "refs": ["interior_condition"]}]
        )
    missing = _missing(needs)
    if comp_unit is None:
        missing.append("comparable_unit_price_yen_m2")
    for k in ("min_resale_profit_yen", "min_resale_margin_pct", "min_discount_pct"):
        if th.get(k) is None:
            missing.append(f"profile.{k}")
    if missing:
        return Path(
            "insufficient",
            missing=missing,
            reasons=[
                {
                    "key": "reason.resale_needs_calc",
                    "params": {},
                    "refs": ["interior_condition"],
                }
            ],
        )
    assert premium is not None
    profit, margin = c["resale_profit_yen"].value, c["resale_margin_pct"].value
    safety = th.get("resale_safety_margin_pct") or Decimal(0)
    fails: list[dict[str, Any]] = []
    if -premium < th["min_discount_pct"]:
        fails.append(
            {
                "key": "reason.resale_discount_short",
                "params": {"discount": _num(-premium), "min": _num(th["min_discount_pct"])},
                "refs": ["price_vs_comparable_pct", "profile.min_discount_pct"],
            }
        )
    if profit < th["min_resale_profit_yen"]:
        fails.append(
            {
                "key": "reason.resale_profit_short",
                "params": {"profit": _num(profit), "min": _num(th["min_resale_profit_yen"])},
                "refs": ["resale_profit_yen", "profile.min_resale_profit_yen"],
            }
        )
    if margin < th["min_resale_margin_pct"] + safety:
        fails.append(
            {
                "key": "reason.resale_margin_short",
                "params": {"margin": _num(margin), "min": _num(th["min_resale_margin_pct"] + safety)},
                "refs": ["resale_margin_pct", "profile.min_resale_margin_pct"],
            }
        )
    blocking = [f for f in flags if f in BLOCKING_RISKS]
    if blocking:
        fails.append({"key": "reason.resale_blocking_risk", "params": {"flags": blocking}, "refs": ["risk_flags"]})
    if fails:
        return Path("fail", reasons=fails, low_value=not blocking or len(fails) > 1)
    return Path(
        "candidate",
        reasons=[
            {
                "key": "reason.resale_ok",
                "params": {"profit": _num(profit), "margin": _num(margin), "discount": _num(-premium)},
                "refs": ["resale_profit_yen", "resale_margin_pct", "price_vs_comparable_pct"],
            },
        ],
    )


def _rental_path(
    c: dict[str, Calc], th: dict[str, Decimal | None], rent: Decimal | None, needs: dict[str, Any]
) -> Path:
    missing = _missing(needs)
    if th.get("min_gross_yield_pct") is None:
        missing.append("profile.min_gross_yield_pct")
    if rent is None or "gross_yield_pct" not in c:
        return Path("insufficient", missing=sorted(set(missing) | {"monthly_rent_yen"}))
    gross = c["gross_yield_pct"].value
    min_gross = th.get("min_gross_yield_pct")
    if min_gross is not None and gross < min_gross:
        # A gross yield below the minimum already decides it: net can only be lower.
        return Path(
            "fail",
            low_value=True,
            reasons=[
                {
                    "key": "reason.rental_gross_short",
                    "params": {"gross": _num(gross), "min": _num(min_gross)},
                    "refs": ["gross_yield_pct", "profile.min_gross_yield_pct"],
                }
            ],
        )
    if missing:
        return Path(
            "insufficient",
            missing=missing,
            reasons=[
                {"key": "reason.rental_net_unknown", "params": {"gross": _num(gross)}, "refs": ["gross_yield_pct"]}
            ],
        )
    net = c["net_yield_pct"].value
    min_net = th.get("min_net_yield_pct")
    if min_net is not None and net < min_net:
        return Path(
            "fail",
            low_value=True,
            reasons=[
                {
                    "key": "reason.rental_net_short",
                    "params": {"net": _num(net), "min": _num(min_net)},
                    "refs": ["net_yield_pct", "profile.min_net_yield_pct"],
                }
            ],
        )
    return Path(
        "candidate",
        reasons=[
            {
                "key": "reason.rental_ok",
                "params": {"gross": _num(gross), "net": _num(net)},
                "refs": ["gross_yield_pct", "net_yield_pct"],
            }
        ],
    )


def _owner_path(
    c: dict[str, Calc],
    th: dict[str, Any],
    prefs: dict[str, Any],
    facts: dict[str, Any],
    val: dict[str, Decimal | None],
    condition: str | None,
    premium: Decimal | None,
    price: Decimal | None,
) -> Path:
    missing: list[str] = []
    if premium is None:
        missing.append("comparable_unit_price_yen_m2")
    if condition is None:
        missing.append("interior_condition")
    if th.get("max_price_premium_pct") is None:
        missing.append("profile.max_price_premium_pct")
    if condition == "poor" and val["renovation_budget_yen"] is None:
        missing.append("renovation_budget_yen")
    if prefs.get("max_commute_minutes") and val["commute_minutes"] is None:
        missing.append("commute_minutes")
    if missing:
        return Path("insufficient", missing=missing)
    assert premium is not None
    fails: list[dict[str, Any]] = []
    price_fail = False
    if premium > th["max_price_premium_pct"]:
        price_fail = True
        fails.append(
            {
                "key": "reason.owner_price_high",
                "params": {"premium": _num(premium), "max": _num(th["max_price_premium_pct"])},
                "refs": ["price_vs_comparable_pct", "profile.max_price_premium_pct"],
            }
        )
    if th.get("max_price_yen") is not None and price is not None and price > th["max_price_yen"]:
        price_fail = True
        fails.append(
            {
                "key": "reason.over_max_price",
                "params": {"price": _num(price), "max": _num(th["max_price_yen"])},
                "refs": ["price_yen", "profile.max_price_yen"],
            }
        )
    unit = c.get("unit_price_yen_m2")
    if th.get("max_unit_price_yen_m2") is not None and unit is not None and unit.value > th["max_unit_price_yen_m2"]:
        price_fail = True
        fails.append(
            {
                "key": "reason.over_max_unit_price",
                "params": {"unit": _num(unit.value), "max": _num(th["max_unit_price_yen_m2"])},
                "refs": ["unit_price_yen_m2", "profile.max_unit_price_yen_m2"],
            }
        )
    if condition == "poor":
        budget = th.get("max_renovation_budget_yen")
        reno = val["renovation_budget_yen"]
        if budget is not None and reno is not None and reno > budget:
            fails.append(
                {
                    "key": "reason.owner_renovation_over_budget",
                    "params": {"cost": _num(reno), "max": _num(budget)},
                    "refs": ["renovation_budget_yen", "profile.max_renovation_budget_yen"],
                }
            )
    pref_fails = []
    if prefs.get("cities") and facts.get("city") not in prefs["cities"]:
        pref_fails.append("city")
    area = _dec(facts.get("area_m2"))
    if prefs.get("min_area_m2") and (area is None or area < Decimal(str(prefs["min_area_m2"]))):
        pref_fails.append("area")
    if prefs.get("layouts") and facts.get("layout") not in prefs["layouts"]:
        pref_fails.append("layout")
    walk = _dec(facts.get("walk_minutes"))
    if th.get("max_walk_minutes") is not None and (walk is None or walk > th["max_walk_minutes"]):
        pref_fails.append("walk")
    commute = val["commute_minutes"]
    if (
        prefs.get("max_commute_minutes")
        and commute is not None
        and commute > Decimal(str(prefs["max_commute_minutes"]))
    ):
        pref_fails.append("commute")
    if pref_fails:
        fails.append({"key": "reason.owner_preferences", "params": {"items": pref_fails}, "refs": []})
    if fails:
        if condition in ("good", "renovated") and price_fail:
            fails.insert(0, {"key": "reason.owner_good_but_expensive", "params": {}, "refs": ["interior_condition"]})
        return Path("fail", reasons=fails, low_value=True)
    return Path(
        "candidate",
        reasons=[
            {
                "key": "reason.owner_ok",
                "params": {"premium": _num(premium)},
                "refs": ["price_vs_comparable_pct", "interior_condition"],
            }
        ],
    )


def _score(
    label: str,
    c: dict[str, Calc],
    th: dict[str, Any],
    tags: list[str],
    missing: list[str],
    risks: list[dict[str, Any]],
) -> int:
    """A transparent 0-100 reference score (the AI may adjust it only within ±15)."""
    s = Decimal(50)
    if label in ("resale_candidate", "rental_candidate", "owner_candidate"):
        s += 20
    if label == "low_value":
        s -= 20
    if "resale_margin_pct" in c and th.get("min_resale_margin_pct") is not None:
        s += max(Decimal(-10), min(Decimal(10), c["resale_margin_pct"].value - th["min_resale_margin_pct"]))
    if "gross_yield_pct" in c and th.get("min_gross_yield_pct") is not None:
        s += max(Decimal(-10), min(Decimal(10), (c["gross_yield_pct"].value - th["min_gross_yield_pct"]) * 2))
    if "price_vs_comparable_pct" in c:
        s += max(Decimal(-10), min(Decimal(10), -c["price_vs_comparable_pct"].value / 2))
    s -= 5 * len([r for r in risks if r["key"].startswith("risk.flag.")])
    s -= 3 * min(len(missing), 5)
    if "near_station" in tags:
        s += 3
    return int(max(Decimal(0), min(Decimal(100), s)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
