"""OpenHands MCP configuration models and FastMCP normalization."""

from __future__ import annotations

import base64
import copy
import re
from typing import Annotated, Any, Literal

from fastmcp.mcp_config import MCPConfig as FastMCPConfig
from pydantic import BaseModel, ConfigDict, Field, model_validator


class MCPNoneAuthCredential(BaseModel):
    strategy: Literal["none"]


class MCPApiKeyAuthCredential(BaseModel):
    strategy: Literal["api_key"]
    value: str
    header_name: str | None = None


class MCPBearerAuthCredential(BaseModel):
    strategy: Literal["bearer"]
    value: str


class MCPBasicAuthCredential(BaseModel):
    strategy: Literal["basic"]
    username: str
    password: str


class MCPHeaderAuthCredential(BaseModel):
    strategy: Literal["header"]
    headers: dict[str, str] = Field(default_factory=dict)


class MCPOAuthAuthCredential(BaseModel):
    strategy: Literal["oauth2"]
    authentication: dict[str, Any] | None = None
    credentials: dict[str, Any] = Field(default_factory=dict)


class MCPCustomAuthCredential(BaseModel):
    strategy: Literal["custom"]
    fastmcp: dict[str, Any] = Field(default_factory=dict)


MCPAuthCredential = Annotated[
    MCPNoneAuthCredential
    | MCPApiKeyAuthCredential
    | MCPBearerAuthCredential
    | MCPBasicAuthCredential
    | MCPHeaderAuthCredential
    | MCPOAuthAuthCredential
    | MCPCustomAuthCredential,
    Field(discriminator="strategy"),
]


def _auth_from_legacy_server(server: dict[str, Any]) -> dict[str, Any] | None:
    auth = server.get("auth")
    authentication = server.pop("authentication", None)
    oauth_credentials = server.pop("oauth_credentials", None)

    if isinstance(auth, dict):
        if auth.get("strategy") == "oauth2":
            if authentication is not None and "authentication" not in auth:
                auth["authentication"] = authentication
            if oauth_credentials is not None and "credentials" not in auth:
                auth["credentials"] = oauth_credentials
        return auth

    if auth == "oauth":
        credential: dict[str, Any] = {"strategy": "oauth2"}
        if authentication is not None:
            credential["authentication"] = authentication
        if oauth_credentials is not None:
            credential["credentials"] = oauth_credentials
        return credential

    if isinstance(auth, str) and auth:
        return {"strategy": "bearer", "value": auth}

    api_key = server.pop("api_key", None)
    if isinstance(api_key, str) and api_key:
        return {"strategy": "api_key", "value": api_key}

    headers = server.get("headers")
    if isinstance(headers, dict):
        authorization = headers.get("Authorization") or headers.get("authorization")
        if isinstance(authorization, str) and authorization:
            headers.pop("Authorization", None)
            headers.pop("authorization", None)
            if not headers:
                server.pop("headers", None)
            bearer = re.sub(r"^Bearer\s+", "", authorization, flags=re.IGNORECASE)
            return {"strategy": "bearer", "value": bearer}

    return None


class OpenHandsMCPServer(BaseModel):
    """One MCP server in the settings DataModel.

    Extra fields are preserved so this remains forward-compatible with
    FastMCP server options that OpenHands does not interpret.
    """

    model_config = ConfigDict(extra="allow")

    url: str | None = None
    transport: str | None = None
    command: str | None = None
    args: list[str] | None = None
    env: dict[str, str] | None = None
    cwd: str | None = None
    headers: dict[str, str] | None = None
    auth: MCPAuthCredential | None = None

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_auth(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = copy.deepcopy(value)
        auth = _auth_from_legacy_server(data)
        if auth is not None:
            data["auth"] = auth
        return data


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
        elif strategy == "custom":
            fastmcp = auth.get("fastmcp")
            if isinstance(fastmcp, dict):
                server.update(fastmcp)
    elif isinstance(auth, str):
        server["auth"] = auth

    if headers:
        server["headers"] = headers
    elif "headers" in server:
        server.pop("headers", None)

    return server


def dump_openhands_mcp_config(
    config: OpenHandsMCPConfig | FastMCPConfig | dict,
) -> dict:
    if isinstance(config, (OpenHandsMCPConfig, FastMCPConfig)):
        return config.model_dump(exclude_none=True, exclude_defaults=True)
    return copy.deepcopy(config)


def to_fastmcp_mcp_config(config: OpenHandsMCPConfig | FastMCPConfig | dict) -> dict:
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
