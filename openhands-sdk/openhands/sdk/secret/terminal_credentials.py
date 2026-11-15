"""Explicit, value-free authorization for terminal credential consumers."""

import re
from typing import Annotated, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    field_validator,
    model_validator,
)


class TerminalCredentialBinding(BaseModel):
    """Allow one terminal consumer on an exact host to use a registry secret."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    consumer: Literal["git", "gh", "glab"]
    host: str
    secret_ref: str
    username: str | None = None

    @field_validator("host")
    @classmethod
    def normalize_host(cls, value: str) -> str:
        hostname, separator, port = value.partition(":")
        labels = hostname.split(".")
        if (
            len(hostname) > 253
            or not all(
                re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
                for label in labels
            )
            or (
                separator
                and (not re.fullmatch(r"[0-9]+", port) or not 1 <= int(port) <= 65535)
            )
        ):
            raise ValueError("host must be an exact hostname with an optional port")
        return hostname.lower() + (f":{int(port)}" if separator else "")

    @field_validator("secret_ref", "username")
    @classmethod
    def validate_name(cls, value: str | None) -> str | None:
        if value is not None and (
            not value.strip()
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
        ):
            raise ValueError(
                "secret_ref and username must be nonempty without controls"
            )
        return value

    @model_validator(mode="after")
    def require_git_username(self) -> Self:
        if self.consumer == "git" and self.username is None:
            raise ValueError("git bindings require an explicit username")
        return self


def validate_terminal_credential_bindings(
    bindings: list[TerminalCredentialBinding],
) -> list[TerminalCredentialBinding]:
    keys = [(binding.consumer, binding.host) for binding in bindings]
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate terminal credential consumer and host")
    return bindings


TerminalCredentialBindings = Annotated[
    list[TerminalCredentialBinding],
    AfterValidator(validate_terminal_credential_bindings),
]
