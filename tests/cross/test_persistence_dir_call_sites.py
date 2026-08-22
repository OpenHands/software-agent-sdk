"""Per-call-site coverage for ``OH_PERSISTENCE_DIR``.

Every module that reads or writes user-level ``~/.openhands`` state must route
through :func:`openhands.sdk.utils.path.get_user_persistence_dir`, so that an
enterprise ephemeral sandbox pointing ``OH_PERSISTENCE_DIR`` at a persistent
volume keeps its data across a resume. This module asserts that contract at
*each* call site rather than trusting the shared helper alone, which guards
against a future edit reintroducing a bare ``Path.home() / ".openhands"``.

Two flavours of call site exist:

* **Import-time constants** (e.g. ``_DEFAULT_PROFILE_DIR``) resolve the env var
  once when their module is first imported. They are verified in a fresh
  subprocess so the import genuinely happens under the test environment, the
  way a real process start does. This also keeps module state from leaking
  between tests.
* **Call-time functions** resolve on every call and are exercised in-process
  with a real read/write wherever the API allows it.
"""

from __future__ import annotations

import json
import os
import site
import subprocess
import sys
import sysconfig
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest


def _import_roots() -> list[str]:
    """Directories that make ``openhands`` + its deps importable in a child.

    Overriding ``HOME`` in the child moves Python's per-user site-packages to
    ``$HOME/.local/...``, which both bypasses the editable install's
    ``.pth``-injected finders and hides third-party dependencies. Resolved in
    the parent (real ``HOME``), these roots pin both to ``PYTHONPATH``.
    """
    repo_root = Path(__file__).resolve().parents[2]
    roots = [
        str(repo_root / pkg)
        for pkg in ("openhands-sdk", "openhands-tools", "openhands-agent-server")
    ]
    roots += list(sys.path)
    roots.append(site.getusersitepackages())
    roots.extend(site.getsitepackages() if hasattr(site, "getsitepackages") else [])
    roots.append(sysconfig.get_path("purelib"))
    seen: set[str] = set()
    ordered: list[str] = []
    for r in roots:
        if r and r not in seen and Path(r).exists():
            seen.add(r)
            ordered.append(r)
    return ordered


_IMPORT_ROOTS = _import_roots()


def _subprocess_env(**overrides: str) -> dict[str, str]:
    """Env for a child interpreter that stays importable with a fake ``HOME``."""
    env = dict(os.environ)
    env.update(overrides)
    env["OPENHANDS_SUPPRESS_BANNER"] = "1"
    existing = env.get("PYTHONPATH", "")
    parts = _IMPORT_ROOTS + ([existing] if existing else [])
    env["PYTHONPATH"] = os.pathsep.join(parts)
    return env


# --------------------------------------------------------------------------- #
# Import-time constants — verified via subprocess import.
# --------------------------------------------------------------------------- #

# name -> (module, attribute, subpath-relative-to-persistence-root)
# ``attribute`` may be an index into a list constant via "attr[i]".
_IMPORT_TIME_CALL_SITES: dict[str, tuple[str, str, str]] = {
    "llm_profile_store": (
        "openhands.sdk.llm.llm_profile_store",
        "_DEFAULT_PROFILE_DIR",
        "profiles",
    ),
    "agent_profile_store": (
        "openhands.sdk.profiles.agent_profile_store",
        "_DEFAULT_PROFILE_DIR",
        "agent-profiles",
    ),
    "skills_fetch_cache": (
        "openhands.sdk.skills.fetch",
        "DEFAULT_CACHE_DIR",
        "cache/skills",
    ),
    "skills_installed": (
        "openhands.sdk.skills.installed",
        "DEFAULT_INSTALLED_SKILLS_DIR",
        "skills/installed",
    ),
    "skills_user_dirs_skills": (
        "openhands.sdk.skills.skill",
        "USER_SKILLS_DIRS[1]",
        "skills",
    ),
    "skills_user_dirs_microagents": (
        "openhands.sdk.skills.skill",
        "USER_SKILLS_DIRS[2]",
        "microagents",
    ),
    "plugin_fetch_cache": (
        "openhands.sdk.plugin.fetch",
        "DEFAULT_CACHE_DIR",
        "cache/plugins",
    ),
    "plugin_source_cache": (
        "openhands.sdk.plugin.source",
        "DEFAULT_CACHE_DIR",
        "cache/git",
    ),
    "plugin_user_dirs": (
        "openhands.sdk.plugin.discovery",
        "USER_PLUGINS_DIRS[1]",
        "plugins",
    ),
    "plugin_installed": (
        "openhands.sdk.plugin.installed",
        "DEFAULT_INSTALLED_PLUGINS_DIR",
        "plugins/installed",
    ),
    "extensions_cache": (
        "openhands.sdk.extensions.installation.manager",
        "DEFAULT_CACHE_DIR",
        "cache/extensions",
    ),
    "agent_soul_path": (
        "openhands.sdk.agent.base",
        "_SOUL_PATH",
        "SOUL.md",
    ),
}

