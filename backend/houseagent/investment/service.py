"""Investment assessment use cases (FR-11): data preparation, rules, optional AI, history and user overrides.

Flow for one property: collect permitted facts + user inputs (with sources) + comparables -> deterministic rules
-> if AI is on, ready and the property meets the minimum data (rule label is not "insufficient_data"), ask the AI
to explain and validate its output -> store a new assessment record (never overwrite).
When AI is off or fails, a rules-only assessment is stored; search and listing management are unaffected.
"""

from __future__ import annotations

import hashlib
import json
import logging
import statistics
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from houseagent.ai import gateway
from houseagent.ai.providers import AIError, AIRequest
from houseagent.db.models import (
    InvestmentAssessment,
    InvestmentInput,
    InvestmentOverride,
    InvestmentProfile,
    Listing,
    ListingSource,
    utcnow,
)
from houseagent.errors import AppError, ErrorCode
from houseagent.investment import ai_eval, rules
from houseagent.services import settings_service as app_settings

log = logging.getLogger(__name__)

MIN_COMPARABLES = 3


# ---- profiles ------------------------------------------------------------------------------------------


def profile_to_dict(p: InvestmentProfile) -> dict[str, Any]:
    return {
        "id": p.id,
        "name": p.name,
        "target_type": p.target_type,
        "thresholds": p.thresholds_json or {},
        "assumptions": p.assumptions_json or {},
        "preferences": p.owner_occupancy_preferences_json or {},
        "is_default": p.is_default,
        "updated_at": p.updated_at,
    }


