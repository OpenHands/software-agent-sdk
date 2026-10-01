# Live check: profile `system_prompt` reaches the LLM

Real agent-server from this branch plus Agent Canvas's recording mock LLM
(`tests/e2e/mock-llm/scripts/mock-llm-server.py`), conversations launched by
`agent_profile_id` only. The system message is read from the agent-step
request the mock LLM recorded (the auto-title call is excluded).

```
$ .venv/bin/python .pr/system_prompt_e2e.py --mock <agent-canvas>/tests/e2e/mock-llm/scripts/mock-llm-server.py
capability advertised: True
rejected saves: {"empty prompt": 422, "ACP profile with prompt": 422}
stored system_prompt == PROMPT: True
materialize resolved_settings.system_prompt == PROMPT: True

[explorer] system message blocks: 2
  block 1 == profile prompt : True
  built-in <ROLE> present   : False
  suffix present            : True
  block 1 starts with       : 'You are a read-only repository explorer. Answer in one sentence.'

[plain] system message blocks: 2
  block 1 == profile prompt : False
  built-in <ROLE> present   : True
  suffix present            : False
  block 1 starts with       : '<SOUL>\nYou are OpenHands agent, a helpful AI assistant that can intera'
```
