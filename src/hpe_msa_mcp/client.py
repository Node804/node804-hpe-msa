"""HTTP client for the HPE MSA REST management API, with session caching.

Unlike the SDK-backed servers in the suite (panos wraps ``pan-os-python``),
HPE MSA ships no Python SDK, so this module talks to the array's REST API
directly over ``httpx``.

MSA auth is a **session key**, not a long-lived API key:

  1. Hash ``"<username>_<password>"`` (SHA-256, MD5 fallback for old firmware).
  2. ``GET /api/login/<hash>`` returns a ``sessionKey`` in its status object.
  3. Every subsequent call sends that key as the ``sessionKey`` header.
  4. The key idles out (~30 min); an expired-session response triggers one
     transparent re-login + retry.

So this client owns login, session-key caching, and re-login-on-expiry.
:class:`MSAClient` instances are cached for the server-process lifetime, keyed
by array name, so a session logs in once per array and reuses the key.

Errors are returned as :class:`ResolveError` (``.message``) rather than raised,
so tools surface a clean string to the model instead of a stack trace. Every
error string passes through :func:`redact_exception` so a leaked login hash or
session key never reaches a log.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
from dataclasses import dataclass, field
from typing import Any

import httpx
from node804_mcp_toolkit import TlsConfig

from ._redact import redact_exception, redact_secrets
from .config.arrays import ArrayEntry, resolve_array

# A single stuck request should fail fast rather than hang the whole MCP
# process — every read tool calls the array synchronously, so an unbounded
# request blocks even local tools like server_status. Bounded per request,
# overridable via MSA_API_TIMEOUT.
_DEFAULT_API_TIMEOUT = 30


def _api_timeout() -> float:
    """Per-request API timeout in seconds (override via ``MSA_API_TIMEOUT``)."""
    raw = os.environ.get("MSA_API_TIMEOUT")
    if raw:
        try:
            value = float(raw)
            if value > 0:
                return value
        except ValueError:
            pass
    return _DEFAULT_API_TIMEOUT


@dataclass(frozen=True)
class ResolveError:
    """Returned when a request can't be completed (resolve/login/transport).

    Tools surface ``ResolveError.message`` to the model rather than raising,
    so the caller gets a clean, already-redacted error string.
    """

    message: str


# MSA marks an expired/invalid session with this return code; the response
# also comes back as HTTP 401 on most firmware. Either triggers one re-login.
_SESSION_EXPIRED_CODES = frozenset({-10005, -10000})


def _build_verify(tls: TlsConfig) -> bool | str:
    """Translate our TLS config into httpx's ``verify`` argument."""
    if not tls.verify:
        return False
    if tls.ca_path is not None and tls.ca_bundle is not None:
        return str(tls.ca_path)
    return True


def _extract_status(payload: Any) -> dict[str, Any]:
    """Return the trailing ``status`` object from an MSA response, or ``{}``.

    Every MSA response ends with a ``status`` array describing the command
    result; the last element is the authoritative one.
    """
    if not isinstance(payload, dict):
        return {}
    status = payload.get("status")
    if isinstance(status, list) and status:
        last = status[-1]
        return last if isinstance(last, dict) else {}
    if isinstance(status, dict):
        return status
    return {}


def _status_is_error(status: dict[str, Any]) -> bool:
    """True when a status object reports a command failure."""
    rtype = str(status.get("response-type", "")).strip().lower()
    if rtype == "error":
        return True
    # Some firmware omits response-type but sets a negative return-code.
    code = status.get("return-code")
    return isinstance(code, int) and code < 0


def _status_is_expired(status: dict[str, Any]) -> bool:
    """True when a status object indicates an expired/invalid session."""
    code = status.get("return-code")
    if isinstance(code, int) and code in _SESSION_EXPIRED_CODES:
        return True
    response = str(status.get("response", "")).lower()
    return "session" in response and ("expired" in response or "invalid" in response)


