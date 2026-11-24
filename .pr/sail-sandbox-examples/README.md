# Evidence: the two Sail examples, run for real

Both examples were run on 2026-11-24 from a Linux VM against real Sail sandboxes, with
the SDK at `main` (`15bbd077d`), the image CI built for that commit
(`ghcr.io/openhands/agent-server:15bbd07-python-amd64`) and `sail` 0.18.3 (the locked
version). Example 18 used `claude-haiku-4-5` through a LiteLLM proxy; example 19 ran
Claude Code through the same proxy.

| Example | Result | Wall time | Cost |
|---|---|---|---|
| `18_convo_with_sail_sandboxed_server.py` | exit 0 | 2 min 31 s (includes installing the SDK and the first import of the image) | $0.028 |
| `19_acp_agent_with_sail_sandbox.py` | exit 0 | 54 s | $0.118 |

What the logs show:

- `18_….txt`: the agent wrote `/workspace/project/FACTS.txt` in the sandbox, then deleted
  it in the second turn; the script printed `EXAMPLE_COST` and exited 0.
- `19_….txt`: Claude Code, started by the agent server inside the sandbox, listed
  `/workspace` and described it; the script printed `EXAMPLE_COST` and exited 0.

The logs are the scripts' own output with ANSI codes removed. They were scanned for
credentials before being added.
