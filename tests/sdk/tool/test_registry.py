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
    unseal_tool_catalog,
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


class _DescribedHelloTool(_SimpleHelloTool):
    catalog_description = "Say hello, briefly."


class _InternalHelloTool(_SimpleHelloTool):
    user_selectable = False


@pytest.fixture(autouse=True)
def _unsealed_catalog(monkeypatch):
    from openhands.sdk.tool import registry

    monkeypatch.setattr(registry, "_SEALED_CATALOG", None)


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
        "description": "",
        "in_default_set": False,
    }
    assert catalog["catalog_internal"]["user_selectable"] is False
    assert catalog["catalog_unusable"]["usable"] is False


def test_catalog_offers_a_builtin_under_its_snake_case_name(monkeypatch):
    """A built-in is keyed by class name internally but offered like any tool."""
    from openhands.sdk.tool import builtins

    catalog = _catalog()

    assert "switch_llm" in catalog
    assert "SwitchLLMTool" not in catalog
    assert builtins.SwitchLLMTool.name == "switch_llm"


def test_catalog_marks_the_default_set(monkeypatch):
    register_tool("catalog_extra", _SimpleHelloTool)
    catalog = _catalog()

    assert catalog["switch_llm"]["in_default_set"] is True
    assert catalog["catalog_extra"]["in_default_set"] is False


def test_catalog_does_not_offer_the_meta_profile_router(monkeypatch):
    entry = _catalog().get("route_task_to_model")

    assert entry is None or entry["user_selectable"] is False


def test_a_builtin_registered_by_class_name_is_offered_once(monkeypatch):
    """A built-in registered under its class name is listed once."""
    from openhands.sdk.tool import builtins

    monkeypatch.setitem(
        builtins.BUILT_IN_TOOL_CLASSES,
        _DescribedHelloTool.__name__,
        _DescribedHelloTool,
    )
    register_tool(_DescribedHelloTool.__name__, _DescribedHelloTool)

    names = [entry.name for entry in list_tool_catalog()]

    assert _DescribedHelloTool.__name__ not in names
    assert names.count(_DescribedHelloTool.name) == 1


def test_builtin_resolves_under_its_snake_case_name():
    from openhands.sdk.tool import builtins

    resolved = resolve_tool(Tool(name="switch_llm"), _create_mock_conv_state())

    assert [t.name for t in resolved] == ["switch_llm"]
    assert isinstance(resolved[0], builtins.SwitchLLMTool)


def test_catalog_carries_the_class_blurb(monkeypatch):
    register_tool("catalog_described", _DescribedHelloTool)

    assert _catalog()["catalog_described"]["description"] == "Say hello, briefly."


def test_catalog_description_defaults_to_empty():
    register_tool("catalog_undescribed", _SimpleHelloTool)

    assert _catalog()["catalog_undescribed"]["description"] == ""


def test_catalog_reports_a_selectable_builtin_as_unusable(monkeypatch):
    """A built-in is listed by class name, so its usability comes from the class."""
    from openhands.sdk.tool import builtins

    monkeypatch.setitem(
        builtins.BUILT_IN_TOOL_CLASSES, "UnusableBuiltin", _UnavailableHelloTool
    )

    assert _catalog()[_UnavailableHelloTool.name] == {
        "name": _UnavailableHelloTool.name,
        "user_selectable": True,
        "usable": False,
        "description": "",
        "in_default_set": False,
    }


def test_sealed_catalog_ignores_later_registrations(monkeypatch):
    """Registrations after sealing are per-conversation and stay out."""
    register_tool("catalog_at_startup", _SimpleHelloTool)
    seal_tool_catalog()

    register_tool("catalog_after_seal", _SimpleHelloTool)
    register_client_tools(
        [ClientToolSpec(name="catalog_client_tool", description="client side")]
    )

    assert "catalog_at_startup" in _catalog()
    assert "catalog_after_seal" in list_registered_tools()
    assert "catalog_after_seal" not in _catalog()
    assert "catalog_client_tool" not in _catalog()


def test_sealed_catalog_keeps_the_class_registered_at_seal():
    register_tool("catalog_resealed", _DescribedHelloTool)
    seal_tool_catalog()

    register_tool("catalog_resealed", _InternalHelloTool)

    assert _catalog()["catalog_resealed"]["user_selectable"] is True
    assert _catalog()["catalog_resealed"]["description"] == "Say hello, briefly."


@pytest.mark.parametrize("name", ["switch_llm", "finish", "SwitchLLMTool"])
def test_client_tool_cannot_take_a_builtin_name(name):
    from openhands.sdk.tool.client_tool import ClientToolRegistrationError

    with pytest.raises(ClientToolRegistrationError, match="collides"):
        register_client_tools([ClientToolSpec(name=name, description="shadow")])

    resolved = resolve_tool(Tool(name="switch_llm"), _create_mock_conv_state())
    assert type(resolved[0]).__name__ == "SwitchLLMTool"


def test_unsealed_catalog_tracks_registrations_again():
    seal_tool_catalog()
    unseal_tool_catalog()

    register_tool("catalog_after_unseal", _SimpleHelloTool)

    assert "catalog_after_unseal" in _catalog()
