# SnapshotReplayAgent — Technical Documentation

---

## 1. Introduction

### The Root Problem: Testing Agentic Systems Is Expensive and Non-Deterministic

Modern AI agents powered by large language models (LLMs) pose a fundamental challenge for regression testing. Each test run involves:

- **Real LLM calls** — slow, rate-limited, and expensive.
- **Non-determinism** — the LLM may produce different tool calls on every run, even for the same prompt.
- **Environment coupling** — actual tool execution (file system, terminal, browser) changes state, making repeated runs order-dependent.

As a result, verifying that a refactored tool, a changed system prompt, or a new environment configuration does **not break agent behavior** requires either expensive end-to-end runs or brittle mocks that don't reflect real tool interactions.

The specific question this work addresses is:

> **Can we replay a previously-recorded agent session — re-executing the exact same sequence of LLM decisions against the live environment — and detect when the environment's response has diverged from the original run?**

This enables a class of tests called **agentic regression tests**: deterministic, LLM-free, environment-aware replays that surface behavioral drift in tools without requiring an LLM at all.

---

## 2. Approach

### Why Not Mock the LLM?

The first candidate approach is to intercept LLM network calls (e.g., via LiteLLM callbacks) and return pre-recorded LLM responses. This has two structural problems:

1. **Fragile coupling to response format** — recorded LLM JSON payloads are deeply tied to model version, tool schema shape, and prompt structure. Any refactor breaks the mocks.
2. **Misses the real goal** — we don't want to test whether the LLM still outputs the same tokens. We want to test whether **the tools still behave the same** when given the same inputs.

### Event-Sourced Replay: The Adopted Strategy

The OpenHands SDK's `Conversation` object already captures every agent interaction as a structured event stream:

| Event Type | Meaning |
|---|---|
| `ActionEvent` | The LLM decided to call a tool with these arguments |
| `ObservationEvent` | The tool responded with this output |
| `MessageEvent` | The agent emitted a final message to the user |

The entire history of a run — decisions, executions, observations — is persisted as an ordered list of these events. This is the **snapshot**.

The replay strategy is:

1. **Record** a successful agent run. Its event log is saved to disk (via `persistence_dir`) or kept in memory.
2. **Replay** by walking the recorded `ActionEvent`s in order, re-executing each tool call against the live environment.
3. **Compare** each live `ObservationEvent` against the corresponding recorded one.
4. **Log** any discrepancies (drift) to a structured JSON file for analysis.

The LLM is never called. The agent replays the recorded decisions but always collects fresh observations from the real environment.

```
  Snapshot (recorded run)
  ┌─────────────────────────────────────────────────┐
  │  ActionEvent(tool=bash, cmd="ls /app")           │
  │  ObservationEvent(result="main.py  tests/")      │  ─── expected
  │  ActionEvent(tool=bash, cmd="cat main.py")       │
  │  ObservationEvent(result="import flask ...")     │  ─── expected
  └─────────────────────────────────────────────────┘

  Replay run (no LLM)
  ┌─────────────────────────────────────────────────┐
  │  ActionEvent(tool=bash, cmd="ls /app")           │  ◄── replayed
  │  ObservationEvent(result="main.py  tests/")      │  ─── actual ✓ No drift
  │  ActionEvent(tool=bash, cmd="cat main.py")       │  ◄── replayed
  │  ObservationEvent(result="import fastapi ...")   │  ─── actual ✗ DRIFT!
  └─────────────────────────────────────────────────┘
```

---

## 3. Implementation

### 3.1 Architecture Overview

`SnapshotReplayAgent` extends the base `Agent` class, which means it inherits:

- The complete tool registry (`tools_map`)
- The parallel tool executor (`_parallel_executor`)
- The `_execute_action_event()` runner used by production agents
- All conversation lifecycle hooks

The only thing it overrides is the `step()` method — replacing the LLM call with event-sourced replay logic.

