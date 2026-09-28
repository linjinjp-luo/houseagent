"""Idempotent reference data: the default workspace, installed sites and their permission records."""

from __future__ import annotations

from sqlalchemy.orm import Session

from houseagent.adapters.registry import all_adapters
from houseagent.db.models import PERMISSION_TYPES, Site, SitePermission, Workspace, utcnow

# Mock sites are local fakes built for testing, so every permission is allowed with a recorded source.
_MOCK_SOURCE = "HouseAgent built-in local mock site (fictional data, runs on 127.0.0.1)"


def seed(db: Session, *, include_mock: bool = True) -> None:
    if db.get(Workspace, 1) is None:
        db.add(Workspace(id=1, name="default"))
        db.flush()
    from houseagent.services.site_assist import load_custom_sites

    load_custom_sites(db)
    from houseagent.ai.gateway import migrate_legacy_key

    migrate_legacy_key(db)
    for adapter in all_adapters():
        is_mock = adapter.site_id.startswith("mock_")
        site = db.get(Site, adapter.site_id)
        if site is None:
            site = Site(
                id=adapter.site_id, name=adapter.display_name, base_url=adapter.base_url, adapter=adapter.site_id
            )
            db.add(site)
            db.flush()
        site.enabled = include_mock if is_mock else site.enabled
        existing = {p.permission_type for p in site.permissions}
        for ptype in PERMISSION_TYPES:
            if ptype in existing:
                continue
            if is_mock:
                status = "denied" if ptype in ("commercial_use", "api_access") else "allowed"
                db.add(
                    SitePermission(
                        site_id=site.id, permission_type=ptype, status=status, source=_MOCK_SOURCE, reviewed_at=utcnow()
                    )
                )
            else:
                db.add(SitePermission(site_id=site.id, permission_type=ptype, status="unknown"))
    db.flush()
