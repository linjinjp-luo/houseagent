"""ORM models (spec 7 and 15.7).

Two-layer listing model: ``Listing`` is the real-world property (HouseAgent's own ID, never a site ID);
``ListingSource`` is one publication on one site, unique by ``site_id + external_listing_id``.
Snapshots are only written when a source is new or something changed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """Stores naive UTC in SQLite and always returns aware UTC datetimes."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC)


class Base(DeclarativeBase):
    type_annotation_map = {datetime: UTCDateTime(), dict[str, Any]: JSON, list[Any]: JSON}


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow, nullable=False)


# --------------------------------------------------------------------------------------------- workspace


class Workspace(TimestampMixin, Base):
    """V1.0 is single-workspace; the table exists so data is not bound to one Windows user forever."""

    __tablename__ = "workspaces"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))


class AppSetting(Base):
    __tablename__ = "app_settings"
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), primary_key=True, default=1)
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


# --------------------------------------------------------------------------------------------- sites


class Site(TimestampMixin, Base):
    __tablename__ = "sites"
    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    base_url: Mapped[str] = mapped_column(String(500))
    adapter: Mapped[str] = mapped_column(String(50))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # ok | stopped - set to stopped on PAGE_CHANGED until the user re-enables it after checking
    adapter_status: Mapped[str] = mapped_column(String(20), default="ok")
    adapter_diagnostic: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    # Browser-assisted import: auto (offered once human verification was detected) | on | off
    assisted_mode: Mapped[str] = mapped_column(String(10), default="auto", server_default="auto")
    # Last time HouseAgent saw the site answer with human verification (automated run or the user's probe)
    verification_detected_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    verification_probe: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    # User-added site (no dedicated adapter): {"links": {deal: search entry URL}}
    custom_config: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    permissions: Mapped[list[SitePermission]] = relationship(back_populates="site", cascade="all, delete-orphan")


PERMISSION_TYPES = ("browser_automation", "data_retention", "commercial_use", "image_storage", "api_access")


class SitePermission(Base):
    """Per-site rule record; default ``unknown`` means the automated feature stays disabled (spec 2.3)."""

    __tablename__ = "site_permissions"
    __table_args__ = (UniqueConstraint("site_id", "permission_type"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id"))
    permission_type: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20), default="unknown")  # allowed | denied | unknown
    source: Mapped[str | None] = mapped_column(Text)  # rule source / authorization document / official reply
    reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    note: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)

    site: Mapped[Site] = relationship(back_populates="permissions")


class SiteRuleCheck(Base):
    """Automatic look-up of a site's robots.txt and terms of use, and the user's acceptance of the findings."""

    __tablename__ = "site_rule_checks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id"), index=True)
    checked_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    # ok | warning | unreachable
    status: Mapped[str] = mapped_column(String(20))
    robots_url: Mapped[str | None] = mapped_column(String(500))
    # [{"path": "/jj/...", "allowed": false, "purpose": "search"}]
    robots_findings: Mapped[list[Any]] = mapped_column(JSON, default=list)
    # [{"url": ..., "keyword": ..., "category": "automation|commercial|private_use|reproduction", "snippet": ...}]
    terms_findings: Mapped[list[Any]] = mapped_column(JSON, default=list)
    # fetch problems: [{"url": ..., "error": ...}]
    fetch_errors: Mapped[list[Any]] = mapped_column(JSON, default=list)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    accepted_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class SiteAccount(TimestampMixin, Base):
    """An account alias. Only the profile directory location is stored - never a password or cookie."""

    __tablename__ = "site_accounts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id"))
    account_alias: Mapped[str] = mapped_column(String(200))
    profile_path: Mapped[str] = mapped_column(String(1000), unique=True)
    # not_logged_in | valid | expiring | relogin_required | check_failed
    login_status: Mapped[str] = mapped_column(String(30), default="not_logged_in")
    auto_search_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    last_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    note: Mapped[str | None] = mapped_column(Text)
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    site: Mapped[Site] = relationship()


