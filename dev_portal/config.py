"""Owner-local Portal configuration; provider handoff secrets never reach disk."""

from __future__ import annotations

import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from urllib.parse import urlsplit


class ConfigError(Exception):
    """A bounded configuration error without credential values."""


def validate_hub_url(value: str) -> str:
    """Accept an origin only: HTTPS, or numeric loopback HTTP (no credentials)."""
    if not isinstance(value, str) or len(value) > 2048 or any(
        ord(c) < 33 or ord(c) == 127 or c == "\\" for c in value
    ):
        raise ConfigError("DevHub URL must be a valid HTTPS or loopback HTTP origin")
    try:
        url = urlsplit(value)
        host = url.hostname
        port = url.port
    except ValueError as exc:
        raise ConfigError("Invalid DevHub URL") from None
    if (
        url.scheme not in ("https", "http")
        or not host
        or url.username is not None
        or url.password is not None
        or url.path not in ("", "/")
        or url.query
        or url.fragment
        or port == 0
    ):
        raise ConfigError("DevHub URL must be an origin without a path or credentials")
    if url.netloc.startswith("["):
        try:
            ipaddress.IPv6Address(host)
        except ValueError:
            raise ConfigError("Invalid DevHub host") from None
    elif not re.fullmatch(r"[A-Za-z0-9.-]+", host) or host.startswith("-") or host.endswith((".", "-")):
        raise ConfigError("Invalid DevHub host")
    if url.scheme == "http":
        try:
            if not ipaddress.ip_address(host).is_loopback:
                raise ConfigError("Plain HTTP requires a numeric loopback address")
        except ValueError:
            raise ConfigError("Plain HTTP requires a numeric loopback address") from None
    return value.rstrip("/")


def config_path() -> Path:
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "GDUEDevPortal" / "config.json"
    return Path.home() / ".config" / "gdue-dev-portal" / "config.json"


def _private_directory(path: Path, create: bool = False) -> None:
    if create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        info = path.lstat()
    except FileNotFoundError:
        raise ConfigError("Configure DevPortal before starting it") from None
    if not stat.S_ISDIR(info.st_mode) or (os.name != "nt" and (info.st_uid != os.getuid() or info.st_mode & 0o077)):
        raise ConfigError("Configuration directory must be private and not a symlink")


def load_config(path: Path | None = None) -> tuple[str, str]:
    target = path or config_path()
    _private_directory(target.parent)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(target, flags)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or (
                os.name != "nt" and (info.st_uid != os.getuid() or info.st_mode & 0o077)
            ):
                raise ConfigError("Configuration file must be private")
            raw = stream.read(8193)
    except FileNotFoundError:
        raise ConfigError("Configure DevPortal before starting it") from None
    except OSError:
        raise ConfigError("Cannot read private configuration") from None
    if len(raw) > 8192:
        raise ConfigError("Configuration is too large")
    try:
        data = json.loads(raw)
    except (UnicodeError, ValueError, TypeError):
        raise ConfigError("Invalid configuration") from None
    if not isinstance(data, dict) or set(data) != {"hub_url", "portal_key"}:
        raise ConfigError("Invalid configuration fields")
    hub = validate_hub_url(data["hub_url"])
    key = data["portal_key"]
    if not isinstance(key, str) or not 8 <= len(key) <= 4096 or any(ord(c) < 33 or ord(c) > 126 for c in key):
        raise ConfigError("Invalid Portal key")
    return hub, key


def save_config(hub_url: str, portal_key: str, path: Path | None = None) -> None:
    hub = validate_hub_url(hub_url)
    if not isinstance(portal_key, str) or not 8 <= len(portal_key) <= 4096 or any(
        ord(c) < 33 or ord(c) > 126 for c in portal_key
    ):
        raise ConfigError("Invalid Portal key")
    target = path or config_path()
    _private_directory(target.parent, create=True)
    if target.exists() or target.is_symlink():
        load_config(target)  # Refuse to replace an unsafe existing file.
    staged = None
    try:
        fd, staged = tempfile.mkstemp(prefix=".config-", dir=target.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            if os.name != "nt":
                os.fchmod(stream.fileno(), 0o600)
            json.dump({"hub_url": hub, "portal_key": portal_key}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(staged, target)
        staged = None
        if os.name != "nt":
            dir_fd = os.open(target.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
    finally:
        if staged is not None:
            Path(staged).unlink(missing_ok=True)
