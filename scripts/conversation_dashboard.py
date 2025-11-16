"""OpenHands conversation dashboard for real-time LLM event introspection.

Run with:
    streamlit run scripts/conversation_dashboard.py
"""

from __future__ import annotations

import importlib
import io
import json
import pkgutil
import time
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any, cast

import streamlit as st
from pydantic import ValidationError

from openhands.sdk.event import (
    ActionEvent,
    AgentErrorEvent,
    Condensation,
    CondensationRequest,
    CondensationSummaryEvent,
    Event,
    LLMConvertibleEvent,
    MessageEvent,
    ObservationEvent,
    PauseEvent,
    SystemPromptEvent,
    UserRejectObservation,
)
from openhands.sdk.llm import Message, content_to_str


DEFAULT_CONVERSATIONS_ROOT = Path("~/.openhands/conversations").expanduser()

st.set_page_config(
    page_title="OpenHands SDK Conversation Dashboard",
    layout="wide",
)

_TOOL_MODULES_LOADED = False
TOOL_IMPORT_ERRORS: list[str] = []

ROLE_STYLES: dict[str, dict[str, str]] = {
    "assistant": {"label": "LLM", "color": "#1f6feb"},
    "user": {"label": "User", "color": "#8e44ad"},
    "tool": {"label": "Tool", "color": "#7f8c8d"},
    "system": {"label": "System", "color": "#2c3e50"},
}
MESSAGE_PREVIEW_LIMIT = 200


def ensure_tool_modules_loaded() -> None:
    global _TOOL_MODULES_LOADED
    if _TOOL_MODULES_LOADED:
        return

    try:
        import openhands.tools as tools_pkg
    except Exception as exc:  # pragma: no cover - defensive import guard
        TOOL_IMPORT_ERRORS.append(f"openhands.tools: {exc}")
        _TOOL_MODULES_LOADED = True
        return

    for module_info in pkgutil.walk_packages(
        tools_pkg.__path__, f"{tools_pkg.__name__}."
    ):
        name = module_info.name
        if ".tests." in name or name.endswith(".tests"):
            continue
        try:
            importlib.import_module(name)
        except Exception as exc:  # pragma: no cover - best effort to load
            TOOL_IMPORT_ERRORS.append(f"{name}: {exc}")

    _TOOL_MODULES_LOADED = True


ensure_tool_modules_loaded()


def trigger_rerun() -> None:
    rerun = getattr(st, "rerun", None)
    if not callable(rerun):  # pragma: no cover - compatibility guard
        raise RuntimeError(
            "streamlit.rerun is not available in this Streamlit version. "
            "Please upgrade Streamlit to a release that includes st.rerun()."
        )
    rerun()


def _prepare_filter_state(
    key: str, options: Sequence[str], defaults: Sequence[str] | None = None
) -> dict[str, bool]:
    sorted_options = list(options)
    default_set = set(defaults or sorted_options)
    current_state = {
        opt: st.session_state.get(key, {}).get(opt, opt in default_set)
        for opt in sorted_options
    }
    st.session_state[key] = current_state
    return current_state


def render_checkbox_filter_group(
    key: str,
    options: Sequence[str],
    defaults: Sequence[str] | None = None,
    columns: int = 3,
) -> list[str]:
    if not options:
        return []

    options = list(options)
    state = _prepare_filter_state(key, options, defaults)
    num_columns = min(columns, len(options))
    cols = st.columns(num_columns)

    for idx, option in enumerate(options):
        column = cols[idx % num_columns]
        with column:
            widget_key = f"{key}__{option}"
            initial = st.session_state.get(widget_key, state[option])
            value = st.checkbox(option, value=initial, key=widget_key)
            state[option] = value

    st.session_state[key] = state
    return [opt for opt, selected in state.items() if selected]


def describe_block(block: LLMMessageBlock) -> str | None:
    if not block.events:
        return None
    event = block.events[0].event
    if isinstance(event, MessageEvent):
        return "message"
    if isinstance(event, SystemPromptEvent):
        return "system prompt"
    if isinstance(event, ObservationEvent):
        return "observation"
    if isinstance(event, Condensation):
        return "condensation"
    return None


