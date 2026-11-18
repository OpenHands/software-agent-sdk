"""Persistence for shared LLM provider connections.

A *provider connection* is a small, named bundle of the credential material an
LLM profile would otherwise carry inline: an ``api_key`` and an optional
``base_url``. Several LLM profiles can reference one connection by id, so
rotating the shared key in one place updates every profile that points at it.

This lives in the SDK — next to :class:`LLMProfileStore` — on purpose. The
profile store resolves a profile's ``provider_connection_id`` into concrete
credentials at load time (see :meth:`LLMProfileStore.load`), and it can only do
that if the connection store is reachable from the same layer. Keeping it here
(rather than in the agent-server) means every path that turns a stored profile
into a runnable :class:`~openhands.sdk.llm.llm.LLM` — named-profile activation
*and* the default/seed launch path — resolves identically.

The credential is encrypted at rest with the same cipher machinery LLM profiles
use, so the connection file never holds a plaintext key when ``OH_SECRET_KEY``
is configured.

A connection's ``provider`` also decides routing when it names a provider the
SDK recognizes (see :meth:`ProviderConnection.route_model`). For a model, the
route is chosen in this order:

1. A recognized connection ``provider``. The profile's model is read as that
   provider's own id and qualified for LiteLLM; an id that already carries the
   prefix is kept. The host of the connection's ``base_url`` is not consulted,
   so a proxy works too.

   - ``openrouter``: ``thinkingmachines/inkling-small:free`` ->
     ``openrouter/thinkingmachines/inkling-small:free``.
   - OpenAI-compatible servers (``custom_openai``, ``openai_like``,
     ``lm_studio``, ``hosted_vllm``, ``llamafile``): the id reaches the server
     verbatim, even one like ``ollama/llama3`` whose first segment LiteLLM
     would otherwise read as a provider.

2. Any other label (``custom``, ``openai``, ``anthropic``, or any free-text
   value saved before this rule existed) leaves the model as stored, so the
   model's own LiteLLM prefix decides, as before.
3. The endpoint: an ``openrouter.ai`` base URL routes through OpenRouter, and
   with any other base URL an id LiteLLM cannot place is sent to it as an
   OpenAI-compatible request (see :meth:`LLMProvider.from_model`).

A model whose prefix names another SDK route (``openhands/``,
``litellm_proxy/``, ``digitalocean/``) under a recognized connection is rejected
with :class:`ProviderConnectionModelConflict` rather than routed elsewhere.
"""

from __future__ import annotations

import json
import re
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Final

from filelock import FileLock, Timeout
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
)

from openhands.sdk.errors import Invalid, LockTimeout, Unprocessable
from openhands.sdk.llm.utils.openhands_provider import (
    CUSTOM_OPENAI_PROVIDER_PREFIX,
    DIGITALOCEAN_PROVIDER_PREFIX,
    LITELLM_PROXY_PREFIX,
    OPENAI_COMPATIBLE_PROVIDER_PREFIXES,
    OPENHANDS_PROVIDER_PREFIX,
    OPENROUTER_PROVIDER_PREFIX,
)
from openhands.sdk.logger import get_logger
from openhands.sdk.utils.pydantic_secrets import (
    SecretField,
    cipher_context,
    client_context,
)


if TYPE_CHECKING:
    from openhands.sdk.utils.cipher import Cipher


_DEFAULT_DIR: Final[Path] = Path.home() / ".openhands" / "provider-connections"
_FILENAME: Final[str] = "provider_connections.json"
_LOCK_TIMEOUT_SECONDS: Final[float] = 30.0

PROVIDER_CONNECTIONS_SCHEMA_VERSION: Final[int] = 1
MAX_PROVIDER_CONNECTIONS: Final[int] = 64

# Connection ids: 1-128 chars, alphanumeric start, then alphanumeric/._-.
# Same shape as profile names — blocks path separators and leading dots.
CONNECTION_ID_PATTERN: Final[str] = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
CONNECTION_ID_REGEX: Final[re.Pattern[str]] = re.compile(CONNECTION_ID_PATTERN)

logger = get_logger(__name__)


class ProviderConnectionLimitExceeded(Exception):
    """Raised when creating a connection would exceed the configured limit."""


class ProviderConnectionNotFound(Unprocessable):
    """A referenced provider connection id does not exist.

    Raised by :meth:`LLMProfileStore.load` when a profile points at a connection
    that has been deleted and the profile has no usable inline key to fall back
    on.

    An :class:`~openhands.sdk.errors.Unprocessable` (so also a
    :class:`ValueError`) so every ``load()`` caller degrades sensibly even
    without a dedicated ``except``. The agent server answers it with 422 from
    the domain error table, and the OpenAI-compatible
    gateway's ``except ValueError`` turns it into a 400 instead of an opaque
    500. The agent-profile launch path already funnels load failures through
    its resolver's ``ValueError`` handling; that path can't actually hit a
    dangling reference, since deleting a referenced connection is blocked while
    any profile points at it.
    """


