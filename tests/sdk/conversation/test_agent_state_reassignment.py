"""Test that all writes to agent_state use the reassignment pattern.

The agent_state field in ConversationState requires reassignment to trigger autosave.
In-place mutations like `state.agent_state[key] = value` will NOT trigger autosave.
The correct pattern is: `state.agent_state = {**state.agent_state, key: value}`

This test scans the SDK codebase to ensure all writes to agent_state follow
this pattern.
"""

import ast
from pathlib import Path

import pytest


class AgentStateWriteVisitor(ast.NodeVisitor):
    """AST visitor that detects in-place mutations to agent_state."""

    def __init__(self, filepath: str):
        self.filepath = filepath
        self.violations: list[tuple[int, str]] = []

    def visit_Subscript(self, node: ast.Subscript) -> None:
        """Detect agent_state[key] = value patterns."""
        # Check if this is an assignment target (left side of =)
        # We need to check the parent context, which is tricky with AST
        # Instead, we'll check in visit_Assign
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        """Detect assignments to agent_state subscripts."""
        for target in node.targets:
            if isinstance(target, ast.Subscript):
                # Check if it's agent_state[...]
                if self._is_agent_state_subscript(target):
                    self.violations.append(
                        (
                            node.lineno,
                            "In-place mutation: agent_state[...] = ... "
                            "(use reassignment pattern instead)",
                        )
                    )
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        """Detect augmented assignments like agent_state[key] += value."""
        if isinstance(node.target, ast.Subscript):
            if self._is_agent_state_subscript(node.target):
                self.violations.append(
                    (
                        node.lineno,
                        f"In-place mutation: agent_state[...] {ast.dump(node.op)}= ... "
                        f"(use reassignment pattern instead)",
                    )
                )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        """Detect method calls that mutate agent_state in-place."""
        if isinstance(node.func, ast.Attribute):
            # Check for agent_state.update(), agent_state.setdefault(), etc.
            mutating_methods = {
                "update",
                "setdefault",
                "pop",
                "popitem",
                "clear",
                "__setitem__",
                "__delitem__",
            }
            if node.func.attr in mutating_methods:
                if self._is_agent_state_attr(node.func.value):
                    self.violations.append(
                        (
                            node.lineno,
                            f"In-place mutation: agent_state.{node.func.attr}() "
                            f"(use reassignment pattern instead)",
                        )
                    )
        self.generic_visit(node)

    def visit_Delete(self, node: ast.Delete) -> None:
        """Detect del agent_state[key] patterns."""
        for target in node.targets:
            if isinstance(target, ast.Subscript):
                if self._is_agent_state_subscript(target):
                    self.violations.append(
                        (
                            node.lineno,
                            "In-place mutation: del agent_state[...] "
                            "(use reassignment pattern instead)",
                        )
                    )
        self.generic_visit(node)

    def _is_agent_state_subscript(self, node: ast.Subscript) -> bool:
        """Check if a subscript is accessing agent_state."""
        return self._is_agent_state_attr(node.value)

    def _is_agent_state_attr(self, node: ast.AST) -> bool:
        """Check if a node refers to agent_state."""
        # Direct name: agent_state[...]
        if isinstance(node, ast.Name) and node.id == "agent_state":
            return True
        # Attribute access: state.agent_state[...] or self.state.agent_state[...]
        if isinstance(node, ast.Attribute) and node.attr == "agent_state":
            return True
        return False


def get_sdk_python_files() -> list[Path]:
    """Get all Python files in the SDK source directory."""
    sdk_dir = Path(__file__).parent.parent.parent.parent / "openhands-sdk"
    if not sdk_dir.exists():
        pytest.skip(f"SDK directory not found: {sdk_dir}")

    python_files = []
    for py_file in sdk_dir.rglob("*.py"):
        # Skip __pycache__ and test files
        if "__pycache__" in str(py_file):
            continue
        python_files.append(py_file)

    return python_files


