"""Stable DTOs for ACP session model metadata.

These live in a standalone module — *not* ``acp_agent`` — so the agent-server
can import them for its public ``ConversationInfo`` schema without importing
``ACPAgent``, which would eagerly register it in the agent
``DiscriminatedUnion`` (see ``openhands/sdk/agent/__init__.py``).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field


@runtime_checkable
class _ModelInfoWithModelId(Protocol):
    model_id: str


@runtime_checkable
class _ModelInfoWithValue(Protocol):
    value: str


@runtime_checkable
class _ModelInfoWithName(Protocol):
    name: str | None


@runtime_checkable
class _ModelInfoWithDescription(Protocol):
    description: str | None


class ACPModelInfo(BaseModel):
    """One model an ACP server offers for a session.

    A normalized, stable mirror of the ACP protocol's ``ModelInfo``. The
    protocol ``models`` capability is flagged **UNSTABLE**, so we re-map it
    into our own type at the SDK boundary rather than re-serializing the
    vendored ``acp.schema`` type onto the agent-server's public API — clients
    get a stable shape regardless of upstream protocol churn.

    Carries everything a client needs to render a picker and resolve a
    ``current_model_id`` to a display label *itself*; the SDK deliberately
    does no name curation.
    """

    # ``model_id`` collides with pydantic's protected ``model_`` namespace;
    # opt out (the name mirrors the protocol field and the persisted shape).
    model_config = ConfigDict(protected_namespaces=())

    model_id: str = Field(
        description=(
            "Server-assigned model identifier. May be concrete "
            '(e.g. ``"gpt-5.6"``) or an opaque alias '
            '(e.g. ``"default"``, ``"auto"``). This is the value to pass back '
            "to the server to switch to this model."
        ),
    )
    name: str | None = Field(
        default=None,
        description='Human-readable label, e.g. ``"GPT-5.5"``.',
    )
    description: str | None = Field(
        default=None,
        description="Optional longer description supplied by the server.",
    )

    @classmethod
    def from_protocol(cls, raw: Any, *, id_attr: str = "model_id") -> ACPModelInfo:
        """Build from a raw ACP ``ModelInfo`` (or any duck-typed object).

        Tolerant of partial/malformed entries: non-string fields degrade to
        ``""`` (``model_id``) or ``None`` (``name``/``description``) rather
        than raising, since the source is an UNSTABLE protocol capability that
        older or half-implemented agents may emit incompletely.

        ``id_attr`` names the attribute carrying the model id — ``"model_id"``
        for a ``models``-capability ``ModelInfo``, ``"value"`` for a
        ``configOptions`` select option.
        """
        if isinstance(raw, ACPModelInfo):
            if id_attr == "model_id":
                return raw
            return cls(
                model_id=raw.model_id,
                name=raw.name,
                description=raw.description,
            )

        model_id = None
        name = None
        description = None

        if isinstance(raw, Mapping):
            model_id = raw.get(id_attr)
            name = raw.get("name")
            description = raw.get("description")
        else:
            if id_attr == "model_id" and isinstance(raw, _ModelInfoWithModelId):
                model_id = raw.model_id
            elif id_attr == "value" and isinstance(raw, _ModelInfoWithValue):
                model_id = raw.value
            elif isinstance(raw, _ModelInfoWithModelId):
                model_id = raw.model_id
            elif isinstance(raw, _ModelInfoWithValue):
                model_id = raw.value

            if isinstance(raw, _ModelInfoWithName):
                name = raw.name
            if isinstance(raw, _ModelInfoWithDescription):
                description = raw.description

        return cls(
            model_id=model_id if isinstance(model_id, str) else "",
            name=name if isinstance(name, str) else None,
            description=description if isinstance(description, str) else None,
        )
