"""Shared fixtures for cross package tests."""

import json
from pathlib import Path

import pytest


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
