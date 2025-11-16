"""Streamlit dashboard focusing on LLM request/response turns.

This companion viewer complements ``03_conversation_dashboard.py`` by
highlighting each raw LLM prompt/response pair alongside the resulting
Agent SDK events (actions, observations, assistant messages).

Run with:
    streamlit run examples/04_llm_turn_dashboard.py
"""

from __future__ import annotations

import importlib
import io
import json
import pkgutil
import time
import zipfile
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from html import escape
from pathlib import Path
from typing import Any

import streamlit as st
from pydantic import ValidationError

from openhands.sdk.event import (
    ActionEvent,
    Condensation,
    Event,
    MessageEvent,
    ObservationEvent,
)
from openhands.sdk.llm import Message, content_to_str


DEFAULT_CONVERSATIONS_ROOT = Path("~/.openhands/conversations").expanduser()
LOG_ROOT = Path("logs")

st.set_page_config(
    page_title="OpenHands LLM Turn Explorer",
    layout="wide",
)

_TOOL_MODULES_LOADED = False

ROLE_STYLES: dict[str, dict[str, str]] = {
    "assistant": {"label": "LLM", "color": "#1f6feb"},
    "user": {"label": "User", "color": "#8e44ad"},
    "tool": {"label": "Tool", "color": "#7f8c8d"},
    "system": {"label": "System", "color": "#2c3e50"},
}
MESSAGE_PREVIEW_LIMIT = 200


def ensure_tool_modules_loaded() -> None:
    """Ensure tool modules are imported so Event.model_validate works."""

    global _TOOL_MODULES_LOADED
    if _TOOL_MODULES_LOADED:
        return

    try:
        import openhands.tools as tools_pkg
    except Exception as exc:  # pragma: no cover - defensive import guard
        st.sidebar.warning(f"Failed loading tool modules: {exc}")
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
        except Exception:  # pragma: no cover - log to sidebar for awareness
            st.sidebar.warning(f"Could not import {name}")

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


@dataclass(frozen=True)
class EventRecord:
    identifier: str
    path: Path
    event: Event | None
    raw: dict[str, Any]
    timestamp: datetime | None


@dataclass(frozen=True)
class ConversationData:
    identifier: str
    path: Path
    base_state: dict[str, Any]
    events: list[EventRecord]


@dataclass(frozen=True)
class LLMLogEntry:
    response_id: str
    model: str
    timestamp: float | None
    created_at: datetime | None
    latency_sec: float | None
    request: dict[str, Any]
    response: dict[str, Any]
    conversation_id: str | None = None
    conversation_path: str | None = None


@dataclass(frozen=True)
class LLMTurn:
    index: int
    response_id: str
    log: LLMLogEntry | None
    actions: list[EventRecord]
    observations: dict[str, list[EventRecord]]
    assistant_messages: list[EventRecord]
    extra_events: list[EventRecord]


@lru_cache(maxsize=1)
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


@lru_cache(maxsize=256)
def compute_last_event_timestamp(path_str: str) -> str | None:
    path = Path(path_str)
    events_dir = path / "events"
    if not events_dir.exists():
        return None

    latest: datetime | None = None
    for event_file in events_dir.glob("*.json"):
        try:
            data = json.loads(event_file.read_text())
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


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value)
    except Exception:
        return None


