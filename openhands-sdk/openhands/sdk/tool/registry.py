import inspect
from collections.abc import Callable, Sequence
from threading import RLock
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from openhands.sdk.logger import get_logger
from openhands.sdk.tool.spec import Tool
from openhands.sdk.tool.tool import ToolDefinition


if TYPE_CHECKING:
    from openhands.sdk.conversation.state import ConversationState

logger = get_logger(__name__)

# A resolver produces ToolDefinition instances for given params.
Resolver = Callable[[dict[str, Any], "ConversationState"], Sequence[ToolDefinition]]
UsabilityChecker = Callable[[], bool]
"""A resolver produces ToolDefinition instances for given params.

Args:
    params: Arbitrary parameters passed to the resolver. These are typically
        used to configure the ToolDefinition instances that are created.
    conversation: Optional conversation state to get directories from.
Returns: A sequence of ToolDefinition instances. Most of the time this will be a
    single-item
    sequence, but in some cases a ToolDefinition.create may produce multiple tools
    (e.g., BrowserToolSet).
"""

_LOCK = RLock()
_REG: dict[str, Resolver] = {}
_USABILITY_REG: dict[str, UsabilityChecker] = {}
_MODULE_QUALNAMES: dict[str, str] = {}  # Maps tool name to module qualname
_TOOL_CLASSES: dict[str, type[ToolDefinition]] = {}
_CATALOG_NAMES: set[str] | None = None


class ToolCatalogEntry(BaseModel):
    """A registered tool as offered to clients configuring an agent."""

    name: str
    user_selectable: bool = True
    usable: bool = True
    description: str = ""


def _resolver_from_instance(name: str, tool: ToolDefinition) -> Resolver:
    if tool.executor is None:
        raise ValueError(
            "Unable to register tool: "
            f"ToolDefinition instance '{name}' must have a non-None .executor"
        )

    def _resolve(
        params: dict[str, Any], _conv_state: "ConversationState"
    ) -> Sequence[ToolDefinition]:
        if params:
            raise ValueError(
                f"ToolDefinition '{name}' is a fixed instance; params not supported"
            )
        return [tool]

    return _resolve


def _is_abstract_method(cls: type, name: str) -> bool:
    try:
        attr = inspect.getattr_static(cls, name)
    except AttributeError:
        return False
    # Unwrap classmethod/staticmethod
    if isinstance(attr, (classmethod, staticmethod)):
        attr = attr.__func__
    return getattr(attr, "__isabstractmethod__", False)


def _resolver_from_subclass(_name: str, cls: type[ToolDefinition]) -> Resolver:
    create = getattr(cls, "create", None)

    if create is None or not callable(create) or _is_abstract_method(cls, "create"):
        raise TypeError(
            "Unable to register tool: "
            f"ToolDefinition subclass '{cls.__name__}' must define .create(**params)"
            f" as a concrete classmethod"
        )

    def _resolve(
        params: dict[str, Any], conv_state: "ConversationState"
    ) -> Sequence[ToolDefinition]:
        created = create(conv_state=conv_state, **params)
        if not isinstance(created, Sequence) or not all(
            isinstance(t, ToolDefinition) for t in created
        ):
            raise TypeError(
                f"ToolDefinition subclass '{cls.__name__}' create() must return "
                f"Sequence[ToolDefinition], "
                f"got {type(created)}"
            )
        # Optional sanity: permit tools without executor; they'll fail at .call()
        return created

    return _resolve


def _usability_from_instance(tool: ToolDefinition) -> UsabilityChecker:
    return lambda: tool.__class__.is_usable()


def _usability_from_subclass(cls: type[ToolDefinition]) -> UsabilityChecker:
    return lambda: cls.is_usable()


def _check_tool_usable(name: str, checker: UsabilityChecker) -> bool:
    try:
        return checker()
    except Exception:
        logger.warning(
            "Failed to determine usability for tool '%s'", name, exc_info=True
        )
        return False


def register_tool(
    name: str,
    factory: ToolDefinition | type[ToolDefinition],
) -> None:
    if not isinstance(name, str) or not name.strip():
        raise ValueError("ToolDefinition name must be a non-empty string")

    if isinstance(factory, ToolDefinition):
        resolver = _resolver_from_instance(name, factory)
        usability_checker = _usability_from_instance(factory)
    elif isinstance(factory, type) and issubclass(factory, ToolDefinition):
        resolver = _resolver_from_subclass(name, factory)
        usability_checker = _usability_from_subclass(factory)
    else:
        raise TypeError(
            "register_tool(...) only accepts: (1) a ToolDefinition instance with "
            ".executor, or (2) a ToolDefinition subclass with .create(**params)"
        )

    tool_class = factory if isinstance(factory, type) else factory.__class__

    with _LOCK:
        # TODO: throw exception when registering duplicate name tools
        if name in _REG:
            logger.warning(f"Duplicate tool name registered: {name}")
        _REG[name] = resolver
        _USABILITY_REG[name] = usability_checker
        _TOOL_CLASSES[name] = tool_class
        _MODULE_QUALNAMES[name] = tool_class.__module__


