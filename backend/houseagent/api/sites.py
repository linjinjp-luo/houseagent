"""Sites, permission records and site accounts."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from houseagent.db.models import Site, SiteAccount
from houseagent.db.session import get_db
from houseagent.errors import AppError, ErrorCode
from houseagent.services import accounts as svc

router = APIRouter()


def _site(db: Session, site_id: str) -> Site:
    site = db.get(Site, site_id)
    if site is None:
        raise AppError(ErrorCode.NOT_FOUND, {"site_id": site_id})
    return site


@router.get("/sites")
def list_sites(db: Session = Depends(get_db, scope="function")) -> list[dict[str, Any]]:
    sites = db.execute(select(Site).where(Site.enabled.is_(True)).order_by(Site.id)).scalars()
    return [svc.site_to_dict(db, s) for s in sites]


@router.get("/sites/{site_id}")
def get_site(site_id: str, db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    return svc.site_to_dict(db, _site(db, site_id))


class PermissionIn(BaseModel):
    status: str
    source: str | None = None
    note: str | None = None


@router.put("/sites/{site_id}/permissions/{ptype}")
def put_permission(
    site_id: str, ptype: str, body: PermissionIn, db: Session = Depends(get_db, scope="function")
) -> dict[str, Any]:
    site = _site(db, site_id)
    svc.update_permission(db, site_id, ptype, body.status, body.source, body.note)
    db.refresh(site)
    return svc.site_to_dict(db, site)


@router.get("/sites/{site_id}/rules-check")
def get_rules_check(site_id: str, db: Session = Depends(get_db, scope="function")) -> dict[str, Any] | None:
    from houseagent.services import site_rules

    site = _site(db, site_id)
    c = site_rules.latest_check(db, site_id)
    return site_rules.check_to_dict(c, site) if c else None


@router.post("/sites/{site_id}/rules-check")
def run_rules_check(site_id: str, db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    """Fetch the site's robots.txt and terms of use now and report what they say about automated access."""
    from houseagent.adapters.registry import get_adapter
    from houseagent.services import site_rules

    site = _site(db, site_id)
    c = site_rules.run_check(db, get_adapter(site_id))
    return site_rules.check_to_dict(c, site)


