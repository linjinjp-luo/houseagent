"""Test fixtures: a real HouseAgent server on a free loopback port with a temporary data directory.

The mock site is served by the same app; adapters use the HTTP engine so no browser is needed.
The run queue is driven synchronously via ``run_queue.run_until_idle()``.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn

from houseagent.config import Settings, set_settings
from houseagent.mock_site.app import STATES
from houseagent.runtime import state as runtime

TOKEN = "test-token"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class Api:
    def __init__(self, base: str) -> None:
        self.base = base
        self.c = httpx.Client(base_url=base, headers={"X-HouseAgent-Token": TOKEN}, timeout=30)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.c, name)

    def ok(self, method: str, url: str, **kw: Any) -> Any:
        r = self.c.request(method, "/api/v1" + url, **kw)
        assert r.status_code < 400, f"{method} {url} -> {r.status_code} {r.text}"
        return r.json()

    def err(self, method: str, url: str, **kw: Any) -> httpx.Response:
        r = self.c.request(method, "/api/v1" + url, **kw)
        assert r.status_code >= 400, f"expected failure: {method} {url} -> {r.status_code}"
        return r

    def mock(self, variant: str, action: str, **data: Any) -> Any:
        r = self.c.post(f"/mock-site/{variant}/admin/{action}", json=data, headers={"Accept": "application/json"})
        assert r.status_code < 400, r.text
        return r.json()

    def login_as_user(self, account: dict[str, Any], variant: str = "a") -> None:
        """Act as the *user* completing the login form in their own isolated profile."""
        from houseagent.browser_worker.session import HttpSession

        with HttpSession(Path(account["profile_path"]), self.base, 10) as s:
            r = s.post_form(f"/mock-site/{variant}/login", {"username": "user", "password": "pw"})
            assert r.status == 200


@pytest.fixture()
def server(tmp_path: Path) -> Iterator[Api]:
    port = _free_port()
    settings = Settings(
        env="test",
        data_dir=tmp_path / "data",
        port=port,
        start_background=False,
        browser_engine="http",
        retry_delays=(0, 0),
        page_timeout_s=2.0,
        session_token=TOKEN,
    )
    set_settings(settings)
    runtime.port = port
    runtime.token = TOKEN
    for st in STATES.values():
        st.reset()
    from houseagent.main import create_app

    app = create_app(settings)
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None, access_log=False))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    deadline = time.monotonic() + 20
    while not srv.started:
        assert time.monotonic() < deadline, "server did not start"
        time.sleep(0.05)
    api = Api(f"http://127.0.0.1:{port}")
    try:
        yield api
    finally:
        from houseagent.scheduler.queue import run_queue

        run_queue.run_until_idle(timeout_s=30)
        srv.should_exit = True
        t.join(timeout=10)


def drain() -> None:
    from houseagent.scheduler.queue import run_queue

    run_queue.run_until_idle(timeout_s=60)


@pytest.fixture()
def ready_account(server: Api) -> dict[str, Any]:
    acc = server.ok(
        "POST", "/accounts", json={"site_id": "mock_a", "account_alias": "個人A", "auto_search_enabled": True}
    )
    server.login_as_user(acc, "a")
    return acc


def make_task(
    api: Api,
    account_id: int | None,
    *,
    name: str = "武蔵浦和 中古",
    site_id: str = "mock_a",
    conditions: dict[str, Any] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    body = {
        "name": name,
        "conditions": conditions or {"prefectures": ["11"], "result_limit": 500},
        "sites": [{"site_id": site_id, "account_id": account_id}],
        "acknowledge_unsupported": True,
        **extra,
    }
    return api.ok("POST", "/search-tasks", json=body)
