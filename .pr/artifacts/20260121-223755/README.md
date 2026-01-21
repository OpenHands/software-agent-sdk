# Restore tool-change persistence probe

- conversation_id: `3fdb3160-98bb-48e1-aa02-9ce56805792a`
- persistence_dir: `/workspace/project/software-agent-sdk/.pr/artifacts/20260121-223755/conversations/3fdb316098bb48e1aa029ce56805792a`
- artifacts_root: `/workspace/project/software-agent-sdk/.pr/artifacts/20260121-223755`

## Snapshots

## Phase A (after create, before user message)

- Persistence dir: `/workspace/project/software-agent-sdk/.pr/artifacts/20260121-223755/conversations/3fdb316098bb48e1aa029ce56805792a`
- Telemetry dir: `/workspace/project/software-agent-sdk/.pr/artifacts/20260121-223755/telemetry_phase_a`

### base_state.json

- Agent tool specs (`agent.tools`): `['file_editor']`
- execution_status: `idle`

### events/

- Event files: `1`
- Event types: `['SystemPromptEvent']`

#### SystemPromptEvent (persisted, original)

- Tools in persisted SystemPromptEvent: `['file_editor', 'finish', 'think']`
- System prompt (first paragraph):

```
You are a helpful assistant for this probe test.
```

### telemetry logs

- Telemetry files: `0`
- No telemetry logs found (did an LLM call occur?)

## Phase A (after send_message, before run)

- Persistence dir: `/workspace/project/software-agent-sdk/.pr/artifacts/20260121-223755/conversations/3fdb316098bb48e1aa029ce56805792a`
- Telemetry dir: `/workspace/project/software-agent-sdk/.pr/artifacts/20260121-223755/telemetry_phase_a`

### base_state.json

- Agent tool specs (`agent.tools`): `['file_editor']`
- execution_status: `idle`

### events/

- Event files: `2`
- Event types: `['SystemPromptEvent', 'MessageEvent']`

#### SystemPromptEvent (persisted, original)

- Tools in persisted SystemPromptEvent: `['file_editor', 'finish', 'think']`
- System prompt (first paragraph):

```
You are a helpful assistant for this probe test.
```

### telemetry logs

- Telemetry files: `0`
- No telemetry logs found (did an LLM call occur?)

## Phase A (after run)

- Persistence dir: `/workspace/project/software-agent-sdk/.pr/artifacts/20260121-223755/conversations/3fdb316098bb48e1aa029ce56805792a`
- Telemetry dir: `/workspace/project/software-agent-sdk/.pr/artifacts/20260121-223755/telemetry_phase_a`

### base_state.json

- Agent tool specs (`agent.tools`): `['file_editor']`
- execution_status: `finished`

### events/

- Event files: `4`
- Event types: `['SystemPromptEvent', 'MessageEvent', 'ActionEvent', 'ObservationEvent']`

#### SystemPromptEvent (persisted, original)

- Tools in persisted SystemPromptEvent: `['file_editor', 'finish', 'think']`
- System prompt (first paragraph):

```
You are a helpful assistant for this probe test.
```

### telemetry logs

- Telemetry files: `1`
- Latest telemetry file: `gpt-4o-mini-1769035075.704-c725.json`
- Tools sent to LLM (per telemetry): `['file_editor', 'finish', 'think']`
- Prompt sent to LLM (system first paragraph):

```
You are a helpful assistant for this probe test.
```

- Prompt sent to LLM (last user message):

```
Hello. Please confirm you can see my tools.
```

## Phase B (after restore with terminal added, before user message)

- Persistence dir: `/workspace/project/software-agent-sdk/.pr/artifacts/20260121-223755/conversations/3fdb316098bb48e1aa029ce56805792a`
- Telemetry dir: `/workspace/project/software-agent-sdk/.pr/artifacts/20260121-223755/telemetry_phase_b`

### base_state.json

- Agent tool specs (`agent.tools`): `['file_editor', 'terminal']`
- execution_status: `finished`

### events/

- Event files: `5`
- Event types: `['SystemPromptEvent', 'MessageEvent', 'ActionEvent', 'ObservationEvent', 'SystemPromptUpdateEvent']`

#### SystemPromptEvent (persisted, original)

- Tools in persisted SystemPromptEvent: `['file_editor', 'finish', 'think']`
- System prompt (first paragraph):

```
You are a helpful assistant for this probe test.
```

#### SystemPromptUpdateEvent (persisted, after restore)

- Reason: `SystemPromptUpdateReason.TOOLS_CHANGED`
- Tools in SystemPromptUpdateEvent: `['file_editor', 'finish', 'terminal', 'think']`

### telemetry logs

- Telemetry files: `0`
- No telemetry logs found (did an LLM call occur?)

## Phase B (after send_message, before run)

