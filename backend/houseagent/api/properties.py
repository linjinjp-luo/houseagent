"""Listing index (properties), history, notes, favorites, tags, match candidates and manual import."""

from __future__ import annotations

import hashlib
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import Session

from houseagent.adapters.base import NormalizedSource
from houseagent.ai import service as ai_service
from houseagent.db.models import (
    FAVORITE_STATUSES,
    Favorite,
    Listing,
    ListingEvent,
    ListingSnapshot,
    ListingSource,
    ListingTag,
    MatchCandidate,
    Note,
    RunResult,
    Site,
    Tag,
    TaskRun,
    utcnow,
)
from houseagent.db.session import get_db
from houseagent.errors import AppError, ErrorCode
from houseagent.services import listings as svc
from houseagent.services import settings_service as app_settings
from houseagent.services.accounts import is_allowed

router = APIRouter()


def _listing(db: Session, listing_id: int) -> Listing:
    listing = db.get(Listing, listing_id)
    if listing is None or listing.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, {"listing_id": listing_id})
    if listing.merged_into_id:
        raise AppError(ErrorCode.NOT_FOUND, {"merged_into": listing.merged_into_id}, message_key="error.listing_merged")
    return listing


def source_to_dict(s: ListingSource) -> dict[str, Any]:
    return {
        "id": s.id,
        "listing_id": s.listing_id,
        "site_id": s.site_id,
        "external_listing_id": s.external_listing_id,
        "source_url": s.source_url,
        "title": s.title,
        "property_type": s.property_type,
        "prefecture": s.prefecture,
        "city": s.city,
        "address": s.address,
        "building_name": s.building_name,
        "deal_type": s.deal_type,
        "price_yen": s.price_yen,
        "management_fee_yen": s.management_fee_yen,
        "deposit_yen": s.deposit_yen,
        "key_money_yen": s.key_money_yen,
        "area_m2": s.area_m2,
        "land_area_m2": s.land_area_m2,
        "layout": s.layout,
        "floor": s.floor,
        "built_year": s.built_year,
        "station": s.station,
        "walk_minutes": s.walk_minutes,
        "observation_status": s.observation_status,
        "url_status": s.url_status,
        "first_seen_at": s.first_seen_at,
        "last_seen_at": s.last_seen_at,
        "last_checked_at": s.last_checked_at,
        "manual_import": s.manual_import,
        "raw_enums": s.raw_enums,
    }


def _tags_for(db: Session, ids: list[int]) -> dict[int, list[str]]:
    out: dict[int, list[str]] = {i: [] for i in ids}
    if not ids:
        return out
    for lid, name in db.execute(
        select(ListingTag.listing_id, Tag.name)
        .join(Tag, Tag.id == ListingTag.tag_id)
        .where(ListingTag.listing_id.in_(ids))
    ):
        out[lid].append(name)
    return out


def listing_to_dict(
    db: Session,
    listing: Listing,
    tags: list[str] | None = None,
    favorite: Favorite | None = None,
    last_event: ListingEvent | None = None,
) -> dict[str, Any]:
    sources = sorted(listing.sources, key=lambda s: s.id)
    active_prices = [s.price_yen for s in sources if s.price_yen and s.observation_status == "active"]
    all_prices = [s.price_yen for s in sources if s.price_yen]
    title = next((s.title for s in sources if s.title), None)
    return {
        "id": listing.id,
        "title": title,
        "deal_type": listing.deal_type,
        "property_type": listing.property_type,
        "prefecture": listing.prefecture,
        "city": listing.city,
        "address": next((s.address for s in sources if s.address), None),
        "building_name": listing.building_name,
        "layout": listing.layout,
        "area_m2": listing.area_m2,
        "land_area_m2": listing.land_area_m2,
        "floor": listing.floor,
        "built_year": listing.built_year,
        "price_yen": min(active_prices) if active_prices else (min(all_prices) if all_prices else None),
        "current_status": listing.current_status,
        "review_status": listing.review_status,
        "first_seen_at": min((s.first_seen_at for s in sources), default=None),
        "last_seen_at": max((s.last_seen_at for s in sources), default=None),
        "site_ids": sorted({s.site_id for s in sources}),
        "sources": [source_to_dict(s) for s in sources],
        "tags": tags or [],
        "favorite": None
        if favorite is None
        else {
            "status": favorite.status,
            "research_status": favorite.research_status,
            "visit_date": favorite.visit_date,
        },
        "last_event": None
        if last_event is None
        else {
            "event_type": last_event.event_type,
            "created_at": last_event.created_at,
            "old_price_yen": last_event.old_price_yen,
            "new_price_yen": last_event.new_price_yen,
        },
    }


