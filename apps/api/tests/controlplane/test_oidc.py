"""OIDC sign-in (02 §6.3; 09b §8.2 MU-AUTH-002)."""

from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from opagent_testing.oidc import FakeOidcProvider

IDP = "https://idp.example"


@pytest.fixture
def idp(cp_app):
    p = FakeOidcProvider(issuer=IDP, clock=cp_app.clock)
    st = cp_app.state.opa
    st.oidc = {"issuer": IDP, "client_id": p.client_id, "client_secret": p.secret}
    st.oidc_transport = p.transport
    return p


def sign_in(cp_app, idp, email, sub, **kw):
    c = TestClient(cp_app, base_url="https://testserver")
    r = c.get("/api/v1/auth/oidc/start", follow_redirects=False)
    assert r.status_code == 303
    back = urlsplit(idp.authorize(r.headers["location"], email, sub, **kw))
    assert back.path == "/api/v1/auth/oidc/callback"
    return c, c.get(f"{back.path}?{back.query}", follow_redirects=False)


@pytest.mark.integration
@pytest.mark.security
@pytest.mark.tc("MU-AUTH-002")
def test_auth_002_oidc(cp_app, idp, login_as):
    owner = login_as("admin@example.invalid")
    r = owner.post("/api/v1/members", json={"email": "Carol@Example.invalid", "role": "ws_editor"})
    assert r.status_code == 201, r.text
    c, r = sign_in(cp_app, idp, "carol@example.invalid", "c-1")
    assert r.status_code == 303 and r.headers["location"] == "/", r.text
    assert "HttpOnly" in r.headers["set-cookie"] and "Secure" in r.headers["set-cookie"]
    me = c.get("/api/v1/auth/me").json()
    assert me["email"] == "carol@example.invalid" and me["roles"] == {"default": "ws_editor"}
    # bound to the IdP subject: a later e-mail change at the IdP still signs the same person in
    c2, r = sign_in(cp_app, idp, "carol.new@example.invalid", "c-1")
    assert r.status_code == 303 and c2.get("/api/v1/auth/me").json()["id"] == me["id"]
    # nobody signs up by signing in
    _, r = sign_in(cp_app, idp, "mallory@example.invalid", "m-1")
    assert r.status_code == 403 and r.json()["code"] == "OIDC_NOT_INVITED"
    owner.post("/api/v1/members", json={"email": "dave@example.invalid", "role": "ws_viewer"})
    _, r = sign_in(cp_app, idp, "dave@example.invalid", "d-1", email_verified=False)
    assert r.status_code == 403 and r.json()["code"] == "OIDC_EMAIL_UNVERIFIED"
    # state: unknown, and single use
    anon = TestClient(cp_app, base_url="https://testserver")
    assert anon.get("/api/v1/auth/oidc/callback?code=x&state=forged").json()["code"] == "OIDC_STATE_INVALID"
    s = anon.get("/api/v1/auth/oidc/start", follow_redirects=False)
    back = urlsplit(idp.authorize(s.headers["location"], "carol@example.invalid", "c-1"))
    assert anon.get(f"{back.path}?{back.query}", follow_redirects=False).status_code == 303
    assert anon.get(f"{back.path}?{back.query}", follow_redirects=False).json()["code"] == "OIDC_STATE_INVALID"
    # a wrong nonce or audience in the ID token is refused
    idp.tamper = {"nonce": "replayed"}
    _, r = sign_in(cp_app, idp, "carol@example.invalid", "c-1")
    assert r.status_code == 401 and r.json()["code"] == "OIDC_NONCE_INVALID"
    idp.tamper = {"aud": "some-other-app"}
    _, r = sign_in(cp_app, idp, "carol@example.invalid", "c-1")
    assert r.status_code == 401 and r.json()["code"] == "OIDC_TOKEN_INVALID"
    # the IdP rotates its signing key: the next login still verifies (JWKS refetched once)
    idp.rotate(keep_old=False)
    before = idp.calls.count("GET /jwks")
    _, r = sign_in(cp_app, idp, "carol@example.invalid", "c-1")
    assert r.status_code == 303 and idp.calls.count("GET /jwks") == before + 1
    _, r = sign_in(cp_app, idp, "carol@example.invalid", "c-1")
    assert r.status_code == 303 and idp.calls.count("GET /jwks") == before + 1  # cached again
