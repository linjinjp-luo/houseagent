"""AI part of the investment assessment (spec 4.6.4 / 15.14): prompt, fixed output schema and output checks.

The AI only explains, summarises risks, picks tags and states its confidence, over numbers the rules already
computed. Its output is rejected (never stored as a formal assessment) when it:

* does not match the schema (unknown keys, wrong types, out-of-range values);
* chooses a primary label the rules do not allow, or a tag the rules did not find;
* moves the score more than ``SCORE_WINDOW`` points from the rules' reference score;
* cites something that is not an input or a calculation, or writes a number (amount, ratio, year ...) that
  cannot be traced to an input or a calculation - i.e. an invented rent, cost, sale price or tax.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal
from typing import Any

from houseagent.investment.rules import LABELS, TAGS

PROMPT_VERSION = "1.0"
SCORE_WINDOW = 15
CONFIDENCE = ("high", "medium", "low")
_LANG = {"ja": "Japanese", "zh": "Simplified Chinese", "en": "English"}

SYSTEM_PROMPT = """You assist an individual in Japan with a first screening of a property listing they track in a
personal tool. You receive facts about one listing, values the user entered (each with its source), the user's
investment standard, and calculations and a rule result that were computed deterministically.

Your job: explain the result for this user, summarise the main reasons and risks, choose the fitting fact tags,
and state how confident the screening is given the data.

Rules you must follow:
- The primary label must be one of `allowed_labels`. Only choose "insufficient_data" instead of the rule label
  when the data given conflicts or is unreliable, and say why.
- Tags must come from `rule_tags`.
- `score` must stay within ±15 of `rule_score`.
- Use only numbers that appear in `facts`, `user_inputs`, `profile` or `calculations`. Do not estimate or invent
  market rent, renovation cost, sale price, transaction prices, vacancy, taxes, building quality or legal /
  ownership status. If something is unknown, say it is unknown.
- Every reason and risk lists in `refs` the keys (from `facts`, `user_inputs`, `profile` or `calculations`) it
  is based on.
- Give 3 to 5 reasons. Keep each sentence short and concrete.
- This is a preliminary screening, not an appraisal, inspection, legal review, loan decision or investment advice.
- Write all text in the language given in `language`."""


def output_schema() -> dict[str, Any]:
    item = {
        "type": "object",
        "properties": {"text": {"type": "string"}, "refs": {"type": "array", "items": {"type": "string"}}},
        "required": ["text", "refs"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "primary_label": {"type": "string", "enum": list(LABELS)},
            "secondary_labels": {"type": "array", "items": {"type": "string", "enum": list(TAGS)}},
            "score": {"type": "integer"},
            "confidence": {"type": "string", "enum": list(CONFIDENCE)},
            "reasons": {"type": "array", "items": item},
            "risks": {"type": "array", "items": item},
            "next_steps": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["primary_label", "secondary_labels", "score", "confidence", "reasons", "risks", "next_steps"],
        "additionalProperties": False,
    }


def build_user_message(payload: dict[str, Any], language: str) -> str:
    return json.dumps({"language": _LANG.get(language, "Japanese"), **payload}, ensure_ascii=False, default=str)


class OutputRejected(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


_NUM = re.compile(r"(?<![A-Za-z])\d[\d,]*(?:\.\d+)?")


def _numbers_in(value: Any, out: set[Decimal]) -> None:
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, int | float | Decimal):
        out.add(Decimal(str(value)))
        return
    if isinstance(value, str):
        for m in _NUM.findall(value):
            try:
                out.add(Decimal(m.replace(",", "")))
            except ArithmeticError:
                continue
        return
    if isinstance(value, dict):
        for v in value.values():
            _numbers_in(v, out)
        return
    if isinstance(value, list | tuple):
        for v in value:
            _numbers_in(v, out)


def traceable_numbers(payload: dict[str, Any]) -> set[Decimal]:
    """Every number in the request, plus the usual ways of writing it (万円, 億円, 千円, rounded)."""
    base: set[Decimal] = set()
    _numbers_in(payload, base)
    out: set[Decimal] = set()
    for v in base:
        for scaled in (v, v / 10_000, v / 100_000_000, v / 1_000, v / 1_000_000):
            out.add(scaled)
    return out


def _traceable(n: Decimal, allowed: set[Decimal]) -> bool:
    if n <= 12 and n == n.to_integral_value():
        return True  # counts, months, ordinals ("3 reasons", "1LDK")
    for a in allowed:
        if a == 0:
            continue
        # rounding to what a person would write: 1 decimal, whole number, or ~1 %
        if abs(n - a) <= Decimal("0.051") * max(Decimal(1), Decimal(10) ** (len(str(int(abs(a)))) - 2)):
            return True
        if abs(n - a) / abs(a) <= Decimal("0.01"):
            return True
    return False


def validate(
    data: dict[str, Any],
    *,
    allowed_labels: list[str],
    rule_tags: list[str],
    rule_score: int,
    ref_keys: set[str],
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Strict checks; returns the cleaned result or raises OutputRejected."""
    expected = set(output_schema()["properties"])
    if set(data) != expected:
        raise OutputRejected("schema_keys")
    label = data["primary_label"]
    if label not in allowed_labels:
        raise OutputRejected("label_not_allowed")
    tags = data["secondary_labels"]
    if not isinstance(tags, list) or any(t not in rule_tags for t in tags):
        raise OutputRejected("tag_not_supported")
    score = data["score"]
    if not isinstance(score, int) or isinstance(score, bool) or not 0 <= score <= 100:
        raise OutputRejected("score_range")
    if abs(score - rule_score) > SCORE_WINDOW:
        raise OutputRejected("score_out_of_window")
    if data["confidence"] not in CONFIDENCE:
        raise OutputRejected("confidence")
    reasons, risks, steps = data["reasons"], data["risks"], data["next_steps"]
    if not isinstance(reasons, list) or not 3 <= len(reasons) <= 5:
        raise OutputRejected("reason_count")
    if not isinstance(risks, list) or len(risks) > 8 or not isinstance(steps, list) or len(steps) > 8:
        raise OutputRejected("list_size")
    allowed_numbers = traceable_numbers(payload)
    texts: list[str] = []
    for group, need_ref in ((reasons, True), (risks, False)):
        for item in group:
            if not isinstance(item, dict) or set(item) != {"text", "refs"} or not isinstance(item["text"], str):
                raise OutputRejected("item_shape")
            refs = item["refs"]
            if not isinstance(refs, list) or any(r not in ref_keys for r in refs):
                raise OutputRejected("unknown_ref")
            if need_ref and not refs:
                raise OutputRejected("missing_ref")
            texts.append(item["text"])
    if any(not isinstance(s, str) for s in steps):
        raise OutputRejected("item_shape")
    texts += steps
    for t in texts:
        if len(t) > 600:
            raise OutputRejected("text_too_long")
        for m in _NUM.findall(t):
            n = Decimal(m.replace(",", ""))
            if not _traceable(n, allowed_numbers):
                raise OutputRejected("untraceable_number")
    return {
        "primary_label": label,
        "secondary_labels": [t for t in TAGS if t in tags],
        "score": score,
        "confidence": data["confidence"],
        "reasons": [{"text": r["text"], "refs": r["refs"]} for r in reasons],
        "risks": [{"text": r["text"], "refs": r["refs"]} for r in risks],
        "next_steps": list(steps),
    }
