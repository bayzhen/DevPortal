import base64
import struct
import tempfile
import threading
import unittest
from pathlib import Path

from dev_portal.client import Build, PortalError
from dev_portal.desktop import DesktopError, DesktopSession
from dev_portal.pairing import Pairing


def pairing():
    alg = b"ssh-ed25519"
    public = struct.pack(">I", len(alg)) + alg + struct.pack(">I", 32) + b"p" * 32
    return Pairing("hub.example", 22, "tester", "ssh-ed25519 " + base64.b64encode(public).decode(),
                   "game-dev-ue", "spouse-device", "K" * 64)


class FakeTunnel:
    def __init__(self, _pairing):
        self.origin = "http://127.0.0.1:18765"
        self.alive = True
        self.closed = False

    def start(self):
        return self

    def transport(self):
        return self

    def close(self):
        self.alive = False
        self.closed = True


class FakeServer:
    def __init__(self, _hub, _downloads):
        self.origin = "http://127.0.0.1:9876"
        self.running = threading.Event()
        self.closed = False

    def serve_forever(self, **_kw):
        self.running.wait(3)

    def shutdown(self):
        self.running.set()

    def server_close(self):
        self.closed = True


class DesktopSessionTests(unittest.TestCase):
    def setUp(self):
        self.saved = []
        self.forgot = []
        self.tunnels = []
        self.servers = []
        def make_tunnel(config):
            tunnel = FakeTunnel(config)
            self.tunnels.append(tunnel)
            return tunnel
        def make_server(hub, downloads):
            server = FakeServer(hub, downloads)
            self.servers.append(server)
            return server
        self.builds = [Build("build-one", "game-dev-ue", "bundle.tar.zst", "linux",
                             "development", 123, "a" * 64)]
        def make_hub(origin, key, transport):
            self.assertEqual(origin, "http://127.0.0.1:18765")
            self.assertEqual(key, "K" * 64)
            self.assertIs(transport, self.tunnels[-1])
            class Hub:
                def builds(_self):
                    return self.builds
            return Hub()
        self.session = DesktopSession(tunnel_factory=make_tunnel, hub_factory=make_hub,
                                      server_factory=make_server, downloads=Path("/tmp/unused"),
                                      remember_loader=lambda: None,
                                      remember_saver=self.saved.append,
                                      remember_forgetter=lambda: self.forgot.append(True))
        self.addCleanup(self.session.close)

    def test_successful_session_only_login_never_saves(self):
        url = self.session.connect(pairing(), remember=False)
        self.assertEqual(url, "http://127.0.0.1:9876")
        self.assertEqual(self.session.project, "game-dev-ue")
        self.assertEqual(self.saved, [])
        self.assertEqual(self.forgot, [True])
        self.session.close()
        self.assertTrue(self.tunnels[0].closed)
        self.assertTrue(self.servers[0].closed)

    def test_remember_after_authenticated_catalog(self):
        self.session.connect(pairing(), remember=True)
        self.assertEqual(self.saved, [pairing()])
        self.assertEqual(self.forgot, [])
        self.session.close()

    def test_forget_restart_login_keeps_existing_download_checkpoint(self):
        with tempfile.TemporaryDirectory() as root:
            self.session.downloads = Path(root) / "Downloads" / "DevPortal"
            private = self.session.downloads / ".devportal"
            private.mkdir(parents=True)
            partial = private / "existing.part"
            checkpoint = private / "existing.json"
            partial.write_bytes(b"archive\r\n\x1a")
            checkpoint.write_text('{"state":"active"}')
            self.session.connect(pairing(), remember=True)
            self.session.forget()
            self.session.close()
            self.session.connect(pairing(), remember=False)
            self.assertEqual(partial.read_bytes(), b"archive\r\n\x1a")
            self.assertEqual(checkpoint.read_text(), '{"state":"active"}')

    def test_forget_during_inflight_remember_cannot_restore_saved_bearer(self):
        waiting, proceed = threading.Event(), threading.Event()
        def delayed_hub(_origin, _key, _transport):
            class Hub:
                def builds(_self):
                    waiting.set()
                    if not proceed.wait(2):
                        raise PortalError("Offline fixture timed out")
                    return self.builds
            return Hub()
        self.session.hub_factory = delayed_hub
        result = []
        worker = threading.Thread(target=lambda: result.append(self.session.connect(pairing(), remember=True)))
        worker.start()
        try:
            self.assertTrue(waiting.wait(2))
            self.session.forget()
        finally:
            proceed.set()
            worker.join(timeout=3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result, ["http://127.0.0.1:9876"])
        self.assertEqual(self.forgot, [True])
        self.assertEqual(self.saved, [])

    def test_mismatched_project_fails_before_save_or_page(self):
        self.builds[0] = Build("build-one", "another-project", "bundle.tar.zst",
                               "linux", "development", 123, "a" * 64)
        with self.assertRaises(DesktopError):
            self.session.connect(pairing(), remember=True)
        self.assertEqual(self.saved, [])
        self.assertTrue(self.tunnels[0].closed)
        self.assertEqual(self.servers, [])

    def test_empty_catalog_does_not_fabricate_project(self):
        self.builds.clear()
        self.session.connect(pairing(), remember=False)
        self.assertIsNone(self.session.project)

    def test_revoked_or_unavailable_key_never_remembered(self):
        def failing(_origin, _key, _transport):
            class Hub:
                def builds(self):
                    raise PortalError("DevHub declined the Portal request")
            return Hub()
        self.session.hub_factory = failing
        with self.assertRaises(PortalError):
            self.session.connect(pairing(), remember=True)
        self.assertEqual(self.saved, [])
        self.assertTrue(self.tunnels[0].closed)


if __name__ == "__main__":
    unittest.main()
