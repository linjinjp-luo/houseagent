"""Optional AI assistance (FR-09). Off by default and never required.

* Filtering, de-duplication and price maths are deterministic and never use AI.
* Only the fields the user allowed are sent; browser sessions, cookies and credentials never are.
* The AI summarises and explains; it does not judge ownership, legal compliance, real transaction prices
  or agent honesty - the system prompt says so and the UI labels output as AI-generated.
* Requests go through the AI gateway (``ai.gateway``): any configured provider, limits and usage records.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from houseagent.db.models import Listing, ListingEvent, ListingSource, Note
from houseagent.errors import AppError, ErrorCode
from houseagent.services import settings_service as app_settings

log = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You help a home buyer in Japan review listings they are tracking in a personal tool. "
    "Summarise the data given and explain its price and status history in plain language. "
    "The data covers only what the user happened to observe, not the whole market. "
    "Do not make judgements about property rights, building-code compliance, actual transaction "
    "prices or whether an agent is trustworthy; say the user should confirm those with the source "
    "site or a professional. Answer in the language requested. Keep it under 200 words."
)


SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {"summary": {"type": "string"}},
    "required": ["summary"],
    "additionalProperties": False,
}


def build_payload(db: Session, listing_id: int, allowed: list[str], include_notes: bool) -> dict[str, Any]:
    listing = db.get(Listing, listing_id)
    if listing is None:
        raise AppError(ErrorCode.NOT_FOUND)
    sources = db.execute(select(ListingSource).where(ListingSource.listing_id == listing_id)).scalars().all()
    payload: dict[str, Any] = {"sources": []}
    for s in sources:
        # Only fields the user allowed, and only if the site allowed us to retain them in the first place.
        payload["sources"].append(
            {k: getattr(s, k) for k in allowed if hasattr(ListingSource, k)}
            | {"site": s.site_id, "status": s.observation_status}
        )
    if "events" in allowed:
        payload["events"] = [
            {
                "type": e.event_type,
                "at": e.created_at,
                "old_price_yen": e.old_price_yen,
                "new_price_yen": e.new_price_yen,
            }
            for e in db.execute(
                select(ListingEvent).where(ListingEvent.listing_id == listing_id).order_by(ListingEvent.id)
            ).scalars()
        ]
    if include_notes:
        payload["user_notes"] = [
            n.body
            for n in db.execute(select(Note).where(Note.listing_id == listing_id, Note.deleted_at.is_(None))).scalars()
            if n.body
        ]
    return payload


def summarize_listing(db: Session, listing_id: int, language: str) -> dict[str, Any]:
    """Plain-language summary of a listing's history, through the active AI service (any provider)."""
    from houseagent.ai import gateway
    from houseagent.ai.providers import AIError, AIRequest

    cfg = gateway.require_ready(db)
    s = app_settings.get_all(db)
    allowed = [f for f in s["ai_allowed_fields"] if f == "events" or f in (cfg.allowed_fields_json or [])]
    payload = build_payload(db, listing_id, allowed, bool(s["ai_send_notes"]))
    lang = {"ja": "Japanese", "zh": "Simplified Chinese", "en": "English"}.get(language, "Japanese")
    request = AIRequest(
        system=SYSTEM_PROMPT,
        user=f"Language: {lang}\n\nListing data (JSON):\n{json.dumps(payload, ensure_ascii=False, default=str)}",
        schema=SUMMARY_SCHEMA,
        schema_name="listing_summary",
        max_tokens=4000,
    )
    try:
        result = gateway.call(db, cfg, "summary", lambda p: p.generate_json(request))
    except AIError as exc:
        db.commit()  # keep the usage record of the failed call (it may still be billed)
        raise AppError(ErrorCode.VALIDATION_ERROR, message_key=f"error.{exc.code}") from exc
    text = result.data.get("summary")
    if not isinstance(text, str) or not text.strip():
        raise AppError(ErrorCode.VALIDATION_ERROR, message_key="error.ai_bad_response")
    return {
        "summary": text.strip(),
        "model": result.model_version or cfg.model_id,
        "provider": cfg.provider_type,
        "sent_fields": sorted(payload.keys()),
        "ai_generated": True,
    }
