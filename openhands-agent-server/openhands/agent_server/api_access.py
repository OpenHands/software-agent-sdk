"""Opt-in request scopes for clients of a privileged local Agent Server.

Session authentication still applies. An orchestrator can retain a separate
operator key while giving a client a limited set of HTTP/WebSocket routes.
"""

import hmac
import re
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qs

from pydantic import BaseModel, Field, SecretStr, field_validator
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


class APIAccessRule(BaseModel):
    method: Literal["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
    path: str = Field(
        min_length=1,
        max_length=256,
        description="Regex matched against the complete decoded request path.",
    )
    workspace_query_parameters: list[str] = Field(default_factory=list)

    @field_validator("path")
    @classmethod
    def valid_pattern(cls, value: str) -> str:
        re.compile(value)
        return value


class ClientAPIPolicy(BaseModel):
    operator_api_key: SecretStr
    http_rules: list[APIAccessRule] = Field(default_factory=list, max_length=128)
    websocket_paths: list[str] = Field(default_factory=list, max_length=16)

    @field_validator("operator_api_key")
    @classmethod
    def nonempty_operator_key(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < 32:
            raise ValueError(
                "A separate operator credential of at least 32 characters is required"
            )
        return value

    @field_validator("websocket_paths")
    @classmethod
    def valid_websocket_patterns(cls, values: list[str]) -> list[str]:
        for value in values:
            if len(value) > 256:
                raise ValueError("WebSocket path pattern is too long")
            re.compile(value)
        return values


class ClientAPIAccessMiddleware:
    def __init__(self, app: ASGIApp, *, policy: ClientAPIPolicy, workspace_path: Path):
        self.app, self.policy = app, policy
        self.workspace = workspace_path.resolve()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        kind = scope["type"]
        if kind not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        supplied = dict(scope.get("headers", [])).get(b"x-operator-api-key", b"")
        operator = hmac.compare_digest(
            supplied, self.policy.operator_api_key.get_secret_value().encode()
        )
        path = scope["path"]
        allowed = operator
        if not allowed and kind == "websocket":
            allowed = any(
                re.fullmatch(pattern, path) for pattern in self.policy.websocket_paths
            )
        if not allowed and kind == "http":
            query = parse_qs(
                scope.get("query_string", b"").decode("utf-8", errors="replace"),
                keep_blank_values=True,
            )
            for rule in self.policy.http_rules:
                if rule.method != scope["method"] or not re.fullmatch(rule.path, path):
                    continue
                allowed = True
                for name in rule.workspace_query_parameters:
                    values = query.get(name, [])
                    if len(values) != 1 or not self._inside_workspace(values[0]):
                        allowed = False
                        break
                if allowed:
                    break
        if allowed:
            await self.app(scope, receive, send)
        elif kind == "websocket":
            await send({"type": "websocket.close", "code": 1008})
        else:
            await JSONResponse(
                {"detail": "Request is outside this client's API scope"},
                status_code=403,
            )(scope, receive, send)

    def _inside_workspace(self, value: str) -> bool:
        try:
            candidate = Path(value)
            return candidate.is_absolute() and candidate.resolve().is_relative_to(
                self.workspace
            )
        except (OSError, ValueError):
            return False
