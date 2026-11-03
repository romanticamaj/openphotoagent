"""A fake OpenID Connect provider for control-plane login tests (09b §8 MU-AUTH-002).

Authorization code + PKCE, RS256 ID tokens with a ``kid``, a JWKS endpoint and key rotation. It runs in-process as
an ``httpx.MockTransport`` (the CP's back-channel) plus ``authorize()`` (what the browser would do at the IdP).
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa


class FakeOidcProvider:
    def __init__(
        self,
        issuer: str = "https://idp.example",
        client_id: str = "opagent",
        secret: str = "idp-secret",  # noqa: S107 - a test double
        clock: Any = None,
    ) -> None:
        self.issuer, self.client_id, self.secret, self.clock = issuer, client_id, secret, clock
        self.keys: list[tuple[str, Any]] = []
        self.rotate()
        self.codes: dict[str, dict[str, Any]] = {}
        self.calls: list[str] = []
        self.tamper: dict[str, Any] = {}  # claim overrides for the next ID token (bad nonce, other audience …)

    def rotate(self, keep_old: bool = True) -> str:
        kid = "k" + secrets.token_hex(4)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.keys = ([*self.keys] if keep_old else []) + [(kid, key)]
        return kid

    def drop_old_keys(self) -> None:
        self.keys = self.keys[-1:]

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def authorize(self, url: str, email: str, sub: str, email_verified: bool = True) -> str:
        """The person signs in at the IdP; returns where the IdP sends the browser back to."""
        u = urlsplit(url)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        assert f"{u.scheme}://{u.netloc}{u.path}" == self.issuer + "/authorize"
        assert q["client_id"] == self.client_id and q["response_type"] == "code"
        assert q["code_challenge_method"] == "S256" and q.get("code_challenge")
        code = secrets.token_urlsafe(16)
        self.codes[code] = {
            "q": q,
            "claims": {"sub": sub, "email": email, "email_verified": email_verified, "nonce": q.get("nonce")},
        }
        return f"{q['redirect_uri']}?code={code}&state={q['state']}"

    def _now(self) -> int:
        return int(self.clock.now().timestamp()) if self.clock is not None else 2_000_000_000

    def _handle(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(f"{req.method} {req.url.path}")
        if req.url.path == "/.well-known/openid-configuration":
            return httpx.Response(
                200,
                json={
                    "issuer": self.issuer,
                    "authorization_endpoint": self.issuer + "/authorize",
                    "token_endpoint": self.issuer + "/token",
                    "jwks_uri": self.issuer + "/jwks",
                    "id_token_signing_alg_values_supported": ["RS256"],
                    "code_challenge_methods_supported": ["S256"],
                },
            )
        if req.url.path == "/jwks":
            keys = []
            for kid, k in self.keys:
                jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(k.public_key()))
                keys.append({**jwk, "kid": kid, "use": "sig", "alg": "RS256"})
            return httpx.Response(200, json={"keys": keys})
        if req.url.path == "/token" and req.method == "POST":
            form = {k: v[0] for k, v in parse_qs(req.content.decode()).items()}
            entry = self.codes.pop(form.get("code", ""), None)
            if entry is None or form.get("client_secret") != self.secret or form.get("client_id") != self.client_id:
                return httpx.Response(400, json={"error": "invalid_grant"})
            want = entry["q"]["code_challenge"]
            got = base64.urlsafe_b64encode(hashlib.sha256(form.get("code_verifier", "").encode()).digest()).decode()
            if got.rstrip("=") != want or form.get("redirect_uri") != entry["q"]["redirect_uri"]:
                return httpx.Response(400, json={"error": "invalid_grant"})
            now = self._now()
            claims = {"iss": self.issuer, "aud": self.client_id, "iat": now, "exp": now + 300, **entry["claims"]}
            claims.update(self.tamper)
            self.tamper = {}
            kid, key = self.keys[-1]
            idt = jwt.encode(claims, key, algorithm="RS256", headers={"kid": kid})
            return httpx.Response(200, json={"access_token": "at", "token_type": "Bearer", "id_token": idt})
        return httpx.Response(404)
