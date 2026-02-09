"""Tests for AgentStateRegistry."""

from openhands.sdk.agent.state import (
    ITERATIVE_REFINEMENT_ITERATION_KEY,
    AgentState,
    AgentStateRegistry,
)


class TestAgentStateRegistry:
    """Tests for the AgentStateRegistry class."""

    def test_default_empty_registry(self):
        """Test that a new registry is empty by default."""
        registry = AgentStateRegistry()
        assert registry.data == {}

    def test_get_with_default(self):
        """Test getting a value with a default."""
        registry = AgentStateRegistry()
        assert registry.get("nonexistent") is None
        assert registry.get("nonexistent", 42) == 42

    def test_set_and_get(self):
        """Test setting and getting values."""
        registry = AgentStateRegistry()
        registry.set("key1", "value1")
        registry.set("key2", 123)
        registry.set("key3", {"nested": "dict"})

        assert registry.get("key1") == "value1"
        assert registry.get("key2") == 123
        assert registry.get("key3") == {"nested": "dict"}

    def test_has(self):
        """Test checking if a key exists."""
        registry = AgentStateRegistry()
        assert not registry.has("key")
        registry.set("key", "value")
        assert registry.has("key")

    def test_remove(self):
        """Test removing a key."""
        registry = AgentStateRegistry()
        registry.set("key", "value")
        assert registry.has("key")

        removed = registry.remove("key")
        assert removed == "value"
        assert not registry.has("key")

        # Removing non-existent key returns None
        assert registry.remove("nonexistent") is None

    def test_clear(self):
        """Test clearing all state."""
        registry = AgentStateRegistry()
        registry.set("key1", "value1")
        registry.set("key2", "value2")
        assert len(registry.data) == 2

        registry.clear()
        assert registry.data == {}

    def test_iterative_refinement_key_constant(self):
        """Test that the iterative refinement key constant is defined."""
        assert ITERATIVE_REFINEMENT_ITERATION_KEY == "iterative_refinement_iteration"

    def test_iterative_refinement_usage_pattern(self):
        """Test the typical usage pattern for iterative refinement."""
        registry = AgentStateRegistry()

        # Initial state - no iteration set
        iteration = registry.get(ITERATIVE_REFINEMENT_ITERATION_KEY, 0)
        assert iteration == 0

        # Increment iteration
        registry.set(ITERATIVE_REFINEMENT_ITERATION_KEY, iteration + 1)
        assert registry.get(ITERATIVE_REFINEMENT_ITERATION_KEY) == 1

        # Increment again
        iteration = registry.get(ITERATIVE_REFINEMENT_ITERATION_KEY, 0)
        registry.set(ITERATIVE_REFINEMENT_ITERATION_KEY, iteration + 1)
        assert registry.get(ITERATIVE_REFINEMENT_ITERATION_KEY) == 2


class TestAgentStateRegistrySerialization:
    """Tests for AgentStateRegistry serialization/deserialization."""

    def test_model_dump(self):
        """Test serialization to dict."""
        registry = AgentStateRegistry()
        registry.set("key1", "value1")
        registry.set("key2", 123)

        dumped = registry.model_dump()
        assert dumped == {"data": {"key1": "value1", "key2": 123}}

    def test_model_dump_json(self):
        """Test serialization to JSON."""
        registry = AgentStateRegistry()
        registry.set("key1", "value1")
        registry.set("key2", 123)

        json_str = registry.model_dump_json()
        assert '"key1":"value1"' in json_str or '"key1": "value1"' in json_str
        assert '"key2":123' in json_str or '"key2": 123' in json_str

    def test_model_validate(self):
        """Test deserialization from dict."""
        data = {"data": {"key1": "value1", "key2": 123}}
        registry = AgentStateRegistry.model_validate(data)

        assert registry.get("key1") == "value1"
        assert registry.get("key2") == 123

    def test_roundtrip_serialization(self):
        """Test that serialization and deserialization are inverse operations."""
        original = AgentStateRegistry()
        original.set("string_key", "string_value")
        original.set("int_key", 42)
        original.set("list_key", [1, 2, 3])
        original.set("dict_key", {"nested": "value"})

        # Serialize and deserialize
        dumped = original.model_dump()
        restored = AgentStateRegistry.model_validate(dumped)

        assert restored.get("string_key") == "string_value"
        assert restored.get("int_key") == 42
        assert restored.get("list_key") == [1, 2, 3]
        assert restored.get("dict_key") == {"nested": "value"}


class TestAgentStateBackwardCompatibility:
    """Tests for backward compatibility with AgentState alias."""

    def test_agent_state_alias(self):
        """Test that AgentState is an alias for AgentStateRegistry."""
        assert AgentState is AgentStateRegistry

    def test_agent_state_instantiation(self):
        """Test that AgentState can be instantiated."""
        state = AgentState()
        assert isinstance(state, AgentStateRegistry)
        state.set("key", "value")
        assert state.get("key") == "value"
