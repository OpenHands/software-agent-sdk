"""A small custom tool for S01 programs: `sleeper` waits, records when it ran,
and can declare resource keys or raise, the way a user's own tool would.

    from S01.sleeper import SleeperTool, TIMELINE

    Agent(llm=..., tools=[Tool(name="SleeperTool")], tool_concurrency_limit=4)

Arguments: label (str), seconds (float), resource ("" = declared, no keys;
"undeclared" = no declaration; anything else = that key), fail (bool).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Sequence

from pydantic import Field

from openhands.sdk import Action, Observation, TextContent, ToolDefinition
from openhands.sdk.tool import ToolExecutor, register_tool
from openhands.sdk.tool.tool import DeclaredResources


TIMELINE: list[dict] = []
_lock = threading.Lock()


class SleeperAction(Action):
    label: str = Field(description="Name of this call")
    seconds: float = Field(default=0.5, description="How long to wait")
    resource: str = Field(default="", description="Resource key to lock")
    fail: bool = Field(default=False, description="Raise instead of returning")


class SleeperObservation(Observation):
    label: str = ""

    @property
    def to_llm_content(self) -> Sequence[TextContent]:
        return [TextContent(text=f"slept: {self.label}")]


class SleeperExecutor(ToolExecutor[SleeperAction, SleeperObservation]):
    def __call__(self, action: SleeperAction, conversation=None) -> SleeperObservation:  # noqa: ARG002
        start = time.monotonic()
        time.sleep(action.seconds)
        with _lock:
            TIMELINE.append(
                {"label": action.label, "start": start, "end": time.monotonic()}
            )
        if action.fail:
            raise RuntimeError(f"sleeper {action.label} failed on purpose")
        return SleeperObservation(label=action.label)


class SleeperTool(ToolDefinition[SleeperAction, SleeperObservation]):
    @classmethod
    def create(cls, conv_state=None, **params) -> Sequence[ToolDefinition]:  # noqa: ARG003
        return [
            cls(
                description="Wait for a while and report the label.",
                action_type=SleeperAction,
                observation_type=SleeperObservation,
                executor=SleeperExecutor(),
            )
        ]

    def declared_resources(self, action: Action) -> DeclaredResources:
        assert isinstance(action, SleeperAction)
        if action.resource == "undeclared":
            return DeclaredResources(keys=(), declared=False)
        keys = (action.resource,) if action.resource else ()
        return DeclaredResources(keys=keys, declared=True)


register_tool("SleeperTool", SleeperTool)


def overlapped(a: str, b: str) -> bool:
    """Did the calls labelled a and b run at the same time?"""
    x = next(t for t in TIMELINE if t["label"] == a)
    y = next(t for t in TIMELINE if t["label"] == b)
    return x["start"] < y["end"] and y["start"] < x["end"]
