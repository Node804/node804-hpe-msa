"""Secret-redaction helpers for error messages and log lines.

The HPE MSA REST API authenticates in two stages that both put secrets into
request URLs and headers:

  1. Login hashes ``username_password`` and sends it as a URL path segment
     (``GET /api/login/<hash>``). httpx and the underlying transport can echo
     that full URL into exception strings on a TLS or transport failure, so
     the login hash (from which the password can be brute-forced offline) can
     leak into logs.
  2. Every subsequent call carries a ``sessionKey`` header. Some error paths
     stringify the request (including headers), surfacing the live session key.

This module scrubs both from arbitrary text. Every code path that turns an
exception into a log line or a tool response runs the result through here.

The redaction is intentionally conservative — regex-only, so there is no
parsing surface to abuse, and it preserves surrounding context so log readers
can still see where the event happened.
"""

from __future__ import annotations

import re
from typing import Final

REDACTED: Final = "***REDACTED***"

# ``/api/login/<hash>`` — the hash is a hex SHA-256 (64 chars) or hex MD5 (32
# chars). Match a generous hex run after the login path so either firmware's
# hash is scrubbed, value only, path structure preserved.
_LOGIN_HASH_RE: Final = re.compile(
    r"(?P<prefix>/api/login/)(?P<val>[0-9a-fA-F]{16,})",
)

# ``sessionKey`` however it is spelled in a header dump or repr:
# ``sessionKey: ABC...``, ``'sessionKey': 'ABC...'``, ``sessionKey=ABC...``.
_SESSION_KEY_RE: Final = re.compile(
    r"(?P<name>sessionKey)(?P<sep>['\"]?\s*[:=]\s*['\"]?)(?P<val>[A-Za-z0-9+/=_\-]{8,})",
    flags=re.IGNORECASE,
)

# Bare ``password=...`` / ``password: ...`` assignments that can appear in a
# config repr or a stray debug line. Defense in depth — the password should
# never reach a log, but if it does, scrub it.
_PASSWORD_RE: Final = re.compile(
    r"(?P<name>password)(?P<sep>['\"]?\s*[:=]\s*['\"]?)(?P<val>[^\s'\"<>&]+)",
    flags=re.IGNORECASE,
)


def redact_secrets(text: str) -> str:
    """Strip login hashes, session keys, and passwords from a string.

    Safe to call on any string — if no secret patterns match, the input is
    returned unchanged. Cheap enough to apply liberally on every error path.
    """
    if not text:
        return text
    text = _LOGIN_HASH_RE.sub(lambda m: f"{m.group('prefix')}{REDACTED}", text)
    text = _SESSION_KEY_RE.sub(lambda m: f"{m.group('name')}{m.group('sep')}{REDACTED}", text)
    text = _PASSWORD_RE.sub(lambda m: f"{m.group('name')}{m.group('sep')}{REDACTED}", text)
    return text


def redact_exception(exc: BaseException) -> str:
    """Render an exception as a redacted string suitable for logs/responses.

    Falls back to the class name when ``str(exc)`` is empty, so a bare
    exception type still produces a useful message.
    """
    return redact_secrets(str(exc)) or exc.__class__.__name__


__all__ = ["REDACTED", "redact_exception", "redact_secrets"]
