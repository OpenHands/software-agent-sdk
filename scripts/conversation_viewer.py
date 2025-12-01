"""Streamlit app to explore OpenHands conversation logs.

Usage:
    streamlit run scripts/conversation_viewer.py

The viewer expects a directory containing conversation folders. By default we
look for ``.conversations`` next to the repository root (the location created by
``openhands`` when recording sessions). You can override the location via:

* Environment variable ``OPENHANDS_CONVERSATIONS_ROOT``
* URL query parameter ``?root=/path/to/logs`` when the app is open
* The sidebar text input labelled "Conversations directory"

Each conversation directory should contain ``base_state.json`` plus an
``events/`` folder with individual ``*.json`` event files. The viewer will
summarise events in a table and show their full payload when expanded.
"""

from __future__ import annotations

import io
import json
import os
import zipfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import streamlit as st


ENV_ROOT = os.getenv("OPENHANDS_CONVERSATIONS_ROOT")
DEFAULT_CONVERSATIONS_ROOT = (
    Path(ENV_ROOT).expanduser()
    if ENV_ROOT
    else Path(__file__).resolve().parents[1] / ".conversations"
)

st.set_page_config(page_title="OpenHands Agent-SDK Conversation Viewer", layout="wide")


@dataclass
class Conversation:
    identifier: str
    path: Path
    base_state: dict[str, Any]
    events: list[dict[str, Any]]


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def add_filename(event: dict[str, Any], filename: str) -> dict[str, Any]:
    event_copy = dict(event)
    event_copy["_filename"] = filename
    return event_copy


@st.cache_data(show_spinner=False)
def load_conversation(path_str: str) -> Conversation:
    path = Path(path_str)
    identifier = path.name

    base_state: dict[str, Any] = {}
    base_state_path = path / "base_state.json"
    if base_state_path.exists():
        try:
            base_state = load_json(base_state_path)
        except json.JSONDecodeError as exc:
            base_state = {"error": f"Failed to parse base_state.json: {exc}"}

    events_dir = path / "events"
    events: list[dict[str, Any]] = []
    if events_dir.exists():
        for event_file in sorted(events_dir.glob("*.json")):
            try:
                event_data = load_json(event_file)
                events.append(add_filename(event_data, event_file.name))
            except json.JSONDecodeError as exc:
                events.append(
                    {
                        "kind": "InvalidJSON",
                        "source": "parser",
                        "timestamp": "",
                        "error": str(exc),
                        "_filename": event_file.name,
                    }
                )

    return Conversation(
        identifier=identifier, path=path, base_state=base_state, events=events
    )


@st.cache_data(show_spinner=False)
def get_last_event_timestamp(conversation_path_str: str) -> str:
    """Get the timestamp of the most recent event in a conversation directory.

    Returns empty string if no events found or if timestamps can't be parsed.
    """
    conversation_path = Path(conversation_path_str)
    events_dir = conversation_path / "events"

    if not events_dir.exists():
        return ""

    latest_timestamp = ""
    latest_datetime = None

    for event_file in events_dir.glob("*.json"):
        try:
            event_data = load_json(event_file)
            timestamp = event_data.get("timestamp", "")
            if timestamp:
                # Try to parse the timestamp to compare properly
                try:
                    # Handle various timestamp formats
                    if "T" in timestamp:
                        # ISO format with T separator
                        dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                    else:
                        # Try other common formats
                        dt = datetime.fromisoformat(timestamp)

                    if latest_datetime is None or dt > latest_datetime:
                        latest_datetime = dt
                        latest_timestamp = timestamp
                except (ValueError, TypeError):
                    # If we can't parse the timestamp, fall back to string comparison
                    if timestamp > latest_timestamp:
                        latest_timestamp = timestamp
        except (json.JSONDecodeError, OSError):
            # Skip files that can't be read or parsed
            continue

    return latest_timestamp


def conversation_dirs(root: Path) -> list[Path]:
    """Return conversation sub-directories under ``root``.

    Sorted by last event timestamp (most recent first).
    """
    dirs = [p for p in root.iterdir() if p.is_dir()]

    # Sort by last event timestamp (most recent first), fall back to directory name
    def sort_key(path: Path) -> tuple[str, str]:
        timestamp = get_last_event_timestamp(str(path))
        # Reverse timestamp for descending order (most recent first)
        # Use empty string as fallback which will sort last
        return (timestamp or "", path.name)

    return sorted(dirs, key=sort_key, reverse=True)


