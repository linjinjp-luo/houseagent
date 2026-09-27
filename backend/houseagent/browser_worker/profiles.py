"""Isolated browser profile directories - one per account alias (spec 4.2 / 15.6).

Cookies and caches live only here, never in the business database, and the directory is restricted to the
current Windows user.
"""

from __future__ import annotations

import getpass
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)


def _slug(text: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_-]+", "-", text).strip("-").lower()
    return s[:40] or "account"


def profile_dir_for(root: Path, site_id: str, account_id: int, alias: str) -> Path:
    return root / site_id / f"{account_id}-{_slug(alias)}"


def restrict_to_current_user(path: Path) -> None:
    """Remove inherited ACLs and grant full control only to the current user (Windows)."""
    if os.name != "nt":
        path.chmod(0o700)
        return
    user = os.environ.get("USERNAME") or getpass.getuser()
    domain = os.environ.get("USERDOMAIN")
    principal = f"{domain}\\{user}" if domain else user
    try:
        subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", f"{principal}:(OI)(CI)F"],
            check=True,
            capture_output=True,
            timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as exc:  # pragma: no cover - depends on host ACL tooling
        log.warning("could not restrict profile directory permissions: %s", type(exc).__name__)


def create_profile_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    restrict_to_current_user(path)


def clear_profile(path: Path) -> None:
    """Delete the stored session only. Tasks, listings, favorites and notes are untouched."""
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    create_profile_dir(path)
