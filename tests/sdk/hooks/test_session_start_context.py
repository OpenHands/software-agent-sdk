"""SessionStart hook behavior that repository-level setup hooks rely on."""

from pathlib import Path

from pydantic import SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation import LocalConversation
from openhands.sdk.event import HookExecutionEvent, LLMConvertibleEvent
from openhands.sdk.hooks.config import HookConfig
from openhands.sdk.llm import LLM, TextContent
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


def _llm_texts(conversation: LocalConversation) -> list[str]:
    events = [
        e for e in conversation.state.events if isinstance(e, LLMConvertibleEvent)
    ]
    return [
        content.text
        for message in LLMConvertibleEvent.events_to_messages(events)
        for content in message.content
        if isinstance(content, TextContent)
    ]


def test_session_start_additional_context_reaches_llm_messages(tmp_path):
    """The documented additionalContext output must reach the agent's prompt."""
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

        assert any(MARKER in text for text in _llm_texts(conversation))
    finally:
        conversation.close()


def test_session_start_hook_env_excludes_conversation_secrets(tmp_path):
    """Hooks run with the sanitized server environment, not conversation secrets."""
    names_file = tmp_path / "hook-env-names.txt"
    command = python_command(
        "import os; from pathlib import Path; "
        f"Path({str(names_file)!r}).write_text('\\n'.join(sorted(os.environ)))"
    )
    conversation = _conversation(tmp_path, _session_start_config(command))
    try:
        conversation.update_secrets({"AZURE_DEVOPS_TOKEN": "not-a-real-token"})
        conversation.send_message("Hello")

        names = set(names_file.read_text().split("\n"))
        assert {"OPENHANDS_PROJECT_DIR", "OPENHANDS_EVENT_TYPE"} <= names
        assert "AZURE_DEVOPS_TOKEN" not in names
    finally:
        conversation.close()
