"""MCP configuration models and FastMCP normalization."""

from __future__ import annotations

import base64
import copy
from collections.abc import Mapping
from typing import Annotated, Literal, cast

from fastmcp.mcp_config import MCPConfig as FastMCPConfig
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    SecretStr,
    SerializationInfo,
    TypeAdapter,
    ValidationInfo,
    field_serializer,
    field_validator,
    model_serializer,
    model_validator,
)

from openhands.sdk.utils.cipher import Cipher
from openhands.sdk.utils.pydantic_secrets import (
    REDACTED_SECRET_VALUE,
    resolve_expose_mode,
    serialize_secret,
    validate_secret,
)


def _validate_optional_secret(value: object, info: ValidationInfo) -> object:
    if isinstance(value, str | SecretStr):
        return validate_secret(value, info)
    return value


def _serialize_optional_secret(
    value: SecretStr | None, info: SerializationInfo
) -> str | None:
    if value is not None and resolve_expose_mode(info.context) == "redact":
        return REDACTED_SECRET_VALUE
    return cast(str | None, serialize_secret(value, info))


def _validate_secret_map(value: object, info: ValidationInfo) -> object:
    if not isinstance(value, dict):
        return value
    validated = {}
    for key, item in value.items():
        if isinstance(item, str | SecretStr):
            secret = validate_secret(item, info)
            if secret is not None:
                validated[key] = secret
        else:
            validated[key] = item
    return validated


def _serialize_secret_map(
    value: dict[str, SecretStr] | None, info: SerializationInfo
) -> dict[str, str | None] | None:
    if value is None:
        return None
    if resolve_expose_mode(info.context) == "redact":
        return {key: REDACTED_SECRET_VALUE for key in value}
    return {
        key: cast(str | None, serialize_secret(secret, info))
        for key, secret in value.items()
    }


def _dump_model_nonempty(
    model: BaseModel, *, context: dict[str, object]
) -> dict[str, JsonValue] | None:
    dumped = cast(
        dict[str, JsonValue],
        model.model_dump(
            mode="json",
            context=context,
            exclude_none=True,
            exclude_defaults=True,
        ),
    )
    return dumped or None


def _drop_empty_fields(value: object) -> object:
    if not isinstance(value, dict):
        return value
    return {key: item for key, item in value.items() if item is not None and item != {}}


class _MCPBaseModel(BaseModel):
    @model_serializer(mode="wrap")
    def _serialize_compact(self, handler, _info: SerializationInfo) -> object:
        return _drop_empty_fields(handler(self))


class MCPNoneAuthCredential(_MCPBaseModel):
    strategy: Literal["none"]


class MCPApiKeyAuthCredential(_MCPBaseModel):
    strategy: Literal["api_key"]
    value: SecretStr | None = None
    header_name: str | None = None

    @field_validator("value", mode="before")
    @classmethod
    def _validate_value(cls, value: object, info: ValidationInfo) -> object:
        return _validate_optional_secret(value, info)

    @field_serializer("value", when_used="always")
    def _serialize_value(
        self, value: SecretStr | None, info: SerializationInfo
    ) -> str | None:
        return _serialize_optional_secret(value, info)


class MCPBearerAuthCredential(_MCPBaseModel):
    strategy: Literal["bearer"]
    value: SecretStr | None = None

    @field_validator("value", mode="before")
    @classmethod
    def _validate_value(cls, value: object, info: ValidationInfo) -> object:
        return _validate_optional_secret(value, info)

    @field_serializer("value", when_used="always")
    def _serialize_value(
        self, value: SecretStr | None, info: SerializationInfo
    ) -> str | None:
        return _serialize_optional_secret(value, info)


class MCPBasicAuthCredential(_MCPBaseModel):
    strategy: Literal["basic"]
    username: str
    password: SecretStr | None = None

    @field_validator("password", mode="before")
    @classmethod
    def _validate_password(cls, value: object, info: ValidationInfo) -> object:
        return _validate_optional_secret(value, info)

    @field_serializer("password", when_used="always")
    def _serialize_password(
        self, value: SecretStr | None, info: SerializationInfo
    ) -> str | None:
        return _serialize_optional_secret(value, info)


class MCPHeaderAuthCredential(_MCPBaseModel):
    strategy: Literal["header"]
    headers: dict[str, SecretStr] = Field(default_factory=dict)

    @field_validator("headers", mode="before")
    @classmethod
    def _validate_headers(cls, value: object, info: ValidationInfo) -> object:
        return _validate_secret_map(value, info)

    @field_serializer("headers", when_used="always")
    def _serialize_headers(
        self, value: dict[str, SecretStr], info: SerializationInfo
    ) -> dict[str, str | None]:
        return _serialize_secret_map(value, info) or {}