class ProviderConnectionModelConflict(Unprocessable):
    """A linked profile's model names a route other than its connection's.

    Like :class:`ProviderConnectionNotFound`, an ``Unprocessable`` (422 in the
    agent server, ``ValueError`` for generic handlers).
    """


# Prefixes that select an SDK route of their own, so a connection that decides
# routing cannot honour them.
_SDK_ROUTE_PREFIXES: Final[tuple[str, ...]] = (
    OPENHANDS_PROVIDER_PREFIX,
    DIGITALOCEAN_PROVIDER_PREFIX,
    LITELLM_PROXY_PREFIX,
)


def _prefix_router(prefix: str) -> Callable[[str], str | None]:
    """Route every model through ``prefix``; ``None`` marks a conflict.

    For these providers a vendor-looking first segment is part of the model id:
    OpenRouter namespaces ids by upstream vendor (``anthropic/...``), and an
    OpenAI-compatible server serves whatever ids it lists (``ollama/llama3``).
    """

    def route(model: str) -> str | None:
        if model.startswith(prefix):
            return model
        if model.startswith(_SDK_ROUTE_PREFIXES):
            return None
        return f"{prefix}{model}"

    return route


# Recognized connection providers (LiteLLM provider names, as Canvas offers
# them) -> model router.
_MODEL_ROUTERS: Final[dict[str, Callable[[str], str | None]]] = {
    "openrouter": _prefix_router(OPENROUTER_PROVIDER_PREFIX),
    **{
        prefix.removesuffix("/"): _prefix_router(prefix)
        for prefix in OPENAI_COMPATIBLE_PROVIDER_PREFIXES
    },
    # LiteLLM lists ``openai_like`` but has no chat route for it.
    "openai_like": _prefix_router(CUSTOM_OPENAI_PROVIDER_PREFIX),
}


class ProviderConnection(BaseModel):
    """A shared credential bundle reused by one or more LLM profiles."""

    id: str = Field(..., min_length=1, max_length=128)
    display_name: str = Field(..., min_length=1, max_length=128)
    provider: str = Field(default="custom", min_length=1, max_length=128)
    # A stored key that cannot be decrypted fails the load instead of reading
    # as unset: a wrong-cipher read-modify-write would otherwise rewrite the
    # collection with api_key=null and destroy the stored ciphertext.
    api_key: Annotated[SecretStr | None, SecretField(undecryptable="raise")] = None
    base_url: str | None = Field(default=None, max_length=2048)
    created_at: int = Field(..., description="Unix epoch seconds.")
    updated_at: int = Field(..., description="Unix epoch seconds.")

    model_config = ConfigDict(populate_by_name=True)

    def api_key_value(self) -> str | None:
        """Return the plaintext key, or ``None`` when unset/empty."""
        if self.api_key is None:
            return None
        value = self.api_key.get_secret_value()
        return value if value.strip() else None

    def route_model(self, model: str) -> str:
        """Return the LiteLLM model id that sends ``model`` through this connection.

        Idempotent. Unrecognized providers return ``model`` unchanged; see the
        module docstring for the precedence rules.

        Raises:
            ProviderConnectionModelConflict: If ``model`` selects a route this
                connection's provider cannot serve.
        """
        router = _MODEL_ROUTERS.get(self.provider.strip().lower())
        if router is None:
            return model
        routed = router(model)
        if routed is None:
            raise ProviderConnectionModelConflict(
                f"Model {model!r} selects a different route than provider "
                f"connection {self.id!r} (provider {self.provider!r}). Enter the "
                "model id as the provider lists it, without that prefix, or link "
                "the profile to a connection for that route."
            )
        return routed


class PersistedProviderConnections(BaseModel):
    """Container for the saved provider connections file."""

    schema_version: int = Field(default=PROVIDER_CONNECTIONS_SCHEMA_VERSION)
    connections: list[ProviderConnection] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True)


