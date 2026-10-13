"""One-shot, pinned Windows OpenSSH forward for a paired Portal session.

The recipient must already have an authorized SSH identity in their Windows
account. Neither an identity nor a Portal bearer is passed to ssh.exe. Close a
failed session and construct a *new* Tunnel for reconnection; the old local
server/HubClient must also be closed by its owner. Partial downloads are not
removed, and need a fresh handoff before ordinary validated resume.
"""

from __future__ import annotations

import atexit
import base64
import binascii
import ctypes
from ctypes import wintypes
import http.client
import json
import math
import os
from pathlib import Path
import re
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
from typing import TYPE_CHECKING, Mapping
from urllib.parse import urlsplit

from .client import PortalError, Response, Transport, _Response

if TYPE_CHECKING:
    from .pairing import Pairing


class TunnelError(PortalError):
    """A fixed, credential-free connection failure."""


_UNAVAILABLE = "Private connection unavailable; reconnect or contact the operator"
_ALIAS = "devportal-pinned.invalid"
_HEALTH_LIMIT = 2048
_ATTEMPTS = 3
_START_TIMEOUT = 6.0
_PROBE_TIMEOUT = 0.75


def _host_key(value: object) -> str:
    """Reject comments, other algorithms and malformed/non-canonical key blobs."""
    if not isinstance(value, str) or len(value) > 256:
        raise TunnelError(_UNAVAILABLE)
    fields = value.split(" ")
    if len(fields) != 2 or fields[0] != "ssh-ed25519":
        raise TunnelError(_UNAVAILABLE)
    try:
        blob = base64.b64decode(fields[1], validate=True)
    except (ValueError, binascii.Error):
        raise TunnelError(_UNAVAILABLE) from None
    if (blob[:15] != b"\x00\x00\x00\x0bssh-ed25519" or blob[15:19] != b"\x00\x00\x00\x20"
            or len(blob) != 51 or base64.b64encode(blob).decode("ascii") != fields[1]):
        raise TunnelError(_UNAVAILABLE)
    return value


def _endpoint(pairing: Pairing) -> tuple[str, int, str, str]:
    try:
        host, port, user, key = pairing.ssh_host, pairing.ssh_port, pairing.ssh_user, pairing.host_key
        if (not isinstance(host, str) or len(host) > 253 or not host
            or any(len(label) > 63 or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?", label)
                   for label in host.split("."))
            or type(port) is not int or not 1 <= port <= 65535
            or not isinstance(user, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,63}", user)):
            raise TunnelError(_UNAVAILABLE)
        return host, port, user, _host_key(key)
    except Exception:
        raise TunnelError(_UNAVAILABLE) from None


def _system_ssh() -> str:
    """Only Windows' installed system OpenSSH, never PATH or a bundled binary."""
    if os.name != "nt":
        raise TunnelError(_UNAVAILABLE)
    # Sysnative bypasses filesystem redirection for a 32-bit client on 64-bit
    # Windows; System32 is the normal native-system location.
    try:
        import ctypes
        directory = ctypes.create_unicode_buffer(32768)
        size = ctypes.windll.kernel32.GetWindowsDirectoryW(directory, len(directory))
        if not 0 < size < len(directory):
            raise TunnelError(_UNAVAILABLE)
        root = Path(directory.value)
        for system_dir in ("Sysnative", "System32"):
            candidate = root / system_dir / "OpenSSH" / "ssh.exe"
            if candidate.is_file():
                return str(candidate)
    except Exception:
        pass
    raise TunnelError(_UNAVAILABLE) from None


def _local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _tcp_rows(table_class: int) -> list[bytes] | None:
    """Read Windows IPv4 MIB_TCPROW_OWNER_PID rows, or fail closed."""
    if sys.platform != "win32":
        return None
    try:
        query = ctypes.WinDLL("iphlpapi", use_last_error=True).GetExtendedTcpTable
        query.restype = wintypes.DWORD
        query.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), wintypes.BOOL,
                          wintypes.ULONG, wintypes.ULONG, wintypes.ULONG]
        size = wintypes.DWORD(0)
        # AF_INET=2; TCP_TABLE_OWNER_PID_LISTENER=3, ALL=5.
        if query(None, ctypes.byref(size), False, 2, table_class, 0) != 122:
            return None
        for _ in range(3):  # TCP table can grow between the sizing and fill calls.
            if not 4 <= size.value <= 16 * 1024 * 1024:
                return None
            table = ctypes.create_string_buffer(size.value)
            code = query(table, ctypes.byref(size), False, 2, table_class, 0)
            if code == 122:
                continue
            if code != 0:
                return None
            raw = table.raw[:size.value]
            count = struct.unpack_from("<I", raw)[0]
            if count > (len(raw) - 4) // 24:
                return None
            return [raw[pos:pos + 24] for pos in range(4, 4 + count * 24, 24)]
        return None
    except (OSError, ValueError, TypeError, AttributeError, struct.error):
        return None


