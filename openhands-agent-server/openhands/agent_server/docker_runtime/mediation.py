"""Prepare secrets for an isolated agent-server."""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import SecretStr

from openhands.sdk.conversation.secret_registry import SecretRegistry
from openhands.sdk.secret import SecretSource, SecretValue, StaticSecret


def materialize_secrets(
    secrets: Mapping[str, SecretValue],
) -> dict[str, SecretSource]:
    """Resolve the SDK's secret sources before crossing a runtime boundary."""
    registry = SecretRegistry()
    registry.update_secrets(secrets)
    return {
        name: StaticSecret(
            value=SecretStr(value)
            if (value := registry.get_secret_value(name))
            else None,
            description=source.description,
        )
        for name, source in registry.secret_sources.items()
    }
