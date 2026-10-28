import base64
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

ISSUER = "https://issuer.example.test/o/app/"
AUDIENCE = "client-abc"
SUBJECT = "someone"
SCOPE = "bridge:use"


def make_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def public_jwk(key, kid):
    return {**RSAAlgorithm.to_jwk(key.public_key(), as_dict=True), "kid": kid, "use": "sig", "alg": "RS256"}


def make_token(key, kid="k1", **overrides):
    now = int(time.time())
    claims = {"iss": ISSUER, "aud": AUDIENCE, "sub": SUBJECT, "scope": "openid " + SCOPE, "iat": now, "exp": now + 600}
    claims.update(overrides)
    for name in [name for name, value in claims.items() if value is None]:
        del claims[name]
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": kid})


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def forge(header: dict, claims: dict, sign=None) -> str:
    body = b64(json.dumps(header).encode()) + "." + b64(json.dumps(claims).encode())
    return body + "." + (b64(sign(body.encode())) if sign else "")


def public_pem(key) -> bytes:
    return key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)


class JwksStub(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                outer.hits += 1
                if outer.down:
                    self.send_response(503)
                    self.end_headers()
                    return
                body = json.dumps({"keys": list(outer.keys)}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        super().__init__(("127.0.0.1", 0), Handler)
        self.keys: list[dict] = []
        self.hits = 0
        self.down = False
        self.thread = threading.Thread(target=self.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return "http://127.0.0.1:%d/jwks" % self.server_address[1]

    def stop(self) -> None:
        self.shutdown()
        self.server_close()
        self.thread.join(timeout=2)
