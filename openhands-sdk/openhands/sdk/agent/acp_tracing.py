"""Observability spans for ACP turns.

The ACP subprocess runs its own inference and tool execution, so neither
lmnr's LiteLLM instrumentation nor ``Agent._execute_action_event`` ever fires
and an ACP trace carries no ``LLM`` or ``TOOL`` spans. This module emits them
from the ACP protocol's own notifications, shaped like the native ones so a
trace consumer needs no ACP-specific branch.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from openhands.sdk.logger import get_logger
from openhands.sdk.observability.laminar import should_enable_observability


if TYPE_CHECKING:
    from lmnr.sdk.types import LaminarSpanContext

logger = get_logger(__name__)

#: Marks a span as produced by an ACP agent rather than the native loop.
AGENT_KIND_METADATA_KEY = "agent_kind"
#: Which ACP CLI produced it ('claude-code', 'codex', 'gemini-cli', 'custom').
ACP_SERVER_METADATA_KEY = "acp_server"
#: Model the ACP CLI reported for the turn, when it reports one.
ACP_MODEL_METADATA_KEY = "acp_model"

TURN_SPAN_NAME = "acp.completion"


def _truncate(value: Any, limit: int = 100_000) -> Any:
    """Cap what a span carries.

    Non-strings are measured by their serialized size and replaced wholesale when
    oversized, rather than passed through: an ACP prompt is a list of content
    blocks, and one image block can be megabytes of base64.
    """
    if isinstance(value, str):
        return value[:limit] + "…[truncated]" if len(value) > limit else value
    try:
        rendered = json.dumps(value, default=str)
    except Exception:
        return value
    if len(rendered) <= limit:
        return value
    return rendered[:limit] + "…[truncated]"


class ACPTurnTrace:
    """Emits one ``LLM`` span per ACP turn plus a ``TOOL`` span per tool call.

    Every method is a no-op when observability is disabled, and no method may
    raise: a tracing failure must not take down the turn it is describing.

    Tool notifications arrive on the ACP executor's event-loop thread while the
    turn is opened and closed on the caller's, so children are parented by explicit
    span context rather than by ambient ``contextvars`` and the shared state is
    mutated under a lock. Span I/O stays outside the lock, except in
    ``start_turn``, where the span must be created and stored as one step so a
    retry cannot open a second one.
    """

    def __init__(
        self,
        acp_server: str | None,
        model_id: str | None,
        mask: Callable[[str], str] | None = None,
    ) -> None:
        self._mask = mask
        self._enabled = should_enable_observability()
        self._metadata: dict[str, Any] = {AGENT_KIND_METADATA_KEY: "acp"}
        if acp_server:
            self._metadata[ACP_SERVER_METADATA_KEY] = acp_server
        if model_id:
            self._metadata[ACP_MODEL_METADATA_KEY] = model_id
        self._turn_span: Any = None
        self._turn_context: LaminarSpanContext | None = None
        self._tool_spans: dict[str, Any] = {}
        self._lock = threading.Lock()

    def start_turn(self, prompt: Any) -> None:
        if not self._enabled:
            return
        try:
            from lmnr import Laminar

            # Under the lock like every other mutation: a retry re-enters this
            # while the portal thread may still be delivering notifications from
            # the attempt that just failed.
            with self._lock:
                if self._turn_span is not None:
                    return
                span = Laminar.start_span(
                    name=TURN_SPAN_NAME,
                    input=_truncate(_mask_prompt(prompt, self._mask)),
                    span_type="LLM",
                    metadata=dict(self._metadata),
                )
                self._turn_span = span
                self._turn_context = Laminar.get_laminar_span_context(span)
        except Exception:
            logger.debug("ACP turn span could not be started", exc_info=True)
            with self._lock:
                self._turn_span = None
                self._turn_context = None

    def tool_started(self, entry: dict[str, Any]) -> None:
        if not self._enabled or self._turn_span is None:
            return
        call_id = str(entry.get("tool_call_id") or "")
        if not call_id:
            return
        try:
            from lmnr import Laminar

            metadata = dict(self._metadata)
            metadata["tool_call_id"] = call_id
            with self._lock:
                if call_id in self._tool_spans or self._turn_span is None:
                    return
                self._tool_spans[call_id] = Laminar.start_span(
                    name=str(
                        entry.get("title") or entry.get("tool_kind") or "acp_tool"
                    ),
                    input=_truncate(_tool_input(entry)),
                    span_type="TOOL",
                    parent_span_context=self._turn_context,
                    metadata=metadata,
                )
        except Exception:
            logger.debug("ACP tool span could not be started", exc_info=True)

    def tool_finished(self, entry: dict[str, Any]) -> None:
        with self._lock:
            span = self._tool_spans.pop(str(entry.get("tool_call_id") or ""), None)
        if span is None:
            return
        self._close_tool_span(span, entry)

    def finish_turn(
        self,
        text: str,
        thoughts: str,
        tool_calls: list[dict[str, Any]],
    ) -> None:
        """Set the turn's assistant message and close every span it opened."""
        with self._lock:
            open_spans = list(self._tool_spans.items())
            self._tool_spans.clear()
            span, self._turn_span, self._turn_context = self._turn_span, None, None
        for call_id, tool_span in open_spans:
            entry = next(
                (t for t in tool_calls if str(t.get("tool_call_id")) == call_id), {}
            )
            self._close_tool_span(tool_span, entry)

        if span is None:
            return
        try:
            span.set_output([_assistant_message(text, thoughts, tool_calls)])
        except Exception:
            logger.debug("ACP turn span output could not be set", exc_info=True)
        try:
            span.end()
        except Exception:
            logger.debug("ACP turn span could not be ended", exc_info=True)

    def abandon(self) -> None:
        """Close whatever is still open after a timed-out or failed turn."""
        with self._lock:
            open_spans = list(self._tool_spans.values())
            self._tool_spans.clear()
            span, self._turn_span, self._turn_context = self._turn_span, None, None
        for tool_span in open_spans:
            self._close_tool_span(tool_span, {})
        if span is None:
            return
        try:
            span.end()
        except Exception:
            logger.debug("ACP turn span could not be ended", exc_info=True)

    @staticmethod
    def _close_tool_span(span: Any, entry: dict[str, Any]) -> None:
        try:
            output = entry.get("raw_output")
            if output is None:
                output = entry.get("content")
            span.set_output(_observation(output))
        except Exception:
            logger.debug("ACP tool span output could not be set", exc_info=True)
        try:
            span.end()
        except Exception:
            logger.debug("ACP tool span could not be ended", exc_info=True)


