from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Literal, get_args, get_origin

from fastmcp.mcp_config import MCPConfig
from pydantic import BaseModel, Field, SecretStr, field_serializer, field_validator
from pydantic.fields import FieldInfo

from openhands.sdk.context.agent_context import AgentContext
from openhands.sdk.llm import LLM
from openhands.sdk.tool import Tool

from .metadata import (
    SETTINGS_METADATA_KEY,
    SETTINGS_SECTION_METADATA_KEY,
    SettingProminence,
    SettingsFieldMetadata,
    SettingsSectionMetadata,
)


if TYPE_CHECKING:
    from openhands.sdk.agent import Agent
    from openhands.sdk.context.condenser import LLMSummarizingCondenser
    from openhands.sdk.critic.base import CriticBase


SettingsValueType = Literal[
    "string",
    "integer",
    "number",
    "boolean",
    "array",
    "object",
]
SettingsChoiceValue = bool | int | float | str


class SettingsChoice(BaseModel):
    value: SettingsChoiceValue
    label: str


class SettingsFieldSchema(BaseModel):
    key: str
    label: str
    description: str | None = None
    section: str
    section_label: str
    value_type: SettingsValueType
    default: Any = None
    prominence: SettingProminence = SettingProminence.MINOR
    depends_on: list[str] = Field(default_factory=list)
    secret: bool = False
    choices: list[SettingsChoice] = Field(default_factory=list)


class SettingsSectionSchema(BaseModel):
    key: str
    label: str
    fields: list[SettingsFieldSchema]


class SettingsSchema(BaseModel):
    model_name: str
    sections: list[SettingsSectionSchema]


CriticMode = Literal["finish_and_message", "all_actions"]
SecurityAnalyzerType = Literal["llm", "none"]


class CondenserSettings(BaseModel):
    enabled: bool = Field(
        default=True,
        description="Enable the LLM summarizing condenser.",
        json_schema_extra={
            SETTINGS_METADATA_KEY: SettingsFieldMetadata(
                label="Enable memory condensation",
                prominence=SettingProminence.CRITICAL,
            ).model_dump()
        },
    )
    max_size: int = Field(
        default=240,
        ge=20,
        description="Maximum number of events kept before the condenser runs.",
        json_schema_extra={
            SETTINGS_METADATA_KEY: SettingsFieldMetadata(
                label="Max size",
                prominence=SettingProminence.MINOR,
                depends_on=("enabled",),
            ).model_dump()
        },
    )


class VerificationSettings(BaseModel):
    """Combined critic and security settings."""

    # -- Critic --
    critic_enabled: bool = Field(
        default=False,
        description="Enable critic evaluation for the agent.",
        json_schema_extra={
            SETTINGS_METADATA_KEY: SettingsFieldMetadata(
                label="Enable critic",
                prominence=SettingProminence.CRITICAL,
            ).model_dump()
        },
    )
    critic_mode: CriticMode = Field(
        default="finish_and_message",
        description="When critic evaluation should run.",
        json_schema_extra={
            SETTINGS_METADATA_KEY: SettingsFieldMetadata(
                label="Critic mode",
                prominence=SettingProminence.MINOR,
                depends_on=("critic_enabled",),
            ).model_dump()
        },
    )
    enable_iterative_refinement: bool = Field(
        default=False,
        description=(
            "Automatically retry tasks when critic scores fall below the threshold."
        ),
        json_schema_extra={
            SETTINGS_METADATA_KEY: SettingsFieldMetadata(
                label="Enable iterative refinement",
                depends_on=("critic_enabled",),
            ).model_dump()
        },
    )
    critic_threshold: float = Field(
        default=0.6,
        ge=0.0,
        le=1.0,
        description="Critic success threshold used for iterative refinement.",
        json_schema_extra={
            SETTINGS_METADATA_KEY: SettingsFieldMetadata(
                label="Critic threshold",
                prominence=SettingProminence.MINOR,
                depends_on=("critic_enabled", "enable_iterative_refinement"),
            ).model_dump()
        },
    )
    max_refinement_iterations: int = Field(
        default=3,
        ge=1,
        description="Maximum number of refinement attempts after critic feedback.",
        json_schema_extra={
            SETTINGS_METADATA_KEY: SettingsFieldMetadata(
                label="Max refinement iterations",
                prominence=SettingProminence.MINOR,
                depends_on=("critic_enabled", "enable_iterative_refinement"),
            ).model_dump()
        },
    )

    # -- Critic deployment --
    critic_server_url: str | None = Field(
        default=None,
        description=(
            "Override the critic service URL. "
            "When None, the APIBasedCritic default is used."
        ),
        json_schema_extra={
            SETTINGS_METADATA_KEY: SettingsFieldMetadata(
                label="Critic server URL",
                prominence=SettingProminence.MINOR,
                depends_on=("critic_enabled",),
            ).model_dump()
        },
    )
    critic_model_name: str | None = Field(
        default=None,
        description=(
            "Override the critic model name. "
            "When None, the APIBasedCritic default is used."
        ),
        json_schema_extra={
            SETTINGS_METADATA_KEY: SettingsFieldMetadata(
                label="Critic model name",
                prominence=SettingProminence.MINOR,
                depends_on=("critic_enabled",),
            ).model_dump()
        },
    )

    # -- Security --
    confirmation_mode: bool = Field(
        default=False,
        description="Require user confirmation before executing risky actions.",
        json_schema_extra={
            SETTINGS_METADATA_KEY: SettingsFieldMetadata(
                label="Confirmation mode",
                prominence=SettingProminence.MAJOR,
            ).model_dump()
        },
    )
    security_analyzer: SecurityAnalyzerType | None = Field(
        default=None,
        description="Security analyzer that evaluates actions before execution.",
        json_schema_extra={
            SETTINGS_METADATA_KEY: SettingsFieldMetadata(
                label="Security analyzer",
                prominence=SettingProminence.MAJOR,
                depends_on=("confirmation_mode",),
            ).model_dump()
        },
    )