def extract_text_blocks(blocks: Iterable[Any] | None) -> str:
    pieces: list[str] = []
    for block in blocks or []:
        if isinstance(block, dict):
            block_type = block.get("type")
            if block_type == "text":
                pieces.append(str(block.get("text", "")))
            elif "text" in block:
                pieces.append(str(block.get("text")))
            elif "content" in block:
                pieces.append(extract_text_blocks(block.get("content")))
        elif isinstance(block, str):
            pieces.append(block)
    return "\n".join(piece for piece in pieces if piece)


def get_event_text(event: dict[str, Any]) -> str:
    kind = event.get("kind")
    if kind == "MessageEvent":
        message = event.get("llm_message", {})
        return extract_text_blocks(message.get("content", []))
    if kind == "ActionEvent":
        segments: list[str] = []
        segments.append(extract_text_blocks(event.get("thought", [])))
        action = event.get("action", {})
        if isinstance(action, dict):
            if action.get("command"):
                segments.append(str(action.get("command")))
            if action.get("path"):
                segments.append(f"Path: {action.get('path')}")
            if action.get("file_text"):
                segments.append(action.get("file_text", ""))
        return "\n\n".join(s for s in segments if s)
    if kind == "ObservationEvent":
        observation = event.get("observation", {})
        return extract_text_blocks(observation.get("content", []))
    if kind == "SystemPromptEvent":
        prompt = event.get("system_prompt", {})
        if isinstance(prompt, dict) and prompt.get("type") == "text":
            return str(prompt.get("text", ""))
    return ""


def truncate(text: str, limit: int = 160) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1] + "\u2026"


