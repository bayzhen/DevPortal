import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from dev_portal.client import PortalError
from dev_portal.download import download, _initial_url
from tests.fakes import BUILD, DLINK, DIGEST, PAYLOAD, TOKEN, FakeResponse, FakeTransport, handoff, range_response


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.destination = Path(self.temp.name) / "output"

    def _files(self):
        base = hashlib.sha256(BUILD.build_id.encode()).hexdigest()
        return (self.destination / ".devportal" / (base + ".part"),
                self.destination / ".devportal" / (base + ".json"))

    def test_full_direct_download_and_no_overwrite(self):
        seen = []
        def respond(method, url, headers):
            self.assertEqual(method, "GET")
            self.assertEqual(headers["User-Agent"], "pan.baidu.com")
            self.assertEqual(headers["Accept-Encoding"], "identity")
            self.assertEqual(url, DLINK + "&access_token=fake-secret%2B%2F%3D")
            return range_response(headers)
        fake = FakeTransport(respond)
        path = download(BUILD, handoff(), self.destination, transport=fake,
                        segment_bytes=7, progress=lambda done, total: seen.append((done, total)))
        self.assertEqual(path.read_bytes(), PAYLOAD)
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), DIGEST)
        self.assertEqual([x[0] for x in seen], [0, 7, 14, 21, 28])
        self.assertEqual(len(fake.calls), 4)
        self.assertFalse(self._files()[0].exists())
        self.assertFalse(self._files()[1].exists())
        with self.assertRaisesRegex(PortalError, "already exists"):
            download(BUILD, handoff(), self.destination, transport=fake, segment_bytes=7)
        self.assertEqual(len(fake.calls), 4)

    def test_interruption_then_fresh_handoff_resume(self):
        def initial(method, url, headers):
            if headers["Range"] == "bytes=0-6":
                return range_response(headers)
            return FakeResponse(403, b'{"access_token":"FAKE-SECRET"}')
        with self.assertRaises(PortalError):
            download(BUILD, handoff(), self.destination, transport=FakeTransport(initial), segment_bytes=7)
        partial, checkpoint = self._files()
        self.assertEqual(partial.read_bytes(), PAYLOAD[:7])
        self.assertEqual(json.loads(checkpoint.read_text())["offset"], 7)
        self.assertNotIn(TOKEN, checkpoint.read_text())
        self.assertNotIn(DLINK, checkpoint.read_text())
        resumed_ranges = []
        def resumed(method, url, headers):
            self.assertIn("access_token=fresh-token", url)
            resumed_ranges.append(headers["Range"])
            return range_response(headers)
        download(BUILD, handoff(token="fresh-token"), self.destination,
                 transport=FakeTransport(resumed), segment_bytes=7)
        self.assertEqual(resumed_ranges[0], "bytes=7-13")
        self.assertEqual((self.destination / BUILD.filename).read_bytes(), PAYLOAD)

    def test_refuses_bad_status_range_length_encoding_and_early_eof(self):
        def wrong_range(headers):
            response = range_response(headers)
            response.headers["content-range"] = f"bytes 1-3/{len(PAYLOAD)}"
            return response
        cases = {
            "200 JSON": lambda h: FakeResponse(200, b'{"error":"provider token"}'),
            "416": lambda h: FakeResponse(416, b"range expired"),
            "wrong range": wrong_range,
            "wrong total": lambda h: FakeResponse(206, b"1234", {"Content-Range": "bytes 0-3/1", "Content-Length": "4"}),
            "wrong length": lambda h: FakeResponse(206, b"1234", {"Content-Range": f"bytes 0-3/{len(PAYLOAD)}", "Content-Length": "2"}),
            "encoded": lambda h: FakeResponse(206, b"1234", {"Content-Range": f"bytes 0-3/{len(PAYLOAD)}", "Content-Length": "4", "Content-Encoding": "gzip"}),
            "JSON 206": lambda h: FakeResponse(206, b"null", {"Content-Range": f"bytes 0-3/{len(PAYLOAD)}", "Content-Length": "4", "Content-Type": "application/json"}),
            "short": lambda h: FakeResponse(206, b"123", {"Content-Range": f"bytes 0-3/{len(PAYLOAD)}", "Content-Length": "4"}),
            "extra": lambda h: FakeResponse(206, b"12345", {"Content-Range": f"bytes 0-3/{len(PAYLOAD)}", "Content-Length": "4"}),
        }
        for name, callback in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                folder = Path(tmp) / "out"
                fake = FakeTransport(lambda method, url, headers: callback(headers))
                with self.assertRaises(PortalError):
                    download(BUILD, handoff(), folder, transport=fake, segment_bytes=4)
                self.assertEqual(list(folder.glob("*.zip")), [])
                partial = next((folder / ".devportal").glob("*.part"))
                self.assertEqual(partial.read_bytes(), b"")

    def test_redirect_vetting_bounded_and_token_exception_redacted(self):
        for target in ("http://d.pcs.baidu.com/file", "https://badbaidupcs.com/file",
                       "https://evil.example/file", "https://user@d.pcs.baidu.com/file", "https://d.pcs.baidu.com:444/file"):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as tmp:
                fake = FakeTransport(lambda *_: FakeResponse(302, b"", {"Location": target}))
                with self.assertRaises(PortalError) as raised:
                    download(BUILD, handoff(), Path(tmp) / "out", transport=fake, segment_bytes=4)
                self.assertNotIn(TOKEN, str(raised.exception))
                self.assertEqual(len(fake.calls), 1)
        visited = []
        def trusted(method, url, headers):
            visited.append(url)
            if "d.pcs.baidu.com" in url:
                return FakeResponse(302, b"", {"Location": "https://d4.baidupcs.com/file?cdn=synthetic"})
            return range_response(headers)
        path = download(BUILD, handoff(), self.destination,
                        transport=FakeTransport(trusted), segment_bytes=28)
        self.assertEqual(path.read_bytes(), PAYLOAD)
        self.assertIn("d4.baidupcs.com", visited[-1])
        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeTransport(lambda *_: FakeResponse(302, b"", {"Location": "/repeat"}))
            with self.assertRaisesRegex(PortalError, "redirect"):
                download(BUILD, handoff(), Path(tmp) / "out", transport=fake, segment_bytes=4)
            self.assertEqual(len(fake.calls), 6)
        secret = "dangerous-url-token"
        def explode(method, url, headers):
            raise RuntimeError("failed " + url + " access_token=" + secret)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(PortalError) as raised:
                download(BUILD, handoff(token=secret), Path(tmp) / "out",
                         transport=FakeTransport(explode), segment_bytes=4)
            self.assertNotIn(secret, str(raised.exception))
            self.assertIsNone(raised.exception.__cause__)

    def test_provider_cdn_host_families_are_vetted_exactly(self):
        for host in ("d.pcs.baidu.com", "d4.baidupcs.com", "store.baidubce.com", "cdn.bdydns.com"):
            with self.subTest(host=host):
                self.assertTrue(_initial_url(f"https://{host}/file", TOKEN).startswith(f"https://{host}/file?access_token="))
        for host in ("baidu.com.evil.example", "evilbaidubce.com", "bdydns.com.evil.example"):
            with self.subTest(host=host), self.assertRaises(PortalError):
                _initial_url(f"https://{host}/file", TOKEN)

    def test_bad_link_never_opens_network_or_files(self):
        fake = FakeTransport(lambda *_: self.fail("Network should not open"))
        for link in ("http://d.pcs.baidu.com/file", "https://evil.example/file",
                     "https://d.pcs.baidu.com/file?access_token=stolen", "https://d.pcs.baidu.com@evil.example/f"):
            with self.subTest(link=link), self.assertRaises(PortalError):
                download(BUILD, handoff(dlink=link), self.destination, transport=fake)
        self.assertFalse(self.destination.exists())

    def test_corrupt_final_quarantines_and_blocks_retry(self):
        wrong = PAYLOAD[:-1] + b"?"
        fake = FakeTransport(lambda method, url, headers: range_response(headers, payload=wrong))
        with self.assertRaisesRegex(PortalError, "SHA-256"):
            download(BUILD, handoff(), self.destination, transport=fake, segment_bytes=7)
        self.assertFalse((self.destination / BUILD.filename).exists())
        partial, checkpoint = self._files()
        self.assertEqual(partial.read_bytes(), wrong)
        self.assertEqual(json.loads(checkpoint.read_text())["state"], "quarantined")
        with self.assertRaisesRegex(PortalError, "quarantined"):
            download(BUILD, handoff(), self.destination, transport=FakeTransport(lambda m, u, h: range_response(h)), segment_bytes=7)

    def test_prefix_drift_and_identity_change_refuse_append(self):
        def interrupted(method, url, headers):
            return range_response(headers) if headers["Range"].startswith("bytes=0-") else FakeResponse(403)
        with self.assertRaises(PortalError):
            download(BUILD, handoff(), self.destination, transport=FakeTransport(interrupted), segment_bytes=7)
        partial, checkpoint = self._files()
        with partial.open("r+b") as file:
            file.write(b"X")
        fake = FakeTransport(lambda m, u, h: self.fail("Network should not open"))
        with self.assertRaisesRegex(PortalError, "changed"):
            download(BUILD, handoff(), self.destination, transport=fake, segment_bytes=7)
        self.assertEqual(json.loads(checkpoint.read_text())["state"], "quarantined")
        with self.assertRaises(PortalError):
            download(BUILD, handoff(token="new"), self.destination, transport=fake, segment_bytes=7)

    def test_uncommitted_crash_bytes_trim_before_resume(self):
        def interrupted(method, url, headers):
            return range_response(headers) if headers["Range"].startswith("bytes=0-") else FakeResponse(403)
        with self.assertRaises(PortalError):
            download(BUILD, handoff(), self.destination, transport=FakeTransport(interrupted), segment_bytes=7)
        partial, checkpoint = self._files()
        with partial.open("ab") as file:
            file.write(b"uncommitted")
            file.flush()
            os.fsync(file.fileno())
        ranges = []
        def resume(method, url, headers):
            ranges.append(headers["Range"])
            return range_response(headers)
        download(BUILD, handoff(), self.destination, transport=FakeTransport(resume), segment_bytes=7)
        self.assertEqual(ranges[0], "bytes=7-13")
        self.assertEqual((self.destination / BUILD.filename).read_bytes(), PAYLOAD)

    def test_handoff_remote_identity_must_match_checkpoint(self):
        def interrupted(method, url, headers):
            return range_response(headers) if headers["Range"].startswith("bytes=0-") else FakeResponse(403)
        with self.assertRaises(PortalError):
            download(BUILD, handoff(), self.destination, transport=FakeTransport(interrupted), segment_bytes=7)
        handoff_changed = handoff()
        from dataclasses import replace
        handoff_changed = replace(handoff_changed, fs_id=98)
        fake = FakeTransport(lambda *_: self.fail("Network should not open"))
        with self.assertRaisesRegex(PortalError, "identity changed"):
            download(BUILD, handoff_changed, self.destination, transport=fake, segment_bytes=7)
        self.assertFalse((self.destination / BUILD.filename).exists())

    def test_hardlink_and_target_race_do_not_overwrite(self):
        def interrupted(method, url, headers):
            return range_response(headers) if headers["Range"].startswith("bytes=0-") else FakeResponse(403)
        with self.assertRaises(PortalError):
            download(BUILD, handoff(), self.destination, transport=FakeTransport(interrupted), segment_bytes=7)
        partial, checkpoint = self._files()
        outside = Path(self.temp.name) / "outside.bin"
        os.link(partial, outside)
        with self.assertRaisesRegex(PortalError, "Unsafe"):
            download(BUILD, handoff(), self.destination, transport=FakeTransport(lambda m, u, h: range_response(h)), segment_bytes=7)
        outside.unlink()
        target = self.destination / BUILD.filename
        def create_racing_target(done, total):
            if done == 7:
                target.write_bytes(b"unrelated-existing-file")
        with self.assertRaisesRegex(PortalError, "already exists"):
            download(BUILD, handoff(), self.destination,
                     transport=FakeTransport(lambda m, u, h: range_response(h)),
                     progress=create_racing_target, segment_bytes=7)
        self.assertEqual(target.read_bytes(), b"unrelated-existing-file")
        self.assertEqual(json.loads(checkpoint.read_text())["offset"], BUILD.size)