- Persistence dir: `/workspace/project/software-agent-sdk/.pr/artifacts/20260121-223755/conversations/3fdb316098bb48e1aa029ce56805792a`
- Telemetry dir: `/workspace/project/software-agent-sdk/.pr/artifacts/20260121-223755/telemetry_phase_b`

### base_state.json

- Agent tool specs (`agent.tools`): `['file_editor', 'terminal']`
- execution_status: `idle`

### events/

- Event files: `6`
- Event types: `['SystemPromptEvent', 'MessageEvent', 'ActionEvent', 'ObservationEvent', 'SystemPromptUpdateEvent', 'MessageEvent']`

#### SystemPromptEvent (persisted, original)

- Tools in persisted SystemPromptEvent: `['file_editor', 'finish', 'think']`
- System prompt (first paragraph):

```
You are a helpful assistant for this probe test.
```

#### SystemPromptUpdateEvent (persisted, after restore)

- Reason: `SystemPromptUpdateReason.TOOLS_CHANGED`
- Tools in SystemPromptUpdateEvent: `['file_editor', 'finish', 'terminal', 'think']`

### telemetry logs

- Telemetry files: `0`
- No telemetry logs found (did an LLM call occur?)

## Phase B (after run)

- Persistence dir: `/workspace/project/software-agent-sdk/.pr/artifacts/20260121-223755/conversations/3fdb316098bb48e1aa029ce56805792a`
- Telemetry dir: `/workspace/project/software-agent-sdk/.pr/artifacts/20260121-223755/telemetry_phase_b`

### base_state.json

- Agent tool specs (`agent.tools`): `['file_editor', 'terminal']`
- execution_status: `finished`

### events/

- Event files: `8`
- Event types: `['SystemPromptEvent', 'MessageEvent', 'ActionEvent', 'ObservationEvent', 'SystemPromptUpdateEvent', 'MessageEvent', 'ActionEvent', 'ObservationEvent']`

#### SystemPromptEvent (persisted, original)

- Tools in persisted SystemPromptEvent: `['file_editor', 'finish', 'think']`
- System prompt (first paragraph):

```
You are a helpful assistant for this probe test.
```

#### SystemPromptUpdateEvent (persisted, after restore)

- Reason: `SystemPromptUpdateReason.TOOLS_CHANGED`
- Tools in SystemPromptUpdateEvent: `['file_editor', 'finish', 'terminal', 'think']`

### telemetry logs

- Telemetry files: `1`
- Latest telemetry file: `gpt-4o-mini-1769035076.285-e64a.json`
- Tools sent to LLM (per telemetry): `['file_editor', 'finish', 'terminal', 'think']`
- Prompt sent to LLM (system first paragraph):

```
You are a helpful assistant for this probe test.
```

- Prompt sent to LLM (last user message):

```
Now I added another tool. Please confirm you can see it.
```

## Phase C (after restore with same tools, before user message)

- Persistence dir: `/workspace/project/software-agent-sdk/.pr/artifacts/20260121-223755/conversations/3fdb316098bb48e1aa029ce56805792a`
- Telemetry dir: `/workspace/project/software-agent-sdk/.pr/artifacts/20260121-223755/telemetry_phase_c`

### base_state.json

- Agent tool specs (`agent.tools`): `['file_editor', 'terminal']`
- execution_status: `finished`

### events/

- Event files: `8`
- Event types: `['SystemPromptEvent', 'MessageEvent', 'ActionEvent', 'ObservationEvent', 'SystemPromptUpdateEvent', 'MessageEvent', 'ActionEvent', 'ObservationEvent']`

#### SystemPromptEvent (persisted, original)

- Tools in persisted SystemPromptEvent: `['file_editor', 'finish', 'think']`
- System prompt (first paragraph):

```
You are a helpful assistant for this probe test.
```

#### SystemPromptUpdateEvent (persisted, after restore)

- Reason: `SystemPromptUpdateReason.TOOLS_CHANGED`
- Tools in SystemPromptUpdateEvent: `['file_editor', 'finish', 'terminal', 'think']`

### telemetry logs

- Telemetry files: `0`
- No telemetry logs found (did an LLM call occur?)

## Phase C (after send_message, before run)

- Persistence dir: `/workspace/project/software-agent-sdk/.pr/artifacts/20260121-223755/conversations/3fdb316098bb48e1aa029ce56805792a`
- Telemetry dir: `/workspace/project/software-agent-sdk/.pr/artifacts/20260121-223755/telemetry_phase_c`

### base_state.json

- Agent tool specs (`agent.tools`): `['file_editor', 'terminal']`
- execution_status: `idle`

### events/

- Event files: `9`
- Event types: `['SystemPromptEvent', 'MessageEvent', 'ActionEvent', 'ObservationEvent', 'SystemPromptUpdateEvent', 'MessageEvent', 'ActionEvent', 'ObservationEvent', 'MessageEvent']`

