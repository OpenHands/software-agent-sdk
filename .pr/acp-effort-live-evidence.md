# Live evidence: `acp_effort` with the pinned adapters

Ran `.pr/acp_effort_live_check.py` with the pinned adapters
(`claude-agent-acp@0.63.0`, `codex-acp@2.2.2`), real Claude and ChatGPT
subscription logins, and a PATH without global adapters. It drives
`Conversation` through three turns: launch, then `switch_acp_effort`, then an
unknown level, then `switch_acp_model`. Effort is checked where each provider
records it: the Anthropic `/v1/messages` body (`output_config.effort`, captured
by a local proxy on `ANTHROPIC_BASE_URL`), and the Codex rollout `turn_context`.

## Claude Code: `acp_model="opus[1m]"`, `acp_effort="high"`

```
turn1                 model=opus[1m] effort=high levels=[default, low, medium, high, xhigh, max]
switch_acp_effort     acp_effort=low effort=low
turn2                 model=opus[1m] effort=low
switch_acp_effort("not-a-level") -> ValueError: not offered for the current model; choose one of: default, low, ...
switch_acp_model      model=haiku effort=None levels=[]   (pinned "low" skipped: Haiku offers no effort)
/v1/messages          (claude-opus-5, effort high, 2 msgs), (claude-opus-5, effort low, 5 msgs),
                      (claude-haiku-4-5-20251001, no effort, 5 msgs)
```

## Codex: `acp_model="gpt-6.1-sol"`, `acp_effort="max"`

```
turn1                 model=gpt-6.1-sol effort=max levels=[low, medium, high, xhigh, max, ultra]
switch_acp_effort     acp_effort=ultra effort=ultra
switch_acp_effort("not-a-level") -> ValueError: ... choose one of: low, medium, high, xhigh, max, ultra.
switch_acp_model      model=gpt-5.6-luna effort=medium levels=[low ... max]   (pinned "ultra" not offered: skipped)
rollout turn_context  (gpt-6.1-sol, max), (gpt-6.1-sol, ultra), (gpt-5.6-luna, medium)
```

## Discovery: `efforts_by_model`

`discover_acp_models` selects each model in its throwaway session to read the
levels it offers. These are local adapter calls with no model traffic, cached
for 5 minutes by `/api/acp/models`.

```
claude-code (10.3 s)  default=(opus[1m], xhigh)  haiku: []  every other model: default..max
codex (3.5 s)         default=(gpt-6.1-terra, high)  gpt-6-luna, gpt-5.6-luna: low..max  others: low..ultra
```