@dataclass
class MSAClient:
    """A logged-in connection to one MSA array, cached for the process lifetime.

    ``hosts`` is the failover-ordered list of management addresses for the one
    array (a VIP, or the two controller IPs). Login walks the list and settles
    on the first host that answers; that host is then used for every subsequent
    request until the session expires, at which point failover runs again.
    """

    name: str
    hosts: list[str]
    username: str
    password: str
    tls: TlsConfig
    timeout: float = field(default_factory=_api_timeout)
    _active_index: int = field(default=0, init=False, repr=False)
    _session_key: str | None = field(default=None, init=False, repr=False)
    _http: httpx.Client | None = field(default=None, init=False, repr=False)

    # --- HTTP plumbing ------------------------------------------------------

    @property
    def host(self) -> str:
        """The management address currently in use (the active failover host)."""
        return self.hosts[self._active_index]

    @property
    def base_url(self) -> str:
        return f"https://{self.host}/api"

    def _http_client(self) -> httpx.Client:
        if self._http is None or self._http.is_closed:
            self._http = httpx.Client(
                base_url=self.base_url,
                verify=_build_verify(self.tls),
                timeout=self.timeout,
                headers={"datatype": "json"},
            )
        return self._http

    def _activate_host(self, index: int) -> None:
        """Switch the active host, rebuilding the HTTP client against its base URL."""
        if index != self._active_index and self._http is not None and not self._http.is_closed:
            self._http.close()
            self._http = None
        self._active_index = index

    def _login_hashes(self) -> list[str]:
        """Login hashes to try, in order: SHA-256 first, MD5 fallback.

        Gen5/Gen6 support SHA-256; only much older firmware is MD5-only. We try
        the strong hash first and fall back so a mixed/old array still works.
        """
        credential = f"{self.username}_{self.password}".encode()
        return [
            hashlib.sha256(credential).hexdigest(),
            hashlib.md5(credential).hexdigest(),
        ]

    # --- Auth ---------------------------------------------------------------

    def login(self) -> ResolveError | None:
        """Authenticate and cache the session key. Returns None on success.

        Tries each configured host in order. A host that can't be reached
        (transport failure) advances to the next; a host that answers with an
        authentication error stops the walk immediately, because the same
        credentials would fail on every host of the same array.
        """
        last_transport_error = "unreachable"
        for index in range(len(self.hosts)):
            self._activate_host(index)
            outcome = self._login_current_host()
            if outcome is None:
                return None  # success — active host + session key are set
            kind, message = outcome
            if kind == "transport":
                last_transport_error = message
                continue  # this host is down; try the next
            # Definitive auth/response error — same creds fail on every host.
            return ResolveError(
                f"Login to MSA array '{self.name}' failed (tried SHA-256 and MD5): {message}"
            )
        hosts = ", ".join(self.hosts)
        return ResolveError(
            f"Could not reach MSA array '{self.name}' on any host ({hosts}): {last_transport_error}"
        )

    def _login_current_host(self) -> tuple[str, str] | None:
        """Attempt login against the active host.

        Returns None on success; ``("transport", msg)`` when the host is
        unreachable; ``("auth", msg)`` when the host answered but rejected the
        credentials (or returned an unparseable/again-erroring response).
        """
        http = self._http_client()
        last_auth_error = "authentication failed"
        for digest in self._login_hashes():
            try:
                resp = http.get(f"/login/{digest}")
            except httpx.HTTPError as exc:
                return ("transport", redact_exception(exc))
            payload = self._parse_json(resp)
            if isinstance(payload, ResolveError):
                last_auth_error = payload.message
                continue
            status = _extract_status(payload)
            if not _status_is_error(status):
                key = status.get("response")
                if isinstance(key, str) and key:
                    self._session_key = key
                    return None
            last_auth_error = redact_secrets(str(status.get("response", "authentication failed")))
        return ("auth", last_auth_error)

    # --- Requests -----------------------------------------------------------

    def request(self, segments: list[str]) -> dict[str, Any] | ResolveError:
        """Run a ``GET /api/<segments...>`` call, logging in as needed.

        ``segments`` are the already-validated path components after ``/api``
        (e.g. ``["show", "disks"]``). On an expired session the client
        re-logs-in once and retries; any second failure is returned as a
        :class:`ResolveError`.

        Returns the parsed response dict (envelope intact, including the
        trailing ``status`` array) so the caller can inspect status before
        trusting the payload.
        """
        if self._session_key is None:
            err = self.login()
            if err is not None:
                return err

        result = self._get(segments)
        if isinstance(result, ResolveError):
            return result
        payload, http_status = result

        status = _extract_status(payload)
        if http_status == 401 or _status_is_expired(status):
            # Session went stale — re-login once and retry.
            self._session_key = None
            err = self.login()
            if err is not None:
                return err
            retry = self._get(segments)
            if isinstance(retry, ResolveError):
                return retry
            payload, _ = retry

        return payload

    def _get(self, segments: list[str]) -> tuple[dict[str, Any], int] | ResolveError:
        """Single GET with the session-key header. Returns (payload, http_status)."""
        http = self._http_client()
        path = "/" + "/".join(segments)
        headers = {"sessionKey": self._session_key} if self._session_key else {}
        try:
            resp = http.get(path, headers=headers)
        except httpx.HTTPError as exc:
            return ResolveError(
                f"Request to MSA array '{self.name}' failed: {redact_exception(exc)}"
            )
        payload = self._parse_json(resp)
        if isinstance(payload, ResolveError):
            return payload
        return payload, resp.status_code

    @staticmethod
    def _parse_json(resp: httpx.Response) -> dict[str, Any] | ResolveError:
        """Parse a response body as JSON, or return a redacted error."""
        try:
            data = resp.json()
        except ValueError:
            snippet = redact_secrets(resp.text[:200])
            return ResolveError(
                f"MSA returned a non-JSON response (HTTP {resp.status_code}): {snippet}"
            )
        if not isinstance(data, dict):
            return ResolveError("MSA returned an unexpected (non-object) JSON response.")
        return data

    def close(self) -> None:
        """Close the underlying HTTP client. Best-effort; used on shutdown/tests."""
        if self._http is not None and not self._http.is_closed:
            # Logout is best-effort — MSA idles the session out anyway.
            with contextlib.suppress(httpx.HTTPError):
                self._http.get("/exit", headers={"sessionKey": self._session_key or ""})
            self._http.close()


