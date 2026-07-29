"""Tests for the persisted settings compatibility check script."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel


os.environ.setdefault("OPENHANDS_SUPPRESS_BANNER", "1")


def _load_script_module(name: str):
    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / ".github" / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, script_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_prod = _load_script_module("check_persisted_settings_compat")
PersistedSettingsCompatError = _prod.PersistedSettingsCompatError
BaselinePayloadCase = _prod.BaselinePayloadCase
FixtureCase = _prod.FixtureCase
SURFACES = _prod.SURFACES
SurfaceConfig = _prod.SurfaceConfig
collect_fixture_cases = _prod.collect_fixture_cases
get_pypi_baseline_version = _prod.get_pypi_baseline_version
validate_baseline_payload_cases = _prod.validate_baseline_payload_cases
validate_fixture_cases = _prod.validate_fixture_cases


class _FlattenedMCPSettings(BaseModel):
    schema_version: int
    mcp_config: dict[str, Any]


class _AdditiveItem(BaseModel):
    existing: str
    added: str | None = None


class _AdditiveSettings(BaseModel):
    schema_version: int
    items: list[_AdditiveItem]


def _load_and_flatten_mcp_settings(data: Any) -> _FlattenedMCPSettings:
    payload = dict(data)
    payload["schema_version"] = 1
    mcp_config = payload["mcp_config"]
    if "mcpServers" in mcp_config:
        payload["mcp_config"] = mcp_config["mcpServers"]
    return _FlattenedMCPSettings.model_validate(payload)


def _load_additive_settings(data: Any) -> _AdditiveSettings:
    return _AdditiveSettings.model_validate(data)


_SHAPE_CHANGING_SURFACE = SurfaceConfig(
    key="agent_settings",
    display_name="AgentSettings",
    current_version=1,
    loader=_load_and_flatten_mcp_settings,
    migration_guidance="Bump the schema version and add a migration.",
)

_ADDITIVE_SURFACE = SurfaceConfig(
    key="agent_settings",
    display_name="AgentSettings",
    current_version=1,
    loader=_load_additive_settings,
    migration_guidance="Bump the schema version and add a migration.",
)


def _mock_pypi_releases(monkeypatch, releases: dict[str, list[dict[str, str]]]) -> None:
    payload = {"releases": releases}

    class _DummyResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps(payload).encode()

    monkeypatch.setattr(
        _prod.urllib.request, "urlopen", lambda *_args, **_kwargs: _DummyResponse()
    )


def test_collect_fixture_cases_and_validate_current_repo_fixtures() -> None:
    cases = collect_fixture_cases()

    validate_fixture_cases(cases)

    versions_by_surface: dict[str, set[int]] = {}
    for case in cases:
        versions_by_surface.setdefault(case.surface_key, set()).add(case.version)

    assert versions_by_surface == {
        "agent_settings": {1, 2, 3, 4, 5},
        "conversation_settings": {1},
        "persisted_settings": {1, 2},
    }


def test_validate_fixture_cases_requires_every_schema_version() -> None:
    cases = [case for case in collect_fixture_cases() if case.version != 2]

    with pytest.raises(
        PersistedSettingsCompatError,
        match="Missing persisted settings fixtures for AgentSettings: v2",
    ):
        validate_fixture_cases(cases)


def test_collect_fixture_cases_ignores_non_directory_entries(
    tmp_path: Path,
) -> None:
    root = tmp_path / "persisted_settings_baselines"
    root.mkdir()
    (root / "README.md").write_text("fixture notes")
    version_dir = root / "v1"
    version_dir.mkdir()
    (version_dir / "conversation_settings.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "max_iterations": 123,
                "confirmation_mode": False,
                "security_analyzer": "llm",
            }
        )
    )

    cases = collect_fixture_cases(root)

    assert [case.path.name for case in cases] == ["conversation_settings.json"]


def test_collect_fixture_cases_rejects_mismatched_directory_version(
    tmp_path: Path,
) -> None:
    root = tmp_path / "persisted_settings_baselines"
    version_dir = root / "v2"
    version_dir.mkdir(parents=True)
    (version_dir / "conversation_settings.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "max_iterations": 123,
                "confirmation_mode": False,
                "security_analyzer": "llm",
            }
        )
    )

    with pytest.raises(
        PersistedSettingsCompatError,
        match="has schema_version 1, but is stored under v2",
    ):
        collect_fixture_cases(root)


def test_validate_fixture_cases_surfaces_loader_guidance_on_failure() -> None:
    bad_case = FixtureCase(
        path=Path(
            "tests/sdk/persisted_settings_baselines/v1/conversation_settings.json"
        ),
        surface_key="conversation_settings",
        version=1,
        payload={
            "schema_version": 1,
            "max_iterations": 0,
            "confirmation_mode": True,
            "security_analyzer": "llm",
        },
        expected_paths={},
    )

    with pytest.raises(
        PersistedSettingsCompatError,
        match="_CONVERSATION_SETTINGS_MIGRATIONS",
    ):
        validate_fixture_cases(
            [bad_case],
            surfaces={"conversation_settings": SURFACES["conversation_settings"]},
        )


def test_validate_fixture_cases_checks_expected_sentinel_values() -> None:
    bad_case = FixtureCase(
        path=Path(
            "tests/sdk/persisted_settings_baselines/v1/conversation_settings.json"
        ),
        surface_key="conversation_settings",
        version=1,
        payload={
            "schema_version": 1,
            "max_iterations": 321,
            "confirmation_mode": True,
            "security_analyzer": "llm",
        },
        expected_paths={"max_iterations": 999},
    )

    with pytest.raises(
        PersistedSettingsCompatError,
        match="changed expected field 'max_iterations'",
    ):
        validate_fixture_cases(
            [bad_case],
            surfaces={"conversation_settings": SURFACES["conversation_settings"]},
        )


def test_get_pypi_baseline_version_prefers_current_or_previous(monkeypatch) -> None:
    _mock_pypi_releases(
        monkeypatch,
        {
            "1.0.0": [{"upload_time_iso_8601": "2026-01-01T00:00:00Z"}],
            "1.1.0": [{"upload_time_iso_8601": "2026-01-02T00:00:00Z"}],
        },
    )

    assert get_pypi_baseline_version("openhands-sdk", "1.1.0") == "1.1.0"
    assert get_pypi_baseline_version("openhands-sdk", "1.2.0") == "1.1.0"


def test_get_pypi_baseline_version_raises_on_metadata_failure(monkeypatch) -> None:
    def _raise_url_error(*_args, **_kwargs):
        raise OSError("boom")

    monkeypatch.setattr(_prod.urllib.request, "urlopen", _raise_url_error)

    with pytest.raises(
        PersistedSettingsCompatError,
        match="Failed to fetch PyPI metadata for openhands-sdk",
    ):
        get_pypi_baseline_version("openhands-sdk", "1.2.0")


def test_validate_baseline_rejects_shape_change_without_version_bump() -> None:
    case = BaselinePayloadCase(
        source="PyPI baseline openhands-sdk==1.0.0",
        key="agent_settings/populated",
        surface_key="agent_settings",
        payload={
            "schema_version": 1,
            "mcp_config": {
                "mcpServers": {
                    "shttp": {
                        "url": "https://example.com/mcp",
                        "timeout": 60,
                    }
                }
            },
        },
    )

    with pytest.raises(
        PersistedSettingsCompatError,
        match=(
            "did not preserve persisted field 'mcp_config.mcpServers' "
            "without advancing schema_version 1"
        ),
    ):
        validate_baseline_payload_cases(
            [case],
            surfaces={"agent_settings": _SHAPE_CHANGING_SURFACE},
        )


def test_validate_baseline_allows_shape_change_with_version_bump() -> None:
    case = BaselinePayloadCase(
        source="PyPI baseline openhands-sdk==0.9.0",
        key="agent_settings/populated",
        surface_key="agent_settings",
        payload={
            "schema_version": 0,
            "mcp_config": {
                "mcpServers": {
                    "shttp": {
                        "url": "https://example.com/mcp",
                        "timeout": 60,
                    }
                }
            },
        },
    )

    validate_baseline_payload_cases(
        [case],
        surfaces={"agent_settings": _SHAPE_CHANGING_SURFACE},
    )


def test_validate_baseline_allows_additive_same_version_fields() -> None:
    case = BaselinePayloadCase(
        source="PyPI baseline openhands-sdk==1.0.0",
        key="agent_settings/default",
        surface_key="agent_settings",
        payload={
            "schema_version": 1,
            "items": [{"existing": "preserved"}],
        },
    )

    validate_baseline_payload_cases(
        [case],
        surfaces={"agent_settings": _ADDITIVE_SURFACE},
    )


def test_generate_baseline_payloads_uses_uv_with_release_cutoff(monkeypatch) -> None:
    monkeypatch.setattr(_prod, "_venv_python", lambda _path: Path("/tmp/fake-python"))
    calls: list[list[str]] = []

    def _fake_uv_run(args: list[str]):
        calls.append(args)
        if args[:2] == ["uv", "venv"]:
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        if args[:3] == ["uv", "pip", "install"]:
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        assert args[0] == "/tmp/fake-python"
        return subprocess.CompletedProcess(
            args,
            0,
            stdout=json.dumps(
                [
                    {
                        "key": "conversation_settings/default",
                        "payload": {
                            "schema_version": 1,
                            "max_iterations": 500,
                            "confirmation_mode": False,
                            "security_analyzer": "llm",
                        },
                    }
                ]
            ),
            stderr="",
        )

    monkeypatch.setattr(_prod, "_uv_run", _fake_uv_run)

    cases = _prod.generate_baseline_payloads(
        sdk_version="1.2.3",
        agent_server_version="1.2.3",
        exclude_newer="2026-01-02T00:00:00Z",
    )

    assert [case.key for case in cases] == ["conversation_settings/default"]
    assert calls[0][:2] == ["uv", "venv"]
    assert calls[1] == [
        "uv",
        "pip",
        "install",
        "--python",
        "/tmp/fake-python",
        "--quiet",
        "--exclude-newer",
        "2026-01-02T00:00:00Z",
        "openhands-sdk==1.2.3",
        "openhands-agent-server==1.2.3",
    ]


def test_generate_baseline_payloads_raises_on_generation_failure(monkeypatch) -> None:
    monkeypatch.setattr(_prod, "_venv_python", lambda _path: Path("/tmp/fake-python"))

    def _raise_called_process_error(_args: list[str]):
        raise subprocess.CalledProcessError(
            1,
            ["uv", "pip", "install"],
            output="stdout",
            stderr="stderr",
        )

    monkeypatch.setattr(_prod, "_uv_run", _raise_called_process_error)

    with pytest.raises(
        PersistedSettingsCompatError,
        match="Failed to generate baseline payloads",
    ):
        _prod.generate_baseline_payloads(
            sdk_version="1.2.3",
            agent_server_version="1.2.3",
            exclude_newer="2026-01-02T00:00:00Z",
        )
