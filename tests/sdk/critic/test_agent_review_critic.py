import pytest
from pydantic import SecretStr

from openhands.sdk.critic.base import CriticResult
from openhands.sdk.critic.impl.agent_review import AgentReviewCritic
from openhands.sdk.llm import LLM


@pytest.fixture
def mock_llm() -> LLM:
    """Create a mock LLM for testing."""
    return LLM(
        usage_id="test",
        model="test/model",
        api_key=SecretStr("test-key"),
    )


def test_parse_output_prefers_last_json_block(mock_llm: LLM) -> None:
    critic = AgentReviewCritic(llm=mock_llm)
    text = """
blah
```json
{"decision": "not_pass", "summary": "old"}
```

more
```json
{"decision": "pass", "summary": "new"}
```
"""
    out = critic._parse_output(text)
    assert out == CriticResult(score=1.0, message="new")


def test_parse_output_missing_json_falls_back_to_not_pass(mock_llm: LLM) -> None:
    critic = AgentReviewCritic(llm=mock_llm)
    out = critic._parse_output("no json here")
    assert out.score == 0.0


def test_parse_output_invalid_decision_is_not_pass(mock_llm: LLM) -> None:
    critic = AgentReviewCritic(llm=mock_llm)
    text = """```json
{"decision": "maybe", "summary": "hmm"}
```"""
    out = critic._parse_output(text)
    assert out.score == 0.0
    assert out.message == "hmm"


def test_parse_output_accepts_embedded_json_without_fence(mock_llm: LLM) -> None:
    critic = AgentReviewCritic(llm=mock_llm)
    text = 'prefix {"decision":"pass","summary":"ok"} suffix'
    out = critic._parse_output(text)
    assert out.score == 1.0
    assert out.message == "ok"


def test_llm_is_required() -> None:
    """Test that llm field is required."""
    with pytest.raises(Exception):  # Pydantic validation error
        AgentReviewCritic()  # type: ignore[call-arg]