def _clean_numbers(raw: dict[str, Any] | None, allowed: dict[str, str], field: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in (raw or {}).items():
        if k not in allowed or v in (None, ""):
            continue
        try:
            num = float(v)
        except (TypeError, ValueError) as exc:
            raise AppError(ErrorCode.VALIDATION_ERROR, {"field": f"{field}.{k}"}) from exc
        if num < 0 and k not in ("max_price_premium_pct",):
            raise AppError(ErrorCode.VALIDATION_ERROR, {"field": f"{field}.{k}"})
        out[k] = int(num) if num.is_integer() else num
    return out


def save_profile(db: Session, body: dict[str, Any], profile_id: int | None = None) -> InvestmentProfile:
    p = db.get(InvestmentProfile, profile_id) if profile_id else None
    if profile_id and (p is None or p.deleted_at is not None):
        raise AppError(ErrorCode.NOT_FOUND)
    if p is None:
        p = InvestmentProfile(name="", target_type="any")
        db.add(p)
    if "name" in body or not p.name:
        p.name = (body.get("name") or "").strip()[:200]
        if not p.name:
            raise AppError(ErrorCode.VALIDATION_ERROR, {"field": "name"})
    if "target_type" in body:
        if body["target_type"] not in rules.TARGETS:
            raise AppError(ErrorCode.VALIDATION_ERROR, {"field": "target_type"})
        p.target_type = body["target_type"]
    if "thresholds" in body:
        p.thresholds_json = _clean_numbers(body["thresholds"], rules.THRESHOLDS, "thresholds")
    if "assumptions" in body:
        p.assumptions_json = _clean_numbers(body["assumptions"], rules.ASSUMPTIONS, "assumptions")
    if "preferences" in body:
        prefs = body["preferences"] or {}
        p.owner_occupancy_preferences_json = {
            "cities": [str(c) for c in prefs.get("cities") or []],
            "layouts": [str(c) for c in prefs.get("layouts") or []],
            **_clean_numbers(
                {k: prefs.get(k) for k in ("min_area_m2", "max_commute_minutes")},
                {"min_area_m2": "m2", "max_commute_minutes": "minutes"},
                "preferences",
            ),
        }
    db.flush()
    others = list_profiles(db)
    if body.get("is_default") or not any(o.is_default for o in others):
        for o in others:
            o.is_default = o.id == p.id
    db.flush()
    return p


def list_profiles(db: Session) -> list[InvestmentProfile]:
    return list(
        db.execute(
            select(InvestmentProfile).where(InvestmentProfile.deleted_at.is_(None)).order_by(InvestmentProfile.id)
        ).scalars()
    )


def get_profile(db: Session, profile_id: int | None) -> InvestmentProfile:
    if profile_id:
        p = db.get(InvestmentProfile, profile_id)
        if p is None or p.deleted_at is not None:
            raise AppError(ErrorCode.NOT_FOUND, {"profile_id": profile_id})
        return p
    p = next((x for x in list_profiles(db) if x.is_default), None)
    if p is None:
        raise AppError(ErrorCode.CONFLICT, message_key="error.investment_profile_required")
    return p


def delete_profile(db: Session, profile_id: int) -> None:
    p = get_profile(db, profile_id)
    p.deleted_at = utcnow()
    p.is_default = False
    rest = list_profiles(db)
    if rest and not any(o.is_default for o in rest):
        rest[0].is_default = True


# ---- per-property inputs --------------------------------------------------------------------------------


def get_inputs(db: Session, listing_id: int) -> dict[str, Any]:
    row = db.execute(select(InvestmentInput).where(InvestmentInput.listing_id == listing_id)).scalar_one_or_none()
    return dict(row.values_json or {}) if row else {}


def save_inputs(db: Session, listing_id: int, values: dict[str, Any]) -> dict[str, Any]:
    """values: {field: {"value": ..., "source": "..."}} - a null value removes the field. Every value needs a
    source (the user, a named agent / site, a quote ...) so results can show where numbers came from."""
    _listing(db, listing_id)
    row = db.execute(select(InvestmentInput).where(InvestmentInput.listing_id == listing_id)).scalar_one_or_none()
    if row is None:
        row = InvestmentInput(listing_id=listing_id, values_json={})
        db.add(row)
    current = dict(row.values_json or {})
    now = utcnow().isoformat()
    for k, item in values.items():
        if k not in rules.INPUT_FIELDS:
            raise AppError(ErrorCode.VALIDATION_ERROR, {"field": k})
        v = item.get("value") if isinstance(item, dict) else item
        if v in (None, "", []):
            current.pop(k, None)
            continue
        kind = rules.INPUT_FIELDS[k]
        if kind == "enum":
            if v not in rules.CONDITIONS:
                raise AppError(ErrorCode.VALIDATION_ERROR, {"field": k})
        elif kind == "list":
            if not isinstance(v, list) or any(f not in rules.RISK_FLAGS for f in v):
                raise AppError(ErrorCode.VALIDATION_ERROR, {"field": k})
        else:
            try:
                num = float(v)
            except (TypeError, ValueError) as exc:
                raise AppError(ErrorCode.VALIDATION_ERROR, {"field": k}) from exc
            if num < 0:
                raise AppError(ErrorCode.VALIDATION_ERROR, {"field": k})
            v = int(num) if num.is_integer() else num
        source = ((item.get("source") if isinstance(item, dict) else None) or "").strip()[:200] or "user"
        prev = current.get(k) or {}
        changed = prev.get("value") != v or prev.get("source") != source
        current[k] = {"value": v, "source": source, "updated_at": now if changed else prev.get("updated_at", now)}
        if isinstance(item, dict) and item.get("note"):
            current[k]["note"] = str(item["note"])[:300]
    row.values_json = current
    row.updated_at = utcnow()
    db.flush()
    return current


# ---- data preparation ------------------------------------------------------------------------------------


def _listing(db: Session, listing_id: int) -> Listing:
    listing = db.get(Listing, listing_id)
    if listing is None or listing.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND)
    return listing


def _primary_source(db: Session, listing_id: int) -> ListingSource | None:
    sources = db.execute(select(ListingSource).where(ListingSource.listing_id == listing_id)).scalars().all()
    active = [s for s in sources if s.observation_status == "active"] or list(sources)
    return max(active, key=lambda s: s.last_seen_at) if active else None


def _facts(listing: Listing, src: ListingSource | None) -> dict[str, Any]:
    def pick(name: str) -> Any:
        v = getattr(src, name, None) if src is not None else None
        return v if v is not None else getattr(listing, name, None)

    return {
        "deal_type": listing.deal_type,
        "property_type": pick("property_type"),
        "prefecture": pick("prefecture"),
        "city": pick("city"),
        "address": getattr(src, "address", None) or listing.address_normalized,
        "price_yen": getattr(src, "price_yen", None),
        "area_m2": pick("area_m2"),
        "land_area_m2": pick("land_area_m2"),
        "layout": pick("layout"),
        "floor": pick("floor"),
        "built_year": pick("built_year"),
        "station": getattr(src, "station", None),
        "walk_minutes": getattr(src, "walk_minutes", None),
        "title": getattr(src, "title", None),
    }


