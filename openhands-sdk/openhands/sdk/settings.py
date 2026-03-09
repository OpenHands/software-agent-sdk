from __future__ import annotations

from collections.abc import Callable
from enum import Enum
from typing import Any, Literal, get_args, get_origin

from pydantic import BaseModel, Field, SecretStr
from pydantic.fields import FieldInfo

from openhands.sdk.agent import Agent
from openhands.sdk.context.condenser import LLMSummarizingCondenser
from openhands.sdk.critic import IterativeRefinementConfig
from openhands.sdk.critic.impl.api import APIBasedCritic
from openhands.sdk.llm import LLM


SETTINGS_METADATA_KEY = "openhands_settings"
SETTINGS_SECTION_METADATA_KEY = "openhands_settings_section"


class SettingsSectionMetadata(BaseModel):
    key: str
    label: str
    order: int


class SettingsFieldMetadata(BaseModel):
    label: str
    order: int
    widget: Literal["text", "password", "number", "boolean", "select"] | None = None
    placeholder: str | None = None
    advanced: bool = False
    depends_on: tuple[str, ...] = ()
    help_text: str | None = None
    slash_command: str | None = None


class SettingsChoice(BaseModel):
    value: str
    label: str


class SettingsFieldSchema(BaseModel):
    key: str
    label: str
    description: str | None = None
    section: str
    section_label: str
    order: int
    widget: Literal["text", "password", "number", "boolean", "select"]
    default: bool | int | float | str | None = None
    required: bool = False
    advanced: bool = False
    depends_on: list[str] = Field(default_factory=list)
    help_text: str | None = None
    slash_command: str | None = None
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


class LLMSettings(BaseModel):
    model: str = Field(
        default="claude-sonnet-4-20250514",
        description="Model name for the primary LLM.",
        json_schema_extra={
            SETTINGS_METADATA_KEY: SettingsFieldMetadata(
                label="Model",
                order=10,
                placeholder="anthropic/claude-sonnet-4-5-20250929",
                slash_command="llm-model",
            ).model_dump()
        },
    )
    api_key: SecretStr | None = Field(
        default=None,
        description="API key used to authenticate the primary LLM.",
        json_schema_extra={
            SETTINGS_METADATA_KEY: SettingsFieldMetadata(
                label="API key",
                order=20,
                widget="password",
                slash_command="llm-api-key",
            ).model_dump()
        },
    )
    base_url: str | None = Field(
        default=None,
        description="Optional custom base URL for the primary LLM.",
        json_schema_extra={
            SETTINGS_METADATA_KEY: SettingsFieldMetadata(
                label="Base URL",
                order=30,
                placeholder="https://api.openai.com/v1",
                advanced=True,
                slash_command="llm-base-url",
            ).model_dump()
        },
    )
    timeout: int | None = Field(
        default=300,
        ge=0,
        description="HTTP timeout in seconds for LLM requests.",
        json_schema_extra={
            SETTINGS_METADATA_KEY: SettingsFieldMetadata(
                label="Timeout (seconds)",
                order=40,
                widget="number",
                advanced=True,
                slash_command="llm-timeout",
            ).model_dump()
        },
    )
    max_input_tokens: int | None = Field(
        default=None,
        ge=1,
        description="Optional maximum number of input tokens for the primary LLM.",
        json_schema_extra={
            SETTINGS_METADATA_KEY: SettingsFieldMetadata(
                label="Max input tokens",
                order=50,
                widget="number",
                advanced=True,
                slash_command="llm-max-input-tokens",
            ).model_dump()
        },
    )


class CondenserSettings(BaseModel):
    enabled: bool = Field(
        default=True,
        description="Enable the default LLM summarizing condenser.",
        json_schema_extra={
            SETTINGS_METADATA_KEY: SettingsFieldMetadata(
                label="Enable memory condensation",
                order=10,
                widget="boolean",
                slash_command="condenser",
            ).model_dump()
        },
    )
    max_size: int = Field(
        default=240,
        ge=20,
        description="Maximum number of events kept before the condenser runs.",
        json_schema_extra={
            SETTINGS_METADATA_KEY: SettingsFieldMetadata(
                label="Condenser max size",
                order=20,
                widget="number",
                depends_on=("enabled",),
                help_text="Minimum value is 20.",
                advanced=True,
                slash_command="condenser-max-size",
            ).model_dump()
        },
    )


class CriticSettings(BaseModel):
    enabled: bool = Field(
        default=False,
        description="Enable critic evaluation for the agent.",
        json_schema_extra={
            SETTINGS_METADATA_KEY: SettingsFieldMetadata(
                label="Enable critic",
                order=10,
                widget="boolean",
                slash_command="critic",
            ).model_dump()
        },
    )
    mode: CriticMode = Field(
        default="finish_and_message",
        description="When critic evaluation should run.",
        json_schema_extra={
            SETTINGS_METADATA_KEY: SettingsFieldMetadata(
                label="Critic mode",
                order=20,
                widget="select",
                depends_on=("enabled",),
                advanced=True,
                slash_command="critic-mode",
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
                order=30,
                widget="boolean",
                depends_on=("enabled",),
                slash_command="iterative-refinement",
            ).model_dump()
        },
    )
    threshold: float = Field(
        default=0.6,
        ge=0.0,
        le=1.0,
        description="Critic success threshold used for iterative refinement.",
        json_schema_extra={
            SETTINGS_METADATA_KEY: SettingsFieldMetadata(
                label="Critic threshold",
                order=40,
                widget="number",
                depends_on=("enabled", "enable_iterative_refinement"),
                slash_command="critic-threshold",
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
                order=50,
                widget="number",
                depends_on=("enabled", "enable_iterative_refinement"),
                slash_command="max-refinement-iterations",
            ).model_dump()
        },
    )


