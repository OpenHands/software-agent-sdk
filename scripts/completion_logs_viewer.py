"""Streamlit app to explore OpenHands completion logs.

Usage:
    streamlit run scripts/completion_logs_viewer.py

The viewer expects a directory containing run folders with ``*.json`` log
files (e.g. ``output/Agent/logs/<run>/log.json``). You can override the logs
directory via:

* Environment variable ``OPENHANDS_COMPLETION_LOGS_ROOT``
* URL query parameter ``?root=/path/to/logs`` when the app is open
* The sidebar text input labelled "Logs directory"
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import streamlit as st

from openhands.sdk.logger import ENV_LOG_DIR


ENV_ROOT = os.getenv("OPENHANDS_COMPLETION_LOGS_ROOT")
DEFAULT_LOG_ROOT = Path(os.path.join(ENV_LOG_DIR, "completion_logs"))

st.set_page_config(page_title="OpenHands Completion Logs Viewer", layout="wide")


def format_timestamp(timestamp: float) -> str:
    try:
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
    except (OSError, OverflowError, ValueError):
        return ""


def render_message(msg: dict[str, Any]) -> None:
    msg_type = msg.get("type") or msg.get("role")
    if msg_type == "message":
        role = msg.get("role", "user")
        st.markdown(f"**{role}**")
        for chunk in msg.get("content", []):
            if isinstance(chunk, dict) and chunk.get("text"):
                st.write(chunk["text"])
    elif msg_type == "function_call":
        args = msg.get("arguments", "")
        preview = (args[:80] + "...") if len(args) > 80 else args
        st.markdown(f"**Tool Call:** `{msg.get('name')}` - {preview}")
        st.code(msg.get("arguments"), language="json")
    elif msg_type == "function_call_output":
        st.markdown("**Tool Output**")
        st.code(msg.get("output", ""), language="text")
    elif msg_type == "reasoning":
        st.markdown("**Reasoning**")
        if msg.get("summary"):
            st.write(msg["summary"])
        elif msg.get("encrypted_content"):
            st.text("(encrypted content)")
    else:
        st.write(msg)


def render_response(resp: dict[str, Any]) -> None:
    st.subheader("Response")
    message = resp.get("message", {})
    if message:
        st.markdown(f"**role:** {message.get('role')}")
        for chunk in message.get("content", []):
            if isinstance(chunk, dict) and chunk.get("text"):
                st.write(chunk["text"])
    tool_calls = resp.get("tool_calls") or []
    for tc in tool_calls:
        with st.expander(f"Tool call: {tc.get('function', {}).get('name')}"):
            st.code(json.dumps(tc, indent=2), language="json")


@st.cache_data(show_spinner=False)
def load_json(path_str: str) -> dict[str, Any]:
    path = Path(path_str)
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        return {"_error": f"Failed to parse {path}: {exc}"}
    except OSError as exc:
        return {"_error": f"Failed to read {path}: {exc}"}


def list_runs(root: Path) -> list[Path]:
    if not root.exists() or not root.is_dir():
        return []
    return sorted(
        [p for p in root.iterdir() if p.is_dir()],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )


def list_log_files(run_dir: Path) -> list[Path]:
    if not run_dir.exists() or not run_dir.is_dir():
        return []
    return sorted(
        run_dir.glob("*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )


def main() -> None:
    st.title("OpenHands Completion Logs Viewer")

    if "logs_root" not in st.session_state:
        params = st.query_params
        default_root = DEFAULT_LOG_ROOT
        root_from_params = params.get("root", str(default_root))
        if isinstance(root_from_params, list):
            root_from_params = (
                root_from_params[0] if root_from_params else str(default_root)
            )
        st.session_state["logs_root"] = root_from_params

    root_input = st.sidebar.text_input(
        "Logs directory",
        value=st.session_state["logs_root"],
        help="Root folder containing OpenHands completion logs",
    )

    if not root_input:
        root_input = st.session_state["logs_root"]

    if root_input != st.session_state["logs_root"]:
        st.session_state["logs_root"] = root_input
        if not st.session_state.get("_suppress_query_update", False):
            try:
                st.session_state["_suppress_query_update"] = True
                st.query_params["root"] = root_input
            finally:
                st.session_state["_suppress_query_update"] = False

    root_path = Path(root_input).expanduser()

    if st.sidebar.button("Reload logs", help="Clear cached data and reload from disk"):
        load_json.clear()
        rerun = getattr(st, "experimental_rerun", None)
        if callable(rerun):
            rerun()
        else:
            st.rerun()

    if not root_path.exists() or not root_path.is_dir():
        st.error(f"Directory not found: {root_path}")
        return

    runs = list_runs(root_path)
    if not runs:
        st.warning("No run directories found in the selected path.")
        return

    run_options = [f"{p.name} ({format_timestamp(p.stat().st_mtime)})" for p in runs]
    run_names = [p.name for p in runs]
    selected_run_idx = 0
    if "selected_run" in st.session_state:
        try:
            selected_run_idx = run_names.index(st.session_state["selected_run"])
        except ValueError:
            selected_run_idx = 0

    selected_run_display = st.sidebar.selectbox(
        "Run (sorted by mtime)",
        run_options,
        index=selected_run_idx,
        help="Most recently modified run appears first",
    )
    selected_run_name = run_names[run_options.index(selected_run_display)]
    st.session_state["selected_run"] = selected_run_name
    selected_run_path = root_path / selected_run_name

    log_files = list_log_files(selected_run_path)
    if not log_files:
        st.info("No log files in this run.")
        return

    log_options = [
        f"{p.name} ({format_timestamp(p.stat().st_mtime)})" for p in log_files
    ]
    log_names = [p.name for p in log_files]
    selected_log_idx = 0
    if "selected_log" in st.session_state:
        try:
            selected_log_idx = log_names.index(st.session_state["selected_log"])
        except ValueError:
            selected_log_idx = 0

    selected_log_display = st.sidebar.selectbox(
        "Log file (sorted by mtime)",
        log_options,
        index=selected_log_idx,
    )
    selected_log_name = log_names[log_options.index(selected_log_display)]
    st.session_state["selected_log"] = selected_log_name
    log_path = selected_run_path / selected_log_name

    data = load_json(str(log_path))
    if not data:
        st.error(f"Failed to load {log_path}")
        return
    if data.get("_error"):
        st.error(data["_error"])
        return

    st.caption(f"Loaded from {log_path}")

    st.subheader("Metadata")
    cols = st.columns(4)
    cols[0].metric("Model", data.get("llm_path", ""))
    cols[1].metric("Latency (s)", f"{data.get('latency_sec', 0):.2f}")
    cols[2].metric("Cost", data.get("cost", ""))
    cols[3].metric("Timestamp", data.get("timestamp", ""))

    st.subheader("Input")
    for idx, msg in enumerate(data.get("input", [])):
        msg_type = msg.get("type", msg.get("role", "message"))
        label = f"{idx:02d} - {msg_type}"
        if msg_type == "function_call":
            name = msg.get("name", "")
            label = f"{label} - {name}".strip()
        with st.expander(label, expanded=False):
            render_message(msg)

    if data.get("response"):
        render_response(data["response"])

    if usage := data.get("usage_summary"):
        with st.expander("Usage summary"):
            st.json(usage)

    with st.expander("Raw log JSON", expanded=False):
        st.json(data)


if __name__ == "__main__":
    main()