def test_agent_state_writes_use_reassignment_pattern():
    """Verify all writes to agent_state use the reassignment pattern.

    The agent_state field requires reassignment to trigger autosave:
    - WRONG: state.agent_state[key] = value  (no autosave)
    - WRONG: state.agent_state.update({key: value})  (no autosave)
    - RIGHT: state.agent_state = {**state.agent_state, key: value}  (triggers autosave)

    This test scans all SDK Python files and fails if any in-place mutations
    to agent_state are found.
    """
    python_files = get_sdk_python_files()
    all_violations: list[tuple[Path, int, str]] = []

    for py_file in python_files:
        try:
            source = py_file.read_text()
            tree = ast.parse(source, filename=str(py_file))
        except SyntaxError:
            continue

        visitor = AgentStateWriteVisitor(str(py_file))
        visitor.visit(tree)

        for lineno, message in visitor.violations:
            all_violations.append((py_file, lineno, message))

    if all_violations:
        error_msg = "Found in-place mutations to agent_state:\n"
        for filepath, lineno, message in all_violations:
            error_msg += f"  {filepath}:{lineno}: {message}\n"
        error_msg += (
            "\nTo trigger autosave, use the reassignment pattern:\n"
            "  state.agent_state = {**state.agent_state, key: value}"
        )
        pytest.fail(error_msg)


def test_agent_state_reassignment_triggers_autosave():
    """Verify that reassigning agent_state triggers autosave.

    This is a runtime test that verifies the autosave mechanism works
    correctly when agent_state is reassigned.
    """
    import uuid

    from pydantic import SecretStr

    from openhands.sdk import Agent
    from openhands.sdk.conversation.state import ConversationState
    from openhands.sdk.io import InMemoryFileStore
    from openhands.sdk.llm import LLM
    from openhands.sdk.workspace import LocalWorkspace

    # Create a state with autosave enabled
    llm = LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm")
    agent = Agent(llm=llm)
    workspace = LocalWorkspace(working_dir="/tmp/test")

    state = ConversationState(
        id=uuid.uuid4(),
        workspace=workspace,
        persistence_dir="/tmp/test/.state",
        agent=agent,
    )

    # Set up filestore and enable autosave
    fs = InMemoryFileStore()
    state._fs = fs
    state._autosave_enabled = True

    # Track saves
    save_count = 0
    original_save = state._save_base_state

    def counting_save(fs):
        nonlocal save_count
        save_count += 1
        original_save(fs)

    state._save_base_state = counting_save

    # Reassign agent_state - should trigger autosave
    with state:
        state.agent_state = {**state.agent_state, "test_key": "test_value"}

    assert save_count == 1, "Reassigning agent_state should trigger autosave"
    assert state.agent_state.get("test_key") == "test_value"


def test_agent_state_inplace_mutation_does_not_trigger_autosave():
    """Verify that in-place mutation of agent_state does NOT trigger autosave.

    This test demonstrates why the reassignment pattern is required.
    """
    import uuid

    from pydantic import SecretStr

    from openhands.sdk import Agent
    from openhands.sdk.conversation.state import ConversationState
    from openhands.sdk.io import InMemoryFileStore
    from openhands.sdk.llm import LLM
    from openhands.sdk.workspace import LocalWorkspace

    # Create a state with autosave enabled
    llm = LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm")
    agent = Agent(llm=llm)
    workspace = LocalWorkspace(working_dir="/tmp/test")

    state = ConversationState(
        id=uuid.uuid4(),
        workspace=workspace,
        persistence_dir="/tmp/test/.state",
        agent=agent,
    )

    # Set up filestore and enable autosave
    fs = InMemoryFileStore()
    state._fs = fs
    state._autosave_enabled = True

    # Track saves
    save_count = 0
    original_save = state._save_base_state

    def counting_save(fs):
        nonlocal save_count
        save_count += 1
        original_save(fs)

    state._save_base_state = counting_save

    # In-place mutation - should NOT trigger autosave (this is the problem!)
    with state:
        state.agent_state["test_key"] = "test_value"

    # This demonstrates the problem: in-place mutation doesn't trigger autosave
    assert save_count == 0, "In-place mutation should NOT trigger autosave"
    # But the value is still set in memory
    assert state.agent_state.get("test_key") == "test_value"