# --------------------------------------------------------------------------------------------- tasks


class SearchTask(TimestampMixin, Base):
    __tablename__ = "search_tasks"
    __table_args__ = (
        Index("uq_task_name_active", "workspace_id", "name", unique=True, sqlite_where=text("deleted_at IS NULL")),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), default=1)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="active")  # draft | active | paused
    priority: Mapped[str] = mapped_column(String(10), default="normal")  # high | normal | low
    # manual | daily | weekly | interval | startup | reminder
    schedule_type: Mapped[str] = mapped_column(String(20), default="manual")
    schedule: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Tokyo")
    condition_current_version: Mapped[int] = mapped_column(Integer, default=1)
    next_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_success_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_startup_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    paused_reason: Mapped[str | None] = mapped_column(String(50))
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    sites: Mapped[list[SearchTaskSite]] = relationship(back_populates="task", cascade="all, delete-orphan")
    versions: Mapped[list[SearchConditionVersion]] = relationship(
        back_populates="task", cascade="all, delete-orphan", order_by="SearchConditionVersion.version"
    )


class SearchConditionVersion(Base):
    __tablename__ = "search_condition_versions"
    __table_args__ = (UniqueConstraint("task_id", "version"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("search_tasks.id"))
    version: Mapped[int] = mapped_column(Integer)
    condition_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)

    task: Mapped[SearchTask] = relationship(back_populates="versions")


class SearchTaskSite(Base):
    __tablename__ = "search_task_sites"
    __table_args__ = (UniqueConstraint("task_id", "site_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("search_tasks.id"))
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id"))
    account_id: Mapped[int | None] = mapped_column(ForeignKey("site_accounts.id"))
    site_specific_config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    task: Mapped[SearchTask] = relationship(back_populates="sites")
    account: Mapped[SiteAccount | None] = relationship()


class TaskRun(TimestampMixin, Base):
    """One execution of a task against one site. History is append-only."""

    __tablename__ = "task_runs"
    __table_args__ = (Index("ix_task_runs_status", "status"), Index("ix_task_runs_task", "task_id"))
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_no: Mapped[str] = mapped_column(String(40), unique=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("search_tasks.id"))
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id"))
    account_id: Mapped[int | None] = mapped_column(ForeignKey("site_accounts.id"))
    account_alias: Mapped[str | None] = mapped_column(String(200))  # frozen for traceability
    condition_version: Mapped[int] = mapped_column(Integer)
    # manual | daily | weekly | interval | startup | catchup
    trigger_type: Mapped[str] = mapped_column(String(20))
    priority_rank: Mapped[int] = mapped_column(Integer, default=2)  # 0 manual,1 high,2 normal,3 low
    # queued | running | completed | paused | failed | cancelled | interrupted
    status: Mapped[str] = mapped_column(String(20), default="queued")
    queued_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    not_before: Mapped[datetime | None] = mapped_column(UTCDateTime())
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(40))
    error_detail: Mapped[str | None] = mapped_column(Text)
    correlation_id: Mapped[str | None] = mapped_column(String(20))
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    cancel_reason: Mapped[str | None] = mapped_column(String(200))
    result_count: Mapped[int] = mapped_column(Integer, default=0)
    new_count: Mapped[int] = mapped_column(Integer, default=0)
    changed_count: Mapped[int] = mapped_column(Integer, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, default=0)
    not_found_count: Mapped[int] = mapped_column(Integer, default=0)
    pages: Mapped[int] = mapped_column(Integer, default=0)
    unsupported_conditions: Mapped[list[Any]] = mapped_column(JSON, default=list)
    # Live progress: queued | preflight | login_check | searching | saving | finishing | done
    progress_stage: Mapped[str] = mapped_column(String(20), default="queued")
    progress_pct: Mapped[int] = mapped_column(Integer, default=0)
    expected_total: Mapped[int | None] = mapped_column(Integer)  # result count the site reported, if any
    # Why a queued run has not started: account_login_window | account_busy | max_parallel | retry_backoff
    wait_reason: Mapped[str | None] = mapped_column(String(40))

    task: Mapped[SearchTask] = relationship()


class RunLogEntry(Base):
    __tablename__ = "run_log_entries"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("task_runs.id"), index=True)
    level: Mapped[str] = mapped_column(String(20))  # info | warning | failure | action_required
    message_key: Mapped[str] = mapped_column(String(100))
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


