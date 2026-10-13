"""Offline fake-SSH and real loopback HTTP tests; no live SSH credentials."""

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import base64
import json
import multiprocessing
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import patch

from dev_portal.client import HubClient, PortalError
from dev_portal.pairing import Pairing
from dev_portal.tunnel import (Tunnel, TunnelError, _owns_accepted_socket,
                               _tcp_connection_owner, _tcp_listener_owner)
from tests.fakes import BUILD, FakeTransport, json_response, public_build, public_handoff


KEY_BLOB = b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20" + bytes(range(32))
KEY = "ssh-ed25519 " + base64.b64encode(KEY_BLOB).decode("ascii")
SECRET = "S" * 64  # A synthetic bearer matching Pairing's fixed alphabet/length.


def pairing(**changes):
    data = dict(ssh_host="gateway.example", ssh_port=2222, ssh_user="existing_user", host_key=KEY,
                project_ref="project-one", device_ref="device-one", portal_key=SECRET)
    data.update(changes)
    return Pairing(**data)


class FakeSSH:
    def __init__(self, *, exits=False):
        self.done = threading.Event()
        self.returncode = None
        self.terminated = False
        self.killed = False
        self.pid = 424242  # Offline mock owner, not the fixture HTTP listener.
        if exits:
            self._finish(255)

    def _finish(self, code):
        self.returncode = code
        self.done.set()

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if not self.done.wait(timeout):
            raise subprocess.TimeoutExpired("ssh.exe", timeout)
        return self.returncode

    def terminate(self):
        self.terminated = True
        self._finish(0)

    def kill(self):
        self.killed = True
        self._finish(-9)


class HealthServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), HealthHandler)
        self.health = True
        self.hang = False
        self.calls = []

    def handle_error(self, *_args):
        # Fixture clients deliberately close health connections on failure.
        pass


class HealthHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: HealthServer

    def log_message(self, *_args):
        return

    def do_GET(self):
        self.server.calls.append((self.path, self.headers.get("Authorization"), self.client_address[1]))
        if self.path == "/health":
            if self.server.hang:
                return  # No response: exercise a bounded probe timeout.
            self._send(200 if self.server.health else 503, {"status": "ok" if self.server.health else "bad"})
        elif self.path == "/portal/v1/builds":
            self._send(200, {"builds": [public_build()]})
        else:
            self._send(404, {})

    def do_POST(self):
        self.server.calls.append((self.path, self.headers.get("Authorization"), self.client_address[1]))
        self._send(200, public_handoff())

    def _send(self, status, payload):
        raw = json.dumps(payload).encode("ascii")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


@contextmanager
def healthy_server():
    server = HealthServer()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _hold_accepted_without_listener(ports, accepted, release):
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        ports.put(listener.getsockname()[1])
        channel, _ = listener.accept()
        listener.close()
        with channel:
            accepted.put(True)
            release.wait(4)
    finally:
        listener.close()


