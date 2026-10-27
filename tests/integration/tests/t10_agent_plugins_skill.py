"""Test that an agent uses a skill delivered by an Agent Plugins package.

The live-LLM half of the Agent Plugins end-to-end coverage (issue #5156). The
deterministic suites assert that a package reaches `agent.agent_context.skills`;
this asserts that a model then actually uses what arrived that way, through the
portable layout (root `plugin.json`, `skills/<name>/SKILL.md`) rather than the
Claude Code one.

Mirrors `t09_invoke_skill.py`, which covers the same ground for a skill handed
to `AgentContext` directly.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from pydantic import SecretStr

from openhands.sdk import LLM, Agent, get_logger
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.conversation.visualizer import DefaultConversationVisualizer
from openhands.sdk.event.llm_convertible.action import ActionEvent
from openhands.sdk.plugin import PluginSource
from openhands.sdk.tool import Tool
from tests.integration.base import (
    BaseIntegrationTest,
    TestResult,
    ToolPresetType,
    get_tools_for_preset,
)
from tests.integration.early_stopper import EarlyStopperBase, EarlyStopResult


PLUGIN_NAME = "quiblet-tools"
SKILL_NAME = "quiblet-converter"
MANIFEST = {
    "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
    "name": PLUGIN_NAME,
    "version": "1.0.0",
    "description": "Converts fictional quiblet units to meters.",
}
INSTRUCTION = (
    "How many meters are 4 quiblets? Quiblet units are fictional — the "
    "conversion factors are only available through the skill made "
    "available to you. Use the skill to produce the exact numeric answer."
)
SKILL_CONTENT = """# Quiblet Converter

Converts fictional quiblet units (quiblets, dwerps, mardles) to meters.

## How to use

Run `python scripts/convert.py <amount> <unit>` from this skill's
directory. It prints the answer in meters. Unit conversion factors are
non-standard and must NOT be guessed — always use the script.
"""
SKILL_MD = f"""---
name: {SKILL_NAME}
description: Convert quiblet units (quiblets, dwerps, mardles) to meters. Required \
for any quiblet-unit question — never guess.
---