def _default_llm_settings() -> LLM:
    model = LLM.model_fields["model"].get_default()
    assert isinstance(model, str)
    return LLM(model=model)


# Canonical persisted AgentSettings payload contract:
# - v1 (legacy): flat dotted OpenHands mapping or unversioned nested payload
# - v2 (current): standard AgentSettings JSON including `schema_version`
_LEGACY_AGENT_SETTINGS_VERSION = 1
_CURRENT_AGENT_SETTINGS_VERSION = 2
_PERSISTED_AGENT_SETTINGS_VERSION_KEY = "schema_version"
_LEGACY_WRAPPED_SETTINGS_VERSION_KEY = "version"
_LEGACY_WRAPPED_SETTINGS_SETTINGS_KEY = "settings"


_MISSING = object()


def _assign_dotted_value(target: dict[str, Any], dotted_key: str, value: Any) -> None:
    current = target
    parts = dotted_key.split(".")
    for part in parts[:-1]:
        current = current.setdefault(part, {})
    current[parts[-1]] = deepcopy(value)


def _normalize_legacy_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key, value in payload.items():
        if key == _PERSISTED_AGENT_SETTINGS_VERSION_KEY:
            continue
        if "." in key:
            _assign_dotted_value(normalized, key, value)
            continue
        normalized[key] = deepcopy(value)
    return normalized


def _migrate_agent_settings_v1_to_v2(payload: Mapping[str, Any]) -> dict[str, Any]:
    migrated = _normalize_legacy_payload(payload)
    migrated[_PERSISTED_AGENT_SETTINGS_VERSION_KEY] = _CURRENT_AGENT_SETTINGS_VERSION
    return migrated


_AGENT_SETTINGS_MIGRATIONS: dict[int, Callable[[Mapping[str, Any]], dict[str, Any]]] = {
    _LEGACY_AGENT_SETTINGS_VERSION: _migrate_agent_settings_v1_to_v2,
}