# --------------------------------------------------------------------------------------------- listings


class Listing(TimestampMixin, Base):
    """Real-world property. Sources on multiple sites may point here after the user confirms a match."""

    __tablename__ = "listings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    deal_type: Mapped[str] = mapped_column(String(10), default="buy", server_default="buy")  # buy | rent
    property_type: Mapped[str | None] = mapped_column(String(40))
    prefecture: Mapped[str | None] = mapped_column(String(40))
    city: Mapped[str | None] = mapped_column(String(80))
    address_normalized: Mapped[str | None] = mapped_column(String(300))
    building_name: Mapped[str | None] = mapped_column(String(300))
    layout: Mapped[str | None] = mapped_column(String(20))
    area_m2: Mapped[float | None] = mapped_column(Float)
    land_area_m2: Mapped[float | None] = mapped_column(Float)
    floor: Mapped[int | None] = mapped_column(Integer)
    built_year: Mapped[int | None] = mapped_column(Integer)
    # active | not_found | unavailable | ended  (derived from sources)
    current_status: Mapped[str] = mapped_column(String(20), default="active")
    # new | viewed | watching | excluded
    review_status: Mapped[str] = mapped_column(String(20), default="new")
    merged_into_id: Mapped[int | None] = mapped_column(ForeignKey("listings.id"))
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    sources: Mapped[list[ListingSource]] = relationship(back_populates="listing")


class ListingSource(TimestampMixin, Base):
    __tablename__ = "listing_sources"
    __table_args__ = (UniqueConstraint("site_id", "external_listing_id", name="uq_source_site_external"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"), index=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id"))
    external_listing_id: Mapped[str] = mapped_column(String(200))
    source_url: Mapped[str] = mapped_column(String(1000))
    title: Mapped[str | None] = mapped_column(String(500))
    property_type: Mapped[str | None] = mapped_column(String(40))
    prefecture: Mapped[str | None] = mapped_column(String(40))
    city: Mapped[str | None] = mapped_column(String(80))
    address: Mapped[str | None] = mapped_column(String(300))
    building_name: Mapped[str | None] = mapped_column(String(300))
    deal_type: Mapped[str] = mapped_column(String(10), default="buy", server_default="buy")  # buy | rent
    # buy: sale price; rent: monthly rent (without management fee)
    price_yen: Mapped[int | None] = mapped_column(Integer)
    management_fee_yen: Mapped[int | None] = mapped_column(Integer)  # rent: monthly 管理費・共益費
    deposit_yen: Mapped[int | None] = mapped_column(Integer)  # rent: 敷金
    key_money_yen: Mapped[int | None] = mapped_column(Integer)  # rent: 礼金
    area_m2: Mapped[float | None] = mapped_column(Float)
    land_area_m2: Mapped[float | None] = mapped_column(Float)
    layout: Mapped[str | None] = mapped_column(String(20))
    floor: Mapped[int | None] = mapped_column(Integer)
    built_year: Mapped[int | None] = mapped_column(Integer)
    station: Mapped[str | None] = mapped_column(String(100))
    walk_minutes: Mapped[int | None] = mapped_column(Integer)
    # active | not_found | unavailable | ended
    observation_status: Mapped[str] = mapped_column(String(20), default="active")
    url_status: Mapped[str] = mapped_column(String(20), default="ok")  # ok | unreachable
    raw_enums: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    last_checked_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    manual_import: Mapped[bool] = mapped_column(Boolean, default=False)

    listing: Mapped[Listing] = relationship(back_populates="sources")


