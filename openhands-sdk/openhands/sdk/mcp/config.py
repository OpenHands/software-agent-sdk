"""OpenHands MCP configuration models and FastMCP normalization."""

from __future__ import annotations

import base64
import copy
from collections.abc import Mapping
from typing import Annotated, Any, Literal

from fastmcp.mcp_config import MCPConfig as FastMCPConfig
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    SerializationInfo,
    ValidationInfo,
    field_serializer,
    field_validator,
    model_validator,
)

from openhands.sdk.utils.pydantic_secrets import (
    REDACTED_SECRET_VALUE,
    MissingCipherError,
    resolve_expose_mode,
    serialize_secret,
    validate_secret,
)
from openhands.sdk.utils.redact import sanitize_dict


def _validate_optional_secret(value: Any, info: ValidationInfo) -> Any:
    if isinstance(value, str | SecretStr):
        return validate_secret(value, info)
    return value


def _serialize_optional_secret(value: SecretStr | None, info: SerializationInfo) -> Any:
    if value is not None and resolve_expose_mode(info.context) == "redact":
        return REDACTED_SECRET_VALUE
    return serialize_secret(value, info)


def _validate_secret_map(value: Any, info: ValidationInfo) -> Any:
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
) -> dict[str, Any] | None:
    if value is None:
        return None
    return {key: serialize_secret(secret, info) for key, secret in value.items()}


class MCPNoneAuthCredential(BaseModel):
    strategy: Literal["none"]


class MCPApiKeyAuthCredential(BaseModel):
    strategy: Literal["api_key"]
    value: SecretStr | None = None
    header_name: str | None = None

    @field_validator("value", mode="before")
    @classmethod
    def _validate_value(cls, value: Any, info: ValidationInfo) -> Any:
        return _validate_optional_secret(value, info)

    @field_serializer("value", when_used="always")
    def _serialize_value(
        self, value: SecretStr | None, info: SerializationInfo
    ) -> str | None:
        return _serialize_optional_secret(value, info)


class MCPBearerAuthCredential(BaseModel):
    strategy: Literal["bearer"]
    value: SecretStr | None = None

    @field_validator("value", mode="before")
    @classmethod
    def _validate_value(cls, value: Any, info: ValidationInfo) -> Any:
        return _validate_optional_secret(value, info)

    @field_serializer("value", when_used="always")
    def _serialize_value(
        self, value: SecretStr | None, info: SerializationInfo
    ) -> str | None:
        return _serialize_optional_secret(value, info)


class MCPBasicAuthCredential(BaseModel):
    strategy: Literal["basic"]
    username: str
    password: SecretStr | None = None

    @field_validator("password", mode="before")
    @classmethod
    def _validate_password(cls, value: Any, info: ValidationInfo) -> Any:
        return _validate_optional_secret(value, info)

    @field_serializer("password", when_used="always")
    def _serialize_password(
        self, value: SecretStr | None, info: SerializationInfo
    ) -> str | None:
        return _serialize_optional_secret(value, info)


class MCPHeaderAuthCredential(BaseModel):
    strategy: Literal["header"]
    headers: dict[str, SecretStr] = Field(default_factory=dict)

    @field_validator("headers", mode="before")
    @classmethod
    def _validate_headers(cls, value: Any, info: ValidationInfo) -> Any:
        return _validate_secret_map(value, info)

    @field_serializer("headers", when_used="always")
    def _serialize_headers(
        self, value: dict[str, SecretStr], info: SerializationInfo
    ) -> dict[str, Any]:
        return _serialize_secret_map(value, info) or {}


class MCPOAuthTokenState(BaseModel):
    model_config = ConfigDict(extra="allow")

    access_token: SecretStr | None = None
    refresh_token: SecretStr | None = None

    @field_validator("access_token", "refresh_token", mode="before")
    @classmethod
    def _validate_secret(cls, value: Any, info: ValidationInfo) -> Any:
        return _validate_optional_secret(value, info)

    @field_serializer("access_token", "refresh_token", when_used="always")
    def _serialize_secret(
        self, value: SecretStr | None, info: SerializationInfo
    ) -> str | None:
        return _serialize_optional_secret(value, info)


class MCPOAuthClientInfoState(BaseModel):
    model_config = ConfigDict(extra="allow")

    client_secret: SecretStr | None = None

    @field_validator("client_secret", mode="before")
    @classmethod
    def _validate_client_secret(cls, value: Any, info: ValidationInfo) -> Any:
        return _validate_optional_secret(value, info)

    @field_serializer("client_secret", when_used="always")
    def _serialize_client_secret(
        self, value: SecretStr | None, info: SerializationInfo
    ) -> str | None:
        return _serialize_optional_secret(value, info)


class MCPOAuthState(BaseModel):
    tokens: MCPOAuthTokenState | None = None
    client_info: MCPOAuthClientInfoState | None = None
    token_expires_at: float | None = None


class MCPOAuthAuthCredential(BaseModel):
    strategy: Literal["oauth2"]
    authentication: dict[str, Any] | None = None
    state: MCPOAuthState | None = None


MCPAuthCredential = Annotated[
    MCPNoneAuthCredential
    | MCPApiKeyAuthCredential
    | MCPBearerAuthCredential
    | MCPBasicAuthCredential
    | MCPHeaderAuthCredential
    | MCPOAuthAuthCredential,
    Field(discriminator="strategy"),
]