def _coerce_persisted_agent_settings_payload(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    if (
        _LEGACY_WRAPPED_SETTINGS_VERSION_KEY in payload
        or _LEGACY_WRAPPED_SETTINGS_SETTINGS_KEY in payload
    ):
        settings_payload = payload.get(_LEGACY_WRAPPED_SETTINGS_SETTINGS_KEY)
        if not isinstance(settings_payload, Mapping):
            raise TypeError(
                "Persisted AgentSettings settings payload must be a mapping."
            )
        version = payload.get(_LEGACY_WRAPPED_SETTINGS_VERSION_KEY)
        if version is None:
            return dict(settings_payload)
        if not isinstance(version, int) or isinstance(version, bool):
            raise TypeError(
                "Persisted AgentSettings version must be an integer when provided."
            )
        migrated_payload = dict(settings_payload)
        migrated_payload[_PERSISTED_AGENT_SETTINGS_VERSION_KEY] = version
        return migrated_payload

    return dict(payload)


def _migrate_persisted_agent_settings_payload(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    migrated_payload = _coerce_persisted_agent_settings_payload(payload)
    version = migrated_payload.get(
        _PERSISTED_AGENT_SETTINGS_VERSION_KEY, _LEGACY_AGENT_SETTINGS_VERSION
    )
    if not isinstance(version, int) or isinstance(version, bool):
        raise TypeError("Persisted AgentSettings schema_version must be an integer.")
    if version < _LEGACY_AGENT_SETTINGS_VERSION:
        raise ValueError(f"Unsupported persisted AgentSettings version {version}.")
    if version > _CURRENT_AGENT_SETTINGS_VERSION:
        raise ValueError(
            "Persisted AgentSettings version is newer than this SDK supports."
        )

    while version < _CURRENT_AGENT_SETTINGS_VERSION:
        migrator = _AGENT_SETTINGS_MIGRATIONS.get(version)
        if migrator is None:
            raise ValueError(f"Missing AgentSettings migrator for version {version}.")
        migrated_payload = migrator(migrated_payload)
        version = migrated_payload[_PERSISTED_AGENT_SETTINGS_VERSION_KEY]

    return migrated_payload


def _normalize_patch_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    if (
        _LEGACY_WRAPPED_SETTINGS_VERSION_KEY in payload
        or _LEGACY_WRAPPED_SETTINGS_SETTINGS_KEY in payload
    ):
        wrapped_payload = payload.get(_LEGACY_WRAPPED_SETTINGS_SETTINGS_KEY)
        if not isinstance(wrapped_payload, Mapping):
            raise TypeError("AgentSettings patch payload must be a mapping.")
        payload = wrapped_payload

    normalized: dict[str, Any] = {}
    for key, value in payload.items():
        if key == _PERSISTED_AGENT_SETTINGS_VERSION_KEY:
            continue
        if "." in key:
            _assign_dotted_value(normalized, key, value)
            continue
        normalized[key] = deepcopy(value)
    return normalized


def _merge_patch_payload(
    base: Mapping[str, Any], patch: Mapping[str, Any]
) -> dict[str, Any]:
    merged = deepcopy(dict(base))
    for key, value in patch.items():
        base_value = merged.get(key)
        if isinstance(base_value, dict) and isinstance(value, Mapping):
            merged[key] = _merge_patch_payload(base_value, value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _diff_payload(base: Mapping[str, Any], target: Mapping[str, Any]) -> dict[str, Any]:
    diff: dict[str, Any] = {}
    for key in sorted(set(base) | set(target)):
        if key == _PERSISTED_AGENT_SETTINGS_VERSION_KEY:
            continue
        base_value = base.get(key, _MISSING)
        target_value = target.get(key, _MISSING)

        if target_value is _MISSING:
            continue
        if base_value is _MISSING:
            diff[key] = deepcopy(target_value)
            continue
        if isinstance(base_value, Mapping) and isinstance(target_value, Mapping):
            nested_diff = _diff_payload(base_value, target_value)
            if nested_diff:
                diff[key] = nested_diff
            continue
        if base_value != target_value:
            diff[key] = deepcopy(target_value)
    return diff


class AgentSettings(BaseModel):
    CURRENT_PERSISTED_VERSION: ClassVar[int] = _CURRENT_AGENT_SETTINGS_VERSION

    schema_version: int = Field(default=_CURRENT_AGENT_SETTINGS_VERSION, ge=1)
    agent: str = Field(
        default="CodeActAgent",
        description="Agent class to use.",
        json_schema_extra={
            SETTINGS_METADATA_KEY: SettingsFieldMetadata(
                label="Agent",
                prominence=SettingProminence.MAJOR,
            ).model_dump()
        },
    )
    llm: LLM = Field(
        default_factory=_default_llm_settings,
        description="LLM settings for the agent.",
        json_schema_extra={
            SETTINGS_SECTION_METADATA_KEY: SettingsSectionMetadata(
                key="llm",
                label="LLM",
            ).model_dump()
        },
    )
    tools: list[Tool] = Field(
        default_factory=list,
        description="Tools available to the agent.",
        json_schema_extra={
            SETTINGS_METADATA_KEY: SettingsFieldMetadata(
                label="Tools",
                prominence=SettingProminence.MAJOR,
            ).model_dump()
        },
    )
    mcp_config: MCPConfig | None = Field(
        default=None,
        description="MCP server configuration for the agent.",
        json_schema_extra={
            SETTINGS_METADATA_KEY: SettingsFieldMetadata(
                label="MCP configuration",
                prominence=SettingProminence.MINOR,
            ).model_dump()
        },
    )
    agent_context: AgentContext = Field(
        default_factory=AgentContext,
        description="Context for the agent (skills, secrets, message suffixes).",
    )
    condenser: CondenserSettings = Field(
        default_factory=CondenserSettings,
        description="Condenser settings for the agent.",
        json_schema_extra={
            SETTINGS_SECTION_METADATA_KEY: SettingsSectionMetadata(
                key="condenser",
                label="Condenser",
            ).model_dump()
        },
    )
    verification: VerificationSettings = Field(
        default_factory=VerificationSettings,
        description="Verification settings (critic + security) for the agent.",
        json_schema_extra={
            SETTINGS_SECTION_METADATA_KEY: SettingsSectionMetadata(
                key="verification",
                label="Verification",
            ).model_dump()
        },
    )

    @field_validator("mcp_config", mode="before")
    @classmethod
    def _normalize_empty_mcp_config(cls, value: Any) -> Any:
        if value in (None, {}):
            return None
        return value

    @field_serializer("mcp_config")
    def _serialize_mcp_config(self, value: MCPConfig | None) -> dict[str, Any]:
        if value is None:
            return {}
        return value.model_dump(exclude_none=True, exclude_defaults=True)

    @classmethod
    def export_schema(cls) -> SettingsSchema:
        """Export a structured schema describing configurable agent settings."""
        return export_settings_schema(cls)

    @classmethod
    def migrate_persisted_payload(cls, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Return the latest canonical persisted AgentSettings payload."""
        return _migrate_persisted_agent_settings_payload(payload)

    @classmethod
    def from_persisted(cls, payload: Mapping[str, Any]) -> AgentSettings:
        """Load persisted AgentSettings after applying SDK-owned migrations."""
        return cls.model_validate(cls.migrate_persisted_payload(payload))

    def patch(self, payload: Mapping[str, Any]) -> AgentSettings:
        """Return a new settings object with a persisted patch applied.

        The patch payload may be sparse and may use either the current nested
        representation or the legacy flat dotted representation.
        """
        base_payload = self.model_dump(mode="json", context={"expose_secrets": True})
        merged_payload = _merge_patch_payload(
            base_payload, _normalize_patch_payload(payload)
        )
        merged_payload[_PERSISTED_AGENT_SETTINGS_VERSION_KEY] = (
            self.CURRENT_PERSISTED_VERSION
        )
        return type(self).from_persisted(merged_payload)

    def diff(self, target: AgentSettings | Mapping[str, Any]) -> dict[str, Any]:
        """Return the minimal persisted patch from these settings to ``target``."""
        target_settings = (
            target
            if isinstance(target, AgentSettings)
            else type(self).from_persisted(target)
        )
        base_payload = self.model_dump(mode="json", context={"expose_secrets": True})
        target_payload = target_settings.model_dump(
            mode="json", context={"expose_secrets": True}
        )
        return _diff_payload(base_payload, target_payload)

    def create_agent(self) -> Agent:
        """Build an :class:`Agent` purely from these settings.

        Example::

            settings = AgentSettings(
                llm=LLM(model="m", api_key="k"),
                tools=[Tool(name="TerminalTool")],
            )
            agent = settings.create_agent()
        """
        from openhands.sdk.agent import Agent

        return Agent(
            llm=self.llm,
            tools=self.tools,
            mcp_config=self._serialize_mcp_config(self.mcp_config),
            agent_context=self.agent_context,
            condenser=self.build_condenser(self.llm),
            critic=self.build_critic(),
        )

    def build_condenser(self, llm: LLM) -> LLMSummarizingCondenser | None:
        """Create a condenser from these settings, or ``None`` if disabled."""
        if not self.condenser.enabled:
            return None

        from openhands.sdk.context.condenser import LLMSummarizingCondenser

        return LLMSummarizingCondenser(llm=llm, max_size=self.condenser.max_size)

    def build_critic(self) -> CriticBase | None:
        """Create an :class:`APIBasedCritic` from these settings.

        Returns ``None`` when the critic is disabled or when the LLM
        has no ``api_key`` (the critic service requires authentication).

        If ``verification.critic_server_url`` or
        ``verification.critic_model_name`` are set they override the
        ``APIBasedCritic`` defaults, allowing deployments to route
        through a custom endpoint (e.g. an LLM proxy).
        """
        if not self.verification.critic_enabled:
            return None

        api_key = self.llm.api_key
        if api_key is None:
            return None

        from openhands.sdk.critic.base import IterativeRefinementConfig
        from openhands.sdk.critic.impl.api import APIBasedCritic

        iterative_refinement = None
        if self.verification.enable_iterative_refinement:
            iterative_refinement = IterativeRefinementConfig(
                success_threshold=self.verification.critic_threshold,
                max_iterations=self.verification.max_refinement_iterations,
            )

        overrides: dict[str, Any] = {}
        if self.verification.critic_server_url is not None:
            overrides["server_url"] = self.verification.critic_server_url
        if self.verification.critic_model_name is not None:
            overrides["model_name"] = self.verification.critic_model_name

        return APIBasedCritic(
            api_key=api_key,
            mode=self.verification.critic_mode,
            iterative_refinement=iterative_refinement,
            **overrides,
        )


def settings_section_metadata(field: FieldInfo) -> SettingsSectionMetadata | None:
    extra = field.json_schema_extra
    if not isinstance(extra, dict):
        return None

    metadata = extra.get(SETTINGS_SECTION_METADATA_KEY)
    if metadata is None:
        return None
    return SettingsSectionMetadata.model_validate(metadata)


def settings_metadata(field: FieldInfo) -> SettingsFieldMetadata | None:
    extra = field.json_schema_extra
    if not isinstance(extra, dict):
        return None

    metadata = extra.get(SETTINGS_METADATA_KEY)
    if metadata is None:
        return None
    return SettingsFieldMetadata.model_validate(metadata)


_GENERAL_SECTION_KEY = "general"
_GENERAL_SECTION_LABEL = "General"


def export_settings_schema(model: type[BaseModel]) -> SettingsSchema:
    """Export a structured settings schema for a Pydantic settings model.

    The returned schema groups nested models into sections and describes each
    exported field with its label, type, default, dependencies, choices, and
    whether the value should be treated as secret input.
    """
    sections: list[SettingsSectionSchema] = []
    general_fields: list[SettingsFieldSchema] = []

    for field_name, field in model.model_fields.items():
        section_metadata = settings_section_metadata(field)

        # Nested section (e.g., llm, condenser, critic, security)
        if section_metadata is not None:
            nested_model = _nested_model_type(field.annotation)
            if nested_model is None:
                continue

            section_default = field.get_default(call_default_factory=True)
            section_label = section_metadata.label or _humanize_name(
                section_metadata.key
            )
            section = SettingsSectionSchema(
                key=section_metadata.key,
                label=section_label,
                fields=[],
            )
            for nested_key, nested_field in nested_model.model_fields.items():
                if nested_field.exclude:
                    continue
                metadata = settings_metadata(nested_field)
                default_value = None
                if isinstance(section_default, BaseModel):
                    default_value = getattr(section_default, nested_key)
                section.fields.append(
                    SettingsFieldSchema(
                        key=f"{section_metadata.key}.{nested_key}",
                        label=(
                            metadata.label
                            if metadata is not None and metadata.label is not None
                            else _humanize_name(nested_key)
                        ),
                        description=nested_field.description,
                        section=section_metadata.key,
                        section_label=section_label,
                        value_type=_infer_value_type(nested_field.annotation),
                        default=_normalize_default(default_value),
                        prominence=(
                            metadata.prominence
                            if metadata is not None
                            else SettingProminence.MINOR
                        ),
                        depends_on=[
                            f"{section_metadata.key}.{dependency}"
                            for dependency in (
                                metadata.depends_on if metadata is not None else ()
                            )
                        ],
                        secret=_contains_secret(nested_field.annotation),
                        choices=_extract_choices(nested_field.annotation),
                    )
                )
            sections.append(section)
            continue

        # Top-level scalar field with settings metadata (e.g., agent)
        metadata = settings_metadata(field)
        if metadata is None:
            continue

        default_value = field.get_default(call_default_factory=True)
        general_fields.append(
            SettingsFieldSchema(
                key=field_name,
                label=(
                    metadata.label
                    if metadata.label is not None
                    else _humanize_name(field_name)
                ),
                description=field.description,
                section=_GENERAL_SECTION_KEY,
                section_label=_GENERAL_SECTION_LABEL,
                value_type=_infer_value_type(field.annotation),
                default=_normalize_default(default_value),
                prominence=metadata.prominence,
                depends_on=list(metadata.depends_on),
                secret=_contains_secret(field.annotation),
                choices=_extract_choices(field.annotation),
            )
        )

    if general_fields:
        sections.insert(
            0,
            SettingsSectionSchema(
                key=_GENERAL_SECTION_KEY,
                label=_GENERAL_SECTION_LABEL,
                fields=general_fields,
            ),
        )

    return SettingsSchema(model_name=model.__name__, sections=sections)


def _nested_model_type(annotation: Any) -> type[BaseModel] | None:
    candidates = _annotation_options(annotation)
    if len(candidates) != 1:
        return None

    candidate = candidates[0]
    if isinstance(candidate, type) and issubclass(candidate, BaseModel):
        return candidate
    return None


def _annotation_options(annotation: Any) -> tuple[Any, ...]:
    origin = get_origin(annotation)
    if origin is None or origin is Literal:
        return (annotation,)
    if origin in (list, tuple, set, frozenset, dict):
        return (annotation,)

    options: list[Any] = []
    for arg in get_args(annotation):
        if arg is type(None):
            continue
        options.extend(_annotation_options(arg))
    return tuple(options) or (annotation,)


def _contains_secret(annotation: Any) -> bool:
    return any(option is SecretStr for option in _annotation_options(annotation))


def _infer_value_type(annotation: Any) -> SettingsValueType:
    choices = _choice_values(annotation)
    if choices:
        return _value_type_for_values(choices)

    options = _annotation_options(annotation)
    if all(_is_stringish(option) for option in options):
        return "string"
    if all(option is bool for option in options):
        return "boolean"
    if all(option is int for option in options):
        return "integer"
    if all(option in (int, float) for option in options):
        return "number"
    if all(_is_array_annotation(option) for option in options):
        return "array"
    if all(_is_object_annotation(option) for option in options):
        return "object"
    return "string"


def _is_stringish(annotation: Any) -> bool:
    return annotation in (str, SecretStr, Path)


def _is_array_annotation(annotation: Any) -> bool:
    return get_origin(annotation) in (list, tuple, set, frozenset)


def _is_object_annotation(annotation: Any) -> bool:
    origin = get_origin(annotation)
    if origin is dict:
        return True
    return isinstance(annotation, type) and issubclass(annotation, BaseModel)


def _choice_values(annotation: Any) -> list[SettingsChoiceValue]:
    inner = _annotation_options(annotation)
    if len(inner) != 1:
        return []

    candidate = inner[0]
    origin = get_origin(candidate)
    if origin is Literal:
        return [
            value
            for value in get_args(candidate)
            if isinstance(value, (bool, int, float, str))
        ]
    if isinstance(candidate, type) and issubclass(candidate, Enum):
        return [
            member.value
            for member in candidate
            if isinstance(member.value, (bool, int, float, str))
        ]
    return []


def _value_type_for_values(values: list[SettingsChoiceValue]) -> SettingsValueType:
    if all(isinstance(value, bool) for value in values):
        return "boolean"
    if all(isinstance(value, int) and not isinstance(value, bool) for value in values):
        return "integer"
    if all(
        isinstance(value, (int, float)) and not isinstance(value, bool)
        for value in values
    ):
        return "number"
    return "string"


def _extract_choices(annotation: Any) -> list[SettingsChoice]:
    inner = _annotation_options(annotation)
    if len(inner) != 1:
        return []

    candidate = inner[0]
    origin = get_origin(candidate)
    if origin is Literal:
        return [
            SettingsChoice(value=value, label=str(value))
            for value in get_args(candidate)
            if isinstance(value, (bool, int, float, str))
        ]
    if isinstance(candidate, type) and issubclass(candidate, Enum):
        return [
            SettingsChoice(
                value=member.value,
                label=_humanize_name(member.name),
            )
            for member in candidate
            if isinstance(member.value, (bool, int, float, str))
        ]
    return []


def _normalize_default(value: Any) -> Any:
    if isinstance(value, SecretStr):
        return None
    if isinstance(value, Enum):
        return _normalize_default(value.value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _normalize_default(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_normalize_default(item) for item in value]
    if isinstance(value, (bool, int, float, str)) or value is None:
        return value
    return None


def _humanize_name(name: str) -> str:
    acronyms = {"api", "aws", "id", "llm", "url"}
    words = []
    for part in name.split("_"):
        words.append(part.upper() if part in acronyms else part.capitalize())
    return " ".join(words)
