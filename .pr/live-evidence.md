# Live evidence: Claude effort via claude-agent-acp

Ran `.pr/live_effort_check.py` against `@agentclientprotocol/claude-agent-acp`
(default `npx` command, local Claude login). The script logs each
`set_config_option` call and the server-reported `model`/`effort`
`currentValue`, then sends one prompt.

## Before (`main` @ 33d8aa29c), `opus[1m]/high`

```
set_config_option(model='opus[1m]/high')
  -> server state: {'model': 'opus[1m]/high', 'effort': 'default'}
ACP prompt failed with server error (attempt 1/4), retrying in 5s: [-32603] Internal error:
  There's an issue with the selected model (opus[1m]/high[1m]). It may not exist or you may not have access to it.
...
ConversationRunError: ... {"errorKind": "model_not_found"}
```

The combined id is accepted as a literal model, and every prompt fails.

## After (this PR), `opus[1m]/high`

```
set_config_option(model='opus[1m]')
  -> server state: {'model': 'opus[1m]', 'effort': 'default'}
set_config_option(effort='high')
  -> server state: {'model': 'opus[1m]', 'effort': 'high'}
OK
acp_model='opus[1m]/high' current_model_id='opus[1m]/high'
```

## After (this PR), `haiku/high` (model without effort support)

claude-agent-acp omits the `effort` option for Haiku. The model switch is kept
and only the effort is dropped with a warning:

```
WARNING: ACP server @agentclientprotocol/claude-agent-acp rejected effort='high' for model 'haiku'
  (Internal error); keeping the model at the server's default effort
set_config_option(model='haiku')
  -> server state: {'model': 'haiku'}
set_config_option(effort='high')
  -> rejected: Internal error
OK
acp_model='haiku/high' current_model_id='haiku/high'
```

Without the best-effort handling, the same run logged "Could not set model
'haiku/high' ... the session will use the server default" and reported
`current_model_id='opus[1m]'` while the session was actually running Haiku.

## Follow-up: Codex `max`/`ultra` and the reported model after a dropped effort

Ran the same `ACPAgent` → `Conversation` flow with the pinned adapters
(`claude-agent-acp@0.63.0`, `codex-acp@2.2.2`) and real subscription logins.
Effort is checked where the provider records it: the Anthropic `/v1/messages`
body (`output_config.effort`) and the Codex rollout `turn_context`.

codex-acp 2.2.2 advertises `low, medium, high, xhigh, max, ultra` for
`gpt-6.1-sol`. Before, `gpt-6.1-sol/max` was rejected as a model id
(`Invalid params`) and the session silently ran on the default model. After:

```
set_config_option(model='gpt-6.1-sol')            -> reasoning_effort: high
set_config_option(reasoning_effort='max')         -> reasoning_effort: max
switch_acp_model('gpt-6.1-sol/ultra')
set_config_option(reasoning_effort='ultra')       -> reasoning_effort: ultra
rollout turn_context: (gpt-6.1-sol, max), (gpt-6.1-sol, ultra)
```

A dropped effort now reports the model the session actually runs:

```
acp_model='haiku/high'
set_config_option(model='haiku')    -> ok
set_config_option(effort='high')    -> rejected: Internal error (warning logged)
current_model_id='haiku'            # was 'haiku/high'
API: model=claude-haiku-4-5-20251001 output_config=None
```