class OpenHandsMCPServer(BaseModel):
    """One MCP server in the settings DataModel."""

    model_config = ConfigDict(extra="forbid")

    url: str | None = None
    type: str | None = None
    transport: str | None = None
    command: str | None = None
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
    def _validate_secret_mapping(cls, value: Any, info: ValidationInfo) -> Any:
        return _validate_secret_map(value, info)

    @field_serializer("env", "headers", when_used="always")
    def _serialize_secret_mapping(
        self, value: dict[str, SecretStr] | None, info: SerializationInfo
    ) -> dict[str, Any] | None:
        return _serialize_secret_map(value, info)


_MCP_SERVER_KNOWN_FIELDS = frozenset(OpenHandsMCPServer.model_fields)


def _sanitize_mcp_extra_fields(config: dict[str, Any]) -> dict[str, Any]:
    """Sanitize unknown MCP server fields without collapsing known auth shape."""
    config = copy.deepcopy(config)
    servers = config.get("mcpServers")
    if not isinstance(servers, dict):
        return sanitize_dict(config)
    for server in servers.values():
        if not isinstance(server, dict):
            continue
        for key, value in list(server.items()):
            if key in _MCP_SERVER_KNOWN_FIELDS:
                continue
            server[key] = sanitize_dict({key: value})[key]
    return config


def drop_unknown_mcp_server_fields(server: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value for key, value in server.items() if key in _MCP_SERVER_KNOWN_FIELDS
    }


def normalize_empty_mcp_config(value: Any) -> Any:
    """Coerce an empty/absent MCP config to ``None`` (else pass through)."""
    return None if value in (None, {}) else value


def serialize_mcp_config(
    value: OpenHandsMCPConfig | None, info: SerializationInfo
) -> dict[str, Any]:
    """Serialize an MCP config, masking/encrypting secrets per expose mode."""
    if value is None:
        return {}
    ctx = info.context or {}
    mode = resolve_expose_mode(ctx)

    if mode == "encrypted" and ctx.get("cipher") is None:
        raise MissingCipherError(
            "Cannot encrypt MCP secrets: no cipher configured. "
            "Set OH_SECRET_KEY environment variable."
        )

    dumped = dump_openhands_mcp_config(value, context=ctx)
    if mode == "redact":
        return _sanitize_mcp_extra_fields(dumped)
    return dumped


def dump_mcp_config_secret_values(
    value: Any,
    *,
    cipher: Any,
    expose_secrets: Literal["encrypted", "plaintext"],
) -> Any:
    if not isinstance(value, Mapping):
        return value
    servers = value.get("mcpServers")
    if not isinstance(servers, Mapping):
        return value

    validate_context = {"cipher": cipher} if expose_secrets == "plaintext" else None
    dump_context = {"cipher": cipher, "expose_secrets": expose_secrets}
    updated = copy.deepcopy(dict(value))
    updated["mcpServers"] = {
        name: OpenHandsMCPServer.model_validate(
            server,
            context=validate_context,
        ).model_dump(
            mode="json",
            context=dump_context,
            exclude_none=True,
            exclude_defaults=True,
        )
        if isinstance(server, Mapping)
        else copy.deepcopy(server)
        for name, server in servers.items()
    }
    return updated


class OpenHandsMCPConfig(BaseModel):
    """MCP config persisted in OpenHands settings.

    It intentionally accepts a richer auth credential object than FastMCP's
    runtime config. Use :func:`to_fastmcp_mcp_config` at runtime boundaries.
    """

    mcpServers: dict[str, OpenHandsMCPServer] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _from_fastmcp_config(cls, value: Any) -> Any:
        if isinstance(value, FastMCPConfig):
            return value.model_dump(exclude_none=True, exclude_defaults=True)
        return value

    @model_validator(mode="after")
    def _validate_runtime_shape(self) -> OpenHandsMCPConfig:
        FastMCPConfig.model_validate(to_fastmcp_mcp_config(self))
        return self


def _basic_auth_header(username: str, password: str) -> str:
    token = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
    return f"Basic {token}"


def _normalize_server_for_fastmcp(server: dict[str, Any]) -> dict[str, Any]:
    server = copy.deepcopy(server)
    auth = server.pop("auth", None)
    headers = dict(server.get("headers") or {})

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
            if isinstance(authentication, dict):
                server["authentication"] = authentication
    elif isinstance(auth, str):
        server["auth"] = auth

    if headers:
        server["headers"] = headers
    elif "headers" in server:
        server.pop("headers", None)

    return server


def dump_openhands_mcp_config(
    config: OpenHandsMCPConfig | FastMCPConfig | dict,
    *,
    context: dict[str, Any] | None = None,
) -> dict:
    if isinstance(config, OpenHandsMCPConfig):
        dump_context = {"expose_secrets": "plaintext"} if context is None else context
        return config.model_dump(
            mode="json",
            context=dump_context,
            exclude_none=True,
            exclude_defaults=True,
        )
    if isinstance(config, FastMCPConfig):
        return config.model_dump(exclude_none=True, exclude_defaults=True)
    return copy.deepcopy(config)


def to_fastmcp_mcp_config(
    config: OpenHandsMCPConfig | FastMCPConfig | dict,
    *,
    cipher: Any | None = None,
) -> dict:
    if cipher is not None and isinstance(config, dict):
        config = OpenHandsMCPConfig.model_validate(config, context={"cipher": cipher})
    dumped = dump_openhands_mcp_config(config)
    servers = dumped.get("mcpServers")
    if not isinstance(servers, dict):
        return dumped
    return {
        **dumped,
        "mcpServers": {
            name: _normalize_server_for_fastmcp(server)
            if isinstance(server, dict)
            else server
            for name, server in servers.items()
        },
    }
