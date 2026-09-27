"""Application configuration and data-directory layout.

Program files and user data are kept apart (spec 10.2 / 15.10): everything the user owns lives under
``%LOCALAPPDATA%\\HouseAgent`` (overridable with ``HOUSEAGENT_DATA_DIR``) so upgrades never touch it.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path


def _default_data_dir() -> Path:
    override = os.environ.get("HOUSEAGENT_DATA_DIR")
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "HouseAgent"


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def program_dir() -> Path:
    """Directory holding bundled program resources (frontend build, alembic scripts)."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


@dataclass
class Settings:
    env: str = field(default_factory=lambda: os.environ.get("HOUSEAGENT_ENV", "production"))
    data_dir: Path = field(default_factory=_default_data_dir)
    host: str = "127.0.0.1"
    port: int = field(default_factory=lambda: int(os.environ.get("HOUSEAGENT_PORT", "8765")))
    # Start the in-process scheduler / queue workers. Tests switch this off and drive the queue directly.
    start_background: bool = field(default_factory=lambda: _bool_env("HOUSEAGENT_BACKGROUND", True))
    # Browser engine for adapters: "playwright" (real browser) or "http" (headless HTTP, mock site / tests).
    browser_engine: str = field(default_factory=lambda: os.environ.get("HOUSEAGENT_BROWSER_ENGINE", "playwright"))
    # Playwright channel; "msedge" uses the Edge already installed on Windows so no download is needed.
    browser_channel: str = field(default_factory=lambda: os.environ.get("HOUSEAGENT_BROWSER_CHANNEL", "msedge"))
    # Retry delays in seconds for NETWORK_ERROR / PAGE_TIMEOUT (spec 15.5: 1 min and 5 min).
    retry_delays: tuple[int, ...] = (60, 300)
    page_timeout_s: float = field(default_factory=lambda: float(os.environ.get("HOUSEAGENT_PAGE_TIMEOUT", "30")))
    # Enable the built-in mock property site at /mock-site (formal V1.0 acceptance target).
    enable_mock_site: bool = field(default_factory=lambda: _bool_env("HOUSEAGENT_MOCK_SITE", True))
    # Fixed session token (tests / dev). Production generates a random token per launch.
    session_token: str | None = field(default_factory=lambda: os.environ.get("HOUSEAGENT_SESSION_TOKEN"))

    @property
    def is_dev(self) -> bool:
        return self.env in {"development", "test"}

    @property
    def database_dir(self) -> Path:
        return self.data_dir / "database"

    @property
    def database_path(self) -> Path:
        return self.database_dir / "houseagent.db"

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.database_path.as_posix()}"

    @property
    def profiles_dir(self) -> Path:
        return self.data_dir / "browser_profiles"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def config_dir(self) -> Path:
        return self.data_dir / "config"

    @property
    def exports_dir(self) -> Path:
        return self.data_dir / "exports"

    @property
    def runtime_file(self) -> Path:
        """Per-launch runtime info (port, pid, token) used by the launcher and the Vite dev proxy."""
        return self.config_dir / "runtime.json"

    @property
    def frontend_dist(self) -> Path:
        env = os.environ.get("HOUSEAGENT_FRONTEND_DIST")
        if env:
            return Path(env)
        bundled = program_dir() / "frontend_dist"
        if bundled.exists():
            return bundled
        return program_dir().parent / "frontend" / "dist"

    def ensure_dirs(self) -> None:
        for d in (
            self.database_dir,
            self.profiles_dir,
            self.logs_dir,
            self.backups_dir,
            self.config_dir,
            self.exports_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def set_settings(settings: Settings) -> None:
    global _settings
    _settings = settings
