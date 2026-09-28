"""Operating-system protected secret storage (spec 4.7.1 / 15.15, NFR-11).

Business code only sees ``SecretStore``: write / read / delete by an opaque reference. The database keeps the
reference and the last four characters, never the value.

* Windows: DPAPI (current-user scope); the ciphertext lives in ``config/secrets/<ref>.dpapi``, which is outside
  the database, backups and exports.
* macOS: the login Keychain via ``/usr/bin/security`` (V1.1 target; not exercised by the Windows test suite).
* Development: ``HOUSEAGENT_SECRET_<REF>`` environment variables are read (never written) so developers can
  work without storing a key; they must not be committed to Git.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from abc import ABC, abstractmethod
from pathlib import Path

from houseagent.config import get_settings

_REF = re.compile(r"^[a-z0-9_-]{1,64}$")


def _check(ref: str) -> str:
    if not _REF.match(ref):
        raise ValueError("invalid secret reference")
    return ref


def env_name(ref: str) -> str:
    return "HOUSEAGENT_SECRET_" + re.sub(r"[^A-Z0-9]", "_", ref.upper())


class SecretStore(ABC):
    name: str

    @abstractmethod
    def available(self) -> bool: ...

    @abstractmethod
    def write(self, ref: str, value: str) -> None: ...

    @abstractmethod
    def read(self, ref: str) -> str | None: ...

    @abstractmethod
    def delete(self, ref: str) -> None: ...

    def has(self, ref: str) -> bool:
        return self.read(ref) is not None


class DpapiSecretStore(SecretStore):
    name = "windows-dpapi"

    def _file(self, ref: str) -> Path:
        d = get_settings().config_dir / "secrets"
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{_check(ref)}.dpapi"

    def available(self) -> bool:
        from houseagent.ai import dpapi

        return dpapi.available()

    def write(self, ref: str, value: str) -> None:
        from houseagent.ai import dpapi

        self._file(ref).write_text(dpapi.protect(value), encoding="ascii")

    def read(self, ref: str) -> str | None:
        from houseagent.ai import dpapi

        f = self._file(ref)
        if not f.exists():
            return None
        try:
            return dpapi.unprotect(f.read_text(encoding="ascii"))
        except Exception:  # noqa: BLE001 - another user / machine: treat as "needs to be entered again"
            return None

    def delete(self, ref: str) -> None:
        self._file(ref).unlink(missing_ok=True)

    def has(self, ref: str) -> bool:
        return self._file(ref).exists()


class KeychainSecretStore(SecretStore):
    name = "macos-keychain"
    SERVICE = "HouseAgent"

    def available(self) -> bool:
        return Path("/usr/bin/security").exists()

    def write(self, ref: str, value: str) -> None:
        subprocess.run(
            ["/usr/bin/security", "add-generic-password", "-U", "-s", self.SERVICE, "-a", _check(ref), "-w", value],
            check=True,
            capture_output=True,
        )

    def read(self, ref: str) -> str | None:
        r = subprocess.run(
            ["/usr/bin/security", "find-generic-password", "-s", self.SERVICE, "-a", _check(ref), "-w"],
            capture_output=True,
            text=True,
        )
        return r.stdout.rstrip("\n") if r.returncode == 0 else None

    def delete(self, ref: str) -> None:
        subprocess.run(
            ["/usr/bin/security", "delete-generic-password", "-s", self.SERVICE, "-a", _check(ref)],
            capture_output=True,
        )


class UnavailableSecretStore(SecretStore):
    name = "unavailable"

    def available(self) -> bool:
        return False

    def write(self, ref: str, value: str) -> None:
        raise RuntimeError("no protected secret storage on this platform")

    def read(self, ref: str) -> str | None:
        return None

    def delete(self, ref: str) -> None:
        return None


_store: SecretStore | None = None


def store() -> SecretStore:
    global _store
    if _store is None:
        if os.name == "nt":
            _store = DpapiSecretStore()
        elif sys.platform == "darwin":
            _store = KeychainSecretStore()
        else:
            _store = UnavailableSecretStore()
    return _store


def set_store(s: SecretStore | None) -> None:
    """Tests plug in another store."""
    global _store
    _store = s


def read_with_env(ref: str) -> tuple[str | None, str]:
    """(value, source): the protected store first, then the development environment variable."""
    value = store().read(ref)
    if value:
        return value, "store"
    env = os.environ.get(env_name(ref))
    return (env, "env") if env else (None, "none")
