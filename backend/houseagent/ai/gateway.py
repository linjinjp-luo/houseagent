"""AI service configuration and the request gateway (spec 4.7 / 15.15, FR-12).

* Configurations hold provider, display name, endpoint, model, limits, the fields that may be sent and a price
  table. The key goes to OS-protected storage (``platform.secrets``); the database keeps a reference and the
  last four characters. The key is never returned to the frontend.
* Every external call goes through ``call``: the global AI switch, daily call / cost limits, a per-config
  concurrency limit, a timeout, limited retries for transient errors and a usage record (no request or
  response text) per attempt.
* When AI is off, nothing here loads a key or opens a connection.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from houseagent.ai.providers import (
    PROVIDER_TYPES,
    RECOMMENDED_MODELS,
    AIError,
    AIProvider,
    AIResult,
    ProviderSettings,
    check_base_url,
    estimate_cost,
    make_provider,
)
from houseagent.db.models import AIProviderConfig, AIUsageRecord, utcnow
from houseagent.errors import AppError, ErrorCode
from houseagent.platform import secrets
from houseagent.services import settings_service as app_settings

log = logging.getLogger(__name__)

DEFAULT_LIMITS: dict[str, Any] = {
    "timeout_s": 60,
    "max_retries": 2,
    "daily_call_limit": 100,
    "daily_cost_limit": None,
    "batch_max": 50,
    "concurrency": 2,
}
# Structured listing fields that may be sent; the full address is off by default (spec 4.7.1).
SENDABLE_FIELDS = (
    "property_type",
    "prefecture",
    "city",
    "address",
    "price_yen",
    "area_m2",
    "land_area_m2",
    "layout",
    "floor",
    "built_year",
    "station",
    "walk_minutes",
    "title",
)
DEFAULT_FIELDS = [f for f in SENDABLE_FIELDS if f not in ("address", "title")]

# How providers can be swapped: anything the business layer needs comes from here.
provider_factory: Callable[[ProviderSettings], AIProvider] = make_provider

_semaphores: dict[int, threading.BoundedSemaphore] = {}
_sem_lock = threading.Lock()


def _err(code: str, **details: Any) -> AppError:
    kind = {
        "ai_disabled": ErrorCode.CONFLICT,
        "ai_not_configured": ErrorCode.CONFLICT,
        "ai_key_missing": ErrorCode.CONFLICT,
        "ai_daily_limit": ErrorCode.RATE_LIMITED,
        "ai_rate_limited": ErrorCode.RATE_LIMITED,
    }.get(code, ErrorCode.VALIDATION_ERROR)
    return AppError(kind, details or None, message_key=f"error.{code}")


def limits(cfg: AIProviderConfig) -> dict[str, Any]:
    return {**DEFAULT_LIMITS, **(cfg.limits_json or {})}


def _ref(cfg: AIProviderConfig) -> str:
    return cfg.key_reference or f"ai_provider_{cfg.id}"


def key_state(cfg: AIProviderConfig) -> str:
    """configured | env | missing (never the value)."""
    if cfg.key_reference and secrets.store().has(cfg.key_reference):
        return "configured"
    import os

    return "env" if os.environ.get(secrets.env_name(_ref(cfg))) else "missing"


def to_dict(cfg: AIProviderConfig, active_id: int | None = None) -> dict[str, Any]:
    return {
        "id": cfg.id,
        "provider_type": cfg.provider_type,
        "display_name": cfg.display_name,
        "base_url": cfg.base_url,
        "custom_url": cfg.base_url is not None,
        "model_id": cfg.model_id,
        "enabled": cfg.enabled,
        "active": cfg.id == active_id,
        "key_state": key_state(cfg),
        "key_last4": cfg.key_last4,
        "limits": limits(cfg),
        "allowed_fields": list(cfg.allowed_fields_json or []),
        "pricing": cfg.pricing_json or {},
        "last_test_at": cfg.last_test_at,
        "last_test_status": cfg.last_test_status,
        "last_test_detail": cfg.last_test_detail,
    }


def list_configs(db: Session) -> list[AIProviderConfig]:
    return list(
        db.execute(
            select(AIProviderConfig).where(AIProviderConfig.deleted_at.is_(None)).order_by(AIProviderConfig.id)
        ).scalars()
    )


def active_config(db: Session) -> AIProviderConfig | None:
    aid = app_settings.get(db, "ai_active_provider_id")
    cfg = db.get(AIProviderConfig, aid) if aid else None
    if cfg is None or cfg.deleted_at is not None:
        return None
    return cfg


def _clean_limits(raw: dict[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in (raw or {}).items():
        if k not in DEFAULT_LIMITS:
            continue
        if v in (None, ""):
            out[k] = None if k in ("daily_cost_limit", "daily_call_limit") else DEFAULT_LIMITS[k]
            continue
        try:
            num = float(v)
        except (TypeError, ValueError) as exc:
            raise _err("ai_limit_invalid", field=k) from exc
        if num < 0 or (k in ("concurrency", "batch_max", "timeout_s") and num < 1):
            raise _err("ai_limit_invalid", field=k)
        out[k] = num if k == "daily_cost_limit" else int(num)
    out["concurrency"] = min(int(out.get("concurrency", DEFAULT_LIMITS["concurrency"])), 8)
    out["max_retries"] = min(int(out.get("max_retries", DEFAULT_LIMITS["max_retries"])), 5)
    return out


def save_config(db: Session, body: dict[str, Any]) -> AIProviderConfig:
    """Create or update (``id`` in body). A new key replaces the stored one; changing the endpoint host without
    entering the key again deletes the stored key, so a key is never sent to a target the user did not confirm."""
    ptype = body.get("provider_type")
    cfg = db.get(AIProviderConfig, body["id"]) if body.get("id") else None
    if body.get("id") and (cfg is None or cfg.deleted_at is not None):
        raise AppError(ErrorCode.NOT_FOUND)
    if cfg is None:
        if ptype not in PROVIDER_TYPES:
            raise _err("ai_provider_unknown")
        cfg = AIProviderConfig(
            provider_type=ptype,
            display_name="",
            model_id="",
            limits_json=dict(DEFAULT_LIMITS),
            allowed_fields_json=list(DEFAULT_FIELDS),
            pricing_json={},
        )
        db.add(cfg)
    elif ptype and ptype != cfg.provider_type:
        raise _err("ai_provider_type_fixed")
    old_host = _host(cfg.base_url, cfg.provider_type)
    if "base_url" in body:
        url = (body.get("base_url") or "").strip() or None
        try:
            if url is not None or cfg.provider_type == "compatible":
                check_base_url(url, cfg.provider_type, app_settings.get(db, "ai_allowed_hosts") or [])
        except AIError as exc:
            raise _err(exc.code) from exc
        cfg.base_url = url.rstrip("/") if url else None
    if cfg.provider_type == "compatible" and not cfg.base_url:
        raise _err("ai_url_required")
    if "display_name" in body or not cfg.display_name:
        cfg.display_name = (body.get("display_name") or "").strip()[:200] or {
            "openai": "OpenAI",
            "anthropic": "Anthropic",
            "compatible": "Compatible API",
        }[cfg.provider_type]
    if "model_id" in body:
        cfg.model_id = (body.get("model_id") or "").strip()[:200]
    if not cfg.model_id:
        rec = next((m for m in RECOMMENDED_MODELS[cfg.provider_type] if m.get("recommended")), None)
        if rec is None:
            raise _err("ai_model_required")
        cfg.model_id = rec["id"]
        cfg.pricing_json = dict(rec["pricing"])
    if "enabled" in body:
        cfg.enabled = bool(body["enabled"])
    if "limits" in body:
        cfg.limits_json = {**limits(cfg), **_clean_limits(body.get("limits"))}
    if "allowed_fields" in body:
        cfg.allowed_fields_json = [f for f in body.get("allowed_fields") or [] if f in SENDABLE_FIELDS]
    if "pricing" in body:
        p = body.get("pricing") or {}
        cfg.pricing_json = {
            k: p.get(k) for k in ("input_per_mtok", "output_per_mtok", "currency", "updated_at") if p.get(k)
        }
    db.flush()
    cfg.key_reference = cfg.key_reference or f"ai_provider_{cfg.id}"
    new_key = (body.get("api_key") or "").strip()
    if new_key:
        store = secrets.store()
        if not store.available():
            raise _err("ai_key_storage_unavailable")
        store.write(cfg.key_reference, new_key)
        cfg.key_last4 = new_key[-4:]
        cfg.last_test_status = None
    elif old_host is not None and _host(cfg.base_url, cfg.provider_type) != old_host:
        delete_secret(cfg)
    if body.get("activate") or active_config(db) is None:
        app_settings.update(db, {"ai_active_provider_id": cfg.id})
    db.flush()
    return cfg


def _host(url: str | None, ptype: str) -> str | None:
    from urllib.parse import urlparse

    from houseagent.ai.providers import OFFICIAL_URLS

    target = url or OFFICIAL_URLS.get(ptype)
    return (urlparse(target).hostname or "").lower() if target else None


def delete_secret(cfg: AIProviderConfig) -> None:
    if cfg.key_reference:
        secrets.store().delete(cfg.key_reference)
    cfg.key_last4 = None
    cfg.last_test_status = None


def delete_config(db: Session, cfg: AIProviderConfig) -> None:
    delete_secret(cfg)
    cfg.deleted_at = utcnow()
    cfg.enabled = False
    if app_settings.get(db, "ai_active_provider_id") == cfg.id:
        app_settings.update(db, {"ai_active_provider_id": None})


# ---- usage and limits ----------------------------------------------------------------------------------


def _day_start(db: Session) -> datetime:
    tz = ZoneInfo(app_settings.get(db, "timezone") or "Asia/Tokyo")
    now = datetime.now(tz)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def usage_today(db: Session, cfg: AIProviderConfig) -> dict[str, Any]:
    since = _day_start(db)
    calls, cost = db.execute(
        select(func.count(), func.sum(AIUsageRecord.estimated_cost)).where(
            AIUsageRecord.provider_config_id == cfg.id,
            AIUsageRecord.requested_at >= since,
            AIUsageRecord.purpose != "test",
        )
    ).one()
    return {"calls": int(calls or 0), "cost": float(cost) if cost is not None else 0.0}


def check_limits(db: Session, cfg: AIProviderConfig, upcoming: int = 1) -> None:
    lim = limits(cfg)
    used = usage_today(db, cfg)
    if lim.get("daily_call_limit") is not None and used["calls"] + upcoming > int(lim["daily_call_limit"]):
        raise _err("ai_daily_limit", used=used["calls"], limit=lim["daily_call_limit"])
    if lim.get("daily_cost_limit") is not None and used["cost"] >= float(lim["daily_cost_limit"]):
        raise _err("ai_daily_limit", cost=used["cost"], limit=lim["daily_cost_limit"])


def remaining_calls(db: Session, cfg: AIProviderConfig) -> int | None:
    lim = limits(cfg).get("daily_call_limit")
    return None if lim is None else max(0, int(lim) - usage_today(db, cfg)["calls"])


def _record(db: Session, cfg: AIProviderConfig, purpose: str, correlation_id: str, **fields: Any) -> None:
    # In the caller's transaction (SQLite has one writer). Callers commit it even when the call failed:
    # assessments store the failure, the connection test stores its status, the summary commits before raising.
    db.add(
        AIUsageRecord(
            provider_config_id=cfg.id,
            purpose=purpose,
            provider_type=cfg.provider_type,
            model_id=cfg.model_id,
            correlation_id=correlation_id,
            cost_currency=(cfg.pricing_json or {}).get("currency"),
            **fields,
        )
    )
    db.flush()


def provider_for(db: Session, cfg: AIProviderConfig) -> AIProvider:
    value, _source = secrets.read_with_env(_ref(cfg))
    if not value and cfg.provider_type != "compatible":
        raise _err("ai_key_missing")
    try:
        return provider_factory(
            ProviderSettings(
                provider_type=cfg.provider_type,
                model_id=cfg.model_id,
                api_key=value or "",
                base_url=cfg.base_url,
                timeout_s=float(limits(cfg)["timeout_s"]),
                allowed_hosts=app_settings.get(db, "ai_allowed_hosts") or [],
            )
        )
    except AIError as exc:
        raise _err(exc.code) from exc


def require_ready(db: Session) -> AIProviderConfig:
    """The active configuration, or a stable reason why AI cannot be used right now."""
    if not app_settings.get(db, "ai_enabled"):
        raise _err("ai_disabled")
    cfg = active_config(db)
    if cfg is None or not cfg.enabled:
        raise _err("ai_not_configured")
    if cfg.provider_type != "compatible" and key_state(cfg) == "missing":
        raise _err("ai_key_missing")
    return cfg


def call(
    db: Session,
    cfg: AIProviderConfig,
    purpose: str,
    fn: Callable[[AIProvider], AIResult],
    *,
    assessment_id: int | None = None,
    correlation_id: str | None = None,
) -> AIResult:
    """Run one logical AI request with limits, retries and usage records. Raises AIError on final failure."""
    if purpose != "test":
        check_limits(db, cfg)
    provider = provider_for(db, cfg)
    lim = limits(cfg)
    cid = correlation_id or uuid.uuid4().hex[:16]
    with _sem_lock:
        sem = _semaphores.setdefault(cfg.id, threading.BoundedSemaphore(int(lim["concurrency"])))
    attempts = int(lim["max_retries"]) + 1
    with sem:
        for attempt in range(attempts):
            started = time.monotonic()
            try:
                result = fn(provider)
            except AIError as exc:
                _record(
                    db,
                    cfg,
                    purpose,
                    cid,
                    assessment_id=assessment_id,
                    status=exc.code,
                    duration_ms=int((time.monotonic() - started) * 1000),
                )
                log.info("AI call failed [%s] %s (attempt %d)", cid, exc.code, attempt + 1)
                if exc.retryable and attempt + 1 < attempts:
                    time.sleep(min(8.0, 1.0 * 2**attempt))
                    continue
                raise
            _record(
                db,
                cfg,
                purpose,
                cid,
                assessment_id=assessment_id,
                status="ok",
                input_units=result.input_units,
                output_units=result.output_units,
                estimated_cost=estimate_cost(cfg.pricing_json or {}, result.input_units, result.output_units),
                duration_ms=result.duration_ms or int((time.monotonic() - started) * 1000),
            )
            return result
    raise AIError("ai_network")  # pragma: no cover - loop always returns or raises


def test_connection(db: Session, cfg: AIProviderConfig) -> dict[str, Any]:
    """Minimal request: endpoint, key, model, permission and structured output. Works even with AI switched off
    so the user can check a configuration before enabling it."""
    cfg.last_test_at = utcnow()
    try:
        provider_for(db, cfg)
        result = call(db, cfg, "test", lambda p: _as_result(p.test_connection()))
        detail = dict(result.data)
        cfg.last_test_status, cfg.last_test_detail = "ok", detail
        return {"ok": True, **detail}
    except AIError as exc:
        cfg.last_test_status, cfg.last_test_detail = exc.code, {"code": exc.code}
        return {"ok": False, "code": exc.code, "message_key": f"error.{exc.code}"}
    except AppError as exc:
        code = (exc.message_key or "error.ai_unavailable").removeprefix("error.")
        cfg.last_test_status, cfg.last_test_detail = code, {"code": code}
        return {"ok": False, "code": code, "message_key": exc.message_key}


def _as_result(d: dict[str, Any]) -> AIResult:
    return AIResult(
        data=d,
        input_units=d.get("input_units"),
        output_units=d.get("output_units"),
        model_version=d.get("model_version"),
        duration_ms=int(d.get("duration_ms") or 0),
    )


def usage_summary(db: Session, days: int = 30) -> dict[str, Any]:
    since = utcnow() - timedelta(days=days)
    rows = db.execute(
        select(
            AIUsageRecord.provider_config_id,
            AIUsageRecord.model_id,
            AIUsageRecord.purpose,
            AIUsageRecord.status,
            func.count(),
            func.sum(AIUsageRecord.input_units),
            func.sum(AIUsageRecord.output_units),
            func.sum(AIUsageRecord.estimated_cost),
            func.max(AIUsageRecord.cost_currency),
        )
        .where(AIUsageRecord.requested_at >= since)
        .group_by(AIUsageRecord.provider_config_id, AIUsageRecord.model_id, AIUsageRecord.purpose, AIUsageRecord.status)
    ).all()
    recent = db.execute(select(AIUsageRecord).order_by(AIUsageRecord.id.desc()).limit(50)).scalars()
    today = {c.id: {**usage_today(db, c), "limits": limits(c)} for c in list_configs(db)}
    return {
        "days": days,
        "groups": [
            {
                "provider_config_id": r[0],
                "model_id": r[1],
                "purpose": r[2],
                "status": r[3],
                "calls": r[4],
                "input_units": r[5] or 0,
                "output_units": r[6] or 0,
                "estimated_cost": r[7],
                "currency": r[8],
            }
            for r in rows
        ],
        "today": today,
        "recent": [
            {
                "id": u.id,
                "requested_at": u.requested_at,
                "provider_config_id": u.provider_config_id,
                "model_id": u.model_id,
                "purpose": u.purpose,
                "status": u.status,
                "input_units": u.input_units,
                "output_units": u.output_units,
                "estimated_cost": u.estimated_cost,
                "currency": u.cost_currency,
                "duration_ms": u.duration_ms,
                "correlation_id": u.correlation_id,
                "assessment_id": u.assessment_id,
            }
            for u in recent
        ],
    }


def migrate_legacy_key(db: Session) -> None:
    """HouseAgent 1.0.0 kept one Anthropic key in config/ai_key.dpapi: move it into a provider configuration."""
    from houseagent.config import get_settings

    legacy = get_settings().config_dir / "ai_key.dpapi"
    if not legacy.exists() or list_configs(db):
        return
    try:
        from houseagent.ai import dpapi

        key = dpapi.unprotect(legacy.read_text(encoding="ascii"))
    except Exception:  # noqa: BLE001 - unreadable (other user / machine): ask the user again
        log.warning("legacy AI key could not be read; please enter it again")
        return
    save_config(
        db,
        {
            "provider_type": "anthropic",
            "display_name": "Anthropic",
            "model_id": app_settings.get(db, "ai_model") or "claude-opus-5",
            "api_key": key,
            "activate": True,
        },
    )
    legacy.unlink(missing_ok=True)


def provider_estimate(request: Any) -> dict[str, Any]:
    """Usage estimate before a batch (no key, no network)."""
    from houseagent.ai.providers import estimate

    return estimate(request)
