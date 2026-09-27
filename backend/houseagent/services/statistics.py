"""Statistics and reports (FR-06 / 9.4). Deterministic - no AI involved.

Every figure describes only the set of listings the user searched and observed; it is not the full market
supply and says nothing about actual transactions. The response always carries that scope note.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from houseagent.db.models import (
    Favorite,
    Listing,
    ListingEvent,
    ListingSource,
    MatchCandidate,
    Notification,
    RunResult,
    SearchTask,
    TaskRun,
)

PRICE_BUCKETS_MAN = [0, 1000, 2000, 3000, 4000, 5000, 6000, 8000, 10000]
RENT_BUCKETS_MAN = [0, 5, 7, 9, 11, 13, 15, 20, 30]  # 万円/月
AREA_BUCKETS = [0, 40, 60, 80, 100, 120, 150, 200]


def _bucket(value: float, edges: list[int]) -> str:
    for lo, hi in zip(edges, edges[1:], strict=False):
        if lo <= value < hi:
            return f"{lo}-{hi}"
    return f"{edges[-1]}+"


def _source_scope(
    task_id: int | None,
    site_id: str | None,
    prefecture: str | None,
    city: str | None,
    property_type: str | None,
    deal_type: str = "buy",
) -> Select[Any]:
    q = (
        select(ListingSource.id)
        .join(Listing, Listing.id == ListingSource.listing_id)
        .where(Listing.deleted_at.is_(None), ListingSource.deal_type == deal_type)
    )
    if task_id:
        q = q.where(
            ListingSource.id.in_(
                select(RunResult.source_id)
                .join(TaskRun, TaskRun.id == RunResult.run_id)
                .where(TaskRun.task_id == task_id)
            )
        )
    if site_id:
        q = q.where(ListingSource.site_id == site_id)
    if prefecture:
        q = q.where(ListingSource.prefecture == prefecture)
    if city:
        q = q.where(ListingSource.city == city)
    if property_type:
        q = q.where(ListingSource.property_type == property_type)
    return q


def compute(
    db: Session,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    task_id: int | None = None,
    site_id: str | None = None,
    prefecture: str | None = None,
    city: str | None = None,
    property_type: str | None = None,
    deal_type: str = "buy",
    tz_name: str = "Asia/Tokyo",
) -> dict[str, Any]:
    """Buy and rent are never mixed: prices mean different things (sale price vs monthly rent)."""
    tz = ZoneInfo(tz_name)
    today = datetime.now(tz).date()
    date_to = date_to or today
    date_from = date_from or (date_to - timedelta(days=29))
    start = datetime.combine(date_from, datetime.min.time(), tz).astimezone(UTC)
    end = datetime.combine(date_to + timedelta(days=1), datetime.min.time(), tz).astimezone(UTC)
    scope = _source_scope(task_id, site_id, prefecture, city, property_type, deal_type)
    buckets = RENT_BUCKETS_MAN if deal_type == "rent" else PRICE_BUCKETS_MAN

    events = (
        db.execute(
            select(ListingEvent).where(
                ListingEvent.source_id.in_(scope), ListingEvent.created_at >= start, ListingEvent.created_at < end
            )
        )
        .scalars()
        .all()
    )
    daily: dict[str, Counter[str]] = defaultdict(Counter)
    d = date_from
    while d <= date_to:
        daily[d.isoformat()] = Counter()
        d += timedelta(days=1)
    down_amounts: list[float] = []
    up_amounts: list[float] = []
    down_pcts: list[float] = []
    for e in events:
        key = e.created_at.astimezone(tz).date().isoformat()
        daily[key][e.event_type] += 1
        if e.event_type in ("PRICE_DOWN", "PRICE_UP") and e.old_price_yen and e.new_price_yen:
            diff = e.new_price_yen - e.old_price_yen
            (down_amounts if diff < 0 else up_amounts).append(abs(diff))
            if diff < 0:
                down_pcts.append(abs(diff) / e.old_price_yen * 100)

    sources = db.execute(select(ListingSource).where(ListingSource.id.in_(scope))).scalars().all()
    active = [s for s in sources if s.observation_status == "active"]
    price_dist = Counter(_bucket(s.price_yen / 10_000, buckets) for s in active if s.price_yen)
    area_dist = Counter(_bucket(s.area_m2, AREA_BUCKETS) for s in active if s.area_m2)
    by_site = Counter(s.site_id for s in active)
    by_type = Counter(s.property_type or "unknown" for s in active)
    by_city = Counter(s.city or "unknown" for s in active)
    listing_sites: dict[int, set[str]] = defaultdict(set)
    for s in sources:
        listing_sites[s.listing_id].add(s.site_id)
    overlap = sum(1 for v in listing_sites.values() if len(v) > 1)

    run_q = select(TaskRun).where(TaskRun.queued_at >= start, TaskRun.queued_at < end)
    if task_id:
        run_q = run_q.where(TaskRun.task_id == task_id)
    if site_id:
        run_q = run_q.where(TaskRun.site_id == site_id)
    runs = db.execute(run_q).scalars().all()
    finished = [r for r in runs if r.status not in ("queued", "running")]
    task_names = {t.id: t.name for t in db.execute(select(SearchTask)).scalars()}
    per_task: dict[int, Counter[str]] = defaultdict(Counter)
    for r in finished:
        per_task[r.task_id][r.status] += 1
    errors = Counter(r.error_code for r in finished if r.error_code)

    def avg(xs: list[float]) -> float | None:
        return round(sum(xs) / len(xs), 1) if xs else None

    prices = sorted(s.price_yen for s in active if s.price_yen)
    areas = sorted(s.area_m2 for s in active if s.area_m2)
    return {
        "scope_note_key": "stats.scope_disclaimer",
        "generated_at": datetime.now(UTC),
        "filters": {
            "deal_type": deal_type,
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "task_id": task_id,
            "site_id": site_id,
            "prefecture": prefecture,
            "city": city,
            "property_type": property_type,
        },
        "sample_size": len(sources),
        "current_observable": len(active),
        "daily": [
            {
                "date": k,
                "new": v["NEW"],
                "price_down": v["PRICE_DOWN"],
                "price_up": v["PRICE_UP"],
                "not_found": v["NOT_FOUND"],
                "reappeared": v["REAPPEARED"],
                "unavailable": v["UNAVAILABLE"],
            }
            for k, v in sorted(daily.items())
        ],
        "totals": {
            "new": sum(v["NEW"] for v in daily.values()),
            "price_down": len(down_amounts),
            "price_up": len(up_amounts),
            "avg_down_yen": avg(down_amounts),
            "avg_up_yen": avg(up_amounts),
            "avg_down_pct": avg(down_pcts),
        },
        "price": {
            "min": prices[0] if prices else None,
            "max": prices[-1] if prices else None,
            "median": prices[len(prices) // 2] if prices else None,
            "distribution": [
                {"bucket": b, "count": price_dist.get(b, 0)}
                for b in [f"{lo}-{hi}" for lo, hi in zip(buckets, buckets[1:], strict=False)] + [f"{buckets[-1]}+"]
            ],
        },
        "area": {
            "min": areas[0] if areas else None,
            "max": areas[-1] if areas else None,
            "distribution": [
                {"bucket": b, "count": area_dist.get(b, 0)}
                for b in [f"{lo}-{hi}" for lo, hi in zip(AREA_BUCKETS, AREA_BUCKETS[1:], strict=False)]
                + [f"{AREA_BUCKETS[-1]}+"]
            ],
        },
        "by_site": [{"site_id": k, "count": v} for k, v in by_site.most_common()],
        "by_type": [{"property_type": k, "count": v} for k, v in by_type.most_common()],
        "by_city": [{"city": k, "count": v} for k, v in by_city.most_common(15)],
        "cross_site": {
            "overlapping_listings": overlap,
            "pending_candidates": db.execute(
                select(func.count()).select_from(MatchCandidate).where(MatchCandidate.status == "pending")
            ).scalar_one(),
        },
        "tasks": [
            {
                "task_id": tid,
                "name": task_names.get(tid, str(tid)),
                "total": sum(c.values()),
                "completed": c["completed"],
                "failed": c["failed"],
                "paused": c["paused"],
                "cancelled": c["cancelled"],
                "interrupted": c["interrupted"],
                "success_rate": round(c["completed"] / sum(c.values()) * 100, 1) if c else None,
            }
            for tid, c in per_task.items()
        ],
        "runs": {
            "total": len(finished),
            "completed": sum(1 for r in finished if r.status == "completed"),
            "success_rate": round(sum(1 for r in finished if r.status == "completed") / len(finished) * 100, 1)
            if finished
            else None,
        },
        "errors": [{"error_code": k, "count": v} for k, v in errors.most_common()],
    }


def dashboard(db: Session, tz_name: str = "Asia/Tokyo") -> dict[str, Any]:
    tz = ZoneInfo(tz_name)
    task_names = {t.id: t.name for t in db.execute(select(SearchTask)).scalars()}
    start = datetime.combine(datetime.now(tz).date(), datetime.min.time(), tz).astimezone(UTC)
    today_events = Counter(
        t for (t,) in db.execute(select(ListingEvent.event_type).where(ListingEvent.created_at >= start))
    )
    failed_today = db.execute(
        select(func.count())
        .select_from(TaskRun)
        .where(TaskRun.finished_at >= start, TaskRun.status.in_(("failed", "paused", "interrupted")))
    ).scalar_one()
    pending_matches = db.execute(
        select(func.count()).select_from(MatchCandidate).where(MatchCandidate.status == "pending")
    ).scalar_one()
    last_success = db.execute(select(func.max(TaskRun.finished_at)).where(TaskRun.status == "completed")).scalar()
    next_run = db.execute(
        select(func.min(SearchTask.next_run_at)).where(SearchTask.deleted_at.is_(None), SearchTask.status == "active")
    ).scalar()
    running = (
        db.execute(
            select(TaskRun)
            .where(TaskRun.status.in_(("queued", "running")))
            .order_by(TaskRun.priority_rank, TaskRun.queued_at)
        )
        .scalars()
        .all()
    )
    attention = (
        db.execute(
            select(Notification)
            .where(Notification.resolved_at.is_(None))
            .order_by(Notification.created_at.desc())
            .limit(20)
        )
        .scalars()
        .all()
    )
    task_count = db.execute(
        select(func.count()).select_from(SearchTask).where(SearchTask.deleted_at.is_(None))
    ).scalar_one()
    # Recent activity: price changes / new listings, runs, favorites
    activity: list[dict[str, Any]] = []
    for e, src in db.execute(
        select(ListingEvent, ListingSource)
        .join(ListingSource, ListingSource.id == ListingEvent.source_id)
        .order_by(ListingEvent.id.desc())
        .limit(15)
    ):
        activity.append(
            {
                "kind": "event",
                "event_type": e.event_type,
                "at": e.created_at,
                "listing_id": e.listing_id,
                "title": src.title,
                "site_id": src.site_id,
                "deal_type": src.deal_type,
                "old_price_yen": e.old_price_yen,
                "new_price_yen": e.new_price_yen,
            }
        )
    for r in db.execute(
        select(TaskRun).where(TaskRun.finished_at.is_not(None)).order_by(TaskRun.finished_at.desc()).limit(10)
    ).scalars():
        activity.append(
            {
                "kind": "run",
                "at": r.finished_at,
                "run_id": r.id,
                "run_no": r.run_no,
                "status": r.status,
                "task_id": r.task_id,
                "new_count": r.new_count,
                "error_code": r.error_code,
            }
        )
    for f in db.execute(select(Favorite).order_by(Favorite.updated_at.desc()).limit(5)).scalars():
        activity.append({"kind": "favorite", "at": f.updated_at, "listing_id": f.listing_id, "status": f.status})
    activity.sort(key=lambda a: a["at"], reverse=True)
    from houseagent.services.notifications import to_dict

    return {
        "last_success_at": last_success,
        "next_run_at": next_run,
        "cards": {
            "new": today_events["NEW"],
            "price_down": today_events["PRICE_DOWN"],
            "price_up": today_events["PRICE_UP"],
            "reappeared": today_events["REAPPEARED"],
            "pending_matches": pending_matches,
            "failed": failed_today,
        },
        "queue": [
            {
                "run_id": r.id,
                "run_no": r.run_no,
                "task_id": r.task_id,
                "site_id": r.site_id,
                "status": r.status,
                "queued_at": r.queued_at,
                "started_at": r.started_at,
                "task_name": task_names.get(r.task_id),
                "progress_stage": r.progress_stage,
                "progress_pct": r.progress_pct,
                "wait_reason": r.wait_reason,
                "result_count": r.result_count,
            }
            for r in running
        ],
        "attention": [to_dict(n) for n in attention],
        "activity": activity[:25],
        "task_count": task_count,
    }