def resolve_tool(
    tool_spec: Tool, conv_state: "ConversationState"
) -> Sequence[ToolDefinition]:
    with _LOCK:
        resolver = _REG.get(tool_spec.name)

    if resolver is None:
        from openhands.sdk.tool.builtins import (
            BUILT_IN_TOOL_CLASSES,
            BUILT_IN_TOOL_CLASSES_BY_TOOL_NAME,
        )

        tool_class = BUILT_IN_TOOL_CLASSES.get(
            tool_spec.name
        ) or BUILT_IN_TOOL_CLASSES_BY_TOOL_NAME.get(tool_spec.name)
        if tool_class is None:
            raise KeyError(f"ToolDefinition '{tool_spec.name}' is not registered")
        resolver = _resolver_from_subclass(tool_spec.name, tool_class)

    params = dict(tool_spec.params)
    response_schema = params.pop("response_schema", None)
    tools = resolver(params, conv_state)
    if response_schema is not None:
        if len(tools) != 1:
            raise ValueError(
                "response_schema requires a spec that resolves to exactly one tool"
            )
        tools = [tools[0].set_response_schema(response_schema)]
    return tools


def list_registered_tools() -> list[str]:
    with _LOCK:
        return list(_REG.keys())


def is_tool_usable(name: str) -> bool:
    """Whether ``name`` is registered AND its usability check passes.

    False for unregistered names; a checker that raises counts as unusable
    (mirrors :func:`list_usable_tools`).
    """
    with _LOCK:
        if name not in _REG:
            return False
        checker = _USABILITY_REG.get(name, lambda: True)
    return _check_tool_usable(name, checker)


def list_usable_tools() -> list[str]:
    with _LOCK:
        tool_names = list(_REG.keys())
        usability_checkers = dict(_USABILITY_REG)

    return [
        name
        for name in tool_names
        if _check_tool_usable(name, usability_checkers.get(name, lambda: True))
    ]


def seal_tool_catalog() -> None:
    """Freeze the catalog to the tools registered so far.

    A server calls this once it has finished loading its tools. Registrations
    after it — a conversation's client tools or dynamically imported modules —
    vanish on restart, so they are never offered for configuring an agent.
    """
    global _CATALOG_NAMES
    with _LOCK:
        _CATALOG_NAMES = set(_REG)


def list_tool_catalog() -> list[ToolCatalogEntry]:
    """List the tools this process offers for configuring an agent.

    Includes the built-ins a user may select: they are resolved by class name
    rather than through the registry, but a profile stores them in ``tools``
    like any other pick.
    """
    from openhands.sdk.tool.builtins import BUILT_IN_TOOL_CLASSES

    with _LOCK:
        names = [
            name for name in _REG if _CATALOG_NAMES is None or name in _CATALOG_NAMES
        ]
        tool_classes = dict(_TOOL_CLASSES)
        usability_checkers = dict(_USABILITY_REG)

    entries = [
        ToolCatalogEntry(
            name=name,
            user_selectable=tool_classes[name].user_selectable,
            usable=_check_tool_usable(name, usability_checkers.get(name, lambda: True)),
            description=tool_classes[name].catalog_description,
        )
        for name in names
    ]
    # Built-ins are keyed by class name, but a profile stores the same snake_case
    # tool name as every other pick.
    listed = {entry.name for entry in entries}
    entries.extend(
        ToolCatalogEntry(
            name=tool_class.name,
            user_selectable=tool_class.user_selectable,
            usable=_check_tool_usable(
                tool_class.name, _usability_from_subclass(tool_class)
            ),
            description=tool_class.catalog_description,
        )
        for tool_class in BUILT_IN_TOOL_CLASSES.values()
        if tool_class.user_selectable and tool_class.name not in listed
    )
    return entries


def get_tool_module_qualnames() -> dict[str, str]:
    """Get a mapping of tool names to their module qualnames.

    Returns:
        A dictionary mapping tool names to module qualnames (e.g.,
        {"glob": "openhands.tools.glob.definition"}).
    """
    with _LOCK:
        return dict(_MODULE_QUALNAMES)
