"""Explicit, value-free credential delivery to terminal programs."""

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
    """Deliver a registry secret to a program, not restrict its subsequent use."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    consumer: str
    secret_ref: str
    adapter: Literal["environment", "git_helper"] = "environment"
    env_var: str | None = None
    host: str | None = None
    username: str | None = None

    @field_validator("consumer")
    @classmethod
    def validate_consumer(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.+-]*", value):
            raise ValueError("consumer must be an executable basename, not a path")
        if value.lower() in {"adapter.py", "config.json"}:
            raise ValueError("consumer conflicts with adapter runtime files")
        return value

    @field_validator("env_var")
    @classmethod
    def validate_env_var(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
            raise ValueError("env_var must be an environment variable name")
        return value

    @field_validator("host")
    @classmethod
    def normalize_host(cls, value: str | None) -> str | None:
        if value is None:
            return None
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
    def validate_adapter(self) -> Self:
        if self.adapter == "environment":
            if (
                self.env_var is None
                or self.host is not None
                or self.username is not None
            ):
                raise ValueError(
                    "environment bindings require env_var and no host or username"
                )
        elif self.host is None or self.username is None or self.env_var is not None:
            raise ValueError(
                "git_helper bindings require host and username and no env_var"
            )
        return self


def validate_terminal_credential_bindings(
    bindings: list[TerminalCredentialBinding],
) -> list[TerminalCredentialBinding]:
    keys = [
        (
            b.consumer.casefold(),
            b.adapter,
            b.env_var.casefold() if b.env_var else b.host,
        )
        for b in bindings
    ]
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate terminal credential delivery target")
    consumers: dict[str, str] = {}
    for binding in bindings:
        previous = consumers.setdefault(binding.consumer.casefold(), binding.consumer)
        if previous != binding.consumer:
            raise ValueError("consumer names must not differ only by case")
    return bindings


TerminalCredentialBindings = Annotated[
    list[TerminalCredentialBinding],
    AfterValidator(validate_terminal_credential_bindings),
]
