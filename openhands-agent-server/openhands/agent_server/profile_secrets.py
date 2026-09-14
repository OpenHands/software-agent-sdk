"""Apply profile secret selection before either workspace launch path."""

from pydantic import SecretStr

from openhands.agent_server.persistence import FileSecretsStore
from openhands.sdk.secret import SecretSource, StaticSecret


def select_profile_secrets(
    supplied: dict[str, SecretSource],
    allowed: set[str] | None,
    store: FileSecretsStore,
) -> dict[str, SecretSource]:
    """Keep explicit scope and fill selected saved secrets for profile-only callers.

    Unscoped profiles preserve request-only behavior. A scoped profile's saved
    names are authoritative: a caller cannot alias a different lookup under an
    allowed saved name. Missing saved names retain supplied values for the SDK's
    existing per-conversation secret use case; absent names add no capability.
    """
    if allowed is None:
        return dict(supplied)
    selected: dict[str, SecretSource] = {}
    for name in sorted(allowed):
        saved = store.get_secret(name)
        if saved is not None:
            selected[name] = StaticSecret(value=SecretStr(saved))
        elif name in supplied:
            selected[name] = supplied[name]
    return selected
