from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import cast

import pytest

from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.tools.workflow import (
    WorkflowAction,
    WorkflowContext,
    WorkflowExecutor,
    WorkflowScriptError,
)
from openhands.tools.workflow.impl import (
    _format_exception,
    _format_value,
    execute_workflow_script,
    validate_workflow_script,
)


@dataclass
class _FakeTask:
    result: str | None = None
    error: str | None = None


class _FakeTaskManager:
    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.descriptions: list[str | None] = []
        self.closed = False

    def start_task(
        self,
        prompt: str,
        subagent_type: str = "default",
        resume: str | None = None,
        description: str | None = None,
        conversation: LocalConversation | None = None,
    ) -> _FakeTask:
        self.prompts.append(f"{subagent_type}: {prompt}")
        self.descriptions.append(description)
        return _FakeTask(result=f"result:{prompt}")

    def close(self) -> None:
        self.closed = True


def _context(manager: _FakeTaskManager) -> WorkflowContext:
    return WorkflowContext(
        parent_conversation=cast(LocalConversation, object()),
        max_concurrency=4,
        manager=manager,
    )


def test_execute_workflow_script_runs_map_and_reduce() -> None:
    manager = _FakeTaskManager()
    script = """
async def main(wf):
    results = await wf.map_agents(
        items=["alpha", "beta"],
        subagent_type="researcher",
        max_concurrency=2,
        prompt=lambda item: f"inspect {item}",
        description=lambda item: f"job {item}",
    )
    return await wf.reduce_agent(
        items=results,
        subagent_type="writer",
        prompt="summarize the results",
        description="final summary",
    )
"""

    result = execute_workflow_script(script, _context(manager))

    expected_reduce_prompt = (
        'writer: summarize the results\n\nInput:\n[\n  "result:inspect alpha",\n'
        '  "result:inspect beta"\n]'
    )
    assert result.startswith("result:summarize the results")
    assert manager.prompts == [
        "researcher: inspect alpha",
        "researcher: inspect beta",
        expected_reduce_prompt,
    ]
    assert manager.descriptions == ["job alpha", "job beta", "final summary"]


def test_run_agent_returns_task_result() -> None:
    manager = _FakeTaskManager()
    script = """
async def main(wf):
    return await wf.run_agent("do the thing", subagent_type="analyst")
"""
    result = execute_workflow_script(script, _context(manager))
    assert result == "result:do the thing"
    assert manager.prompts == ["analyst: do the thing"]


def test_map_agents_uses_context_default_concurrency_when_none_given() -> None:
    manager = _FakeTaskManager()
    script = """
async def main(wf):
    return await wf.map_agents(
        items=["one", "two"],
        prompt="inspect {item}",
        subagent_type="researcher",
    )
"""

    assert execute_workflow_script(script, _context(manager)) == [
        "result:inspect one",
        "result:inspect two",
    ]


def test_map_agents_reports_all_sub_agent_failures() -> None:
    class FailingTaskManager(_FakeTaskManager):
        def start_task(
            self,
            prompt: str,
            subagent_type: str = "default",
            resume: str | None = None,
            description: str | None = None,
            conversation: LocalConversation | None = None,
        ) -> _FakeTask:
            self.prompts.append(f"{subagent_type}: {prompt}")
            if prompt in {"inspect bad", "inspect worse"}:
                return _FakeTask(error=f"failed {prompt}")
            return _FakeTask(result=f"result:{prompt}")

    script = """
async def main(wf):
    return await wf.map_agents(
        items=["good", "bad", "worse"],
        prompt="inspect {item}",
        subagent_type="researcher",
    )
"""
    manager = FailingTaskManager()

    with pytest.raises(ExceptionGroup) as exc_info:
        execute_workflow_script(script, _context(manager))

    assert "map_agents" in str(exc_info.value)
    assert [str(exc) for exc in exc_info.value.exceptions] == [
        "[item 2] failed inspect bad",
        "[item 3] failed inspect worse",
    ]
    assert set(manager.prompts) == {
        "researcher: inspect good",
        "researcher: inspect bad",
        "researcher: inspect worse",
    }