def _mask_prompt(prompt: Any, mask: Callable[[str], str] | None) -> Any:
    """Run the conversation's secret masker over every string in the prompt.

    Returns ``None`` rather than the original if masking fails: an unrecorded
    prompt is recoverable, an unmasked one shipped to the backend is not.
    """
    if mask is None:
        return prompt

    def walk(node: Any) -> Any:
        if isinstance(node, str):
            return mask(node)
        if isinstance(node, list):
            return [walk(v) for v in node]
        if isinstance(node, dict):
            return {k: walk(v) for k, v in node.items()}
        dump = getattr(node, "model_dump", None)
        return walk(dump()) if callable(dump) else node

    try:
        return walk(prompt)
    except Exception:
        logger.debug("ACP prompt could not be masked", exc_info=True)
        return None


def _observation(output: Any) -> dict[str, Any]:
    """Wrap a tool result the way a native ``Observation`` serializes.

    A bare string would be stored as a JSON scalar and reach consumers still
    wrapped in its quotes; only a ``{``/``[`` payload gets parsed back.
    """
    if not isinstance(output, str):
        try:
            output = json.dumps(output, default=str)
        except Exception:
            output = str(output)
    return {"content": [{"type": "text", "text": _truncate(output)}]}


def _assistant_message(
    text: str,
    thoughts: str,
    tool_calls: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the OpenAI-shaped assistant message a trace consumer expects."""
    message: dict[str, Any] = {"role": "assistant", "content": text or ""}
    if thoughts:
        message["reasoning_content"] = thoughts
    calls = [_tool_call(tc) for tc in tool_calls if tc.get("tool_call_id")]
    if calls:
        message["tool_calls"] = calls
    return message


def _tool_input(entry: dict[str, Any]) -> Any:
    """What the tool was called with, as far as the server reports it.

    ``raw_input`` is optional in ACP and Codex omits it entirely, leaving the
    display ``title`` as the only signal of what the call was for.
    """
    raw_input = entry.get("raw_input")
    if raw_input is not None:
        return raw_input
    title = entry.get("title")
    return {"title": title} if title else {}


def _tool_call(entry: dict[str, Any]) -> dict[str, Any]:
    raw_input = _tool_input(entry)
    if not isinstance(raw_input, str):
        try:
            raw_input = json.dumps(raw_input, default=str)
        except Exception:
            raw_input = "{}"
    return {
        "id": str(entry.get("tool_call_id") or ""),
        "type": "function",
        "function": {
            # ACP reports a display ``title`` and a categorical ``kind``; only
            # the latter is stable across invocations of the same tool.
            "name": str(entry.get("tool_kind") or entry.get("title") or "acp_tool"),
            "arguments": raw_input,
        },
    }
