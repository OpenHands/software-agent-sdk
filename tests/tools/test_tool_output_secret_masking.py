"""Tool output is masked at the shared chokepoint (issue #4677).

``ToolDefinition.__call__`` is the one place every tool's observation passes
through, so masking there covers a new tool by default rather than by
remembering to patch it.
"""

import importlib
import inspect
import pkgutil
from pathlib import Path

import pytest
from pydantic import SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.llm import LLM
from openhands.sdk.tool import Tool, ToolDefinition, register_tool
from openhands.tools.apply_patch.definition import (
    ApplyPatchAction,
    ApplyPatchObservation,
    ApplyPatchTool,
)
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.file_editor.definition import FileEditorAction
from openhands.tools.glob import GlobTool
from openhands.tools.glob.definition import GlobAction, GlobObservation
from openhands.tools.grep import GrepTool
from openhands.tools.grep.definition import GrepAction, GrepObservation


SECRET = "sk-supersecret-value"
MASK = "<secret-hidden>"

_TOOLS = {
    "FileEditorTool": FileEditorTool,
    "GrepTool": GrepTool,
    "GlobTool": GlobTool,
    "ApplyPatchTool": ApplyPatchTool,
}


@pytest.fixture
def conversation(tmp_path: Path) -> LocalConversation:
    for name, cls in _TOOLS.items():
        register_tool(name, cls)
    llm = LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm")
    agent = Agent(llm=llm, tools=[Tool(name=name) for name in _TOOLS])
    convo = LocalConversation(agent, workspace=str(tmp_path))
    convo._ensure_agent_ready()
    convo.update_secrets({"TOKEN": SECRET})
    return convo


def test_file_editor_view_masks_file_contents(
    conversation: LocalConversation, tmp_path: Path
):
    """The headline case: reading a secret-bearing file no longer leaks it."""
    env = tmp_path / ".env"
    env.write_text(f"API_KEY={SECRET}\n")

    action = FileEditorAction(command="view", path=str(env))
    observation = conversation.agent.tools_map["file_editor"](action, conversation)

    assert SECRET not in observation.text
    assert f"API_KEY={MASK}" in observation.text
    # The file itself is untouched — only the returned record is masked.
    assert env.read_text() == f"API_KEY={SECRET}\n"


def test_grep_masks_the_echoed_pattern(conversation: LocalConversation, tmp_path: Path):
    """``grep`` returns paths, not file contents — the leak is the echoed pattern."""
    action = GrepAction(pattern=SECRET, path=str(tmp_path))
    observation = conversation.agent.tools_map["grep"](action, conversation)

    assert isinstance(observation, GrepObservation)
    assert observation.pattern == MASK
    assert SECRET not in observation.text


def test_glob_masks_a_matched_path(conversation: LocalConversation, tmp_path: Path):
    """``glob`` returns paths, so a secret-bearing filename is the leak."""
    (tmp_path / f"note-{SECRET}.txt").write_text("hi\n")

    action = GlobAction(pattern="*.txt", path=str(tmp_path))
    observation = conversation.agent.tools_map["glob"](action, conversation)

    assert isinstance(observation, GlobObservation)
    assert observation.files and all(SECRET not in f for f in observation.files)
    assert SECRET not in observation.text


def test_apply_patch_masks_nested_commit_content(conversation: LocalConversation):
    """The nested-model case ``Observation.content`` masking would have missed.

    The secret lands in ``commit.changes[...].new_content``, not in ``content``.
    """
    patch = (
        f"*** Begin Patch\n*** Add File: secrets.txt\n+TOKEN={SECRET}\n*** End Patch"
    )
    action = ApplyPatchAction(patch=patch)
    observation = conversation.agent.tools_map["apply_patch"](action, conversation)

    assert isinstance(observation, ApplyPatchObservation)
    assert observation.commit is not None
    change = observation.commit.changes["secrets.txt"]
    assert change.new_content == f"TOKEN={MASK}"
    assert SECRET not in observation.model_dump_json()


def test_no_conversation_means_no_masking(tmp_path: Path):
    """Called without a conversation there is no registry, so nothing is masked."""
    for name, cls in _TOOLS.items():
        register_tool(name, cls)
    llm = LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm")
    agent = Agent(llm=llm, tools=[Tool(name="FileEditorTool")])
    convo = LocalConversation(agent, workspace=str(tmp_path))
    convo._ensure_agent_ready()

    env = tmp_path / ".env"
    env.write_text(f"API_KEY={SECRET}\n")
    action = FileEditorAction(command="view", path=str(env))
    observation = convo.agent.tools_map["file_editor"](action, None)

    assert SECRET in observation.text


def _all_tool_definition_subclasses() -> set[type[ToolDefinition]]:
    """Import every shipped tool package, then collect ToolDefinition subclasses."""
    import openhands.tools

    for module in pkgutil.walk_packages(
        openhands.tools.__path__, prefix="openhands.tools."
    ):
        try:
            importlib.import_module(module.name)
        except Exception:  # optional extras must not fail the guard
            continue

    def walk(cls):
        for sub in cls.__subclasses__():
            yield sub
            yield from walk(sub)

    return set(walk(ToolDefinition))


def test_no_tool_bypasses_the_masking_chokepoint():
    """Fails when a new tool overrides __call__ without delegating to super().

    This is the property the issue asks for: tool #14 is covered by default.
    An override that builds its own Observation and returns it silently opts
    out of masking, so it has to go through ``super().__call__``.
    """
    offenders = []
    for cls in _all_tool_definition_subclasses():
        override = cls.__dict__.get("__call__")
        if override is None:
            continue
        try:
            source = inspect.getsource(override)
        except (OSError, TypeError):  # pragma: no cover - source always available
            continue
        if "super().__call__" not in source:
            offenders.append(f"{cls.__module__}.{cls.__qualname__}")

    assert not offenders, (
        "These ToolDefinition subclasses override __call__ without delegating to "
        "super().__call__, so their output never reaches the secret-masking "
        f"chokepoint: {sorted(offenders)}"
    )
