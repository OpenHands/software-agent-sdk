"""Shared fixtures for canvas extension tests."""

import json
from pathlib import Path
from typing import Any

import pytest

from openhands.agent_server.canvas_extensions.manifest import MANIFEST_FILENAME


def write_extension(
    directory: Path,
    name: str = "my-extension",
    version: str = "1.0.0",
    display_name: str = "My Extension",
    description: str = "",
    entrypoint: str = "dist/index.js",
    pages: list[dict[str, str]] | None = None,
) -> Path:
    """Write a valid, loadable canvas extension package to *directory*."""
    directory.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "name": name,
        "display_name": display_name,
        "version": version,
        "description": description,
        "entrypoint": entrypoint,
    }
    if pages is not None:
        manifest["contributes"] = {"pages": pages}
    (directory / MANIFEST_FILENAME).write_text(json.dumps(manifest))
    entry_file = directory / entrypoint
    entry_file.parent.mkdir(parents=True, exist_ok=True)
    entry_file.write_text("console.log('ok')")
    return directory


@pytest.fixture
def extension_dir(tmp_path: Path) -> Path:
    return write_extension(tmp_path / "source" / "my-extension")


@pytest.fixture
def installed_dir(tmp_path: Path) -> Path:
    return tmp_path / "installed"
