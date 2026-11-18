from contextlib import closing
from uuid import uuid4

import pytest
from pydantic import ValidationError

from openhands.sdk import LLM, Agent, Conversation, TerminalCredentialBinding
from openhands.sdk.conversation.request import StartConversationRequest
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.settings import ConversationSettings
from openhands.sdk.workspace import LocalWorkspace


@pytest.fixture
def binding():
    return TerminalCredentialBinding(
        consumer="git",
        adapter="git_helper",
        host="GitHub.COM:0443",
        secret_ref="MyKey",
        username="x-access-token",
    )


@pytest.mark.parametrize(
    "host",
    [
        "https://github.com",
        "github.com/repo",
        "user@github.com",
        "*.github.com",
        "github.com\n",
        " github.com",
        "github.com:0",
        "github.com:65536",
        "github.com:",
        "github.com:abc",
        "github.com.",
        "-host",
        "",
        "host?query",
    ],
)
def test_invalid_host(host):
    with pytest.raises(ValidationError):
        TerminalCredentialBinding(
            consumer="git",
            adapter="git_helper",
            host=host,
            secret_ref="KEY",
            username="user",
        )


def test_binding_normalization_and_immutability(binding):
    assert binding.host == "github.com:443"
    assert binding.secret_ref == "MyKey"
    with pytest.raises(ValidationError):
        binding.host = "elsewhere.test"


@pytest.mark.parametrize(
    "changes",
    [
        {"username": None},
        {"username": ""},
        {"username": "bad\nvalue"},
        {"secret_ref": ""},
        {"secret_ref": "\t"},
        {"consumer": "../curl"},
        {"adapter": "unknown"},
        {"env_var": "TOKEN"},
        {"token": "not-a-reference"},
    ],
)
def test_invalid_binding(binding, changes):
    with pytest.raises(ValidationError):
        TerminalCredentialBinding.model_validate({**binding.model_dump(), **changes})


def test_configuration_roundtrip_and_duplicate_rejection(binding, tmp_path):
    agent = Agent(llm=LLM(model="test-model"), tools=[])
    workspace = LocalWorkspace(working_dir=tmp_path)
    settings = ConversationSettings(terminal_credential_bindings=[binding])
    restored = ConversationSettings.from_persisted(settings.model_dump(mode="json"))
    request = restored.create_request(
        StartConversationRequest, agent=agent, workspace=workspace
    )
    assert request.terminal_credential_bindings == [binding]
    assert request.secrets == {}
    assert (
        restored.create_request(
            StartConversationRequest,
            agent=agent,
            workspace=workspace,
            terminal_credential_bindings=[],
        ).terminal_credential_bindings
        == []
    )
    assert ConversationSettings.from_persisted({}).terminal_credential_bindings == []
    duplicate = binding.model_dump() | {"host": "GITHUB.COM:443"}
    for model, kwargs in [
        (ConversationSettings, {}),
        (StartConversationRequest, {"agent": agent, "workspace": workspace}),
        (ConversationState, {"id": uuid4(), "agent": agent, "workspace": workspace}),
    ]:
        with pytest.raises(ValidationError, match="duplicate"):
            model.model_validate(
                {**kwargs, "terminal_credential_bindings": [binding, duplicate]}
            )


def test_local_factory_resume_and_fork(binding, tmp_path):
    agent = Agent(llm=LLM(model="test-model"), tools=[])
    with closing(
        Conversation(
            agent=agent,
            workspace=tmp_path,
            persistence_dir=tmp_path / "conversations",
            terminal_credential_bindings=[binding],
            visualizer=None,
        )
    ) as conversation:
        conversation_id = conversation.id
        assert conversation.state.terminal_credential_bindings == [binding]
        assert conversation.state.secret_registry.get_secret_value("MyKey") is None
        with closing(conversation.fork()) as fork:
            assert fork.state.terminal_credential_bindings == [binding]
    with closing(
        Conversation(
            agent=agent,
            workspace=tmp_path,
            persistence_dir=tmp_path / "conversations",
            conversation_id=conversation_id,
            visualizer=None,
        )
    ) as resumed:
        assert resumed.state.terminal_credential_bindings == [binding]
    with closing(
        Conversation(
            agent=agent,
            workspace=tmp_path,
            persistence_dir=tmp_path / "conversations",
            conversation_id=conversation_id,
            terminal_credential_bindings=[],
            visualizer=None,
        )
    ) as cleared:
        assert cleared.state.terminal_credential_bindings == []


@pytest.mark.parametrize(
    "consumer", ["curl", "aws", "my-company-cli", "gh", "glab", "python3.13"]
)
def test_arbitrary_environment_consumers(consumer):
    binding = TerminalCredentialBinding(
        consumer=consumer, secret_ref="WORK_KEY", env_var="API_KEY"
    )
    assert binding.adapter == "environment"
    assert binding.host is None
    assert (
        TerminalCredentialBinding.model_validate_json(binding.model_dump_json())
        == binding
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"consumer": ""},
        {"consumer": "/usr/bin/curl"},
        {"consumer": "../curl"},
        {"consumer": "curl;env"},
        {"consumer": "config.json"},
        {"consumer": "adapter.py"},
        {"env_var": None},
        {"env_var": ""},
        {"env_var": "KEY=value"},
        {"env_var": "1KEY"},
        {"host": "example.test"},
        {"username": "user"},
        {"adapter": "git_helper"},
    ],
)
def test_invalid_environment_binding(changes):
    with pytest.raises(ValidationError):
        TerminalCredentialBinding.model_validate(
            {
                "consumer": "custom-cli",
                "secret_ref": "KEY",
                "env_var": "TOKEN",
                **changes,
            }
        )


def test_environment_duplicate_targets_and_multiple_secrets():
    first = TerminalCredentialBinding(
        consumer="aws", secret_ref="ID", env_var="AWS_ACCESS_KEY_ID"
    )
    second = TerminalCredentialBinding(
        consumer="aws", secret_ref="SECRET", env_var="AWS_SECRET_ACCESS_KEY"
    )
    assert (
        len(
            ConversationSettings(
                terminal_credential_bindings=[first, second]
            ).terminal_credential_bindings
        )
        == 2
    )
    with pytest.raises(ValidationError, match="duplicate"):
        ConversationSettings(terminal_credential_bindings=[first, first])
