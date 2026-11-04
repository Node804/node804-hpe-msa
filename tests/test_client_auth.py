"""Tests for MSAClient auth: login, session reuse, MD5 fallback, re-login.

Uses ``respx`` to mock the httpx transport so no live array is required.
"""

from __future__ import annotations

import hashlib
from typing import Any

import httpx
import respx
from node804_mcp_toolkit import TlsConfig

from hpe_msa_mcp.client import MSAClient, ResolveError

HOST = "msa.test"
BASE = f"https://{HOST}/api"
USER = "manage"
PW = "secret"


def _hashes() -> tuple[str, str]:
    cred = f"{USER}_{PW}".encode()
    return hashlib.sha256(cred).hexdigest(), hashlib.md5(cred).hexdigest()


def _envelope(objects_key: str, objects: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        objects_key: objects,
        "status": [{"response-type": "Success", "response": "", "return-code": 0}],
    }


def _login_ok(session_key: str) -> dict[str, Any]:
    return {"status": [{"response-type": "Success", "response": session_key, "return-code": 1}]}


def _login_err() -> dict[str, Any]:
    return {
        "status": [
            {"response-type": "Error", "response": "Authentication failure", "return-code": -1}
        ]
    }


def _client(hosts: list[str] | None = None) -> MSAClient:
    return MSAClient(
        name="t",
        hosts=hosts or [HOST],
        username=USER,
        password=PW,
        tls=TlsConfig(verify=False),
    )


@respx.mock
def test_login_success_and_session_reuse() -> None:
    sha, _ = _hashes()
    login_route = respx.get(f"{BASE}/login/{sha}").mock(
        return_value=httpx.Response(200, json=_login_ok("KEY1"))
    )
    show_route = respx.get(f"{BASE}/show/system").mock(
        return_value=httpx.Response(200, json=_envelope("system", [{"system-name": "san-a"}]))
    )

    client = _client()
    r1 = client.request(["show", "system"])
    r2 = client.request(["show", "system"])

    assert not isinstance(r1, ResolveError)
    assert r1["system"][0]["system-name"] == "san-a"
    assert not isinstance(r2, ResolveError)
    # Logged in exactly once; the second request reused the cached session key.
    assert login_route.call_count == 1
    assert show_route.call_count == 2
    # The session-key header was sent on the show call.
    assert show_route.calls[0].request.headers["sessionKey"] == "KEY1"


@respx.mock
def test_md5_fallback_when_sha256_rejected() -> None:
    sha, md5 = _hashes()
    sha_route = respx.get(f"{BASE}/login/{sha}").mock(
        return_value=httpx.Response(200, json=_login_err())
    )
    md5_route = respx.get(f"{BASE}/login/{md5}").mock(
        return_value=httpx.Response(200, json=_login_ok("KEYMD5"))
    )
    respx.get(f"{BASE}/show/system").mock(
        return_value=httpx.Response(200, json=_envelope("system", [{"system-name": "old-fw"}]))
    )

    client = _client()
    result = client.request(["show", "system"])

    assert not isinstance(result, ResolveError)
    assert sha_route.called
    assert md5_route.called


@respx.mock
def test_login_failure_returns_resolve_error() -> None:
    sha, md5 = _hashes()
    respx.get(f"{BASE}/login/{sha}").mock(return_value=httpx.Response(200, json=_login_err()))
    respx.get(f"{BASE}/login/{md5}").mock(return_value=httpx.Response(200, json=_login_err()))

    client = _client()
    result = client.request(["show", "system"])

    assert isinstance(result, ResolveError)
    assert "failed" in result.message.lower()


@respx.mock
def test_relogin_on_expired_session() -> None:
    sha, _ = _hashes()
    login_route = respx.get(f"{BASE}/login/{sha}").mock(
        side_effect=[
            httpx.Response(200, json=_login_ok("KEY1")),
            httpx.Response(200, json=_login_ok("KEY2")),
        ]
    )
    expired = {
        "status": [{"response-type": "Error", "response": "session expired", "return-code": -10005}]
    }
    show_route = respx.get(f"{BASE}/show/system").mock(
        side_effect=[
            httpx.Response(401, json=expired),
            httpx.Response(200, json=_envelope("system", [{"system-name": "san-a"}])),
        ]
    )

    client = _client()
    result = client.request(["show", "system"])

    assert not isinstance(result, ResolveError)
    assert result["system"][0]["system-name"] == "san-a"
    # Logged in twice (initial + re-login) and retried the show once.
    assert login_route.call_count == 2
    assert show_route.call_count == 2
    # The retry carried the fresh session key.
    assert show_route.calls[1].request.headers["sessionKey"] == "KEY2"


@respx.mock
def test_transport_error_is_redacted_resolve_error() -> None:
    sha, _ = _hashes()
    respx.get(f"{BASE}/login/{sha}").mock(side_effect=httpx.ConnectError("boom"))

    client = _client()
    result = client.request(["show", "system"])
    assert isinstance(result, ResolveError)
    assert "could not reach" in result.message.lower()


@respx.mock
def test_host_failover_when_first_controller_down() -> None:
    # Controller A (host1) is unreachable; the client fails over to B (host2).
    host1, host2 = "msa-a.test", "msa-b.test"
    sha, _ = _hashes()
    respx.get(f"https://{host1}/api/login/{sha}").mock(side_effect=httpx.ConnectError("down"))
    login2 = respx.get(f"https://{host2}/api/login/{sha}").mock(
        return_value=httpx.Response(200, json=_login_ok("KEY2"))
    )
    show2 = respx.get(f"https://{host2}/api/show/system").mock(
        return_value=httpx.Response(200, json=_envelope("system", [{"system-name": "san-a"}]))
    )

    client = _client(hosts=[host1, host2])
    result = client.request(["show", "system"])

    assert not isinstance(result, ResolveError)
    assert result["system"][0]["system-name"] == "san-a"
    assert login2.called
    assert show2.called
    assert client.host == host2  # settled on the reachable controller


@respx.mock
def test_all_hosts_down_reports_every_host() -> None:
    host1, host2 = "msa-a.test", "msa-b.test"
    sha, _ = _hashes()
    respx.get(f"https://{host1}/api/login/{sha}").mock(side_effect=httpx.ConnectError("down"))
    respx.get(f"https://{host2}/api/login/{sha}").mock(side_effect=httpx.ConnectError("down"))

    client = _client(hosts=[host1, host2])
    result = client.request(["show", "system"])
    assert isinstance(result, ResolveError)
    assert host1 in result.message and host2 in result.message
