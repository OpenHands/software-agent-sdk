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

### 4. CondensationProperty

**Purpose**: Handles condensation operations including forgotten events and summary insertion.

**Why It Exists**: Context condensation is a core mechanism for managing context window limits. This property processes `Condensation` events and applies their effects.

**Validation Logic**:
1. Collect all `forgotten_event_ids` from Condensation events
2. Remove events with IDs in the forgotten set
3. Filter out CondensationRequest and Condensation events themselves (meta-events)
4. Insert summary from most recent Condensation at specified offset
5. Track whether there's an unhandled CondensationRequest

**Manipulation Index Calculation**:
- All indices are valid for this property (no restrictions)
- Condensation effects are applied during validation, not via boundary restriction
- Returns `set(range(len(events) + 1))`

**Auxiliary Data**:
- `forgotten_event_ids: set[EventID]` - Accumulated from all Condensation events
- `condensations: list[Condensation]` - History of condensation operations
- `unhandled_condensation_request: bool` - Flag for pending requests
- `most_recent_condensation: Condensation | None` - Latest condensation with summary info

**Example**:
```
Condensation: forget_ids={id_5, id_7}, summary="Earlier work...", summary_offset=2
Events: [E0, E1, E5, E7, E10] → [E0, E1, Summary("Earlier work..."), E10]
```

---

## Interface Definition

All properties implement this protocol:

```python
from typing import Protocol

class ViewProperty(Protocol):
    """A property (invariant) that the View maintains."""

    def calculate_manipulation_indices(
        self,
        events: list[LLMConvertibleEvent]
    ) -> set[int]:
        """
        Calculate the set of indices where manipulation (insert/forget) is safe
        for this property.

        Returns:
            Set of valid indices (0 to len(events) inclusive)
        """
        ...

    def validate(
        self,
        events: list[LLMConvertibleEvent]
    ) -> list[LLMConvertibleEvent]:
        """
        Validate and enforce this property on the event sequence.
        May filter, reorder, or insert events as needed.

        Returns:
            Validated event sequence
        """
        ...
```

### ManipulationIndexCalculator

Orchestrates all properties to compute final manipulation indices:

```python
class ManipulationIndexCalculator:
    """Calculates safe manipulation boundaries by intersecting property constraints."""

    def __init__(self, properties: list[ViewProperty]):
        self.properties = properties

    def calculate_indices(
        self,
        events: list[LLMConvertibleEvent]
    ) -> list[int]:
        """
        Calculate manipulation indices by taking intersection of all properties.

        Returns:
            Sorted list of valid manipulation indices
        """
        if not self.properties:
            return list(range(len(events) + 1))

        # Start with first property's indices
        result = self.properties[0].calculate_manipulation_indices(events)

        # Intersect with all other properties
        for prop in self.properties[1:]:
            result &= prop.calculate_manipulation_indices(events)

        return sorted(result)

    def find_next_index(
        self,
        indices: list[int],
        threshold: int,
        strict: bool = False
    ) -> int:
        """
        Find next manipulation index >= threshold (or > threshold if strict).

        Args:
            indices: Sorted list of valid manipulation indices
            threshold: Minimum index value
            strict: If True, return index > threshold; else >= threshold

        Returns:
            Next valid index, or len(events) if none found
        """
        import bisect
        if strict:
            idx = bisect.bisect_right(indices, threshold)
        else:
            idx = bisect.bisect_left(indices, threshold)
        return indices[idx] if idx < len(indices) else indices[-1]
```

---

## Migration Process

This refactoring will be done in 10 commit-sized steps, each maintaining a fully working implementation with all tests passing.

### Step 1: Add Property Interface and Tests

**Goal**: Define the `ViewProperty` protocol and create test infrastructure

**Changes**:
- Create `openhands-sdk/openhands/sdk/context/view/properties.py`
- Define `ViewProperty` Protocol
- Add basic test in `tests/sdk/context/test_view_properties.py`

**Test Strategy**: Create a simple mock property to verify the interface

**Success Criteria**: Tests pass, interface is documented

---

### Step 2: Extract ActionBatch Utility

**Goal**: Move `ActionBatch` to a shared utility module

**Changes**:
- Create `openhands-sdk/openhands/sdk/context/view/utils.py`
- Move `ActionBatch` class from `view.py` to `utils.py`
- Update imports in `view.py`

**Test Strategy**: Run existing batch atomicity tests

**Success Criteria**: All tests pass, no behavior changes

---

### Step 3: Implement BatchAtomicityProperty

**Goal**: Extract batch atomicity logic into a property class

**Changes**:
- Add `BatchAtomicityProperty` class to `properties.py`
- Implement `calculate_manipulation_indices()` method
- Implement `validate()` method (delegates to existing `_enforce_batch_atomicity`)
- Add comprehensive tests in `tests/sdk/context/test_batch_atomicity_property.py`