@router.post("/sites/{site_id}/rules-check/{check_id}/accept")
def accept_rules_check(site_id: str, check_id: int, db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    """The user read the warnings and continues for personal, non-commercial use."""
    from houseagent.db.models import SiteRuleCheck
    from houseagent.services import site_rules

    site = _site(db, site_id)
    c = db.get(SiteRuleCheck, check_id)
    if c is None or c.site_id != site_id:
        raise AppError(ErrorCode.NOT_FOUND)
    site_rules.accept(db, c)
    db.refresh(site)
    return svc.site_to_dict(db, site)


@router.post("/sites/{site_id}/adapter/reenable")
def reenable_adapter(site_id: str, db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    site = _site(db, site_id)
    site.adapter_status = "ok"
    site.adapter_diagnostic = None
    return svc.site_to_dict(db, site)


# ---- browser-assisted import settings and user-added sites -----------------------------------------------


class AssistedModeIn(BaseModel):
    mode: str


@router.put("/sites/{site_id}/assisted")
def put_assisted_mode(
    site_id: str, body: AssistedModeIn, db: Session = Depends(get_db, scope="function")
) -> dict[str, Any]:
    from houseagent.services import site_assist

    site = _site(db, site_id)
    site_assist.set_mode(site, body.mode)
    return svc.site_to_dict(db, site)


@router.post("/sites/{site_id}/detect-verification")
def detect_verification(site_id: str, db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    """Open the site's search entry page once and report whether it answers with human verification."""
    from houseagent.adapters.registry import get_adapter
    from houseagent.services import site_assist

    site = _site(db, site_id)
    result = site_assist.probe(site, get_adapter(site_id))
    return {**result, "site": svc.site_to_dict(db, site)}


class CustomSiteIn(BaseModel):
    name: str
    links: dict[str, str | None]


class CustomSitePatch(BaseModel):
    name: str | None = None
    links: dict[str, str | None] | None = None


@router.post("/sites", status_code=201)
def add_site(body: CustomSiteIn, db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    from houseagent.services import site_assist

    site = site_assist.create_custom_site(db, body.name, body.links)
    return svc.site_to_dict(db, site)


@router.patch("/sites/{site_id}")
def edit_site(site_id: str, body: CustomSitePatch, db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    from houseagent.services import site_assist

    site = _site(db, site_id)
    site_assist.update_custom_site(db, site, body.name, body.links)
    return svc.site_to_dict(db, site)


@router.delete("/sites/{site_id}")
def remove_site(site_id: str, db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    from houseagent.services import site_assist

    site_assist.remove_custom_site(_site(db, site_id))
    return {"ok": True}


@router.post("/sites/{site_id}/login")
def site_login(site_id: str, db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    """Spec 8: start the manual login flow for the site's first account."""
    acc = (
        db.execute(
            select(SiteAccount)
            .where(SiteAccount.site_id == site_id, SiteAccount.deleted_at.is_(None))
            .order_by(SiteAccount.id)
        )
        .scalars()
        .first()
    )
    if acc is None:
        raise AppError(ErrorCode.NOT_FOUND, message_key="error.no_account_for_site")
    svc.open_login_window(acc)
    return svc.account_to_dict(db, acc)


# ---- accounts -------------------------------------------------------------------------------------------


class AccountIn(BaseModel):
    site_id: str
    account_alias: str
    note: str | None = None
    auto_search_enabled: bool = False


class AccountPatch(BaseModel):
    account_alias: str | None = None
    note: str | None = None
    auto_search_enabled: bool | None = None


@router.get("/accounts")
def list_accounts(site_id: str | None = None, db: Session = Depends(get_db, scope="function")) -> list[dict[str, Any]]:
    q = select(SiteAccount).where(SiteAccount.deleted_at.is_(None)).order_by(SiteAccount.id)
    if site_id:
        q = q.where(SiteAccount.site_id == site_id)
    return [svc.account_to_dict(db, a) for a in db.execute(q).scalars()]


@router.post("/accounts", status_code=201)
def create_account(body: AccountIn, db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    acc = svc.create_account(db, body.site_id, body.account_alias, body.note, body.auto_search_enabled)
    return svc.account_to_dict(db, acc)


@router.patch("/accounts/{account_id}")
def patch_account(
    account_id: int, body: AccountPatch, db: Session = Depends(get_db, scope="function")
) -> dict[str, Any]:
    acc = svc.get_account(db, account_id)
    if body.account_alias is not None:
        if not body.account_alias.strip():
            raise AppError(ErrorCode.VALIDATION_ERROR, {"field": "account_alias"})
        acc.account_alias = body.account_alias.strip()
    if body.note is not None:
        acc.note = body.note
    if body.auto_search_enabled is not None:
        acc.auto_search_enabled = body.auto_search_enabled
    return svc.account_to_dict(db, acc)


@router.get("/accounts/{account_id}/affected-tasks")
def affected(account_id: int, db: Session = Depends(get_db, scope="function")) -> list[dict[str, Any]]:
    svc.get_account(db, account_id)
    return svc.affected_tasks(db, account_id)


@router.post("/accounts/{account_id}/open-login")
def open_login(account_id: int, db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    acc = svc.get_account(db, account_id)
    svc.open_login_window(acc)
    return svc.account_to_dict(db, acc)


@router.post("/accounts/{account_id}/close-login")
def close_login(account_id: int, db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    acc = svc.get_account(db, account_id)
    svc.close_login_window(acc)
    return svc.account_to_dict(db, acc)


@router.post("/accounts/{account_id}/check-login")
def check_login(account_id: int, db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    acc = svc.get_account(db, account_id)
    svc.check_login(db, acc)
    return svc.account_to_dict(db, acc)


@router.post("/accounts/{account_id}/confirm-login")
def confirm_login(account_id: int, db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    acc = svc.get_account(db, account_id)
    svc.confirm_manual_login(db, acc)
    return svc.account_to_dict(db, acc)


@router.post("/accounts/{account_id}/clear-session")
def clear_session(account_id: int, db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    acc = svc.get_account(db, account_id)
    svc.clear_session(db, acc)
    return svc.account_to_dict(db, acc)


class AccountDeleteIn(BaseModel):
    action: str  # rebind | pause | session_only
    rebind_to: int | None = None


@router.post("/accounts/{account_id}/delete")
def delete_account(
    account_id: int, body: AccountDeleteIn, db: Session = Depends(get_db, scope="function")
) -> dict[str, Any]:
    acc = svc.get_account(db, account_id)
    svc.delete_account(db, acc, body.action, body.rebind_to)
    return {"deleted": body.action != "session_only", "action": body.action}
