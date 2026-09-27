"""Automatic look-up of a site's robots.txt and terms of use.

HouseAgent does not interpret law. It fetches the site's own robots.txt and terms pages, checks the paths the
adapter will request against robots.txt and quotes terms passages that mention automated collection,
commercial use or reproduction. The user reads the findings and decides; the acceptance is recorded as the
permission source ("personal use only"). A weekly re-check asks again when the rules changed.
"""

from __future__ import annotations

import hashlib
import html
import logging
import re
import urllib.robotparser
from datetime import timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from houseagent.adapters.base import SiteAdapter
from houseagent.adapters.registry import all_adapters, get_adapter
from houseagent.db.models import Site, SiteRuleCheck, utcnow
from houseagent.db.session import session_scope
from houseagent.errors import AppError, ErrorCode
from houseagent.services import notifications
from houseagent.services.accounts import permission_map, update_permission

log = logging.getLogger(__name__)

RECHECK_AFTER = timedelta(days=7)
SOURCE_PREFIX = "rules_check:"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) HouseAgent/1.0 (personal use)"

# (category, keyword). "automation" findings raise a warning; the rest are shown as notes.
KEYWORDS: list[tuple[str, str]] = [
    ("automation", "スクレイピング"),
    ("automation", "クローラ"),
    ("automation", "クローリング"),
    ("automation", "ロボット"),
    ("automation", "自動的"),
    ("automation", "自動で"),
    ("automation", "機械的"),
    ("automation", "プログラム"),
    ("automation", "ボット"),
    ("automation", "大量"),
    ("automation", "過度"),
    ("automation", "scrap"),
    ("automation", "crawl"),
    ("automation", "robot"),
    ("automation", "spider"),
    ("automation", "automated"),
    ("automation", "bot "),
    ("commercial", "商業目的"),
    ("commercial", "営利"),
    ("commercial", "commercial"),
    ("private_use", "私的利用"),
    ("private_use", "私的使用"),
    ("private_use", "personal use"),
    ("reproduction", "複製"),
    ("reproduction", "転載"),
    ("reproduction", "二次利用"),
    ("reproduction", "reproduc"),
]
SNIPPET = 80


def _text(page_html: str) -> str:
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", page_html)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def scan_terms(url: str, text: str) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    lowered = text.lower()
    seen_spans: dict[str, list[tuple[int, int]]] = {}  # per category, so nearby clauses of another kind still show
    for category, kw in KEYWORDS:
        start = 0
        while True:
            i = lowered.find(kw.lower(), start)
            if i < 0:
                break
            start = i + len(kw)
            a, b = max(0, i - SNIPPET), min(len(text), i + len(kw) + SNIPPET)
            spans = seen_spans.setdefault(category, [])
            if any(a < e and b > s for s, e in spans):  # one quote per passage and category
                continue
            spans.append((a, b))
            findings.append(
                {
                    "url": url,
                    "category": category,
                    "keyword": kw,
                    "snippet": ("…" if a else "") + text[a:b] + ("…" if b < len(text) else ""),
                }
            )
            if sum(1 for f in findings if f["keyword"] == kw) >= 3:
                break
    return findings


def run_check(db: Session, adapter: SiteAdapter, client: httpx.Client | None = None) -> SiteRuleCheck:
    info = adapter.rules_info()
    if info is None:
        raise AppError(ErrorCode.CONFLICT, {"site_id": adapter.site_id}, message_key="error.rules_check_not_needed")
    own = client is None
    http = client or httpx.Client(timeout=20, follow_redirects=True, headers={"User-Agent": USER_AGENT})
    robots_findings: list[dict[str, Any]] = []
    terms_findings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    digest = hashlib.sha256()
    try:
        try:
            r = http.get(info.robots_url)
            r.raise_for_status()
            digest.update(r.text.encode("utf-8"))
            parser = urllib.robotparser.RobotFileParser()
            parser.parse(r.text.splitlines())
            for path, purpose in info.paths:
                robots_findings.append(
                    {"path": path, "purpose": purpose, "allowed": parser.can_fetch(info.user_agent, path)}
                )
        except httpx.HTTPError as exc:
            errors.append({"url": info.robots_url, "error": type(exc).__name__})
        for url in info.terms_urls:
            try:
                r = http.get(url)
                r.raise_for_status()
                terms_findings.extend(scan_terms(url, _text(r.text)))
            except httpx.HTTPError as exc:
                errors.append({"url": url, "error": type(exc).__name__})
    finally:
        if own:
            http.close()
    # Stable fingerprint: robots.txt text + the quoted terms passages (not the raw pages, which carry
    # rotating banners and would look like a rule change every time).
    for f in sorted(terms_findings, key=lambda x: (x["url"], x["snippet"])):
        digest.update((f["url"] + f["snippet"]).encode("utf-8"))
    if errors and not robots_findings and not terms_findings:
        status = "unreachable"
    elif (
        any(not f["allowed"] for f in robots_findings)
        or any(f["category"] == "automation" for f in terms_findings)
        or errors
    ):
        status = "warning"
    else:
        status = "ok"
    check = SiteRuleCheck(
        site_id=adapter.site_id,
        status=status,
        robots_url=info.robots_url,
        robots_findings=robots_findings,
        terms_findings=terms_findings,
        fetch_errors=errors,
        content_hash=digest.hexdigest() if (robots_findings or terms_findings) else None,
    )
    db.add(check)
    db.flush()
    return check


