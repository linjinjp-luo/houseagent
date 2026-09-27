"""Windows launcher (spec 10.1 / 15.10).

* Single instance: a second launch only opens the running instance's page.
* Port: the configured port (8765) if free, otherwise any free loopback port; recorded in runtime.json.
* Opens http://127.0.0.1:<port> in the default browser, optionally shows a tray icon (Open / Quit).
* Normal exit stops accepting new runs and safely cancels running ones before the server stops.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import IO, Any

import httpx
import uvicorn

from houseagent.config import Settings, get_settings, set_settings
from houseagent.logging_setup import configure_logging
from houseagent.runtime import state as runtime

log = logging.getLogger("houseagent.launcher")


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def choose_port(preferred: int) -> int:
    if _port_free(preferred):
        return preferred
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class InstanceLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._fh: IO[str] | None = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.path, "a+")  # noqa: SIM115 - held for the process lifetime
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)  # type: ignore[attr-defined]
        except OSError:
            fh.close()
            return False
        self._fh = fh
        return True

    def release(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None


def _existing_instance_url(settings: Settings) -> str | None:
    try:
        info = json.loads(settings.runtime_file.read_text(encoding="utf-8"))
        url = f"http://127.0.0.1:{info['port']}"
        r = httpx.get(url + "/api/v1/health", timeout=3)
        if r.status_code == 200:
            return url
    except Exception:
        return None
    return None


def write_runtime(settings: Settings) -> None:
    settings.config_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "port": runtime.port,
        "pid": os.getpid(),
        "token": runtime.token,
        "started_at": runtime.started_at.isoformat(),
    }
    settings.runtime_file.write_text(json.dumps(data), encoding="utf-8")
    try:
        from houseagent.browser_worker.profiles import restrict_to_current_user

        restrict_to_current_user(settings.config_dir)
    except Exception:
        pass


def _wait_ready(url: str, timeout_s: float = 30) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            if httpx.get(url + "/api/v1/health", timeout=2).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(0.3)
    return False


def _tray(url: str, server: uvicorn.Server) -> Any:
    try:
        import pystray
        from PIL import Image, ImageDraw
    except Exception:
        return None
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.polygon([(32, 6), (60, 30), (52, 30), (52, 58), (12, 58), (12, 30), (4, 30)], fill=(37, 99, 235, 255))
    d.rectangle([26, 38, 38, 58], fill=(255, 255, 255, 255))

    def _open(_i: Any = None, _it: Any = None) -> None:
        webbrowser.open(url)

    def _quit(icon: Any, _it: Any = None) -> None:
        server.should_exit = True
        icon.stop()

    icon = pystray.Icon(
        "HouseAgent",
        img,
        "HouseAgent",
        menu=pystray.Menu(pystray.MenuItem("Open / 開く", _open, default=True), pystray.MenuItem("Quit / 終了", _quit)),
    )
    threading.Thread(target=icon.run, name="tray", daemon=True).start()
    return icon


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="HouseAgent")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--no-tray", action="store_true")
    parser.add_argument("--port", type=int)
    parser.add_argument("--dev", action="store_true", help="development mode (enables API docs, Vite origin)")
    args = parser.parse_args(argv)

    if args.dev:
        os.environ["HOUSEAGENT_ENV"] = "development"
    settings = Settings()
    if args.port:
        settings.port = args.port
    set_settings(settings)
    settings.ensure_dirs()
    configure_logging(settings.logs_dir)

    lock = InstanceLock(settings.config_dir / "houseagent.lock")
    if not lock.acquire():
        url = _existing_instance_url(settings)
        if url:
            webbrowser.open(url)
            return 0
        log.error("another HouseAgent instance holds the lock but is not responding")
        return 1

    runtime.port = choose_port(settings.port)
    if settings.session_token:
        runtime.token = settings.session_token
    write_runtime(settings)
    url = runtime.origin

    from houseagent.main import create_app

    app = create_app(settings)
    config = uvicorn.Config(app, host="127.0.0.1", port=runtime.port, log_config=None, access_log=False)
    server = uvicorn.Server(config)

    def _request_shutdown() -> None:
        server.should_exit = True

    runtime.request_shutdown = _request_shutdown

    open_browser = not args.no_browser
    tray_enabled = not args.no_tray
    try:
        from houseagent.db import session as dbs
        from houseagent.services import settings_service

        # Settings are read after the server initialises the database; defaults apply on first launch.
        def _post_start() -> None:
            if not _wait_ready(url):
                return
            try:
                with dbs.session_scope() as db:
                    s = settings_service.get_all(db)
            except Exception:
                s = settings_service.DEFAULTS
            if open_browser and s.get("startup_open_browser", True):
                webbrowser.open(url)
            if tray_enabled and s.get("tray_enabled", True):
                _tray(url, server)

        threading.Thread(target=_post_start, name="post-start", daemon=True).start()
        server.run()
    finally:
        try:
            settings.runtime_file.unlink(missing_ok=True)
        except OSError:
            pass
        lock.release()
    return 0


if __name__ == "__main__":
    sys.exit(main())


def dev_server() -> None:
    """Entry used by scripts/dev.ps1: backend only, fixed port, API docs enabled."""
    os.environ.setdefault("HOUSEAGENT_ENV", "development")
    sys.exit(main(["--no-browser", "--no-tray", "--dev"] + sys.argv[1:]))


_ = get_settings  # re-exported for scripts
