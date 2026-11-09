"""Shared fixtures for cross package tests."""

import json
import shutil
import threading
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
import uvicorn

from openhands.agent_server.__main__ import preload_modules
from openhands.workspace.docker.workspace import find_available_tcp_port


@pytest.fixture
def write_verification_family(tmp_path, monkeypatch):
    feature_map = tmp_path / "map"
    feature_map.mkdir()

    def write(module, content):
        monkeypatch.setattr(module, "FEATURE_MAP_DIR", feature_map)
        path = feature_map / "F98-demo.md"
        path.write_text(content)
        return path

    return write


@pytest.fixture
def llm_fixtures_dir():
    """Get the LLM fixtures directory path."""
    return Path(__file__).parent.parent / "fixtures" / "llm_data"


@pytest.fixture
def fncall_raw_logs(llm_fixtures_dir):
    """Load function calling raw logs from real data."""
    logs = []
    log_dir = llm_fixtures_dir / "llm-logs"
    if log_dir.exists():
        for log_file in log_dir.glob("*.json"):
            with open(log_file) as f:
                logs.append(json.load(f))
    return logs


@pytest.fixture
def nonfncall_raw_logs(llm_fixtures_dir):
    """Load non-function calling raw logs from real data."""
    logs = []
    log_dir = llm_fixtures_dir / "nonfncall-llm-logs"
    if log_dir.exists():
        for log_file in log_dir.glob("*.json"):
            with open(log_file) as f:
                logs.append(json.load(f))
    return logs


@pytest.fixture
def run_rest_api_breakage_check(monkeypatch):
    """Run the check's exit policy with supplied schemas and oasdiff output."""

    def run(module, previous_schema, current_schema, changes):
        monkeypatch.setattr(
            module, "_read_version_from_pyproject", lambda _path: "1.47.0"
        )
        monkeypatch.setattr(
            module, "_get_baseline_version", lambda _distribution, _current: "1.47.0"
        )
        monkeypatch.setattr(
            module, "_find_sdk_deprecated_fastapi_routes", lambda _root: []
        )
        monkeypatch.setattr(module, "_generate_current_openapi", lambda: current_schema)
        monkeypatch.setattr(
            module, "_generate_openapi_for_git_ref", lambda _ref: previous_schema
        )
        monkeypatch.setattr(
            module, "_run_oasdiff_breakage_check", lambda _prev, _cur: (changes, 0)
        )
        return module.main()

    return run


@contextmanager
def live_server_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    import_modules: str | None = None,
    session_api_keys: list[str] | None = None,
    deferred_init: bool = False,
    config_overrides: dict[str, object] | None = None,
) -> Generator[dict]:
    """Launch a real FastAPI server backed by temp workspace and conversations.

    We set OPENHANDS_AGENT_SERVER_CONFIG_PATH before creating the app so that
    routers pick up the correct default config and in-memory services.
    """

    # Create an isolated config pointing to tmp dirs
    conversations_path = tmp_path / "conversations"
    workspace_path = tmp_path / "workspace"

    # Ensure clean directories (both tmp and any leftover in cwd)
    # Clean up any leftover directories from previous runs in current working directory
    cwd_conversations = Path("workspace/conversations")
    if cwd_conversations.exists():
        shutil.rmtree(cwd_conversations)

    # Also clean up the workspace directory entirely to be safe
    cwd_workspace = Path("workspace")
    if cwd_workspace.exists():
        # Only remove conversations subdirectory to avoid interfering with other tests
        for item in cwd_workspace.iterdir():
            if item.name == "conversations":
                shutil.rmtree(item)

    # Clean up tmp directories
    if conversations_path.exists():
        shutil.rmtree(conversations_path)
    if workspace_path.exists():
        shutil.rmtree(workspace_path)

    conversations_path.mkdir(parents=True, exist_ok=True)
    workspace_path.mkdir(parents=True, exist_ok=True)

    # Verify the conversations directory is truly empty
    assert not list(conversations_path.iterdir()), (
        f"Conversations path not empty: {list(conversations_path.iterdir())}"
    )

    cfg: dict[str, object] = {
        "session_api_keys": session_api_keys or [],
        "conversations_path": str(conversations_path),
        "workspace_path": str(workspace_path),
        "bash_events_dir": str(tmp_path / "bash-events"),
    }
    if deferred_init:
        cfg.update(deferred_init=True, secret_key="test-bootstrap-key")
    cfg.update(config_overrides or {})
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps(cfg))

    # Ensure default config uses our file and disable any env key override
    monkeypatch.setenv("OPENHANDS_AGENT_SERVER_CONFIG_PATH", str(cfg_file))
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(tmp_path / ".openhands"))
    monkeypatch.delenv("OH_CONVERSATIONS_PATH", raising=False)
    monkeypatch.delenv("SESSION_API_KEY", raising=False)
    monkeypatch.delenv("OH_SESSION_API_KEYS_0", raising=False)
    monkeypatch.delenv("TMUX_TMPDIR", raising=False)

    if import_modules is not None:
        preload_modules(import_modules)

    # Build app after env is set
    from openhands.agent_server.api import create_app
    from openhands.agent_server.config import Config
    from openhands.agent_server.persistence import reset_stores

    reset_stores()
    cfg_obj = Config.model_validate_json(cfg_file.read_text())

    app = create_app(cfg_obj)

    # Start uvicorn on a free port
    port = find_available_tcp_port()
    config = uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="warning", ws="wsproto"
    )
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Wait for the server to be ready with health check

    base_url = f"http://127.0.0.1:{port}"
    server_ready = False
    for attempt in range(50):  # Wait up to 5 seconds
        try:
            with httpx.Client() as client:
                response = client.get(f"{base_url}/health", timeout=2.0)
                if response.status_code == 200:
                    server_ready = True
                    break
        except (httpx.RequestError, httpx.TimeoutException):
            pass
        time.sleep(0.1)

    if not server_ready:
        raise RuntimeError("Server failed to start within timeout")

    try:
        yield {
            "app": app,
            "conversation_service": (
                None if deferred_init else app.state.conversation_service
            ),
            "host": f"http://127.0.0.1:{port}",
            "workspace_path": workspace_path,
        }
    finally:
        # uvicorn.Server lacks a robust shutdown API here; rely on daemon thread exit.
        server.should_exit = True
        thread.join(timeout=2)

        # Clean up any leftover directories created during the test
        cwd_conversations = Path("workspace/conversations")
        if cwd_conversations.exists():
            shutil.rmtree(cwd_conversations)
        reset_stores()
