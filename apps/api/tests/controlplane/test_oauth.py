"""MCP over HTTP with the built-in OAuth 2.1 AS (ADR-0015; 09b §8.6)."""

import base64
import hashlib
import json
import secrets
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as hs
from opagent_api import oauth
from opagent_api.permissions import ROLES, capped_scopes
from opagent_mcp.scopes import ALL_SCOPES

ISS = "https://testserver"
RES = ISS + "/mcp"
CID = "https://client.example.com/oauth/client.json"
REDIRECT = "http://127.0.0.1:33418/callback"
PUBLIC_IP = "93.184.216.34"
META = {
    "client_id": CID,
    "client_name": "Example Assistant",
    "redirect_uris": [REDIRECT],
    "token_endpoint_auth_method": "none",
    "grant_types": ["authorization_code", "refresh_token"],
    "response_types": ["code"],
}


class Hosts:
    """Fake DNS + fake client-metadata host; records every request that would have left the CP."""

    def __init__(self):
        self.dns = {"client.example.com": [PUBLIC_IP]}
        self.pages = {("client.example.com", "/oauth/client.json"): (200, {}, json.dumps(META).encode())}
        self.requests = []

    def resolve(self, host, port):
        return self.dns.get(host, [])

    def handle(self, request):
        self.requests.append(request)
        key = (request.headers["host"], request.url.path)
        status, headers, body = self.pages.get(key, (404, {}, b""))
        return httpx.Response(status, headers=headers, content=body)


@pytest.fixture
def hosts(cp_app):
    h = Hosts()
    st = cp_app.state.opa
    st.oauth_resolver, st.oauth_transport = h.resolve, httpx.MockTransport(h.handle)
    return h


def pkce():
    v = secrets.token_urlsafe(48)
    return v, base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).decode().rstrip("=")


def params(challenge, scope="photos:read", **over):
    p = {
        "response_type": "code",
        "client_id": CID,
        "redirect_uri": REDIRECT,
        "scope": scope,
        "state": "st-1",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "resource": RES,
    }
    p.update(over)
    return {k: v for k, v in p.items() if v is not None}


def consent(user, p):
    return user.get("/oauth/authorize", params=p)


def approve(user, p):
    page = consent(user, p)
    assert page.status_code == 200, page.text
    r = user.post(
        "/oauth/authorize", data={**p, "decision": "approve", "csrf": page.json()["csrf"]}, follow_redirects=False
    )
    assert r.status_code == 303, r.text
    loc = urlsplit(r.headers["location"])
    q = parse_qs(loc.query)
    assert f"{loc.scheme}://{loc.netloc}{loc.path}" == REDIRECT
    assert q["iss"] == [ISS] and q["state"] == ["st-1"]  # RFC 9207
    return q["code"][0]


def exchange(c, code, verifier, **over):
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT,
        "client_id": CID,
        "code_verifier": verifier,
        "resource": RES,
    }
    data.update(over)
    return c.post("/oauth/token", data={k: v for k, v in data.items() if v is not None})


def refresh(c, rt):
    return c.post(
        "/oauth/token", data={"grant_type": "refresh_token", "refresh_token": rt, "client_id": CID, "resource": RES}
    )


def tokens(cp_app, user, scope="photos:read"):
    v, ch = pkce()
    r = exchange(TestClient(cp_app, base_url=ISS), approve(user, params(ch, scope)), v)
    assert r.status_code == 200, r.text
    return r.json()


def call(cp_app, access, name="search_photos", args=None):
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": args or {"query": "x"}},
    }
    h = {"Authorization": f"Bearer {access}"} if access else {}
    return TestClient(cp_app, base_url=ISS).post("/mcp", json=body, headers=h)


@pytest.mark.integration
@pytest.mark.tc("MU-OAUTH-002")
def test_oauth_002_protected_resource_metadata(cp_app, hosts):
    anon = TestClient(cp_app, base_url=ISS)
    prm = anon.get("/.well-known/oauth-protected-resource").json()
    assert prm["resource"] == RES and prm["authorization_servers"] == [ISS]
    assert "organize:apply" not in prm["scopes_supported"]
    assert anon.get("/.well-known/oauth-protected-resource/mcp").json() == prm
    r = call(cp_app, None)
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == f'Bearer resource_metadata="{ISS}/.well-known/oauth-protected-resource"'
    md = anon.get("/.well-known/oauth-authorization-server").json()
    assert md["issuer"] == ISS and md["code_challenge_methods_supported"] == ["S256"]
    assert md["grant_types_supported"] == ["authorization_code", "refresh_token"]
    assert md["token_endpoint_auth_methods_supported"] == ["none"]
    assert md["authorization_response_iss_parameter_supported"] is True


