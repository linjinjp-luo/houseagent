"""Browser sessions used by adapters.

``PlaywrightSession`` drives a real browser with a persistent, per-account profile (default; uses the Edge
installed on Windows so no browser download is required). ``HttpSession`` is a lightweight engine for the
built-in mock site and automated tests; it keeps its cookie jar inside the same isolated profile directory.
Neither exposes cookies, passwords or page internals to callers - only page text and status.
"""

from __future__ import annotations

import json
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx

from houseagent.errors import AdapterError, ErrorCode

log = logging.getLogger(__name__)


@dataclass
class PageResult:
    url: str
    status: int
    text: str


class BrowserSession(ABC):
    def __init__(self, profile_dir: Path, base_origin: str, timeout_s: float) -> None:
        self.profile_dir = profile_dir
        self.base_origin = base_origin
        self.timeout_s = timeout_s

    def resolve(self, url: str) -> str:
        return urljoin(self.base_origin + "/", url)

    @abstractmethod
    def goto(self, url: str) -> PageResult: ...

    @abstractmethod
    def close(self) -> None: ...

    def __enter__(self) -> BrowserSession:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


class HttpSession(BrowserSession):
    COOKIE_FILE = "http_engine_cookies.json"

    def __init__(self, profile_dir: Path, base_origin: str, timeout_s: float) -> None:
        super().__init__(profile_dir, base_origin, timeout_s)
        self._client = httpx.Client(
            timeout=timeout_s, follow_redirects=True, headers={"User-Agent": "HouseAgent/1.0 (+local)"}
        )
        self._load_cookies()

    def _cookie_path(self) -> Path:
        return self.profile_dir / self.COOKIE_FILE

    def _load_cookies(self) -> None:
        p = self._cookie_path()
        if p.exists():
            try:
                for c in json.loads(p.read_text(encoding="utf-8")):
                    self._client.cookies.set(c["name"], c["value"], domain=c.get("domain", ""), path=c.get("path", "/"))
            except Exception:
                log.warning("http engine cookie jar unreadable; starting logged out")

    def _save_cookies(self) -> None:
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        data = [
            {"name": c.name, "value": c.value, "domain": c.domain, "path": c.path} for c in self._client.cookies.jar
        ]
        self._cookie_path().write_text(json.dumps(data), encoding="utf-8")

    def goto(self, url: str) -> PageResult:
        target = self.resolve(url)
        try:
            r = self._client.get(target)
        except httpx.TimeoutException as exc:
            raise AdapterError(ErrorCode.PAGE_TIMEOUT, "page timeout") from exc
        except httpx.TransportError as exc:
            raise AdapterError(ErrorCode.NETWORK_ERROR, type(exc).__name__) from exc
        self._save_cookies()
        return PageResult(url=str(r.url), status=r.status_code, text=r.text)

    def post_form(self, url: str, data: dict[str, str]) -> PageResult:
        """Only used by tests to act as the *user* submitting the mock login form."""
        r = self._client.post(self.resolve(url), data=data)
        self._save_cookies()
        return PageResult(url=str(r.url), status=r.status_code, text=r.text)

    def close(self) -> None:
        self._client.close()


class PlaywrightSession(BrowserSession):
    def __init__(self, profile_dir: Path, base_origin: str, timeout_s: float, *, headless: bool, channel: str) -> None:
        super().__init__(profile_dir, base_origin, timeout_s)
        self._pw: Any = None
        try:
            from playwright.sync_api import sync_playwright

            self._pw = sync_playwright().start()
            kwargs: dict[str, Any] = {"headless": headless}
            if channel and channel != "chromium":
                kwargs["channel"] = channel
            self._ctx = self._pw.chromium.launch_persistent_context(str(profile_dir), **kwargs)
            self._ctx.set_default_timeout(timeout_s * 1000)
        except Exception as exc:
            if self._pw is not None:
                try:
                    self._pw.stop()
                except Exception:
                    pass
            self._pw = None
            raise AdapterError(ErrorCode.BROWSER_START_FAILED, type(exc).__name__) from exc

    def _page(self) -> Any:
        return self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()

    def goto(self, url: str) -> PageResult:
        from playwright.sync_api import Error as PwError
        from playwright.sync_api import TimeoutError as PwTimeout

        page = self._page()
        try:
            resp = page.goto(self.resolve(url), wait_until="domcontentloaded")
            status = resp.status if resp else 200
            return PageResult(url=page.url, status=status, text=page.content())
        except PwTimeout as exc:
            raise AdapterError(ErrorCode.PAGE_TIMEOUT, "page timeout") from exc
        except PwError as exc:
            msg = str(exc)
            if "net::" in msg:
                raise AdapterError(ErrorCode.NETWORK_ERROR, "network") from exc
            raise AdapterError(ErrorCode.UNKNOWN_ERROR, "browser error") from exc

    def wait_until_closed(self, max_wait_s: float, stop_event: Any = None) -> None:
        """Block until the user closes the login window, the app asks to close it, or the wait expires."""
        deadline = time.monotonic() + max_wait_s
        while time.monotonic() < deadline:
            if stop_event is not None and stop_event.is_set():
                return
            try:
                if not self._ctx.pages:
                    return
                self._ctx.pages[0].wait_for_timeout(1000)
            except Exception:
                return

    def close(self) -> None:
        try:
            self._ctx.close()
        except Exception:
            pass
        if self._pw is not None:
            try:
                self._pw.stop()
            except Exception:
                pass


def open_session(profile_dir: Path, base_origin: str, timeout_s: float, *, headless: bool = True) -> BrowserSession:
    from houseagent.config import get_settings

    s = get_settings()
    profile_dir.mkdir(parents=True, exist_ok=True)
    if s.browser_engine == "http":
        return HttpSession(profile_dir, base_origin, timeout_s)
    return PlaywrightSession(profile_dir, base_origin, timeout_s, headless=headless, channel=s.browser_channel)