def test_workflow_script_can_catch_common_exceptions() -> None:
    script = """
async def main(wf):
    try:
        raise ValueError("recoverable")
    except ValueError as exc:
        return str(exc)
"""

    assert (
        execute_workflow_script(script, _context(_FakeTaskManager())) == "recoverable"
    )


def test_format_value_truncates_large_intermediate_results() -> None:
    value = _format_value("x" * 12_050)

    assert len(value) < 12_100
    assert value.endswith("[truncated workflow intermediate results]")


def test_format_exception_includes_exception_group_details() -> None:
    error = ExceptionGroup(
        "map_agents: one or more sub-agents failed",
        [RuntimeError("first failure"), RuntimeError("second failure")],
    )

    assert _format_exception(error) == (
        "map_agents: one or more sub-agents failed:\n"
        "  [1] first failure\n"
        "  [2] second failure"
    )


def test_validate_workflow_script_rejects_missing_async_main() -> None:
    with pytest.raises(WorkflowScriptError, match="async main"):
        validate_workflow_script("def main(wf):\n    return 'nope'\n")


def test_validate_workflow_script_rejects_unsafe_calls() -> None:
    script = """
async def main(wf):
    return open('secrets.txt').read()
"""

    with pytest.raises(WorkflowScriptError, match="open"):
        validate_workflow_script(script)


def test_validate_workflow_script_rejects_private_wf_access() -> None:
    script = """
async def main(wf):
    return wf._parent_conversation
"""

    with pytest.raises(WorkflowScriptError, match="private wf attributes"):
        validate_workflow_script(script)


def test_validate_workflow_script_rejects_unsafe_module_access() -> None:
    script = """
async def main(wf):
    os.system('echo nope')
"""

    with pytest.raises(WorkflowScriptError, match="unsafe modules"):
        validate_workflow_script(script)


def test_validate_workflow_script_rejects_imports() -> None:
    script = """
import os

async def main(wf):
    return 'nope'
"""

    with pytest.raises(WorkflowScriptError, match="import"):
        validate_workflow_script(script)


def test_workflow_executor_returns_error_observation_without_conversation() -> None:
    observation = WorkflowExecutor()(WorkflowAction(name="demo", script=""))

    assert observation.is_error
    assert observation.status == "error"
    assert "requires a local conversation" in observation.text


def test_workflow_context_helper_flattens_one_level() -> None:
    context = _context(_FakeTaskManager())

    assert context.flatten([[1, 2], 3, [4]]) == [1, 2, 3, 4]


def test_workflow_executor_success_path() -> None:
    @dataclass
    class _FakeState:
        persistence_dir: str | None = None

    @dataclass
    class _FakeConv:
        state: _FakeState

    conv = cast(LocalConversation, _FakeConv(state=_FakeState()))
    action = WorkflowAction(
        name="trivial",
        script="async def main(wf):\n    return 'done'",
    )

    obs = WorkflowExecutor()(action, conversation=conv)

    assert not obs.is_error
    assert obs.status == "completed"
    assert obs.text == "done"


def test_workflow_context_close_propagates_to_manager() -> None:
    manager = _FakeTaskManager()
    context = _context(manager)

    assert not manager.closed
    context.close()
    assert manager.closed


def test_workflow_context_close_is_idempotent() -> None:
    manager = _FakeTaskManager()
    context = _context(manager)

    context.close()
    context.close()  # second call must not raise
    assert manager.closed


def test_run_agent_raises_after_close() -> None:
    manager = _FakeTaskManager()
    context = _context(manager)
    context.close()

    with pytest.raises(WorkflowScriptError, match="already closed"):
        asyncio.run(context.run_agent("any prompt"))


def test_map_agents_respects_context_concurrency_cap() -> None:
    """Per-call max_concurrency must be silently capped at context max."""
    script = """
async def main(wf):
    results = await wf.map_agents(
        items=["a", "b"],
        prompt="ping {item}",
        subagent_type="researcher",
        max_concurrency=1000,
    )
    return results
"""
    manager = _FakeTaskManager()
    context = _context(manager)
    execute_workflow_script(script, context)
