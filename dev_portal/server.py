"""Loopback-only web controls; browser sees catalog/progress, never provider handoffs."""

from __future__ import annotations

import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
import json
from pathlib import Path
import secrets
import threading
from urllib.parse import urlsplit

from .client import HubClient, PortalError
from .download import download


class PortalServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, hub: HubClient, destination: Path, port: int = 0, *, transfer=None):
        # Hostname is not caller-configurable: do not expose the local control service.
        super().__init__(("127.0.0.1", port), PortalHandler)
        self.hub = hub
        self.destination = destination
        self.transfer = transfer
        self.csrf = secrets.token_urlsafe(32)
        self.state = {"state": "idle", "build_id": None, "filename": None,
                      "bytes_done": 0, "total_bytes": 0, "error": None}
        self.state_lock = threading.Lock()

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.server_port}"

    def progress(self) -> dict:
        with self.state_lock:
            return dict(self.state)

    def request_download(self, build_id: str) -> bool:
        with self.state_lock:
            if self.state["state"] in ("queued", "downloading"):
                return False
            self.state = {"state": "queued", "build_id": build_id, "filename": None,
                          "bytes_done": 0, "total_bytes": 0, "error": None}
        thread = threading.Thread(target=self._worker, args=(build_id,), daemon=True)
        thread.start()
        return True

    def _worker(self, build_id: str) -> None:
        try:
            builds = self.hub.builds()
            build = next((item for item in builds if item.build_id == build_id), None)
            if build is None:
                raise PortalError("Build is not available")
            handoff = self.hub.handoff(build)
            with self.state_lock:
                self.state.update(state="downloading", filename=build.filename, total_bytes=build.size)
            def progress(done: int, total: int) -> None:
                with self.state_lock:
                    self.state.update(bytes_done=done, total_bytes=total)
            download(build, handoff, self.destination, transport=self.transfer, progress=progress)
            with self.state_lock:
                self.state.update(state="complete")
        except PortalError as exc:
            with self.state_lock:
                self.state.update(state="failed", error=str(exc))
        except Exception:
            # A transport exception may include an authenticated URL: do not render/log it.
            with self.state_lock:
                self.state.update(state="failed", error="Download failed; retry to resume")


class PortalHandler(BaseHTTPRequestHandler):
    server: PortalServer

    def log_message(self, format: str, *args: object) -> None:
        # BaseHTTPRequestHandler otherwise logs user-supplied request paths/query strings.
        return

    def _allowed(self, *, mutation: bool = False) -> bool:
        if self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}":
            return False
        if not self.path.startswith("/") or self.path.startswith("//") or urlsplit(self.path).query or urlsplit(self.path).fragment:
            return False
        origin = self.headers.get("Origin")
        if origin and origin != self.server.origin:
            return False
        if self.headers.get("Sec-Fetch-Site", "").lower() == "cross-site":
            return False
        if mutation:
            return (
                origin == self.server.origin
                and hmac.compare_digest(self.headers.get("X-DevPortal-CSRF", ""), self.server.csrf)
            )
        return True

    def _send(self, code: int, data: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, code: int, data: dict) -> None:
        self._send(code, json.dumps(data, ensure_ascii=True, separators=(",", ":")).encode(), "application/json; charset=utf-8")

    def do_GET(self) -> None:
        if not self._allowed():
            self._json(403, {"error": "Local request denied"})
            return
        if self.path == "/":
            template = files("dev_portal").joinpath("static/index.html").read_text(encoding="utf-8")
            content = template.replace("@@CSRF@@", self.server.csrf).encode()
            self._send(200, content, "text/html; charset=utf-8")
        elif self.path == "/static/app.css" or self.path == "/static/app.js":
            name = self.path.rsplit("/", 1)[-1]
            data = files("dev_portal").joinpath("static", name).read_bytes()
            mime = "text/css" if name.endswith(".css") else "text/javascript"
            self._send(200, data, mime + "; charset=utf-8")
        elif self.path == "/api/builds":
            try:
                builds = self.server.hub.builds()
                self._json(200, {"builds": [item.public() for item in builds]})
            except PortalError:
                self._json(502, {"error": "Build catalog unavailable"})
            except Exception:
                self._json(502, {"error": "Build catalog unavailable"})
        elif self.path == "/api/progress":
            self._json(200, self.server.progress())
        else:
            self._json(404, {"error": "Not found"})

    def do_POST(self) -> None:
        if not self._allowed(mutation=True):
            self._json(403, {"error": "Local request denied"})
            return
        if self.path != "/api/download":
            self._json(404, {"error": "Not found"})
            return
        length = self.headers.get("Content-Length", "")
        if not length.isdigit() or int(length) > 512 or self.headers.get("Content-Type", "").lower() != "application/json":
            self._json(400, {"error": "Invalid request"})
            return
        try:
            data = json.loads(self.rfile.read(int(length)))
        except (ValueError, UnicodeError):
            self._json(400, {"error": "Invalid request"})
            return
        if not isinstance(data, dict) or set(data) != {"build_id"} or not isinstance(data["build_id"], str) or len(data["build_id"]) > 128:
            self._json(400, {"error": "Invalid request"})
            return
        if not self.server.request_download(data["build_id"]):
            self._json(409, {"error": "A download is already active"})
            return
        self._json(202, {"state": "queued"})
