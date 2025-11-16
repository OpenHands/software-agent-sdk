from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

from openhands.sdk.conversation.visualizer import ConversationVisualizer
from openhands.sdk.event import ActionEvent, MessageEvent, StreamingDeltaEvent
from openhands.sdk.event.base import Event
from openhands.sdk.llm.llm import RESPONSES_COMPLETION_EVENT_TYPES
from openhands.sdk.llm.streaming import StreamPartKind


if TYPE_CHECKING:
    from openhands.sdk.conversation.conversation_stats import ConversationStats
    from openhands.sdk.llm.streaming import LLMStreamChunk


# These are external inputs
_OBSERVATION_COLOR = "yellow"
_THOUGHT_COLOR = "bright_black"
_ERROR_COLOR = "red"
# These are agent actions
_ACTION_COLOR = "blue"
_MESSAGE_ASSISTANT_COLOR = _ACTION_COLOR

DEFAULT_HIGHLIGHT_REGEX = {
    r"^Reasoning:": f"bold {_THOUGHT_COLOR}",
    r"^Thought:": f"bold {_THOUGHT_COLOR}",
    r"^Action:": f"bold {_ACTION_COLOR}",
    r"^Arguments:": f"bold {_ACTION_COLOR}",
    r"^Tool:": f"bold {_OBSERVATION_COLOR}",
    r"^Result:": f"bold {_OBSERVATION_COLOR}",
    r"^Rejection Reason:": f"bold {_ERROR_COLOR}",
    # Markdown-style
    r"\*\*(.*?)\*\*": "bold",
    r"\*(.*?)\*": "italic",
}

_PANEL_PADDING = (1, 1)
_SECTION_CONFIG: dict[str, tuple[str, str]] = {
    "reasoning": ("Reasoning", _THOUGHT_COLOR),
    "assistant": ("Assistant", _ACTION_COLOR),
    "function_arguments": ("Function Arguments", _ACTION_COLOR),
    "tool_output": ("Tool Output", _ACTION_COLOR),
    "refusal": ("Refusal", _ERROR_COLOR),
}

_SESSION_CONFIG: dict[str, tuple[str, str]] = {
    "message": (
        f"[bold {_MESSAGE_ASSISTANT_COLOR}]Message from Agent (streaming)"  # type: ignore[str-format]
        f"[/bold {_MESSAGE_ASSISTANT_COLOR}]",
        _MESSAGE_ASSISTANT_COLOR,
    ),
    "action": (
        f"[bold {_ACTION_COLOR}]Agent Action (streaming)[/bold {_ACTION_COLOR}]",
        _ACTION_COLOR,
    ),
}

_SECTION_ORDER = [
    "reasoning",
    "assistant",
    "function_arguments",
    "tool_output",
    "refusal",
]


@dataclass(slots=True)
class _StreamSection:
    header: str
    style: str
    content: str = ""


class _StreamSession:
    def __init__(
        self,
        *,
        console: Console,
        session_type: str,
        response_id: str | None,
        output_index: int | None,
        use_live: bool,
    ) -> None:
        self._console: Console = console
        self._session_type: str = session_type
        self._response_id: str | None = response_id
        self._output_index: int | None = output_index
        self._use_live: bool = use_live
        self._sections: dict[str, _StreamSection] = {}
        self._order: list[str] = []
        self._live: Live | None = None
        self._last_renderable: Panel | None = None

    @property
    def response_id(self) -> str | None:
        return self._response_id

    def append_text(self, section_key: str, text: str | None) -> None:
        if not text:
            return
        header, style = _SECTION_CONFIG.get(section_key, (section_key.title(), "cyan"))
        section = self._sections.get(section_key)
        if section is None:
            section = _StreamSection(header, style)
            self._sections[section_key] = section
            self._order.append(section_key)
            self._order.sort(
                key=lambda key: _SECTION_ORDER.index(key)
                if key in _SECTION_ORDER
                else len(_SECTION_ORDER)
            )
        section.content += text
        self._update()

    def finish(self, *, persist: bool) -> None:
        renderable = self._render_panel()
        if self._use_live:
            if self._live is not None:
                self._live.stop()
                self._live = None
            if persist:
                self._console.print(renderable)
                self._console.print()
            else:
                self._console.print()
        else:
            if persist:
                self._console.print(renderable)
                self._console.print()

    def _update(self) -> None:
        renderable = self._render_panel()
        if self._use_live:
            if self._live is None:
                self._live = Live(
                    renderable,
                    console=self._console,
                    refresh_per_second=24,
                    transient=True,
                )
                self._live.start()
            else:
                self._live.update(renderable)
        else:
            self._last_renderable = renderable

    def _render_panel(self) -> Panel:
        body_parts: list[Any] = []
        for key in self._order:
            section = self._sections[key]
            if not section.content:
                continue
            body_parts.append(Text(f"{section.header}:", style=f"bold {section.style}"))
            body_parts.append(Text(section.content, style=section.style))
        if not body_parts:
            body_parts.append(Text("[streaming...]", style="dim"))

        title, border_style = _SESSION_CONFIG.get(
            self._session_type, ("[bold cyan]Streaming[/bold cyan]", "cyan")
        )
        return Panel(
            Group(*body_parts),
            title=title,
            border_style=border_style,
            padding=_PANEL_PADDING,
            expand=True,
        )