# Call sites that intentionally keep a ``~/.agents`` entry ahead of the
# persistence-dir entry. Verified to still point at ``~/.agents`` regardless of
# ``OH_PERSISTENCE_DIR``.
_AGENTS_DIR_CALL_SITES: dict[str, tuple[str, str, str]] = {
    "skills_user_dirs_agents": (
        "openhands.sdk.skills.skill",
        "USER_SKILLS_DIRS[0]",
        ".agents/skills",
    ),
    "plugin_user_dirs_agents": (
        "openhands.sdk.plugin.discovery",
        "USER_PLUGINS_DIRS[0]",
        ".agents/plugins",
    ),
}


def _resolve_attr(module: object, attr: str) -> object:
    """Resolve ``name`` or ``name[index]`` against an imported module."""
    if attr.endswith("]"):
        base, index = attr[:-1].split("[")
        return getattr(module, base)[int(index)]
    return getattr(module, attr)


def _probe_constants_in_subprocess(env: dict[str, str]) -> dict[str, str]:
    """Import each call site's module in a subprocess and return its path.

    Runs under ``env`` so import-time resolution of ``OH_PERSISTENCE_DIR`` /
    ``HOME`` happens exactly as it would on a real process start.
    """
    all_sites = {**_IMPORT_TIME_CALL_SITES, **_AGENTS_DIR_CALL_SITES}
    spec = {name: (mod, attr) for name, (mod, attr, _sub) in all_sites.items()}
    script = (
        "import json, importlib\n"
        f"spec = {spec!r}\n"
        "out = {}\n"
        "import os\n"
        "def resolve(module, attr):\n"
        "    if attr.endswith(']'):\n"
        "        base, index = attr[:-1].split('[')\n"
        "        return getattr(module, base)[int(index)]\n"
        "    return getattr(module, attr)\n"
        "for name, (mod, attr) in spec.items():\n"
        "    m = importlib.import_module(mod)\n"
        "    out[name] = os.fspath(resolve(m, attr))\n"
        "print(json.dumps(out))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stderr
    # The banner/log lines go to stderr; stdout carries only our JSON line.
    return json.loads(result.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def constants_with_persistence_env() -> Iterator[dict[str, str]]:
    with tempfile.TemporaryDirectory() as tmpdir:
        home = str(Path(tmpdir) / "unused-home")
        env = _subprocess_env(OH_PERSISTENCE_DIR=tmpdir, HOME=home)
        paths = _probe_constants_in_subprocess(env)
        paths["__root__"] = tmpdir
        paths["HOME"] = home
        yield paths


@pytest.fixture(scope="module")
def constants_with_home_fallback() -> Iterator[dict[str, str]]:
    with tempfile.TemporaryDirectory() as tmpdir:
        env = _subprocess_env(HOME=tmpdir)
        env.pop("OH_PERSISTENCE_DIR", None)
        paths = _probe_constants_in_subprocess(env)
        paths["__root__"] = tmpdir
        yield paths


@pytest.mark.parametrize("name", sorted(_IMPORT_TIME_CALL_SITES))
def test_import_time_constant_honors_persistence_env(
    name: str, constants_with_persistence_env: dict[str, str]
) -> None:
    root = Path(constants_with_persistence_env["__root__"])
    _mod, _attr, subpath = _IMPORT_TIME_CALL_SITES[name]
    assert Path(constants_with_persistence_env[name]) == root / subpath


@pytest.mark.parametrize("name", sorted(_IMPORT_TIME_CALL_SITES))
def test_import_time_constant_falls_back_to_home(
    name: str, constants_with_home_fallback: dict[str, str]
) -> None:
    home = Path(constants_with_home_fallback["__root__"])
    _mod, _attr, subpath = _IMPORT_TIME_CALL_SITES[name]
    assert Path(constants_with_home_fallback[name]) == home / ".openhands" / subpath


@pytest.mark.parametrize("name", sorted(_AGENTS_DIR_CALL_SITES))
def test_agents_dir_call_sites_ignore_persistence_env(
    name: str, constants_with_persistence_env: dict[str, str]
) -> None:
    """The ``~/.agents`` entries stay on ``$HOME/.agents`` even with the env set."""
    _mod, _attr, subpath = _AGENTS_DIR_CALL_SITES[name]
    home = Path(constants_with_persistence_env["HOME"])
    resolved = Path(constants_with_persistence_env[name])
    # Anchored at $HOME/.agents/..., never redirected into the persistence dir.
    assert resolved == home / subpath
    assert ".openhands" not in resolved.parts


# --------------------------------------------------------------------------- #
# Call-time functions — exercised in-process with real reads/writes.
# --------------------------------------------------------------------------- #


@pytest.fixture
def persistence_dir(monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """``OH_PERSISTENCE_DIR`` pointed at a clean tempdir for call-time sites."""
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setenv("OH_PERSISTENCE_DIR", tmpdir)
        yield Path(tmpdir)


def test_credentials_dir_uses_persistence_dir(persistence_dir: Path) -> None:
    from openhands.sdk.llm.auth.credentials import get_credentials_dir

    assert get_credentials_dir() == persistence_dir / "auth"


def test_skills_cache_dir_uses_persistence_dir(persistence_dir: Path) -> None:
    from openhands.sdk.skills.utils import get_skills_cache_dir

    assert get_skills_cache_dir() == persistence_dir / "cache" / "skills"


def test_hook_config_reads_user_hooks_json(persistence_dir: Path) -> None:
    from openhands.sdk.hooks.config import HookConfig

    (persistence_dir / "hooks.json").write_text(
        json.dumps(
            {
                "pre_tool_use": [
                    {
                        "matcher": "*",
                        "hooks": [{"type": "command", "command": "echo hi"}],
                    }
                ]
            }
        )
    )
    # A project dir with no local hooks forces the user-level lookup.
    with tempfile.TemporaryDirectory() as project:
        cfg = HookConfig.load(working_dir=project)
    assert cfg.pre_tool_use[0].hooks[0].command == "echo hi"


def test_load_user_agents_reads_persistence_dir(persistence_dir: Path) -> None:
    from openhands.sdk.subagent.load import load_user_agents

    agents_dir = persistence_dir / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "reviewer.md").write_text(
        "---\nname: reviewer\ndescription: reviews code\n---\nBe a reviewer.\n"
    )
    names = {a.name for a in load_user_agents()}
    assert "reviewer" in names


def test_load_memory_reads_user_index(persistence_dir: Path) -> None:
    from openhands.sdk.context.memory import load_memory

    memory_dir = persistence_dir / "memory"
    memory_dir.mkdir(parents=True)
    (memory_dir / "MEMORY.md").write_text("Remember: the launch code is 0000.")
    with tempfile.TemporaryDirectory() as project:
        combined = load_memory(working_dir=project)
    assert "the launch code is 0000." in (combined or "")


def test_prompt_jinja_cache_lives_in_persistence_dir(persistence_dir: Path) -> None:
    from openhands.sdk.agent import base
    from openhands.sdk.context.prompts import prompt as prompt_mod

    prompt_mod._get_env.cache_clear()
    try:
        prompt_mod._get_env(base._BUILTIN_PROMPT_DIR)
        assert (persistence_dir / "cache" / "jinja").is_dir()
    finally:
        prompt_mod._get_env.cache_clear()


def test_tom_consult_file_store_root(persistence_dir: Path) -> None:
    pytest.importorskip("tom_swe")
    from typing import cast

    from openhands.sdk.conversation.state import ConversationState
    from openhands.sdk.io import LocalFileStore
    from openhands.tools.tom_consult.definition import (
        SleeptimeComputeTool,
        TomConsultTool,
    )
    from openhands.tools.tom_consult.executor import TomConsultExecutor

    # ``create`` ignores ``conv_state`` (state is passed at execution time), so a
    # typed ``None`` keeps pyright happy without building a full state object.
    conv_state = cast(ConversationState, None)
    for tool_cls in (TomConsultTool, SleeptimeComputeTool):
        tools = tool_cls.create(conv_state=conv_state)
        executor = tools[0].executor
        assert isinstance(executor, TomConsultExecutor)
        file_store = executor.file_store
        assert isinstance(file_store, LocalFileStore)
        assert file_store.root == str(persistence_dir)


def test_canvas_extensions_dir_uses_persistence_dir(persistence_dir: Path) -> None:
    from openhands.agent_server.canvas_extensions.installed import (
        get_installed_canvas_extensions_dir,
    )

    assert (
        get_installed_canvas_extensions_dir()
        == persistence_dir / "canvas-extensions" / "installed"
    )


def test_agent_server_profile_persistence_dir(persistence_dir: Path) -> None:
    from openhands.agent_server.persistence.store import _get_profile_persistence_dir

    assert _get_profile_persistence_dir() == persistence_dir


def test_default_llm_profile_store_survives_resume() -> None:
    """End-to-end: a default-constructed store persists across a HOME wipe.

    Reproduces the ephemeral-sandbox resume. ``LLMProfileStore()`` (no
    ``base_dir``) uses the import-time default, so each run happens in its own
    subprocess to exercise that resolution honestly. Run 1 writes with a
    throwaway HOME; the HOME is then deleted and a fresh empty HOME is used for
    run 2, which must still read the profile back from ``OH_PERSISTENCE_DIR``.
    """
    import shutil

    with (
        tempfile.TemporaryDirectory() as persist,
        tempfile.TemporaryDirectory() as home_root,
    ):
        home1 = Path(home_root) / "home1"
        home1.mkdir()

        write = (
            "from openhands.sdk.llm import LLM\n"
            "from openhands.sdk.llm.llm_profile_store import LLMProfileStore\n"
            "LLMProfileStore().save('prod', LLM(model='gpt-4o', usage_id='prod'))\n"
        )
        r1 = subprocess.run(
            [sys.executable, "-c", write],
            env=_subprocess_env(OH_PERSISTENCE_DIR=persist, HOME=str(home1)),
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert r1.returncode == 0, r1.stderr
        assert (Path(persist) / "profiles" / "prod.json").is_file()
        assert not (home1 / ".openhands").exists()

        # Resume: destroy the ephemeral HOME, hand run 2 a brand-new empty one.
        shutil.rmtree(home1)
        home2 = Path(home_root) / "home2"
        home2.mkdir()

        read = (
            "from openhands.sdk.llm.llm_profile_store import LLMProfileStore\n"
            "print(LLMProfileStore().load('prod').model)\n"
        )
        r2 = subprocess.run(
            [sys.executable, "-c", read],
            env=_subprocess_env(OH_PERSISTENCE_DIR=persist, HOME=str(home2)),
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert r2.returncode == 0, r2.stderr
        assert r2.stdout.strip().splitlines()[-1] == "gpt-4o"
