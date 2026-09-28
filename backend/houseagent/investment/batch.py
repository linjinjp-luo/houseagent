"""Batch investment assessments (spec 4.7.2 / 15.14).

Before a batch starts the user sees how many properties it covers, how many would call the AI (rules run first;
only properties meeting the minimum data do) and the estimated usage. The batch runs in the background with the
configured concurrency; it pauses when the daily call / cost limit is reached and never continues on its own;
queued items can be cancelled. Retrying a failed call never creates a second assessment record.
"""

from __future__ import annotations

import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from houseagent.ai import gateway
from houseagent.ai.providers import AIRequest, estimate_cost
from houseagent.db.models import Favorite, Listing, utcnow
from houseagent.db.session import session_scope
from houseagent.errors import AppError, ErrorCode
from houseagent.investment import ai_eval, rules, service

log = logging.getLogger(__name__)


@dataclass
class BatchJob:
    id: str
    listing_ids: list[int]
    profile_id: int
    use_ai: bool
    trigger: str
    status: str = "running"  # running | completed | cancelled | paused_limit
    done: int = 0
    failed: int = 0
    ai_calls: int = 0
    labels: dict[str, int] = field(default_factory=dict)
    stop_reason: str | None = None
    started_at: Any = field(default_factory=utcnow)
    finished_at: Any = None
    cancel: threading.Event = field(default_factory=threading.Event)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "total": len(self.listing_ids),
            "done": self.done,
            "failed": self.failed,
            "ai_calls": self.ai_calls,
            "labels": self.labels,
            "status": self.status,
            "stop_reason": self.stop_reason,
            "trigger": self.trigger,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


_jobs: dict[str, BatchJob] = {}
_lock = threading.Lock()


def resolve_ids(db: Session, scope: str, listing_ids: list[int] | None) -> list[int]:
    if scope == "favorites":
        ids = list(
            db.execute(
                select(Favorite.listing_id)
                .join(Listing, Listing.id == Favorite.listing_id)
                .where(Listing.deleted_at.is_(None), Listing.deal_type == "buy")
            ).scalars()
        )
    else:
        ids = list(listing_ids or [])
        if ids:
            ids = list(
                db.execute(
                    select(Listing.id).where(
                        Listing.id.in_(ids), Listing.deleted_at.is_(None), Listing.deal_type == "buy"
                    )
                ).scalars()
            )
    return sorted(dict.fromkeys(ids))


def estimate(db: Session, ids: list[int], profile_id: int | None, use_ai: bool) -> dict[str, Any]:
    profile = service.get_profile(db, profile_id)
    cfg, reason = service.ai_status(db) if use_ai else (None, "not_requested")
    need_ai = 0
    input_units = 0
    output_units = 0
    for lid in ids:
        prep = service.prepare(db, lid, profile)
        rr = rules.evaluate(prep["facts"], prep["inputs"], prep["comparable"], prep["profile"])
        if rr.rule_label == "insufficient_data" or cfg is None:
            continue
        need_ai += 1
        payload = service._ai_payload(prep, rr, list(cfg.allowed_fields_json or []))
        req = AIRequest(ai_eval.SYSTEM_PROMPT, ai_eval.build_user_message(payload, "ja"), ai_eval.output_schema())
        e = gateway.provider_estimate(req)
        input_units += e["input_units"]
        output_units += e["output_units"]
    lim = gateway.limits(cfg) if cfg is not None else {}
    return {
        "total": len(ids),
        "ai_calls": need_ai,
        "rules_only": len(ids) - need_ai,
        "ai_unavailable_reason": reason,
        "input_units": input_units,
        "output_units": output_units,
        "estimated_cost": estimate_cost(cfg.pricing_json or {}, input_units, output_units) if cfg else None,
        "currency": (cfg.pricing_json or {}).get("currency") if cfg else None,
        "pricing_updated_at": (cfg.pricing_json or {}).get("updated_at") if cfg else None,
        "batch_max": lim.get("batch_max"),
        "remaining_calls_today": gateway.remaining_calls(db, cfg) if cfg is not None else None,
        "profile_id": profile.id,
    }