@pytest.mark.integration
@pytest.mark.tc("MU-OAUTH-007", "MU-OAUTH-001")
def test_oauth_007_end_to_end_discovery(cp_app, hosts, as_role):
    user = as_role("ws_editor")
    anon = TestClient(cp_app, base_url=ISS)
    r = call(cp_app, None)  # 1. unauthenticated → where to look
    prm_url = r.headers["www-authenticate"].split('resource_metadata="')[1].rstrip('"')
    prm = anon.get(urlsplit(prm_url).path).json()  # 2. PRM → the AS
    md = anon.get(urlsplit(prm["authorization_servers"][0]).path + "/.well-known/oauth-authorization-server").json()
    assert urlsplit(md["authorization_endpoint"]).path == "/oauth/authorize"
    page = consent(user, params(pkce()[1], "photos:read labels:write"))
    assert page.json()["client_domain"] == "client.example.com"  # the domain, not only the self-declared name
    assert page.json()["client_name"] == "Example Assistant"
    t = tokens(cp_app, user)  # 3–4. authorize with resource → token
    claims = jwt.decode(t["access_token"], options={"verify_signature": False})
    assert claims["aud"] == RES and claims["iss"] == ISS and claims["exp"] - claims["iat"] == 900
    ok = call(cp_app, t["access_token"])  # 5. tools/call
    assert ok.status_code == 200 and ok.json()["result"].get("isError") is not True, ok.text
    deny = call(cp_app, t["access_token"], "update_labels", {"photo_ids": ["a"], "add": ["x"]})
    assert deny.status_code == 403 and 'error="insufficient_scope"' in deny.headers["www-authenticate"]
    assert 'scope="labels:write"' in deny.headers["www-authenticate"]
    # MU-OAUTH-001: revoking the grant fails the very next call
    gid = user.get("/api/v1/oauth/grants").json()[0]["id"]
    assert user.delete(f"/api/v1/oauth/grants/{gid}").status_code == 200
    r = call(cp_app, t["access_token"])
    assert r.status_code == 401 and 'error="invalid_token"' in r.headers["www-authenticate"]
    assert refresh(anon, t["refresh_token"]).status_code == 400


@pytest.mark.integration
@pytest.mark.security
@pytest.mark.tc("MU-OAUTH-003")
def test_oauth_003_scope_and_audience(cp_app, hosts, as_role):
    admin = as_role("ws_admin")  # even a role that may apply never gets organize:apply through OAuth
    page = consent(admin, params(pkce()[1], "photos:read organize:apply organize:propose"))
    assert "organize:apply" not in page.json()["scopes"] and "organize:propose" in page.json()["scopes"]
    t = tokens(cp_app, admin, "photos:read organize:apply organize:propose")
    assert "organize:apply" not in t["scope"].split()
    claims = jwt.decode(t["access_token"], options={"verify_signature": False})
    assert "organize:apply" not in claims["scope"].split()
    key = oauth._key(cp_app.state.opa)
    for bad in (
        {**claims, "aud": "https://other.example/mcp"},  # another resource's token
        {**claims, "iss": "https://evil.example"},
    ):
        assert call(cp_app, jwt.encode(bad, key, algorithm="HS256")).status_code == 401
    # a forged organize:apply claim is still not honoured: the tool is not callable
    forged = jwt.encode({**claims, "scope": "photos:read organize:apply"}, key, algorithm="HS256")
    r = call(cp_app, forged, "apply_organize", {"plan_id": "p"})
    assert r.status_code == 403 and 'scope="organize:apply"' in r.headers["www-authenticate"]
    # a worker token or the raw CP secret do not pass as MCP tokens
    raw = jwt.encode(claims, cp_app.state.opa.jwt_secret, algorithm="HS256")
    assert call(cp_app, raw).status_code == 401


@pytest.mark.integration
@pytest.mark.security
@pytest.mark.tc("MU-OAUTH-004")
def test_oauth_004_pkce_and_redirect_strict(cp_app, hosts, as_role):
    user = as_role("ws_editor")
    v, ch = pkce()
    assert consent(user, params(None, code_challenge_method=None)).status_code == 400  # no PKCE
    assert consent(user, params(v, code_challenge_method="plain")).status_code == 400  # plain
    for uri in (
        REDIRECT + "/",
        REDIRECT[:-1],
        "http://127.0.0.1:33418/other",
        REDIRECT.replace("127.0.0.1", "localhost"),
    ):
        r = consent(user, params(ch, redirect_uri=uri))
        assert r.status_code == 400, uri
    r = user.post(
        "/oauth/authorize", data={**params(ch, redirect_uri=REDIRECT + "/"), "decision": "approve", "csrf": "x"}
    )
    assert r.status_code in (400, 403) and "location" not in r.headers  # never redirected to an unregistered URI
    assert consent(user, params(ch, resource="https://other.example/mcp")).status_code == 400
    code = approve(user, params(ch))
    anon = TestClient(cp_app, base_url=ISS)
    assert exchange(anon, code, "wrong-verifier-" + "x" * 40).status_code == 400
    code = approve(user, params(ch))
    first = exchange(anon, code, v)
    assert first.status_code == 200
    assert call(cp_app, first.json()["access_token"]).status_code == 200
    again = exchange(anon, code, v)  # the same code a second time
    assert again.status_code == 400 and again.json()["error"] == "invalid_grant"
    assert call(cp_app, first.json()["access_token"]).status_code == 401  # what the code issued is revoked
    assert refresh(anon, first.json()["refresh_token"]).status_code == 400


