"""Generic listing processing: uniqueness, change detection, history and cross-site matching.

Site-independent - adapters hand over ``NormalizedSource`` objects and never touch the database.
Rules (spec 7.3 / FR-04 / FR-05 / 15.7):

* ``site_id + external_listing_id`` identifies a source; repeated searches never duplicate it.
* A snapshot is written only when a source is new or its summary changed.
* Absent from a complete result list -> ``NOT_FOUND`` ("not observed this time"), never "sold".
* Only explicit evidence from the site marks ``UNAVAILABLE``.
* Cross-site matches are only *suggested*; the user confirms every merge.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from houseagent.adapters.base import SUMMARY_FIELDS, NormalizedSource
from houseagent.db.models import (
    Favorite,
    Listing,
    ListingEvent,
    ListingSnapshot,
    ListingSource,
    ListingTag,
    MatchCandidate,
    Note,
    RunResult,
    TaskRun,
    utcnow,
)
from houseagent.errors import AppError, ErrorCode

# Index fields that are always allowed: they are what makes a source traceable at all.
_INDEX_FIELDS = ("title", "deal_type")


@dataclass
class IngestCounts:
    results: int = 0
    new: int = 0
    changed: int = 0
    skipped: int = 0


def normalize_text(v: str | None) -> str | None:
    if not v:
        return None
    s = unicodedata.normalize("NFKC", v)
    return "".join(s.split()).lower() or None


def _allowed_summary(item: NormalizedSource, retention_fields: list[str], retention_allowed: bool) -> dict[str, Any]:
    if not retention_allowed:
        return {k: getattr(item, k) for k in _INDEX_FIELDS}
    allowed = set(retention_fields) | set(_INDEX_FIELDS)
    return {k: getattr(item, k) for k in SUMMARY_FIELDS if k in allowed}


def summary_hash(summary: dict[str, Any], availability: str) -> str:
    payload = json.dumps({**summary, "_availability": availability}, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _event(
    db: Session,
    src: ListingSource,
    run_id: int | None,
    etype: str,
    old: int | None = None,
    new: int | None = None,
    evidence: str | None = None,
) -> None:
    db.add(
        ListingEvent(
            source_id=src.id,
            listing_id=src.listing_id,
            run_id=run_id,
            event_type=etype,
            old_price_yen=old,
            new_price_yen=new,
            evidence=evidence,
        )
    )


def _snapshot(db: Session, src: ListingSource, run_id: int | None, summary: dict[str, Any], h: str) -> None:
    db.add(
        ListingSnapshot(
            source_id=src.id,
            run_id=run_id,
            price_yen=src.price_yen,
            status=src.observation_status,
            summary_hash=h,
            fields=summary,
        )
    )


def _last_hash(db: Session, source_id: int) -> str | None:
    return db.execute(
        select(ListingSnapshot.summary_hash)
        .where(ListingSnapshot.source_id == source_id)
        .order_by(ListingSnapshot.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def refresh_listing_status(db: Session, listing_id: int) -> None:
    listing = db.get(Listing, listing_id)
    if listing is None:
        return
    statuses = [s.observation_status for s in listing.sources]
    if "active" in statuses:
        listing.current_status = "active"
    elif "ended" in statuses:
        listing.current_status = "ended"
    elif "unavailable" in statuses:
        listing.current_status = "unavailable"
    elif statuses:
        listing.current_status = "not_found"


def _new_listing_from(summary: dict[str, Any]) -> Listing:
    return Listing(
        deal_type=summary.get("deal_type") or "buy",
        property_type=summary.get("property_type"),
        prefecture=summary.get("prefecture"),
        city=summary.get("city"),
        address_normalized=normalize_text(summary.get("address")),
        building_name=summary.get("building_name"),
        layout=summary.get("layout"),
        area_m2=summary.get("area_m2"),
        land_area_m2=summary.get("land_area_m2"),
        floor=summary.get("floor"),
        built_year=summary.get("built_year"),
    )


def upsert_source(
    db: Session,
    site_id: str,
    item: NormalizedSource,
    *,
    run: TaskRun | None,
    retention_fields: list[str],
    retention_allowed: bool,
    rank: int = 0,
    manual: bool = False,
) -> tuple[ListingSource, bool, bool]:
    """Insert or update one source. Returns (source, is_new, is_changed)."""
    now = utcnow()
    run_id = run.id if run else None
    summary = _allowed_summary(item, retention_fields, retention_allowed)
    h = summary_hash(summary, item.availability)
    src = db.execute(
        select(ListingSource).where(
            ListingSource.site_id == site_id, ListingSource.external_listing_id == item.external_id
        )
    ).scalar_one_or_none()
    if src is None:
        listing = _new_listing_from(summary)
        db.add(listing)
        db.flush()
        src = ListingSource(
            listing=listing,
            site_id=site_id,
            external_listing_id=item.external_id,
            source_url=item.url,
            raw_enums=item.raw_enums,
            first_seen_at=now,
            last_seen_at=now,
            last_checked_at=now,
            manual_import=manual,
            observation_status="unavailable" if item.availability == "unavailable" else "active",
            **summary,
        )
        db.add(src)
        db.flush()
        _event(db, src, run_id, "NEW", new=src.price_yen)
        if src.observation_status == "unavailable":
            _event(db, src, run_id, "UNAVAILABLE", evidence="site_marked_ended")
        _snapshot(db, src, run_id, summary, h)
        refresh_listing_status(db, listing.id)
        find_match_candidates(db, listing)
        return src, True, False

    changed = False
    prev_status = src.observation_status
    old_price = src.price_yen
    for k, v in summary.items():
        setattr(src, k, v)
    src.source_url = item.url or src.source_url
    src.raw_enums = item.raw_enums or src.raw_enums
    src.last_seen_at = now
    src.last_checked_at = now
    src.url_status = "ok"
    if item.availability == "unavailable":
        if prev_status != "unavailable":
            src.observation_status = "unavailable"
            _event(db, src, run_id, "UNAVAILABLE", evidence="site_marked_ended")
            changed = True
    else:
        if prev_status in ("not_found", "unavailable"):
            _event(db, src, run_id, "REAPPEARED")
            changed = True
        src.observation_status = "active"
    new_price = src.price_yen
    if old_price is not None and new_price is not None and new_price != old_price:
        _event(db, src, run_id, "PRICE_DOWN" if new_price < old_price else "PRICE_UP", old_price, new_price)
        changed = True
    if _last_hash(db, src.id) != h or changed:
        _snapshot(db, src, run_id, summary, h)
    refresh_listing_status(db, src.listing_id)
    return src, False, changed


def ingest_items(
    db: Session,
    run: TaskRun,
    items: list[NormalizedSource],
    *,
    retention_fields: list[str],
    retention_allowed: bool,
    rank_offset: int = 0,
) -> IngestCounts:
    counts = IngestCounts()
    seen_ids = {r for (r,) in db.execute(select(RunResult.source_id).where(RunResult.run_id == run.id))}
    for i, item in enumerate(items):
        existing = db.execute(
            select(ListingSource.id).where(
                ListingSource.site_id == run.site_id, ListingSource.external_listing_id == item.external_id
            )
        ).scalar_one_or_none()
        if existing is not None and existing in seen_ids:
            counts.skipped += 1  # same listing repeated within this run (e.g. pagination overlap)
            continue
        src, is_new, is_changed = upsert_source(
            db,
            run.site_id,
            item,
            run=run,
            retention_fields=retention_fields,
            retention_allowed=retention_allowed,
            rank=rank_offset + i,
        )
        db.add(RunResult(run_id=run.id, source_id=src.id, rank=rank_offset + i, is_new=is_new, is_changed=is_changed))
        seen_ids.add(src.id)
        counts.results += 1
        counts.new += int(is_new)
        counts.changed += int(is_changed)
    db.flush()
    return counts


def mark_not_found(db: Session, run: TaskRun) -> int:
    """After a *complete* run: sources this task found before (same condition version) but not now."""
    now = utcnow()
    seen_now = select(RunResult.source_id).where(RunResult.run_id == run.id)
    prev_runs = select(TaskRun.id).where(
        TaskRun.task_id == run.task_id,
        TaskRun.site_id == run.site_id,
        TaskRun.condition_version == run.condition_version,
        TaskRun.id != run.id,
    )
    q = (
        select(ListingSource)
        .join(RunResult, RunResult.source_id == ListingSource.id)
        .where(
            RunResult.run_id.in_(prev_runs),
            ListingSource.id.not_in(seen_now),
            ListingSource.observation_status == "active",
        )
        .distinct()
    )
    n = 0
    for src in db.execute(q).scalars():
        src.observation_status = "not_found"
        src.last_checked_at = now  # last_seen_at is untouched: we did not see it
        _event(db, src, run.id, "NOT_FOUND")
        refresh_listing_status(db, src.listing_id)
        n += 1
    db.flush()
    return n


# --------------------------------------------------------------------------------------------- matching


def _score(a: Listing, b: Listing) -> tuple[float, list[str]]:
    reasons: list[str] = []
    score = 0.0
    addr = bool(a.address_normalized and a.address_normalized == b.address_normalized)
    bname = bool(a.building_name and normalize_text(a.building_name) == normalize_text(b.building_name))
    area = bool(a.area_m2 and b.area_m2 and abs(a.area_m2 - b.area_m2) <= max(a.area_m2, b.area_m2) * 0.02)
    if addr:
        score += 0.35
        reasons.append("address")
    if bname:
        score += 0.25
        reasons.append("building_name")
    if area:
        score += 0.2
        reasons.append("area")
    if a.floor is not None and a.floor == b.floor:
        score += 0.1
        reasons.append("floor")
    if a.built_year and a.built_year == b.built_year:
        score += 0.1
        reasons.append("built_year")
    # Price similarity alone is never a reason; location + size evidence is required.
    if not ((addr or bname) and area):
        return 0.0, []
    if a.floor is not None and b.floor is not None and a.floor != b.floor:
        return 0.0, []
    return round(score, 2), reasons


def find_match_candidates(db: Session, listing: Listing) -> list[MatchCandidate]:
    if not listing.address_normalized and not listing.building_name:
        return []
    my_sites = {s.site_id for s in listing.sources}
    q = select(Listing).where(Listing.id != listing.id, Listing.merged_into_id.is_(None), Listing.deleted_at.is_(None))
    if listing.address_normalized:
        q = q.where(
            (Listing.address_normalized == listing.address_normalized)
            | (Listing.building_name == listing.building_name)
        )
    else:
        q = q.where(Listing.building_name == listing.building_name)
    out = []
    for other in db.execute(q).scalars():
        if my_sites & {s.site_id for s in other.sources}:
            continue  # cross-site only
        score, reasons = _score(listing, other)
        if score < 0.6:
            continue
        a, b = sorted((listing.id, other.id))
        exists = db.execute(
            select(MatchCandidate).where(MatchCandidate.listing_a_id == a, MatchCandidate.listing_b_id == b)
        ).scalar_one_or_none()
        if exists:
            continue
        mc = MatchCandidate(listing_a_id=a, listing_b_id=b, score=score, reasons=reasons)
        db.add(mc)
        out.append(mc)
    db.flush()
    return out


def confirm_match(db: Session, candidate: MatchCandidate) -> Listing:
    """User-confirmed merge: B's sources, notes, tags and favorite move to A. Nothing is deleted."""
    if candidate.status != "pending":
        raise AppError(ErrorCode.CONFLICT, {"status": candidate.status})
    a = db.get(Listing, candidate.listing_a_id)
    b = db.get(Listing, candidate.listing_b_id)
    assert a and b
    for src in list(b.sources):
        src.listing_id = a.id
    db.query(ListingEvent).filter(ListingEvent.listing_id == b.id).update({"listing_id": a.id})
    db.query(Note).filter(Note.listing_id == b.id).update({"listing_id": a.id})
    fav_a = db.execute(select(Favorite).where(Favorite.listing_id == a.id)).scalar_one_or_none()
    fav_b = db.execute(select(Favorite).where(Favorite.listing_id == b.id)).scalar_one_or_none()
    if fav_b is not None:
        if fav_a is None:
            fav_b.listing_id = a.id
        else:
            db.delete(fav_b)
    tags_a = {t for (t,) in db.execute(select(ListingTag.tag_id).where(ListingTag.listing_id == a.id))}
    for lt in db.execute(select(ListingTag).where(ListingTag.listing_id == b.id)).scalars().all():
        if lt.tag_id not in tags_a:
            db.add(ListingTag(listing_id=a.id, tag_id=lt.tag_id))
        db.delete(lt)
    b.merged_into_id = a.id
    candidate.status = "confirmed"
    candidate.resolved_at = utcnow()
    db.flush()
    db.expire(a)
    refresh_listing_status(db, a.id)
    return a


def reject_match(db: Session, candidate: MatchCandidate) -> None:
    candidate.status = "rejected"
    candidate.resolved_at = utcnow()
    db.flush()


def unlink_source(db: Session, source: ListingSource) -> Listing:
    """Split a source back out to its own listing. Source records and history are kept."""
    current = db.get(Listing, source.listing_id)
    assert current is not None
    if len(current.sources) <= 1:
        raise AppError(ErrorCode.CONFLICT, message_key="error.unlink_last_source")
    summary = {k: getattr(source, k) for k in SUMMARY_FIELDS}
    listing = _new_listing_from(summary)
    db.add(listing)
    db.flush()
    source.listing_id = listing.id
    db.query(ListingEvent).filter(ListingEvent.source_id == source.id).update({"listing_id": listing.id})
    db.flush()
    db.expire(current)
    refresh_listing_status(db, listing.id)
    refresh_listing_status(db, current.id)
    return listing
