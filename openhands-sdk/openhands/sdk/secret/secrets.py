"""Secret sources and types for handling sensitive data."""

import os
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Annotated
from urllib.parse import urljoin, urlsplit

import httpx
from pydantic import Field, SecretStr, field_validator

from openhands.sdk.logger import get_logger
from openhands.sdk.utils.models import DiscriminatedUnionMixin
from openhands.sdk.utils.pydantic_secrets import SecretField
from openhands.sdk.utils.redact import is_secret_key


logger = get_logger(__name__)

_INTERNAL_SERVER_URL_ENV = "OH_INTERNAL_SERVER_URL"
_DEFAULT_INTERNAL_SERVER_URL = "http://127.0.0.1:8000"


def _resolve_lookup_secret_url(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.netloc or parsed.scheme:
        return url

    base_url = os.getenv(_INTERNAL_SERVER_URL_ENV, _DEFAULT_INTERNAL_SERVER_URL)
    return urljoin(f"{base_url.rstrip('/')}/", url)


LocalSecretResolver = Callable[[str], str | None]

_local_secret_resolvers: list[LocalSecretResolver] = []


def register_local_secret_resolver(resolver: LocalSecretResolver) -> None:
    """Register an in-process resolver for ``LookupSecret`` URLs.

    A process that both serves and consumes ``LookupSecret`` URLs — an
    agent-server running an in-process conversation, say — cannot fetch them
    over HTTP from its own event loop: the request can only be answered by the
    loop that is blocked waiting for it, so it stalls until the client times
    out. Registering a resolver lets such a process answer its own URLs
    directly and skip the round trip.

    The resolver receives the fully-resolved URL and returns the secret value,
    or ``None`` when it does not serve that URL, in which case resolution falls
    through to the next resolver and finally to HTTP.
    """
    if resolver not in _local_secret_resolvers:
        _local_secret_resolvers.append(resolver)


def unregister_local_secret_resolver(resolver: LocalSecretResolver) -> None:
    """Remove a resolver registered with ``register_local_secret_resolver``."""
    if resolver in _local_secret_resolvers:
        _local_secret_resolvers.remove(resolver)


def _resolve_secret_locally(url: str) -> str | None:
    """Return a locally-served value for ``url``, or ``None`` if there is none."""
    for resolver in list(_local_secret_resolvers):
        try:
            value = resolver(url)
        except Exception:
            logger.warning(
                "Local secret resolver raised; falling back to HTTP", exc_info=True
            )
            continue
        if value is not None:
            return value
    return None


class SecretSource(DiscriminatedUnionMixin, ABC):
    """Source for a named secret which may be obtained dynamically"""

    description: str | None = Field(
        default=None,
        description="Optional description for this secret",
    )
    scope: str | None = Field(
        default=None,
        description=(
            "Optional grouping name shown to the agent when rendering the "
            "available secrets (e.g. 'personal', 'organization'). The SDK "
            "attaches no meaning to the value; it only groups by it."
        ),
    )
    scope_description: str | None = Field(
        default=None,
        description=(
            "Optional guidance shown once above the secret's scope group when "
            "rendering secrets, e.g. who actions are attributed to. Only the "
            "first non-empty value within a scope is used; conflicts log a "
            "warning."
        ),
    )

    @abstractmethod
    def get_value(self) -> str | None:
        """Get the value of a secret in plain text"""


class StaticSecret(SecretSource):
    """A secret stored locally"""

    value: Annotated[SecretStr | None, SecretField()] = None

    def get_value(self) -> str | None:
        if self.value is None:
            return None
        return self.value.get_secret_value()


class LookupSecret(SecretSource):
    """A secret looked up from some external url"""

    url: str
    # Only headers whose name looks secret (see ``is_secret_key``) are secrets.
    # When one cannot be decrypted it keeps its value: a client may send a
    # plaintext auth header in a request that is otherwise marked encrypted.
    headers: Annotated[
        dict[str, str],
        SecretField(undecryptable="keep", secret_keys=is_secret_key),
    ] = Field(default_factory=dict)

    @field_validator("url")
    @classmethod
    def _normalize_url(cls, url: str) -> str:
        return _resolve_lookup_secret_url(url)

    def get_value(self) -> str:
        local_value = _resolve_secret_locally(self.url)
        if local_value is not None:
            return local_value
        response = httpx.get(self.url, headers=self.headers, timeout=30.0)
        response.raise_for_status()
        return response.text


# Type alias for secret values - can be a plain string or a SecretSource
SecretValue = str | SecretSource