@router.get("/properties")
def list_properties(
    q: str | None = None,
    deal_type: str | None = None,
    site_id: str | None = None,
    prefecture: str | None = None,
    city: str | None = None,
    property_type: str | None = None,
    price_min: float | None = None,
    price_max: float | None = None,
    area_min: float | None = None,
    area_max: float | None = None,
    status: str | None = None,
    review_status: str | None = None,
    event: str | None = None,
    event_date: date | None = None,
    event_from: date | None = None,
    event_to: date | None = None,
    task_id: int | None = None,
    run_id: int | None = None,
    favorite: bool | None = None,
    tag: str | None = None,
    sort: str = "newest",
    page: int = 1,
    page_size: int = 50,
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    tz = ZoneInfo(app_settings.get(db, "timezone"))
    src_filters = []
    if site_id:
        src_filters.append(ListingSource.site_id == site_id)
    if price_min is not None:
        src_filters.append(ListingSource.price_yen >= price_min * 10_000)
    if price_max is not None:
        src_filters.append(ListingSource.price_yen <= price_max * 10_000)
    if q:
        like = f"%{q.strip()}%"
        src_filters.append(
            or_(
                ListingSource.title.like(like),
                ListingSource.address.like(like),
                ListingSource.building_name.like(like),
                ListingSource.external_listing_id.like(like),
            )
        )
    if task_id:
        src_filters.append(
            ListingSource.id.in_(
                select(RunResult.source_id)
                .join(TaskRun, TaskRun.id == RunResult.run_id)
                .where(TaskRun.task_id == task_id)
            )
        )
    if run_id:
        src_filters.append(ListingSource.id.in_(select(RunResult.source_id).where(RunResult.run_id == run_id)))
    query = select(Listing).where(Listing.deleted_at.is_(None), Listing.merged_into_id.is_(None))
    if src_filters:
        query = query.where(exists().where(ListingSource.listing_id == Listing.id, and_(*src_filters)))
    if deal_type:
        query = query.where(Listing.deal_type == deal_type)
    if prefecture:
        query = query.where(Listing.prefecture == prefecture)
    if city:
        query = query.where(Listing.city == city)
    if property_type:
        query = query.where(Listing.property_type == property_type)
    if area_min is not None:
        query = query.where(Listing.area_m2 >= area_min)
    if area_max is not None:
        query = query.where(Listing.area_m2 <= area_max)
    if status:
        query = query.where(Listing.current_status.in_(status.split(",")))
    if review_status:
        query = query.where(Listing.review_status.in_(review_status.split(",")))
    if event or event_date or event_from or event_to:
        ev = [ListingEvent.listing_id == Listing.id]
        if event:
            ev.append(ListingEvent.event_type.in_(event.split(",")))
        if event_date:
            event_from = event_to = event_date
        if event_from:
            ev.append(ListingEvent.created_at >= datetime.combine(event_from, time.min, tz))
        if event_to:
            ev.append(ListingEvent.created_at < datetime.combine(event_to + timedelta(days=1), time.min, tz))
        if run_id:
            ev.append(ListingEvent.run_id == run_id)
        query = query.where(exists().where(and_(*ev)))
    if favorite is True:
        query = query.where(exists().where(Favorite.listing_id == Listing.id))
    if tag:
        query = query.where(
            exists().where(ListingTag.listing_id == Listing.id, ListingTag.tag_id == Tag.id, Tag.name == tag)
        )
    total = db.execute(select(func.count()).select_from(query.subquery())).scalar_one()
    min_price = (
        select(func.min(ListingSource.price_yen))
        .where(ListingSource.listing_id == Listing.id)
        .correlate(Listing)
        .scalar_subquery()
    )
    first_seen = (
        select(func.min(ListingSource.first_seen_at))
        .where(ListingSource.listing_id == Listing.id)
        .correlate(Listing)
        .scalar_subquery()
    )
    order = {
        "price_asc": min_price.asc(),
        "price_desc": min_price.desc(),
        "area_desc": Listing.area_m2.desc(),
        "first_seen": first_seen.asc(),
    }.get(sort, first_seen.desc())
    page_size = max(1, min(page_size, 200))
    rows = (
        db.execute(query.order_by(order, Listing.id.desc()).offset((max(page, 1) - 1) * page_size).limit(page_size))
        .scalars()
        .all()
    )
    ids = [r.id for r in rows]
    tags = _tags_for(db, ids)
    favs = {f.listing_id: f for f in db.execute(select(Favorite).where(Favorite.listing_id.in_(ids))).scalars()}
    last_events: dict[int, ListingEvent] = {}
    for e in db.execute(
        select(ListingEvent).where(ListingEvent.listing_id.in_(ids)).order_by(ListingEvent.id)
    ).scalars():
        last_events[e.listing_id] = e
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [listing_to_dict(db, r, tags.get(r.id), favs.get(r.id), last_events.get(r.id)) for r in rows],
    }


