"""File-based sub-agents discovered from a ``LocalConversation`` workspace."""

from pathlib import Path
from unittest.mock import patch

from pydantic import SecretStr

from openhands.sdk import LLM, Agent
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.llm.llm_profile_store import LLMProfileStore
from openhands.sdk.subagent.registry import (
    _reset_registry_for_tests,
    get_agent_factory,
)
from openhands.sdk.utils.cipher import Cipher


def setup_function() -> None:
    _reset_registry_for_tests()


def teardown_function() -> None:
    _reset_registry_for_tests()


def test_file_agents_decrypt_profiles_with_the_conversation_cipher(
    tmp_path: Path,
) -> None:
    """A workspace agent naming an encrypted profile gets the decrypted key."""
    cipher = Cipher("conversation-profile-key")
    profiles = tmp_path / "profiles"
    LLMProfileStore(base_dir=profiles).save(
        "enc",
        LLM(model="gpt-4o", api_key=SecretStr("sk-profile-secret"), usage_id="enc"),
        include_secrets=True,
        cipher=cipher,
    )
    workspace = tmp_path / "workspace"
    agents_dir = workspace / ".agents" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "worker.md").write_text(
        "---\n"
        "name: profile-worker\n"
        "description: Uses an encrypted profile\n"
        "model: enc\n"
        f"profile_store_dir: {profiles}\n"
        "---\n\n"
        "Do the work.\n"
    )
    llm = LLM(model="test/model", api_key=SecretStr("parent-key"), usage_id="parent")
    conversation = LocalConversation(
        agent=Agent(llm=llm, tools=[]),
        workspace=workspace,
        cipher=cipher,
        visualizer=None,
    )

    with patch(
        "openhands.sdk.subagent.load.Path.home", return_value=tmp_path / "no_user"
    ):
        conversation._register_file_based_agents()

    agent = get_agent_factory("profile-worker").factory_func(llm)
    assert isinstance(agent.llm.api_key, SecretStr)
    assert agent.llm.api_key.get_secret_value() == "sk-profile-secret"
    conversation.close()
