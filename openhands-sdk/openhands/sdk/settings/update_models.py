"""Typed partial-update models for the settings API.

These models replace the legacy ``dict[str, Any]`` request bodies used by
``PATCH /api/settings``. They mirror the persisted settings shape with every
field optional and ``extra="forbid"``, so the OpenAPI schema is meaningful
and typos / stale fields fail loudly with 422 instead of being silently
ignored.

Update semantics
----------------

* A field that is absent from the request body means "do not change this
  field". Sending an explicit ``null`` for an ``Optional`` field on the
  source model means "clear it"; for a non-optional field, ``null`` is a
  422 (rejected by re-validation).
* Nested objects (``llm``, ``condenser``, ``verification``) accept their
  own typed ``*Update`` shape and are merged at the immediate top level of
  that sub-object — they are *not* recursively deep-merged below that. To
  change one field of LLM, send only that field inside ``llm``; the rest
  of the current LLM is preserved.
* Lists and dicts (``tools``, ``acp_env``, ``mcp_config``) **replace** the
  current value when present. Send an empty collection to clear; send the
  full new collection to add/remove members.
* PATCH requires the request body's ``agent_kind`` to match the currently
  persisted variant (treating ``"llm"`` as an alias of ``"openhands"``).
  To switch variants, use ``PUT``.

After applying a partial update, the result is re-validated through the
source model's full validator chain (constraints, secret validators, MCP
config normalisation), so all the usual invariants still hold.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, Literal, Union, get_args

from fastmcp.mcp_config import MCPConfig
from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    Tag,
    TypeAdapter,
    create_model,
)

from openhands.sdk.context.agent_context import AgentContext
from openhands.sdk.llm import LLM
from openhands.sdk.tool import Tool

from .model import (
    ACPAgentSettings,
    CondenserSettings,
    ConversationSettings,
    LLMAgentSettings,
    OpenHandsAgentSettings,
    SecurityAnalyzerType,
    VerificationSettings,
    validate_agent_settings,
)


if TYPE_CHECKING:
    pass


# ── Base ──────────────────────────────────────────────────────────────────


class _UpdateBase(BaseModel):
    """Common config for partial-update models.

    - ``extra="forbid"`` makes typos / stale field names a 422 instead of a
      silent ignore (this is the single biggest fix vs. the legacy
      ``dict[str, Any]`` payload).
    - ``populate_by_name=True`` preserves alias compatibility with the
      source models.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


def _make_partial(model_cls: type[BaseModel], name: str) -> type[_UpdateBase]:
    """Build an all-Optional partial of ``model_cls``.

    Each field is widened to ``T | None`` with default ``None``. Constraints
    on the source field (``ge``, ``le``, regex, etc.) are NOT carried over —
    this model only checks structural shape. Real validation happens when
    the apply helpers re-build the source model from the merged dict.
    """
    fields: dict[str, tuple[Any, Any]] = {}
    for fname, finfo in model_cls.model_fields.items():
        ann = finfo.annotation
        if ann is None:
            continue
        # Widen to Optional unless already nullable
        args = get_args(ann)
        if type(None) in args:
            widened: Any = ann
        else:
            widened = Union[ann, None]  # noqa: UP007 — keep Union for create_model
        fields[fname] = (widened, Field(default=None, description=finfo.description))

    return create_model(  # type: ignore[call-overload,return-value]
        name,
        __base__=_UpdateBase,
        **fields,
    )


# ── Nested partials (auto-generated) ──────────────────────────────────────

LLMUpdate = _make_partial(LLM, "LLMUpdate")
"""All-optional partial of :class:`~openhands.sdk.llm.LLM`.

Sent as the ``llm`` field of an agent settings update. Only fields explicitly
present are applied; the rest of the current LLM (including ``api_key`` and
other secrets) is preserved untouched.
"""

CondenserSettingsUpdate = _make_partial(CondenserSettings, "CondenserSettingsUpdate")
"""All-optional partial of :class:`CondenserSettings`."""

VerificationSettingsUpdate = _make_partial(
    VerificationSettings, "VerificationSettingsUpdate"
)
"""All-optional partial of :class:`VerificationSettings`."""


# ── Agent settings partials (hand-written) ────────────────────────────────


class OpenHandsAgentSettingsUpdate(_UpdateBase):
    """Typed partial update for :class:`OpenHandsAgentSettings`.

    The discriminator literal ``"llm"`` is also accepted for backwards
    compatibility with the deprecated :class:`LLMAgentSettings` alias.
    """

    agent_kind: Literal["openhands", "llm"] = "openhands"
    agent: str | None = None
    llm: LLMUpdate | None = None  # type: ignore[valid-type]
    tools: list[Tool] | None = None
    enable_sub_agents: bool | None = None
    enable_switch_llm_tool: bool | None = None
    mcp_config: MCPConfig | None = None
    agent_context: AgentContext | None = None
    condenser: CondenserSettingsUpdate | None = None  # type: ignore[valid-type]
    verification: VerificationSettingsUpdate | None = None  # type: ignore[valid-type]


class ACPAgentSettingsUpdate(_UpdateBase):
    """Typed partial update for :class:`ACPAgentSettings`."""

    agent_kind: Literal["acp"] = "acp"
    acp_server: str | None = None  # validated against ACPServerKind by re-validation
    acp_command: list[str] | None = None
    acp_args: list[str] | None = None
    acp_env: dict[str, str] | None = None
    acp_model: str | None = None
    acp_session_mode: str | None = None
    acp_prompt_timeout: float | None = None
    llm: LLMUpdate | None = None  # type: ignore[valid-type]
    agent_context: AgentContext | None = None


