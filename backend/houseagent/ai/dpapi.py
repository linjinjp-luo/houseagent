"""Windows DPAPI protection for application secrets (spec 15.6). Never written as plain text."""

from __future__ import annotations

import base64
import ctypes
import os
from ctypes import wintypes


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _to_blob(data: bytes) -> _Blob:
    buf = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))


def _from_blob(blob: _Blob) -> bytes:
    out = ctypes.string_at(blob.pbData, blob.cbData)
    ctypes.windll.kernel32.LocalFree(blob.pbData)  # type: ignore[attr-defined]
    return out


def available() -> bool:
    return os.name == "nt"


def protect(plain: str) -> str:
    if not available():
        raise RuntimeError("DPAPI is only available on Windows")
    src, dst = _to_blob(plain.encode("utf-8")), _Blob()
    if not ctypes.windll.crypt32.CryptProtectData(  # type: ignore[attr-defined]
        ctypes.byref(src), "HouseAgent", None, None, None, 0x1, ctypes.byref(dst)
    ):
        raise OSError("CryptProtectData failed")
    return base64.b64encode(_from_blob(dst)).decode("ascii")


def unprotect(token: str) -> str:
    if not available():
        raise RuntimeError("DPAPI is only available on Windows")
    src, dst = _to_blob(base64.b64decode(token)), _Blob()
    if not ctypes.windll.crypt32.CryptUnprotectData(  # type: ignore[attr-defined]
        ctypes.byref(src), None, None, None, None, 0x1, ctypes.byref(dst)
    ):
        raise OSError("CryptUnprotectData failed")
    return _from_blob(dst).decode("utf-8")
