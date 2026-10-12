"""Injected offline HTTP responses: no live Baidu/DevHub credentials or packages."""

from __future__ import annotations

import hashlib
import json
import re

from dev_portal.client import Build, Handoff

PAYLOAD = b"synthetic-test-build-content"
DIGEST = hashlib.sha256(PAYLOAD).hexdigest()
BUILD = Build("build-one", "project-one", "Linux-Test.zip", "Linux", "Development", len(PAYLOAD), DIGEST)
TOKEN = "fake-secret+/="
DLINK = "https://d.pcs.baidu.com/file/test?sig=A%2B%2F&expires=42"


def handoff(dlink: str = DLINK, token: str = TOKEN) -> Handoff:
    return Handoff(BUILD.build_id, 97, "/apps/gdue/builds/Linux-Test.zip", dlink, token,
                   BUILD.filename, BUILD.size, BUILD.sha256, BUILD.platform)


def public_build():
    return BUILD.public()


def public_handoff(dlink: str = DLINK, token: str = TOKEN):
    return {"build_id": BUILD.build_id, "fs_id": 97, "remote_path": "/apps/gdue/builds/Linux-Test.zip",
            "dlink": dlink, "access_token": token, "filename": BUILD.filename,
            "size": BUILD.size, "sha256": BUILD.sha256, "platform": BUILD.platform}


class FakeResponse:
    def __init__(self, status: int, content: bytes = b"", headers: dict | None = None):
        self.status = status
        self.content = content
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}
        self.position = 0
        self.closed = False

    def getheader(self, name):
        return self.headers.get(name.lower())

    def read(self, size=-1):
        if size < 0:
            size = len(self.content) - self.position
        result = self.content[self.position:self.position + size]
        self.position += len(result)
        return result

    def close(self):
        self.closed = True


class FakeTransport:
    def __init__(self, callback):
        self.callback = callback
        self.calls = []

    def open(self, method, url, headers, *, timeout):
        self.calls.append((method, url, dict(headers), timeout))
        return self.callback(method, url, headers)


def json_response(data, status=200):
    body = json.dumps(data).encode()
    return FakeResponse(status, body, {"Content-Type": "application/json", "Content-Length": str(len(body))})


def range_response(headers, payload=PAYLOAD, status=206):
    match = re.fullmatch(r"bytes=(\d+)-(\d+)", headers["Range"])
    assert match is not None
    start, end = map(int, match.groups())
    content = payload[start:end + 1]
    return FakeResponse(status, content, {"Content-Range": f"bytes {start}-{end}/{len(payload)}",
                                          "Content-Length": str(len(content))})
