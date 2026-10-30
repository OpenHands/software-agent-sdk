"""``resolve_llm_credentials`` over the launch-source shapes it accepts."""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from openhands.sdk import LLM, Agent
from openhands.sdk.launch import AgentLaunchError, resolve_llm_credentials
from openhands.sdk.llm.llm_profile_store import LLMProfileStore
from openhands.sdk.llm.provider_connection_store import (
    ProviderConnection,
    ProviderConnectionStore,
)
from openhands.sdk.settings.model import OpenHandsAgentSettings


CONNECTION_ID = "conn"
CONNECTION_KEY = "sk-from-connection"


@pytest.fixture
def profiles(tmp_path):
    provider_store = ProviderConnectionStore(base_dir=tmp_path / "provider-connections")
    provider_store.create(
        ProviderConnection(
            id=CONNECTION_ID,
            display_name="Shared",
            api_key=SecretStr(CONNECTION_KEY),
            base_url="https://provider.example/v1",
            created_at=1,
            updated_at=1,
        )
    )
    return LLMProfileStore(
        base_dir=tmp_path / "profiles", provider_store=provider_store
    )


def _linked(**kwargs) -> LLM:
    return LLM(model="gpt-4o", provider_connection_id=CONNECTION_ID, **kwargs)


def test_an_agent_keeps_its_shape_and_gains_the_connection_credentials(profiles):
    agent = Agent(llm=_linked(), tools=[], tool_concurrency_limit=3)

    resolved = resolve_llm_credentials(agent, lambda: profiles)

    assert isinstance(resolved, Agent)
    assert resolved.tool_concurrency_limit == 3
    assert resolved.llm.api_key is not None
    assert resolved.llm.api_key.get_secret_value() == CONNECTION_KEY
    assert resolved.llm.base_url == "https://provider.example/v1"


def test_settings_are_resolved(profiles):
    settings = OpenHandsAgentSettings(llm=_linked())

    resolved = resolve_llm_credentials(settings, lambda: profiles)

    assert isinstance(resolved, OpenHandsAgentSettings)
    assert resolved.llm.api_key is not None
    assert resolved.llm.api_key.get_secret_value() == CONNECTION_KEY


def test_meta_profile_llms_are_resolved(profiles):
    settings = OpenHandsAgentSettings(
        llm=_linked(), meta_profile_llms={"cheap": _linked(usage_id="cheap")}
    )

    resolved = resolve_llm_credentials(settings, lambda: profiles)

    assert resolved.meta_profile_llms["cheap"].base_url == (
        "https://provider.example/v1"
    )


def test_a_keyless_source_with_no_reference_is_untouched(profiles):
    agent = Agent(llm=LLM(model="gpt-4o", api_key=SecretStr("sk-inline")), tools=[])

    assert resolve_llm_credentials(agent, lambda: profiles) is agent


def test_a_loader_without_the_capability_leaves_the_source_alone():
    """Cloud adapters implement ``LLMProfileLoader`` minimally."""

    class ReadOnlyProfiles:
        def load(self, name: str, *, cipher=None) -> LLM:
            raise FileNotFoundError(name)

    agent = Agent(llm=_linked(), tools=[])

    resolved = resolve_llm_credentials(
        agent,
        lambda: ReadOnlyProfiles(),  # type: ignore[arg-type]
    )

    assert resolved is agent


def test_a_dangling_reference_is_a_launch_error(profiles):
    agent = Agent(llm=LLM(model="gpt-4o", provider_connection_id="ghost"), tools=[])

    with pytest.raises(AgentLaunchError, match="ghost"):
        resolve_llm_credentials(agent, lambda: profiles)
