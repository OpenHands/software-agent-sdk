"""Implementation of the dynamic workflow tool."""

from __future__ import annotations

import ast
import asyncio
import inspect
import json as jsonlib
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any, Protocol

from openhands.sdk.logger import get_logger
from openhands.sdk.tool import ToolExecutor
from openhands.tools.task.manager import TaskManager
from openhands.tools.workflow.definition import WorkflowObservation


if TYPE_CHECKING:
    from openhands.sdk.conversation.impl.local_conversation import LocalConversation
    from openhands.tools.workflow.definition import WorkflowAction

logger = get_logger(__name__)

_MAX_SCRIPT_CHARS = 20_000
_UNSAFE_CALLS = frozenset(
    {
        "breakpoint",
        "compile",
        "delattr",
        "dir",
        "eval",
        "exec",
        "getattr",
        "globals",
        "input",
        "locals",
        "open",
        "setattr",
        "vars",
        "__import__",
    }
)
_UNSAFE_ATTRIBUTE_ROOTS = frozenset({"os", "subprocess"})


class WorkflowScriptError(ValueError):
    """Raised when a workflow script is invalid or unsafe."""


class _TaskLike(Protocol):
    result: str | None
    error: str | None


class _TaskStarter(Protocol):
    def start_task(
        self,
        prompt: str,
        subagent_type: str = "default",
        resume: str | None = None,
        description: str | None = None,
        conversation: LocalConversation | None = None,
    ) -> _TaskLike: ...

    def close(self) -> None: ...


class WorkflowContext:
    """Small capability object exposed to generated workflow scripts."""

    def __init__(
        self,
        parent_conversation: LocalConversation,
        max_concurrency: int,
        manager: _TaskStarter | None = None,
    ) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be at least 1")
        self._parent_conversation = parent_conversation
        self._max_concurrency = max_concurrency
        if manager is None:
            task_manager = TaskManager()
            task_manager.attach_parent(parent_conversation)
            self._manager = task_manager
        else:
            self._manager = manager
        self._semaphore: asyncio.Semaphore | None = None

    @property
    def _default_semaphore(self) -> asyncio.Semaphore:
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self._max_concurrency)
        return self._semaphore

    async def run_agent(
        self,
        prompt: str,
        subagent_type: str = "general-purpose",
        description: str | None = None,
    ) -> str:
        """Run a single sub-agent task and return its final result."""
        async with self._default_semaphore:
            return await self._run_agent_task(
                prompt=prompt,
                subagent_type=subagent_type,
                description=description,
            )

    async def _run_agent_task(
        self,
        prompt: str,
        subagent_type: str,
        description: str | None,
    ) -> str:
        task = await asyncio.to_thread(
            self._manager.start_task,
            prompt=prompt,
            subagent_type=subagent_type,
            description=description,
            conversation=self._parent_conversation,
        )
        if task.error:
            raise RuntimeError(task.error)
        return task.result or ""

    async def map_agents(
        self,
        items: Sequence[Any],
        prompt: Callable[[Any], str] | str,
        subagent_type: str = "general-purpose",
        max_concurrency: int | None = None,
        description: Callable[[Any], str] | str | None = None,
    ) -> list[str]:
        """Run one sub-agent task per item and return results in item order.

        A per-call ``max_concurrency`` overrides the context default for this map
        operation only; it is not a nested subset of the context-wide limit.
        """
        if max_concurrency is not None and max_concurrency < 1:
            raise ValueError("max_concurrency must be at least 1")
        semaphore = (
            asyncio.Semaphore(max_concurrency)
            if max_concurrency is not None
            else self._default_semaphore
        )

        async def run_one(item: Any) -> str:
            rendered_prompt = _render_required_template(prompt, item)
            rendered_description = _render_template(description, item)
            async with semaphore:
                return await self._run_agent_task(
                    prompt=rendered_prompt,
                    subagent_type=subagent_type,
                    description=rendered_description,
                )

        results = await asyncio.gather(
            *(run_one(item) for item in items),
            return_exceptions=True,
        )
        failures = [result for result in results if isinstance(result, BaseException)]
        if failures:
            exceptions = [
                failure
                if isinstance(failure, Exception)
                else RuntimeError(str(failure))
                for failure in failures
            ]
            raise ExceptionGroup(
                "map_agents: one or more sub-agents failed",
                exceptions,
            )
        return [str(result) for result in results]

    async def reduce_agent(
        self,
        items: Any,
        prompt: str,
        subagent_type: str = "general-purpose",
        description: str | None = None,
    ) -> str:
        """Run a single reducer sub-agent with serialized intermediate results."""
        return await self.run_agent(
            prompt=f"{prompt}\n\nInput:\n{_format_value(items)}",
            subagent_type=subagent_type,
            description=description,
        )

    def flatten(self, values: Sequence[Any]) -> list[Any]:
        """Flatten one list level."""
        flattened: list[Any] = []
        for value in values:
            if isinstance(value, list):
                flattened.extend(value)
            else:
                flattened.append(value)
        return flattened

    def close(self) -> None:
        self._manager.close()


