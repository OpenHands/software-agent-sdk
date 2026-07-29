from __future__ import annotations

import asyncio
import json
import threading
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
    _MAX_REDUCE_INPUT_CHARS,
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


def _context(manager: _FakeTaskManager, max_concurrency: int = 4) -> WorkflowContext:
    return WorkflowContext(
        parent_conversation=cast(LocalConversation, object()),
        max_concurrency=max_concurrency,
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
    # map_agents uses asyncio.to_thread; thread scheduling is non-deterministic so the
    # first two prompts may arrive in any order. gather() preserves result ordering but
    # not dispatch order — use set comparison for the map phase.
    assert set(manager.prompts[:2]) == {
        "researcher: inspect alpha",
        "researcher: inspect beta",
    }
    assert manager.prompts[2] == expected_reduce_prompt
    assert set(manager.descriptions[:2]) == {"job alpha", "job beta"}
    assert manager.descriptions[2] == "final summary"


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


def test_workflow_script_can_catch_exception_group_with_plain_except() -> None:
    class _ErrorManager(_FakeTaskManager):
        def start_task(
            self,
            prompt: str,
            subagent_type: str = "default",
            resume: str | None = None,
            description: str | None = None,
            conversation: LocalConversation | None = None,
        ) -> _FakeTask:
            super().start_task(
                prompt=prompt,
                subagent_type=subagent_type,
                resume=resume,
                description=description,
                conversation=conversation,
            )
            return _FakeTask(error=f"failed {prompt}")

    script = """
async def main(wf):
    try:
        await wf.map_agents(items=["one", "two"], prompt="inspect {item}")
    except Exception as exc:
        return str(exc)
"""
    manager = _ErrorManager()

    assert execute_workflow_script(script, _context(manager)) == (
        "map_agents: one or more sub-agents failed (2 sub-exceptions)"
    )
    assert manager.prompts == [
        "general-purpose: inspect one",
        "general-purpose: inspect two",
    ]


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


def test_validate_workflow_script_rejects_wf_close() -> None:
    script = """
async def main(wf):
    wf.close()
"""

    with pytest.raises(WorkflowScriptError, match="wf.close"):
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

    class _PeakTrackingManager(_FakeTaskManager):
        def __init__(self) -> None:
            super().__init__()
            self._active = 0
            self.peak_active = 0
            self._lock = threading.Lock()

        def start_task(
            self,
            prompt: str,
            subagent_type: str = "default",
            resume: str | None = None,
            description: str | None = None,
            conversation: LocalConversation | None = None,
        ) -> _FakeTask:
            with self._lock:
                self._active += 1
                self.peak_active = max(self.peak_active, self._active)
            try:
                return super().start_task(
                    prompt,
                    subagent_type=subagent_type,
                    resume=resume,
                    description=description,
                    conversation=conversation,
                )
            finally:
                with self._lock:
                    self._active -= 1

    # Context capped at 3; per-call max_concurrency=1000 should be min'd to 3
    context_cap = 3
    manager = _PeakTrackingManager()
    context = _context(manager, max_concurrency=context_cap)
    script = """
async def main(wf):
    return await wf.map_agents(
        items=list(range(10)),
        prompt="task {item}",
        max_concurrency=1000,
    )
"""
    execute_workflow_script(script, context)
    assert manager.peak_active <= context_cap


def test_pipeline_returns_results_in_item_order() -> None:
    ctx = _context(_FakeTaskManager())

    async def stage(value: str) -> str:
        return value + "!"

    result = asyncio.run(ctx.pipeline(["a", "b", "c"], stage))
    assert result == ["a!", "b!", "c!"]


def test_pipeline_has_no_barrier_between_stages() -> None:
    """A fast item reaches a later stage while a slow item is still in stage 1."""
    ctx = _context(_FakeTaskManager())
    order: list[str] = []

    async def stage1(item: str) -> str:
        if item == "slow":
            await asyncio.sleep(0.05)
            order.append("slow:s1:end")
        else:
            order.append(f"{item}:s1")
        return item

    async def stage2(item: str) -> str:
        order.append(f"{item}:s2")
        return item

    result = asyncio.run(ctx.pipeline(["slow", "fast"], stage1, stage2))
    assert result == ["slow", "fast"]  # order preserved despite race
    # fast reached stage 2 before slow finished stage 1 -> no barrier
    assert order.index("fast:s2") < order.index("slow:s1:end")


def test_pipeline_stage_failure_drops_item_to_none() -> None:
    ctx = _context(_FakeTaskManager())

    async def stage(item: str) -> str:
        if item == "bad":
            raise RuntimeError("boom")
        return item.upper()

    result = asyncio.run(ctx.pipeline(["ok", "bad", "ok2"], stage))
    assert result == ["OK", None, "OK2"]


def test_pipeline_supports_sync_and_async_stages() -> None:
    ctx = _context(_FakeTaskManager())

    def sync_stage(value: str) -> str:
        return value + "!"

    async def async_stage(value: str) -> str:
        return value.upper()

    result = asyncio.run(ctx.pipeline(["a"], sync_stage, async_stage))
    assert result == ["A!"]


def test_pipeline_requires_at_least_one_stage() -> None:
    ctx = _context(_FakeTaskManager())
    with pytest.raises(ValueError, match="at least one stage"):
        asyncio.run(ctx.pipeline(["a"]))


def test_pipeline_reachable_from_generated_script() -> None:
    manager = _FakeTaskManager()
    ctx = _context(manager)
    script = """
async def main(wf):
    async def review(item):
        return await wf.run_agent(f"review {item}")

    async def verify(prev):
        return await wf.run_agent(f"verify {prev}")

    return await wf.pipeline(["x", "y"], review, verify)
"""
    result = execute_workflow_script(script, ctx)
    assert result == [
        "result:verify result:review x",
        "result:verify result:review y",
    ]


def test_format_value_small_passthrough() -> None:
    value = ["a", "b", "c"]
    assert _format_value(value) == json.dumps(value, indent=2, default=str)


def test_format_value_large_list_drops_whole_elements() -> None:
    """Over-limit lists drop whole trailing elements and stay valid JSON, instead
    of slicing mid-token."""
    value = [f"finding {i}: " + "x" * 500 for i in range(60)]
    out = _format_value(value)
    assert "items omitted to fit" in out
    assert len(out) <= _MAX_REDUCE_INPUT_CHARS + 80
    head = out.split("\n... [")[0]
    parsed = json.loads(head)  # must be valid JSON (the whole point)
    assert parsed == value[: len(parsed)]  # leading elements kept, in order
    assert 0 < len(parsed) < len(value)


def test_format_value_large_dict_drops_whole_keys() -> None:
    value = {f"k{i}": "y" * 500 for i in range(60)}
    out = _format_value(value)
    assert "items omitted to fit" in out
    parsed = json.loads(out.split("\n... [")[0])
    assert isinstance(parsed, dict)
    assert 0 < len(parsed) < len(value)


def test_format_value_single_oversized_element_char_truncated() -> None:
    """A single element bigger than the budget falls back to char truncation."""
    out = _format_value(["z" * (_MAX_REDUCE_INPUT_CHARS + 5000)])
    assert out.endswith("[truncated workflow intermediate results]")
    assert len(out) <= _MAX_REDUCE_INPUT_CHARS + 60


def test_format_value_long_string_char_truncated() -> None:
    out = _format_value("q" * (_MAX_REDUCE_INPUT_CHARS + 100))
    assert out.endswith("[truncated workflow intermediate results]")
    assert len(out) <= _MAX_REDUCE_INPUT_CHARS + 60
