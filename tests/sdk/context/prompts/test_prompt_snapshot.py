"""Golden-snapshot oracle for the rendered system prompt (issue #3607).

Pins ``static_system_message`` + ``dynamic_context`` across the matrix that varies
their output: model family x enable_browser x llm_security_analyzer x cli_mode.
Snapshots live under ``snapshots/`` (one .txt per cell), the dependency-free
golden-file idiom from ``tests/sdk/persisted_settings_baselines``.

Regenerate after an intentional prompt change:
    REGEN_PROMPT_SNAPSHOTS=1 uv run pytest \
        tests/sdk/context/prompts/test_prompt_snapshot.py
"""

import os
import re
import socket
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from uuid import uuid4

import pytest

from openhands.sdk.agent import Agent
from openhands.sdk.agent.base import _DEFAULT_SOUL
from openhands.sdk.context.agent_context import AgentContext
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.llm import LLM
from openhands.sdk.skills import Skill
from openhands.sdk.tool.spec import Tool
from openhands.sdk.workspace import LocalWorkspace


SNAPSHOT_DIR: Final[Path] = Path(__file__).parent / "snapshots"
SEPARATOR: Final[str] = (
    "\n\n===== DYNAMIC CONTEXT (second system content block) =====\n\n"
)
REGEN: Final[bool] = os.environ.get("REGEN_PROMPT_SNAPSHOTS") == "1"

# Strings chosen so get_model_prompt_spec resolves each family (and the gpt-5
# variant); an unmatched string is the "other" branch (no model_specific section).
FAMILY_MODELS: Final[dict[str, str]] = {
    "anthropic": "claude-sonnet-4-5",
    "openai": "gpt-5",
    "gemini": "gemini-2.5-pro",
    "other": "custom-made-model",
}

# Pin current_datetime (defaults to datetime.now()) so the dynamic block is
# reproducible. The two legacy (trigger=None) skills are vendor-named to exercise
# the model-family gating in get_system_message_suffix: anthropic keeps "claude",
# gemini keeps "gemini", openai keeps neither, "other" (no family) keeps both.
# Secrets are covered via the production get_dynamic_context() path (see below).
DYNAMIC_CONTEXT: Final[AgentContext] = AgentContext(
    current_datetime="2025-01-01T00:00:00+00:00",
    system_message_suffix="Follow the repository's coding conventions.",
    skills=[
        Skill(name="claude", content="Anthropic-only repo guidance.", trigger=None),
        Skill(name="gemini", content="Gemini-only repo guidance.", trigger=None),
    ],
)

# The datetime shape injected into the dynamic block; in the static block, a leak.
ISO_DATETIME_RE: Final[re.Pattern[str]] = re.compile(
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}"
)


@dataclass(frozen=True)
class Cell:
    family: str
    enable_browser: bool
    llm_security_analyzer: bool
    cli_mode: bool

    @property
    def id(self) -> str:
        def flag(name: str, value: bool) -> str:
            return f"{name}-{'on' if value else 'off'}"

        return "__".join(
            [
                self.family,
                flag("browser", self.enable_browser),
                flag("secana", self.llm_security_analyzer),
                flag("cli", self.cli_mode),
            ]
        )


def _build_matrix() -> list[Cell]:
    cells: list[Cell] = []
    for family in FAMILY_MODELS:
        for enable_browser in (True, False):
            for llm_security_analyzer in (True, False):
                # cli_mode only affects the security-analyzer section.
                cli_values = (True, False) if llm_security_analyzer else (True,)
                for cli_mode in cli_values:
                    cells.append(
                        Cell(family, enable_browser, llm_security_analyzer, cli_mode)
                    )
    return cells


MATRIX: Final[list[Cell]] = _build_matrix()


def _build_agent(cell: Cell) -> Agent:
    llm = LLM(model=FAMILY_MODELS[cell.family], usage_id="snapshot-llm")
    return Agent(
        llm=llm,
        tools=[],
        agent_context=DYNAMIC_CONTEXT,
        system_prompt_kwargs={
            "enable_browser": cell.enable_browser,
            "llm_security_analyzer": cell.llm_security_analyzer,
            "cli_mode": cell.cli_mode,
            # Pin soul_content to the built-in default so snapshots are
            # deterministic regardless of whether ~/.openhands/SOUL.md exists
            # on the machine running the tests.
            "soul_content": _DEFAULT_SOUL,
        },
    )


def _rendered(cell: Cell) -> str:
    agent = _build_agent(cell)
    return agent.static_system_message + SEPARATOR + (agent.dynamic_context or "")


