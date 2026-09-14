"""Apply profile secret selection at conversation and command boundaries."""

from uuid import UUID

from pydantic import SecretStr

from openhands.agent_server.persistence import FileSecretsStore, get_agent_profile_store
from openhands.sdk.conversation.secret_registry import SecretRegistry
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


def command_secret_registry(
    profile_id: UUID, store: FileSecretsStore
) -> SecretRegistry:
    """Resolve exactly one profile's selected saved secrets for a shell command."""
    profiles = get_agent_profile_store()
    name = profiles.name_for_id(profile_id)
    if name is None:
        raise ValueError(f"Agent profile with id '{profile_id}' not found")
    try:
        profile = profiles.load(name)
    except FileNotFoundError as exc:
        raise ValueError(f"Agent profile '{name}' (id={profile_id}) not found") from exc

    refs = profile.secret_refs or []
    selected = select_profile_secrets({}, set(refs), store)
    missing = sorted(set(refs) - selected.keys())
    if missing:
        raise ValueError(
            "Selected profile secrets are unavailable: " + ", ".join(missing)
        )
    return SecretRegistry(secret_sources=selected)
