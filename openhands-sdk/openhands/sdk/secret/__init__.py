"""Secret management module for handling sensitive data.

This module provides classes and types for managing secrets in OpenHands.
"""

from openhands.sdk.secret.secrets import (
    LocalSecretResolver,
    LookupSecret,
    SecretSource,
    SecretValue,
    StaticSecret,
    register_local_secret_resolver,
    unregister_local_secret_resolver,
)
from openhands.sdk.secret.terminal_credentials import TerminalCredentialBinding


__all__ = [
    "TerminalCredentialBinding",
    "LocalSecretResolver",
    "SecretSource",
    "StaticSecret",
    "LookupSecret",
    "SecretValue",
    "register_local_secret_resolver",
    "unregister_local_secret_resolver",
]
