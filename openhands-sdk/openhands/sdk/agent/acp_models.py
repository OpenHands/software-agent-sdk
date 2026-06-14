"""Stable DTOs for ACP session model metadata.

These live in a standalone module — *not* ``acp_agent`` — so the agent-server
can import them for its public ``ConversationInfo`` schema without importing
``ACPAgent``, which would eagerly register it in the agent
``DiscriminatedUnion`` (see ``openhands/sdk/agent/__init__.py``).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


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
            '(e.g. ``"gpt-5.5/xhigh"``) or an opaque alias '
            '(e.g. ``"default"``, ``"auto"``). This is the value to pass to '
            "the provider's model-selection method to switch to this model."
        ),
    )
    name: str | None = Field(
        default=None,
        description='Human-readable label, e.g. ``"GPT-5.5 (xhigh)"``.',
    )
    description: str | None = Field(
        default=None,
        description="Optional longer description supplied by the server.",
    )

    @classmethod
    def from_protocol(cls, raw: Any) -> ACPModelInfo:
        """Build from a raw ACP ``ModelInfo`` (or any duck-typed object).

        Tolerant of partial/malformed entries: non-string fields degrade to
        ``""`` (``model_id``) or ``None`` (``name``/``description``) rather
        than raising, since the source is an UNSTABLE protocol capability that
        older or half-implemented agents may emit incompletely.
        """
        model_id = getattr(raw, "model_id", None)
        name = getattr(raw, "name", None)
        description = getattr(raw, "description", None)
        return cls(
            model_id=model_id if isinstance(model_id, str) else "",
            name=name if isinstance(name, str) else None,
            description=description if isinstance(description, str) else None,
        )
