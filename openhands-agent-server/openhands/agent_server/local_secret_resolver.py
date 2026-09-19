"""Serve this server's own ``LookupSecret`` URLs without a loopback request.

``LookupSecret`` resolves lazily over HTTP so raw values never transit an SDK
client. When the client and the server are the same process, that round trip
buys nothing and costs correctness: a resolution on the event loop blocks the
very loop that would answer it, so it stalls until the client times out.
Resolving those URLs against the local store keeps the laziness and drops the
round trip.
"""

from urllib.parse import urlsplit

from openhands.agent_server.config import Config
from openhands.agent_server.persistence import get_secrets_store
from openhands.sdk.logger import get_logger
from openhands.sdk.secret import (
    register_local_secret_resolver,
    unregister_local_secret_resolver,
)


logger = get_logger(__name__)

_SECRET_PATH_PREFIX = "/api/settings/secrets/"
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})


def _secret_name_if_local(url: str) -> str | None:
    """Return the secret name when ``url`` is one this process serves itself."""
    parsed = urlsplit(url)
    if parsed.hostname not in _LOOPBACK_HOSTS:
        return None
    if not parsed.path.startswith(_SECRET_PATH_PREFIX):
        return None
    name = parsed.path[len(_SECRET_PATH_PREFIX) :]
    # Only a bare name; anything deeper is a different route.
    if not name or "/" in name:
        return None
    return name


def build_local_secret_resolver(config: Config):
    """Build a resolver that answers this server's own secret URLs."""

    def resolve(url: str) -> str | None:
        name = _secret_name_if_local(url)
        if name is None:
            return None
        value = get_secrets_store(config).get_secret(name)
        if value is None:
            # Fall through to HTTP so the caller still sees the server's 404.
            return None
        logger.debug("Resolved secret '%s' in-process", name)
        return value

    return resolve


class local_secret_resolution:
    """Context manager registering the in-process resolver for a server."""

    def __init__(self, config: Config) -> None:
        self._resolver = build_local_secret_resolver(config)

    def __enter__(self) -> None:
        register_local_secret_resolver(self._resolver)

    def __exit__(self, *exc_info) -> None:
        unregister_local_secret_resolver(self._resolver)
