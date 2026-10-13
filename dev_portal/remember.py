"""Explicit, per-user remembered pairing; Windows bearer bytes are DPAPI-protected.

This is separate from the legacy CLI config.json, which is never migrated or read
as a pairing. On non-Windows source installs, private owner-only file permissions
provide the same boundary as the existing CLI configuration.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import stat
import sys
import tempfile

from .pairing import Pairing, PairingError, decode_pairing, encode_pairing


class RememberError(Exception):
    """Only fixed, credential-free messages are exposed to the caller."""


class _Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def _windows_crypt(data: bytes, *, decrypt: bool) -> bytes:
    """Protect only for the current Windows account; never fall back to plaintext."""
    if sys.platform != "win32":
        raise RememberError("Windows account protection unavailable")
    try:
        crypt = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        func = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
        func.restype = wintypes.BOOL
        if decrypt:
            func.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.POINTER(_Blob),
                             ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
        else:
            func.argtypes = [ctypes.POINTER(_Blob), wintypes.LPCWSTR, ctypes.POINTER(_Blob),
                             ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        raw = ctypes.create_string_buffer(data)
        source = _Blob(len(data), ctypes.cast(raw, ctypes.POINTER(ctypes.c_ubyte)))
        target = _Blob()
        if not func(ctypes.byref(source), None, None, None, None, 0x1, ctypes.byref(target)):
            raise RememberError("Windows account protection failed")
        try:
            if not target.data or not 0 < target.size <= 16384:
                raise RememberError("Invalid protected login")
            return ctypes.string_at(target.data, target.size)
        finally:
            kernel.LocalFree(ctypes.cast(target.data, ctypes.c_void_p))
    except RememberError:
        raise
    except (OSError, AttributeError, ValueError):
        raise RememberError("Windows account protection failed") from None


def remembered_path() -> Path:
    if sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return root / "GDUEDevPortal" / "pairing.dat"
    return Path.home() / ".config" / "gdue-dev-portal" / "pairing.dat"


def _directory(path: Path, *, create: bool) -> None:
    if create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        info = path.lstat()
    except FileNotFoundError:
        if not create:
            return
        raise RememberError("Private login directory unavailable") from None
    if not stat.S_ISDIR(info.st_mode) or path.is_symlink():
        raise RememberError("Private login directory invalid")
    if sys.platform != "win32" and (info.st_uid != os.getuid() or info.st_mode & 0o077):
        raise RememberError("Private login directory invalid")


def _existing(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or path.is_symlink():
        raise RememberError("Saved login file invalid")
    if sys.platform != "win32" and (info.st_uid != os.getuid() or info.st_mode & 0o077):
        raise RememberError("Saved login file invalid")
    return True


def load_remembered(path: Path | None = None) -> Pairing | None:
    target = path or remembered_path()
    _directory(target.parent, create=False)
    if not _existing(target):
        return None
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(target, flags)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 16384:
                raise RememberError("Saved login file invalid")
            raw = stream.read(16385)
        if len(raw) > 16384:
            raise RememberError("Saved login file invalid")
        if sys.platform == "win32":
            if not raw.startswith(b"DPW1"):
                raise RememberError("Saved login file invalid")
            raw = _windows_crypt(raw[4:], decrypt=True)
        elif not raw.startswith(b"DPX1"):
            raise RememberError("Saved login file invalid")
        else:
            raw = raw[4:]
        return decode_pairing(raw.decode("ascii"))
    except (OSError, UnicodeError, PairingError):
        raise RememberError("Saved login unavailable; contact the operator") from None


def save_remembered(pairing: Pairing, path: Path | None = None) -> None:
    target = path or remembered_path()
    _directory(target.parent, create=True)
    _existing(target)  # Refuse an unsafe pre-existing target.
    try:
        raw = encode_pairing(pairing).encode("ascii")
        raw = (b"DPW1" + _windows_crypt(raw, decrypt=False) if sys.platform == "win32"
               else b"DPX1" + raw)
        if len(raw) > 16384:
            raise RememberError("Saved login too large")
        fd, staged = tempfile.mkstemp(prefix=".pairing-", dir=target.parent)
        try:
            if sys.platform != "win32":
                os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as stream:
                fd = -1  # fdopen owns the descriptor, including on write failure.
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(staged, target)
        finally:
            if fd >= 0:
                os.close(fd)
            if os.path.lexists(staged):
                os.unlink(staged)
    except (OSError, PairingError):
        raise RememberError("Cannot save protected login") from None


def forget_remembered(path: Path | None = None) -> None:
    target = path or remembered_path()
    _directory(target.parent, create=False)
    if _existing(target):
        try:
            target.unlink()
        except OSError:
            raise RememberError("Cannot forget saved login") from None
