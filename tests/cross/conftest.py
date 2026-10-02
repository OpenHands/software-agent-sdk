"""Shared fixtures for cross package tests."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


@pytest.fixture
def budget_exhausted_provider():
    """Serve a provider's budget-exhausted response over real HTTP."""
    requests = []
    api_key = "sk-abcdefghijklmnopqrstuvwxyz1234567890"

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(
                json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            )
            body = json.dumps(
                {
                    "error": {
                        "message": f"Budget has been exceeded for API key: {api_key}",
                        "type": "budget_exceeded",
                        "code": "429",
                    }
                }
            ).encode()
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}", requests, api_key
        finally:
            server.shutdown()
            thread.join(timeout=2)


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
