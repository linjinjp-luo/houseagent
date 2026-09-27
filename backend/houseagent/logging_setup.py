"""Logging with redaction (spec 10.3 / 15.6).

Logs must never contain passwords, cookies, auth tokens or captcha values. A filter scrubs anything that
looks like one before a record is written.
"""

from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

_PATTERNS = [
    # Whole header values: "Cookie: a=1; b=2", "Authorization: Bearer x" (up to end of line / quote)
    re.compile(r"(?i)\b(cookie|set-cookie|authorization|proxy-authorization)(\s*[:=]\s*)([^\r\n\"']+)"),
    re.compile(
        r"(?i)(cookie|set-cookie|authorization|x-houseagent-token|password|passwd|pwd|token|secret|"
        r"api[_-]?key|session[_-]?id|captcha)(\s*[:=]\s*|\"\s*:\s*\")([^\s,;\"']+)"
    ),
    re.compile(r"(?i)(bearer\s+)([A-Za-z0-9._\-]+)"),
    re.compile(r"sk-ant-[A-Za-z0-9_\-]+"),
]


def redact(text: str) -> str:
    text = _PATTERNS[0].sub(lambda m: f"{m.group(1)}{m.group(2)}***", text)
    text = _PATTERNS[1].sub(lambda m: f"{m.group(1)}{m.group(2)}***", text)
    text = _PATTERNS[2].sub(lambda m: f"{m.group(1)}***", text)
    text = _PATTERNS[3].sub("***", text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # pragma: no cover - malformed record
            return True
        record.msg = redact(msg)
        record.args = ()
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        return True


_configured = False


def configure_logging(logs_dir: Path, level: str = "INFO") -> None:
    global _configured
    root = logging.getLogger()
    root.setLevel(level.upper())
    if _configured:
        return
    logs_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    fh = RotatingFileHandler(logs_dir / "houseagent.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    fh.setFormatter(fmt)
    fh.addFilter(RedactingFilter())
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    sh.addFilter(RedactingFilter())
    root.addHandler(fh)
    root.addHandler(sh)
    _configured = True
