import base64
import http.client
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

FAKE_TOKEN = "fake-app-password-123"
FAKE_BASIC = base64.b64encode(("cole:" + FAKE_TOKEN).encode()).decode()


class Stub(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, handler):
        super().__init__(("127.0.0.1", 0), handler)
        self.seen: list[dict] = []
        self.thread = threading.Thread(target=self.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)

    @property
    def base(self) -> str:
        return "http://127.0.0.1:%d" % self.server_address[1]

    def stop(self) -> None:
        self.shutdown()
        self.server_close()
        self.thread.join(timeout=2)


def start(handler) -> Stub:
    try:
        stub = Stub(handler)
    except PermissionError:
        pytest.skip("the test sandbox does not permit a loopback listener")
    stub.thread.start()
    return stub


class Denier(BaseHTTPRequestHandler):
    status = 401
    headers: dict = {}
    body = b"<html>secret-ish page " + FAKE_TOKEN.encode() + b"</html>"

    def _answer(self):
        self.server.seen.append({"method": self.command, "path": self.path, "headers": dict(self.headers)})
        self.send_response(self.status)
        for key, value in self.headers_out.items():
            self.send_header(key, value)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()
        self.wfile.write(self.body)

    do_GET = do_POST = do_PATCH = do_DELETE = _answer

    def log_message(self, *args):
        pass


def denier(status: int, headers: dict | None = None) -> Stub:
    handler = type("H", (Denier,), {"status": status, "headers_out": headers or {}})
    return start(handler)


class Gate(BaseHTTPRequestHandler):
    # tests need a proxy double
    target = ("127.0.0.1", 0)

    def _forward(self):
        seen = {"method": self.command, "path": self.path, "headers": dict(self.headers)}
        self.server.seen.append(seen)
        if self.headers.get("Authorization") != "Basic " + FAKE_BASIC:
            self.send_response(401)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length") or 0)
        data = self.rfile.read(length) if length else None
        fwd = {key: value for key, value in self.headers.items() if key.lower() not in {"authorization", "content-length"}}
        fwd.update({"X-authentik-username": "cole", "X-authentik-meta-app": "jobseer", "X-authentik-meta-outpost": "local"})
        conn = http.client.HTTPConnection(*self.target, timeout=5)
        conn.request(self.command, self.path, body=data, headers=fwd)
        reply = conn.getresponse()
        payload = reply.read()
        self.send_response(reply.status)
        for key, value in reply.getheaders():
            if key.lower() in {"content-type", "set-cookie"}:
                self.send_header(key, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
        conn.close()

    do_GET = do_POST = do_PATCH = do_DELETE = _forward

    def log_message(self, *args):
        pass


def gate(dashboard) -> Stub:
    host, port = dashboard.server_address[:2]
    handler = type("G", (Gate,), {"target": (host, port)})
    return start(handler)