def event_summary_rows(events: Sequence[dict[str, Any]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for idx, event in enumerate(events):
        kind = event.get("kind", "")
        source = event.get("source", "")
        preview = (
            truncate(get_event_text(event))
            if kind != "InvalidJSON"
            else event.get("error", "")
        )
        rows.append(
            {
                "#": f"{idx:03d}",
                "File": event.get("_filename", ""),
                "Kind": kind,
                "Source": source,
                "Timestamp": event.get("timestamp", ""),
                "Preview": preview,
            }
        )
    return rows


def draw_base_state(base_state: dict[str, Any]) -> None:
    if not base_state:
        st.info("No base_state.json found for this conversation.")
        return

    st.subheader("Base State")
    cols = st.columns(3)
    agent = base_state.get("agent", {})
    llm = agent.get("llm", {})
    cols[0].metric("Agent kind", agent.get("kind", "Unknown"))
    cols[1].metric("LLM model", llm.get("model", "Unknown"))
    cols[2].metric("Temperature", str(llm.get("temperature", "Unknown")))

    with st.expander("View raw base_state.json", expanded=False):
        st.json(base_state)


def create_conversation_zip(conversation_path: Path) -> bytes:
    """Create a zip file containing all files from the conversation directory.

    Args:
        conversation_path: Path to the conversation directory

    Returns:
        Bytes of the zip file
    """
    buffer = io.BytesIO()

    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        # Add base_state.json if it exists
        base_state_path = conversation_path / "base_state.json"
        if base_state_path.exists():
            zip_file.write(base_state_path, "base_state.json")

        # Add all event files from the events directory
        events_dir = conversation_path / "events"
        if events_dir.exists():
            for event_file in sorted(events_dir.glob("*.json")):
                arcname = f"events/{event_file.name}"
                zip_file.write(event_file, arcname)

    buffer.seek(0)
    return buffer.getvalue()


def draw_event_detail(event: dict[str, Any]) -> None:
    meta_cols = st.columns(4)
    meta_cols[0].markdown(f"**File**\n{event.get('_filename', '—')}")
    meta_cols[1].markdown(f"**Kind**\n{event.get('kind', '—')}")
    meta_cols[2].markdown(f"**Source**\n{event.get('source', '—')}")
    meta_cols[3].markdown(f"**Timestamp**\n{event.get('timestamp', '—')}")

    text = get_event_text(event)
    if text:
        st.markdown("**Narrative**")
        st.code(text)

    if event.get("kind") == "ActionEvent" and event.get("action"):
        st.markdown("**Action Payload**")
        st.json(event.get("action"))

    if event.get("kind") == "ObservationEvent" and event.get("observation"):
        st.markdown("**Observation Payload**")
        st.json(event.get("observation"))

    st.markdown("**Raw Event JSON**")
    st.json(event)


def main() -> None:
    st.title("OpenHands Conversation Viewer")

    # Initialize root directory in session state if not present
    if "root_directory" not in st.session_state:
        params = st.query_params
        default_root = DEFAULT_CONVERSATIONS_ROOT
        # Handle both old (list) and new (string) query param formats
        root_from_params = params.get("root", str(default_root))
        if isinstance(root_from_params, list):
            root_from_params = (
                root_from_params[0] if root_from_params else str(default_root)
            )
        st.session_state["root_directory"] = root_from_params

    root_input = st.sidebar.text_input(
        "Conversations directory",
        value=st.session_state["root_directory"],
        help="Root folder containing OpenHands conversation dumps",
    )

    # Ensure root_input is not None (should not happen with default value)
    if not root_input:
        root_input = st.session_state["root_directory"]

    # Update session state if root input changed
    if root_input != st.session_state["root_directory"]:
        st.session_state["root_directory"] = root_input
        if not st.session_state.get("_suppress_query_update", False):
            try:
                st.session_state["_suppress_query_update"] = True
                st.query_params["root"] = root_input
            finally:
                st.session_state["_suppress_query_update"] = False

    root_path = Path(root_input).expanduser()

    if st.sidebar.button(
        "Reload conversations", help="Clear cached data and reload from disk"
    ):
        load_conversation.clear()
        get_last_event_timestamp.clear()
        rerun = getattr(st, "experimental_rerun", None)
        if callable(rerun):
            rerun()
        else:
            st.rerun()

    if not root_path.exists() or not root_path.is_dir():
        st.error(f"Directory not found: {root_path}")
        return

    directories = conversation_dirs(root_path)
    if not directories:
        st.warning("No conversation folders found in the selected directory.")
        return

    # Create options with timestamps for better UX
    options_with_timestamps = []
    options = []
    for directory in directories:
        timestamp = get_last_event_timestamp(str(directory))
        if timestamp:
            # Format timestamp for display
            try:
                if "T" in timestamp:
                    dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                    formatted_time = dt.strftime("%Y-%m-%d %H:%M")
                else:
                    formatted_time = timestamp[:16]  # Truncate if too long
                display_name = f"{directory.name} ({formatted_time})"
            except (ValueError, TypeError):
                display_name = f"{directory.name} ({timestamp[:16]})"
        else:
            display_name = f"{directory.name} (no events)"

        options_with_timestamps.append(display_name)
        options.append(directory.name)

    selected_idx = 0
    if "conversation" in st.session_state:
        try:
            selected_idx = options.index(st.session_state["conversation"])
        except ValueError:
            selected_idx = 0

    selected_display = st.sidebar.selectbox(
        "Conversation (sorted by last event)",
        options_with_timestamps,
        index=selected_idx,
        help="Conversations are sorted by their most recent event timestamp",
    )
    selected = options[options_with_timestamps.index(selected_display)]
    st.session_state["conversation"] = selected

    conversation = load_conversation(str(root_path / selected))

    # Add download button for the conversation
    st.sidebar.divider()
    zip_data = create_conversation_zip(conversation.path)
    st.sidebar.download_button(
        label="📥 Download Conversation as ZIP",
        data=zip_data,
        file_name=f"{selected}.zip",
        mime="application/zip",
        help="Download all conversation files as a ZIP archive",
    )

    st.caption(f"Loaded from {conversation.path}")
    draw_base_state(conversation.base_state)

    st.subheader("Events")
    events = conversation.events
    if not events:
        st.info("No events found for this conversation.")
        return

    kinds = sorted({event.get("kind", "Unknown") for event in events})
    selected_kinds = st.sidebar.multiselect(
        "Filter by event kind", kinds, default=kinds
    )

    search_term = st.sidebar.text_input("Search across events", value="")
    lowered = search_term.lower()

    filtered_events: list[dict[str, Any]] = []
    for event in events:
        if selected_kinds and event.get("kind", "Unknown") not in selected_kinds:
            continue
        if lowered:
            as_text = json.dumps(event).lower()
            if lowered not in as_text:
                continue
        filtered_events.append(event)

    st.markdown(f"Showing {len(filtered_events)} of {len(events)} events")

    summary = event_summary_rows(filtered_events)
    st.dataframe(summary, use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("Event Details")

    for idx, event in enumerate(filtered_events):
        label = " · ".join(
            [
                f"{idx:03d}",
                event.get("kind", "Unknown"),
                event.get("source", "Unknown"),
            ]
        )
        with st.expander(label, expanded=False):
            draw_event_detail(event)


if __name__ == "__main__":
    main()