class StreamingConversationVisualizer(ConversationVisualizer):
    """Streaming-focused visualizer that renders deltas in-place."""

    requires_streaming: bool = True

    def __init__(
        self,
        highlight_regex: dict[str, str] | None = None,
        skip_user_messages: bool = False,
        conversation_stats: "ConversationStats | None" = None,
    ) -> None:
        super().__init__(
            highlight_regex=highlight_regex,
            skip_user_messages=skip_user_messages,
            conversation_stats=conversation_stats,
        )
        self._use_live: bool = self._console.is_terminal
        self._stream_sessions: dict[tuple[str, int, str], _StreamSession] = {}

    def on_event(self, event: Event) -> None:
        if isinstance(event, StreamingDeltaEvent):
            self._handle_stream_chunk(event.stream_chunk, persist_on_finish=True)
            return

        if self._should_skip_event(event):
            return

        super().on_event(event)

    def _handle_stream_chunk(
        self, stream_chunk: "LLMStreamChunk", *, persist_on_finish: bool
    ) -> None:
        if stream_chunk.part_kind == "status":
            if (
                stream_chunk.type in RESPONSES_COMPLETION_EVENT_TYPES
                or stream_chunk.is_final
            ):
                self._finish_stream_sessions(
                    stream_chunk.response_id, persist=persist_on_finish
                )
            return

        session_type = self._session_type_for_part(stream_chunk.part_kind)
        if session_type is None:
            return

        key = self._make_stream_session_key(stream_chunk, session_type)
        session = self._stream_sessions.get(key)
        if session is None:
            session = _StreamSession(
                console=self._console,
                session_type=session_type,
                response_id=stream_chunk.response_id,
                output_index=stream_chunk.output_index,
                use_live=self._use_live,
            )
            self._stream_sessions[key] = session

        section_key = self._section_key_for_part(stream_chunk.part_kind)
        session.append_text(
            section_key, stream_chunk.text_delta or stream_chunk.arguments_delta
        )

        if stream_chunk.is_final:
            if persist_on_finish:
                self._finish_session_by_key(key, persist=True)
            else:
                if not self._use_live:
                    self._finish_session_by_key(key, persist=False)
                elif stream_chunk.response_id is None:
                    self._finish_session_by_key(key, persist=False)

    def _session_type_for_part(self, part_kind: StreamPartKind) -> str | None:
        if part_kind in {"assistant_message", "reasoning_summary", "refusal"}:
            return "message"
        if part_kind in {"function_call_arguments", "tool_call_output"}:
            return "action"
        return None

    def _section_key_for_part(self, part_kind: StreamPartKind) -> str:
        if part_kind == "assistant_message":
            return "assistant"
        if part_kind == "reasoning_summary":
            return "reasoning"
        if part_kind == "function_call_arguments":
            return "function_arguments"
        if part_kind == "tool_call_output":
            return "tool_output"
        if part_kind == "refusal":
            return "refusal"
        return "assistant"

    def _make_stream_session_key(
        self, chunk: "LLMStreamChunk", session_type: str
    ) -> tuple[str, int, str]:
        response_key = (
            chunk.response_id
            or f"unknown::{chunk.item_id or chunk.output_index or chunk.type}"
        )
        output_index = chunk.output_index if chunk.output_index is not None else 0
        return (response_key, output_index, session_type)

    def _finish_stream_sessions(
        self, response_id: str | None, *, persist: bool
    ) -> None:
        if not self._stream_sessions:
            return
        if response_id is None:
            keys = list(self._stream_sessions.keys())
        else:
            keys = [
                key
                for key, session in self._stream_sessions.items()
                if session.response_id == response_id
            ]
            if not keys:
                keys = list(self._stream_sessions.keys())
        for key in keys:
            self._finish_session_by_key(key, persist=persist)

    def _finish_session_by_key(
        self, key: tuple[str, int, str], *, persist: bool
    ) -> None:
        session = self._stream_sessions.pop(key, None)
        if session is not None:
            session.finish(persist=persist)

    def _create_event_panel(self, event: Event) -> Panel | None:
        if isinstance(event, StreamingDeltaEvent):
            content = event.visualize
            if not content.plain.strip():
                return None
            if self._highlight_patterns:
                content = self._apply_highlighting(content)
            return Panel(
                content,
                title="[bold cyan]Streaming Delta[/bold cyan]",
                border_style="cyan",
                padding=_PANEL_PADDING,
                expand=True,
            )
        return super()._create_event_panel(event)

    def _should_skip_event(self, event: Event) -> bool:
        if isinstance(event, MessageEvent) and event.source == "agent":
            return True
        if isinstance(event, ActionEvent) and event.source == "agent":
            return True
        return False


def create_streaming_visualizer(
    highlight_regex: dict[str, str] | None = None,
    conversation_stats: "ConversationStats | None" = None,
    **kwargs,
) -> StreamingConversationVisualizer:
    """Create a streaming-aware visualizer instance."""

    return StreamingConversationVisualizer(
        highlight_regex=DEFAULT_HIGHLIGHT_REGEX
        if highlight_regex is None
        else highlight_regex,
        conversation_stats=conversation_stats,
        **kwargs,
    )