class MCPOAuthTokenState(_MCPBaseModel):
    model_config = ConfigDict(extra="allow")

    access_token: SecretStr | None = None
    refresh_token: SecretStr | None = None

    @field_validator("access_token", "refresh_token", mode="before")
    @classmethod
    def _validate_secret(cls, value: object, info: ValidationInfo) -> object:
        return _validate_optional_secret(value, info)

    @field_serializer("access_token", "refresh_token", when_used="always")
    def _serialize_secret(
        self, value: SecretStr | None, info: SerializationInfo
    ) -> str | None:
        return _serialize_optional_secret(value, info)


class MCPOAuthClientInfoState(_MCPBaseModel):
    model_config = ConfigDict(extra="allow")

    client_secret: SecretStr | None = None

    @field_validator("client_secret", mode="before")
    @classmethod
    def _validate_client_secret(cls, value: object, info: ValidationInfo) -> object:
        return _validate_optional_secret(value, info)

    @field_serializer("client_secret", when_used="always")
    def _serialize_client_secret(
        self, value: SecretStr | None, info: SerializationInfo
    ) -> str | None:
        return _serialize_optional_secret(value, info)


class MCPOAuthTokenExpiryState(_MCPBaseModel):
    expires_at: float | None = None


MCPOAuthClientAuthMethod = Literal[
    "none",
    "client_secret_post",
    "client_secret_basic",
    "private_key_jwt",
]


