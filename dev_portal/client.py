"""Exact, bounded Portal API client. No HTTP redirect ever forwards the bearer key."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import http.client
import json
import re
import time
from typing import Mapping, Protocol
from urllib.parse import quote, urlsplit

from .config import validate_hub_url

MAX_JSON = 1_048_576
MAX_BUILDS = 128
MAX_BUILD_BYTES = 256 * 1024**3
_BUILD_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
# Win32 also treats superscript ¹²³ as device-number digits in COM/LPT names.
_RESERVED = re.compile(r"(?i)(con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³])(?:\..*)?\Z")


class PortalError(Exception):
    """Only constant, bounded, credential-free messages are raised."""


def _text(value: object, label: str, maximum: int = 128) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise PortalError(f"Invalid {label} in Portal response")
    return value


def safe_filename(value: object) -> str:
    name = _text(value, "filename", 200)
    if (
        name in (".", "..") or name.startswith(".") or name.endswith((".", " "))
        or any(c in name for c in '/\\:<>"|?*') or _RESERVED.fullmatch(name)
    ):
        raise PortalError("Invalid filename in Portal response")
    return name


def _size(value: object) -> int:
    if type(value) is not int or value <= 0 or value > MAX_BUILD_BYTES:
        raise PortalError("Invalid build size in Portal response")
    return value


@dataclass(frozen=True)
class Build:
    build_id: str
    project_ref: str
    filename: str
    platform: str
    channel: str
    size: int
    sha256: str

    @classmethod
    def parse(cls, data: object) -> "Build":
        if not isinstance(data, dict) or set(data) != set(cls.__dataclass_fields__):
            raise PortalError("Invalid build metadata in Portal response")
        build_id = _text(data["build_id"], "build ID")
        if not _BUILD_ID.fullmatch(build_id):
            raise PortalError("Invalid build ID in Portal response")
        digest = _text(data["sha256"], "SHA-256", 64)
        if not _DIGEST.fullmatch(digest):
            raise PortalError("Invalid SHA-256 in Portal response")
        return cls(
            build_id, _text(data["project_ref"], "project reference"),
            safe_filename(data["filename"]), _text(data["platform"], "platform"),
            _text(data["channel"], "channel"), _size(data["size"]), digest,
        )

    def public(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Handoff:
    build_id: str
    fs_id: int
    remote_path: str
    dlink: str = field(repr=False)
    access_token: str = field(repr=False)
    filename: str = ""
    size: int = 0
    sha256: str = ""
    platform: str = ""

    @classmethod
    def parse(cls, data: object, expected: Build) -> "Handoff":
        if not isinstance(data, dict) or set(data) != set(cls.__dataclass_fields__):
            raise PortalError("Invalid download handoff")
        fs_id = data["fs_id"]
        if type(fs_id) is not int or not 0 < fs_id < 2**63:
            raise PortalError("Invalid download handoff")
        remote_path = _text(data["remote_path"], "remote path", 1024)
        if not remote_path.startswith("/") or any(c in remote_path for c in "?#\\"):
            raise PortalError("Invalid download handoff")
        token = _text(data["access_token"], "access token", 4096)
        dlink = _text(data["dlink"], "download link", 16_384)
        if not all(
            data[name] == getattr(expected, name)
            for name in ("build_id", "filename", "size", "sha256", "platform")
        ):
            raise PortalError("Download handoff disagrees with catalog")
        # Validation of the secret URL's host and redirects occurs at the transfer boundary.
        return cls(expected.build_id, fs_id, remote_path, dlink, token,
                   expected.filename, expected.size, expected.sha256, expected.platform)


class Response(Protocol):
    status: int
    def getheader(self, name: str) -> str | None: ...
    def read(self, size: int = -1) -> bytes: ...
    def close(self) -> None: ...


class Transport(Protocol):
    def open(self, method: str, url: str, headers: Mapping[str, str], *, timeout: float) -> Response: ...


class _Response:
    def __init__(self, response: http.client.HTTPResponse, connection: http.client.HTTPConnection):
        self._response = response
        self._connection = connection
        self.status = response.status

    def getheader(self, name: str) -> str | None:
        return self._response.getheader(name)

    def read(self, size: int = -1) -> bytes:
        return self._response.read(size)

    def close(self) -> None:
        try:
            self._response.close()
        finally:
            self._connection.close()


class HTTPTransport:
    """Raw stdlib HTTP: redirects are *never* automatic, TLS verifies certificates."""

    def open(self, method: str, url: str, headers: Mapping[str, str], *, timeout: float) -> Response:
        parsed = urlsplit(url)
        connection_type = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
        conn = connection_type(parsed.hostname, port=parsed.port, timeout=timeout)
        try:
            path = parsed.path or "/"
            if parsed.query:
                path += "?" + parsed.query
            conn.request(method, path, headers=dict(headers), body=b"" if method == "POST" else None)
            return _Response(conn.getresponse(), conn)
        except Exception:
            conn.close()
            raise


class HubClient:
    def __init__(self, hub_url: str, portal_key: str, transport: Transport | None = None):
        self.origin = validate_hub_url(hub_url)
        if not isinstance(portal_key, str) or not 8 <= len(portal_key) <= 4096 or any(
            ord(c) < 33 or ord(c) > 126 for c in portal_key
        ):
            raise PortalError("Invalid Portal key")
        self._key = portal_key
        self._transport = transport or HTTPTransport()

    def _json(self, method: str, path: str) -> object:
        response = None
        try:
            headers = {"Authorization": "Bearer " + self._key, "Accept": "application/json"}
            if method == "POST":
                headers["Content-Length"] = "0"
            response = self._transport.open(method, self.origin + path, headers, timeout=10)
            if response.status != 200:
                raise PortalError("DevHub declined the Portal request")
            content_type = response.getheader("Content-Type") or ""
            if not content_type.lower().startswith("application/json"):
                raise PortalError("DevHub did not return JSON")
            length = response.getheader("Content-Length")
            if length is not None and (not length.isdigit() or int(length) > MAX_JSON):
                raise PortalError("DevHub response is too large")
            deadline = time.monotonic() + 30
            raw = bytearray()
            # One byte per read makes a slowly dripping peer unable to hold one
            # large blocking read indefinitely; the JSON cap bounds the work.
            while len(raw) <= MAX_JSON:
                if time.monotonic() > deadline:
                    raise PortalError("DevHub response timed out")
                byte = response.read(1)
                if not byte:
                    break
                if len(byte) != 1:
                    raise PortalError("Invalid DevHub response")
                raw.extend(byte)
            if len(raw) > MAX_JSON:
                raise PortalError("DevHub response is too large")
            if length is not None and len(raw) != int(length):
                raise PortalError("Invalid DevHub response length")
            return json.loads(raw)
        except PortalError:
            raise
        except Exception:
            # Never render/log upstream exceptions: socket errors can contain bearer URLs.
            raise PortalError("DevHub request failed") from None
        finally:
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass  # Never expose a URL-containing transport exception.

    def builds(self) -> list[Build]:
        data = self._json("GET", "/portal/v1/builds")
        if not isinstance(data, dict) or set(data) != {"builds"} or not isinstance(data["builds"], list) or len(data["builds"]) > MAX_BUILDS:
            raise PortalError("Invalid build catalog")
        result = [Build.parse(item) for item in data["builds"]]
        if len({item.build_id for item in result}) != len(result):
            raise PortalError("Duplicate build ID in catalog")
        return result

    def handoff(self, build: Build) -> Handoff:
        if not _BUILD_ID.fullmatch(build.build_id):
            raise PortalError("Invalid build ID")
        data = self._json("POST", "/portal/v1/builds/" + quote(build.build_id, safe="") + "/download")
        return Handoff.parse(data, build)
