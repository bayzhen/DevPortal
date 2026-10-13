import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest

from dev_portal.client import HubClient
from dev_portal.server import PortalServer
from tests.fakes import BUILD, DIGEST, PAYLOAD, TOKEN, FakeTransport, json_response, public_build, public_handoff, range_response


class LocalPageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.calls = []
        def hub_response(method, url, headers):
            self.calls.append((method, url))
            if method == "GET" and url.endswith("/portal/v1/builds"):
                return json_response({"builds": [public_build()]})
            if method == "POST" and url.endswith("/portal/v1/builds/build-one/download"):
                return json_response(public_handoff())
            self.fail("Unexpected DevHub request")
        self.transfer = FakeTransport(lambda method, url, headers: range_response(headers))
        self.server = PortalServer(HubClient("https://hub.example", "portal-key-safe",
                                              FakeTransport(hub_response)),
                                   Path(self.temp.name) / "downloads", transfer=self.transfer)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def request(self, method, path, *, origin=None, csrf=None, host=None, payload=None, fetch_site=None):
        headers = {"Host": host or f"127.0.0.1:{self.server.server_port}"}
        if origin is not None:
            headers["Origin"] = origin
        if csrf is not None:
            headers["X-DevPortal-CSRF"] = csrf
        if fetch_site is not None:
            headers["Sec-Fetch-Site"] = fetch_site
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload).encode()
        else:
            body = b"" if method == "POST" else None
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        try:
            conn.request(method, path, body=body, headers=headers)
            result = conn.getresponse()
            return result.status, result.read(), dict(result.getheaders())
        finally:
            conn.close()

    def test_page_and_catalog_hide_handoff_secrets(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        status, page, headers = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(self.server.csrf.encode(), page)
        self.assertNotIn(TOKEN.encode(), page)
        self.assertNotIn(b"dlink", page)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        status, assets, headers = self.request("GET", "/static/app.js")
        self.assertEqual(status, 200)
        self.assertNotIn(TOKEN.encode(), assets)
        status, catalog, headers = self.request("GET", "/api/builds")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(catalog), {"builds": [public_build()]})
        self.assertNotIn(TOKEN.encode(), catalog)
        self.assertNotIn(b"dlink", catalog)
        self.assertEqual(self.calls, [("GET", "https://hub.example/portal/v1/builds")])

    def test_cross_origin_wrong_host_and_missing_csrf_never_handoff(self):
        malicious = "https://attacker.example"
        for origin, csrf, host, site in (
            (malicious, self.server.csrf, None, None),
            (None, self.server.csrf, None, None),
            (self.server.origin, "bad", None, None),
            (self.server.origin, self.server.csrf, "localhost:" + str(self.server.server_port), None),
            (self.server.origin, self.server.csrf, None, "cross-site"),
        ):
            with self.subTest(origin=origin, csrf=csrf, host=host, site=site):
                status, body, _ = self.request("POST", "/api/download", origin=origin,
                                               csrf=csrf, host=host,
                                               payload={"build_id": BUILD.build_id}, fetch_site=site)
                self.assertEqual(status, 403)
                self.assertNotIn(TOKEN.encode(), body)
        status, _, _ = self.request("GET", "/api/builds", origin=malicious)
        self.assertEqual(status, 403)
        self.assertEqual(self.calls, [])
        self.assertFalse((Path(self.temp.name) / "downloads").exists())

    def test_full_list_handoff_local_file_flow_without_hub_file_proxy(self):
        status, catalog, _ = self.request("GET", "/api/builds")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(catalog)["builds"][0]["platform"], "Linux")
        done = threading.Event()
        worker = self.server._worker
        def wrapped(build_id):
            try:
                worker(build_id)
            finally:
                done.set()
        self.server._worker = wrapped
        status, body, _ = self.request("POST", "/api/download",
                                       origin=self.server.origin, csrf=self.server.csrf,
                                       payload={"build_id": BUILD.build_id})
        self.assertEqual(status, 202, body)
        self.assertTrue(done.wait(4), "local transfer worker did not finish")
        status, response, _ = self.request("GET", "/api/progress")
        self.assertEqual(status, 200)
        state = json.loads(response)
        self.assertEqual(state["state"], "complete", state)
        self.assertEqual(state["bytes_done"], len(PAYLOAD))
        self.assertNotIn(TOKEN.encode(), response)
        self.assertEqual((Path(self.temp.name) / "downloads" / BUILD.filename).read_bytes(), PAYLOAD)
        self.assertEqual([method for method, _ in self.calls], ["GET", "GET", "POST"])
        self.assertTrue(all(url.startswith("https://hub.example/portal/v1/builds") for _, url in self.calls))
        self.assertTrue(all(url.startswith("https://d.pcs.baidu.com/") for _, url, _, _ in self.transfer.calls))

    def test_project_label_is_public_only_and_empty_catalog_is_not_invented(self):
        status, page, _ = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b'id="project-name"', page)
        status, script, _ = self.request("GET", "/static/app.js")
        self.assertEqual(status, 200)
        self.assertIn(b'projectName.textContent', script)
        self.assertIn(b'projects.length > 1', script)
        self.assertNotIn(TOKEN.encode(), page + script)
        self.assertNotIn(b'portal-key-safe', page + script)

    def test_unknown_build_and_malformed_action_are_bounded(self):
        status, body, _ = self.request("POST", "/api/download", origin=self.server.origin,
                                       csrf=self.server.csrf, payload={"build_id": "other", "dlink": "malicious"})
        self.assertEqual(status, 400)
        self.assertEqual(self.calls, [])
        done = threading.Event()
        worker = self.server._worker
        def wrapped(build_id):
            try:
                worker(build_id)
            finally:
                done.set()
        self.server._worker = wrapped
        status, _, _ = self.request("POST", "/api/download", origin=self.server.origin,
                                    csrf=self.server.csrf, payload={"build_id": "other"})
        self.assertEqual(status, 202)
        self.assertTrue(done.wait(3))
        status, response, _ = self.request("GET", "/api/progress")
        self.assertEqual(json.loads(response)["state"], "failed")
        self.assertEqual(self.calls, [("GET", "https://hub.example/portal/v1/builds")])
