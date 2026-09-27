"""Unified error codes (spec 15.9) and the API error type.

Business logic only ever deals with stable codes; the frontend maps ``message_key`` to ja/zh/en text.
"""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    AUTH_REQUIRED = "AUTH_REQUIRED"
    PERMISSION_BLOCKED = "PERMISSION_BLOCKED"
    CAPTCHA_REQUIRED = "CAPTCHA_REQUIRED"
    RATE_LIMITED = "RATE_LIMITED"
    NETWORK_ERROR = "NETWORK_ERROR"
    PAGE_TIMEOUT = "PAGE_TIMEOUT"
    PAGE_CHANGED = "PAGE_CHANGED"
    CONDITION_UNSUPPORTED = "CONDITION_UNSUPPORTED"
    BROWSER_START_FAILED = "BROWSER_START_FAILED"
    DATABASE_ERROR = "DATABASE_ERROR"
    CANCELLED = "CANCELLED"
    INTERRUPTED = "INTERRUPTED"
    UNKNOWN_ERROR = "UNKNOWN_ERROR"
    # API-level (non-run) codes
    VALIDATION_ERROR = "VALIDATION_ERROR"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    UNAUTHORIZED = "UNAUTHORIZED"
    READ_ONLY = "READ_ONLY"


# Codes that may be retried automatically (spec 15.5). Auth / permission / captcha never are.
RETRYABLE: frozenset[ErrorCode] = frozenset({ErrorCode.NETWORK_ERROR, ErrorCode.PAGE_TIMEOUT})

# Codes that stop a run and require a human (mapped to run status "paused").
NEEDS_USER: frozenset[ErrorCode] = frozenset(
    {ErrorCode.AUTH_REQUIRED, ErrorCode.CAPTCHA_REQUIRED, ErrorCode.RATE_LIMITED, ErrorCode.PERMISSION_BLOCKED}
)


def new_correlation_id() -> str:
    return uuid.uuid4().hex[:12]


class AppError(Exception):
    """Error surfaced through the API as ``{error_code, message_key, correlation_id, details}``."""

    status_code: int = 400

    def __init__(
        self,
        code: ErrorCode,
        details: dict[str, Any] | None = None,
        *,
        status_code: int | None = None,
        message_key: str | None = None,
    ) -> None:
        super().__init__(code.value)
        self.code = code
        self.details = details or {}
        self.message_key = message_key or f"error.{code.value}"
        self.correlation_id = new_correlation_id()
        if status_code is not None:
            self.status_code = status_code
        elif code == ErrorCode.NOT_FOUND:
            self.status_code = 404
        elif code == ErrorCode.CONFLICT:
            self.status_code = 409
        elif code == ErrorCode.UNAUTHORIZED:
            self.status_code = 401
        elif code in (ErrorCode.PERMISSION_BLOCKED, ErrorCode.READ_ONLY):
            self.status_code = 403
        elif code == ErrorCode.DATABASE_ERROR:
            self.status_code = 500

    def to_dict(self) -> dict[str, Any]:
        return {
            "error_code": self.code.value,
            "message_key": self.message_key,
            "correlation_id": self.correlation_id,
            "details": self.details,
        }


class AdapterError(Exception):
    """Raised by site adapters; always carries a unified code (``classify_error`` output)."""

    def __init__(self, code: ErrorCode, message: str = "", *, diagnostic: dict[str, Any] | None = None) -> None:
        super().__init__(message or code.value)
        self.code = code
        self.diagnostic = diagnostic or {}

    @property
    def retryable(self) -> bool:
        return self.code in RETRYABLE