def comparables(db: Session, listing_id: int, facts: dict[str, Any]) -> dict[str, Any] | None:
    """Median unit price of other observed sale listings of the same type in the same city (HouseAgent's own
    observations - not transaction prices). None when fewer than MIN_COMPARABLES are known."""
    if not facts.get("city") or not facts.get("property_type"):
        return None
    rows = db.execute(
        select(ListingSource.listing_id, ListingSource.price_yen, ListingSource.area_m2, ListingSource.last_seen_at)
        .join(Listing, Listing.id == ListingSource.listing_id)
        .where(
            ListingSource.city == facts["city"],
            ListingSource.property_type == facts["property_type"],
            ListingSource.deal_type == "buy",
            ListingSource.price_yen.is_not(None),
            ListingSource.area_m2 > 0,
            ListingSource.listing_id != listing_id,
            Listing.deleted_at.is_(None),
        )
    ).all()
    per_listing: dict[int, float] = {}
    seen: list[datetime] = []
    for lid, price, area, last_seen in rows:
        per_listing.setdefault(lid, price / area)
        seen.append(last_seen)
    if len(per_listing) < MIN_COMPARABLES:
        return None
    return {
        "unit_price_yen_m2": round(statistics.median(per_listing.values())),
        "n": len(per_listing),
        "from": min(seen).date().isoformat(),
        "to": max(seen).date().isoformat(),
    }


def prepare(db: Session, listing_id: int, profile: InvestmentProfile) -> dict[str, Any]:
    listing = _listing(db, listing_id)
    src = _primary_source(db, listing_id)
    facts = _facts(listing, src)
    raw_inputs = get_inputs(db, listing_id)
    inputs = {k: v.get("value") for k, v in raw_inputs.items()}
    if "interior_condition" not in inputs and facts.get("title"):
        # The site says so in the listing title: a sourced fact, not a guess.
        import re

        if re.search(r"リフォーム済|リノベーション済|リノベ済|フルリフォーム", facts["title"]):
            inputs["interior_condition"] = "renovated"
            raw_inputs["interior_condition"] = {
                "value": "renovated",
                "source": f"site:{src.site_id if src else ''} title",
                "updated_at": src.last_seen_at.isoformat() if src else None,
            }
    comp = comparables(db, listing_id, facts)
    prof = {
        "target_type": profile.target_type,
        "thresholds": profile.thresholds_json or {},
        "assumptions": profile.assumptions_json or {},
        "preferences": profile.owner_occupancy_preferences_json or {},
    }
    sources: dict[str, Any] = {
        "listing": {
            "site_id": src.site_id if src else None,
            "url": src.source_url if src else None,
            "last_seen_at": src.last_seen_at.isoformat() if src else None,
            "fields": [k for k, v in facts.items() if v is not None],
        },
        "user_inputs": {
            k: {"source": v.get("source"), "updated_at": v.get("updated_at")} for k, v in raw_inputs.items()
        },
        "profile": {"id": profile.id, "name": profile.name, "updated_at": profile.updated_at.isoformat()},
    }
    if comp:
        sources["comparables"] = {"kind": "houseagent_observed_listings", **comp}
    snapshot = {
        "facts": facts,
        "inputs": inputs,
        "comparable": comp,
        "profile": prof,
        "rule_version": rules.RULE_VERSION,
    }
    digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True, default=str).encode()).hexdigest()
    return {
        "facts": facts,
        "inputs": inputs,
        "comparable": comp,
        "profile": prof,
        "sources": sources,
        "snapshot": snapshot,
        "hash": digest,
    }


def _ai_payload(prep: dict[str, Any], rr: rules.RuleResult, allowed_fields: list[str]) -> dict[str, Any]:
    facts = {k: v for k, v in prep["facts"].items() if k in allowed_fields or k == "deal_type"}
    return {
        "facts": facts,
        "user_inputs": prep["inputs"],
        "profile": prep["profile"],
        "calculations": rr.calculations_dict(),
        "rule_result": {
            "rule_label": rr.rule_label,
            "allowed_labels": rr.allowed_labels,
            "paths": {k: {"outcome": p.outcome, "reasons": p.reasons} for k, p in rr.paths.items()},
            "missing_fields": rr.missing,
        },
        "rule_tags": rr.tags,
        "rule_score": rr.score,
        "allowed_labels": rr.allowed_labels,
    }


def _ref_keys(payload: dict[str, Any]) -> set[str]:
    keys = set(payload["facts"]) | set(payload["user_inputs"]) | set(payload["calculations"])
    keys |= {f"profile.{k}" for part in ("thresholds", "assumptions") for k in payload["profile"][part]}
    keys |= {f"profile.{k}" for k in payload["profile"]["preferences"]}
    return keys | {"comparable_unit_price_yen_m2", "price_yen", "built_year", "walk_minutes", "risk_flags"}


