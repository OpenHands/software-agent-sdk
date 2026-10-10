"""A conversation must not undo the condenser split when the agent LLM switches.

``switch_llm`` rebinds the condenser to the new agent model *only* when the
condenser was following the old one (see the config comparison in
``LocalConversation._condenser_for_switched_llm``). A dedicated condenser LLM —
what ``condenser.llm_profile_ref`` embeds at launch (#5469) — is a different
model by design, so a model switch for the agent must leave it alone. These are
regression tests for that boundary.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import SecretStr

from openhands.sdk import LLM, LocalConversation
from openhands.sdk.agent import Agent
from openhands.sdk.context.condenser import LLMSummarizingCondenser
from openhands.sdk.llm import llm_profile_store
from openhands.sdk.llm.llm_profile_store import LLMProfileStore
from openhands.sdk.settings.model import (
    LLMSummarizingCondenserSettings,
    OpenHandsAgentSettings,
)


@pytest.fixture()
def profile_store(tmp_path, monkeypatch):
    profile_dir = tmp_path / "profiles"
    profile_dir.mkdir()
    monkeypatch.setattr(llm_profile_store, "_DEFAULT_PROFILE_DIR", profile_dir)
    return LLMProfileStore(base_dir=profile_dir)


def _condenser_from_settings(
    agent_model: str, condenser_model: str, profile_name: str
) -> LLMSummarizingCondenser:
    """Build the condenser exactly as a profile launch would leave it: the
    settings carry the ref and the embedded resolved LLM."""
    condenser_llm = LLM(
        model=condenser_model,
        api_key=SecretStr("condenser-key"),
        usage_id=profile_name,
    )
    settings = OpenHandsAgentSettings(
        llm=LLM(
            model=agent_model,
            api_key=SecretStr("agent-key"),
            usage_id="default",
        ),
        condenser=LLMSummarizingCondenserSettings(
            llm_profile_ref=profile_name, llm=condenser_llm
        ),
    )
    assert isinstance(settings.condenser, LLMSummarizingCondenserSettings)
    agent = settings.create_agent()
    assert isinstance(agent.condenser, LLMSummarizingCondenser)
    return agent.condenser


def _conversation(tmp_path: Path, condenser: LLMSummarizingCondenser, agent_llm: LLM):
    conv = LocalConversation(
        agent=Agent(llm=agent_llm, condenser=condenser, tools=[]),
        workspace=tmp_path,
    )
    conv._ensure_agent_ready()
    return conv


def _condenser_after(conv) -> LLMSummarizingCondenser:
    assert isinstance(conv.agent.condenser, LLMSummarizingCondenser)
    return conv.agent.condenser


def test_switch_llm_keeps_a_dedicated_condenser_llm(profile_store, tmp_path):
    """The agent model moves; the summarization model does not."""
    condenser = _condenser_from_settings(
        "litellm_proxy/agent-old", "litellm_proxy/cheap", "cheap"
    )
    conv = _conversation(
        tmp_path, condenser, LLM(model="litellm_proxy/agent-old", usage_id="default")
    )

    conv.switch_llm(
        LLM(
            model="litellm_proxy/agent-new",
            api_key=SecretStr("agent-new-key"),
            usage_id="profile:new",
        )
    )

    assert conv.agent.llm.model == "litellm_proxy/agent-new"
    switched = _condenser_after(conv)
    assert switched.llm.model == "litellm_proxy/cheap"
    assert switched.llm.usage_id == "cheap"
    assert isinstance(switched.llm.api_key, SecretStr)
    assert switched.llm.api_key.get_secret_value() == "condenser-key"


def test_switch_profile_keeps_a_dedicated_condenser_llm(profile_store, tmp_path):
    """A profile-store switch (usage_id='profile:{name}') behaves the same."""
    profile_store.save(
        "cheap",
        LLM(model="litellm_proxy/cheap", usage_id="cheap", max_input_tokens=32768),
    )
    condenser = _condenser_from_settings(
        "litellm_proxy/agent-old", "litellm_proxy/cheap", "cheap"
    )
    conv = _conversation(
        tmp_path,
        condenser,
        LLM(model="litellm_proxy/agent-old", usage_id="default"),
    )

    conv.switch_profile("cheap")

    assert conv.agent.llm.model == "litellm_proxy/cheap"
    # The condenser's own copy stays keyed by the profile name, not the
    # switch-target usage_id.
    assert _condenser_after(conv).llm.usage_id == "cheap"
    assert _condenser_after(conv).llm.model == "litellm_proxy/cheap"


def test_switch_llm_still_refreshes_a_following_condenser(profile_store, tmp_path):
    """The original behavior must survive: a condenser with no dedicated LLM
    (usage_id='condenser') tracks the agent's new model."""
    follow = LLMSummarizingCondenser(
        llm=LLM(
            model="litellm_proxy/agent-old",
            usage_id="condenser",
            api_key=SecretStr("old-key"),
        ),
        max_size=100,
    )
    conv = _conversation(
        tmp_path,
        follow,
        LLM(
            model="litellm_proxy/agent-old",
            api_key=SecretStr("old-key"),
            usage_id="default",
        ),
    )

    conv.switch_llm(
        LLM(
            model="litellm_proxy/agent-new",
            api_key=SecretStr("new-key"),
            usage_id="profile:new",
        )
    )

    refreshed = _condenser_after(conv)
    assert refreshed.llm.model == "litellm_proxy/agent-new"
    assert isinstance(refreshed.llm.api_key, SecretStr)
    assert refreshed.llm.api_key.get_secret_value() == "new-key"
