""" "Needs attention" items (login expired, permission pending, captcha, adapter page changed ...)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from houseagent.db.models import Notification, utcnow


def add(
    db: Session,
    kind: str,
    message_key: str,
    *,
    task_id: int | None = None,
    run_id: int | None = None,
    site_id: str | None = None,
    account_id: int | None = None,
    params: dict[str, Any] | None = None,
) -> Notification:
    # Collapse duplicates of the same open item.
    q = select(Notification).where(
        Notification.kind == kind,
        Notification.resolved_at.is_(None),
        Notification.task_id.is_(task_id) if task_id is None else Notification.task_id == task_id,
        Notification.account_id.is_(account_id) if account_id is None else Notification.account_id == account_id,
    )
    existing = db.execute(q).scalars().first()
    if existing is not None and kind != "reminder":
        existing.run_id = run_id or existing.run_id
        existing.params = params or existing.params
        existing.created_at = utcnow()
        return existing
    n = Notification(
        kind=kind,
        message_key=message_key,
        task_id=task_id,
        run_id=run_id,
        site_id=site_id,
        account_id=account_id,
        params=params or {},
    )
    db.add(n)
    db.flush()
    return n


def resolve_for_account(db: Session, account_id: int, kinds: tuple[str, ...] = ("login_required",)) -> None:
    for n in db.execute(
        select(Notification).where(
            Notification.account_id == account_id, Notification.kind.in_(kinds), Notification.resolved_at.is_(None)
        )
    ).scalars():
        n.resolved_at = utcnow()


def to_dict(n: Notification) -> dict[str, Any]:
    return {
        "id": n.id,
        "kind": n.kind,
        "message_key": n.message_key,
        "task_id": n.task_id,
        "run_id": n.run_id,
        "site_id": n.site_id,
        "account_id": n.account_id,
        "params": n.params,
        "created_at": n.created_at,
        "resolved_at": n.resolved_at,
    }