def ai_status(db: Session) -> tuple[Any | None, str | None]:
    """(active config, None) when AI can be used, else (None, reason)."""
    try:
        return gateway.require_ready(db), None
    except AppError as exc:
        return None, (exc.message_key or "error.ai_not_configured").removeprefix("error.")


def assess(
    db: Session,
    listing_id: int,
    profile_id: int | None = None,
    *,
    use_ai: bool = True,
    trigger: str = "manual",
) -> InvestmentAssessment:
    listing = _listing(db, listing_id)
    if listing.deal_type != "buy":
        raise AppError(ErrorCode.VALIDATION_ERROR, message_key="error.investment_buy_only")
    profile = get_profile(db, profile_id)
    prep = prepare(db, listing_id, profile)
    rr = rules.evaluate(prep["facts"], prep["inputs"], prep["comparable"], prep["profile"])
    cid = uuid.uuid4().hex[:16]
    a = InvestmentAssessment(
        listing_id=listing_id,
        profile_id=profile.id,
        status="rules_only",
        primary_label=rr.rule_label,
        rule_label=rr.rule_label,
        secondary_labels_json=rr.tags,
        score=rr.score,
        confidence=None,
        rationale_json=rr.reasons,
        risks_json=rr.risks,
        missing_fields_json=rr.missing,
        calculations_json={
            "values": rr.calculations_dict(),
            "paths": {k: {"outcome": p.outcome, "missing": p.missing} for k, p in rr.paths.items()},
            "assumptions": rr.assumptions,
            "next_steps": rr.next_steps,
        },
        sources_json=prep["sources"],
        input_snapshot_json=prep["snapshot"],
        input_snapshot_hash=prep["hash"],
        rule_version=rules.RULE_VERSION,
        trigger=trigger,
        correlation_id=cid,
    )
    db.add(a)
    db.flush()
    if not use_ai:
        a.ai_error = "not_requested"
        return a
    if rr.rule_label == "insufficient_data":
        # Local rules first: no AI call until the minimum data is there (spec 4.7.2).
        a.ai_error = "insufficient_data"
        return a
    cfg, reason = ai_status(db)
    if cfg is None:
        a.ai_error = reason
        return a
    payload = _ai_payload(prep, rr, list(cfg.allowed_fields_json or []))
    language = app_settings.get(db, "language")
    request = AIRequest(
        system=ai_eval.SYSTEM_PROMPT,
        user=ai_eval.build_user_message(payload, language),
        schema=ai_eval.output_schema(),
        schema_name="investment_assessment",
        max_tokens=8000,
    )
    a.prompt_version = ai_eval.PROMPT_VERSION
    a.model_provider, a.model_name = cfg.provider_type, cfg.model_id
    try:
        result = gateway.call(
            db, cfg, "assessment", lambda p: p.assess_property(request), assessment_id=a.id, correlation_id=cid
        )
    except AIError as exc:
        a.ai_error = exc.code
        return a
    except AppError as exc:
        a.ai_error = (exc.message_key or "error.ai_unavailable").removeprefix("error.")
        return a
    a.model_version = result.model_version
    try:
        clean = ai_eval.validate(
            result.data,
            allowed_labels=rr.allowed_labels,
            rule_tags=rr.tags,
            rule_score=rr.score,
            ref_keys=_ref_keys(payload),
            payload=payload,
        )
    except ai_eval.OutputRejected as exc:
        # Not stored as a formal assessment: the rules result stays, flagged as "AI result abnormal".
        log.info("AI assessment output rejected [%s]: %s", cid, exc.reason)
        a.status, a.ai_error = "ai_rejected", f"ai_output_invalid:{exc.reason}"
        return a
    a.status = "ai"
    a.primary_label = clean["primary_label"]
    a.secondary_labels_json = clean["secondary_labels"]
    a.score = clean["score"]
    a.confidence = clean["confidence"]
    a.rationale_json = clean["reasons"]
    a.risks_json = rr.risks + clean["risks"]
    a.calculations_json = {**a.calculations_json, "ai_next_steps": clean["next_steps"]}
    db.flush()
    return a


# ---- reading ---------------------------------------------------------------------------------------------