def _tcp_listener_owner(port: int) -> int | None:
    rows = _tcp_rows(3)
    if rows is None:
        return None
    owners = {
        struct.unpack_from("<I", row, 20)[0] for row in rows
        if (struct.unpack_from("<I", row)[0] == 2  # TCP_STATE_LISTEN
            and row[4:8] == b"\x7f\x00\x00\x01"
            and row[8:10] == port.to_bytes(2, "big"))
    }
    return next(iter(owners)) if len(owners) == 1 else None


def _tcp_connection_owner(port: int, client_port: int) -> int | None:
    """Return the PID owning the *accepted*, persistent health TCP connection.

    A co-bound listener can vanish while keeping its accepted socket alive;
    checking only listener PID cannot authenticate a previously accepted socket.
    """
    rows = _tcp_rows(5)
    if rows is None:
        return None
    owners = {
        struct.unpack_from("<I", row, 20)[0] for row in rows
        if (struct.unpack_from("<I", row)[0] == 5  # TCP_STATE_ESTAB
            and row[4:8] == b"\x7f\x00\x00\x01"
            and row[8:10] == port.to_bytes(2, "big")
            and row[12:16] == b"\x7f\x00\x00\x01"
            and row[16:18] == client_port.to_bytes(2, "big"))
    }
    return next(iter(owners)) if len(owners) == 1 else None


def _owns_local_listener(port: int, process: subprocess.Popen) -> bool:
    pid = getattr(process, "pid", None)
    return isinstance(pid, int) and pid > 0 and _tcp_listener_owner(port) == pid


def _owns_accepted_socket(connection: http.client.HTTPConnection, port: int,
                          process: subprocess.Popen) -> bool:
    try:
        sock = connection.sock
        if sock is None or sock.getsockname()[0] != "127.0.0.1" or sock.getpeername() != ("127.0.0.1", port):
            return False
        pid = getattr(process, "pid", None)
        return isinstance(pid, int) and pid > 0 and _tcp_connection_owner(port, sock.getsockname()[1]) == pid
    except (OSError, ValueError, AttributeError):
        return False


def _private_file(path: Path, data: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="ascii", newline="\n") as stream:
        stream.write(data)


def _stop(process: subprocess.Popen) -> None:
    try:
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=1)
    except Exception:
        # No OS/process exception may reach the UI (including argv or host).
        pass


def _health_connection(port: int, timeout: float, *, reusable: bool) -> http.client.HTTPConnection | None:
    """Real, unauthenticated bounded GET; optionally retain its *same* socket."""
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    passed = False
    try:
        deadline = time.monotonic() + timeout
        connection.request("GET", "/health", headers={"Accept": "application/json"})
        response = connection.getresponse()
        content_type = (response.getheader("Content-Type") or "").split(";", 1)[0].strip().lower()
        if response.status != 200 or content_type != "application/json":
            return None
        length = response.getheader("Content-Length")
        if length is not None and (not length.isdigit() or int(length) > _HEALTH_LIMIT):
            return None
        raw = bytearray()
        while len(raw) <= _HEALTH_LIMIT:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            if connection.sock is not None:
                connection.sock.settimeout(min(remaining, timeout))
            byte = response.read(1)
            if not byte:
                break
            raw.extend(byte)
        if len(raw) > _HEALTH_LIMIT or (length is not None and len(raw) != int(length)):
            return None
        if not isinstance((body := json.loads(raw)), dict) or body.get("status") != "ok":
            return None
        if reusable and connection.sock is None:
            return None
        passed = True
        return connection
    except (OSError, ValueError, TypeError, http.client.HTTPException):
        return None
    finally:
        # A failed probe must not retain or accidentally reuse a stale socket.
        if not passed:
            connection.close()