**Test Strategy**:
- Port relevant tests from `test_view_batch_atomicity.py`
- Test manipulation index calculation independently
- Test validation independently

**Success Criteria**: New property passes all tests, existing View tests still pass

---

### Step 4: Implement ToolLoopAtomicityProperty

**Goal**: Extract tool loop atomicity logic into a property class

**Changes**:
- Add `ToolLoopAtomicityProperty` class to `properties.py`
- Implement `calculate_manipulation_indices()` method
- Implement `validate()` method (no-op, relies on batch atomicity)
- Add tests in `tests/sdk/context/test_tool_loop_atomicity_property.py`

**Test Strategy**:
- Create scenarios with thinking blocks
- Verify tool loops are identified correctly
- Test manipulation indices exclude tool loop interiors

**Success Criteria**: Property correctly identifies and protects tool loops

---

### Step 5: Implement ToolCallMatchingProperty

**Goal**: Extract tool call matching logic into a property class

**Changes**:
- Add `ToolCallMatchingProperty` class to `properties.py`
- Implement `calculate_manipulation_indices()` method (returns all indices)
- Implement `validate()` method (filters unmatched tool calls)
- Add tests in `tests/sdk/context/test_tool_call_matching_property.py`

**Test Strategy**:
- Port tests from `test_view_action_filtering.py`
- Test filtering of unmatched actions
- Test filtering of unmatched observations

**Success Criteria**: Property correctly filters orphaned tool calls/observations

---

### Step 6: Implement CondensationProperty

**Goal**: Extract condensation handling logic into a property class

**Changes**:
- Add `CondensationProperty` class to `properties.py`
- Implement `calculate_manipulation_indices()` method (returns all indices)
- Implement `validate()` method (processes forgotten events, inserts summary)
- Add tests in `tests/sdk/context/test_condensation_property.py`

**Test Strategy**:
- Port tests from `test_view_condensation_batch_atomicity.py`
- Test forgotten event removal
- Test summary insertion
- Test unhandled request tracking

**Success Criteria**: Property handles all condensation scenarios correctly

---

### Step 7: Create ManipulationIndexCalculator

**Goal**: Create orchestrator for property-based index calculation

**Changes**:
- Create `openhands-sdk/openhands/sdk/context/view/manipulation.py`
- Implement `ManipulationIndexCalculator` class
- Add tests in `tests/sdk/context/test_manipulation_calculator.py`

**Test Strategy**:
- Test with no properties (all indices valid)
- Test with single property
- Test with multiple properties (verify intersection)
- Test `find_next_index()` helper

**Success Criteria**: Calculator correctly intersects property constraints

---

### Step 8: Refactor View to Use Properties

**Goal**: Update View class to use property-based architecture

**Changes**:
- Update `View.__init__` to instantiate properties
- Replace `manipulation_indices` property with calculator-based implementation
- Replace `from_events()` to use property validators
- Keep old methods as fallback (mark as deprecated)

**Test Strategy**: Run full test suite - all existing tests should pass

**Success Criteria**: View behavior unchanged, all tests pass

---

### Step 9: Remove Old Implementation

**Goal**: Delete deprecated code paths

**Changes**:
- Remove old `manipulation_indices` implementation
- Remove old validation methods (`filter_unmatched_tool_calls`, `_enforce_batch_atomicity`, etc.)
- Remove deprecated markers
- Update any remaining internal references

**Test Strategy**: Run full test suite

**Success Criteria**: All tests pass, code is cleaner

---

### Step 10: Final Cleanup and Documentation

**Goal**: Polish and document the new architecture

**Changes**:
- Update docstrings in `view.py` to reference properties
- Add module-level documentation to `properties.py`
- Update type hints if needed
- Update this README with final implementation notes

**Test Strategy**:
- Run full test suite
- Verify test coverage for all properties
- Run linting/type checking

**Success Criteria**: Clean, well-documented implementation with no regressions

---

## Testing Strategy

Each property should have:
1. **Unit tests**: Test the property in isolation with minimal event sequences
2. **Integration tests**: Test the property's interaction with others via View
3. **Edge cases**: Empty sequences, single events, large sequences

Key test scenarios:
- **BatchAtomicity**: Multi-action responses, mixed batches
- **ToolLoopAtomicity**: Thinking + tools, multiple loops, nested structures
- **ToolCallMatching**: Orphaned actions, orphaned observations, partial matches
- **Condensation**: Multiple condensations, summary placement, forgotten events

---

## Future Extensions

This architecture makes it easy to add new properties:

**Example: MessagePairingProperty**
- Ensure user/assistant messages alternate
- Provide indices that maintain alternation

**Example: TokenLimitProperty**
- Track cumulative token counts
- Provide indices that respect token budgets

**Example: ToolCallDepthProperty**
- Limit nesting depth of tool calls
- Provide indices that maintain depth constraints

To add a property:
1. Implement `ViewProperty` protocol
2. Add to View's property list
3. Write tests
4. Done! Intersection handles composition automatically.