```
  Agent (base)
    ├── llm: LLM                      (inherited, but not called in replay mode)
    ├── tools_map: dict[str, Tool]    (inherited, used for live tool execution)
    ├── _parallel_executor            (inherited, runs tools concurrently)
    └── step()                        ◄── OVERRIDDEN by SnapshotReplayAgent
          │
          ▼
  SnapshotReplayAgent
    ├── replay_mode: bool
    ├── replay_persistence: str | None
    ├── replay_conversation_id: str | None (optional target session ID)
    ├── replay_snapshot: list[Event] | None
    ├── drift_log_path: str | None
    ├── _actual_replay_mode: bool      (private — True only if snapshot loaded)
    ├── _replay_events: list[Event]    (ordered ActionEvents + MessageEvents)
    ├── _current_event_idx: int        (cursor into _replay_events)
    └── _expected_observations: dict  (action_id → [ObservationEvent])
```

### 3.2 Snapshot Loading (`model_post_init`)

When the agent is constructed, `model_post_init` fires and attempts to load the snapshot. The loading follows a priority chain and includes path resolution:

```
replay_mode = True?
    │
    ├─► replay_persistence set?
    │       ├─► replay_conversation_id provided?
    │       │     └─► Resolve exact path via BaseConversation.get_persistence_dir()
    │       │
    │       └─► (fallback) Discovery Logic:
    │             └─► Search for nested conversation directories containing 'events/'
    │
    ├─► (fallback) replay_snapshot provided and non-empty?
    │       └─► Use the in-memory list of Event objects directly
    │
    └─► Neither found?
            └─► Log warning, fall back to real LLM calls (_actual_replay_mode = False)
```

On success, `_prepare_replay_events()` splits the loaded events into two internal structures:

- **`_replay_events`** — an ordered list of `ActionEvent`s and agent `MessageEvent`s that the agent will step through one at a time.
- **`_expected_observations`** — a dict keyed by `action_id`, mapping each original action to its recorded `ObservationEvent`(s). These are the *ground truth* for drift comparison.

> **Note on Timing:** Since loading happens during agent initialization, the recording phase must be fully complete and flushed to disk before the replaying agent is instantiated.

### 3.3 The `step()` Method — Replay Execution Loop

Each call to `step()` advances the replay by one event. The logic is:

```
step(conversation, on_event)
    │
    ├─► Not in replay mode? → delegate to super().step() (real LLM)
    │
    ├─► Pending unmatched actions in state?
    │       └─► Execute them with drift check (resume in-flight batch)
    │
    ├─► Replay script exhausted?
    │       └─► Set status = FINISHED, return
    │
    ├─► Next event is ActionEvent?
    │       ├─► Strip LLM Reasoning (summary, thought, reasoning_content)
    │       │   (Ensures clean logs focusing only on tool execution)
    │       └─► Call _execute_actions_with_drift_check()
    │
    └─► Next event is MessageEvent?
            ├─► If source == 'agent', strip LLM reasoning fields
            ├─► Emit the message via on_event()
            └─► Set status = FINISHED
```

> **Key design note:** The replay agent intentionally strips LLM-specific metadata (like `summary` and `thought`) before replaying events. This ensures that replayed logs are concise and focused on the tool interaction, preventing noisy LLM reasoning from cluttering regression test outputs.

### 3.4 Action Execution with Drift Detection (`_execute_actions_with_drift_check`)

This is the core of the replay engine:

```python
def _execute_actions_with_drift_check(self, conversation, action_events, on_event):
    # 1. Execute actions via the inherited parallel executor
    batch = _ActionBatch.prepare(
        action_events,
        state=state,
        executor=self._parallel_executor,
        tool_runner=lambda ae: self._execute_action_event(conversation, ae),
        tools=self.tools_map,
    )
    batch.emit(on_event)          # emits ObservationEvents to the conversation

    # 2. Compare results against snapshot if drift_log_path is configured
    if self.drift_log_path:
        for ae in action_events:
            actual_obs  = batch.results_by_id.get(ae.id, [])
            expected_obs = self._expected_observations.get(ae.id, [])

            drift = "No drift" if not self._check_drift(actual_obs, expected_obs)
                   else {"Drift Present": True, "Expected": [...]}

            log_entry = {
                "Action": ae.model_dump(mode="json"),
                "Actual Observation": [...],
                "Drift from expected observation": drift,
            }
            f.write(json.dumps(log_entry) + "\n")

    # 3. Finalize the batch (handles iterative refinement, marks finished)
    batch.finalize(...)
```

### 3.5 Drift Detection (`_check_drift`)

Two observation lists are considered drifted if:

- They have **different lengths** (tool returned more or fewer observations), OR
- Any corresponding pair differs in **content** after stripping run-specific metadata fields.

Metadata fields excluded from comparison:

| Field | Reason |
|---|---|
| `id` | UUIDs generated fresh each run |
| `timestamp` | Wall-clock time differs |
| `action_id` | Links to current run's action UUID |
| `tool_call_id` | Links to current run's tool call UUID |

Everything else — the actual tool output payload — is compared directly.

### 3.6 Drift Log Format

Each action produces one JSONL line in the drift log:

**No drift:**
```json
{
  "Action": { "tool_name": "bash", "action": { "command": "ls /app" }, ... },
  "Actual Observation": [{ "observation": { "output": "main.py  tests/" }, ... }],
  "Drift from expected observation": "No drift"
}
```

**Drift detected:**
```json
{
  "Action": { "tool_name": "bash", "action": { "command": "cat main.py" }, ... },
  "Actual Observation": [{ "observation": { "output": "import fastapi ..." }, ... }],
  "Drift from expected observation": {
    "Drift Present": true,
    "Expected": [{ "observation": { "output": "import flask ..." }, ... }]
  }
}
```

---

## 4. Feature Reference

### 4.1 Dual Snapshot Source

| Source | Field | When to Use |
|---|---|---|
| On-disk persistence | `replay_persistence: str` | Production regression tests. Pass the `persistence_dir` from a previous `Conversation` run. |
| In-memory list | `replay_snapshot: list[Event]` | Unit tests, programmatic test fixtures, or when events are constructed in code. |

The persistence directory takes priority. The in-memory list is used as a fallback.

### 4.2 Graceful Fallback to Real LLM

If `replay_mode=True` but no snapshot is found (empty list or failed disk load), the agent falls back to real LLM execution and logs a warning. This means it is always safe to set `replay_mode=True` — it will never silently fail.

### 4.3 Clean Replay Logs

The agent automatically strips LLM-specific metadata (summaries, internal reasoning content, thinking blocks) from `ActionEvent`s and `MessageEvent`s before replaying them. This results in a cleaner UI/terminal output that highlights only the replayed tool actions and their live observations.

### 4.4 Session Discovery and Target IDs

The agent supports two ways to locate a specific session within a shared persistence repository:
1. **Automatic Discovery**: If only a base path is provided, the agent recursively searches for the first subdirectory containing an `events/` folder.
2. **Explicit Target**: If `replay_conversation_id` is provided, the agent calculates the exact path using the SDK's standard conversation directory logic.

### 4.5 Parallel Tool Execution Preserved

The replay reuses the base `Agent`'s `_parallel_executor` and `_ActionBatch` machinery. Tools that are safe to run in parallel continue to run in parallel during replay, matching the actual production concurrency profile.

### 4.5 Structured Drift Logging (JSONL)

The drift log is append-only JSONL (one JSON object per line, one per action). Each entry contains:
- The full serialized `Action`
- The full serialized live `ObservationEvent`(s)
- Either `"No drift"` or a dict with `"Drift Present": true` and the `"Expected"` observations

This format is designed to be machine-parseable for CI dashboards and human-readable for debugging.

### 4.6 Non-Deterministic Field Exclusion

Timestamps, UUIDs, and run-linking IDs are automatically stripped before drift comparison, eliminating false positives from fields that are expected to change between runs.

### 4.7 Iterative Refinement Support

The replay respects iterative refinement logic via `batch.finalize(check_iterative_refinement=...)`, so replays of sessions that used self-correction loops behave consistently with production runs.

---

## 5. Test Cases

### 5.1 Test Fixtures and Helpers

Before the tests, two shared helper functions are defined:

#### `_make_events() → tuple[ActionEvent, ObservationEvent]`

Builds a canonical matched pair of events used across all step tests:

```python
def _make_events() -> tuple[ActionEvent, ObservationEvent]:
    tc = MessageToolCall(
        id="tc1",
        name="dummy",
        arguments='{"value": "hello"}',
        origin="completion",
    )
    action_event = ActionEvent(
        source="agent",
        tool_name="dummy",
        tool_call_id="tc1",
        tool_call=tc,
        action=DummyAction(value="hello"),
        thought=[],
        llm_response_id="response1",
    )
    obs_event = ObservationEvent(
        source="environment",
        action_id=action_event.id,   # links to the action's auto-generated UUID
        tool_name="dummy",
        tool_call_id="tc1",
        observation=DummyObservation(result="Done hello"),
    )
    return action_event, obs_event
```

