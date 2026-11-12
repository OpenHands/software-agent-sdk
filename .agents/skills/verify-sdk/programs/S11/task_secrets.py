"""A parent conversation with a secret delegates a command that needs it.

The parent registers `PROBE_TOKEN` with `conversation.update_secrets(...)`,
runs `echo probe=${PROBE_TOKEN:+set} value=$PROBE_TOKEN` itself, then delegates the same
command to the programmatic sub-agent `prober` (create), and once more to the
same task id (resume). Each terminal observation says `probe=set` when the
secret reached that shell and `probe=` when it did not, and prints the value
so that masking is visible: the parent's observation shows `<secret-hidden>`,
and the secret's value appears in no observation or task result.
"""

import json

from s11_helpers import scripted, task_observations
from sdk_verify import emit, run_paths

from openhands.sdk import Agent, Conversation, Tool
from openhands.sdk.subagent import register_agent
from openhands.tools.task import TaskToolSet
from openhands.tools.terminal import TerminalTool


SECRET = "s3cr3t-probe-value-6724"
PROBE = "echo probe=${PROBE_TOKEN:+set} value=$PROBE_TOKEN"

paths = run_paths()
workspace = paths["workspace"]

prober_scripts = [
    [
        {"tool": "terminal", "args": {"command": PROBE}},
        {"finish": "probed"},
    ],
    [
        {"tool": "terminal", "args": {"command": PROBE}},
        {"finish": "probed again"},
    ],
]
sub_conversations = []


def make_prober(_llm):
    return Agent(
        llm=scripted(prober_scripts.pop(0), usage_id="prober"),
        tools=[Tool(name=TerminalTool.name)],
    )


register_agent("prober", make_prober, "Runs a probe command.")

parent = Conversation(
    agent=Agent(
        llm=scripted(
            [
                {"tool": "terminal", "args": {"command": PROBE}},
                {
                    "tool": "task",
                    "args": {"prompt": "Run the probe", "subagent_type": "prober"},
                },
                {
                    "tool": "task",
                    "args": {
                        "prompt": "Run the probe again",
                        "subagent_type": "prober",
                        "resume": "task_00000001",
                    },
                },
                {"finish": "done"},
            ],
            usage_id="parent",
        ),
        tools=[Tool(name=TerminalTool.name), Tool(name=TaskToolSet.name)],
    ),
    workspace=str(workspace),
    persistence_dir=str(paths["conversations"]),
    max_iteration_per_run=20,
    visualizer=None,
)
parent.update_secrets({"PROBE_TOKEN": SECRET})
parent.send_message("Probe the token here and in a sub-agent.")
parent.run()

parent_probe = None
for e in parent.state.events:
    obs = getattr(e, "observation", None)
    if obs is not None and type(obs).__name__ == "TerminalObservation":
        parent_probe = obs.text
        break
tasks = task_observations(parent.state.events)
status = parent.state.execution_status.value
parent.close()


def probe_value(text):
    if text is None:
        return None
    if "probe=set" in text:
        return "set"
    if "probe=" in text:
        return "unset"
    return "missing"


# The sub-agents' own terminal output is in their persisted event logs.
sub_probes = []
sub_texts = []
sub_root = paths["conversations"]
for f in sorted(sub_root.rglob("subagents/*/events/*.json")):
    event = json.loads(f.read_text())
    obs = event.get("observation") or {}
    if event.get("kind") != "ObservationEvent" or obs.get("kind") not in (
        "TerminalObservation",
        None,
    ):
        continue
    text = "".join(c.get("text", "") for c in obs.get("content") or [])
    if text.startswith("probe="):
        sub_probes.append(probe_value(text))
        sub_texts.append(text)

all_text = (parent_probe or "") + "".join(t["text"] for t in tasks) + "".join(sub_texts)
emit(
    {
        "ok": True,
        "execution_status": status,
        "parent_probe": probe_value(parent_probe),
        "task_statuses": [t["status"] for t in tasks],
        "sub_probes": sub_probes,
        "create_probe": sub_probes[0] if sub_probes else None,
        "resume_probe": sub_probes[1] if len(sub_probes) > 1 else None,
        "parent_masked": "<secret-hidden>" in (parent_probe or ""),
        "secret_leaked": SECRET in all_text,
    }
)