class ListingSnapshot(Base):
    __tablename__ = "listing_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("listing_sources.id"), index=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("task_runs.id"))
    captured_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    price_yen: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20))
    summary_hash: Mapped[str] = mapped_column(String(64))
    fields: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


EVENT_TYPES = ("NEW", "PRICE_DOWN", "PRICE_UP", "NOT_FOUND", "UNAVAILABLE", "REAPPEARED", "SALE_ENDED")


class ListingEvent(Base):
    __tablename__ = "listing_events"
    __table_args__ = (Index("ix_listing_events_type_created", "event_type", "created_at"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("listing_sources.id"), index=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"), index=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("task_runs.id"))
    event_type: Mapped[str] = mapped_column(String(20))
    old_price_yen: Mapped[int | None] = mapped_column(Integer)
    new_price_yen: Mapped[int | None] = mapped_column(Integer)
    evidence: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class RunResult(Base):
    """Which sources a run observed - answers "under which condition was this listing found"."""

    __tablename__ = "run_results"
    run_id: Mapped[int] = mapped_column(ForeignKey("task_runs.id"), primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("listing_sources.id"), primary_key=True)
    rank: Mapped[int] = mapped_column(Integer, default=0)
    is_new: Mapped[bool] = mapped_column(Boolean, default=False)
    is_changed: Mapped[bool] = mapped_column(Boolean, default=False)


class MatchCandidate(Base):
    """Suspected cross-site duplicate. Never merged automatically."""

    __tablename__ = "match_candidates"
    __table_args__ = (UniqueConstraint("listing_a_id", "listing_b_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    listing_a_id: Mapped[int] = mapped_column(ForeignKey("listings.id"))
    listing_b_id: Mapped[int] = mapped_column(ForeignKey("listings.id"))
    score: Mapped[float] = mapped_column(Float)
    reasons: Mapped[list[Any]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | confirmed | rejected
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


# --------------------------------------------------------------------------------------------- user data

FAVORITE_STATUSES = ("watching", "planning_visit", "visited", "not_considering", "ended")


class Favorite(TimestampMixin, Base):
    __tablename__ = "favorites"
    __table_args__ = (UniqueConstraint("workspace_id", "listing_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), default=1)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"))
    status: Mapped[str] = mapped_column(String(30), default="watching")
    # pending_research | researched | pending_visit | ended
    research_status: Mapped[str] = mapped_column(String(30), default="pending_research")
    visit_date: Mapped[str | None] = mapped_column(String(20))


class Note(TimestampMixin, Base):
    """Personal note or research record. Survives the source being delisted."""

    __tablename__ = "notes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), default=1)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20), default="note")  # note | research
    body: Mapped[str] = mapped_column(Text, default="")
    # research: sunlight, transport, surroundings, building_condition, price_evaluation (1-5) + pros/cons/next
    fields: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class Tag(Base):
    __tablename__ = "tags"
    __table_args__ = (UniqueConstraint("workspace_id", "name"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), default=1)
    name: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class ListingTag(Base):
    __tablename__ = "listing_tags"
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"), primary_key=True)
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id"), primary_key=True)


class Notification(Base):
    """ "Needs attention" items and reminder-mode todos."""

    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # reminder | login_required | permission_required | captcha | page_changed | task_paused | run_failed
    kind: Mapped[str] = mapped_column(String(40))
    task_id: Mapped[int | None] = mapped_column(ForeignKey("search_tasks.id"))
    run_id: Mapped[int | None] = mapped_column(ForeignKey("task_runs.id"))
    site_id: Mapped[str | None] = mapped_column(ForeignKey("sites.id"))
    account_id: Mapped[int | None] = mapped_column(ForeignKey("site_accounts.id"))
    message_key: Mapped[str] = mapped_column(String(100))
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
