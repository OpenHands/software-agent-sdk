"""Tests for the TypeScript client Agent Server release updater."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


def _load_prod_module():
    repo_root = Path(__file__).resolve().parents[2]
    script_path = (
        repo_root
        / ".github"
        / "scripts"
        / "prepare_typescript_client_agent_server_bump.py"
    )
    name = "prepare_typescript_client_agent_server_bump"
    spec = importlib.util.spec_from_file_location(name, script_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_prod = _load_prod_module()
prepare_bump = _prod.prepare_bump
validate_openapi_artifact = _prod.validate_openapi_artifact


def _write_client_fixture(root: Path, version: str) -> None:
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / "package.json").write_text(
        json.dumps(
            {
                "config": {
                    "agentServerImage": (
                        f"ghcr.io/openhands/agent-server:{version}-python"
                    )
                }
            },
            indent=2,
        )
        + "\n"
    )
    (root / ".github" / "workflows" / "integration-tests.yml").write_text(
        f"image: ghcr.io/openhands/agent-server:{version}-python\n"
    )
    (root / "AGENTS.md").write_text(
        f"Pinned to software-agent-sdk v{version} and `v{version}`.\n"
    )
    (root / "README.md").write_text(
        f"Use ghcr.io/openhands/agent-server:{version}-python.\n"
    )


def _write_openapi(path: Path, version: str) -> None:
    path.write_text(
        json.dumps(
            {
                "openapi": "3.1.0",
                "info": {"title": "Agent Server", "version": version},
                "paths": {"/api/settings": {}},
                "components": {"schemas": {"Settings": {"type": "object"}}},
            }
        )
    )


def test_prepare_bump_updates_all_mirrors_from_exact_artifact(tmp_path: Path):
    client_root = tmp_path / "typescript-client"
    client_root.mkdir()
    _write_client_fixture(client_root, "1.37.0")
    artifact = tmp_path / "openapi.json"
    _write_openapi(artifact, "1.38.0")

    result = prepare_bump(
        client_root,
        target_version="1.38.0",
        openapi_artifact=artifact,
    )

    assert result.previous_version == "1.37.0"
    assert result.target_version == "1.38.0"
    assert result.changed_files == (
        Path("package.json"),
        Path(".github/workflows/integration-tests.yml"),
        Path("AGENTS.md"),
        Path("README.md"),
    )
    for relative_path in result.changed_files:
        content = (client_root / relative_path).read_text()
        assert "1.37.0" not in content
        assert "1.38.0" in content


def test_prepare_bump_is_idempotent_for_already_pinned_release(tmp_path: Path):
    client_root = tmp_path / "typescript-client"
    client_root.mkdir()
    _write_client_fixture(client_root, "1.38.0")
    artifact = tmp_path / "openapi.json"
    _write_openapi(artifact, "1.38.0")

    result = prepare_bump(
        client_root,
        target_version="1.38.0",
        openapi_artifact=artifact,
    )

    assert result.changed_files == ()


def test_rejects_mismatched_or_incomplete_openapi_artifact(tmp_path: Path):
    artifact = tmp_path / "openapi.json"
    _write_openapi(artifact, "1.39.0")

    with pytest.raises(ValueError, match="does not match"):
        validate_openapi_artifact(artifact, "1.38.0")

    artifact.write_text(
        json.dumps(
            {
                "openapi": "3.1.0",
                "info": {"version": "1.38.0"},
                "paths": {},
                "components": {},
            }
        )
    )
    with pytest.raises(ValueError, match=r"components\.schemas"):
        validate_openapi_artifact(artifact, "1.38.0")


@pytest.mark.parametrize("version", ["latest", "1.2", "v1.2.3", "1.2.3-rc1"])
def test_rejects_unpinned_target_versions(tmp_path: Path, version: str):
    client_root = tmp_path / "typescript-client"
    client_root.mkdir()
    _write_client_fixture(client_root, "1.37.0")
    artifact = tmp_path / "openapi.json"
    _write_openapi(artifact, version)

    with pytest.raises(ValueError, match="expected X.Y.Z"):
        prepare_bump(
            client_root,
            target_version=version,
            openapi_artifact=artifact,
        )
