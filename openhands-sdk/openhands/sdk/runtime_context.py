from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_SERVICE_NAME = re.compile(r"^[a-z][a-z0-9_-]*$")
_MODE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")


class RuntimeService(BaseModel):
    """Describe a service reachable from a conversation runtime."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    url_from_agent: str | None = Field(default=None, max_length=2048)
    api_prefix: str | None = Field(default=None, max_length=512)
    docs_url: str | None = Field(default=None, max_length=2048)
    openapi_url: str | None = Field(default=None, max_length=2048)
    auth_header_name: str | None = Field(default=None, max_length=128)
    auth_env_var: str | None = None
    available: bool = True

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        if not _SERVICE_NAME.fullmatch(value):
            raise ValueError("name must be a lowercase service identifier")
        return value

    @field_validator("url_from_agent", "docs_url", "openapi_url")
    @classmethod
    def _validate_url(cls, value: str | None) -> str | None:
        if value is not None and (
            any(char.isspace() for char in value) or "<" in value or ">" in value
        ):
            raise ValueError("runtime service URLs must be single-line values")
        return value

    @field_validator("api_prefix")
    @classmethod
    def _validate_api_prefix(cls, value: str | None) -> str | None:
        if value is not None and (
            not value.startswith("/") or any(char.isspace() for char in value)
        ):
            raise ValueError("api_prefix must be a single-line absolute path")
        return value

    @field_validator("auth_header_name")
    @classmethod
    def _validate_auth_header_name(cls, value: str | None) -> str | None:
        if value is not None and not _HEADER_NAME.fullmatch(value):
            raise ValueError("auth_header_name must be a valid HTTP header name")
        return value

    @field_validator("auth_env_var")
    @classmethod
    def _validate_auth_env_var(cls, value: str | None) -> str | None:
        if value is not None and not _ENV_NAME.fullmatch(value):
            raise ValueError("auth_env_var must be a valid environment variable name")
        return value


class ConversationRuntimeContext(BaseModel):
    """Capture deployment facts for one conversation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    mode: str | None = Field(default=None, max_length=64)
    services: tuple[RuntimeService, ...] = ()

    @field_validator("mode")
    @classmethod
    def _validate_mode(cls, value: str | None) -> str | None:
        if value is not None and not _MODE_NAME.fullmatch(value):
            raise ValueError("mode must be a deployment identifier")
        return value

    @model_validator(mode="after")
    def _validate_unique_service_names(self) -> ConversationRuntimeContext:
        names = [service.name for service in self.services]
        if len(names) != len(set(names)):
            raise ValueError("runtime service names must be unique")
        return self