def _agent_update_discriminator(value: Any) -> str:
    """Map the deprecated ``"llm"`` tag onto ``"openhands"`` for routing."""
    if isinstance(value, BaseModel):
        kind = getattr(value, "agent_kind", "openhands")
    elif isinstance(value, dict):
        kind = value.get("agent_kind", "openhands")
    else:
        return "openhands"
    return "openhands" if kind in ("openhands", "llm") else str(kind)


AgentSettingsUpdate = Annotated[
    Annotated[OpenHandsAgentSettingsUpdate, Tag("openhands")]
    | Annotated[ACPAgentSettingsUpdate, Tag("acp")],
    Discriminator(_agent_update_discriminator),
]
"""Discriminated union of agent settings update variants."""


_AGENT_UPDATE_ADAPTER: TypeAdapter[
    OpenHandsAgentSettingsUpdate | ACPAgentSettingsUpdate
] = TypeAdapter(AgentSettingsUpdate)


def validate_agent_settings_update(
    data: Any,
) -> OpenHandsAgentSettingsUpdate | ACPAgentSettingsUpdate:
    """Validate ``data`` as an :data:`AgentSettingsUpdate` discriminated union."""
    return _AGENT_UPDATE_ADAPTER.validate_python(data)


# ── Conversation settings partial ─────────────────────────────────────────


class ConversationSettingsUpdate(_UpdateBase):
    """Typed partial update for :class:`ConversationSettings`."""

    max_iterations: int | None = Field(default=None, ge=1)
    confirmation_mode: bool | None = None
    security_analyzer: SecurityAnalyzerType | None = None


# ── Apply helpers ─────────────────────────────────────────────────────────


def _normalize_agent_kind(kind: str) -> str:
    """Treat ``"llm"`` and ``"openhands"`` as the same variant."""
    return "openhands" if kind in ("openhands", "llm") else kind


def _explicitly_set_fields(model: BaseModel) -> dict[str, Any]:
    """Return only the fields the caller actually sent.

    Uses ``model_fields_set`` so we distinguish "absent" (no change) from
    "explicitly null" (clear, where allowed). The result is a plain dict —
    nested ``*Update`` instances are themselves reduced to their explicitly
    set fields, so the recursive merge does the right thing.
    """
    out: dict[str, Any] = {}
    for fname in model.model_fields_set:
        value = getattr(model, fname)
        if isinstance(value, _UpdateBase):
            out[fname] = _explicitly_set_fields(value)
        else:
            out[fname] = value
    return out


def apply_agent_update(
    current: OpenHandsAgentSettings | LLMAgentSettings | ACPAgentSettings,
    update: OpenHandsAgentSettingsUpdate | ACPAgentSettingsUpdate,
) -> OpenHandsAgentSettings | LLMAgentSettings | ACPAgentSettings:
    """Apply a typed partial update onto a current agent settings object.

    Variant invariance
    ------------------
    The update's ``agent_kind`` must match ``current.agent_kind`` (treating
    ``"llm"`` and ``"openhands"`` as the same variant). PATCH does not switch
    variants — callers that want to switch should use a full PUT instead.

    Re-validation
    -------------
    After merging the explicitly-set fields onto ``current``'s plaintext
    dump, the merged dict is re-validated through
    :func:`validate_agent_settings`. All field validators, constraints, and
    normalisation (e.g. MCP env decryption) run as usual.

    Raises
    ------
    ValueError
        If the variant differs or re-validation fails.
    """
    current_kind = _normalize_agent_kind(current.agent_kind)
    update_kind = _normalize_agent_kind(update.agent_kind)
    if current_kind != update_kind:
        raise ValueError(
            "PATCH cannot switch agent_kind "
            f"(current={current.agent_kind!r}, requested={update.agent_kind!r}); "
            "use PUT to replace the whole agent settings object."
        )

    # Plaintext-canonical projection of current state.
    base = current.model_dump(mode="json", context={"expose_secrets": "plaintext"})
    overlay = _explicitly_set_fields(update)
    # Drop the discriminator from the overlay; we keep current's value below.
    overlay.pop("agent_kind", None)

    merged: dict[str, Any] = dict(base)
    for key, value in overlay.items():
        existing = base.get(key)
        if isinstance(value, dict) and isinstance(existing, dict):
            # One-level merge: explicit keys in value override existing keys.
            nested = dict(existing)
            nested.update(value)
            merged[key] = nested
        else:
            merged[key] = value

    # Preserve the canonical discriminator (don't accidentally upgrade "llm"
    # to "openhands" — the union routes both to the right class anyway).
    merged["agent_kind"] = current.agent_kind

    return validate_agent_settings(merged)


def apply_conversation_update(
    current: ConversationSettings,
    update: ConversationSettingsUpdate,
) -> ConversationSettings:
    """Apply a typed partial update onto current conversation settings.

    Only fields the client explicitly sent are applied; the rest of
    ``current`` is preserved. The result is re-validated by
    :class:`ConversationSettings` so constraints (``ge=1`` on
    ``max_iterations``, etc.) still fire.
    """
    base = current.model_dump(mode="json")
    overlay = _explicitly_set_fields(update)
    merged = {**base, **overlay}
    return ConversationSettings.model_validate(merged)


__all__ = [
    "ACPAgentSettingsUpdate",
    "AgentSettingsUpdate",
    "CondenserSettingsUpdate",
    "ConversationSettingsUpdate",
    "LLMUpdate",
    "OpenHandsAgentSettingsUpdate",
    "VerificationSettingsUpdate",
    "apply_agent_update",
    "apply_conversation_update",
    "validate_agent_settings_update",
]
