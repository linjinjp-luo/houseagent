"""AI services (FR-12) and investment assessments (FR-11).

Keys go in (``api_key`` on create / update) but never come out: responses carry only whether a key is set and
its last four characters.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from houseagent.ai import gateway
from houseagent.ai.providers import RECOMMENDED_MODELS
from houseagent.db.models import AIProviderConfig, InvestmentAssessment
from houseagent.db.session import get_db
from houseagent.errors import AppError, ErrorCode
from houseagent.investment import batch, rules
from houseagent.investment import service as investment
from houseagent.platform import secrets
from houseagent.services import settings_service as app_settings

router = APIRouter()


def _cfg(db: Session, cfg_id: int) -> AIProviderConfig:
    cfg = db.get(AIProviderConfig, cfg_id)
    if cfg is None or cfg.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND)
    return cfg


# ---- AI services -------------------------------------------------------------------------------------------


@router.get("/ai/providers")
def list_providers(db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    active = app_settings.get(db, "ai_active_provider_id")
    return {
        "enabled": bool(app_settings.get(db, "ai_enabled")),
        "active_id": active,
        "items": [gateway.to_dict(c, active) for c in gateway.list_configs(db)],
        "recommended": RECOMMENDED_MODELS,
        "sendable_fields": list(gateway.SENDABLE_FIELDS),
        "default_limits": gateway.DEFAULT_LIMITS,
        "secret_store": secrets.store().name,
        "secret_store_available": secrets.store().available(),
        "allowed_hosts": app_settings.get(db, "ai_allowed_hosts") or [],
    }


class ProviderIn(BaseModel):
    id: int | None = None
    provider_type: str | None = None
    display_name: str | None = None
    base_url: str | None = None
    model_id: str | None = None
    api_key: str | None = None
    enabled: bool | None = None
    activate: bool | None = None
    limits: dict[str, Any] | None = None
    allowed_fields: list[str] | None = None
    pricing: dict[str, Any] | None = None


@router.post("/ai/providers")
def save_provider(body: ProviderIn, db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    """Create or update (with ``id``). Only the fields sent are changed."""
    cfg = gateway.save_config(db, body.model_dump(exclude_unset=True))
    return gateway.to_dict(cfg, app_settings.get(db, "ai_active_provider_id"))


@router.post("/ai/providers/{cfg_id}/test")
def test_provider(cfg_id: int, db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    cfg = _cfg(db, cfg_id)
    result = gateway.test_connection(db, cfg)
    return {**result, "provider": gateway.to_dict(cfg, app_settings.get(db, "ai_active_provider_id"))}


@router.post("/ai/providers/{cfg_id}/activate")
def activate_provider(cfg_id: int, db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    cfg = _cfg(db, cfg_id)
    app_settings.update(db, {"ai_active_provider_id": cfg.id})
    return gateway.to_dict(cfg, cfg.id)


@router.delete("/ai/providers/{cfg_id}/secret")
def delete_provider_secret(cfg_id: int, db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    cfg = _cfg(db, cfg_id)
    gateway.delete_secret(cfg)
    return gateway.to_dict(cfg, app_settings.get(db, "ai_active_provider_id"))


@router.delete("/ai/providers/{cfg_id}")
def delete_provider(cfg_id: int, db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    gateway.delete_config(db, _cfg(db, cfg_id))
    return {"ok": True}


@router.get("/ai/usage")
def ai_usage(days: int = 30, db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    return gateway.usage_summary(db, max(1, min(days, 365)))


# ---- investment profiles -----------------------------------------------------------------------------------


@router.get("/investment-profiles")
def list_profiles(db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    return {
        "items": [investment.profile_to_dict(p) for p in investment.list_profiles(db)],
        "vocabulary": {
            "targets": list(rules.TARGETS),
            "thresholds": rules.THRESHOLDS,
            "assumptions": rules.ASSUMPTIONS,
            "labels": list(rules.LABELS),
            "tags": list(rules.TAGS),
            "inputs": rules.INPUT_FIELDS,
            "conditions": list(rules.CONDITIONS),
            "risk_flags": list(rules.RISK_FLAGS),
        },
    }


@router.post("/investment-profiles", status_code=201)
def create_profile(body: dict[str, Any], db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    return investment.profile_to_dict(investment.save_profile(db, body))


@router.patch("/investment-profiles/{profile_id}")
def update_profile(
    profile_id: int, body: dict[str, Any], db: Session = Depends(get_db, scope="function")
) -> dict[str, Any]:
    return investment.profile_to_dict(investment.save_profile(db, body, profile_id))


@router.delete("/investment-profiles/{profile_id}")
def delete_profile(profile_id: int, db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    investment.delete_profile(db, profile_id)
    return {"ok": True}


# ---- per-property inputs and assessments -------------------------------------------------------------------


@router.get("/properties/{listing_id}/investment-inputs")
def get_inputs(listing_id: int, db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    return investment.get_inputs(db, listing_id)


@router.put("/properties/{listing_id}/investment-inputs")
def put_inputs(
    listing_id: int, body: dict[str, Any], db: Session = Depends(get_db, scope="function")
) -> dict[str, Any]:
    return investment.save_inputs(db, listing_id, body)


class AssessIn(BaseModel):
    profile_id: int | None = None
    use_ai: bool = True


@router.post("/properties/{listing_id}/investment-assessments")
def assess_property(
    listing_id: int, body: AssessIn | None = None, db: Session = Depends(get_db, scope="function")
) -> dict[str, Any]:
    body = body or AssessIn()
    a = investment.assess(db, listing_id, body.profile_id, use_ai=body.use_ai, trigger="manual")
    return investment.to_dict(db, a, stale=False)


@router.get("/properties/{listing_id}/investment-assessments")
def assessment_history(listing_id: int, db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    cfg, reason = investment.ai_status(db)
    return {
        "items": investment.history(db, listing_id),
        "ai_ready": cfg is not None,
        "ai_unavailable_reason": reason,
        "profiles": [investment.profile_to_dict(p) for p in investment.list_profiles(db)],
    }


class BatchIn(BaseModel):
    scope: str = "ids"  # ids | favorites
    listing_ids: list[int] | None = None
    profile_id: int | None = None
    use_ai: bool = True
    estimate_only: bool = False


@router.post("/properties/investment-assessments:batch")
def batch_assess(body: BatchIn, db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    """With ``estimate_only`` returns counts and estimated usage; otherwise starts the background batch."""
    if body.scope not in ("ids", "favorites"):
        raise AppError(ErrorCode.VALIDATION_ERROR, {"field": "scope"})
    ids = batch.resolve_ids(db, body.scope, body.listing_ids)
    if body.estimate_only:
        return batch.estimate(db, ids, body.profile_id, body.use_ai)
    trigger = "favorites" if body.scope == "favorites" else "batch"
    return batch.start(db, ids, body.profile_id, body.use_ai, trigger).to_dict()


@router.get("/investment-batches/current")
def current_batch() -> dict[str, Any] | None:
    job = batch.current()
    return job.to_dict() if job else None


@router.post("/investment-batches/{job_id}/cancel")
def cancel_batch(job_id: str) -> dict[str, Any]:
    return batch.cancel(job_id).to_dict()


class OverrideIn(BaseModel):
    user_label: str
    user_tags: list[str] = []
    reason: str


@router.patch("/investment-assessments/{assessment_id}/override")
def override_assessment(
    assessment_id: int, body: OverrideIn, db: Session = Depends(get_db, scope="function")
) -> dict[str, Any]:
    investment.override(db, assessment_id, body.user_label, body.user_tags, body.reason)
    a = db.get(InvestmentAssessment, assessment_id)
    assert a is not None
    return investment.to_dict(db, a)
