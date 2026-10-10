"""Settings schema structural facts come from canonical JSON Schema."""

from openhands.sdk.llm import LLM
from openhands.sdk.settings.canonical_schema import (
    PRESENTATION_METADATA_KEYS,
    schema_properties,
)
from openhands.sdk.settings.metadata import SETTINGS_METADATA_KEY, SettingsFieldMetadata
from openhands.sdk.settings.model import (
    ACPAgentSettings,
    ConversationSettings,
    LLMSummarizingCondenserSettings,
    NoOpCondenserSettings,
    OpenHandsAgentSettings,
    VerificationSettings,
    export_settings_schema,
)


_PRESENTATION_MODELS = (
    OpenHandsAgentSettings,
    ACPAgentSettings,
    ConversationSettings,
    LLM,
    LLMSummarizingCondenserSettings,
    NoOpCondenserSettings,
    VerificationSettings,
)


def _fields(model: type) -> dict:
    schema = export_settings_schema(model)
    return {field.key: field for section in schema.sections for field in section.fields}


def test_presentation_annotations_do_not_restate_structural_facts() -> None:
    structural = {
        "const",
        "default",
        "enum",
        "exclusiveMaximum",
        "exclusiveMinimum",
        "format",
        "maxLength",
        "maximum",
        "minLength",
        "minimum",
        "type",
    }
    assert set(SettingsFieldMetadata.model_fields) == PRESENTATION_METADATA_KEYS
    for model in _PRESENTATION_MODELS:
        for prop in schema_properties(model).values():
            metadata = prop.get(SETTINGS_METADATA_KEY)
            if not isinstance(metadata, dict):
                continue
            assert structural.isdisjoint(metadata)


def test_numeric_constraints_come_from_json_schema() -> None:
    fields = _fields(OpenHandsAgentSettings)
    max_size = fields["condenser.max_size"]
    assert max_size.value_type == "integer"
    assert max_size.default == 240
    assert max_size.minimum == 20
    assert max_size.exclusive_minimum is None

    progress = fields["condenser.minimum_progress"]
    assert progress.value_type == "number"
    assert progress.exclusive_minimum == 0.0
    assert progress.exclusive_maximum == 1.0

    max_tokens = fields["condenser.max_tokens"]
    assert max_tokens.exclusive_minimum == 0
    assert max_tokens.default is None

    iterations = _fields(ConversationSettings)["max_iterations"]
    assert iterations.minimum == 1
    assert iterations.default == 500


def test_condenser_variant_membership_is_explicit() -> None:
    fields = _fields(OpenHandsAgentSettings)
    kind = fields["condenser.condenser_kind"]
    assert kind.variant_selector is True
    assert [choice.value for choice in kind.choices] == ["llm_summarizing", "no_op"]
    assert kind.applies_to == ["llm_summarizing", "no_op"]

    assert fields["condenser.enabled"].applies_to == ["llm_summarizing", "no_op"]
    assert fields["condenser.enabled"].variant_selector is False
    assert fields["condenser.max_size"].applies_to == ["llm_summarizing"]
    assert "max_size" not in schema_properties(NoOpCondenserSettings)

    # Fields outside a union do not invent variant membership.
    assert fields["llm.model"].applies_to == []
    assert fields["llm.model"].variant_selector is False


def test_secret_and_choices_follow_json_schema() -> None:
    fields = _fields(OpenHandsAgentSettings)
    assert fields["llm.api_key"].secret is True
    assert fields["llm.api_key"].value_type == "string"
    assert [choice.value for choice in fields["verification.critic_mode"].choices] == [
        "finish_and_message",
        "all_actions",
    ]


def test_open_literal_union_stays_free_text() -> None:
    reasoning_effort = _fields(OpenHandsAgentSettings)["llm.reasoning_effort"]
    assert reasoning_effort.choices == []
    assert reasoning_effort.value_type == "string"
    assert reasoning_effort.default == "high"