def override(db: Session, assessment_id: int, label: str, tags: list[str], reason: str) -> InvestmentOverride:
    a = db.get(InvestmentAssessment, assessment_id)
    if a is None:
        raise AppError(ErrorCode.NOT_FOUND)
    if label not in rules.LABELS or any(t not in rules.TAGS for t in tags):
        raise AppError(ErrorCode.VALIDATION_ERROR, {"field": "label"})
    if not (reason or "").strip():
        raise AppError(ErrorCode.VALIDATION_ERROR, {"field": "reason"}, message_key="error.override_reason_required")
    o = InvestmentOverride(assessment_id=a.id, user_label=label, user_tags_json=tags, reason=reason.strip()[:2000])
    db.add(o)
    db.flush()
    return o


def to_dict(db: Session, a: InvestmentAssessment, *, stale: bool | None = None) -> dict[str, Any]:
    overrides = (
        db.execute(
            select(InvestmentOverride).where(InvestmentOverride.assessment_id == a.id).order_by(InvestmentOverride.id)
        )
        .scalars()
        .all()
    )
    last = overrides[-1] if overrides else None
    return {
        "id": a.id,
        "listing_id": a.listing_id,
        "profile_id": a.profile_id,
        "status": a.status,
        "primary_label": a.primary_label,
        "rule_label": a.rule_label,
        "final_label": last.user_label if last else a.primary_label,
        "final_tags": last.user_tags_json if last else a.secondary_labels_json,
        "secondary_labels": a.secondary_labels_json,
        "score": a.score,
        "confidence": a.confidence,
        "reasons": a.rationale_json,
        "risks": a.risks_json,
        "missing_fields": a.missing_fields_json,
        "calculations": a.calculations_json,
        "sources": a.sources_json,
        "input_snapshot": a.input_snapshot_json,
        "input_snapshot_hash": a.input_snapshot_hash,
        "rule_version": a.rule_version,
        "prompt_version": a.prompt_version,
        "model_provider": a.model_provider,
        "model_name": a.model_name,
        "model_version": a.model_version,
        "ai_error": a.ai_error,
        "trigger": a.trigger,
        "created_at": a.created_at,
        "overrides": [
            {
                "id": o.id,
                "user_label": o.user_label,
                "user_tags": o.user_tags_json,
                "reason": o.reason,
                "created_at": o.created_at,
            }
            for o in overrides
        ],
        "stale": stale,
    }


def history(db: Session, listing_id: int) -> list[dict[str, Any]]:
    _listing(db, listing_id)
    rows = (
        db.execute(
            select(InvestmentAssessment)
            .where(InvestmentAssessment.listing_id == listing_id)
            .order_by(InvestmentAssessment.id.desc())
        )
        .scalars()
        .all()
    )
    current_hash: str | None = None
    if rows:
        try:
            prof = db.get(InvestmentProfile, rows[0].profile_id) if rows[0].profile_id else None
            if prof is not None and prof.deleted_at is None:
                current_hash = prepare(db, listing_id, prof)["hash"]
        except AppError:
            current_hash = None
    return [
        to_dict(db, a, stale=(current_hash is not None and a.input_snapshot_hash != current_hash) if i == 0 else None)
        for i, a in enumerate(rows)
    ]


def latest_by_listing(db: Session, listing_ids: list[int]) -> dict[int, dict[str, Any]]:
    """Compact latest result per listing for list screens (final label after user override)."""
    if not listing_ids:
        return {}
    from sqlalchemy import func

    latest_ids = (
        select(func.max(InvestmentAssessment.id))
        .where(InvestmentAssessment.listing_id.in_(listing_ids))
        .group_by(InvestmentAssessment.listing_id)
    )
    rows = db.execute(select(InvestmentAssessment).where(InvestmentAssessment.id.in_(latest_ids))).scalars().all()
    ov = {
        o.assessment_id: o
        for o in db.execute(
            select(InvestmentOverride)
            .where(InvestmentOverride.assessment_id.in_([r.id for r in rows]))
            .order_by(InvestmentOverride.id)
        ).scalars()
    }
    return {
        r.listing_id: {
            "id": r.id,
            "status": r.status,
            "label": ov[r.id].user_label if r.id in ov else r.primary_label,
            "overridden": r.id in ov,
            "tags": (ov[r.id].user_tags_json if r.id in ov else r.secondary_labels_json)[:4],
            "score": r.score,
            "confidence": r.confidence,
            "missing_count": len(r.missing_fields_json or []),
            "created_at": r.created_at,
        }
        for r in rows
    }