**Why `action_event.id` for `action_id`?** `ActionEvent` is a frozen Pydantic model — its `id` UUID is generated at construction time and cannot be mutated. This helper captures the ID at construction and links the observation to it correctly.

#### `_make_conv_mock(events) → MagicMock`

Builds a minimal mock `LocalConversation` with a state holding the event list:

```python
def _make_conv_mock(events: list) -> MagicMock:
    state = MagicMock()
    state.events = events
    state.pop_blocked_action.return_value = None  # no security hooks active
    conv = MagicMock()
    conv.state = state
    return conv
```

**Why mock?** `LocalConversation` and `ConversationState` require a full workspace, agent, and persistent state. For pure unit tests of the replay logic, a minimal mock is far simpler and doesn't introduce side effects.

#### Test Tool Fixtures

```python
class DummyAction(Action):
    value: str

class DummyObservation(Observation):
    result: str

class DummyTool(ToolDefinition[DummyAction, DummyObservation]):
    name: ClassVar[str] = "dummy"
    description: str = "Dummy tool for testing"
    action_type: type[Action] = DummyAction
    observation_type: type[Observation] | None = DummyObservation

    @classmethod
    def create(cls, *args, **kwargs) -> Sequence["DummyTool"]:
        class DummyExecutor(ToolExecutor[DummyAction, DummyObservation]):
            def __call__(self, action: DummyAction, conversation=None) -> DummyObservation:
                return DummyObservation(result=f"Done {action.value}")
        return [cls(executor=DummyExecutor())]
```

`DummyTool` mirrors the real tool pattern in the SDK: it has typed `Action`/`Observation` models, a `ToolExecutor` subclass, and a `create()` factory. The executor deterministically returns `"Done {value}"`.

---

### 5.2 Test: `test_replay_agent_initialization`

**Purpose:** Verify the snapshot-loading decision logic in `model_post_init`.

**What it tests:**
- With an empty `replay_snapshot=[]`, `_actual_replay_mode` should be `False`.
- With a non-empty `replay_snapshot` (even just a `MessageEvent`), `_actual_replay_mode` should be `True`.

```python
def test_replay_agent_initialization():
    """Replay mode is disabled when the snapshot is empty, enabled otherwise."""
    llm = LLM(model="test", api_key=SecretStr("test"))

    # Case 1: empty snapshot → fallback to LLM mode
    agent_empty = SnapshotReplayAgent(llm=llm, replay_mode=True, replay_snapshot=[])
    assert agent_empty._actual_replay_mode is False

    # Case 2: non-empty snapshot → replay mode activated
    events: list[Event] = [
        MessageEvent(
            source="agent",
            llm_message=Message(role="assistant", content=[TextContent(text="Hello")]),
        )
    ]
    agent_loaded = SnapshotReplayAgent(llm=llm, replay_mode=True, replay_snapshot=events)
    assert agent_loaded._actual_replay_mode is True
```

**Result:** ✅ Passes. Confirms the guard logic that prevents silent failures when no snapshot is available.

---

### 5.3 Test: `test_replay_agent_step_no_drift`

**Purpose:** End-to-end replay step where the live tool produces the same output as the snapshot.

**Scenario:**
- Snapshot contains: `ActionEvent(dummy, value="hello")` + `ObservationEvent(result="Done hello")`
- Live `DummyTool` executor also returns `"Done hello"`
- Drift log should record `"No drift"`

```python
def test_replay_agent_step_no_drift(tmp_path):
    """When the live observation matches the snapshot, drift log shows 'No drift'."""
    drift_log = str(tmp_path / "drift.log")

    action_event, obs_event = _make_events()
    events = [action_event, obs_event]

    llm = LLM(model="test", api_key=SecretStr("test"))

    # Patch class-level helper so step() sees no pending unmatched actions
    ConversationState.get_unmatched_actions = staticmethod(lambda x: [])

    conv = _make_conv_mock(events)
    agent = SnapshotReplayAgent(
        llm=llm, tools=[], replay_mode=True,
        replay_snapshot=events, drift_log_path=drift_log,
    )
    agent._initialize(conv.state)

    # Inject the live tool directly into the private tool registry
    tools = DummyTool.create()
    agent._tools = {tool.name: tool for tool in tools}

    emitted: list = []
    agent.step(conv, on_event=lambda e: emitted.append(e))

    # Verify a live ObservationEvent was emitted
    assert len(emitted) == 1
    assert isinstance(emitted[0], ObservationEvent)

    # Verify the drift log shows no drift
    assert os.path.exists(drift_log)
    with open(drift_log) as f:
        log_data = json.loads(f.read().strip())
    assert log_data["Drift from expected observation"] == "No drift"
```

