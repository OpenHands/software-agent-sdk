from openhands.sdk import LLM, AgentSettings, SettingProminence


# Fields on LLM that have ``exclude=True`` and should not appear in the schema.
_LLM_EXCLUDED_FIELDS = {
    name
    for name, fi in LLM.model_fields.items()
    if fi.exclude
}


def test_agent_settings_export_schema_groups_sections() -> None:
    schema = AgentSettings.export_schema()

    assert schema.model_name == "AgentSettings"
    section_keys = [section.key for section in schema.sections]
    assert section_keys == [
        "general",
        "llm",
        "condenser",
        "critic",
        "security",
    ]

    sections = {s.key: s for s in schema.sections}

    # -- general section (top-level scalar fields) --
    general_fields = {f.key: f for f in sections["general"].fields}
    assert "agent" in general_fields
    assert general_fields["agent"].default == "CodeActAgent"
    assert general_fields["agent"].prominence is SettingProminence.MAJOR

    # -- llm section --
    llm_fields = {f.key: f for f in sections["llm"].fields}
    expected_llm_keys = {
        f"llm.{name}" for name in LLM.model_fields if name not in _LLM_EXCLUDED_FIELDS
    }
    assert set(llm_fields) == expected_llm_keys

    assert llm_fields["llm.model"].required is True
    assert llm_fields["llm.model"].value_type == "string"
    assert llm_fields["llm.model"].prominence is SettingProminence.CRITICAL
    assert llm_fields["llm.api_key"].label == "API Key"
    assert llm_fields["llm.api_key"].value_type == "string"
    assert llm_fields["llm.api_key"].required is False
    assert llm_fields["llm.api_key"].secret is True
    assert llm_fields["llm.api_key"].prominence is SettingProminence.CRITICAL
    assert llm_fields["llm.base_url"].prominence is SettingProminence.CRITICAL
    assert llm_fields["llm.reasoning_effort"].choices[0].value == "low"
    assert llm_fields["llm.reasoning_effort"].prominence is SettingProminence.MAJOR
    assert llm_fields["llm.litellm_extra_body"].value_type == "object"
    assert llm_fields["llm.litellm_extra_body"].default == {}
    assert llm_fields["llm.litellm_extra_body"].prominence is SettingProminence.MINOR
    assert llm_fields["llm.num_retries"].prominence is SettingProminence.MINOR

    # Excluded fields must not appear
    assert "llm.fallback_strategy" not in llm_fields
    assert "llm.retry_listener" not in llm_fields

    # -- condenser section --
    condenser_fields = {f.key: f for f in sections["condenser"].fields}
    assert (
        condenser_fields["condenser.enabled"].prominence is SettingProminence.CRITICAL
    )
    assert condenser_fields["condenser.max_size"].depends_on == ["condenser.enabled"]
    assert condenser_fields["condenser.max_size"].prominence is SettingProminence.MINOR

    # -- critic section --
    critic_fields = {f.key: f for f in sections["critic"].fields}
    assert critic_fields["critic.mode"].value_type == "string"
    assert [choice.value for choice in critic_fields["critic.mode"].choices] == [
        "finish_and_message",
        "all_actions",
    ]
    assert critic_fields["critic.mode"].depends_on == ["critic.enabled"]
    assert critic_fields["critic.mode"].prominence is SettingProminence.MINOR
    assert critic_fields["critic.threshold"].depends_on == [
        "critic.enabled",
        "critic.enable_iterative_refinement",
    ]
    assert critic_fields["critic.threshold"].prominence is SettingProminence.MINOR

    # -- security section --
    security_fields = {f.key: f for f in sections["security"].fields}
    assert security_fields["security.confirmation_mode"].value_type == "boolean"
    assert security_fields["security.confirmation_mode"].default is False
    assert (
        security_fields["security.confirmation_mode"].prominence
        is SettingProminence.MAJOR
    )
    assert security_fields["security.security_analyzer"].choices[0].value == "llm"
    assert security_fields["security.security_analyzer"].depends_on == [
        "security.confirmation_mode",
    ]