# --- Client cache -----------------------------------------------------------

_CLIENTS: dict[str, MSAClient] = {}
"""Cache of constructed clients keyed by array name. Lifetime = server process."""


def get_client(name: str | None, *, tls: TlsConfig) -> MSAClient | ResolveError:
    """Return a cached, logged-in client for the named array.

    Resolves the array from the registry (or the ``MSA_HOST`` env fallback),
    constructs a client if one isn't cached, and logs it in. Returns
    :class:`ResolveError` instead of raising so tools can surface a clean
    message without try/except.
    """
    entry = resolve_array(name)
    if entry is None:
        if name is None:
            from .config.arrays import is_multi_array

            if is_multi_array():
                return ResolveError(
                    "Multiple arrays are configured — specify which one with the "
                    "'array' parameter (use list_arrays to see configured targets)."
                )
            return ResolveError(
                "No MSA array configured. Set MSA_HOST/MSA_USERNAME/MSA_PASSWORD or "
                "create an arrays.json with at least one entry."
            )
        return ResolveError(
            f"MSA array '{name}' not found. Use list_arrays to see configured targets."
        )

    cached = _CLIENTS.get(entry.name)
    if cached is not None:
        return cached

    client = _build_client(entry, tls)
    err = client.login()
    if err is not None:
        return err
    _CLIENTS[entry.name] = client
    return client


def _build_client(entry: ArrayEntry, tls: TlsConfig) -> MSAClient:
    """Construct (without logging in) an MSAClient for a resolved entry."""
    assert entry.password is not None  # resolve_array only returns entries with a password
    return MSAClient(
        name=entry.name,
        hosts=list(entry.hosts),
        username=entry.username,
        password=entry.password,
        tls=tls,
    )


def reset_client_cache() -> None:
    """Drop all cached clients. Used by tests; rarely needed at runtime."""
    for client in _CLIENTS.values():
        client.close()
    _CLIENTS.clear()


def probe_client(name: str | None, *, tls: TlsConfig) -> dict[str, Any]:
    """Light-touch metadata for ``server_status``: confirms a target resolves.

    Does NOT make a network call — that would slow startup and add a network
    dependency to a status tool. Just confirms the config-side resolution
    chain works.
    """
    entry = resolve_array(name)
    if entry is None:
        return {"resolved": False}
    return {"resolved": True, "name": entry.name, "host": entry.host}


__all__ = [
    "MSAClient",
    "ResolveError",
    "get_client",
    "probe_client",
    "reset_client_cache",
]
