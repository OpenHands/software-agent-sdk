from collections.abc import Sequence
from unittest.mock import MagicMock

import pytest

from openhands.sdk import register_tool
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.llm.message import ImageContent, TextContent
from openhands.sdk.tool import ToolDefinition
from openhands.sdk.tool.client_tool import ClientToolSpec, register_client_tools
from openhands.sdk.tool.registry import (
    list_registered_tools,
    list_tool_catalog,
    list_usable_tools,
    resolve_tool,
    seal_tool_catalog,
)
from openhands.sdk.tool.schema import Action, Observation
from openhands.sdk.tool.spec import Tool
from openhands.sdk.tool.tool import ToolExecutor


def _create_mock_conv_state() -> ConversationState:
    """Create a mock ConversationState for testing."""
    mock_conv_state = MagicMock(spec=ConversationState)
    mock_conv_state.workspace = "workspace/project"
    mock_conv_state.persistence_dir = None
    return mock_conv_state


class _HelloAction(Action):
    name: str


class _HelloObservation(Observation):
    message: str = ""

    @property
    def to_llm_content(self) -> Sequence[TextContent | ImageContent]:
        return [TextContent(text=self.message)]


class _HelloExec(ToolExecutor[_HelloAction, _HelloObservation]):
    def __call__(self, action: _HelloAction, conversation=None) -> _HelloObservation:
        return _HelloObservation(message=f"Hello, {action.name}!")


class _ConfigurableHelloTool(ToolDefinition):
    @classmethod
    def create(
        cls,
        conv_state: ConversationState,
        greeting: str = "Hello",
        punctuation: str = "!",
    ):
        class _ConfigurableExec(ToolExecutor[_HelloAction, _HelloObservation]):
            def __init__(self, greeting: str, punctuation: str) -> None:
                self._greeting: str = greeting
                self._punctuation: str = punctuation

            def __call__(
                self, action: _HelloAction, conversation=None
            ) -> _HelloObservation:
                return _HelloObservation(
                    message=f"{self._greeting}, {action.name}{self._punctuation}"
                )

        return [
            cls(
                description=f"{greeting}{punctuation}",
                action_type=_HelloAction,
                observation_type=_HelloObservation,
                executor=_ConfigurableExec(greeting, punctuation),
            )
        ]


class _SimpleHelloTool(ToolDefinition[_HelloAction, _HelloObservation]):
    """Simple concrete tool for registry testing."""

    @classmethod
    def create(cls, conv_state=None, **params) -> Sequence["_SimpleHelloTool"]:
        return [
            cls(
                description="Says hello",
                action_type=_HelloAction,
                observation_type=_HelloObservation,
                executor=_HelloExec(),
            )
        ]


class _UnavailableHelloTool(_SimpleHelloTool):
    @classmethod
    def is_usable(cls) -> bool:
        return False


class _InternalHelloTool(_SimpleHelloTool):
    user_selectable = False


def _catalog() -> dict[str, dict]:
    return {entry.name: entry.model_dump() for entry in list_tool_catalog()}


def _hello_tool_factory(conv_state=None, **params) -> list[ToolDefinition]:
    return list(_SimpleHelloTool.create(conv_state, **params))


def test_register_tool_rejects_callable_factory():
    # Callable factories were removed in v1.24.0; register_tool now accepts only
    # a ToolDefinition instance or a ToolDefinition subclass. Passing a callable
    # is now both a static type error and a runtime TypeError.
    with pytest.raises(TypeError, match=r"only accepts"):
        register_tool("say_hello", _hello_tool_factory)  # type: ignore[arg-type]


def test_register_tool_type_respects_is_usable():
    register_tool("say_hello_unusable", _UnavailableHelloTool)

    assert "say_hello_unusable" not in list_usable_tools()


def test_register_tool_instance_rejects_params():
    t = _hello_tool_factory()[0]  # Get the single tool from the list
    register_tool("say_hello_instance", t)
    with pytest.raises(ValueError):
        resolve_tool(
            Tool(name="say_hello_instance", params={"x": 1}),
            _create_mock_conv_state(),
        )


def test_register_tool_instance_returns_same_object():
    tool = _hello_tool_factory()[0]  # Get the single tool from the list
    register_tool("say_hello_instance_same", tool)

    resolved_first = resolve_tool(
        Tool(name="say_hello_instance_same"), _create_mock_conv_state()
    )
    resolved_second = resolve_tool(
        Tool(name="say_hello_instance_same"), _create_mock_conv_state()
    )

    assert resolved_first == [tool]
    assert resolved_first[0] is tool
    assert resolved_second[0] is tool


def test_register_tool_type_uses_create_params():
    register_tool("say_configurable_hello_type", _ConfigurableHelloTool)

    tools = resolve_tool(
        Tool(
            name="say_configurable_hello_type",
            params={"greeting": "Howdy", "punctuation": "?"},
        ),
        _create_mock_conv_state(),
    )

    assert len(tools) == 1
    tool = tools[0]
    assert isinstance(tool, _ConfigurableHelloTool)
    assert tool.description == "Howdy?"

    observation = tool(_HelloAction(name="Alice"))
    assert isinstance(observation, _HelloObservation)
    assert observation.message == "Howdy, Alice?"


def test_catalog_reports_selectability_and_usability():
    register_tool("catalog_plain", _SimpleHelloTool)
    register_tool("catalog_internal", _InternalHelloTool)
    register_tool("catalog_unusable", _UnavailableHelloTool)

    catalog = _catalog()

    assert catalog["catalog_plain"] == {
        "name": "catalog_plain",
        "user_selectable": True,
        "usable": True,
    }
    assert catalog["catalog_internal"]["user_selectable"] is False
    assert catalog["catalog_unusable"]["usable"] is False


def test_sealed_catalog_ignores_later_registrations(monkeypatch):
    """Tools a conversation registers vanish on restart, so a server seals the
    catalog once its own tools are loaded."""
    from openhands.sdk.tool import registry

    register_tool("catalog_at_startup", _SimpleHelloTool)
    monkeypatch.setattr(registry, "_CATALOG_NAMES", None)
    seal_tool_catalog()

    register_tool("catalog_after_seal", _SimpleHelloTool)
    register_client_tools(
        [ClientToolSpec(name="catalog_client_tool", description="client side")]
    )

    assert "catalog_at_startup" in _catalog()
    assert "catalog_after_seal" in list_registered_tools()
    assert "catalog_after_seal" not in _catalog()
    assert "catalog_client_tool" not in _catalog()