@router.get("/properties/{listing_id}")
def get_property(listing_id: int, db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    listing = _listing(db, listing_id)
    if listing.review_status == "new":
        listing.review_status = "viewed"
    fav = db.execute(select(Favorite).where(Favorite.listing_id == listing_id)).scalar_one_or_none()
    notes = (
        db.execute(
            select(Note)
            .where(Note.listing_id == listing_id, Note.deleted_at.is_(None))
            .order_by(Note.created_at.desc())
        )
        .scalars()
        .all()
    )
    candidates = (
        db.execute(
            select(MatchCandidate).where(
                MatchCandidate.status == "pending",
                or_(MatchCandidate.listing_a_id == listing_id, MatchCandidate.listing_b_id == listing_id),
            )
        )
        .scalars()
        .all()
    )
    found_by = db.execute(
        select(TaskRun.task_id, TaskRun.condition_version, func.min(TaskRun.id))
        .join(RunResult, RunResult.run_id == TaskRun.id)
        .join(ListingSource, ListingSource.id == RunResult.source_id)
        .where(ListingSource.listing_id == listing_id)
        .group_by(TaskRun.task_id, TaskRun.condition_version)
    ).all()
    return {
        **listing_to_dict(db, listing, _tags_for(db, [listing_id])[listing_id], fav),
        "notes": [note_to_dict(n) for n in notes],
        "match_candidates": [candidate_to_dict(db, c) for c in candidates],
        "found_by": [{"task_id": t, "condition_version": v, "first_run_id": r} for t, v, r in found_by],
    }


@router.get("/properties/{listing_id}/history")
def get_history(listing_id: int, db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    _listing(db, listing_id)
    source_ids = [s for (s,) in db.execute(select(ListingSource.id).where(ListingSource.listing_id == listing_id))]
    events = db.execute(
        select(ListingEvent, TaskRun.run_no, TaskRun.task_id)
        .outerjoin(TaskRun, TaskRun.id == ListingEvent.run_id)
        .where(ListingEvent.source_id.in_(source_ids))
        .order_by(ListingEvent.id)
    ).all()
    snaps = (
        db.execute(
            select(ListingSnapshot)
            .where(ListingSnapshot.source_id.in_(source_ids))
            .order_by(ListingSnapshot.captured_at)
        )
        .scalars()
        .all()
    )
    site_of = {
        s.id: s.site_id for s in db.execute(select(ListingSource).where(ListingSource.id.in_(source_ids))).scalars()
    }
    return {
        "events": [
            {
                "id": e.id,
                "event_type": e.event_type,
                "source_id": e.source_id,
                "site_id": site_of.get(e.source_id),
                "run_id": e.run_id,
                "run_no": run_no,
                "task_id": task_id,
                "old_price_yen": e.old_price_yen,
                "new_price_yen": e.new_price_yen,
                "evidence": e.evidence,
                "created_at": e.created_at,
            }
            for e, run_no, task_id in events
        ],
        "snapshots": [
            {
                "id": s.id,
                "source_id": s.source_id,
                "site_id": site_of.get(s.source_id),
                "run_id": s.run_id,
                "captured_at": s.captured_at,
                "price_yen": s.price_yen,
                "status": s.status,
            }
            for s in snaps
        ],
    }


class ReviewIn(BaseModel):
    review_status: str


@router.patch("/properties/{listing_id}")
def patch_property(listing_id: int, body: ReviewIn, db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    listing = _listing(db, listing_id)
    if body.review_status not in ("new", "viewed", "watching", "excluded"):
        raise AppError(ErrorCode.VALIDATION_ERROR, {"field": "review_status"})
    listing.review_status = body.review_status
    return {"id": listing.id, "review_status": listing.review_status}


# ---- notes / research -----------------------------------------------------------------------------------


def note_to_dict(n: Note) -> dict[str, Any]:
    return {
        "id": n.id,
        "listing_id": n.listing_id,
        "kind": n.kind,
        "body": n.body,
        "fields": n.fields,
        "created_at": n.created_at,
        "updated_at": n.updated_at,
    }


class NoteIn(BaseModel):
    kind: str = "note"
    body: str = ""
    fields: dict[str, Any] = Field(default_factory=dict)


@router.post("/properties/{listing_id}/notes", status_code=201)
def add_note(listing_id: int, body: NoteIn, db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    _listing(db, listing_id)
    if body.kind not in ("note", "research"):
        raise AppError(ErrorCode.VALIDATION_ERROR, {"field": "kind"})
    n = Note(listing_id=listing_id, kind=body.kind, body=body.body, fields=body.fields)
    db.add(n)
    db.flush()
    return note_to_dict(n)


@router.patch("/notes/{note_id}")
def update_note(note_id: int, body: NoteIn, db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    n = db.get(Note, note_id)
    if n is None or n.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND)
    n.body, n.fields, n.kind = body.body, body.fields, body.kind
    db.flush()
    return note_to_dict(n)


@router.delete("/notes/{note_id}")
def delete_note(note_id: int, db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    n = db.get(Note, note_id)
    if n is None:
        raise AppError(ErrorCode.NOT_FOUND)
    n.deleted_at = utcnow()
    return {"deleted": True}


# ---- favorites ------------------------------------------------------------------------------------------


class FavoriteIn(BaseModel):
    status: str = "watching"
    research_status: str = "pending_research"
    visit_date: str | None = None


@router.put("/properties/{listing_id}/favorite")
def put_favorite(listing_id: int, body: FavoriteIn, db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    _listing(db, listing_id)
    if body.status not in FAVORITE_STATUSES or body.research_status not in (
        "pending_research",
        "researched",
        "pending_visit",
        "ended",
    ):
        raise AppError(ErrorCode.VALIDATION_ERROR, {"field": "status"})
    fav = db.execute(select(Favorite).where(Favorite.listing_id == listing_id)).scalar_one_or_none()
    if fav is None:
        fav = Favorite(listing_id=listing_id)
        db.add(fav)
    fav.status, fav.research_status, fav.visit_date = body.status, body.research_status, body.visit_date
    db.flush()
    return {
        "listing_id": listing_id,
        "status": fav.status,
        "research_status": fav.research_status,
        "visit_date": fav.visit_date,
    }


@router.delete("/properties/{listing_id}/favorite")
def delete_favorite(listing_id: int, db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    fav = db.execute(select(Favorite).where(Favorite.listing_id == listing_id)).scalar_one_or_none()
    if fav is not None:
        db.delete(fav)
    return {"deleted": True}


@router.get("/favorites")
def list_favorites(status: str | None = None, db: Session = Depends(get_db, scope="function")) -> list[dict[str, Any]]:
    q = select(Favorite, Listing).join(Listing, Listing.id == Favorite.listing_id).order_by(Favorite.updated_at.desc())
    if status:
        q = q.where(Favorite.status.in_(status.split(",")))
    rows = db.execute(q).all()
    ids = [lst.id for _, lst in rows]
    tags = _tags_for(db, ids)
    note_counts: dict[int, int] = dict(
        db.execute(
            select(Note.listing_id, func.count())
            .where(Note.listing_id.in_(ids), Note.deleted_at.is_(None))
            .group_by(Note.listing_id)
        )
        .tuples()
        .all()
    )
    out = []
    for fav, lst in rows:
        target = (db.get(Listing, lst.merged_into_id) if lst.merged_into_id else None) or lst
        d = listing_to_dict(db, target, tags.get(lst.id), fav)
        d["note_count"] = note_counts.get(lst.id, 0)
        out.append(d)
    return out


# ---- tags -----------------------------------------------------------------------------------------------


@router.get("/tags")
def list_tags(db: Session = Depends(get_db, scope="function")) -> list[dict[str, Any]]:
    rows = db.execute(
        select(Tag.id, Tag.name, func.count(ListingTag.listing_id))
        .outerjoin(ListingTag, ListingTag.tag_id == Tag.id)
        .group_by(Tag.id)
        .order_by(Tag.name)
    ).all()
    return [{"id": i, "name": n, "count": c} for i, n, c in rows]


class TagsIn(BaseModel):
    listing_ids: list[int]
    add: list[str] = Field(default_factory=list)
    remove: list[str] = Field(default_factory=list)


@router.post("/tags/apply")
def apply_tags(body: TagsIn, db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    """Batch add/remove tags (merging across sites is never batched - see match candidates)."""
    for name in {n.strip() for n in body.add if n.strip()}:
        tag = db.execute(select(Tag).where(Tag.name == name)).scalar_one_or_none()
        if tag is None:
            tag = Tag(name=name)
            db.add(tag)
            db.flush()
        for lid in body.listing_ids:
            _listing(db, lid)
            if db.get(ListingTag, (lid, tag.id)) is None:
                db.add(ListingTag(listing_id=lid, tag_id=tag.id))
    for name in body.remove:
        tag = db.execute(select(Tag).where(Tag.name == name)).scalar_one_or_none()
        if tag is not None:
            db.query(ListingTag).filter(
                ListingTag.tag_id == tag.id, ListingTag.listing_id.in_(body.listing_ids)
            ).delete(synchronize_session=False)
    db.flush()
    return {"tags": _tags_for(db, body.listing_ids)}


# ---- match candidates -----------------------------------------------------------------------------------


def candidate_to_dict(db: Session, c: MatchCandidate) -> dict[str, Any]:
    a, b = db.get(Listing, c.listing_a_id), db.get(Listing, c.listing_b_id)
    return {
        "id": c.id,
        "score": c.score,
        "reasons": c.reasons,
        "status": c.status,
        "created_at": c.created_at,
        "listing_a": listing_to_dict(db, a) if a else None,
        "listing_b": listing_to_dict(db, b) if b else None,
    }


@router.get("/match-candidates")
def list_candidates(status: str = "pending", db: Session = Depends(get_db, scope="function")) -> list[dict[str, Any]]:
    rows = (
        db.execute(select(MatchCandidate).where(MatchCandidate.status == status).order_by(MatchCandidate.score.desc()))
        .scalars()
        .all()
    )
    out = []
    for c in rows:
        a, b = db.get(Listing, c.listing_a_id), db.get(Listing, c.listing_b_id)
        if status == "pending" and (not a or not b or a.merged_into_id or b.merged_into_id):
            continue
        out.append(candidate_to_dict(db, c))
    return out


@router.post("/match-candidates/{cid}/confirm")
def confirm_candidate(cid: int, db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    c = db.get(MatchCandidate, cid)
    if c is None:
        raise AppError(ErrorCode.NOT_FOUND)
    merged = svc.confirm_match(db, c)
    return listing_to_dict(db, merged)


@router.post("/match-candidates/{cid}/reject")
def reject_candidate(cid: int, db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    c = db.get(MatchCandidate, cid)
    if c is None:
        raise AppError(ErrorCode.NOT_FOUND)
    svc.reject_match(db, c)
    return {"id": cid, "status": c.status}


@router.post("/sources/{source_id}/unlink")
def unlink(source_id: int, db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    s = db.get(ListingSource, source_id)
    if s is None:
        raise AppError(ErrorCode.NOT_FOUND)
    return listing_to_dict(db, svc.unlink_source(db, s))


class LinkIn(BaseModel):
    other_listing_id: int


@router.post("/properties/{listing_id}/link")
def link_manually(listing_id: int, body: LinkIn, db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    """User-initiated association of two listings; goes through the same explicit confirmation path."""
    _listing(db, listing_id)
    _listing(db, body.other_listing_id)
    if listing_id == body.other_listing_id:
        raise AppError(ErrorCode.VALIDATION_ERROR, {"field": "other_listing_id"})
    a, b = sorted((listing_id, body.other_listing_id))
    c = db.execute(
        select(MatchCandidate).where(MatchCandidate.listing_a_id == a, MatchCandidate.listing_b_id == b)
    ).scalar_one_or_none()
    if c is None:
        c = MatchCandidate(listing_a_id=a, listing_b_id=b, score=1.0, reasons=["manual"])
        db.add(c)
        db.flush()
    elif c.status != "pending":
        c.status = "pending"
    return listing_to_dict(db, svc.confirm_match(db, c))


@router.post("/sources/{source_id}/check-url")
def check_url(source_id: int, db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    """Probe whether the original page is still reachable. No content is stored."""
    import httpx

    from houseagent.runtime import state as runtime

    s = db.get(ListingSource, source_id)
    if s is None:
        raise AppError(ErrorCode.NOT_FOUND)
    url = s.source_url if s.source_url.startswith("http") else runtime.origin + s.source_url
    try:
        r = httpx.head(url, follow_redirects=True, timeout=10)
        if r.status_code == 405:
            r = httpx.get(url, follow_redirects=True, timeout=10)
        s.url_status = "ok" if r.status_code < 400 else "unreachable"
    except httpx.HTTPError:
        s.url_status = "unreachable"
    return source_to_dict(s)


# ---- manual import --------------------------------------------------------------------------------------


class ManualImportIn(BaseModel):
    site_id: str
    source_url: str
    external_listing_id: str | None = None
    title: str | None = None
    property_type: str | None = None
    prefecture: str | None = None
    city: str | None = None
    address: str | None = None
    building_name: str | None = None
    price_man: float | None = None  # buy: price; rent: monthly rent (万円)
    management_fee_yen: int | None = None
    deposit_yen: int | None = None
    key_money_yen: int | None = None
    area_m2: float | None = None
    layout: str | None = None
    built_year: int | None = None


@router.post("/properties/manual-import", status_code=201)
def manual_import(body: ManualImportIn, db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    """User records a listing they viewed themselves (works even when automated search is not permitted)."""
    site = db.get(Site, body.site_id)
    if site is None:
        raise AppError(ErrorCode.VALIDATION_ERROR, {"field": "site_id"})
    url = body.source_url.strip()
    if not (url.startswith("http://") or url.startswith("https://") or url.startswith("/mock-site/")):
        raise AppError(ErrorCode.VALIDATION_ERROR, {"field": "source_url"})
    ext = (body.external_listing_id or "").strip() or "url-" + hashlib.sha1(url.encode()).hexdigest()[:16]
    item = NormalizedSource(
        external_id=ext,
        url=url,
        deal_type="rent" if (body.property_type or "").startswith("rent_") else "buy",
        management_fee_yen=body.management_fee_yen,
        deposit_yen=body.deposit_yen,
        key_money_yen=body.key_money_yen,
        title=body.title,
        property_type=body.property_type,
        prefecture=body.prefecture,
        city=body.city,
        address=body.address,
        building_name=body.building_name,
        price_yen=round(body.price_man * 10_000) if body.price_man is not None else None,
        area_m2=body.area_m2,
        layout=body.layout,
        built_year=body.built_year,
    )
    from houseagent.adapters.base import SUMMARY_FIELDS

    # The user typed these values themselves, so they are user data rather than retained site data.
    src, is_new, _ = svc.upsert_source(
        db, site.id, item, run=None, retention_fields=list(SUMMARY_FIELDS), retention_allowed=True, manual=True
    )
    return {
        "listing_id": src.listing_id,
        "source_id": src.id,
        "created": is_new,
        "automation_allowed": is_allowed(site, "browser_automation"),
    }


@router.post("/properties/{listing_id}/ai-summary")
def ai_summary(listing_id: int, db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    _listing(db, listing_id)
    return ai_service.summarize_listing(db, listing_id, app_settings.get(db, "language"))