**Key decisions:**
- `ConversationState.get_unmatched_actions` is patched to return `[]` so `step()` proceeds to replay rather than flushing a pending batch.
- `agent._initialize(state)` is called to warm up internal private attributes (tool map, executor) without going through a full `LocalConversation`.
- `agent._tools` is set directly after `_initialize()` to inject `DummyTool` without having it registered in the tool spec system.

**Result:** ✅ Passes.

---

### 5.4 Test: `test_replay_agent_step_with_drift`

**Purpose:** Verify drift is detected and correctly logged when the live tool produces a different result than the snapshot.

**Scenario:**
- Snapshot contains: `ObservationEvent(result="Done hello")`
- Live `DriftingDummyTool` returns `"Unexpected result"` instead
- Drift log should contain `{"Drift Present": true, "Expected": [...]}`

```python
def test_replay_agent_step_with_drift(tmp_path):
    """When the live observation differs from the snapshot, drift is logged."""

    class DriftingDummyTool(DummyTool):
        name: ClassVar[str] = "dummy"

        @classmethod
        def create(cls, *args, **kwargs) -> Sequence[DummyTool]:
            class DriftingExecutor(ToolExecutor[DummyAction, DummyObservation]):
                def __call__(self, action: DummyAction, conversation=None) -> DummyObservation:
                    return DummyObservation(result="Unexpected result")  # ← differs from snapshot
            return [cls(executor=DriftingExecutor())]

    drift_log = str(tmp_path / "drift.log")
    action_event, obs_event = _make_events()
    events = [action_event, obs_event]

    llm = LLM(model="test", api_key=SecretStr("test"))
    ConversationState.get_unmatched_actions = staticmethod(lambda x: [])

    conv = _make_conv_mock(events)
    agent = SnapshotReplayAgent(
        llm=llm, tools=[], replay_mode=True,
        replay_snapshot=events, drift_log_path=drift_log,
    )
    agent._initialize(conv.state)
    tools = DriftingDummyTool.create()
    agent._tools = {tool.name: tool for tool in tools}

    emitted: list = []
    agent.step(conv, on_event=lambda e: emitted.append(e))

    assert os.path.exists(drift_log)
    with open(drift_log) as f:
        log_data = json.loads(f.read().strip())
    drift_data = log_data["Drift from expected observation"]
    assert isinstance(drift_data, dict)
    assert drift_data["Drift Present"] is True
```

**Key decisions:**
- `DriftingDummyTool` is defined inline within the test to keep the scope contained. It inherits `DummyTool` but swaps the executor.
- The same snapshot events are used — only the live executor differs.
- The assertion targets the `"Drift from expected observation"` key specifically, validating the structured format.

**Result:** ✅ Passes.

---

### 5.5 Test Results Summary

| Test | Coverage | Result |
|---|---|---|
| `test_replay_agent_initialization` | Snapshot loading logic, fallback guard | ✅ Pass |
| `test_replay_agent_step_no_drift` | Full step execution, drift log with matching output | ✅ Pass |
| `test_replay_agent_step_with_drift` | Full step execution, drift log with differing output | ✅ Pass |

**Run command:**
```bash
uv run pytest tests/sdk/agent/test_replay_agent.py -v
```

**Output:**
```
collected 3 items

tests/sdk/agent/test_replay_agent.py::test_replay_agent_initialization  PASSED
tests/sdk/agent/test_replay_agent.py::test_replay_agent_step_no_drift   PASSED
tests/sdk/agent/test_replay_agent.py::test_replay_agent_step_with_drift PASSED

3 passed in 0.03s
```

All pre-commit checks (ruff lint, ruff format, pyright type checking, import dependency rules, tool subclass registration) also pass cleanly.

---

## 6. File Locations

| File | Path |
|---|---|
| Implementation | `openhands-sdk/openhands/sdk/agent/replay_agent.py` |
| Tests | `tests/sdk/agent/test_replay_agent.py` |
