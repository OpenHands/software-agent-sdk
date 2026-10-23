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