def _render_required_template(template: Callable[[Any], str] | str, item: Any) -> str:
    if callable(template):
        return str(template(item))
    return template.format(item=item)


def _render_template(
    template: Callable[[Any], str] | str | None, item: Any
) -> str | None:
    if template is None:
        return None
    return _render_required_template(template, item)


def _format_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    return jsonlib.dumps(value, indent=2, default=str)


def validate_workflow_script(script: str) -> None:
    """Perform best-effort validation for generated workflow scripts."""
    if len(script) > _MAX_SCRIPT_CHARS:
        raise WorkflowScriptError(
            f"Workflow script is too large: {len(script)} > {_MAX_SCRIPT_CHARS}"
        )

    try:
        tree = ast.parse(script)
    except SyntaxError as e:
        raise WorkflowScriptError(f"Workflow script has invalid syntax: {e}") from e

    main_defs = [
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "main"
    ]
    if len(main_defs) != 1:
        raise WorkflowScriptError(
            "Workflow script must define exactly one async main(wf)"
        )

    main_def = main_defs[0]
    if len(main_def.args.args) != 1 or main_def.args.args[0].arg != "wf":
        raise WorkflowScriptError("Workflow entry point must be `async def main(wf):`")

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            raise WorkflowScriptError("Workflow scripts may not import modules")
        if isinstance(node, ast.Name) and node.id.startswith("__"):
            raise WorkflowScriptError("Workflow scripts may not access dunder names")
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            raise WorkflowScriptError(
                "Workflow scripts may not access dunder attributes"
            )
        if (
            isinstance(node, ast.Attribute)
            and _attribute_root_name(node) in _UNSAFE_ATTRIBUTE_ROOTS
        ):
            raise WorkflowScriptError("Workflow scripts may not access unsafe modules")
        if isinstance(node, ast.Call):
            call_name = _call_name(node.func)
            if call_name in _UNSAFE_CALLS:
                raise WorkflowScriptError(
                    f"Workflow scripts may not call `{call_name}`"
                )


def _call_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _attribute_root_name(node: ast.Attribute) -> str | None:
    value = node.value
    while isinstance(value, ast.Attribute):
        value = value.value
    if isinstance(value, ast.Name):
        return value.id
    return None


def execute_workflow_script(script: str, context: WorkflowContext) -> Any:
    """Validate and execute a workflow script from a synchronous context."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise WorkflowScriptError(
            "Workflow scripts must be executed from a synchronous context"
        )

    validate_workflow_script(script)
    namespace: dict[str, Any] = {}
    exec(compile(script, "<dynamic-workflow>", "exec"), _safe_globals(), namespace)
    main = namespace.get("main")
    if not inspect.iscoroutinefunction(main):
        raise WorkflowScriptError("Workflow entry point must be async")
    return asyncio.run(main(context))


def _format_exception(error: Exception) -> str:
    if isinstance(error, ExceptionGroup):
        details = "\n".join(
            f"  [{index}] {exception}"
            for index, exception in enumerate(error.exceptions, start=1)
        )
        return f"{error.args[0]}:\n{details}"
    return str(error)


def _safe_globals() -> dict[str, Any]:
    safe_builtins = {
        "abs": abs,
        "all": all,
        "any": any,
        "bool": bool,
        "dict": dict,
        "enumerate": enumerate,
        "float": float,
        "int": int,
        "len": len,
        "list": list,
        "max": max,
        "min": min,
        "range": range,
        "round": round,
        "set": set,
        "sorted": sorted,
        "str": str,
        "sum": sum,
        "tuple": tuple,
        "zip": zip,
    }
    return {"__builtins__": safe_builtins}


class WorkflowExecutor(ToolExecutor["WorkflowAction", WorkflowObservation]):
    """Executor for the dynamic workflow tool."""

    def __call__(
        self,
        action: WorkflowAction,
        conversation: LocalConversation | None = None,
    ) -> WorkflowObservation:
        if conversation is None:
            return WorkflowObservation.from_text(
                text="Workflow tool requires a local conversation context.",
                name=action.name,
                status="error",
                is_error=True,
            )

        context = WorkflowContext(
            parent_conversation=conversation,
            max_concurrency=action.max_concurrency,
        )
        try:
            result = execute_workflow_script(action.script, context)
            return WorkflowObservation.from_text(
                text=str(result),
                name=action.name,
                status="completed",
            )
        except Exception as e:
            error_text = _format_exception(e)
            logger.warning("Workflow '%s' failed: %s", action.name, e, exc_info=True)
            return WorkflowObservation.from_text(
                text=error_text,
                name=action.name,
                status="error",
                is_error=True,
            )
        finally:
            context.close()
