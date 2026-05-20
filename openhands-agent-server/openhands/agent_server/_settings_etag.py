"""ETag and ``If-Match`` helpers for settings endpoints.

The legacy ``PATCH /api/settings`` had no version stamp and no
``If-Match`` support, so concurrent writes silently lost each other (the
file lock only prevented on-disk corruption, not lost updates). The new
typed endpoints emit an ``ETag`` on every read and write and accept
``If-Match`` for optimistic concurrency.

ETag stability
--------------

The ETag is computed over a *plaintext-canonical* projection of the
persisted settings, **not** over the encrypted bytes on disk. Fernet
includes a fresh nonce on every encryption, so two saves of the same
state produce different ciphertexts — hashing those would defeat the
ETag entirely (identical PATCHes would each look like a change).
"""

from __future__ import annotations

import hashlib
import json

from fastapi import HTTPException, status

from openhands.agent_server.persistence import PersistedSettings


def compute_settings_etag(settings: PersistedSettings) -> str:
    """Compute a stable, quoted ETag over the plaintext-canonical state.

    Hashes a sort-keyed, separator-canonical JSON of the plaintext
    serialisation. Returns a quoted hex string suitable for the
    ``ETag``/``If-Match`` HTTP headers.
    """
    canonical = settings.model_dump(
        mode="json", context={"expose_secrets": "plaintext"}
    )
    blob = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(blob).hexdigest()[:32]
    return f'"{digest}"'


def parse_if_match(if_match: str | None) -> set[str] | None:
    """Parse an ``If-Match`` header value into a set of acceptable ETags.

    Returns ``None`` when no precondition is supplied. Returns an empty
    sentinel when the wildcard ``*`` is supplied (caller treats this as
    "resource must exist", which always holds because settings default).
    """
    if if_match is None:
        return None
    candidates = {tok.strip() for tok in if_match.split(",") if tok.strip()}
    if "*" in candidates:
        return {"*"}
    return candidates


def check_if_match(if_match: str | None, current_etag: str) -> None:
    """Raise ``412 Precondition Failed`` if ``If-Match`` does not match.

    Behaviour
    ---------
    * ``If-Match`` absent: no precondition is enforced — the request is
      allowed through. (Strict mode is left to deployments that wrap this
      with middleware; we default to permissive so existing single-client
      flows keep working.)
    * ``If-Match: *``: precondition succeeds because the settings
      resource always exists (defaults are returned when no file is on
      disk).
    * ``If-Match: "etag"`` (comma-separated list allowed): the current
      ETag must be in the set, otherwise 412.

    The response carries the current ETag back to the client so it can
    retry against the live state.
    """
    candidates = parse_if_match(if_match)
    if candidates is None or "*" in candidates:
        return
    if current_etag not in candidates:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail=(
                "If-Match does not match the current settings ETag. "
                "Re-fetch /api/settings and retry with the new ETag."
            ),
            headers={"ETag": current_etag},
        )


__all__ = ["check_if_match", "compute_settings_etag", "parse_if_match"]
