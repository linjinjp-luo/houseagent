"""Browser-assisted import for any site, without per-site development.

- Every adapter can read a result page with the generic card extractor, so assisted import only needs to be
  switched on for a site: ``assisted_mode`` on, or ``auto`` once HouseAgent has seen the site answer with
  human verification (an automated run hit CAPTCHA_REQUIRED, or the user's one-off probe found it).
- Users can add sites HouseAgent does not know (name + search entry URLs). They get the generic adapter:
  manual import, "open the site" links and assisted import; never automated search.

HouseAgent never solves or bypasses verification: detection only decides whether to offer the visible browser.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy.orm import Session

from houseagent.adapters.base import SiteAdapter
from houseagent.adapters.generic.extract import looks_like_verification
from houseagent.adapters.manual.adapter import ManualOnlyAdapter
from houseagent.adapters.registry import register
from houseagent.db.models import PERMISSION_TYPES, Site, SitePermission, utcnow
from houseagent.errors import AppError, ErrorCode
from houseagent.regions import DEALS

MODES = ("auto", "on", "off")


def assisted_available(site: Site | None, adapter: SiteAdapter) -> bool:
    """Whether the task screens offer browser-assisted import for this site."""
    mode = site.assisted_mode if site is not None else "auto"
    if mode == "off" or site is None or site.id.startswith("mock_"):
        return False
    if mode == "on" or adapter.get_capabilities().assisted:
        return True
    return site.verification_detected_at is not None


def set_mode(site: Site, mode: str) -> None:
    if mode not in MODES:
        raise AppError(ErrorCode.VALIDATION_ERROR, {"field": "assisted_mode"})
    site.assisted_mode = mode


def mark_verification(site: Site, source: str, url: str | None = None, signal: str | None = None) -> None:
    """Remember that the site showed human verification (turns assisted import on in auto mode)."""
    site.verification_detected_at = utcnow()
    site.verification_probe = {
        "checked_at": utcnow().isoformat(),
        "source": source,
        "detected": True,
        "url": url,
        "signal": signal,
    }


def probe(site: Site, adapter: SiteAdapter, client: httpx.Client | None = None) -> dict[str, Any]:
    """One request per search entry page (user-initiated, like the rules check) to see whether it is verification.

    A clean answer here does not prove the result pages are clean (some sites challenge only later), so the
    user can still switch assisted import on by hand.
    """
    from houseagent.services.site_rules import USER_AGENT

    urls = list(dict.fromkeys(adapter.get_capabilities().links.values())) or [adapter.base_url]
    http = client or httpx.Client(timeout=20, follow_redirects=True, headers={"User-Agent": USER_AGENT})
    results: list[dict[str, Any]] = []
    try:
        for url in urls[:2]:
            try:
                r = http.get(url)
                signal = looks_like_verification(r.text, r.status_code)
                results.append({"url": url, "status": r.status_code, "signal": signal})
            except httpx.HTTPError as exc:
                results.append({"url": url, "status": None, "signal": None, "error": type(exc).__name__})
    finally:
        if client is None:
            http.close()
    hit = next((r for r in results if r["signal"]), None)
    reachable = any(r["status"] is not None for r in results)
    if hit:
        mark_verification(site, "probe", hit["url"], hit["signal"])
    else:
        site.verification_detected_at = None
        site.verification_probe = {
            "checked_at": utcnow().isoformat(),
            "source": "probe",
            "detected": False,
            "reachable": reachable,
            "url": urls[0],
        }
    return {"detected": hit is not None, "reachable": reachable, "results": results}


# ---- user-added sites -----------------------------------------------------------------------------------


def _clean_url(value: str | None, field: str) -> str | None:
    v = (value or "").strip()
    if not v:
        return None
    u = urlparse(v)
    if u.scheme not in ("http", "https") or not u.netloc:
        raise AppError(ErrorCode.VALIDATION_ERROR, {"field": field}, message_key="error.invalid_url")
    return v


def custom_adapter(site: Site) -> ManualOnlyAdapter:
    links = dict((site.custom_config or {}).get("links") or {})
    return ManualOnlyAdapter(site.id, site.name, site.base_url, links)


def load_custom_sites(db: Session) -> None:
    """Register the adapters of user-added sites (called at start-up before anything asks for them)."""
    for site in db.query(Site).filter(Site.custom_config.is_not(None)).all():
        register(custom_adapter(site))


def create_custom_site(db: Session, name: str, links: dict[str, str | None]) -> Site:
    name = (name or "").strip()
    if not name:
        raise AppError(ErrorCode.VALIDATION_ERROR, {"field": "name"})
    clean = {d: u for d in DEALS if (u := _clean_url(links.get(d), f"links.{d}"))}
    if not clean:
        raise AppError(ErrorCode.VALIDATION_ERROR, {"field": "links"}, message_key="error.custom_site_link_required")
    first = urlparse(next(iter(clean.values())))
    base_url = f"{first.scheme}://{first.netloc}"
    slug = re.sub(r"[^a-z0-9]+", "_", first.netloc.lower().removeprefix("www.")).strip("_")[:30] or "site"
    site_id, n = f"custom_{slug}", 2
    while db.get(Site, site_id) is not None:
        existing = db.get(Site, site_id)
        if existing is not None and existing.custom_config is not None and not existing.enabled:
            break  # re-adding a removed site keeps its listings and history
        site_id, n = f"custom_{slug}_{n}", n + 1
    site = db.get(Site, site_id)
    if site is None:
        site = Site(id=site_id, name=name, base_url=base_url, adapter="generic")
        db.add(site)
    site.name, site.base_url, site.enabled = name[:200], base_url, True
    site.custom_config = {"links": clean}
    site.assisted_mode = "on"  # a user-added site is only reachable through manual / browser-assisted import
    db.flush()
    have = {p.permission_type for p in site.permissions}
    for ptype in PERMISSION_TYPES:
        if ptype not in have:
            db.add(SitePermission(site_id=site.id, permission_type=ptype, status="unknown"))
    register(custom_adapter(site))
    db.flush()
    return site


def update_custom_site(db: Session, site: Site, name: str | None, links: dict[str, str | None] | None) -> None:
    if site.custom_config is None:
        raise AppError(ErrorCode.CONFLICT, message_key="error.not_custom_site")
    if name is not None and name.strip():
        site.name = name.strip()[:200]
    if links is not None:
        clean = {d: u for d in DEALS if (u := _clean_url(links.get(d), f"links.{d}"))}
        if not clean:
            raise AppError(
                ErrorCode.VALIDATION_ERROR, {"field": "links"}, message_key="error.custom_site_link_required"
            )
        site.custom_config = {"links": clean}
    db.flush()
    register(custom_adapter(site))


def remove_custom_site(site: Site) -> None:
    """Hide a user-added site. Its listings, runs and history stay (tasks keep pointing at it)."""
    if site.custom_config is None:
        raise AppError(ErrorCode.CONFLICT, message_key="error.not_custom_site")
    site.enabled = False
