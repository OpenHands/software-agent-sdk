"""Test Tool class functionality."""

import gc
import threading
from abc import ABC

import pytest
from pydantic import Field, ValidationError

from openhands.sdk.tool import Action
from openhands.sdk.tool.spec import Tool
from openhands.sdk.tool.tool import (
    _action_types_with_risk,
    _action_types_with_summary,
    _create_action_type_with_summary,
    create_action_type_with_risk,
)
from openhands.sdk.utils.models import _get_checked_concrete_subclasses


# Must live at module scope (Pydantic rejects <locals> classes).
class _Bug2199Action(Action, ABC):
    cmd: str = Field(description="test")


class _Bug2642ActionA(Action, ABC):
    command: str = Field(description="shell command")


class _Bug2642ActionB(Action, ABC):
    path: str = Field(description="file path")


class _Bug2642ActionC(Action, ABC):
    tab_id: int = Field(description="tab id")


def test_tool_json_round_trip():
    tool = Tool(name="TestTool", params={"working_dir": "/test", "timeout": 45})

    assert Tool.model_validate_json(tool.model_dump_json()) == tool


def test_tool_immutability():
    """Test that Tool behaves correctly with parameter modifications."""
    original_params = {"test_param": "/workspace"}
    tool = Tool(name="TerminalTool", params=original_params)

    # Modifying the original params should not affect the tool
    original_params["test_param"] = "/changed"
    assert tool.params["test_param"] == "/workspace"


def test_tool_rejects_empty_name():
    with pytest.raises(ValidationError):
        Tool(name="")


def test_issue_2199_1(request):
    """Reproduce issue #2199: duplicate dynamic Action wrapper classes.

    When subagent threads concurrently call ``create_action_type_with_risk``
    or ``_create_action_type_with_summary`` on the same input, a TOCTOU race
    on the module-level cache can create two distinct class objects with the
    same ``__name__``, causing ``_get_checked_concrete_subclasses(Action)``
    to raise ``ValueError("Duplicate class definition ...")``.

    Ref: https://github.com/issues/assigned?issue=OpenHands%7Csoftware-agent-sdk%7C2199
    """
    """Many threads wrapping the same type must all get the same class object."""
    saved_risk = dict(_action_types_with_risk)

    def _cleanup():
        _action_types_with_risk.clear()
        _action_types_with_risk.update(saved_risk)
        gc.collect()

    request.addfinalizer(_cleanup)

    results: list[type] = []
    barrier = threading.Barrier(8)

    def worker():
        barrier.wait()
        results.append(create_action_type_with_risk(_Bug2199Action))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(set(id(r) for r in results)) == 1, "All threads must get the same class"
    _get_checked_concrete_subclasses(Action)


def test_issue_2199_2(request):
    """
    Same race test for _create_action_type_with_summary.
    """
    saved_risk = dict(_action_types_with_risk)
    saved_summary = dict(_action_types_with_summary)

    def _cleanup():
        _action_types_with_risk.clear()
        _action_types_with_risk.update(saved_risk)
        _action_types_with_summary.clear()
        _action_types_with_summary.update(saved_summary)
        gc.collect()

    request.addfinalizer(_cleanup)

    with_risk = create_action_type_with_risk(_Bug2199Action)
    results: list[type] = []
    barrier = threading.Barrier(8)

    def worker():
        barrier.wait()
        results.append(_create_action_type_with_summary(with_risk))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(set(id(r) for r in results)) == 1, "All threads must get the same class"
    _get_checked_concrete_subclasses(Action)


def test_issue_2642(request):
    """Duplicate Action class definition error when spawning sub-agents.

    When a sub-agent conversation re-initialises tools in the same process,
    ``create_action_type_with_risk`` may produce a *second* class object with
    the same ``__name__`` if the old WithRisk classes are still alive in
    ``Action.__subclasses__()`` but the module-level cache has lost track of
    them.  ``_get_checked_concrete_subclasses(Action)`` then raises
    ``ValueError("Duplicate class definition ...")``.

    Ref: https://github.com/OpenHands/software-agent-sdk/issues/2642
    """
    bug_actions: list[type[Action]] = [
        _Bug2642ActionA,
        _Bug2642ActionB,
        _Bug2642ActionC,
    ]

    saved_risk = dict(_action_types_with_risk)
    saved_summary = dict(_action_types_with_summary)

    def _cleanup():
        _action_types_with_risk.clear()
        _action_types_with_risk.update(saved_risk)
        _action_types_with_summary.clear()
        _action_types_with_summary.update(saved_summary)
        gc.collect()

    request.addfinalizer(_cleanup)

    # Step 1 — Simulate the parent conversation creating WithRisk wrappers.
    # In production this happens when the agent calls
    # _get_tool_schema(add_security_risk_prediction=True) for each tool.
    first_gen: list[type] = []
    for action_type in bug_actions:
        with_risk = create_action_type_with_risk(action_type)
        _create_action_type_with_summary(with_risk)
        first_gen.append(with_risk)

    # Sanity: no duplicates yet.
    _get_checked_concrete_subclasses(Action)

    # Step 2 — Simulate the cache losing track of the old classes.
    # In production this happens when the delegate tool spawns a sub-agent
    # whose action_type is a different object (e.g. from a re-import or
    # dynamic tool recreation), causing a cache-key mismatch.
    _action_types_with_risk.clear()
    _action_types_with_summary.clear()

    # Step 3 — Simulate the sub-agent conversation re-initialising its tools.
    # Cache miss → type() is called again → second class with same __name__.
    for action_type in bug_actions:
        create_action_type_with_risk(action_type)

    # Step 4 — This is the call that blows up in the bug report
    # (triggered by Action.resolve_kind() during Event/ToolDefinition
    # deserialization in the sub-agent).
    _get_checked_concrete_subclasses(Action)
