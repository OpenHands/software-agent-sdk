"""SessionStart hook behavior that repository-level setup hooks rely on."""

from pathlib import Path

from pydantic import SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation import LocalConversation
from openhands.sdk.event import HookExecutionEvent, LLMConvertibleEvent, MessageEvent
from openhands.sdk.hooks.config import HookConfig
from openhands.sdk.llm import LLM, Message, TextContent
from tests.command_utils import python_command


MARKER = "SESSION_START_CONTEXT_MARKER_7c1f"


def _session_start_config(command: str) -> HookConfig:
    return HookConfig.from_dict(
        {"session_start": [{"matcher": "*", "hooks": [{"command": command}]}]}
    )


def _conversation(tmp_path: Path, hook_config: HookConfig) -> LocalConversation:
    agent = Agent(llm=LLM(model="test-model", api_key=SecretStr("test-key")), tools=[])
    return LocalConversation(
        agent=agent,
        workspace=str(tmp_path),
        hook_config=hook_config,
        visualizer=None,
    )


def _llm_messages(conversation: LocalConversation) -> list[Message]:
    events = [
        e for e in conversation.state.events if isinstance(e, LLMConvertibleEvent)
    ]
    return LLMConvertibleEvent.events_to_messages(events)


def _message_has_marker(message: Message) -> bool:
    return any(
        isinstance(content, TextContent) and MARKER in content.text
        for content in message.content
    )


def test_session_start_additional_context_reaches_llm_messages(tmp_path):
    """additionalContext must reach the agent without displacing the system prompt."""
    command = python_command(
        f"import json; print(json.dumps({{'additionalContext': {MARKER!r}}}))"
    )
    conversation = _conversation(tmp_path, _session_start_config(command))
    try:
        conversation.send_message("Hello")

        hook_events = [
            e for e in conversation.state.events if isinstance(e, HookExecutionEvent)
        ]
        assert [e.additional_context for e in hook_events] == [MARKER]

        messages = _llm_messages(conversation)
        # Repository-wide invariant: a system message precedes the first user
        # message in every request. SessionStart runs before the agent emits its
        # SystemPromptEvent, so the context must be delivered after it, not before.
        assert messages[0].role == "system"

        marker_indexes = [i for i, m in enumerate(messages) if _message_has_marker(m)]
        assert marker_indexes, "additionalContext did not reach the agent"
        assert all(i > 0 for i in marker_indexes), "context displaced the system prompt"
        assert all(messages[i].role == "user" for i in marker_indexes)

        # Delivered exactly once (no duplicate environment injection).
        injected = [
            e
            for e in conversation.state.events
            if isinstance(e, MessageEvent)
            and e.source == "environment"
            and _message_has_marker(e.llm_message)
        ]
        assert len(injected) == 1
    finally:
        conversation.close()


def test_session_start_hook_env_excludes_sensitive_host_variables(
    tmp_path, monkeypatch
):
    """Hooks run with the sanitized server environment, not the host's secrets."""
    names_file = tmp_path / "hook-env-names.txt"
    command = python_command(
        "import os; from pathlib import Path; "
        f"Path({str(names_file)!r}).write_text('\\n'.join(sorted(os.environ)))"
    )
    monkeypatch.setenv("SESSION_API_KEY", "host-credential")
    conversation = _conversation(tmp_path, _session_start_config(command))
    try:
        conversation.send_message("Hello")

        names = set(names_file.read_text().split("\n"))
        assert {"OPENHANDS_PROJECT_DIR", "OPENHANDS_EVENT_TYPE"} <= names
        assert "SESSION_API_KEY" not in names
    finally:
        conversation.close()


def test_session_start_hook_env_includes_conversation_secrets(tmp_path):
    """A hook command can use the conversation's registered secrets.

    A hook command is never name-scanned, so it cannot be given only the
    secrets it mentions: it receives the whole registry. See #6966.
    """
    names_file = tmp_path / "hook-env-names.txt"
    value_file = tmp_path / "hook-env-value.txt"
    command = python_command(
        "import os; from pathlib import Path; "
        f"Path({str(names_file)!r}).write_text('\\n'.join(sorted(os.environ))); "
        f"Path({str(value_file)!r}).write_text("
        "os.environ.get('AZURE_DEVOPS_TOKEN', ''))"
    )
    conversation = _conversation(tmp_path, _session_start_config(command))
    try:
        conversation.update_secrets({"AZURE_DEVOPS_TOKEN": "not-a-real-token"})
        conversation.send_message("Hello")

        names = set(names_file.read_text().split("\n"))
        assert "AZURE_DEVOPS_TOKEN" in names
        # The hook really resolved the value; the persisted event masks it.
        assert value_file.read_text() == "not-a-real-token"

        hook_events = [
            e for e in conversation.state.events if isinstance(e, HookExecutionEvent)
        ]
        assert hook_events
        for event in hook_events:
            assert "not-a-real-token" not in (event.stdout or "")
    finally:
        conversation.close()
