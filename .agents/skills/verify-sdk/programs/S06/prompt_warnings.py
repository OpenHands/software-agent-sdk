"""Does building the built-in system prompts log a prompt-wording warning?

The project presets edit the default prompt's wording; an edit whose text the
default no longer contains is skipped with a ``Default prompt text changed``
warning. A fresh interpreter (so the sections are loaded, not cached) builds
the default, planning and both project prompts the way a program does and
reports the warnings it logged and what the worker's troubleshooting block says.
"""

import json
import subprocess
import sys

from sdk_verify import emit


CHILD = r"""
import json, logging
seen = []
class Capture(logging.Handler):
    def emit(self, record):
        seen.append(record.getMessage())
logging.getLogger().addHandler(Capture())
logging.getLogger().setLevel(logging.WARNING)

from openhands.sdk import Agent
from openhands.sdk.testing import TestLLM

prompts = {}
for name in ("system_prompt.j2", "system_prompt_planning.j2",
             "system_prompt_project_coordinator.j2",
             "system_prompt_project_worker.j2"):
    agent = Agent(llm=TestLLM.from_messages([], model="test-model"),
                  tools=[], system_prompt_filename=name)
    prompts[name] = agent.static_system_message

worker = prompts["system_prompt_project_worker.j2"]
block = worker.split("<TROUBLESHOOTING>", 1)[-1].split("</TROUBLESHOOTING>", 1)[0]
print(json.dumps({
    "prompts_built": len(prompts),
    "wording_warnings": [m for m in seen if "Default prompt text changed" in m],
    "worker_troubleshooting_mentions_report": "in your report" in block,
}))
"""

proc = subprocess.run(
    [sys.executable, "-c", CHILD], capture_output=True, text=True, timeout=300
)
lines = [line for line in proc.stdout.splitlines() if line.startswith("{")]
result = json.loads(lines[-1]) if lines else {"child_error": proc.stderr[-2000:]}
result["wording_warning_count"] = len(result.get("wording_warnings", []))
emit(result)