def load_conversation(path_str: str) -> ConversationData:
    path = Path(path_str)
    identifier = path.name

    base_state: dict[str, Any] = {}
    base_state_path = path / "base_state.json"
    if base_state_path.exists():
        try:
            base_state = json.loads(base_state_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            base_state = {"error": f"Failed to parse base_state.json: {exc}"}

    events_dir = path / "events"
    records: list[EventRecord] = []
    if events_dir.exists():
        for event_file in sorted(events_dir.glob("*.json")):
            raw: dict[str, Any]
            model: Event | None
            try:
                raw = json.loads(event_file.read_text())
            except json.JSONDecodeError as exc:
                raw = {
                    "kind": "InvalidJSON",
                    "error": f"Failed to parse {event_file.name}: {exc}",
                    "timestamp": None,
                }
                model = None
            except OSError as exc:
                raw = {
                    "kind": "IOError",
                    "error": f"Failed to read {event_file.name}: {exc}",
                    "timestamp": None,
                }
                model = None
            else:
                try:
                    model = Event.model_validate(raw)
                except ValidationError as exc:
                    raw = dict(raw)
                    raw["_validation_error"] = exc.errors()
                    model = None
            ts = parse_timestamp(raw.get("timestamp"))
            records.append(
                EventRecord(
                    identifier=event_file.stem,
                    path=event_file,
                    event=model,
                    raw=raw,
                    timestamp=ts,
                )
            )

    return ConversationData(
        identifier=identifier,
        path=path,
        base_state=base_state,
        events=records,
    )


def load_llm_logs() -> tuple[dict[str, LLMLogEntry], dict[str, str]]:
    """Return mapping from response_id -> log entry and reasoning_id -> response_id."""

    response_logs: dict[str, LLMLogEntry] = {}
    reasoning_map: dict[str, str] = {}

    if not LOG_ROOT.exists():
        return response_logs, reasoning_map

    for path in LOG_ROOT.rglob("*.json"):
        try:
            data = json.loads(path.read_text())
        except Exception:
            continue

        if not isinstance(data, dict):
            continue

        resp = data.get("response")
        if not isinstance(resp, dict):
            continue

        response_id = resp.get("id")
        if not isinstance(response_id, str):
            continue

        created_at = resp.get("created_at")
        created_dt = None
        if isinstance(created_at, str):
            created_dt = parse_timestamp(created_at)
        elif isinstance(created_at, (int, float)):
            created_dt = datetime.fromtimestamp(created_at)

        conversation_id = data.get("conversation_id")
        conversation_path = data.get("conversation_path")

        log_entry = LLMLogEntry(
            response_id=response_id,
            model=str(resp.get("model", "unknown")),
            timestamp=data.get("timestamp"),
            created_at=created_dt,
            latency_sec=data.get("latency_sec"),
            request=data.get("request", {}),
            response=resp,
            conversation_id=conversation_id,
            conversation_path=conversation_path,
        )
        response_logs[response_id] = log_entry

        for item in resp.get("output", []) or []:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "reasoning" and isinstance(item.get("id"), str):
                reasoning_map[item["id"]] = response_id

    return response_logs, reasoning_map


def get_role_style(role: str) -> tuple[str, str]:
    style = ROLE_STYLES.get(role, {"label": role.capitalize(), "color": "#2c3e50"})
    return style["label"], style["color"]


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


def extract_text_chunks(parts: Iterable[dict[str, Any]]) -> str:
    chunks: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        if part.get("type") in {"input_text", "output_text", "text"}:
            text = part.get("text")
            if isinstance(text, str):
                chunks.append(text)
        elif part.get("type") == "input_image":
            chunks.append("[image input]")
    return "\n".join(chunks)


def build_llm_turns(
    conversation: ConversationData,
    response_logs: dict[str, LLMLogEntry],
    reasoning_map: dict[str, str],
) -> list[LLMTurn]:
    actions_by_response: dict[str, list[EventRecord]] = defaultdict(list)
    observations_by_action: dict[str, list[EventRecord]] = defaultdict(list)
    agent_messages: list[EventRecord] = []
    extra_events: list[EventRecord] = []

    for record in conversation.events:
        event = record.event
        if isinstance(event, ActionEvent):
            actions_by_response[event.llm_response_id].append(record)
        elif isinstance(event, ObservationEvent):
            observations_by_action[event.action_id].append(record)
        elif isinstance(event, MessageEvent) and event.source == "agent":
            agent_messages.append(record)
        elif isinstance(event, Condensation):
            extra_events.append(record)

    # Map message reasoning IDs to response ids via logs
    message_response_map: dict[str, str] = {}
    for record in agent_messages:
        event = record.event
        assert isinstance(event, MessageEvent)
        reasoning = getattr(event, "responses_reasoning_item", None)
        if reasoning and isinstance(reasoning.id, str):
            resp_id = reasoning_map.get(reasoning.id)
            if resp_id:
                message_response_map[record.identifier] = resp_id

    # Determine chronological order of response ids for this conversation
    candidate_ids: set[str] = set(actions_by_response.keys())
    candidate_ids.update(message_response_map.values())
    turns: list[tuple[datetime | float | None, str]] = []
    for resp_id in candidate_ids:
        log = response_logs.get(resp_id)
        if log and log.created_at is not None:
            turns.append((log.created_at, resp_id))
        elif log and log.timestamp is not None:
            turns.append((log.timestamp, resp_id))
        else:
            # fall back to earliest associated event timestamp
            ts_candidates: list[datetime] = []
            for record in actions_by_response.get(resp_id, []):
                if record.timestamp:
                    ts_candidates.append(record.timestamp)
            for record_id, mapped_id in message_response_map.items():
                if mapped_id == resp_id:
                    event_rec = next(
                        (r for r in agent_messages if r.identifier == record_id),
                        None,
                    )
                    if event_rec and event_rec.timestamp:
                        ts_candidates.append(event_rec.timestamp)
            ts = min(ts_candidates) if ts_candidates else None
            turns.append((ts, resp_id))

    turns.sort(key=lambda item: (item[0] or datetime.min))

    llm_turns: list[LLMTurn] = []
    for idx, (_, resp_id) in enumerate(turns, start=1):
        log_entry = response_logs.get(resp_id)
        # Gather assistant messages belonging to this response
        assistant_records: list[EventRecord] = []
        for record in agent_messages:
            mapped = message_response_map.get(record.identifier)
            if mapped == resp_id:
                assistant_records.append(record)
        actions = actions_by_response.get(resp_id, [])
        obs_map: dict[str, list[EventRecord]] = {
            action.event.id: observations_by_action.get(action.event.id, [])
            for action in actions
            if isinstance(action.event, ActionEvent)
        }
        llm_turns.append(
            LLMTurn(
                index=idx,
                response_id=resp_id,
                log=log_entry,
                actions=actions,
                observations=obs_map,
                assistant_messages=assistant_records,
                extra_events=[
                    ev for ev in extra_events if _event_belongs_to_response(ev)
                ],
            )
        )

    return llm_turns


def _event_belongs_to_response(record: EventRecord) -> bool:
    event = record.event
    if isinstance(event, Condensation):
        return False
    return False


def message_text(message: Message) -> str:
    parts = [part.strip() for part in content_to_str(message.content) if part.strip()]
    return "\n\n".join(parts)


def render_log_prompt(turn: LLMTurn) -> None:
    log = turn.log
    if not log:
        st.info("No LLM log found for this response ID.")
        return

    request = log.request

    instructions = request.get("instructions")
    if isinstance(instructions, str) and instructions.strip():
        with st.expander("System instructions", expanded=False):
            st.code(instructions)

    inputs = request.get("inputs") or []
    if isinstance(inputs, list) and inputs:
        for idx, item in enumerate(inputs, start=1):
            if not isinstance(item, dict):
                continue
            if item.get("type") == "message":
                role = item.get("role", "user")
                content = extract_text_chunks(item.get("content") or [])
                st.markdown(f"**{role.capitalize()} #{idx}**")
                st.code(content or "[empty]")
            else:
                st.markdown(f"`{json.dumps(item)}`")

    tools = request.get("tools")
    if isinstance(tools, list) and tools:
        with st.expander("Tool definitions", expanded=False):
            st.json(tools)


def render_log_response(turn: LLMTurn) -> None:
    log = turn.log
    if not log:
        st.info("No LLM log available for this response ID.")
        return

    response = log.response

    usage = response.get("usage") or log.response.get("usage")
    metrics = []
    if isinstance(usage, dict):
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")
        if prompt_tokens is not None:
            metrics.append(f"Prompt tokens: {prompt_tokens}")
        if completion_tokens is not None:
            metrics.append(f"Completion tokens: {completion_tokens}")
    if log.latency_sec is not None:
        metrics.append(f"Latency: {log.latency_sec:.2f}s")
    if metrics:
        st.caption(" · ".join(metrics))

    outputs = response.get("output") or []
    if not isinstance(outputs, list) or not outputs:
        st.info("No response output payload recorded.")
        return

    for item in outputs:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type", "unknown")
        if item_type == "reasoning":
            render_section_label("Reasoning", "#1f6feb")
            summary = item.get("summary")
            if summary:
                st.write("\n".join(summary))
            content = item.get("content")
            if content:
                with st.expander("Reasoning detail", expanded=False):
                    st.write("\n".join(content))
            if item.get("encrypted_content"):
                st.info("Encrypted reasoning payload present (not displayed).")
        elif item_type == "message":
            render_section_label("Assistant message", "#1f6feb")
            content = extract_text_chunks(item.get("content") or [])
            st.code(content or "[empty]")
        elif item_type == "function_call":
            render_section_label("Tool call", "#1f6feb")
            st.code(
                json.dumps(
                    {
                        "id": item.get("id"),
                        "name": item.get("name"),
                        "arguments": item.get("arguments"),
                        "status": item.get("status"),
                    },
                    indent=2,
                    ensure_ascii=False,
                )
            )
        elif item_type == "tool_result":
            render_section_label("Tool result", "#1f6feb")
            st.code(json.dumps(item, indent=2, ensure_ascii=False))
        else:
            with st.expander(f"Response item ({item_type})", expanded=False):
                st.json(item)


def render_turn(turn: LLMTurn) -> None:
    title = f"{turn.index:02d} · LLM"
    if turn.log:
        title += f" · {turn.log.model}"
    render_message_header(title, "#1f6feb")

    cols = st.columns(2)
    with cols[0]:
        st.markdown("### Prompt")
        render_log_prompt(turn)
    with cols[1]:
        st.markdown("### Response")
        render_log_response(turn)

    if turn.actions:
        st.markdown("### Agent actions")
        for record in turn.actions:
            event = record.event
            assert isinstance(event, ActionEvent)
            with st.expander(
                f"Action: {event.tool_name} ({record.identifier})",
                expanded=False,
            ):
                st.json(record.raw)
                observations = turn.observations.get(event.id, [])
                for obs in observations:
                    with st.expander(f"Observation {obs.identifier}", expanded=False):
                        st.json(obs.raw)

    if turn.assistant_messages:
        st.markdown("### Assistant messages")
        for record in turn.assistant_messages:
            event = record.event
            assert isinstance(event, MessageEvent)
            text = message_text(event.llm_message)
            st.code(text or "[no text content]")


def main() -> None:
    st.title("LLM Turn Explorer")

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
        compute_last_event_timestamp.cache_clear()
        list_conversation_dirs.cache_clear()
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
    response_logs, reasoning_map = load_llm_logs()
    llm_turns = build_llm_turns(conversation, response_logs, reasoning_map)

    search_term = st.sidebar.text_input(
        "Search turns",
        value="",
        placeholder="Filter by prompt/response text",
    )
    search_lower = search_term.strip().lower()

    if not llm_turns:
        st.info("No LLM responses matched this conversation.")
        return

    st.caption(f"Loaded from {conversation.path}")
    render_base_state(conversation.base_state)

    for turn in llm_turns:
        if search_lower:
            haystacks: list[str] = []
            if turn.log:
                request = json.dumps(turn.log.request, default=str).lower()
                response = json.dumps(turn.log.response, default=str).lower()
                haystacks.extend([request, response])
            for record in turn.actions:
                haystacks.append(json.dumps(record.raw, default=str).lower())
            for record in turn.assistant_messages:
                haystacks.append(json.dumps(record.raw, default=str).lower())
            if all(search_lower not in h for h in haystacks):
                continue
        render_turn(turn)

    st.sidebar.download_button(
        label="Download conversation as ZIP",
        data=create_conversation_zip(conversation),
        file_name=f"{conversation.identifier}.zip",
        mime="application/zip",
    )

    if auto_refresh:
        time.sleep(refresh_interval)
        trigger_rerun()


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
        with st.expander("Condenser configuration", expanded=False):
            st.json(condenser)


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


if __name__ == "__main__":
    main()
