"""The built-in sub-agents from openhands-tools and the task tool's
description, as a program that calls register_builtins_agents() sees them.

Usage: builtins.py
"""

import warnings

from sdk_verify import emit

from openhands.sdk.subagent import get_agent_factory, get_registered_agent_definitions
from openhands.tools import register_builtins_agents
from openhands.tools.task import TaskToolSet
from openhands.tools.task.definition import TASK_TOOL_EXAMPLES


without_browser = register_builtins_agents(enable_browser=False)
with_browser = register_builtins_agents(enable_browser=True)
registered = sorted(d.name for d in get_registered_agent_definitions())

deprecated: dict[str, str] = {}
warned: dict[str, bool] = {}
for old in ("default", "default cli mode", "explore", "bash"):
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        deprecated[old] = get_agent_factory(old).definition.name
    warned[old] = any(
        "deprecat" in type(w.message).__name__.lower()
        or "deprecat" in str(w.message).lower()
        for w in caught
    )

description = TaskToolSet.create(conv_state=None)[0].description  # type: ignore[arg-type]
# Each built-in's usage example, keyed by the registered agent name.
example_markers = {
    "code-explorer": "Multi-step exploration",
    "bash-runner": "Running tests",
    "web-researcher": "Research information on a website",
    "general-purpose": "Perform a multi-step task",
}
emit(
    {
        "ok": True,
        "registered_without_browser": sorted(without_browser),
        "registered_later_with_browser": sorted(with_browser),
        "registered": registered,
        "deprecated_maps_to": deprecated,
        "deprecated_warned": warned,
        "description_lists": {
            name: f"**{name}**" in description for name in registered
        },
        "example_in_description": {
            name: marker in description for name, marker in example_markers.items()
        },
        "example_keys": sorted(TASK_TOOL_EXAMPLES),
    }
)
