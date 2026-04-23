"""Test that an agent uses the `invoke_skill` tool when a relevant
AgentSkills-format skill is loaded.

Regression coverage for the `invoke_skill` built-in tool (issue #2824 /
PR #2835). Without this test, a silent change to the tool description,
`<available_skills>` block, or auto-attach logic could stop models from
picking up the tool in real conversations.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from pydantic import SecretStr

from openhands.sdk import LLM, Agent, AgentContext, get_logger
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.conversation.visualizer import DefaultConversationVisualizer
from openhands.sdk.event.llm_convertible.action import ActionEvent
from openhands.sdk.skills import Skill
from openhands.sdk.tool import Tool
from tests.integration.base import (
    BaseIntegrationTest,
    TestResult,
    ToolPresetType,
    get_tools_for_preset,
)
from tests.integration.early_stopper import EarlyStopperBase, EarlyStopResult


SKILL_NAME = "frobnitz-converter"
INSTRUCTION = (
    "How many meters are 7 frobs? Frobnitz units are fictional — the "
    "conversion factors are only available through the skill made "
    "available to you. Use the skill to produce the exact numeric answer."
)
SKILL_CONTENT = """# Frobnitz Converter

Converts fictional frobnitz units (frobs, snargs, blarps) to meters.

## How to use

Run `python scripts/convert.py <amount> <unit>` from this skill's
directory. It prints the answer in meters. Unit conversion factors are
non-standard and must NOT be guessed — always use the script.
"""
CONVERT_SCRIPT = '''"""Convert frobnitz units to meters."""

from __future__ import annotations

import sys


FACTORS_TO_METERS = {
    "frobs": 3.1415,
    "snargs": 0.0271828,
    "blarps": 42.42,
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
EXPECTED_METERS = 7 * 3.1415  # 21.9905


logger = get_logger(__name__)


class InvokeSkillTest(BaseIntegrationTest):
    """Assert the agent calls `invoke_skill` for a relevant skill."""

    INSTRUCTION: str = INSTRUCTION

    def __init__(
        self,
        instruction: str,
        llm_config: dict[str, Any],
        instance_id: str,
        workspace: str,
        tool_preset: ToolPresetType = "default",
    ):
        # Re-run the base constructor logic but build the Agent with an
        # `agent_context` that includes an AgentSkills-format skill, so the
        # `invoke_skill` tool auto-attaches.
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

        # Skill lives OUTSIDE the workspace so the agent cannot discover
        # `scripts/convert.py` by exploring its cwd — it must rely on the
        # absolute path appended by `invoke_skill`'s location footer.
        self.skill_dir = (
            Path(workspace).parent / f"{instance_id}_skill_cache" / SKILL_NAME
        )
        self.skill_md = self.skill_dir / "SKILL.md"

        self.agent = Agent(
            llm=self.llm,
            tools=self.tools,
            condenser=self.condenser,
            agent_context=AgentContext(skills=[self._make_skill()]),
        )
        self.collected_events = []
        self.llm_messages = []
        self.log_file_path = os.path.join(workspace, f"{instance_id}_agent_logs.txt")
        self.early_stopper: EarlyStopperBase | None = None
        self.early_stop_result: EarlyStopResult | None = None

        self.conversation = LocalConversation(
            agent=self.agent,
            workspace=self.workspace,
            callbacks=[self.conversation_callback],
            visualizer=DefaultConversationVisualizer(),
            max_iteration_per_run=self.max_iteration_per_run,
        )

    def _make_skill(self) -> Skill:
        return Skill(
            name=SKILL_NAME,
            content=SKILL_CONTENT,
            description=(
                "Convert frobnitz units (frobs, snargs, blarps) to meters. "
                "Required for any frobnitz-unit question — never guess."
            ),
            source=str(self.skill_md),
            is_agentskills_format=True,
        )

    @property
    def tools(self) -> list[Tool]:
        return get_tools_for_preset(self.tool_preset, enable_browser=False)

    def setup(self) -> None:
        """Materialize the skill AND its bundled script on disk, so the
        location footer resolves AND the agent has a real file to reach
        when it follows the footer."""
        scripts_dir = self.skill_dir / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        self.skill_md.write_text(SKILL_CONTENT)
        (scripts_dir / "convert.py").write_text(CONVERT_SCRIPT)

    def verify_result(self) -> TestResult:
        action_events = [e for e in self.collected_events if isinstance(e, ActionEvent)]

        # 1. Agent invoked the skill.
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

        # 2. After invocation, the agent tried to view or run a bundled
        #    resource (scripts/ or references/). Skill lives outside the
        #    workspace, so this is only possible via the footer path.
        invoke_idx = self.collected_events.index(invoked[0])
        touched = False
        for e in self.collected_events[invoke_idx + 1 :]:
            if not isinstance(e, ActionEvent):
                continue
            blob = str(getattr(e.action, "model_dump", lambda: {})())
            if "scripts/" in blob or "references/" in blob:
                touched = True
                break
        if not touched:
            return TestResult(
                success=False,
                reason=(
                    "Agent invoked the skill but never touched `scripts/` or "
                    "`references/` afterwards — the location footer is not "
                    "being used."
                ),
            )

        return TestResult(
            success=True,
            reason=(
                f"Agent invoked '{SKILL_NAME}' and reached a bundled "
                f"resource via the footer path."
            ),
        )