class Tunnel:
    """Start once; close on exit/failure; expose only an owned loopback origin."""

    def __init__(self, pairing: Pairing, *, startup_timeout: float = _START_TIMEOUT, probe_timeout: float = _PROBE_TIMEOUT):
        self._host, self._ssh_port, self._user, self._key = _endpoint(pairing)
        if not all(isinstance(value, (int, float)) and not isinstance(value, bool)
                   and math.isfinite(value) and 0 < value <= maximum
                   for value, maximum in ((probe_timeout, 2), (startup_timeout, 15))):
            raise TunnelError(_UNAVAILABLE)
        self._startup_timeout = startup_timeout
        self._probe_timeout = probe_timeout
        self._process: subprocess.Popen | None = None
        self._port: int | None = None
        self._temporary: tempfile.TemporaryDirectory | None = None
        self._ready = False
        self._used = False
        self._closed = threading.Event()
        self._lock = threading.RLock()

    @property
    def origin(self) -> str:
        with self._lock:
            if not self._ready or self._port is None:
                raise TunnelError(_UNAVAILABLE)
            return f"http://127.0.0.1:{self._port}"

    @property
    def alive(self) -> bool:
        with self._lock:
            try:
                process, port = self._process, self._port
                if (not self._ready or process is None or port is None
                        or process.poll() is not None or not _owns_local_listener(port, process)):
                    self.close()
                    return False
                connection = _health_connection(port, self._probe_timeout, reusable=True)
                if connection is None:
                    self.close()
                    return False
                try:
                    owned_socket = _owns_accepted_socket(connection, port, process)
                finally:
                    connection.close()
                if (not owned_socket or process.poll() is not None
                        or not _owns_local_listener(port, process)):
                    self.close()
                    return False
                return True
            except Exception:
                self.close()
                return False

    def _argv(self, executable: str, port: int, directory: Path) -> list[str]:
        options = (
            f"UserKnownHostsFile={directory / 'known_hosts'}", "GlobalKnownHostsFile=none",
            f"HostKeyAlias={_ALIAS}", "StrictHostKeyChecking=yes", "HostKeyAlgorithms=ssh-ed25519",
            "CheckHostIP=no", "VerifyHostKeyDNS=no", "UpdateHostKeys=no",
            "BatchMode=yes", "NumberOfPasswordPrompts=0", "PasswordAuthentication=no",
            "KbdInteractiveAuthentication=no", "PreferredAuthentications=publickey",
            "ExitOnForwardFailure=yes", "ConnectTimeout=5", "ConnectionAttempts=1",
            "ForwardAgent=no", "ForwardX11=no", "PermitLocalCommand=no",
            "ProxyCommand=none", "ProxyJump=none", "CanonicalizeHostname=no",
            "ControlMaster=no", "GatewayPorts=no", "RequestTTY=no",
        )
        return [executable, "-F", str(directory / "config"),
                *(arg for option in options for arg in ("-o", option)),
                "-N", "-T", "-L", f"127.0.0.1:{port}:127.0.0.1:8765",
                "-p", str(self._ssh_port), "-l", self._user, "--", self._host]

    def start(self) -> Tunnel:
        with self._lock:
            if self._used:
                raise TunnelError(_UNAVAILABLE)
            self._used = True
            try:
                executable = _system_ssh()
                self._temporary = tempfile.TemporaryDirectory(prefix="devportal-ssh-")
                directory = Path(self._temporary.name)
                _private_file(directory / "config", "")
                _private_file(directory / "known_hosts", f"{_ALIAS} {self._key}\n")
                deadline = time.monotonic() + self._startup_timeout
                for _ in range(_ATTEMPTS):
                    if time.monotonic() >= deadline:
                        break
                    port = _local_port()
                    # The local bind is intentionally *not* held across ssh.exe:
                    # OpenSSH must own it, and ExitOnForwardFailure catches races.
                    self._process = subprocess.Popen(
                        self._argv(executable, port, directory), shell=False,
                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        close_fds=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                    self._port = port
                    while time.monotonic() < deadline and self._process.poll() is None:
                        # A healthy unrelated local listener must never be
                        # mistaken for the OpenSSH process we launched.
                        if not _owns_local_listener(port, self._process):
                            time.sleep(min(0.05, max(0, deadline - time.monotonic())))
                            continue
                        first = _health_connection(port, self._probe_timeout, reusable=True)
                        if first is not None:
                            first_owned = _owns_accepted_socket(first, port, self._process)
                            first.close()
                            if not first_owned:
                                raise TunnelError(_UNAVAILABLE)
                            time.sleep(0.08)
                            second = _health_connection(port, self._probe_timeout, reusable=True)
                            if second is not None:
                                second_owned = _owns_accepted_socket(second, port, self._process)
                                second.close()
                                if (second_owned and self._process.poll() is None
                                        and _owns_local_listener(port, self._process)):
                                    self._ready = True
                                    atexit.register(self.close)
                                    process = self._process
                                    threading.Thread(target=self._watch, args=(process,), daemon=True,
                                                     name="DevPortal SSH watcher").start()
                                    return self
                        time.sleep(min(0.05, max(0, deadline - time.monotonic())))
                    _stop(self._process)
                    self._process = None
                    self._port = None
            except Exception:
                self.close()
                raise TunnelError(_UNAVAILABLE) from None
            self.close()
            raise TunnelError(_UNAVAILABLE)

    def _watch(self, process: subprocess.Popen) -> None:
        try:
            process.wait()
        except Exception:
            pass
        with self._lock:
            if self._process is process:
                self.close()

    def wait(self, timeout: float | None = None) -> bool:
        """Wake the owning UI when the child dies or the session is closed."""
        return self._closed.wait(timeout)

    def transport(self, underlying: Transport | None = None) -> GuardedTransport:
        """Pass to HubClient(self.origin, pairing.portal_key, tunnel.transport()).

        The default uses one health-checked TCP connection for both the probe
        and the authorized request. Injection is for offline test transports.
        """
        return GuardedTransport(self, underlying)

    def close(self) -> None:
        with self._lock:
            self._used = True
            self._ready = False
            process, self._process = self._process, None
            self._port = None
            temporary, self._temporary = self._temporary, None
            atexit.unregister(self.close)
            if process is not None:
                _stop(process)
            if temporary is not None:
                try:
                    temporary.cleanup()
                except OSError:
                    pass
            self._closed.set()


class GuardedTransport:
    """A HubClient Transport that never sends Authorization to a stale port."""

    def __init__(self, tunnel: Tunnel, underlying: Transport | None = None):
        self._tunnel = tunnel
        self._underlying = underlying

    def open(self, method: str, url: str, headers: Mapping[str, str], *, timeout: float) -> Response:
        tunnel = self._tunnel
        with tunnel._lock:
            try:
                origin = tunnel.origin
                parsed = urlsplit(url)
                allowed = (method == "GET" and parsed.path == "/portal/v1/builds") or (
                    method == "POST" and re.fullmatch(r"/portal/v1/builds/[A-Za-z0-9][A-Za-z0-9._-]{0,127}/download", parsed.path)
                )
                if (not allowed or url != origin + parsed.path or parsed.query or parsed.fragment
                    or not isinstance(timeout, (float, int)) or not 0 < timeout <= 30):
                    raise TunnelError(_UNAVAILABLE)
                process, port = tunnel._process, tunnel._port
                if (process is None or port is None or process.poll() is not None
                        or not _owns_local_listener(port, process)):
                    raise TunnelError(_UNAVAILABLE)
                if self._underlying is not None:
                    if not tunnel.alive:
                        raise TunnelError(_UNAVAILABLE)
                    # An injectable transport is for offline fixtures; the
                    # production path below pins health+auth to one TCP socket.
                    response = self._underlying.open(method, url, headers, timeout=timeout)
                    if response.status != 200:
                        response.close()
                        raise TunnelError(_UNAVAILABLE)
                    return response
                connection = _health_connection(port, tunnel._probe_timeout, reusable=True)
                if connection is None:
                    raise TunnelError(_UNAVAILABLE)
                try:
                    if (process.poll() is not None or connection.sock is None
                            or not _owns_local_listener(port, process)
                            or not _owns_accepted_socket(connection, port, process)):
                        raise TunnelError(_UNAVAILABLE)
                    connection.sock.settimeout(timeout)
                    connection.request(method, parsed.path, headers=dict(headers),
                                       body=b"" if method == "POST" else None)
                    response = connection.getresponse()
                    if response.status != 200:
                        connection.close()
                        raise TunnelError(_UNAVAILABLE)
                    return _Response(response, connection)
                except Exception:
                    connection.close()
                    raise
            except Exception:
                tunnel.close()
                raise TunnelError(_UNAVAILABLE) from None
