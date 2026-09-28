"""FR-11 deterministic rules and AI output checks (no server, no AI)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from houseagent.investment import ai_eval, rules

NOW = datetime(2026, 9, 28)
FACTS = {
    "deal_type": "buy",
    "property_type": "used_mansion",
    "city": "11108",
    "price_yen": 20_000_000,
    "area_m2": 60.0,
    "layout": "3LDK",
    "built_year": 1995,
    "walk_minutes": 6,
}
COMPARABLE = {"unit_price_yen_m2": 500_000, "n": 8}  # this listing: 333,333 /m2 -> 33 % below
COSTS = {
    "management_fee_monthly_yen": 12_000,
    "repair_reserve_monthly_yen": 10_000,
    "property_tax_annual_yen": 80_000,
    "insurance_annual_yen": 20_000,
}
ASSUMPTIONS = {
    "purchase_cost_rate_pct": 7,
    "sale_cost_rate_pct": 4,
    "vacancy_rate_pct": 5,
    "leasing_cost_months_per_year": 1,
}


def profile(target: str = "any", **th: Any) -> dict[str, Any]:
    thresholds = {
        "min_gross_yield_pct": 7,
        "min_net_yield_pct": 4,
        "min_resale_profit_yen": 2_000_000,
        "min_resale_margin_pct": 8,
        "resale_safety_margin_pct": 2,
        "min_discount_pct": 15,
        "max_price_premium_pct": 5,
        "max_holding_months": 6,
        **th,
    }
    return {"target_type": target, "thresholds": thresholds, "assumptions": ASSUMPTIONS, "preferences": {}}


def run(inputs: dict[str, Any], prof: dict[str, Any] | None = None, **facts: Any) -> rules.RuleResult:
    return rules.evaluate({**FACTS, **facts}, inputs, COMPARABLE, prof or profile(), now=NOW)


def test_resale_candidate_with_full_data() -> None:
    r = run(
        {
            **COSTS,
            "interior_condition": "poor",
            "renovation_budget_yen": 4_000_000,
            "expected_sale_price_yen": 32_000_000,
        },
        profile("resale"),
    )
    assert r.rule_label == "resale_candidate"
    c = r.calculations_dict()
    # 32,000,000 - 20,000,000 - 1,400,000 - 4,000,000 - holding 6 x (22,000 + 100,000/12) - sale 1,280,000
    assert c["purchase_costs_yen"]["value"] == 1_400_000
    assert c["holding_costs_yen"]["value"] == 182_000
    assert c["resale_profit_yen"]["value"] == 5_138_000
    assert c["price_vs_comparable_pct"]["value"] == pytest.approx(-33.33)
    assert "interior_old" in r.tags and "price_below_area" in r.tags
    assert c["resale_profit_yen"]["formula"].startswith("expected_sale_price_yen - price_yen")


def test_old_interior_without_enough_discount_is_not_resale() -> None:
    # same numbers, but comparable listings are only 5 % more expensive per m2
    r = rules.evaluate(
        FACTS,
        {
            **COSTS,
            "interior_condition": "poor",
            "renovation_budget_yen": 4_000_000,
            "expected_sale_price_yen": 32_000_000,
        },
        {"unit_price_yen_m2": 350_000, "n": 5},
        profile("resale"),
        now=NOW,
    )
    assert r.rule_label == "low_value"
    assert any(x["key"] == "reason.resale_discount_short" for x in r.reasons)


def test_old_interior_with_thin_profit_is_not_resale() -> None:
    r = run(
        {
            **COSTS,
            "interior_condition": "poor",
            "renovation_budget_yen": 6_000_000,
            "expected_sale_price_yen": 28_000_000,
        },
        profile("resale"),
    )
    assert r.rule_label == "low_value"
    assert {x["key"] for x in r.reasons} >= {"reason.resale_profit_short", "reason.resale_margin_short"}


def test_margin_needs_safety_buffer() -> None:
    # margin 12.24 %: above 8 % but not above 8 % + 5 % safety buffer
    inputs = {
        **COSTS,
        "interior_condition": "poor",
        "renovation_budget_yen": 4_000_000,
        "expected_sale_price_yen": 29_000_000,
    }
    ok = run(inputs, profile("resale", resale_safety_margin_pct=0))
    short = run(inputs, profile("resale", resale_safety_margin_pct=5))
    assert ok.rule_label == "resale_candidate" and short.rule_label == "low_value"


def test_missing_renovation_inputs_is_insufficient_not_resale() -> None:
    r = run({**COSTS, "interior_condition": "poor"}, profile("resale"))
    assert r.rule_label == "insufficient_data"
    assert {"renovation_budget_yen", "expected_sale_price_yen"} <= set(r.missing)
    assert "renovation_possible_needs_calc" in r.tags and "renovation_cost_unconfirmed" in r.tags
    assert any(s["key"] == "next.renovation_budget_yen" for s in r.next_steps)


def test_rental_candidate_and_boundary() -> None:
    # gross = 116,667 x 12 / 20,000,000 = 7.00 % exactly: meets a 7 % minimum
    # net = (1,400,004 x 0.95 - 116,667 - 364,000) / 21,400,000 = 3.97 %
    r = run(
        {**COSTS, "monthly_rent_yen": 116_667, "interior_condition": "average"},
        profile("rental", min_net_yield_pct=3.5),
    )
    c = r.calculations_dict()
    assert c["gross_yield_pct"]["value"] == 7.0
    assert r.rule_label == "rental_candidate" and "rental_yield_good" in r.tags
    assert c["net_yield_pct"]["value"] == 3.97
    assert run({**COSTS, "monthly_rent_yen": 116_667}, profile("rental")).rule_label == "low_value"


def test_rental_net_needs_every_running_cost() -> None:
    # management fee missing: empty is not 0, so the net yield is not computed and the result is insufficient
    inputs = {**COSTS, "monthly_rent_yen": 150_000}
    del inputs["management_fee_monthly_yen"]
    r = run(inputs, profile("rental"))
    assert "net_yield_pct" not in r.calculations and "gross_yield_pct" in r.calculations
    assert r.rule_label == "insufficient_data" and "annual_running_costs_yen" in r.missing


def test_rental_gross_below_minimum_is_low_value() -> None:
    r = run({**COSTS, "monthly_rent_yen": 90_000}, profile("rental"))
    assert r.rule_label == "low_value"


def test_owner_candidate_and_good_but_expensive() -> None:
    r = run({"interior_condition": "good"}, profile("owner"))
    assert r.rule_label == "owner_candidate" and "move_in_ready" in r.tags
    pricey = rules.evaluate(
        FACTS, {"interior_condition": "renovated"}, {"unit_price_yen_m2": 280_000, "n": 4}, profile("owner"), now=NOW
    )
    assert pricey.rule_label == "low_value"
    assert pricey.reasons[0]["key"] == "reason.owner_good_but_expensive"


def test_building_age_alone_never_decides() -> None:
    r = run({"interior_condition": "good"}, profile("owner", max_age_years=20), built_year=1975)
    assert r.rule_label == "owner_candidate"
    assert {"old_building", "old_seismic_standard"} <= set(r.tags)
    assert any(x["key"] == "risk.age_over_max" for x in r.risks)


def test_blocking_risk_downgrades_resale() -> None:
    r = run(
        {
            **COSTS,
            "interior_condition": "poor",
            "renovation_budget_yen": 4_000_000,
            "expected_sale_price_yen": 32_000_000,
            "risk_flags": ["structure"],
        },
        profile("resale"),
    )
    assert r.rule_label != "resale_candidate"
    assert any(s["key"] == "next.investigate_risks" for s in r.next_steps)


def test_any_target_picks_candidate_and_low_value_needs_all_paths() -> None:
    full = {
        **COSTS,
        "interior_condition": "poor",
        "renovation_budget_yen": 4_000_000,
        "expected_sale_price_yen": 32_000_000,
        "monthly_rent_yen": 60_000,
    }
    assert run(full).rule_label == "resale_candidate"
    # every path checked and failed -> low value
    weak = {**full, "expected_sale_price_yen": 25_000_000, "renovation_budget_yen": 9_000_000}
    assert run(weak, profile("any", max_price_premium_pct=-50)).rule_label == "low_value"
    # rent unknown: that path cannot be judged -> not "low value"
    no_rent = {k: v for k, v in weak.items() if k != "monthly_rent_yen"}
    assert run(no_rent, profile("any", max_price_premium_pct=-50)).rule_label == "insufficient_data"


def test_no_price_is_insufficient() -> None:
    r = run({"interior_condition": "good"}, price_yen=None)
    assert r.rule_label == "insufficient_data" and "price_yen" in r.missing


# ---- AI output checks ------------------------------------------------------------------------------------


def _payload(r: rules.RuleResult) -> dict[str, Any]:
    return {
        "facts": FACTS,
        "user_inputs": {"monthly_rent_yen": 116_667},
        "profile": profile("rental"),
        "calculations": r.calculations_dict(),
    }  # noqa: E501


def _good(r: rules.RuleResult) -> dict[str, Any]:
    return {
        "primary_label": r.rule_label,
        "secondary_labels": r.tags[:2],
        "score": r.score + 5,
        "confidence": "medium",
        "reasons": [
            {
                "text": "表面利回りは7.0%で基準の7%を満たします。",
                "refs": ["gross_yield_pct", "profile.min_gross_yield_pct"],
            },
            {"text": "価格2,000万円、専有60㎡。", "refs": ["price_yen", "area_m2"]},
            {"text": "駅徒歩6分。", "refs": ["walk_minutes"]},
        ],
        "risks": [{"text": "1995年築のため修繕計画を確認してください。", "refs": ["built_year"]}],
        "next_steps": ["管理規約と修繕履歴を確認"],
    }


def _check(r: rules.RuleResult, data: dict[str, Any]) -> dict[str, Any]:
    payload = _payload(r)
    keys = (
        set(payload["facts"])
        | set(payload["user_inputs"])
        | set(payload["calculations"])
        | {f"profile.{k}" for k in payload["profile"]["thresholds"]}
    )
    return ai_eval.validate(
        data, allowed_labels=r.allowed_labels, rule_tags=r.tags, rule_score=r.score, ref_keys=keys, payload=payload
    )


def test_valid_ai_output_passes() -> None:
    r = run({**COSTS, "monthly_rent_yen": 116_667}, profile("rental", min_net_yield_pct=3.5))
    out = _check(r, _good(r))
    assert out["primary_label"] == "rental_candidate" and out["confidence"] == "medium"


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (
            lambda d: d["reasons"].append({"text": "相場家賃は月13.5万円と推定されます。", "refs": ["price_yen"]}),
            "untraceable_number",
        ),
        (lambda d: d.update(primary_label="resale_candidate"), "label_not_allowed"),
        (lambda d: d.update(score=d["score"] - 40), "score_out_of_window"),
        (lambda d: d["reasons"][0].update(refs=["market_rent"]), "unknown_ref"),
        (lambda d: d["reasons"][0].update(refs=[]), "missing_ref"),
        (lambda d: d.update(extra="x"), "schema_keys"),
        (lambda d: d["secondary_labels"].append("unit_price_high"), "tag_not_supported"),
        (lambda d: d.update(reasons=d["reasons"][:2]), "reason_count"),
        (lambda d: d["next_steps"].append("売却想定価格は2,480万円"), "untraceable_number"),
    ],
)
def test_ai_output_rejections(mutate: Any, reason: str) -> None:
    r = run({**COSTS, "monthly_rent_yen": 116_667}, profile("rental", min_net_yield_pct=3.5))
    data = _good(r)
    mutate(data)
    with pytest.raises(ai_eval.OutputRejected) as exc:
        _check(r, data)
    assert exc.value.reason == reason
