#!/usr/bin/env python3
"""Real-world test for terminal query filtering fix.

Tests that terminal query sequences (DSR, OSC 11, etc.) are filtered from
captured terminal output before display, preventing visible escape code garbage.

Usage:
    # With All-Hands LLM proxy:
    LLM_BASE_URL="https://llm-proxy.eval.all-hands.dev" LLM_API_KEY="$LLM_API_KEY" \
        uv run python .pr/test_real_world.py

    # With direct API:
    LLM_API_KEY="your-key" uv run python .pr/test_real_world.py

See: https://github.com/OpenHands/software-agent-sdk/issues/2244
"""

import os
import sys

from openhands.sdk import LLM, Agent, Conversation, Tool
from openhands.tools.terminal import TerminalTool


print("=" * 60)
print("REAL-WORLD TEST: Terminal Query Filtering Fix")
print("=" * 60)
print(f"stdin.isatty(): {sys.stdin.isatty()}")
print(f"stdout.isatty(): {sys.stdout.isatty()}")
print()

llm = LLM(
    model=os.environ.get("LLM_MODEL", "claude-sonnet-4-20250514"),
    api_key=os.environ["LLM_API_KEY"],
    base_url=os.environ.get("LLM_BASE_URL"),
)

agent = Agent(llm=llm, tools=[Tool(name=TerminalTool.name)])
conversation = Conversation(agent=agent, workspace="/tmp")

# Commands with spinners (like gh) send terminal queries that would
# cause visible garbage if not filtered
print(">>> Sending message to agent...")
print(">>> The gh command sends terminal queries - these should be filtered")
print()

conversation.send_message("Run: gh pr list --repo OpenHands/openhands --limit 3")
conversation.run()
conversation.close()

print()
print("=" * 60)
print("TEST COMPLETE")
print("=" * 60)
print()
print("SUCCESS CRITERIA:")
print("  1. NO visible escape codes (^[[...R, rgb:...) in the output above")
print("  2. NO garbage on the shell prompt after this script exits")
print("  3. Colors in the gh output should still be visible (if terminal supports)")
print()
print("The fix filters terminal QUERY sequences while preserving formatting.")
