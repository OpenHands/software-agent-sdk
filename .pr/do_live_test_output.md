# DigitalOcean Inference live test output

Run on 2026-10-09 against `https://inference.do-ai.run/v1` from branch
`feat/digitalocean-provider` (commit c3a4c0108), with a DigitalOcean token
passed as `DO_API_KEY`. Output is verbatim.

## End-to-end agent conversation (`do_agent_e2e.py`)

Real SDK `Conversation` with the terminal and file-editor tools. The agent must
write `fib.py`, run it, and save the first 10 Fibonacci numbers to `out.txt`;
the script then checks the files on disk.

```
$ DO_API_KEY=... uv run python .pr/do_agent_e2e.py glm-5.3 anthropic-claude-sonnet-5.5 openai-gpt-5.6-luna
PASS  glm-5.3: status=ConversationExecutionStatus.FINISHED files=['fib.py', 'out.txt'] prompt=18773 completion=324
PASS  anthropic-claude-sonnet-5.5: status=ConversationExecutionStatus.FINISHED files=['fib.py', 'out.txt'] prompt=19173 completion=232
PASS  openai-gpt-5.6-luna: status=ConversationExecutionStatus.FINISHED files=['fib.py', 'out.txt'] prompt=25295 completion=493
```

## Single tool-calling request (`do_tool_call_smoke.py`)

One `litellm.completion` call per model with exactly the kwargs
`litellm_call_kwargs("digitalocean/<model>", None)` returns.

```
$ DO_API_KEY=... uv run python .pr/do_tool_call_smoke.py glm-5.3 anthropic-claude-sonnet-5.5 openai-gpt-5.6-luna
PASS  glm-5.3: tool_calls=['get_weather']
PASS  anthropic-claude-sonnet-5.5: tool_calls=['get_weather']
PASS  openai-gpt-5.6-luna: tool_calls=['get_weather']
```

## Known gaps

- LiteLLM has no pricing for DigitalOcean model IDs, so cost is reported as 0.
- Context window / max output tokens fall back to SDK defaults.
