# `/goal` shared-history demo

Proves that the `/goal` loop writes into the **same** conversation history as the
main chat — it drives the `Conversation` you pass in, it does **not** fork or
create a sidecar conversation.

## Run

```bash
# Deterministic, no network (scripted TestLLMs) — always works:
uv run python .pr/goal_shared_history.py

# Real agent doing real work (creates files, runs pytest) — opt in explicitly:
GOAL_DEMO_REAL=1 LLM_API_KEY=sk-... LLM_MODEL=gpt-5.5 \
    uv run python .pr/goal_shared_history.py
```

## What to look for

The script sends a normal "main conversation" message, then runs `run_goal(...)`
on the **same** `Conversation`. The `PROOF` section at the end shows:

```
same conversation id .............. True
only one Conversation object ...... True (no fork was created)
event log GREW in place ........... 3 -> 7
main-convo events still present ... True
goal objective is in THIS log ..... True
goal outcome ...................... complete (after 2 round(s))
```

i.e. the goal's objective, the agent's work, the judge-driven followups, and the
completion are all appended to the **one** `conversation.state.events` log under
the **one** `conversation.id` — alongside (not replacing) the main-convo events.

## Seeing what the LLM is doing

The demo passes `visualizer=None` to keep the proof output clean. To watch the
agent's activity:

- **Live**: drop `visualizer=None` (the default is `DefaultConversationVisualizer`),
  and every event — messages, tool calls, observations — prints as it happens.
- **After the fact**: the script ends with a `REPLAY` section that renders the
  saved history through the visualizer. Because every turn is persisted in
  `conversation.state.events`, you can replay it any time:

  ```python
  from openhands.sdk.conversation.visualizer import DefaultConversationVisualizer
  viz = DefaultConversationVisualizer()
  for event in conversation.state.events:
      viz.on_event(event)
  ```

In the deterministic (no-key) run the agent only emits scripted text, so you see
messages. In real mode (`GOAL_DEMO_REAL=1`) you also see the actual terminal
commands, file edits, and `pytest` output the agent runs.

## How this maps to the agent server

`run_goal` (used here) and the agent server's `EventService.start_goal` use the
same mechanism: they drive a single `Conversation`/`_conversation`, so every
event lands in that conversation's shared log and streams to subscribers. A
`POST /conversations/{id}/goal` endpoint runs the loop in the background on the
**existing** conversation — same history as the main chat.
