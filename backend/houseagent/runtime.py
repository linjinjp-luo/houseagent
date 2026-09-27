"""Per-launch runtime state (actual port, session token)."""

from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass
class RuntimeState:
    port: int = 8765
    token: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    # Set by the launcher: asks the server to exit gracefully (stop accepting runs, cancel safely).
    request_shutdown: Callable[[], None] | None = None

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.port}"


state = RuntimeState()
