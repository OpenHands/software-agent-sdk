"""Resolve provider connections referenced by a caller-supplied launch source.

A stored LLM profile that points at a shared provider connection carries no
inline ``api_key``: :meth:`LLMProfileStore.load` applies the connection's
credentials at load time. A client that builds its own agent (or agent settings)
and references a connection by id produces the same config shape, but nothing on
that path reads the connection, so the LLM reaches the provider keyless and the
run fails authentication.

:func:`resolve_llm_credentials` closes that gap for the non-profile sources of
:func:`openhands.sdk.launch.resolve`'s input, so every launch path resolves a
connection identically, whatever shape the request arrives in.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

from pydantic import BaseModel

from openhands.sdk.agent.base import AgentBase
from openhands.sdk.launch.errors import AgentLaunchError
from openhands.sdk.llm.llm import LLM
from openhands.sdk.settings.model import ACPAgentSettings, OpenHandsAgentSettings


if TYPE_CHECKING:
    from openhands.sdk.llm.llm_profile_store import LLMProfileLoader
    from openhands.sdk.utils.cipher import Cipher


type CredentialStoreSource = Callable[[], "LLMProfileLoader"]
"""Supplies the profile store, read only when a connection is referenced."""

type CredentialSource = AgentBase | OpenHandsAgentSettings | ACPAgentSettings


def resolve_llm_credentials[T: CredentialSource](
    source: T, profiles: CredentialStoreSource, *, cipher: Cipher | None = None
) -> T:
    """Apply every referenced provider connection's credentials to ``source``.

    ``source`` is a caller-built agent or agent settings, so its LLMs may name a
    provider connection instead of carrying an inline key. Returns ``source``
    itself when nothing references a connection.

    Raises:
        AgentLaunchError: a referenced connection does not exist and the LLM has
            no inline key to fall back on.
    """
    resolve_connection: Callable[[LLM], LLM] | None = None
    store_read = False

    def resolve(llm: LLM) -> LLM:
        nonlocal resolve_connection, store_read
        if llm.provider_connection_id is None:
            return llm
        if not store_read:
            store_read = True
            resolve_connection = _connection_resolver(profiles(), cipher)
        if resolve_connection is None:
            return llm
        try:
            return resolve_connection(llm)
        except ValueError as exc:
            # A dangling reference is a fixable config problem, not a server
            # fault; report it the way an unresolvable profile reference is.
            raise AgentLaunchError(str(exc)) from exc

    return cast(T, _map_llms(source, resolve, set()))


def _connection_resolver(
    profiles: LLMProfileLoader, cipher: Cipher | None
) -> Callable[[LLM], LLM] | None:
    """The store's connection resolver, when it has one.

    ``LLMProfileLoader`` is a read-only contract that cloud adapters implement
    minimally, so credential resolution is an optional capability.
    """
    resolve = getattr(profiles, "resolve_provider_connection", None)
    if resolve is None:
        return None
    return lambda llm: resolve(llm, cipher=cipher)


def _map_llms(value: Any, map_llm: Callable[[LLM], LLM], seen: set[int]) -> Any:
    """Rebuild ``value`` with ``map_llm`` applied to every LLM it holds.

    Launch sources are frozen models, so a resolved LLM only reaches the agent
    through a copy of its owner. Untouched branches come back as the same
    objects, so a source with no connection reference is returned unchanged.
    """
    if isinstance(value, LLM):
        if id(value) in seen:
            return value
        seen.add(id(value))
        mapped = map_llm(value)
        updates = _rebuilt_fields(mapped, map_llm, seen)
        return mapped.model_copy(update=updates) if updates else mapped
    if isinstance(value, BaseModel):
        if id(value) in seen:
            return value
        seen.add(id(value))
        updates = _rebuilt_fields(value, map_llm, seen)
        return value.model_copy(update=updates) if updates else value
    if isinstance(value, dict):
        if id(value) in seen:
            return value
        seen.add(id(value))
        mapped_items = {
            key: _map_llms(item, map_llm, seen) for key, item in value.items()
        }
        if all(mapped_items[key] is item for key, item in value.items()):
            return value
        return mapped_items
    if isinstance(value, list | tuple):
        if id(value) in seen:
            return value
        seen.add(id(value))
        mapped_items = [_map_llms(item, map_llm, seen) for item in value]
        if all(m is item for m, item in zip(mapped_items, value, strict=True)):
            return value
        return tuple(mapped_items) if isinstance(value, tuple) else mapped_items
    return value


def _rebuilt_fields(
    model: BaseModel, map_llm: Callable[[LLM], LLM], seen: set[int]
) -> dict[str, Any]:
    """The fields of ``model`` whose children changed; empty when none did."""
    updates: dict[str, Any] = {}
    for name in type(model).model_fields:
        try:
            child = getattr(model, name)
        except AttributeError:
            continue
        rebuilt = _map_llms(child, map_llm, seen)
        if rebuilt is not child:
            updates[name] = rebuilt
    return updates
