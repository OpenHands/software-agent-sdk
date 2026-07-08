"""Example: File-based subagent definitions.

Demonstrates the `.agents/agents/*.md` convention for defining subagents
that can be delegated to via the TaskToolSet. This script discovers and
registers the bundled agent definitions without calling an LLM.

For programmatic registration, see `42_file_based_subagents.py`.
For end-to-end delegation with an LLM, see `41_task_tool_set.py`.
"""

from pathlib import Path

from openhands.sdk.subagent import discover_agents, register_file_agents
from openhands.sdk.subagent.registry import get_registered_agent_definitions


script_dir = Path(__file__).resolve().parent

print("=" * 60)
print("File-based subagent discovery")
print("=" * 60)

discovered = discover_agents(script_dir, include_user=False)
print(f"Discovered {len(discovered)} project agent(s):")
for agent_def in discovered:
    print(f"  - {agent_def.name}: {agent_def.description}")
    if agent_def.tools:
        print(f"    tools={agent_def.tools}")

registered = register_file_agents(script_dir)
print(f"\nRegistered agent names: {registered}")

registered_defs = get_registered_agent_definitions()
registered_names = {agent_def.name for agent_def in registered_defs}
for name in registered:
    assert name in registered_names, f"Expected {name} in registry"

print("\nTo delegate work, give a main agent the TaskToolSet and ask it to")
print("call a subagent by name. For example:")
print('  conversation.send_message("Delegate to code-reviewer to ...")')
print("\nEXAMPLE_COST: 0")
