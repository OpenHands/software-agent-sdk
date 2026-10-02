# Live check: profile `persona` reaches the LLM, capability guidance kept

A real agent-server from this branch plus Agent Canvas's recording mock LLM (`tests/e2e/mock-llm/scripts/mock-llm-server.py`), with conversations launched by `agent_profile_id` only. The system message is read from the agent-step request the mock LLM recorded. The default tools apply, and the server adds the browser.

```
$ .venv/bin/python .pr/persona_e2e.py --mock <agent-canvas>/tests/e2e/mock-llm/scripts/mock-llm-server.py
capability advertised: True
rejected saves: {"empty prompt": 422, "ACP profile with prompt": 422}
stored persona == PERSONA: True
materialize resolved_settings.persona == PERSONA: True

[explorer] system message blocks: 2
  block 1 starts with persona: True
  kept ['<MEMORY>', '<SECURITY>', '<SECURITY_RISK_ASSESSMENT>', '<EXTERNAL_SERVICES>', '<PROCESS_MANAGEMENT>']: [True, True, True, True, True]
  replaced ['<SOUL>', '<ROLE>', '<CODE_QUALITY>', '<VERSION_CONTROL>', '<PULL_REQUESTS>'] present: [False, False, False, False, False]
  browser guidance present   : True
  suffix present             : True

[plain] system message blocks: 2
  block 1 starts with persona: False
  kept ['<MEMORY>', '<SECURITY>', '<SECURITY_RISK_ASSESSMENT>', '<EXTERNAL_SERVICES>', '<PROCESS_MANAGEMENT>']: [True, True, True, True, True]
  replaced ['<SOUL>', '<ROLE>', '<CODE_QUALITY>', '<VERSION_CONTROL>', '<PULL_REQUESTS>'] present: [True, True, True, True, True]
  browser guidance present   : True
  suffix present             : False
```
