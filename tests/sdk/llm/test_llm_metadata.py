from openhands.sdk.llm import LLM


def test_llm_metadata_default():
    """Test that metadata field defaults to empty dict."""
    llm = LLM(model="gpt-4o", usage_id="test")
    assert llm.metadata == {}


def test_llm_metadata_initialization():
    """Test metadata field initialization with custom values."""
    custom_metadata = {
        "trace_version": "1.0.0",
        "tags": ["model:gpt-4", "agent:my-agent"],
        "session_id": "session-123",
        "trace_user_id": "user-456",
    }
    llm = LLM(model="gpt-4o", usage_id="test", metadata=custom_metadata)
    assert llm.metadata == custom_metadata


def test_llm_metadata_modification():
    """Test that metadata field can be modified after initialization."""
    llm = LLM(model="gpt-4o", usage_id="test")

    # Start with empty metadata
    assert llm.metadata == {}

    # Add some metadata
    llm.metadata["custom_key"] = "custom_value"
    llm.metadata["session_id"] = "session-123"

    assert llm.metadata["custom_key"] == "custom_value"
    assert llm.metadata["session_id"] == "session-123"


def test_llm_metadata_complex_structure():
    """Test metadata field with complex nested structure."""
    complex_metadata = {
        "trace_version": "2.1.0",
        "tags": ["model:claude-3", "agent:coding-agent", "env:production"],
        "session_info": {
            "id": "session-789",
            "user_id": "user-101",
            "created_at": "2024-01-01T00:00:00Z",
        },
        "metrics": {
            "tokens_used": 1500,
            "response_time_ms": 250,
        },
    }
    llm = LLM(model="claude-3-5-sonnet", usage_id="test", metadata=complex_metadata)
    assert llm.metadata == complex_metadata

    # Test nested access
    assert llm.metadata["session_info"]["id"] == "session-789"
    assert llm.metadata["metrics"]["tokens_used"] == 1500
