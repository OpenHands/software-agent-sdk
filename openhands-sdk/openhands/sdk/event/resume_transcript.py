"""Render SDK events as a resume-bootstrap transcript.

When an ACP-backed conversation must restart but the ACP server's own
session storage has been wiped (e.g. the sandbox was recycled), the
``session/load`` resume path is unavailable: the server has no record
of the session id we persisted. One workaround is to start a fresh
``new_session`` and replay the SDK's durable event history as the
opening user message — a "bootstrap-prompt resume".

This module provides the rendering primitive. The caller decides where
events come from (durable event store, in-memory state, …), how to
package the rendered string (e.g. as a ``SendMessageRequest``), and
what provider-specific post-processing to apply to the result (path
sanitization, output scrubbing, etc.).

The companion ``RESUME_CONTEXT_MARKER`` constant is exported so
producers and consumers can both detect an already-resumed message
without hard-coding the string.
"""

from __future__ import annotations

from collections.abc import Sequence

from openhands.sdk.event.acp_tool_call import ACPToolCallEvent
from openhands.sdk.event.base import Event
from openhands.sdk.event.llm_convertible import ActionEvent, MessageEvent
from openhands.sdk.llm import content_to_str


RESUME_CONTEXT_MARKER = "<<RESUMED CONVERSATION>>"
"""Header marker prefixing every bootstrap-resume transcript.

Both producers (the renderer) and consumers (callers that need to
detect an already-resumed message and avoid double-wrapping) reference
this constant so the contract is single-sourced.
"""

DEFAULT_HEADER_BODY = (
    "The conversation history below is from a prior session whose live "
    "context was lost. Treat it as background and continue from where "
    "the previous session left off."
)
DEFAULT_FOOTER = "--- End of prior session ---"

DEFAULT_MAX_CHARS = 60_000
DEFAULT_MAX_MESSAGE_CHARS = 8_000
DEFAULT_MAX_TOOL_CHARS = 2_000


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


def _render_message_event(event: MessageEvent, max_chars: int) -> str | None:
    role_label = "[USER]" if event.llm_message.role == "user" else "[ASSISTANT]"
    parts = [p for p in content_to_str(event.llm_message.content) if p]
    text = "\n".join(parts).strip()
    if not text:
        return None
    return f"{role_label}: {_truncate(text, max_chars)}"


def _render_action_event(event: ActionEvent, max_chars: int) -> str | None:
    # Built-in Actions (e.g. ``FinishAction``) expose a ``message`` field that
    # carries the agent's final summary for the turn. Other Actions don't, and
    # the LLMConvertible path renders them separately — skip silently.
    message = getattr(event.action, "message", None) if event.action else None
    if not isinstance(message, str) or not message.strip():
        return None
    return f"[AGENT]: {_truncate(message.strip(), max_chars)}"


def _render_tool_event(event: ACPToolCallEvent, max_chars: int) -> str | None:
    # ACP streams ``pending → pending → completed`` for a single tool call;
    # placeholder events emitted before parameters arrive carry no input,
    # no output, and ``is_error`` is False — skip them so the transcript
    # doesn't repeat every tool call.
    if not event.raw_input and not event.raw_output and not event.is_error:
        return None
    status = "failed" if event.is_error else (event.status or "completed")
    name = event.title or event.tool_kind or "tool"
    parts: list[str] = [f"[TOOL USE: {name}] ({status})"]
    if event.raw_input:
        parts.append("  input:")
        for line in str(event.raw_input).splitlines() or [""]:
            parts.append(f"    {line}")
    if event.raw_output:
        parts.append("  output:")
        for line in str(event.raw_output).splitlines() or [""]:
            parts.append(f"    {line}")
    return _truncate("\n".join(parts), max_chars)


def _terminal_tool_indices(events: Sequence[Event]) -> set[int]:
    """Indices of the *terminal* ACPToolCallEvent for each ``tool_call_id``.

    Events whose ``tool_call_id`` appears with a later index are non-terminal
    and should be skipped.
    """
    last: dict[str, int] = {}
    for i, event in enumerate(events):
        if isinstance(event, ACPToolCallEvent) and event.tool_call_id:
            last[event.tool_call_id] = i
    return set(last.values())


def render_resume_transcript(
    events: Sequence[Event],
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
    max_message_chars: int = DEFAULT_MAX_MESSAGE_CHARS,
    max_tool_chars: int = DEFAULT_MAX_TOOL_CHARS,
    marker: str = RESUME_CONTEXT_MARKER,
    header_body: str = DEFAULT_HEADER_BODY,
    footer: str = DEFAULT_FOOTER,
) -> str | None:
    """Render ``events`` as a single resume-bootstrap transcript string.

    Returns ``None`` when no event in ``events`` produces visible output
    (e.g. a fresh conversation, or only filtered placeholder tool events).

    ``MessageEvent``s become ``[USER]: …`` / ``[ASSISTANT]: …`` blocks,
    ``ACPToolCallEvent``s become ``[TOOL USE: <name>] (<status>)`` blocks
    with raw input/output indented underneath, and ``ActionEvent``s whose
    ``action`` exposes a ``message`` (e.g. ``FinishAction``) become
    ``[AGENT]: …`` summary lines. Other event types are ignored.

    ``ACPToolCallEvent``s are deduplicated by ``tool_call_id``: only the
    final (terminal) event in each ACP streaming pending→completed
    sequence is rendered.

    The caller is responsible for:
      * passing events in chronological order (newest-first fetches must
        be reversed before being handed in);
      * any provider-specific scrubbing of tool ``raw_input`` /
        ``raw_output`` (path sanitization, filtering provider-internal
        metadata keys, stripping shell/test boilerplate, etc.);
      * packaging the rendered string into a ``SendMessageRequest`` or
        equivalent message envelope.
    """
    keep_tool_indices = _terminal_tool_indices(events)

    lines: list[str] = []
    for i, event in enumerate(events):
        rendered: str | None
        if isinstance(event, MessageEvent):
            rendered = _render_message_event(event, max_message_chars)
        elif isinstance(event, ACPToolCallEvent):
            if event.tool_call_id and i not in keep_tool_indices:
                continue
            rendered = _render_tool_event(event, max_tool_chars)
        elif isinstance(event, ActionEvent):
            rendered = _render_action_event(event, max_message_chars)
        else:
            rendered = None
        if rendered:
            lines.append(rendered)
            lines.append("")

    if not lines:
        return None

    header = [marker, "", header_body, ""] if header_body else [marker, ""]
    return _truncate("\n".join(header + lines + [footer]), max_chars)
