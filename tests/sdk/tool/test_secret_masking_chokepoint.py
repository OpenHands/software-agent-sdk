"""Registered secrets must be masked for every tool, not tool by tool.

`ToolDefinition.__call__` is the single place every tool with an executor returns
through, so masking there covers the whole tool surface. Masking inside individual
tools is what left 12 of 13 packages handing raw secrets to the model.
"""

import os
import tempfile

import pytest
from pydantic import SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation import Conversation
from openhands.sdk.llm import LLM
from openhands.sdk.tool.tool import ToolDefinition


SECRET = "sk-test-secret-value-abcdef123456"

# MCPToolDefinition overrides __call__ and masks on its own path
# (openhands-sdk/openhands/sdk/mcp/tool.py). Anything else that overrides it
# leaves the chokepoint and has to say how it masks instead.
_OVERRIDES_ALLOWED_TO_BYPASS = {"MCPToolDefinition"}


def _all_subclasses(cls):
    for sub in cls.__subclasses__():
        yield sub
        yield from _all_subclasses(sub)


@pytest.fixture
def conversation():
    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, ".env"), "w") as handle:
            handle.write(f"API_KEY={SECRET}\n")
        llm = LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test")
        yield Conversation(
            agent=Agent(llm=llm, tools=[]),
            workspace=tmp,
            persistence_dir=tmp,
            secrets={"API_KEY": SECRET},
        )


def _observation_text(observation) -> str:
    return " ".join(
        block.text for block in observation.content if hasattr(block, "text")
    )


def test_file_editor_does_not_hand_the_model_a_raw_secret(conversation):
    from openhands.tools.file_editor import FileEditorTool

    tool = FileEditorTool.create(conv_state=conversation.state)[0]
    target = os.path.join(conversation.state.workspace.working_dir, ".env")

    observation = tool(tool.action_type(command="view", path=target), conversation)

    text = _observation_text(observation)
    assert SECRET not in text
    assert "<secret-hidden>" in text


def test_masking_is_skipped_without_a_conversation(conversation):
    """A direct call with no conversation has no registry to mask against."""
    from openhands.tools.file_editor import FileEditorTool

    tool = FileEditorTool.create(conv_state=conversation.state)[0]
    target = os.path.join(conversation.state.workspace.working_dir, ".env")

    observation = tool(tool.action_type(command="view", path=target), None)

    assert SECRET in _observation_text(observation)


def test_no_tool_leaves_the_masking_chokepoint_unannounced():
    """Fails when a tool starts overriding __call__ without arranging its own masking.

    This is the property the per-tool approach could not give: a tool added later
    is covered by default, and one that opts out of the chokepoint has to be named
    here deliberately.
    """
    import openhands.tools  # noqa: F401  - import for subclass registration

    overriding = {
        sub.__name__
        for sub in _all_subclasses(ToolDefinition)
        if "__call__" in sub.__dict__
    }

    assert overriding <= _OVERRIDES_ALLOWED_TO_BYPASS, (
        f"{sorted(overriding - _OVERRIDES_ALLOWED_TO_BYPASS)} override "
        "ToolDefinition.__call__ and so bypass secret masking; mask on that path "
        "or add the class here with a reason."
    )
