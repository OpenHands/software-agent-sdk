"""Synthetic public-extensions and organization-marketplace repositories."""

import json
from pathlib import Path

import pytest

from openhands.sdk.skills.skill import (
    PUBLIC_PLUGIN_CATALOG_ENABLED_ENV,
    PUBLIC_SKILLS_ENABLED_ENV,
    PUBLIC_SKILLS_PATH_ENV,
    _invalidate_public_skills_cache,
)


PUBLIC_GITHUB = "PUBLIC-GITHUB-MARKER"
ORG_GITHUB = "ORG-GITHUB-MARKER"
PLUGIN_GITHUB = "PLUGIN-GITHUB-MARKER"


def _skill(directory: Path, name: str, body: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {name} skill\n---\n{body}\n"
    )


def make_public_repo(root: Path) -> Path:
    """A local stand-in for the default public extensions repository."""
    _skill(root / "skills" / "github", "github", PUBLIC_GITHUB)
    _skill(root / "skills" / "public-only", "public-only", "public only")
    return root


def make_standalone_marketplace(root: Path, unique: str = "org-unique") -> Path:
    _skill(root / "skills" / "github", "github", ORG_GITHUB)
    _skill(root / "skills" / unique, unique, f"{unique} body")
    (root / ".plugin").mkdir(parents=True, exist_ok=True)
    manifest = {
        "name": "org-standalone",
        "owner": {"name": "Org"},
        "plugins": [],
        "skills": [
            {"name": "github", "source": "./skills/github"},
            {"name": unique, "source": f"./skills/{unique}"},
        ],
    }
    (root / ".plugin" / "marketplace.json").write_text(json.dumps(manifest))
    return root


def make_plugin_marketplace(root: Path) -> Path:
    """A marketplace with one true plugin: two skills, a hook, MCP and an agent."""
    plugin = root / "plugins" / "org-tools"
    (plugin / ".plugin").mkdir(parents=True, exist_ok=True)
    (plugin / ".plugin" / "plugin.json").write_text(
        json.dumps({"name": "org-tools", "version": "1.0.0", "description": "t"})
    )
    _skill(plugin / "skills" / "github", "github", PLUGIN_GITHUB)
    _skill(plugin / "skills" / "plugin-other", "plugin-other", "plugin other")
    hooks = {"PreToolUse": [{"matcher": "org-*", "hooks": [{"command": "true"}]}]}
    (plugin / "hooks").mkdir()
    (plugin / "hooks" / "hooks.json").write_text(json.dumps({"hooks": hooks}))
    (plugin / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"org-server": {"command": "org-mcp"}}})
    )
    (plugin / "agents").mkdir()
    (plugin / "agents" / "org-helper.md").write_text(
        "---\nname: org-helper\ndescription: Org helper\n---\nHelp.\n"
    )
    (root / ".plugin").mkdir(parents=True, exist_ok=True)
    manifest = {
        "name": "org-plugins",
        "owner": {"name": "Org"},
        "plugins": [{"name": "org-tools", "source": "./plugins/org-tools"}],
    }
    (root / ".plugin" / "marketplace.json").write_text(json.dumps(manifest))
    return root


@pytest.fixture
def public_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point the default public source at a synthetic local repository."""
    repo = make_public_repo(tmp_path / "public")
    monkeypatch.setenv(PUBLIC_SKILLS_PATH_ENV, str(repo))
    monkeypatch.delenv(PUBLIC_SKILLS_ENABLED_ENV, raising=False)
    monkeypatch.delenv(PUBLIC_PLUGIN_CATALOG_ENABLED_ENV, raising=False)
    _invalidate_public_skills_cache()
    yield repo
    _invalidate_public_skills_cache()
