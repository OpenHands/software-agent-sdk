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
_MAX_REDUCE_INPUT_CHARS = 12_000
_WORKFLOW_TIMEOUT_SECONDS = 3600.0  # 1 hour; prevents indefinitely hung workflows
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
# Attribute-root deny-list is intentionally narrow: scripts cannot import
# modules, so only names that are pre-injected via _safe_globals() need to
# be listed here. os and subprocess are the two that would be most harmful
# if they were ever inadvertently exposed.
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
        self._closed = False

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
        if self._closed:
            raise WorkflowScriptError("WorkflowContext is already closed")
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

        A per-call ``max_concurrency`` caps concurrency for this map operation
        only; it is silently capped at the context's ``max_concurrency`` limit.
        """
        if max_concurrency is not None and max_concurrency < 1:
            raise ValueError("max_concurrency must be at least 1")
        semaphore = (
            asyncio.Semaphore(min(max_concurrency, self._max_concurrency))
            if max_concurrency is not None
            else self._default_semaphore
        )

        async def run_one(index: int, item: Any) -> str:
            rendered_prompt = _render_required_template(prompt, item)
            rendered_description = _render_template(description, item)
            async with semaphore:
                try:
                    return await self._run_agent_task(
                        prompt=rendered_prompt,
                        subagent_type=subagent_type,
                        description=rendered_description,
                    )
                except Exception as exc:
                    raise RuntimeError(f"[item {index + 1}] {exc}") from exc

        results = await asyncio.gather(
            *(run_one(i, item) for i, item in enumerate(items)),
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
        """Run a single reducer sub-agent with serialized intermediate results.

        Delegates to ``run_agent``, which acquires ``_default_semaphore``.
        Counts against the shared concurrency limit if called while a
        ``map_agents`` operation is in progress.
        """
        return await self.run_agent(
            prompt=f"{prompt}\n\nInput:\n{_format_value(items)}",
            subagent_type=subagent_type,
            description=description,
        )

    def flatten(self, values: list[Any]) -> list[Any]:
        """Flatten one list level."""
        flattened: list[Any] = []
        for value in values:
            if isinstance(value, list):
                flattened.extend(value)
            else:
                flattened.append(value)
        return flattened

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._manager.close()


def _render_required_template(template: Callable[[Any], str] | str, item: Any) -> str:
    if callable(template):
        return str(template(item))
    # Plain replace avoids Python's format mini-language attribute traversal
    # (e.g. "{item._manager}"), which would bypass the AST private-attribute guard.
    return template.replace("{item}", str(item))


def _render_template(
    template: Callable[[Any], str] | str | None, item: Any
) -> str | None:
    if template is None:
        return None
    return _render_required_template(template, item)


def _format_value(value: Any) -> str:
    if isinstance(value, str):
        text = value
    else:
        text = jsonlib.dumps(value, indent=2, default=str)
    if len(text) <= _MAX_REDUCE_INPUT_CHARS:
        return text
    return (
        text[:_MAX_REDUCE_INPUT_CHARS]
        + "\n... [truncated workflow intermediate results]"
    )


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
    if (
        len(main_def.args.args) != 1
        or main_def.args.args[0].arg != "wf"
        or main_def.args.kwonlyargs
        or main_def.args.vararg
        or main_def.args.kwarg
    ):
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
            and _attribute_root_name(node) == "wf"
            and node.attr.startswith("_")
        ):
            raise WorkflowScriptError(
                "Workflow scripts may not access private wf attributes"
            )
        if (
            isinstance(node, ast.Attribute)
            and _attribute_root_name(node) in _UNSAFE_ATTRIBUTE_ROOTS
        ):
            raise WorkflowScriptError("Workflow scripts may not access unsafe modules")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in _UNSAFE_CALLS:
                raise WorkflowScriptError(
                    f"Workflow scripts may not call `{node.func.id}`"
                )


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

    async def _run_with_timeout() -> Any:
        async with asyncio.timeout(_WORKFLOW_TIMEOUT_SECONDS):
            return await main(context)

    try:
        return asyncio.run(_run_with_timeout())
    except TimeoutError:
        raise WorkflowScriptError(
            f"Workflow timed out after {_WORKFLOW_TIMEOUT_SECONDS:.0f} seconds"
        ) from None


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
        "Exception": Exception,
        "float": float,
        "IndexError": IndexError,
        "int": int,
        "isinstance": isinstance,
        "type": type,
        "KeyError": KeyError,
        "len": len,
        "list": list,
        "max": max,
        "min": min,
        "print": print,
        "range": range,
        "repr": repr,
        "round": round,
        "RuntimeError": RuntimeError,
        "set": set,
        "sorted": sorted,
        "str": str,
        "sum": sum,
        "tuple": tuple,
        "TypeError": TypeError,
        "ValueError": ValueError,
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