def start(db: Session, ids: list[int], profile_id: int | None, use_ai: bool, trigger: str) -> BatchJob:
    if not ids:
        raise AppError(ErrorCode.VALIDATION_ERROR, {"field": "listing_ids"}, message_key="error.batch_empty")
    profile = service.get_profile(db, profile_id)
    cfg, _reason = service.ai_status(db) if use_ai else (None, None)
    if cfg is not None:
        batch_max = int(gateway.limits(cfg)["batch_max"])
        if len(ids) > batch_max:
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                {"count": len(ids), "max": batch_max},
                message_key="error.ai_batch_too_large",
            )
    with _lock:
        if any(j.status == "running" for j in _jobs.values()):
            raise AppError(ErrorCode.CONFLICT, message_key="error.batch_running")
        job = BatchJob(uuid.uuid4().hex[:12], ids, profile.id, use_ai, trigger)
        _jobs[job.id] = job
    concurrency = int(gateway.limits(cfg)["concurrency"]) if cfg is not None else 1
    threading.Thread(target=_run, args=(job, concurrency), daemon=True, name=f"assess-{job.id}").start()
    return job


def _one(job: BatchJob, lid: int) -> None:
    if job.cancel.is_set():
        return
    try:
        with session_scope() as db:
            if job.use_ai:
                cfg, _ = service.ai_status(db)
                if cfg is not None:
                    try:
                        gateway.check_limits(db, cfg)
                    except AppError:
                        # Limit reached: stop here; the user decides whether to continue tomorrow.
                        job.stop_reason = "ai_daily_limit"
                        job.cancel.set()
                        return
            a = service.assess(db, lid, job.profile_id, use_ai=job.use_ai, trigger=job.trigger)
            with _lock:
                job.done += 1
                job.labels[a.primary_label] = job.labels.get(a.primary_label, 0) + 1
                if a.model_version or a.status in ("ai", "ai_rejected"):
                    job.ai_calls += 1
                if a.ai_error == "ai_daily_limit":
                    job.stop_reason = "ai_daily_limit"
                    job.cancel.set()
    except Exception:  # noqa: BLE001 - one property must not stop the batch
        log.exception("assessment of listing %s failed", lid)
        with _lock:
            job.failed += 1


def _run(job: BatchJob, concurrency: int) -> None:
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        for lid in job.listing_ids:
            pool.submit(_one, job, lid)
    with _lock:
        job.finished_at = utcnow()
        if job.stop_reason == "ai_daily_limit":
            job.status = "paused_limit"
        elif job.cancel.is_set():
            job.status = "cancelled"
        else:
            job.status = "completed"


def get(job_id: str) -> BatchJob:
    job = _jobs.get(job_id)
    if job is None:
        raise AppError(ErrorCode.NOT_FOUND)
    return job


def current() -> BatchJob | None:
    with _lock:
        jobs = sorted(_jobs.values(), key=lambda j: j.started_at, reverse=True)
    return jobs[0] if jobs else None


def cancel(job_id: str) -> BatchJob:
    job = get(job_id)
    job.stop_reason = job.stop_reason or "cancelled"
    job.cancel.set()
    return job


def after_run(task_id: int, listing_ids: list[int], profile_id: int | None) -> None:
    """Hook for "assess after the search completes" (default off). Uses AI only when it is on and ready."""
    if not listing_ids:
        return
    with session_scope() as db:
        try:
            ids = resolve_ids(db, "ids", listing_ids)
            profile = service.get_profile(db, profile_id)
            cfg, _ = service.ai_status(db)
            if cfg is not None:
                ids = ids[: int(gateway.limits(cfg)["batch_max"])]
            if ids:
                start(db, ids, profile.id, True, "after_run")
        except AppError as exc:
            log.info("post-run assessment for task %s skipped: %s", task_id, exc.message_key)