class ProviderConnectionStore:
    """File-backed store for shared LLM provider connections.

    All connections live in a single JSON file guarded by a file lock, so
    concurrent create/update/delete calls in-process are serialized. The
    ``api_key`` of each connection is encrypted at rest when a cipher is
    supplied, matching :class:`LLMProfileStore`'s secret handling.
    """

    def __init__(self, base_dir: Path | str | None = None) -> None:
        self.base_dir = Path(base_dir) if base_dir is not None else _DEFAULT_DIR
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._path = self.base_dir / _FILENAME
        self._file_lock = FileLock(self.base_dir / ".provider-connections.lock")

    @contextmanager
    def _acquire_lock(self, timeout: float = _LOCK_TIMEOUT_SECONDS) -> Iterator[None]:
        try:
            with self._file_lock.acquire(timeout=timeout):
                yield
        except Timeout:
            logger.error(
                f"[Provider Connections] Failed to acquire lock within {timeout}s"
            )
            raise LockTimeout(
                "Profile store is busy. Please retry.",
                message=(
                    "Provider connection store lock acquisition timed out after "
                    f"{timeout}s"
                ),
            )

    def _read(self, *, cipher: Cipher | None) -> PersistedProviderConnections:
        """Read the file without locking. Missing file -> empty container.

        A corrupted file raises ``ValueError`` rather than being silently
        replaced, so a bad key or truncated write never destroys stored
        credentials.
        """
        if not self._path.exists():
            return PersistedProviderConnections()
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise Invalid(f"Provider connections file is unreadable: {e}") from e
        if not isinstance(raw, dict):
            raise Invalid("Provider connections file must contain a JSON object")
        version = raw.get("schema_version", PROVIDER_CONNECTIONS_SCHEMA_VERSION)
        if not isinstance(version, int):
            raise Invalid("schema_version must be an integer")
        if version > PROVIDER_CONNECTIONS_SCHEMA_VERSION:
            raise Invalid(
                f"schema_version {version} is newer than supported "
                f"{PROVIDER_CONNECTIONS_SCHEMA_VERSION}"
            )
        raw["schema_version"] = PROVIDER_CONNECTIONS_SCHEMA_VERSION
        context = cipher_context(cipher)
        try:
            return PersistedProviderConnections.model_validate(raw, context=context)
        except ValidationError as e:
            raise Invalid(str(e)) from e

    def _write(
        self, persisted: PersistedProviderConnections, *, cipher: Cipher | None
    ) -> None:
        context = cipher_context(cipher) if cipher else client_context("plaintext")
        data = persisted.model_dump(mode="json", context=context)
        payload = json.dumps(data, indent=2)
        with tempfile.NamedTemporaryFile(
            mode="w", dir=self.base_dir, suffix=".tmp", delete=False
        ) as tmp:
            tmp.write(payload)
            tmp_path = Path(tmp.name)
        try:
            Path.replace(tmp_path, self._path)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

    def list(self, *, cipher: Cipher | None = None) -> list[ProviderConnection]:
        with self._acquire_lock():
            return list(self._read(cipher=cipher).connections)

    def get(
        self, connection_id: str, *, cipher: Cipher | None = None
    ) -> ProviderConnection | None:
        with self._acquire_lock():
            for connection in self._read(cipher=cipher).connections:
                if connection.id == connection_id:
                    return connection
        return None

    def create(
        self, connection: ProviderConnection, *, cipher: Cipher | None = None
    ) -> ProviderConnection:
        if not CONNECTION_ID_REGEX.match(connection.id):
            raise Invalid(f"Invalid provider connection id: {connection.id!r}")
        with self._acquire_lock():
            persisted = self._read(cipher=cipher)
            if any(c.id == connection.id for c in persisted.connections):
                raise Invalid(f"Provider connection {connection.id!r} already exists")
            if len(persisted.connections) >= MAX_PROVIDER_CONNECTIONS:
                raise ProviderConnectionLimitExceeded(
                    f"Provider connection limit reached ({MAX_PROVIDER_CONNECTIONS})."
                )
            persisted.connections.append(connection)
            self._write(persisted, cipher=cipher)
        logger.info(
            "[Provider Connections] Created connection",
            extra={"connection_id": connection.id},
        )
        return connection

    def update(
        self, connection: ProviderConnection, *, cipher: Cipher | None = None
    ) -> ProviderConnection:
        with self._acquire_lock():
            persisted = self._read(cipher=cipher)
            if not any(c.id == connection.id for c in persisted.connections):
                raise ProviderConnectionNotFound(connection.id)
            persisted.connections = [
                connection if c.id == connection.id else c
                for c in persisted.connections
            ]
            self._write(persisted, cipher=cipher)
        logger.info(
            "[Provider Connections] Updated connection",
            extra={"connection_id": connection.id},
        )
        return connection

    def delete(self, connection_id: str, *, cipher: Cipher | None = None) -> None:
        with self._acquire_lock():
            persisted = self._read(cipher=cipher)
            remaining = [c for c in persisted.connections if c.id != connection_id]
            if len(remaining) == len(persisted.connections):
                raise ProviderConnectionNotFound(connection_id)
            persisted.connections = remaining
            self._write(persisted, cipher=cipher)
        logger.info(
            "[Provider Connections] Deleted connection",
            extra={"connection_id": connection_id},
        )