class MCPOAuthAuthentication(_MCPBaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["oauth"]
    client_auth_method: MCPOAuthClientAuthMethod | None = None
    scopes: str | list[str] | None = None
    client_name: str | None = None
    client_metadata_url: str | None = None
    additional_client_metadata: dict[str, JsonValue] | None = None


class MCPOAuthStateResponse(_MCPBaseModel):
    tokens: dict[str, JsonValue] | None = None
    client_info: dict[str, JsonValue] | None = None
    token_expires_at: float | None = None


MCPOAuthTokenStorageField = Literal["tokens", "client_info", "token_expires_at"]


class MCPOAuthState(_MCPBaseModel):
    tokens: MCPOAuthTokenState | None = None
    client_info: MCPOAuthClientInfoState | None = None
    token_expires_at: float | None = None

    @property
    def has_values(self) -> bool:
        return bool(self.to_plain_dict())

    def _to_storage_dict(self, *, context: dict[str, object]) -> dict[str, JsonValue]:
        data: dict[str, JsonValue] = {}
        if self.tokens is not None and self.tokens.access_token is not None:
            tokens = _dump_model_nonempty(self.tokens, context=context)
            if tokens is not None:
                data["tokens"] = tokens
        if self.client_info is not None:
            client_info = _dump_model_nonempty(self.client_info, context=context)
            if client_info is not None:
                data["client_info"] = client_info
        if self.token_expires_at is not None:
            data["token_expires_at"] = self.token_expires_at
        return data

    def get_token_storage_value(
        self, field: MCPOAuthTokenStorageField
    ) -> dict[str, JsonValue] | None:
        if field == "tokens":
            if self.tokens is None or self.tokens.access_token is None:
                return None
            return _dump_model_nonempty(
                self.tokens,
                context={"expose_secrets": "plaintext"},
            )
        if field == "client_info":
            if self.client_info is None:
                return None
            return _dump_model_nonempty(
                self.client_info,
                context={"expose_secrets": "plaintext"},
            )
        if self.token_expires_at is None:
            return None
        return cast(
            dict[str, JsonValue],
            MCPOAuthTokenExpiryState(expires_at=self.token_expires_at).model_dump(
                mode="json",
                exclude_none=True,
            ),
        )

    def with_token_storage_value(
        self, field: MCPOAuthTokenStorageField, value: Mapping[str, JsonValue]
    ) -> MCPOAuthState:
        if field == "tokens":
            return self.model_copy(
                update={"tokens": MCPOAuthTokenState.model_validate(value)}
            )
        if field == "client_info":
            return self.model_copy(
                update={"client_info": MCPOAuthClientInfoState.model_validate(value)}
            )

        expires_at = MCPOAuthTokenExpiryState.model_validate(value).expires_at
        return self.model_copy(update={"token_expires_at": expires_at})

    def without_token_storage_value(
        self, field: MCPOAuthTokenStorageField
    ) -> tuple[MCPOAuthState, bool]:
        if field == "tokens":
            return self.model_copy(update={"tokens": None}), self.tokens is not None
        if field == "client_info":
            return (
                self.model_copy(update={"client_info": None}),
                self.client_info is not None,
            )
        return (
            self.model_copy(update={"token_expires_at": None}),
            self.token_expires_at is not None,
        )

    def to_plain_dict(self, *, cipher: Cipher | None = None) -> dict[str, JsonValue]:
        """Dump OAuth state with secret values in plaintext.

        When ``cipher`` is provided, encrypted Fernet token strings are first
        validated back through the DataModel so stored/API ciphertext becomes
        usable FastMCP token-storage state.
        """
        if cipher is None:
            return self._to_storage_dict(context={"expose_secrets": "plaintext"})
        decrypted = type(self).model_validate(
            self._to_storage_dict(context={"expose_secrets": "plaintext"}),
            context={"cipher": cipher},
        )
        return decrypted._to_storage_dict(context={"expose_secrets": "plaintext"})

    def to_response(self, *, cipher: Cipher | None = None) -> MCPOAuthStateResponse:
        """Dump OAuth state for API responses: plaintext locally, encrypted remotely."""
        context: dict[str, object] = (
            {"expose_secrets": "plaintext"}
            if cipher is None
            else {"cipher": cipher, "expose_secrets": "encrypted"}
        )
        return MCPOAuthStateResponse.model_validate(
            self._to_storage_dict(context=context)
        )


class MCPOAuthAuthCredential(_MCPBaseModel):
    strategy: Literal["oauth2"]
    authentication: MCPOAuthAuthentication | None = None
    state: MCPOAuthState | None = None

    @field_validator("state", mode="after")
    @classmethod
    def _drop_empty_state(cls, value: MCPOAuthState | None) -> MCPOAuthState | None:
        return value if value is not None and value.has_values else None


MCPAuthCredential = Annotated[
    MCPNoneAuthCredential
    | MCPApiKeyAuthCredential
    | MCPBearerAuthCredential
    | MCPBasicAuthCredential
    | MCPHeaderAuthCredential
    | MCPOAuthAuthCredential,
    Field(discriminator="strategy"),
]

MCPTransport = Literal["stdio", "http", "streamable-http", "sse"]


def _normalize_transport_field(transport: object) -> object:
    return "http" if transport == "shttp" else transport


def _normalize_server_transport_field(
    server: Mapping[str, object],
) -> dict[str, object]:
    normalized = dict(server)
    typed_transport = normalized.pop("type", None)
    if "transport" not in normalized and typed_transport is not None:
        normalized["transport"] = _normalize_transport_field(typed_transport)
    return normalized


class MCPServer(_MCPBaseModel):
    """One MCP server in the settings DataModel."""

    model_config = ConfigDict(extra="forbid")

    url: str | None = Field(default=None, min_length=1)
    transport: MCPTransport | None = None
    command: str | None = Field(default=None, min_length=1)
    args: list[str] | None = None
    env: dict[str, SecretStr] | None = None
    cwd: str | None = None
    description: str | None = None
    icon: str | None = None
    timeout: float | None = None
    sse_read_timeout: float | None = None
    keep_alive: bool | None = None
    headers: dict[str, SecretStr] | None = None
    auth: MCPAuthCredential | None = None

    @field_validator("env", "headers", mode="before")
    @classmethod
    def _validate_secret_mapping(cls, value: object, info: ValidationInfo) -> object:
        return _validate_secret_map(value, info)

    @field_serializer("env", "headers", when_used="always")
    def _serialize_secret_mapping(
        self, value: dict[str, SecretStr] | None, info: SerializationInfo
    ) -> dict[str, str | None] | None:
        return _serialize_secret_map(value, info)

    @model_validator(mode="after")
    def _validate_server(self) -> MCPServer:
        declared_transport = self.effective_transport
        if declared_transport == "stdio" and not self.command:
            raise ValueError("stdio MCP servers require 'command'")
        if declared_transport in {"http", "streamable-http", "sse"} and not self.url:
            raise ValueError("remote MCP servers require 'url'")
        if self.auth is not None and self.headers is not None:
            has_authorization_header = any(
                key.lower() == "authorization" for key in self.headers
            )
            if has_authorization_header:
                raise ValueError(
                    "'auth' cannot be combined with an explicit top-level "
                    "'Authorization' header; use auth.strategy='header' instead."
                )
        return self

    @property
    def effective_transport(self) -> MCPTransport | None:
        return self.transport

    @property
    def oauth_auth(self) -> MCPOAuthAuthCredential | None:
        return self.auth if isinstance(self.auth, MCPOAuthAuthCredential) else None

    def initial_oauth_state(
        self, *, cipher: Cipher | None = None
    ) -> MCPOAuthState | None:
        auth = self.oauth_auth
        if auth is None or auth.state is None:
            return None
        return MCPOAuthState.model_validate(
            auth.state.to_plain_dict(cipher=cipher),
            context={"cipher": cipher} if cipher is not None else None,
        )

    def with_decrypted_secrets(self, *, cipher: Cipher | None = None) -> MCPServer:
        if cipher is None:
            return self
        data = self.model_dump(
            mode="json",
            context={"expose_secrets": "plaintext"},
            exclude_none=True,
            exclude_defaults=True,
        )
        return type(self).model_validate(data, context={"cipher": cipher})


_MCP_SERVER_KNOWN_FIELDS = frozenset(MCPServer.model_fields)
_MCP_CONFIG_ADAPTER: TypeAdapter[dict[str, MCPServer]] = TypeAdapter(
    dict[str, MCPServer]
)


def drop_unknown_mcp_server_fields(server: Mapping[str, object]) -> dict[str, object]:
    server = _normalize_server_transport_field(server)
    return {
        key: value for key, value in server.items() if key in _MCP_SERVER_KNOWN_FIELDS
    }


def _extract_mcp_config(value: object) -> object:
    """Extract a server map from SDK-native or external FastMCP-shaped input."""
    if value in (None, {}):
        return {}
    if isinstance(value, FastMCPConfig):
        return value.model_dump(exclude_none=True, exclude_defaults=True).get(
            "mcpServers", {}
        )
    if isinstance(value, Mapping) and "mcpServers" in value:
        servers = value.get("mcpServers")
        return servers if servers is not None else {}
    return value


def coerce_mcp_config(
    value: object, *, context: dict[str, object] | None = None
) -> dict[str, MCPServer]:
    extracted = _extract_mcp_config(value)
    if isinstance(extracted, Mapping):
        extracted = {
            name: _normalize_server_transport_field(server)
            if isinstance(server, Mapping)
            else server
            for name, server in extracted.items()
        }
    return _MCP_CONFIG_ADAPTER.validate_python(
        extracted,
        context=context,
    )


def _basic_auth_header(username: str, password: str) -> str:
    token = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
    return f"Basic {token}"


def _normalize_server_for_fastmcp(
    server: Mapping[str, JsonValue],
) -> dict[str, JsonValue]:
    server = copy.deepcopy(dict(server))
    auth = server.pop("auth", None)
    raw_headers = server.get("headers")
    headers = dict(raw_headers) if isinstance(raw_headers, Mapping) else {}

    if isinstance(auth, dict):
        strategy = auth.get("strategy")
        if strategy == "api_key":
            value = auth.get("value")
            header_name = auth.get("header_name")
            if isinstance(value, str) and value:
                if isinstance(header_name, str) and header_name:
                    headers[header_name] = value
                else:
                    server["auth"] = value
        elif strategy == "bearer":
            value = auth.get("value")
            if isinstance(value, str) and value:
                server["auth"] = value
        elif strategy == "basic":
            username = auth.get("username")
            password = auth.get("password")
            if isinstance(username, str) and isinstance(password, str):
                headers["Authorization"] = _basic_auth_header(username, password)
        elif strategy == "header":
            auth_headers = auth.get("headers")
            if isinstance(auth_headers, dict):
                headers.update(auth_headers)
        elif strategy == "oauth2":
            server["auth"] = "oauth"
            authentication = auth.get("authentication")
            if isinstance(authentication, Mapping):
                server["authentication"] = dict(authentication)
    elif isinstance(auth, str):
        server["auth"] = auth

    if headers:
        server["headers"] = headers
    elif "headers" in server:
        server.pop("headers", None)

    return server


def dump_mcp_config(
    mcp_config: Mapping[str, MCPServer],
    *,
    context: dict[str, object] | None = None,
) -> dict[str, dict[str, JsonValue]]:
    dump_context = {"expose_secrets": "plaintext"} if context is None else context
    return {
        name: cast(
            dict[str, JsonValue],
            server.model_dump(
                mode="json",
                context=dump_context,
                exclude_none=True,
                exclude_defaults=True,
            ),
        )
        for name, server in mcp_config.items()
    }


def to_fastmcp_mcp_config(
    mcp_config: Mapping[str, MCPServer],
    *,
    cipher: Cipher | None = None,
) -> dict[str, JsonValue]:
    context = {"cipher": cipher, "expose_secrets": "plaintext"} if cipher else None
    return {
        "mcpServers": {
            name: _normalize_server_for_fastmcp(server)
            for name, server in dump_mcp_config(
                mcp_config,
                context=context,
            ).items()
        },
    }