def check_to_dict(c: SiteRuleCheck, site: Site | None = None) -> dict[str, Any]:
    info = get_adapter(c.site_id).rules_info()
    return {
        "id": c.id,
        "site_id": c.site_id,
        "checked_at": c.checked_at,
        "status": c.status,
        "robots_url": c.robots_url,
        "robots_findings": c.robots_findings,
        "terms_findings": c.terms_findings,
        "fetch_errors": c.fetch_errors,
        "accepted_at": c.accepted_at,
        "terms_urls": info.terms_urls if info else [],
        "automation_permission": permission_map(site)["browser_automation"] if site else None,
    }


def latest_check(db: Session, site_id: str) -> SiteRuleCheck | None:
    return db.execute(
        select(SiteRuleCheck).where(SiteRuleCheck.site_id == site_id).order_by(SiteRuleCheck.id.desc()).limit(1)
    ).scalar_one_or_none()


def accept(db: Session, check: SiteRuleCheck) -> None:
    """The user read the findings and chose to continue - for personal use only."""
    if check.status == "unreachable":
        raise AppError(ErrorCode.CONFLICT, message_key="error.rules_unreachable")
    blocked = [f["path"] for f in check.robots_findings if not f["allowed"]]
    kws = sorted({f["keyword"] for f in check.terms_findings if f["category"] == "automation"})
    day = utcnow().date().isoformat()
    source = (
        f"{SOURCE_PREFIX}{check.id} accepted by user on {day} for personal, non-commercial use. "
        f"robots.txt disallows: {', '.join(blocked) or 'none'}; terms mention: {', '.join(kws) or 'none'}."
    )
    note = "Automatic rules check accepted by the user; re-checked weekly."
    update_permission(db, check.site_id, "browser_automation", "allowed", source, note)
    update_permission(db, check.site_id, "data_retention", "allowed", source + " Summary fields only.", note)
    update_permission(db, check.site_id, "commercial_use", "denied", source, note)
    check.accepted_at = utcnow()
    db.flush()


def recheck_accepted_sites() -> None:
    """Weekly: re-run checks for sites allowed through an accepted check; ask again if the rules changed."""
    for adapter in all_adapters():
        if adapter.rules_info() is None:
            continue
        try:
            with session_scope() as db:
                site = db.get(Site, adapter.site_id)
                if site is None:
                    continue
                perm = permission_map(site)["browser_automation"]
                if perm["status"] != "allowed" or not (perm["source"] or "").startswith(SOURCE_PREFIX):
                    continue
                last = latest_check(db, site.id)
                if last is not None and last.checked_at > utcnow() - RECHECK_AFTER:
                    continue
                accepted = db.execute(
                    select(SiteRuleCheck)
                    .where(SiteRuleCheck.site_id == site.id, SiteRuleCheck.accepted_at.is_not(None))
                    .order_by(SiteRuleCheck.id.desc())
                    .limit(1)
                ).scalar_one_or_none()
                new = run_check(db, adapter)
                if new.status == "unreachable" or accepted is None:
                    continue
                if new.content_hash != accepted.content_hash:
                    # Rules changed since the user accepted: stop automation until they review again.
                    update_permission(
                        db,
                        site.id,
                        "browser_automation",
                        "unknown",
                        None,
                        f"Rules changed (check {new.id}); review required.",
                    )
                    notifications.add(
                        db, "rules_changed", "notice.rules_changed", site_id=site.id, params={"site": site.name}
                    )
        except Exception:
            log.exception("rules re-check failed for %s", adapter.site_id)