{SKILL_CONTENT}"""
CONVERT_SCRIPT = '''"""Convert quiblet units to meters."""

from __future__ import annotations

import sys


FACTORS_TO_METERS = {
    "quiblets": 1.6180,
    "dwerps": 0.0577,
    "mardles": 23.14,
}


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: convert.py <amount> <unit>", file=sys.stderr)
        return 2
    amount = float(argv[1])
    unit = argv[2].lower().rstrip("s") + "s"
    if unit not in FACTORS_TO_METERS:
        print(f"unknown unit: {argv[2]}", file=sys.stderr)
        return 1
    print(f"{amount * FACTORS_TO_METERS[unit]:.4f} m")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
'''
EXPECTED_METERS = 4 * 1.6180  # 6.4720


logger = get_logger(__name__)


class AgentPluginsSkillTest(BaseIntegrationTest):
    """Assert the agent calls `invoke_skill` for a plugin-supplied skill."""

    INSTRUCTION: str = INSTRUCTION

    def __init__(
        self,
        instruction: str,
        llm_config: dict[str, Any],
        instance_id: str,
        workspace: str,
        tool_preset: ToolPresetType = "default",
    ):
        # Re-run the base constructor logic, but attach the skill as an Agent
        # Plugins package instead of putting it on the AgentContext by hand.
        # Plugins load lazily on the first run, i.e. after setup() has written
        # the package to disk.
        self.instruction = instruction
        self.llm_config = llm_config
        self.workspace = workspace
        self.instance_id = instance_id
        self.tool_preset = tool_preset

        api_key = os.getenv("LLM_API_KEY")
        base_url = os.getenv("LLM_BASE_URL")
        if not api_key or not base_url:
            raise ValueError("LLM_API_KEY and LLM_BASE_URL must be set.")

        self.llm = LLM(
            **{
                **llm_config,
                "base_url": base_url,
                "api_key": SecretStr(api_key),
            },
            usage_id="test-llm",
        )

        # The package lives OUTSIDE the workspace so the agent cannot discover
        # `scripts/convert.py` by exploring its cwd — it must follow the
        # absolute path appended by `invoke_skill`'s location footer.
        self.plugin_dir = (
            Path(workspace).parent / f"{instance_id}_plugin_cache" / PLUGIN_NAME
        )
        self.skill_dir = self.plugin_dir / "skills" / SKILL_NAME

        self.agent = Agent(llm=self.llm, tools=self.tools, condenser=self.condenser)
        self.collected_events = []
        self.llm_messages = []
        self.log_file_path = os.path.join(workspace, f"{instance_id}_agent_logs.txt")
        self.early_stopper: EarlyStopperBase | None = None
        self.early_stop_result: EarlyStopResult | None = None

        self.conversation = LocalConversation(
            agent=self.agent,
            workspace=self.workspace,
            plugins=[PluginSource(source=str(self.plugin_dir))],
            callbacks=[self.conversation_callback],
            visualizer=DefaultConversationVisualizer(),
            max_iteration_per_run=self.max_iteration_per_run,
        )

    @property
    def tools(self) -> list[Tool]:
        return get_tools_for_preset(self.tool_preset, enable_browser=False)

    def setup(self) -> None:
        """Write the Agent Plugins package: manifest, skill, bundled script."""
        scripts_dir = self.skill_dir / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        (self.plugin_dir / "plugin.json").write_text(json.dumps(MANIFEST, indent=2))
        (self.skill_dir / "SKILL.md").write_text(SKILL_MD)
        (scripts_dir / "convert.py").write_text(CONVERT_SCRIPT)

    def verify_result(self) -> TestResult:
        # 0. The skill reached the agent through the package, not some other
        #    source — otherwise the rest would pass without testing plugins.
        context = self.conversation.agent.agent_context
        sources = {
            skill.name: skill.source for skill in (context.skills if context else [])
        }
        if SKILL_NAME not in sources:
            return TestResult(
                success=False,
                reason=(
                    f"Skill '{SKILL_NAME}' never reached the agent context. "
                    f"Present: {sorted(sources) or '<none>'}."
                ),
            )
        source = sources[SKILL_NAME] or ""
        if not source.startswith(str(self.plugin_dir.resolve())):
            return TestResult(
                success=False,
                reason=(
                    f"Skill '{SKILL_NAME}' did not come from the plugin package: "
                    f"{source!r}."
                ),
            )

        action_events = [e for e in self.collected_events if isinstance(e, ActionEvent)]

        # 1. Agent invoked the plugin's skill.
        invoked = [
            e
            for e in action_events
            if e.tool_name == "invoke_skill"
            and getattr(e.action, "name", "").strip() == SKILL_NAME
        ]
        if not invoked:
            called_tools = sorted({e.tool_name for e in action_events})
            return TestResult(
                success=False,
                reason=(
                    f"Agent never called invoke_skill(name='{SKILL_NAME}'). "
                    f"Tool calls observed: {called_tools or '<none>'}."
                ),
            )

        # 2. After invocation, the agent reached the script bundled in the
        #    package. The package is outside the workspace, so this is only
        #    possible via the footer path.
        invoke_idx = self.collected_events.index(invoked[0])
        touched = False
        for e in self.collected_events[invoke_idx + 1 :]:
            if not isinstance(e, ActionEvent):
                continue
            blob = str(getattr(e.action, "model_dump", lambda: {})())
            if "scripts/" in blob:
                touched = True
                break
        if not touched:
            return TestResult(
                success=False,
                reason=(
                    "Agent invoked the plugin skill but never touched "
                    "`scripts/` afterwards — the location footer is not "
                    "being used."
                ),
            )

        return TestResult(
            success=True,
            reason=(
                f"Agent invoked '{SKILL_NAME}' from plugin '{PLUGIN_NAME}' and "
                f"reached its bundled script."
            ),
        )