@pytest.mark.integration
@pytest.mark.security
@pytest.mark.tc("MU-OAUTH-005")
def test_oauth_005_refresh_rotation_and_reuse(cp_app, hosts, as_role):
    user = as_role("ws_editor")
    anon = TestClient(cp_app, base_url=ISS)
    t1 = tokens(cp_app, user)
    r2 = refresh(anon, t1["refresh_token"])
    assert r2.status_code == 200
    t2 = r2.json()
    assert t2["refresh_token"] != t1["refresh_token"]
    assert call(cp_app, t2["access_token"]).status_code == 200
    reuse = refresh(anon, t1["refresh_token"])  # the rotated-away token comes back: stolen
    assert reuse.status_code == 400 and reuse.json()["error"] == "invalid_grant"
    assert call(cp_app, t2["access_token"]).status_code == 401  # the whole family is gone
    assert refresh(anon, t2["refresh_token"]).status_code == 400
    assert user.get("/api/v1/oauth/grants").json()[0]["revoked"] is True


@pytest.mark.integration
@pytest.mark.security
@pytest.mark.tc("MU-OAUTH-006")
def test_oauth_006_cimd_ssrf(cp_app, hosts, as_role):
    for addr in (
        "127.0.0.1",
        "10.1.2.3",
        "169.254.169.254",
        "::1",
        "192.168.1.10",
        "172.16.0.1",
        "fc00::1",
        "::ffff:10.0.0.1",
    ):
        hosts.dns["evil.example"] = [addr]
        with pytest.raises(oauth.CimdRejected):
            oauth.fetch_client_metadata("https://evil.example/c.json", hosts.resolve, httpx.MockTransport(hosts.handle))
    hosts.dns["mixed.example"] = [PUBLIC_IP, "10.0.0.1"]  # one private answer is enough to refuse
    for url in (
        "https://mixed.example/c.json",
        "https://10.0.0.5/c.json",  # a literal private address
        "http://client.example.com/oauth/client.json",  # not https
        "https://user:pw@client.example.com/oauth/client.json",
        "https://client.example.com/",  # no path
    ):
        with pytest.raises(oauth.CimdRejected):
            oauth.fetch_client_metadata(url, hosts.resolve, httpx.MockTransport(hosts.handle))
    assert hosts.requests == []  # none of these ever reached the network
    hosts.dns["redir.example"] = [PUBLIC_IP]
    hosts.pages[("redir.example", "/c.json")] = (302, {"location": "http://10.0.0.1/internal"}, b"")
    hosts.dns["big.example"] = [PUBLIC_IP]
    hosts.pages[("big.example", "/c.json")] = (200, {}, b'{"client_id": "' + b"x" * 70_000 + b'"}')
    hosts.dns["liar.example"] = [PUBLIC_IP]
    hosts.pages[("liar.example", "/c.json")] = (200, {}, json.dumps({**META, "client_id": CID}).encode())
    for url in ("https://redir.example/c.json", "https://big.example/c.json", "https://liar.example/c.json"):
        with pytest.raises(oauth.CimdRejected):
            oauth.fetch_client_metadata(url, hosts.resolve, httpx.MockTransport(hosts.handle))
    assert [str(r.url.host) for r in hosts.requests] == [PUBLIC_IP] * 3  # pinned to the checked address
    assert all(r.extensions.get("sni_hostname") == r.headers["host"] for r in hosts.requests)
    assert not any("10.0.0.1" in str(r.url) for r in hosts.requests)  # the redirect was not followed
    # through the authorize endpoint: a client hosted on a private address is simply unknown
    user = as_role("ws_editor")
    hosts.dns["evil.example"] = ["10.9.9.9"]
    r = consent(user, params(pkce()[1], client_id="https://evil.example/c.json"))
    assert r.status_code == 400 and r.json()["error"] == "invalid_client"


ROLE_NAMES = sorted(ROLES)


class FakeState:
    def __init__(self):
        from opagent_testing.clock import FakeClock

        self.clock, self.jwt_secret, self.public_url = FakeClock(), "k" * 40, ISS

    @property
    def root(self):
        return self


@pytest.mark.security
@pytest.mark.tc("MU-OAUTH-008")
@settings(max_examples=300, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(hs.lists(hs.sampled_from([*ALL_SCOPES, "admin", "organize:apply"]), max_size=12), hs.sampled_from(ROLE_NAMES))
def test_oauth_008_never_organize_apply(scopes, role):
    perms = ROLES[role]
    capped = capped_scopes(scopes, perms)
    assert "organize:apply" not in capped
    client = oauth.Client(CID, META)
    assert "organize:apply" not in client.get_allowed_scope(" ".join(scopes)).split()
    assert "organize:apply" not in oauth.supported_scopes()
    g = oauth.Grantee("u", "g", "default", [*capped, "organize:apply"], RES)  # even a corrupted grant row
    srv = object.__new__(oauth.Server)
    srv.st = FakeState()
    tok = srv._tokens("authorization_code", client, g, " ".join([*scopes, "organize:apply"]))
    assert "organize:apply" not in tok["scope"].split()
    assert set(tok["scope"].split()) <= set(capped)
