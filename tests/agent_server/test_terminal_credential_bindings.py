from uuid import uuid4

import pytest

from openhands.agent_server.launch import (
    forward_to_runtime,
    scoped_terminal_credential_bindings,
)
from openhands.agent_server.persistence import PersistedSettings
from openhands.sdk import LLM, TerminalCredentialBinding
from openhands.sdk.conversation.request import StartConversationRequest
from openhands.sdk.launch import ResolvedLaunch
from openhands.sdk.profiles.agent_profile import LaunchedAgentProfile
from openhands.sdk.settings import ConversationSettings, OpenHandsAgentSettings
from openhands.sdk.utils.cipher import Cipher
from openhands.sdk.workspace import LocalWorkspace


@pytest.fixture
def bindings():
    return [
        TerminalCredentialBinding(
            consumer="gh", host="github.com", secret_ref="selected"
        ),
        TerminalCredentialBinding(
            consumer="glab", host="gitlab.com", secret_ref="other"
        ),
    ]


@pytest.mark.parametrize("selected, expected", [(None, 2), ([], 0), (["selected"], 1)])
def test_profile_filters_bindings_without_expanding_selection(
    bindings, selected, expected, tmp_path
):
    source = ResolvedLaunch(
        settings=OpenHandsAgentSettings(llm=LLM(model="test-model")),
        profile=LaunchedAgentProfile(
            agent_profile_id=uuid4(), revision=0, secret_refs=selected
        ),
    )
    scoped = scoped_terminal_credential_bindings(bindings, source)
    assert scoped == bindings[:expected]
    request = StartConversationRequest(
        agent_settings=source.settings.model_dump(mode="json"),
        workspace=LocalWorkspace(working_dir=tmp_path),
        terminal_credential_bindings=bindings,
    )
    payload = forward_to_runtime(
        request, source, {}, Cipher("test-key"), load_memory=False
    )
    assert payload["terminal_credential_bindings"] == [
        b.model_dump(mode="json") for b in scoped
    ]
    assert payload["secrets"] == {}
    assert source.profile is not None
    assert source.profile.secret_refs == selected


def test_sparse_settings_patch_replaces_binding_list_atomically(bindings):
    settings = PersistedSettings(
        conversation_settings=ConversationSettings(
            terminal_credential_bindings=bindings
        )
    )
    settings.update({"conversation_settings_diff": {"max_iterations": 42}})
    assert settings.conversation_settings.terminal_credential_bindings == bindings
    with pytest.raises(ValueError):
        settings.update(
            {
                "conversation_settings_diff": {
                    "terminal_credential_bindings": [bindings[0].model_dump()] * 2
                }
            }
        )
    assert settings.conversation_settings.terminal_credential_bindings == bindings
    with pytest.raises(ValueError):
        settings.update(
            {"conversation_settings_diff": {"terminal_credential_bindings": None}}
        )
    settings.update(
        {"conversation_settings_diff": {"terminal_credential_bindings": []}}
    )
    assert settings.conversation_settings.terminal_credential_bindings == []
    assert settings.conversation_settings.max_iterations == 42