def render_message_header(text: str, color: str) -> None:
    st.markdown(
        f"""
        <div style="
            border-left:4px solid {color};
            padding:0.9rem 1.2rem;
            background-color:rgba(15,23,42,0.03);
            border-radius:10px;
            margin:0.8rem 0;
        ">
            <span style="
                color:{color};
                font-weight:600;
                font-size:1.05rem;
            ">{escape(text)}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_section_label(text: str, color: str) -> None:
    st.markdown(
        f"""
        <div style="
            color:{color};
            font-weight:600;
            margin-top:0.75rem;
            margin-bottom:0.35rem;
            font-size:0.95rem;
        ">
            {escape(text)}
        </div>
        """,
        unsafe_allow_html=True,
    )


@dataclass(frozen=True)
class EventRecord:
    filename: str
    raw: dict[str, Any]
    event: Event | None

    @property
    def kind(self) -> str:
        if self.event is not None:
            return self.event.__class__.__name__
        return str(self.raw.get("kind", "Unknown"))

    @property
    def source(self) -> str:
        if self.event is not None:
            return str(self.event.source)
        return str(self.raw.get("source", "Unknown"))

    @property
    def timestamp(self) -> datetime | None:
        if self.event is not None and isinstance(self.event.timestamp, str):
            parsed = parse_timestamp(self.event.timestamp)
            if parsed is not None:
                return parsed
        raw_ts = self.raw.get("timestamp")
        if isinstance(raw_ts, str):
            return parse_timestamp(raw_ts)
        return None


@dataclass(frozen=True)
class ConversationData:
    identifier: str
    path: Path
    base_state: dict[str, Any]
    events: list[EventRecord]


@dataclass(frozen=True)
class LLMMessageBlock:
    message: Message
    events: list[EventRecord]


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value)
    except Exception:
        return None


@st.cache_data(show_spinner=False, ttl=1)
def list_conversation_dirs(root: str) -> list[str]:
    path = Path(root)
    if not path.exists() or not path.is_dir():
        return []
    directories = [p for p in path.iterdir() if p.is_dir()]

    def sort_key(p: Path) -> tuple[str, str]:
        ts_iso = compute_last_event_timestamp(str(p)) or ""
        return ts_iso, p.name

    directories.sort(key=sort_key, reverse=True)
    return [str(p) for p in directories]


@st.cache_data(show_spinner=False, ttl=1)
def compute_last_event_timestamp(path_str: str) -> str | None:
    path = Path(path_str)
    events_dir = path / "events"
    if not events_dir.exists():
        return None

    latest: datetime | None = None
    for event_file in events_dir.glob("*.json"):
        try:
            with event_file.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError):
            continue
        timestamp = data.get("timestamp")
        if not isinstance(timestamp, str):
            continue
        dt = parse_timestamp(timestamp)
        if dt is None:
            continue
        if latest is None or dt > latest:
            latest = dt
    return latest.isoformat() if latest else None


def load_conversation(path_str: str) -> ConversationData:
    path = Path(path_str)
    identifier = path.name

    base_state: dict[str, Any] = {}
    base_state_path = path / "base_state.json"
    if base_state_path.exists():
        try:
            with base_state_path.open("r", encoding="utf-8") as handle:
                base_state = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            base_state = {"error": f"Failed to parse base_state.json: {exc}"}

    events: list[EventRecord] = []
    events_dir = path / "events"
    if events_dir.exists():
        for event_file in sorted(events_dir.glob("*.json")):
            try:
                with event_file.open("r", encoding="utf-8") as handle:
                    raw_data = json.load(handle)
            except json.JSONDecodeError as exc:
                raw_data = {
                    "kind": "InvalidJSON",
                    "error": f"Failed to parse {event_file.name}: {exc}",
                    "timestamp": None,
                }
                event_model: Event | None = None
            except OSError as exc:
                raw_data = {
                    "kind": "IOError",
                    "error": f"Failed to read {event_file.name}: {exc}",
                    "timestamp": None,
                }
                event_model = None
            else:
                try:
                    event_model = Event.model_validate(raw_data)
                except ValidationError as exc:
                    raw_data = dict(raw_data)
                    raw_data["_validation_error"] = exc.errors()
                    event_model = None
            events.append(
                EventRecord(filename=event_file.name, raw=raw_data, event=event_model)
            )

    return ConversationData(
        identifier=identifier,
        path=path,
        base_state=base_state,
        events=events,
    )


def build_llm_message_blocks(records: Sequence[EventRecord]) -> list[LLMMessageBlock]:
    convertible_records = [
        rec for rec in records if isinstance(rec.event, LLMConvertibleEvent)
    ]
    blocks: list[LLMMessageBlock] = []
    i = 0
    while i < len(convertible_records):
        record = convertible_records[i]
        event = cast(LLMConvertibleEvent, record.event)
        if isinstance(event, ActionEvent):
            response_id = event.llm_response_id
            grouped = [record]
            j = i + 1
            while j < len(convertible_records):
                next_record = convertible_records[j]
                next_event = cast(LLMConvertibleEvent, next_record.event)
                if not isinstance(next_event, ActionEvent):
                    break
                if next_event.llm_response_id != response_id:
                    break
                grouped.append(next_record)
                j += 1
            message = LLMConvertibleEvent.events_to_messages(
                [cast(LLMConvertibleEvent, r.event) for r in grouped]
            )[0]
            blocks.append(LLMMessageBlock(message=message, events=grouped))
            i = j
        else:
            message = event.to_llm_message()
            blocks.append(LLMMessageBlock(message=message, events=[record]))
            i += 1
    return blocks


def message_text(message: Message) -> str:
    parts = [part.strip() for part in content_to_str(message.content) if part.strip()]
    return "\n\n".join(parts)


def truncate_system_prompt(
    text: str, max_lines: int = 30, max_chars: int = 1200
) -> tuple[str, bool]:
    if not text:
        return "", False
    lines = text.splitlines()
    truncated = False
    if len(lines) > max_lines:
        head_count = max_lines // 2
        head = lines[:head_count]
        tail = lines[-5:]
        text = "\n".join(head + ["…"] + tail)
        truncated = True
    if len(text) > max_chars:
        text = text[: max_chars - 1] + "…"
        truncated = True
    return text, truncated


def rich_text_to_str(value: Any) -> str:
    try:
        plain = value.plain
        if isinstance(plain, str):
            return plain
    except AttributeError:
        pass
    return str(value)


def single_line(text: str, limit: int = 160) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1] + "…"


def event_preview(record: EventRecord, limit: int = 160) -> str:
    event = record.event
    text = ""
    if isinstance(event, MessageEvent):
        text = message_text(event.to_llm_message())
    elif isinstance(event, SystemPromptEvent):
        truncated, _ = truncate_system_prompt(event.system_prompt.text)
        text = truncated
    elif isinstance(event, ActionEvent):
        text = rich_text_to_str(event.visualize)
    elif isinstance(event, ObservationEvent):
        text = rich_text_to_str(event.visualize)
    elif isinstance(event, AgentErrorEvent):
        text = event.error
    elif isinstance(event, UserRejectObservation):
        text = event.rejection_reason
    elif isinstance(event, PauseEvent):
        text = "User paused the conversation."
    elif isinstance(event, Condensation):
        count = len(event.forgotten_event_ids)
        text = f"Condensed {count} event(s)."
        if event.summary:
            text += f" Summary: {event.summary}"
    elif isinstance(event, CondensationSummaryEvent):
        text = event.summary
    elif isinstance(event, CondensationRequest):
        text = "Condensation requested by the agent."
    elif event is not None:
        text = rich_text_to_str(event.visualize)
    if not text:
        text = str(record.raw.get("error", ""))
    return single_line(text, limit=limit)


def format_timestamp(dt: datetime | None) -> str:
    if dt is None:
        return "—"
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def create_conversation_zip(conversation: ConversationData) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        base_state_path = conversation.path / "base_state.json"
        if base_state_path.exists():
            archive.write(base_state_path, "base_state.json")
        events_dir = conversation.path / "events"
        if events_dir.exists():
            for event_file in sorted(events_dir.glob("*.json")):
                archive.write(event_file, f"events/{event_file.name}")
    buffer.seek(0)
    return buffer.getvalue()


def render_base_state(base_state: dict[str, Any]) -> None:
    st.subheader("Conversation configuration")

    if not base_state:
        st.info("base_state.json not found for this conversation.")
        return

    st.markdown(
        """
        <style>
        .config-card {
            background-color: #f9fafc;
            border-radius: 12px;
            border: 1px solid rgba(0, 0, 0, 0.04);
            padding: 1rem 1.2rem;
            margin-bottom: 1rem;
            display: flex;
            flex-direction: column;
            gap: 0.75rem;
        }
        .config-card__item {
            display: flex;
            flex-direction: column;
            gap: 0.2rem;
        }
        .config-card__label {
            font-size: 0.82rem;
            text-transform: uppercase;
            letter-spacing: 0.06em;
            color: #4b5563;
            font-weight: 600;
        }
        .config-card__value {
            font-size: 0.92rem;
            color: #111827;
            word-break: break-word;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    agent = base_state.get("agent", {})
    llm = agent.get("llm", {})
    temperature = llm.get("temperature")
    items = [
        ("Agent", agent.get("kind", "Unknown")),
        ("LLM model", llm.get("model", "Unknown")),
        (
            "Temperature",
            temperature if temperature is not None else "Unknown",
        ),
    ]

    card_html = ["<div class='config-card'>"]
    for label, value in items:
        card_html.append(
            "<div class='config-card__item'>"
            f"<span class='config-card__label'>{escape(str(label))}</span>"
            f"<span class='config-card__value'>{escape(str(value))}</span>"
            "</div>"
        )
    card_html.append("</div>")
    st.markdown("".join(card_html), unsafe_allow_html=True)

    condenser = base_state.get("conversation", {}).get("condenser")
    if condenser:
        st.markdown("**Condenser configuration**")
        st.json(condenser)

    with st.expander("View raw base_state.json", expanded=False):
        st.json(base_state)


def get_role_style(role: str) -> tuple[str, str]:
    style = ROLE_STYLES.get(role, {"label": role.capitalize(), "color": "#2c3e50"})
    return style["label"], style["color"]


def render_llm_blocks(blocks: Sequence[LLMMessageBlock]) -> None:
    st.subheader("LLM messages and related events")
    if not blocks:
        st.info("No LLM-convertible events found in this conversation.")
        return

    for idx, block in enumerate(blocks, start=1):
        message = block.message
        role_label, role_color = get_role_style(message.role)
        header_parts = [f"{idx:02d}", role_label]
        if message.role == "assistant" and message.tool_calls:
            header_parts.append("tool call")
        if message.role == "tool" and message.name:
            header_parts.append(message.name)
        descriptor = describe_block(block)
        if descriptor and descriptor not in header_parts:
            header_parts.append(descriptor)
        header = " · ".join(header_parts)

        with st.container(border=True):
            render_message_header(header, role_color)
            text = message_text(message)
            if message.role == "system":
                truncated, was_truncated = truncate_system_prompt(text)
                label = (
                    "System prompt (truncated)" if was_truncated else "System prompt"
                )
                render_section_label(label, role_color)
                st.code(truncated or "[empty]")
                if was_truncated:
                    with st.expander("Show full system prompt", expanded=False):
                        st.code(text or "[empty]")
            else:
                body = text or ""
                displayed = body if body.strip() else "[no text content]"
                render_section_label("Message content", role_color)
                if len(displayed) <= MESSAGE_PREVIEW_LIMIT:
                    st.code(displayed)
                else:
                    preview = displayed[: MESSAGE_PREVIEW_LIMIT - 1].rstrip()
                    if not preview.endswith("…"):
                        preview = preview + "…"
                    st.code(preview)
                    with st.expander("Show full content", expanded=False):
                        st.code(displayed)

            if message.reasoning_content:
                render_section_label("Reasoning", role_color)
                st.code(message.reasoning_content)

            if message.thinking_blocks:
                with st.expander(
                    f"Thinking blocks ({len(message.thinking_blocks)})", expanded=False
                ):
                    for block_idx, thinking in enumerate(
                        message.thinking_blocks, start=1
                    ):
                        st.markdown(f"Block {block_idx}")
                        st.code(getattr(thinking, "thinking", str(thinking)))

            if (
                message.responses_reasoning_item
                and message.responses_reasoning_item.summary
            ):
                render_section_label("Responses reasoning summary", role_color)
                st.write(message.responses_reasoning_item.summary)

            if message.tool_calls:
                render_section_label("Tool calls", role_color)
                for tool_call in message.tool_calls:
                    st.markdown(f"- **{tool_call.name}** (`{tool_call.id}`)")
                    try:
                        parsed_args = json.loads(tool_call.arguments)
                    except json.JSONDecodeError:
                        st.code(tool_call.arguments)
                    else:
                        st.json(parsed_args)

            if message.role == "tool":
                details: list[str] = []
                if message.name:
                    details.append(f"Tool `{message.name}`")
                if message.tool_call_id:
                    details.append(f"Call ID `{message.tool_call_id}`")
                if details:
                    st.markdown(" • ".join(details))

            if block.events:
                st.markdown("**Related events**")
                for rec in block.events:
                    label = f"{rec.kind} · {rec.source} · {rec.filename}"
                    with st.expander(label, expanded=False):
                        st.json(rec.raw)


def main() -> None:
    st.title("OpenHands Conversation Dashboard")

    if "root_directory" not in st.session_state:
        st.session_state["root_directory"] = str(DEFAULT_CONVERSATIONS_ROOT)

    st.sidebar.markdown("**Conversation source**")
    root_input_value = st.sidebar.text_input(
        "Conversations directory",
        value=st.session_state["root_directory"],
        help=(
            "Folder containing conversation dumps "
            "(defaults to ~/.openhands/conversations)"
        ),
    )
    cols = st.sidebar.columns([1, 1])
    with cols[0]:
        auto_refresh = st.toggle(
            "Auto refresh",
            value=True,
            help="Periodically reload events while a conversation is running",
        )
    with cols[1]:
        refresh_interval = st.slider(
            "Refresh interval (seconds)",
            min_value=1,
            max_value=30,
            value=5,
            step=1,
        )
    reload_clicked = st.sidebar.button("Reload now")

    root_input = root_input_value or ""

    if root_input != st.session_state["root_directory"]:
        st.session_state["root_directory"] = root_input

    root_path = Path(root_input).expanduser()
    if reload_clicked:
        compute_last_event_timestamp.clear()
        list_conversation_dirs.clear()
        trigger_rerun()
        return

    if not root_path.exists() or not root_path.is_dir():
        st.error(f"Directory not found: {root_path}")
        return

    conversation_paths = [Path(p) for p in list_conversation_dirs(str(root_path))]
    if not conversation_paths:
        st.warning("No conversation folders found in the selected directory.")
        return

    options_with_labels: list[str] = []
    for path in conversation_paths:
        ts_iso = compute_last_event_timestamp(str(path))
        ts_display = ""
        if ts_iso:
            dt = parse_timestamp(ts_iso)
            if dt is not None:
                ts_display = dt.strftime("%Y-%m-%d %H:%M")
        label = path.name if not ts_display else f"{path.name} ({ts_display})"
        options_with_labels.append(label)

    selected_idx = 0
    if "conversation" in st.session_state:
        try:
            selected_idx = [p.name for p in conversation_paths].index(
                st.session_state["conversation"]
            )
        except ValueError:
            selected_idx = 0

    st.sidebar.markdown("**Conversation**")
    selected_label = st.sidebar.selectbox(
        "Conversation (most recent first)",
        options_with_labels,
        index=selected_idx,
    )
    selected_path = conversation_paths[options_with_labels.index(selected_label)]
    st.session_state["conversation"] = selected_path.name

    conversation = load_conversation(str(selected_path))
    llm_blocks = build_llm_message_blocks(conversation.events)

    role_options = sorted({block.message.role for block in llm_blocks})
    default_roles = [role for role in role_options if role != "system"] or role_options

    with st.sidebar:
        st.markdown("Search")
        search_key = f"search_{conversation.identifier}"
        search_default = st.session_state.get(search_key, "")
        if not isinstance(search_default, str):
            search_default = str(search_default)
        search_term = st.text_input(
            "Search",
            value=search_default,
            placeholder="Search event JSON or message text",
            key=search_key,
            label_visibility="collapsed",
            help="Filter events and message content by keyword",
        )

        selected_roles = render_checkbox_filter_group(
            key=f"roles_filter_{conversation.identifier}",
            options=role_options,
            defaults=default_roles,
            columns=2,
        )

    search_lower = search_term.strip().lower()

    filtered_blocks: list[LLMMessageBlock] = []
    for block in llm_blocks:
        if selected_roles and block.message.role not in selected_roles:
            continue
        if search_lower:
            message_text_blob = message_text(block.message).lower()
            related_json = " ".join(
                json.dumps(rec.raw, default=str) for rec in block.events
            ).lower()
            if (
                search_lower not in message_text_blob
                and search_lower not in related_json
            ):
                continue
        filtered_blocks.append(block)

    st.sidebar.download_button(
        label="Download conversation as ZIP",
        data=create_conversation_zip(conversation),
        file_name=f"{conversation.identifier}.zip",
        mime="application/zip",
    )

    st.caption(f"Loaded from {conversation.path}")
    render_base_state(conversation.base_state)
    render_llm_blocks(filtered_blocks)

    if auto_refresh:
        time.sleep(refresh_interval)
        trigger_rerun()


if __name__ == "__main__":
    main()
