"""Direct Baidu range transfer with strict identity, private checkpoints and atomic publication."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import stat
import tempfile
import time
from typing import Callable, Iterator
from urllib.parse import parse_qsl, quote, urljoin, urlsplit

from .client import Build, Handoff, HTTPTransport, PortalError, Transport, safe_filename

SEGMENT_BYTES = 4 * 1024 * 1024
IO_BYTES = 64 * 1024
MAX_REDIRECTS = 5
CONTENT_RANGE = re.compile(r"bytes ([0-9]+)-([0-9]+)/([0-9]+)\Z")
_ALLOWED_SUFFIXES = ("baidu.com", "baidupcs.com", "baidubce.com", "bdydns.com")


def _vetted_url(url: str) -> None:
    if not isinstance(url, str) or len(url) > 16_384 or any(ord(c) < 33 or ord(c) == 127 or c == "\\" for c in url):
        raise PortalError("Untrusted Baidu download link")
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        raise PortalError("Untrusted Baidu download link") from None
    if (
        parsed.scheme != "https" or not host or port not in (None, 443)
        or parsed.username is not None or parsed.password is not None or parsed.fragment
        or not re.fullmatch(r"[a-z0-9-]+(?:\.[a-z0-9-]+)*", host)
        or not any(host == suffix or host.endswith("." + suffix) for suffix in _ALLOWED_SUFFIXES)
    ):
        raise PortalError("Untrusted Baidu download link")


def _initial_url(dlink: str, token: str) -> str:
    _vetted_url(dlink)
    if any(key.lower() == "access_token" for key, _ in parse_qsl(urlsplit(dlink).query, keep_blank_values=True)):
        raise PortalError("Download link already has a token")
    url = dlink + ("&" if "?" in dlink else "?") + "access_token=" + quote(token, safe="")
    _vetted_url(url)
    return url


def _segment(transport: Transport, handoff: Handoff, start: int, length: int) -> bytes:
    end = start + length - 1
    url = _initial_url(handoff.dlink, handoff.access_token)
    headers = {
        "User-Agent": "pan.baidu.com", "Range": f"bytes={start}-{end}",
        "Accept-Encoding": "identity", "Connection": "close",
    }
    for redirects in range(MAX_REDIRECTS + 1):
        response = None
        try:
            _vetted_url(url)
            response = transport.open("GET", url, headers, timeout=15)
            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader("Location")
                if not isinstance(location, str) or not location or len(location) > 16_384 or redirects == MAX_REDIRECTS:
                    raise PortalError("Baidu redirect refused")
                following = urljoin(url, location)
                _vetted_url(following)
                url = following
                continue
            if response.status != 206:
                raise PortalError("Baidu did not return the requested byte range")
            mime = (response.getheader("Content-Type") or "").split(";", 1)[0].strip().lower()
            if mime == "application/json" or mime.startswith("text/"):
                raise PortalError("Baidu returned an error document, not package bytes")
            match = CONTENT_RANGE.fullmatch(response.getheader("Content-Range") or "")
            if not match or tuple(map(int, match.groups())) != (start, end, handoff.size):
                raise PortalError("Baidu returned a mismatched byte range")
            content_length = response.getheader("Content-Length")
            if content_length is None or not content_length.isdigit() or int(content_length) != length:
                raise PortalError("Baidu returned a mismatched byte count")
            if (response.getheader("Content-Encoding") or "identity").lower() != "identity":
                raise PortalError("Baidu returned encoded package bytes")
            deadline = time.monotonic() + 90
            parts = bytearray()
            while len(parts) < length:
                if time.monotonic() > deadline:
                    raise PortalError("Baidu transfer timed out")
                allowed = min(IO_BYTES, length - len(parts))
                piece = response.read(allowed)
                if not piece:
                    raise PortalError("Baidu ended a byte range early")
                if len(piece) > allowed:
                    raise PortalError("Baidu sent extra package bytes")
                parts.extend(piece)
            if response.read(1):
                raise PortalError("Baidu sent extra package bytes")
            return bytes(parts)
        except PortalError:
            raise
        except Exception:
            # Never expose an exception that may contain dlink/access_token.
            raise PortalError("Baidu transfer failed") from None
        finally:
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass  # Never surface an exception containing an authenticated URL.
    raise PortalError("Baidu redirect refused")


def _private_dir(path: Path, *, create: bool = False, writable: bool = False) -> None:
    if create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or (
        os.name != "nt" and (
            info.st_uid != os.getuid() or (info.st_mode & (0o022 if writable else 0o077))
        )
    ):
        raise PortalError("Download directory is not owner-controlled")


def _private_file(fd: int) -> None:
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or (
        os.name != "nt" and (info.st_uid != os.getuid() or info.st_mode & 0o077)
    ):
        raise PortalError("Unsafe checkpoint or partial file")


def _open_private(path: Path, flags: int) -> int:
    if path.is_symlink():
        raise PortalError("Unsafe checkpoint or partial file")
    # The Windows CRT defaults os.open to text mode: CRLF rewriting and Ctrl-Z
    # logical EOF corrupt arbitrary archive bytes and break verified resume.
    fd = os.open(path, flags | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0), 0o600)
    try:
        _private_file(fd)
    except BaseException:
        os.close(fd)
        raise
    return fd


def _sync_dir(directory: Path) -> None:
    if os.name != "nt":
        fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


@contextmanager
def _locked(path: Path) -> Iterator[None]:
    fd = _open_private(path, os.O_RDWR | os.O_CREAT)
    try:
        try:
            if os.name == "nt":
                import msvcrt
                if os.fstat(fd).st_size == 0:
                    os.write(fd, b"\0")  # Lock a real byte, not a stale PID lease.
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise PortalError("Another download is active for this build") from None
        yield
    finally:
        if os.name == "nt":
            try:
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        os.close(fd)


def _checkpoint_identity(build: Build, handoff: Handoff) -> dict:
    return {
        "build_id": build.build_id, "filename": build.filename, "size": build.size,
        "sha256": build.sha256, "platform": build.platform,
        "fs_id": handoff.fs_id, "remote_path": handoff.remote_path,
    }


def _load_checkpoint(path: Path, identity: dict) -> tuple[int, str]:
    fd = _open_private(path, os.O_RDONLY)
    with os.fdopen(fd, "rb") as file:
        raw = file.read(8193)
    if len(raw) > 8192:
        raise PortalError("Invalid checkpoint")
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeError):
        raise PortalError("Invalid checkpoint") from None
    if (
        not isinstance(data, dict) or data.get("version") != 1
        or any(data.get(key) != value for key, value in identity.items())
        or set(data) != {"version", "identity", "offset", "state", "prefix_sha256"} | set(identity)
        or data.get("state") != "active" or type(data.get("offset")) is not int
        or not 0 <= data["offset"] <= identity["size"]
        or not isinstance(data.get("prefix_sha256"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", data["prefix_sha256"])
    ):
        raise PortalError("Checkpoint identity changed or partial quarantined")
    return data["offset"], data["prefix_sha256"]


def _save_checkpoint(path: Path, identity: dict, offset: int, prefix_sha256: str, *, state: str = "active") -> None:
    staged = None
    try:
        fd, staged = tempfile.mkstemp(prefix=".checkpoint-", dir=path.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            if os.name != "nt":
                os.fchmod(file.fileno(), 0o600)
            json.dump({"version": 1, "identity": "baidu-owner-file", **identity,
                       "offset": offset, "state": state, "prefix_sha256": prefix_sha256},
                      file, separators=(",", ":"))
            file.flush()
            os.fsync(file.fileno())
        os.replace(staged, path)
        staged = None
        _sync_dir(path.parent)
    finally:
        if staged is not None:
            Path(staged).unlink(missing_ok=True)


def _prefix_hash(fd: int, length: int, *, build_id: str, phase: str) -> "hashlib._Hash":
    os.lseek(fd, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    read = 0
    while read < length:
        piece = os.read(fd, min(1024 * 1024, length - read))
        if not piece:
            observed_size = os.fstat(fd).st_size
            logging.getLogger(__name__).warning(
                "partial verification build_id=%s phase=%s expected_bytes=%d read_bytes=%d "
                "observed_size=%d io_mode=binary result=stopped reason=short_read "
                "external_change=unavailable",
                build_id, phase, length, read, observed_size,
            )
            raise PortalError(
                f"Partial verification stopped early ({read}/{length} bytes read; "
                f"file size {observed_size}); partial and checkpoint kept"
            )
        digest.update(piece)
        read += len(piece)
    return digest


def _verified_existing_target(path: Path, build: Build) -> None:
    """Verify a pre-existing published path without trusting its name or checkpoint."""
    if path.is_symlink():
        raise PortalError("Target file already exists; it will not be overwritten")
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                     | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0))
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode) or before.st_size != build.size:
                raise PortalError("Target file already exists; it will not be overwritten")
            if _prefix_hash(fd, build.size, build_id=build.build_id, phase="published").hexdigest() != build.sha256:
                raise PortalError("Target file already exists; it will not be overwritten")
            after = os.fstat(fd)
            named = path.lstat()
            identity = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
            if not stat.S_ISREG(named.st_mode) or identity(before) != identity(after) or identity(after) != identity(named):
                raise PortalError("Target file changed during verification; it will not be overwritten")
        finally:
            os.close(fd)
    except OSError:
        raise PortalError("Cannot verify existing target; it will not be overwritten") from None


def download(build: Build, handoff: Handoff, destination: Path, *,
             transport: Transport | None = None,
             progress: Callable[[int, int], None] | None = None,
             segment_bytes: int = SEGMENT_BYTES) -> Path:
    """One bounded attempt; on failure a later attempt gets a *fresh* handoff."""
    if not 1 <= segment_bytes <= SEGMENT_BYTES:
        raise ValueError("segment_bytes is outside the bounded range")
    if (build.build_id, build.filename, build.size, build.sha256, build.platform) != (
        handoff.build_id, handoff.filename, handoff.size, handoff.sha256, handoff.platform
    ):
        raise PortalError("Download handoff disagrees with catalog")
    # Vet even an injected Build before touching local disk or opening network.
    safe_filename(build.filename)
    _initial_url(handoff.dlink, handoff.access_token)
    destination = Path(destination).expanduser()
    if os.name == "nt" and not destination.resolve().is_relative_to(Path.home().resolve()):
        # Windows chmod does not establish an ACL: inherit the owner's profile ACL.
        raise PortalError("On Windows choose a downloads directory inside your user profile")
    _private_dir(destination, create=True, writable=True)
    private = destination / ".devportal"
    _private_dir(private, create=True)
    base = hashlib.sha256(build.build_id.encode("utf-8")).hexdigest()
    partial = private / (base + ".part")
    metadata = private / (base + ".json")
    final = destination / build.filename
    identity = _checkpoint_identity(build, handoff)
    transfer = transport or HTTPTransport()
    with _locked(private / (base + ".lock")):
        if final.exists() or final.is_symlink():
            _verified_existing_target(final, build)
            logging.getLogger(__name__).info(
                "download recovery build_id=%s phase=published expected_bytes=%d "
                "result=verified reason=existing_target private_cleanup=unavailable",
                build.build_id, build.size,
            )
            if progress:
                progress(build.size, build.size)
            return final
        has_partial, has_metadata = partial.exists() or partial.is_symlink(), metadata.exists() or metadata.is_symlink()
        if has_partial and not has_metadata:
            fd = _open_private(partial, os.O_RDWR)
            try:
                if os.fstat(fd).st_size:
                    raise PortalError("Unpaired partial/checkpoint; manual inspection required")
                _save_checkpoint(metadata, identity, 0, hashlib.sha256(b"").hexdigest())
                logging.getLogger(__name__).info(
                    "download recovery build_id=%s phase=initial expected_bytes=%d "
                    "result=checkpointed reason=empty_orphan prior_writer=unavailable",
                    build.build_id, build.size,
                )
            finally:
                os.close(fd)
        elif has_metadata and not has_partial:
            raise PortalError("Unpaired partial/checkpoint; manual inspection required")
        elif not has_partial:
            fd = _open_private(partial, os.O_RDWR | os.O_CREAT | os.O_EXCL)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            _save_checkpoint(metadata, identity, 0, hashlib.sha256(b"").hexdigest())
        offset, expected_prefix = _load_checkpoint(metadata, identity)
        fd = _open_private(partial, os.O_RDWR)
        try:
            current_size = os.fstat(fd).st_size
            if current_size < offset or current_size > build.size:
                raise PortalError("Partial size disagrees with checkpoint")
            # Verify the committed prefix before trimming uncommitted crash bytes.
            # An older Windows text-mode client may have expanded LF on disk;
            # preserve those bytes for inspection instead of truncating first.
            prefix = _prefix_hash(fd, offset, build_id=build.build_id, phase="resume")
            if prefix.hexdigest() != expected_prefix:
                logging.getLogger(__name__).warning(
                    "partial verification build_id=%s phase=resume expected_bytes=%d "
                    "observed_size=%d io_mode=binary result=quarantined reason=hash_mismatch "
                    "prior_writer=unavailable", build.build_id, offset, current_size,
                )
                _save_checkpoint(metadata, identity, offset, expected_prefix, state="quarantined")
                raise PortalError("Partial bytes changed; partial quarantined without truncation")
            if current_size > offset:
                # Crash after fsync(partial), before fsync(checkpoint): drop uncommitted bytes.
                os.ftruncate(fd, offset)
                os.fsync(fd)
            if progress:
                progress(offset, build.size)
            while offset < build.size:
                length = min(segment_bytes, build.size - offset)
                segment = _segment(transfer, handoff, offset, length)
                os.lseek(fd, offset, os.SEEK_SET)
                written = 0
                while written < length:
                    written += os.write(fd, segment[written:])
                os.fsync(fd)
                offset += length
                prefix.update(segment)
                _save_checkpoint(metadata, identity, offset, prefix.hexdigest())
                if progress:
                    progress(offset, build.size)
            if _prefix_hash(fd, build.size, build_id=build.build_id, phase="final").hexdigest() != build.sha256:
                _save_checkpoint(metadata, identity, offset, prefix.hexdigest(), state="quarantined")
                raise PortalError("SHA-256 verification failed; partial quarantined")
            os.fsync(fd)
            _private_dir(destination, writable=True)
            _private_file(fd)
            try:
                os.link(partial, final, follow_symlinks=False)
            except FileExistsError:
                raise PortalError("Target file already exists; it will not be overwritten") from None
            _sync_dir(destination)
        finally:
            os.close(fd)
        partial.unlink()
        metadata.unlink()
        _sync_dir(private)
        return final
