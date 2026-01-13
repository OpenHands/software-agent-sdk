from openhands.sdk.critic.base import CriticResult
from openhands.sdk.critic.impl.agent_review import AgentReviewCritic


def test_parse_output_prefers_last_json_block():
    critic = AgentReviewCritic()
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


def test_parse_output_missing_json_falls_back_to_not_pass():
    critic = AgentReviewCritic()
    out = critic._parse_output("no json here")
    assert out.score == 0.0


def test_parse_output_invalid_decision_is_not_pass():
    critic = AgentReviewCritic()
    text = """```json
{"decision": "maybe", "summary": "hmm"}
```"""
    out = critic._parse_output(text)
    assert out.score == 0.0
    assert out.message == "hmm"


def test_parse_output_accepts_embedded_json_without_fence():
    critic = AgentReviewCritic()
    text = 'prefix {"decision":"pass","summary":"ok"} suffix'
    out = critic._parse_output(text)
    assert out.score == 1.0
    assert out.message == "ok"
