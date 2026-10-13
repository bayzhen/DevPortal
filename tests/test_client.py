import contextlib
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest

from dev_portal.client import Build, Handoff, HubClient, PortalError, MAX_JSON, safe_filename
from dev_portal.config import ConfigError, load_config, save_config, validate_hub_url
from tests.fakes import BUILD, TOKEN, FakeResponse, FakeTransport, json_response, public_build, public_handoff


class ConfigurationTests(unittest.TestCase):
    def test_origin_policy(self):
        for url in ("https://hub.example", "https://hub.example:8443/", "http://127.0.0.1:8765", "http://[::1]:9000"):
            with self.subTest(url=url):
                self.assertTrue(validate_hub_url(url))
        for url in ("http://hub.example", "http://localhost:8765", "http://192.168.0.1",
                    "http://127.0.0.2.evil", "https://user:pass@hub.example", "https://hub.example/path",
                    "https://hub.example/?secret=123", "https://hub.example/#frag", "https://hub.example\\@evil"):
            with self.subTest(url=url), self.assertRaises(ConfigError):
                validate_hub_url(url)

    def test_owner_config_only_persists_hub_and_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "private" / "config.json"
            save_config("http://127.0.0.1:8765", "portal-key-safe", target)
            self.assertEqual(load_config(target), ("http://127.0.0.1:8765", "portal-key-safe"))
            self.assertEqual(set(json.loads(target.read_text())), {"hub_url", "portal_key"})
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(target.parent.stat().st_mode), 0o700)
                self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
            target.write_text('{"hub_url":"https://x","portal_key":"portal-key-safe","dlink":"secret"}')
            with self.assertRaises(ConfigError):
                load_config(target)

    def test_unsafe_key_file_and_symlink_rejected(self):
        if os.name == "nt":
            self.skipTest("POSIX file mode boundary")
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "private" / "config.json"
            save_config("https://hub.example", "portal-key-safe", target)
            target.chmod(0o644)
            with self.assertRaises(ConfigError):
                load_config(target)
            target.chmod(0o600)
            other = target.with_name("other.json")
            target.rename(other)
            target.symlink_to(other)
            with self.assertRaises(ConfigError):
                load_config(target)


class HubClientTests(unittest.TestCase):
    def test_exact_get_and_post_protocol(self):
        def response(method, url, headers):
            self.assertEqual(headers["Authorization"], "Bearer portal-key-safe")
            self.assertEqual(headers["Accept"], "application/json")
            if method == "GET":
                self.assertEqual(url, "https://hub.example/portal/v1/builds")
                self.assertNotIn("Content-Length", headers)
                return json_response({"builds": [public_build()]})
            self.assertEqual(method, "POST")
            self.assertEqual(headers["Content-Length"], "0")
            self.assertEqual(url, "https://hub.example/portal/v1/builds/build-one/download")
            return json_response(public_handoff())
        transport = FakeTransport(response)
        client = HubClient("https://hub.example", "portal-key-safe", transport)
        builds = client.builds()
        self.assertEqual(builds, [BUILD])
        handoff = client.handoff(builds[0])
        self.assertEqual(handoff.access_token, TOKEN)
        self.assertNotIn(TOKEN, repr(handoff))
        self.assertEqual([c[0] for c in transport.calls], ["GET", "POST"])

    def test_response_bounds_bad_catalog_and_handoff_identity(self):
        for payload in ({"builds": [public_build()] * 129}, {"builds": [public_build(), public_build()]},
                        {"builds": [{**public_build(), "sha256": "bad"}]},
                        {"builds": [{**public_build(), "size": True}]},
                        {"builds": [{**public_build(), "filename": "../bad.zip"}]}):
            with self.subTest(payload=str(payload)[:50]):
                client = HubClient("https://hub.example", "portal-key-safe", FakeTransport(lambda *_: json_response(payload)))
                with self.assertRaises(PortalError):
                    client.builds()
        oversized = FakeResponse(200, b"x" * (MAX_JSON + 1), {"Content-Type": "application/json"})
        client = HubClient("https://hub.example", "portal-key-safe", FakeTransport(lambda *_: oversized))
        with self.assertRaisesRegex(PortalError, "too large"):
            client.builds()
        mismatch = {**public_handoff(), "sha256": "0" * 64}
        client = HubClient("https://hub.example", "portal-key-safe", FakeTransport(lambda *_: json_response(mismatch)))
        with self.assertRaisesRegex(PortalError, "disagrees"):
            client.handoff(BUILD)

    def test_errors_never_render_secret_or_follow_redirect(self):
        token = "secret-handoff-token"
        def forbidden(method, url, headers):
            return FakeResponse(302, f"redirect {token}".encode(), {"Location": "https://evil.example/?access_token=" + token})
        fake = FakeTransport(forbidden)
        client = HubClient("https://hub.example", "portal-key-safe", fake)
        with self.assertRaises(PortalError) as raised:
            client.builds()
        self.assertNotIn(token, str(raised.exception))
        self.assertEqual(len(fake.calls), 1)
        def exploding(method, url, headers):
            raise RuntimeError("request failed https://hub.example/?key=" + token)
        client = HubClient("https://hub.example", "portal-key-safe", FakeTransport(exploding))
        with self.assertRaises(PortalError) as raised:
            client.builds()
        self.assertNotIn(token, str(raised.exception))
        self.assertIsNone(raised.exception.__cause__)

    def test_handoff_path_delimiters_cannot_be_advertised_as_usable_builds(self):
        filename = "demo#1.zip"
        self.assertEqual(safe_filename(filename), filename)
        build = replace(BUILD, filename=filename)
        data = {**public_handoff(), "filename": filename,
                "remote_path": "/apps/gdue/builds/demo#1.zip"}
        with self.assertRaisesRegex(PortalError, "Invalid download handoff"):
            Handoff.parse(data, build)

    def test_filename_windows_safe(self):
        # Mirrored in the independently deployed DevHub portal catalog tests.
        unsafe = ("CON.txt", "LPT9", "a:b", ".hidden", "name. ", "a\\b", "a/b",
                  "PRN", "AUX.zip", "COM1.txt", "NUL", "LPT1.x", "name.",
                  "bad\x7f", "bad\x00", "<bad>", "a?b", "a*b", "x" * 201,
                  "COM¹.zip", "COM².zip", "LPT³.zip")
        for name in unsafe:
            with self.subTest(name=name), self.assertRaises(PortalError):
                safe_filename(name)
        for name in ("demo.tar.zst", "com0.zip", "conventional.zip", "x" * 200):
            with self.subTest(name=name):
                self.assertEqual(safe_filename(name), name)
