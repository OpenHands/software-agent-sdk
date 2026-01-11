# View

The `View` class is responsible for representing and manipulating the subset of events that will be provided to the agent's LLM on every step.

It is closely tied to the context condensation system, and works to ensure the resulting sequence of messages are well-formed and respect the structure expected by common LLM APIs.

## Architecture Overview

### Property-Based Design

The View maintains several **properties** (invariants) that must hold for the event sequence to be valid. Each property has two responsibilities:

1. **Validation**: Check that the property holds and filter/transform events to enforce it
2. **Manipulation Index Calculation**: Determine "safe boundaries" where events can be inserted or removed without violating the property

The final set of manipulation indices is computed by taking the **intersection** of the indices from all properties. This ensures that operations at those indices will respect all invariants simultaneously.

### Why This Matters

This design provides:
- **Modularity**: Each property is self-contained and independently testable
- **Composability**: New properties can be added without modifying existing ones
- **Clarity**: The interaction between properties is explicit (intersection)
- **Safety**: Manipulation operations are guaranteed to maintain all invariants

## Properties

The View maintains four core properties:

### 1. BatchAtomicityProperty

**Purpose**: Ensures that ActionEvents sharing the same `llm_response_id` form an atomic unit that cannot be split.

**Why It Exists**: When an LLM makes a single response containing multiple tool calls, those calls are semantically related. If any one is forgotten (e.g., during condensation), all must be forgotten together to maintain consistency.

**Validation Logic**:
- Groups ActionEvents by their `llm_response_id` field
- When any ActionEvent in a batch is marked for removal, adds all other ActionEvents from that batch to the removal set
- Uses `ActionBatch.from_events()` to build the mapping

**Manipulation Index Calculation**:
1. Build mapping: `llm_response_id` → list of ActionEvent indices
2. For each batch, find the min and max indices of all actions
3. Mark the range `[min, max]` as atomic (cannot insert/remove within)
4. Return all indices *outside* these atomic ranges

**Auxiliary Data**:
- `batches: dict[EventID, list[int]]` - Maps llm_response_id to action indices

**Example**:
```
Events: [E0, A1, A2, E3, A4]  (A1, A2 share llm_response_id='batch1')
Atomic ranges: [1, 2]
Manipulation indices: {0, 3, 5}  (can manipulate before/between/after, not within batch)
```

---

### 2. ToolLoopAtomicityProperty

**Purpose**: Ensures that "tool loops" (thinking blocks followed by tool calls) remain atomic units.

**Why It Exists**: Claude API requires that thinking blocks stay with their associated tool calls. A tool loop is:
- An initial batch containing thinking blocks (ActionEvents with non-empty `thinking_blocks`)
- All subsequent consecutive ActionEvent batches
- Terminated by the first non-ActionEvent/ObservationEvent

**Validation Logic**:
- Identifies batches that start with thinking blocks
- Extends the atomic unit through all consecutive ActionEvent/ObservationEvent batches
- Does not perform removal (relies on batch atomicity)

**Manipulation Index Calculation**:
1. Identify batches with thinking blocks (potential tool loop starts)
2. For each such batch, scan forward to find where the tool loop ends (first non-action/observation)
3. Mark entire range as atomic
4. Return all indices *outside* these tool loop ranges

**Auxiliary Data**:
- `batch_ranges: list[tuple[int, int, bool]]` - (min_idx, max_idx, has_thinking) for each batch
- `tool_loop_ranges: list[tuple[int, int]]` - Start and end indices of tool loops

**Example**:
```
Events: [E0, A1(thinking), O1, A2, E3]
Tool loop: [1, 3] (A1 with thinking → O1 → A2, stops at E3)
Manipulation indices: {0, 4, 5}  (can only manipulate before loop or after)
```

---

### 3. ToolCallMatchingProperty

**Purpose**: Ensures that ActionEvents and ObservationEvents are properly paired via `tool_call_id`.

**Why It Exists**: LLM APIs expect tool calls to have corresponding observations. Orphaned actions or observations cause API errors.

**Validation Logic**:
1. Extract all `tool_call_id` values from ActionEvents
2. Extract all `tool_call_id` values from ObservationEvents (includes ObservationEvent, UserRejectObservation, AgentErrorEvent)
3. Keep ActionEvents only if their `tool_call_id` exists in observations
4. Keep ObservationEvents only if their `tool_call_id` exists in actions
5. Keep all other event types unconditionally

**Manipulation Index Calculation**:
- All indices are valid for this property (no restrictions on boundaries)
- Validation happens through filtering, not boundary restriction
- Returns `set(range(len(events) + 1))`

**Auxiliary Data**:
- `action_tool_call_ids: set[ToolCallID]` - Tool call IDs from actions
- `observation_tool_call_ids: set[ToolCallID]` - Tool call IDs from observations

**Example**:
```
Events: [A1(tc_1), O1(tc_1), A2(tc_2)]
A2 has no matching observation → filtered out
Result: [A1(tc_1), O1(tc_1)]
```

---