CriticFactory = Callable[[LLM, "AgentSettings", Agent | None], APIBasedCritic | None]
AgentFactory = Callable[[LLM], Agent]


class AgentSettings(BaseModel):
    llm: LLMSettings = Field(
        default_factory=LLMSettings,
        description="LLM settings for the agent.",
        json_schema_extra={
            SETTINGS_SECTION_METADATA_KEY: SettingsSectionMetadata(
                key="llm",
                label="LLM",
                order=10,
            ).model_dump()
        },
    )
    condenser: CondenserSettings = Field(
        default_factory=CondenserSettings,
        description="Condenser settings for the agent.",
        json_schema_extra={
            SETTINGS_SECTION_METADATA_KEY: SettingsSectionMetadata(
                key="condenser",
                label="Condenser",
                order=20,
            ).model_dump()
        },
    )
    critic: CriticSettings = Field(
        default_factory=CriticSettings,
        description="Critic settings for the agent.",
        json_schema_extra={
            SETTINGS_SECTION_METADATA_KEY: SettingsSectionMetadata(
                key="critic",
                label="Critic",
                order=30,
            ).model_dump()
        },
    )

    @classmethod
    def from_agent(
        cls,
        agent: Agent,
        *,
        enable_critic: bool | None = None,
    ) -> AgentSettings:
        condenser_defaults = cls.model_fields["condenser"].get_default(
            call_default_factory=True
        )
        condenser_max_size = condenser_defaults.max_size
        condenser_enabled = False
        if isinstance(agent.condenser, LLMSummarizingCondenser):
            condenser_enabled = True
            condenser_max_size = agent.condenser.max_size

        critic = agent.critic
        critic_enabled = (
            enable_critic if enable_critic is not None else critic is not None
        )
        critic_defaults = cls.model_fields["critic"].get_default(
            call_default_factory=True
        )
        critic_mode: CriticMode = critic_defaults.mode
        enable_iterative_refinement = critic_defaults.enable_iterative_refinement
        critic_threshold = critic_defaults.threshold
        max_refinement_iterations = critic_defaults.max_refinement_iterations
        if critic is not None:
            critic_mode = critic.mode
            if critic.iterative_refinement is not None:
                enable_iterative_refinement = True
                critic_threshold = critic.iterative_refinement.success_threshold
                max_refinement_iterations = critic.iterative_refinement.max_iterations

        return cls.model_validate(
            {
                "llm": {
                    "model": agent.llm.model,
                    "api_key": _to_secret(agent.llm.api_key),
                    "base_url": agent.llm.base_url,
                    "timeout": agent.llm.timeout,
                    "max_input_tokens": agent.llm.max_input_tokens,
                },
                "condenser": {
                    "enabled": condenser_enabled,
                    "max_size": condenser_max_size,
                },
                "critic": {
                    "enabled": critic_enabled,
                    "mode": critic_mode,
                    "enable_iterative_refinement": enable_iterative_refinement,
                    "threshold": critic_threshold,
                    "max_refinement_iterations": max_refinement_iterations,
                },
            }
        )

    def apply_to_agent(
        self,
        agent: Agent | None = None,
        *,
        agent_factory: AgentFactory | None = None,
        critic_factory: CriticFactory | None = None,
    ) -> Agent:
        llm_update = {
            "model": self.llm.model,
            "api_key": self.llm.api_key,
            "base_url": self.llm.base_url,
            "timeout": self.llm.timeout,
            "max_input_tokens": self.llm.max_input_tokens,
        }

        base_agent = agent
        if base_agent is None:
            llm = LLM(**llm_update)
            base_agent = (
                agent_factory(llm) if agent_factory is not None else Agent(llm=llm)
            )
        else:
            llm = base_agent.llm.model_copy(update=llm_update)
            base_agent = base_agent.model_copy(update={"llm": llm})

        condenser = base_agent.condenser
        if self.condenser.enabled:
            condenser_llm = llm.model_copy(update={"usage_id": "condenser"})
            if isinstance(condenser, LLMSummarizingCondenser):
                condenser = condenser.model_copy(
                    update={"llm": condenser_llm, "max_size": self.condenser.max_size}
                )
            else:
                condenser = LLMSummarizingCondenser(
                    llm=condenser_llm,
                    max_size=self.condenser.max_size,
                )
        else:
            condenser = None

        critic = base_agent.critic
        if self.critic.enabled:
            iterative_refinement = None
            if self.critic.enable_iterative_refinement:
                iterative_refinement = IterativeRefinementConfig(
                    success_threshold=self.critic.threshold,
                    max_iterations=self.critic.max_refinement_iterations,
                )

            if critic_factory is not None:
                critic = critic_factory(llm, self, base_agent)
                if critic is not None:
                    critic = critic.model_copy(
                        update={
                            "mode": self.critic.mode,
                            "iterative_refinement": iterative_refinement,
                        }
                    )
            elif isinstance(critic, APIBasedCritic):
                critic = critic.model_copy(
                    update={
                        "mode": self.critic.mode,
                        "iterative_refinement": iterative_refinement,
                    }
                )
            else:
                critic = None
        else:
            critic = None

        return base_agent.model_copy(
            update={"llm": llm, "condenser": condenser, "critic": critic}
        )

    def to_agent(
        self,
        agent: Agent | None = None,
        *,
        agent_factory: AgentFactory | None = None,
        critic_factory: CriticFactory | None = None,
    ) -> Agent:
        return self.apply_to_agent(
            agent,
            agent_factory=agent_factory,
            critic_factory=critic_factory,
        )

    @classmethod
    def export_schema(cls) -> SettingsSchema:
        return export_settings_schema(cls)


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


