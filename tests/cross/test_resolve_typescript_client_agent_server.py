"""Tests for the TypeScript client pinned Agent Server resolver."""

from __future__ import annotations

import importlib.util
import json
import sys
import urllib.error
from pathlib import Path

import pytest


def _load_prod_module():
    repo_root = Path(__file__).resolve().parents[2]
    script_path = (
        repo_root / ".github" / "scripts" / "resolve_typescript_client_agent_server.py"
    )
    name = "resolve_typescript_client_agent_server"
    spec = importlib.util.spec_from_file_location(name, script_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_prod = _load_prod_module()


def _fixture(root: Path, pinned: str, source: str) -> tuple[Path, Path]:
    client = root / "client"
    client.mkdir()
    image = f"ghcr.io/openhands/agent-server:{pinned}-python"
    (client / "package.json").write_text(
        json.dumps({"config": {"agentServerImage": image}})
    )
    pyproject = root / "pyproject.toml"
    pyproject.write_text(f'[project]\nname = "x"\nversion = "{source}"\n')
    return client, pyproject


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("url", code, "msg", None, None)  # type: ignore[arg-type]


@pytest.fixture
def urlopen(monkeypatch):
    calls: list[str] = []
    outcome: dict[str, object] = {"result": None}

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake(url, timeout):
        calls.append(url)
        if isinstance(outcome["result"], Exception):
            raise outcome["result"]
        return _Response()

    monkeypatch.setattr(_prod.urllib.request, "urlopen", fake)
    return calls, outcome


def test_release_pr_uses_source_contract(tmp_path, urlopen):
    calls, outcome = urlopen
    outcome["result"] = _http_error(404)
    client, pyproject = _fixture(tmp_path, "1.53.0", "1.53.0")

    assert _prod.resolve(client, pyproject) == {
        "pinned_version": "1.53.0",
        "pinned_is_source": "true",
        "use_source_contract": "true",
    }
    assert calls == [
        "https://github.com/OpenHands/software-agent-sdk/releases/download/"
        "v1.53.0/openapi.json"
    ]


def test_published_release_uses_release_contract(tmp_path, urlopen):
    client, pyproject = _fixture(tmp_path, "1.53.0", "1.53.0")

    assert _prod.resolve(client, pyproject)["use_source_contract"] == "false"


def test_pin_behind_source_never_uses_source_contract(tmp_path, urlopen):
    calls, _ = urlopen
    client, pyproject = _fixture(tmp_path, "1.52.0", "1.53.0")

    result = _prod.resolve(client, pyproject)

    assert result["pinned_is_source"] == "false"
    assert result["use_source_contract"] == "false"
    assert calls == []


def test_transient_http_error_is_not_treated_as_unreleased(tmp_path, urlopen):
    _, outcome = urlopen
    outcome["result"] = _http_error(502)
    client, pyproject = _fixture(tmp_path, "1.53.0", "1.53.0")

    with pytest.raises(urllib.error.HTTPError):
        _prod.resolve(client, pyproject)


def test_rejects_non_release_pin(tmp_path, urlopen):
    client, pyproject = _fixture(tmp_path, "1.53.0", "1.53.0")
    (client / "package.json").write_text(
        json.dumps({"config": {"agentServerImage": "agent-server:latest"}})
    )

    with pytest.raises(SystemExit, match="exact release tag"):
        _prod.resolve(client, pyproject)