class TunnelTests(unittest.TestCase):
    @contextmanager
    def session(self, server, *, processes=None, **options):
        launched = [] if processes is None else processes
        def fake_popen(argv, **kwargs):
            instance = FakeSSH()
            launched.append((instance, list(argv), kwargs))
            return instance
        with (patch("dev_portal.tunnel._system_ssh", return_value=r"C:\Windows\System32\OpenSSH\ssh.exe"),
              patch("dev_portal.tunnel._local_port", return_value=server.server_port),
              patch("dev_portal.tunnel._owns_local_listener", return_value=True),
              patch("dev_portal.tunnel._owns_accepted_socket", return_value=True),
              patch("dev_portal.tunnel.subprocess.Popen", side_effect=fake_popen)):
            tunnel = Tunnel(pairing(), **options).start()
            try:
                yield tunnel, launched
            finally:
                tunnel.close()

    def test_pinned_argv_private_files_no_secrets_public_listener_or_proxy(self):
        with healthy_server() as server, self.session(server) as (tunnel, launched):
            instance, argv, kwargs = launched[0]
            self.assertTrue(tunnel.alive)
            self.assertEqual(tunnel.origin, f"http://127.0.0.1:{server.server_port}")
            self.assertEqual(argv[0], r"C:\Windows\System32\OpenSSH\ssh.exe")
            self.assertEqual(argv[-2:], ["--", "gateway.example"])
            self.assertEqual(argv[argv.index("-l") + 1], "existing_user")
            self.assertEqual(argv[argv.index("-p") + 1], "2222")
            self.assertEqual(argv[argv.index("-L") + 1], f"127.0.0.1:{server.server_port}:127.0.0.1:8765")
            self.assertIn("-N", argv)
            self.assertEqual(kwargs["shell"], False)
            self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
            self.assertEqual(kwargs["stdout"], subprocess.DEVNULL)
            self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
            self.assertEqual(kwargs["close_fds"], True)
            self.assertNotIn(SECRET, repr((argv, kwargs)))
            options = [argv[i + 1] for i, item in enumerate(argv[:-1]) if item == "-o"]
            for required in ("StrictHostKeyChecking=yes", "BatchMode=yes", "ExitOnForwardFailure=yes",
                             "GlobalKnownHostsFile=none", "HostKeyAlgorithms=ssh-ed25519", "ForwardAgent=no",
                             "ForwardX11=no", "ProxyCommand=none", "ProxyJump=none", "GatewayPorts=no",
                             "PasswordAuthentication=no"):
                self.assertIn(required, options)
            config = Path(argv[argv.index("-F") + 1])
            self.assertEqual(config.read_bytes(), b"")
            hosts = Path(next(option.partition("=")[2] for option in options if option.startswith("UserKnownHostsFile=")))
            self.assertEqual(hosts.read_text(), f"devportal-pinned.invalid {KEY}\n")
            self.assertNotIn("gateway.example", hosts.read_text())
            self.assertNotIn(SECRET, hosts.read_text())
        self.assertTrue(instance.terminated)
        self.assertFalse(config.exists())
        self.assertFalse(hosts.exists())
        with self.assertRaises(TunnelError):
            _ = tunnel.origin

    def test_guard_default_health_then_authorization_on_same_connection(self):
        with healthy_server() as server, self.session(server) as (tunnel, _):
            hub = HubClient(tunnel.origin, SECRET, tunnel.transport())
            self.assertEqual(hub.builds(), [BUILD])
            handoff = hub.handoff(BUILD)
            self.assertEqual(handoff.build_id, BUILD.build_id)
            api = [call for call in server.calls if call[0].startswith("/portal/")]
            self.assertEqual(len(api), 2)
            for path, authorization, source_port in api:
                self.assertEqual(authorization, "Bearer " + SECRET)
                self.assertTrue(any(p == "/health" and auth is None and port == source_port
                                    for p, auth, port in server.calls))
            self.assertTrue(all(auth is None for path, auth, _ in server.calls if path == "/health"))

    def test_stale_port_does_not_receive_bearer_even_while_process_alive(self):
        with healthy_server() as server, self.session(server) as (tunnel, launched):
            hub = HubClient(tunnel.origin, SECRET, tunnel.transport())
            server.health = False  # A new occupant/other service isn't DevHub.
            with self.assertRaises(PortalError) as raised:
                hub.builds()
            self.assertNotIn(SECRET, str(raised.exception))
            self.assertIsNone(raised.exception.__cause__)
            self.assertFalse(tunnel.alive)
            self.assertTrue(launched[0][0].terminated)
            self.assertFalse(any(path.startswith("/portal/") for path, _, _ in server.calls))
            with self.assertRaises(TunnelError):
                tunnel.transport().open("GET", "http://127.0.0.1:8765/portal/v1/builds", {}, timeout=1)

    def test_port_owner_changes_after_start_before_handoff_blocks_bearer(self):
        with healthy_server() as server, self.session(server) as (tunnel, _):
            hub = HubClient(tunnel.origin, SECRET, tunnel.transport())
            with patch("dev_portal.tunnel._owns_local_listener", return_value=False):
                with self.assertRaises(PortalError):
                    hub.builds()
            self.assertFalse(any(path.startswith("/portal/") for path, _, _ in server.calls))
            self.assertFalse(tunnel.alive)

    def test_accepted_socket_owner_changes_after_start_blocks_bearer(self):
        with healthy_server() as server, self.session(server) as (tunnel, _):
            hub = HubClient(tunnel.origin, SECRET, tunnel.transport())
            with patch("dev_portal.tunnel._owns_accepted_socket", return_value=False):
                with self.assertRaises(PortalError):
                    hub.builds()
            self.assertFalse(any(path.startswith("/portal/") for path, _, _ in server.calls))
            self.assertFalse(tunnel.alive)

    def test_child_exit_blocks_injected_transport_and_old_session(self):
        with healthy_server() as server, self.session(server) as (tunnel, launched), tempfile.TemporaryDirectory() as scratch:
            partial = Path(scratch) / "build.part"
            partial.write_bytes(b"durable partial bytes")
            underlying = FakeTransport(lambda *_: json_response({"builds": []}))
            hub = HubClient(tunnel.origin, SECRET, tunnel.transport(underlying))
            launched[0][0]._finish(255)
            self.assertTrue(tunnel.wait(timeout=1))  # Child watcher cleans up without polling the browser.
            with self.assertRaises(PortalError) as raised:
                hub.builds()
            self.assertNotIn(SECRET, str(raised.exception))
            self.assertEqual(underlying.calls, [])
            self.assertFalse(tunnel.alive)
            self.assertFalse(any(path.startswith("/portal/") for path, _, _ in server.calls))
            self.assertEqual(partial.read_bytes(), b"durable partial bytes")
            with self.assertRaises(TunnelError):
                tunnel.start()  # Restart requires a new Tunnel and fresh handoff.

    def test_guard_denies_public_http_and_unrelated_loopback_paths(self):
        with healthy_server() as server:
            for suffix in ("http://hub.example/portal/v1/builds", "https://hub.example/portal/v1/builds",
                           "http://127.0.0.2:8765/portal/v1/builds", "/health",
                           "/portal/v1/builds?secret=" + SECRET):
                with self.subTest(url=suffix), self.session(server) as (tunnel, _):
                    underlying = FakeTransport(lambda *_: json_response({"builds": []}))
                    url = tunnel.origin + suffix if suffix.startswith("/") else suffix
                    with self.assertRaises(TunnelError) as raised:
                        tunnel.transport(underlying).open("GET", url,
                                                           {"Authorization": "Bearer " + SECRET}, timeout=1)
                    self.assertNotIn(SECRET, str(raised.exception))
                    self.assertEqual(underlying.calls, [])

    def test_declined_auth_closes_session(self):
        with healthy_server() as server, self.session(server) as (tunnel, launched):
            underlying = FakeTransport(lambda *_: json_response({}, status=401))
            hub = HubClient(tunnel.origin, SECRET, tunnel.transport(underlying))
            with self.assertRaises(PortalError):
                hub.builds()
            self.assertEqual(len(underlying.calls), 1)
            self.assertFalse(tunnel.alive)
            self.assertTrue(launched[0][0].terminated)

    def test_missing_executable_bad_key_and_option_injection_redacted(self):
        for malicious in ("-oProxyCommand=evil", "user@gateway.example", "host.example\nHostKeyAlias=x"):
            with self.subTest(host=malicious), self.assertRaises(TunnelError) as raised:
                Tunnel(pairing(ssh_host=malicious))
            self.assertNotIn(malicious, str(raised.exception))
        for key in ("ssh-rsa AAAA", "ssh-ed25519 BAD!", "ssh-ed25519 " + base64.b64encode(KEY_BLOB + b"x").decode()):
            with self.subTest(key=key), self.assertRaises(TunnelError) as raised:
                Tunnel(pairing(host_key=key))
            self.assertNotIn(key, str(raised.exception))
        with patch("dev_portal.tunnel._system_ssh", side_effect=FileNotFoundError(SECRET)):
            with self.assertRaises(TunnelError) as raised:
                Tunnel(pairing()).start()
            self.assertNotIn(SECRET, str(raised.exception))
            self.assertIsNone(raised.exception.__cause__)
        with patch("dev_portal.tunnel.os.name", "posix"):
            with self.assertRaises(TunnelError):
                from dev_portal.tunnel import _system_ssh
                _system_ssh()  # Non-Windows does not silently use PATH's ssh.

    def test_port_collision_retries_locally_and_does_not_forward_to_wrong_port(self):
        with healthy_server() as server:
            first, second = FakeSSH(exits=True), FakeSSH()
            args = []
            def fake_popen(argv, **kwargs):
                args.append(list(argv))
                return first if len(args) == 1 else second
            with (patch("dev_portal.tunnel._system_ssh", return_value="system-ssh.exe"),
                  patch("dev_portal.tunnel._local_port", side_effect=[49301, server.server_port]),
                   patch("dev_portal.tunnel._owns_local_listener", return_value=True),
                   patch("dev_portal.tunnel._owns_accepted_socket", return_value=True),
                  patch("dev_portal.tunnel.subprocess.Popen", side_effect=fake_popen)):
                tunnel = Tunnel(pairing()).start()
                try:
                    self.assertEqual(len(args), 2)
                    self.assertNotIn(SECRET, repr(args))
                    self.assertEqual(args[0][args[0].index("-L") + 1], "127.0.0.1:49301:127.0.0.1:8765")
                    self.assertEqual(tunnel.origin, f"http://127.0.0.1:{server.server_port}")
                    self.assertEqual(server.calls[0][0], "/health")
                finally:
                    tunnel.close()
            self.assertTrue(second.terminated)

    def test_counterfeit_health_listener_never_receives_bearer_during_ssh_handshake(self):
        with healthy_server() as server:
            counterfeit_child = FakeSSH()  # Live handshake, but it did not bind server's port.
            with (patch("dev_portal.tunnel._system_ssh", return_value="system-ssh.exe"),
                  patch("dev_portal.tunnel._local_port", return_value=server.server_port),
                  patch("dev_portal.tunnel._owns_local_listener", return_value=False),
                  patch("dev_portal.tunnel.subprocess.Popen", return_value=counterfeit_child)):
                with self.assertRaises(TunnelError):
                    Tunnel(pairing(), startup_timeout=0.12).start()
            self.assertTrue(counterfeit_child.terminated)
            self.assertFalse(any(path.startswith("/portal/") for path, _, _ in server.calls))
            self.assertEqual(server.calls, [])  # Do not even probe an unowned listener.

    def test_counterfeit_accepted_socket_rejected_even_if_ssh_still_listens(self):
        with healthy_server() as server:
            child = FakeSSH()
            with (patch("dev_portal.tunnel._system_ssh", return_value="system-ssh.exe"),
                  patch("dev_portal.tunnel._local_port", return_value=server.server_port),
                  patch("dev_portal.tunnel._owns_local_listener", return_value=True),
                  patch("dev_portal.tunnel._owns_accepted_socket", return_value=False),
                  patch("dev_portal.tunnel.subprocess.Popen", return_value=child)):
                with self.assertRaises(TunnelError):
                    Tunnel(pairing(), startup_timeout=0.2).start()
            self.assertTrue(child.terminated)
            self.assertTrue(all(authorization is None and path == "/health"
                                for path, authorization, _ in server.calls))

    @unittest.skipUnless(sys.platform == "win32", "Windows TCP owner API fixture")
    def test_real_windows_loopback_listener_reports_owning_process(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            port = listener.getsockname()[1]
            self.assertEqual(_tcp_listener_owner(port), os.getpid())
            with socket.create_connection(("127.0.0.1", port), timeout=1) as client:
                accepted, _ = listener.accept()
                with accepted:
                    self.assertEqual(_tcp_connection_owner(port, client.getsockname()[1]), os.getpid())
        self.assertNotEqual(_tcp_listener_owner(port), os.getpid())

    @unittest.skipUnless(sys.platform == "win32", "Windows cross-process socket ownership fixture")
    def test_windows_accepted_socket_owner_survives_listener_close(self):
        context = multiprocessing.get_context("spawn")
        ports, accepted, release = context.Queue(), context.Queue(), context.Event()
        child = context.Process(target=_hold_accepted_without_listener, args=(ports, accepted, release))
        child.start()
        try:
            port = ports.get(timeout=3)
            with socket.create_connection(("127.0.0.1", port), timeout=2) as client:
                self.assertTrue(accepted.get(timeout=3))
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as replacement:
                    replacement.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    replacement.bind(("127.0.0.1", port))
                    replacement.listen(1)
                    # Final forged-health state: SSH owns the sole listener,
                    # yet a different process still owns the accepted socket.
                    self.assertEqual(_tcp_listener_owner(port), os.getpid())
                    self.assertEqual(_tcp_connection_owner(port, client.getsockname()[1]), child.pid)
                    self.assertFalse(_owns_accepted_socket(SimpleNamespace(sock=client), port,
                                                           SimpleNamespace(pid=os.getpid())))
        finally:
            release.set()
            child.join(timeout=3)
            if child.is_alive():
                child.terminate()
                child.join(timeout=2)
            ports.close()
            accepted.close()
        self.assertEqual(child.exitcode, 0)

    def test_probe_timeout_cleans_child_and_files_without_credentials(self):
        with healthy_server() as server, patch("dev_portal.tunnel._owns_local_listener", return_value=True):
            server.hang = True
            launched = []
            def fake_popen(argv, **kwargs):
                process = FakeSSH()
                launched.append((process, list(argv)))
                return process
            with (patch("dev_portal.tunnel._system_ssh", return_value="system-ssh.exe"),
                  patch("dev_portal.tunnel._local_port", return_value=server.server_port),
                  patch("dev_portal.tunnel.subprocess.Popen", side_effect=fake_popen)):
                tunnel = Tunnel(pairing(), startup_timeout=0.2, probe_timeout=0.06)
                with self.assertRaises(TunnelError) as raised:
                    tunnel.start()
            self.assertNotIn(SECRET, str(raised.exception))
            self.assertIsNone(raised.exception.__cause__)
            self.assertTrue(launched)
            self.assertTrue(all(process.terminated for process, _ in launched))
            self.assertTrue(all(not Path(argv[argv.index("-F") + 1]).exists() for _, argv in launched))
            self.assertFalse(any(path.startswith("/portal/") for path, _, _ in server.calls))

    def test_fake_transport_exceptions_redacted_and_session_closed(self):
        with healthy_server() as server, self.session(server) as (tunnel, _):
            def explode(*_):
                raise RuntimeError(SECRET)
            hub = HubClient(tunnel.origin, SECRET, tunnel.transport(FakeTransport(explode)))
            with self.assertRaises(PortalError) as raised:
                hub.builds()
            self.assertNotIn(SECRET, str(raised.exception))
            self.assertFalse(tunnel.alive)
