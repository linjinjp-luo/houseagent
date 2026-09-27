"""Optional AI assistance (FR-09). Off by default and never required.

* Filtering, de-duplication and price maths are deterministic and never use AI.
* Only the fields the user allowed are sent; browser sessions, cookies and credentials never are.
* The AI summarises and explains; it does not judge ownership, legal compliance, real transaction prices
  or agent honesty - the system prompt says so and the UI labels output as AI-generated.
* The provider sits behind ``AIProvider`` so it can be replaced.
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from houseagent.ai import dpapi
from houseagent.config import get_settings
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


class AIProvider(ABC):
    @abstractmethod
    def summarize(self, payload: dict[str, Any], language: str) -> str: ...


class AnthropicProvider(AIProvider):
    def __init__(self, model: str, api_key: str | None) -> None:
        import anthropic

        # No stored key -> the SDK resolves ANTHROPIC_API_KEY or an `ant auth login` profile.
        self._client = (
            anthropic.Anthropic(api_key=api_key, timeout=60.0) if api_key else anthropic.Anthropic(timeout=60.0)
        )
        self._model = model

    def summarize(self, payload: dict[str, Any], language: str) -> str:
        import anthropic

        lang = {"ja": "Japanese", "zh": "Simplified Chinese", "en": "English"}.get(language, "Japanese")
        try:
            response = self._client.beta.messages.create(
                model=self._model,
                max_tokens=2000,
                system=SYSTEM_PROMPT,
                # Server-side fallback: a policy decline is retried on a fallback model in the same call.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                messages=[
                    {
                        "role": "user",
                        "content": f"Language: {lang}\n\nListing data (JSON):\n"
                        f"{json.dumps(payload, ensure_ascii=False, default=str)}",
                    }
                ],
            )
        except anthropic.AuthenticationError as exc:
            raise AppError(ErrorCode.VALIDATION_ERROR, message_key="error.ai_auth") from exc
        except anthropic.RateLimitError as exc:
            raise AppError(ErrorCode.RATE_LIMITED, message_key="error.ai_rate_limited") from exc
        except anthropic.APIStatusError as exc:
            log.warning("AI request failed with status %s", exc.status_code)
            raise AppError(ErrorCode.UNKNOWN_ERROR, message_key="error.ai_failed") from exc
        except anthropic.APIConnectionError as exc:
            raise AppError(ErrorCode.NETWORK_ERROR, message_key="error.ai_network") from exc
        if response.stop_reason == "refusal":
            raise AppError(ErrorCode.UNKNOWN_ERROR, message_key="error.ai_refused")
        return "".join(b.text for b in response.content if b.type == "text").strip()


# --------------------------------------------------------------------------------------------- key storage


def _key_file() -> Path:
    return get_settings().config_dir / "ai_key.dpapi"


def store_api_key(key: str | None) -> None:
    f = _key_file()
    if not key:
        f.unlink(missing_ok=True)
        return
    if not dpapi.available():
        raise AppError(ErrorCode.VALIDATION_ERROR, message_key="error.ai_key_storage_unavailable")
    f.write_text(dpapi.protect(key.strip()), encoding="ascii")


def has_stored_key() -> bool:
    return _key_file().exists()


def _load_api_key() -> str | None:
    f = _key_file()
    if not f.exists():
        return None
    try:
        return dpapi.unprotect(f.read_text(encoding="ascii"))
    except Exception:
        log.warning("stored AI key could not be decrypted")
        return None


# --------------------------------------------------------------------------------------------- use cases


def _provider(db: Session) -> AIProvider:
    s = app_settings.get_all(db)
    if not s["ai_enabled"]:
        raise AppError(ErrorCode.CONFLICT, message_key="error.ai_disabled")
    return AnthropicProvider(model=s["ai_model"], api_key=_load_api_key())


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
    s = app_settings.get_all(db)
    provider = _provider(db)
    payload = build_payload(db, listing_id, list(s["ai_allowed_fields"]), bool(s["ai_send_notes"]))
    text = provider.summarize(payload, language)
    return {"summary": text, "model": s["ai_model"], "sent_fields": sorted(payload.keys()), "ai_generated": True}