def _check_snapshot(name: str, content: str) -> None:
    path = SNAPSHOT_DIR / f"{name}.txt"
    if REGEN:
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return
    assert path.exists(), (
        f"Missing snapshot {path}. Regenerate with "
        f"REGEN_PROMPT_SNAPSHOTS=1 pytest {Path(__file__).name}"
    )
    assert content == path.read_text(encoding="utf-8"), (
        f"Rendered prompt for '{name}' diverged from its committed snapshot. If the "
        f"change is intentional, regenerate with REGEN_PROMPT_SNAPSHOTS=1."
    )


@pytest.mark.parametrize("cell", MATRIX, ids=[c.id for c in MATRIX])
def test_prompt_snapshot(cell: Cell) -> None:
    _check_snapshot(cell.id, _rendered(cell))


# Representative cell with every section "on", used to pin the windows variant.
PLATFORM_CELL: Final[Cell] = Cell(
    family="anthropic",
    enable_browser=True,
    llm_security_analyzer=True,
    cli_mode=True,
)


def test_prompt_snapshot_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    # refine() (context/prompts/prompt.py) swaps bash -> powershell and
    # terminal -> execute_powershell on win32, reading sys.platform at render time.
    monkeypatch.setattr(sys, "platform", "win32")
    content = _rendered(PLATFORM_CELL)
    _check_snapshot(f"{PLATFORM_CELL.id}__win32", content)
    assert "powershell" in content  # the substitution actually fired


def test_enable_browser_is_autodetected_from_tools() -> None:
    # The matrix drives enable_browser via system_prompt_kwargs, bypassing the
    # auto-detection in base.py (enable_browser defaults to whether a
    # browser_tool_set is in `tools`). Pin that wiring with a real tool spec.
    llm = LLM(model=FAMILY_MODELS["anthropic"], usage_id="snapshot-llm")
    agent = Agent(llm=llm, tools=[Tool(name="browser_tool_set")])
    assert "<BROWSER_TOOLS>" in agent.static_system_message


def test_dynamic_context_with_secret_registry(tmp_path: Path) -> None:
    # Production builds the dynamic block via get_dynamic_context(state), which
    # merges secrets from state.secret_registry (agent.py) -- the <CUSTOM_SECRETS>
    # path and real assembly the bare dynamic_context property does not cover.
    # (working_dir is not rendered into the suffix, so tmp_path stays out of the
    # snapshot.)
    llm = LLM(model=FAMILY_MODELS["anthropic"], usage_id="snapshot-llm")
    agent = Agent(llm=llm, tools=[], agent_context=DYNAMIC_CONTEXT)
    state = ConversationState(
        id=uuid4(),
        agent=agent,
        workspace=LocalWorkspace(working_dir=str(tmp_path)),
    )
    state.secret_registry.update_secrets({"GITHUB_TOKEN": "unused-in-snapshot"})
    _check_snapshot(
        "dynamic_context__with_secret_registry",
        agent.get_dynamic_context(state) or "",
    )


def test_soul_default_snapshot(tmp_path: Path) -> None:
    """Full prompt with the built-in default soul matches the snapshot."""
    llm = LLM(model=FAMILY_MODELS["anthropic"], usage_id="snapshot-llm")
    agent = Agent(
        llm=llm,
        tools=[],
        system_prompt_kwargs={"soul_content": _DEFAULT_SOUL},
    )
    _check_snapshot("soul__default", agent.static_system_message)


def test_soul_custom_snapshot(tmp_path: Path) -> None:
    """Full prompt with a custom soul_content matches the snapshot."""
    llm = LLM(model=FAMILY_MODELS["anthropic"], usage_id="snapshot-llm")
    agent = Agent(
        llm=llm,
        tools=[],
        system_prompt_kwargs={
            "soul_content": "You are a tiny cat agent with toe beans."
        },
    )
    _check_snapshot("soul__custom", agent.static_system_message)


@pytest.mark.parametrize("cell", MATRIX, ids=[c.id for c in MATRIX])
def test_static_block_has_no_dynamic_content(cell: Cell) -> None:
    """Static block is the cache-shared prefix (#2827): per-run/per-env content
    there silently breaks cross-conversation cache hits. Paths/hostname are checked
    against this machine's concrete values -- the only ones injection could leak."""
    static = _build_agent(cell).static_system_message
    assert not ISO_DATETIME_RE.search(static), "static block contains a timestamp"
    assert os.getcwd() not in static, "static block contains the working directory"
    assert str(Path.home()) not in static, "static block contains the home directory"
    assert socket.gethostname() not in static, "static block contains the hostname"
