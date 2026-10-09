"""End-to-end agent run on DigitalOcean Inference via the `digitalocean/` provider.

Usage:
    DO_API_KEY=<DigitalOcean model access key or PAT> \
        uv run python .pr/do_agent_e2e.py <do-model-id> [<do-model-id> ...]
"""

import os
import sys
import tempfile
from pathlib import Path

from pydantic import SecretStr

from openhands.sdk import LLM, Agent, Conversation, Tool
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.terminal import TerminalTool


API_KEY = os.environ["DO_API_KEY"]
TASK = (
    "Create fib.py that prints the first 10 Fibonacci numbers, one per line. "
    "Run it with python3 and save its output to out.txt. Then stop."
)
EXPECTED = "\n".join(str(n) for n in [0, 1, 1, 2, 3, 5, 8, 13, 21, 34])

for model in sys.argv[1:]:
    workspace = Path(tempfile.mkdtemp(prefix="do-e2e-"))
    llm = LLM(
        model=f"digitalocean/{model}",
        api_key=SecretStr(API_KEY),
        usage_id=f"do-e2e-{model}",
        num_retries=1,
    )
    agent = Agent(
        llm=llm,
        tools=[Tool(name=TerminalTool.name), Tool(name=FileEditorTool.name)],
    )
    conversation = Conversation(
        agent=agent,
        workspace=str(workspace),
        max_iteration_per_run=15,
        visualizer=None,
    )
    try:
        conversation.send_message(TASK)
        conversation.run()
        out = workspace / "out.txt"
        ok = (workspace / "fib.py").exists() and out.exists()
        ok = ok and out.read_text().strip() == EXPECTED
        usage = llm.metrics.accumulated_token_usage
        tokens = (
            f"prompt={usage.prompt_tokens} completion={usage.completion_tokens}"
            if usage
            else "no usage"
        )
        status = conversation.state.execution_status
        print(
            f"{'PASS' if ok else 'FAIL'}  {model}: status={status} "
            f"files={sorted(p.name for p in workspace.iterdir())} {tokens}"
        )
    except Exception as e:  # noqa: BLE001
        print(f"FAIL  {model}: {type(e).__name__}: {str(e)[:300]}")
    finally:
        conversation.close()