def export_settings_schema(model: type[BaseModel]) -> SettingsSchema:
    sections: list[tuple[int, SettingsSectionSchema]] = []
    for field in model.model_fields.values():
        section_metadata = settings_section_metadata(field)
        if section_metadata is None:
            continue

        nested_model = _nested_model_type(field.annotation)
        if nested_model is None:
            continue

        section_default = field.get_default(call_default_factory=True)
        section = SettingsSectionSchema(
            key=section_metadata.key,
            label=section_metadata.label,
            fields=[],
        )
        for nested_key, nested_field in nested_model.model_fields.items():
            metadata = settings_metadata(nested_field)
            if metadata is None:
                continue

            widget = metadata.widget or _infer_widget(nested_field.annotation)
            default_value = None
            if isinstance(section_default, BaseModel):
                default_value = getattr(section_default, nested_key)
            section.fields.append(
                SettingsFieldSchema(
                    key=f"{section_metadata.key}.{nested_key}",
                    label=metadata.label,
                    description=nested_field.description,
                    section=section_metadata.key,
                    section_label=section_metadata.label,
                    order=metadata.order,
                    widget=widget,
                    default=_normalize_default(default_value),
                    required=not _is_optional(nested_field.annotation),
                    advanced=metadata.advanced,
                    depends_on=[
                        f"{section_metadata.key}.{dependency}"
                        for dependency in metadata.depends_on
                    ],
                    help_text=metadata.help_text,
                    slash_command=metadata.slash_command,
                    secret=widget == "password",
                    choices=_extract_choices(nested_field.annotation),
                )
            )

        section.fields.sort(key=lambda field_schema: field_schema.order)
        sections.append((section_metadata.order, section))

    sections.sort(key=lambda item: item[0])
    return SettingsSchema(
        model_name=model.__name__,
        sections=[section for _, section in sections],
    )


def _nested_model_type(annotation: Any) -> type[BaseModel] | None:
    inner = _strip_optional(annotation)
    if isinstance(inner, type) and issubclass(inner, BaseModel):
        return inner
    return None


def _is_optional(annotation: Any) -> bool:
    origin = get_origin(annotation)
    if origin is None:
        return False
    return any(arg is type(None) for arg in get_args(annotation))


def _strip_optional(annotation: Any) -> Any:
    origin = get_origin(annotation)
    if origin is None:
        return annotation
    args = [arg for arg in get_args(annotation) if arg is not type(None)]
    if len(args) == 1:
        return args[0]
    return annotation


def _infer_widget(
    annotation: Any,
) -> Literal["text", "password", "number", "boolean", "select"]:
    inner = _strip_optional(annotation)
    origin = get_origin(inner)
    if inner is SecretStr:
        return "password"
    if inner is bool:
        return "boolean"
    if inner in (int, float):
        return "number"
    if origin is Literal:
        return "select"
    if isinstance(inner, type) and issubclass(inner, Enum):
        return "select"
    return "text"


def _extract_choices(annotation: Any) -> list[SettingsChoice]:
    inner = _strip_optional(annotation)
    origin = get_origin(inner)
    if origin is Literal:
        return [
            SettingsChoice(value=str(value), label=str(value))
            for value in get_args(inner)
        ]
    if isinstance(inner, type) and issubclass(inner, Enum):
        return [
            SettingsChoice(
                value=str(member.value),
                label=member.name.replace("_", " ").title(),
            )
            for member in inner
        ]
    return []


def _normalize_default(value: Any) -> bool | int | float | str | None:
    if isinstance(value, SecretStr):
        return None
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, (bool, int, float, str)) or value is None:
        return value
    return None


def _to_secret(value: str | SecretStr | None) -> SecretStr | None:
    if value is None:
        return None
    if isinstance(value, SecretStr):
        return value
    return SecretStr(value)