#### SystemPromptEvent (persisted, original)

- Tools in persisted SystemPromptEvent: `['file_editor', 'finish', 'think']`
- System prompt (first paragraph):

```
You are a helpful assistant for this probe test.
```

#### SystemPromptUpdateEvent (persisted, after restore)

- Reason: `SystemPromptUpdateReason.TOOLS_CHANGED`
- Tools in SystemPromptUpdateEvent: `['file_editor', 'finish', 'terminal', 'think']`

### telemetry logs

- Telemetry files: `0`
- No telemetry logs found (did an LLM call occur?)

## Phase C (after run)

- Persistence dir: `/workspace/project/software-agent-sdk/.pr/artifacts/20260121-223755/conversations/3fdb316098bb48e1aa029ce56805792a`
- Telemetry dir: `/workspace/project/software-agent-sdk/.pr/artifacts/20260121-223755/telemetry_phase_c`

### base_state.json

- Agent tool specs (`agent.tools`): `['file_editor', 'terminal']`
- execution_status: `finished`

### events/

- Event files: `11`
- Event types: `['SystemPromptEvent', 'MessageEvent', 'ActionEvent', 'ObservationEvent', 'SystemPromptUpdateEvent', 'MessageEvent', 'ActionEvent', 'ObservationEvent', 'MessageEvent', 'ActionEvent', 'ObservationEvent']`

#### SystemPromptEvent (persisted, original)

- Tools in persisted SystemPromptEvent: `['file_editor', 'finish', 'think']`
- System prompt (first paragraph):

```
You are a helpful assistant for this probe test.
```

#### SystemPromptUpdateEvent (persisted, after restore)

- Reason: `SystemPromptUpdateReason.TOOLS_CHANGED`
- Tools in SystemPromptUpdateEvent: `['file_editor', 'finish', 'terminal', 'think']`

### telemetry logs

- Telemetry files: `1`
- Latest telemetry file: `gpt-4o-mini-1769035076.853-87a0.json`
- Tools sent to LLM (per telemetry): `['file_editor', 'finish', 'terminal', 'think']`
- Prompt sent to LLM (system first paragraph):

```
You are a helpful assistant for this probe test.
```

- Prompt sent to LLM (last user message):

```
What tools do you have available?
```

## What changed on disk?

### Files changed (persistence_dir)

- added: `['events/event-00004-d4007ff2-cf62-413a-83ed-4d371ea30adb.json', 'events/event-00005-9d21c5c9-69fe-4494-b975-c4a24f58ac65.json', 'events/event-00006-a9e5cb71-e254-4c69-8372-242333096833.json', 'events/event-00007-52c16a5b-daa6-4193-be61-0a80b5c763e6.json']`
- removed: `[]`
- changed: `['base_state.json']`

### base_state.json diff (after Phase A run -> after Phase B run)

```diff
--- base_state.json (before)
+++ base_state.json (after)
@@ -15,7 +15,7 @@
       "extended_thinking_budget": 200000,
       "litellm_extra_body": {},
       "log_completions": true,
-      "log_completions_folder": "/workspace/project/software-agent-sdk/.pr/artifacts/20260121-223755/telemetry_phase_a",
+      "log_completions_folder": "/workspace/project/software-agent-sdk/.pr/artifacts/20260121-223755/telemetry_phase_b",
       "max_input_tokens": 128000,
       "max_message_chars": 30000,
       "max_output_tokens": 16384,
@@ -45,6 +45,10 @@
     "tools": [
       {
         "name": "file_editor",
+        "params": {}
+      },
+      {
+        "name": "terminal",
         "params": {}
       }
     ]
```

## Key checks

### Phase A -> B (tool added)

- `base_state.json` agent.tools updated on restore: `['file_editor']` -> `['file_editor', 'terminal']`
- Persisted `SystemPromptEvent.tools` (original) unchanged: `['file_editor', 'finish', 'think']` == `['file_editor', 'finish', 'think']`
- ✅ `SystemPromptUpdateEvent` was persisted on restore with reason: `SystemPromptUpdateReason.TOOLS_CHANGED`
- ✅ `SystemPromptUpdateEvent.tools` contains new tool: `['file_editor', 'finish', 'terminal', 'think']`
- Tools sent to LLM after restore (per telemetry): `['file_editor', 'finish', 'terminal', 'think']`

### Phase C (second restore, same tools)

- Number of `SystemPromptUpdateEvent` in event log: `1`
- ✅ No new `SystemPromptUpdateEvent` emitted (tools unchanged, reusing existing update)
- Tools sent to LLM in Phase C (per telemetry): `['file_editor', 'finish', 'terminal', 'think']`
- ✅ LLM sees `terminal` tool in Phase C

