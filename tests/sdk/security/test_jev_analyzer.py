import json

import httpx
import pytest
from pydantic import SecretStr

from openhands.sdk.event import ActionEvent
from openhands.sdk.llm import MessageToolCall, TextContent
from openhands.sdk.security import (
    EnsembleSecurityAnalyzer,
    JevSecurityAnalyzer,
    SecurityAnalyzerBase,
    SecurityRisk,
)
from openhands.sdk.utils.cipher import Cipher


def action(command: str = "ls") -> ActionEvent:
    return ActionEvent(
        thought=[TextContent(text="Inspect the project")],
        summary="List files",
        reasoning_content="Read-only inspection",
        action=None,
        tool_name="terminal",
        tool_call_id="call",
        tool_call=MessageToolCall(
            id="call",
            name="terminal",
            arguments=json.dumps({"command": command}),
            origin="completion",
        ),
        llm_response_id="response",
    )


def answers(danger: object = 0.0, injection: object = 0.0, index=0):
    return {
        f"{index}.dangerous_operation": {"type": "noul", "noul": danger},
        f"{index}.prompt_injection": {"type": "noul", "noul": injection},
    }


@pytest.fixture
def jev_transport(monkeypatch):
    requests = []
    responses = []

    def handle(request):
        requests.append(request)
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return httpx.Response(response[0], json=response[1])

    client_class = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: client_class(transport=httpx.MockTransport(handle), **kwargs),
    )
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    return requests, responses


def test_batch_classification_and_details(jev_transport):
    requests, responses = jev_transport
    responses.append((200, {"answers": answers() | answers(1.0, index=1)}))
    analyzer = JevSecurityAnalyzer(api_key=SecretStr("test-key"), policy="No uploads")
    actions = [action(), action("rm -rf /")]
    results = analyzer.analyze_actions(actions)
    assert [r.risk for _, r in results] == [SecurityRisk.LOW, SecurityRisk.HIGH]
    assert results[0][1].details == {
        "probabilities": {
            "dangerous_operation": 0.0,
            "prompt_injection": 0.0,
        }
    }
    assert [a for a, _ in results] == actions
    assert len(requests) == 1
    payload = json.loads(requests[0].content)
    assert len(payload["questions"]) == 4
    assert (
        payload["questions"]["1.prompt_injection"]["instructions"]["policy"]
        == "No uploads"
    )
    state = payload["state"]["actions"]["1"]
    assert "rm -rf /" in state["executable"]["arguments"]
    assert state["commentary"] == {
        "summary": "List files",
        "thought": ["Inspect the project"],
        "reasoning": "Read-only inspection",
    }
    assert requests[0].headers["Authorization"] == "Bearer test-key"
    assert analyzer.analyze_actions([]) == []


@pytest.mark.parametrize(
    ("danger", "injection", "risk"),
    [
        (0.0, 0.0, SecurityRisk.LOW),
        (0.2, 0.0, SecurityRisk.MEDIUM),
        (0.0, 0.49, SecurityRisk.MEDIUM),
        (0.5, 0.0, SecurityRisk.HIGH),
        (0.0, 1.0, SecurityRisk.HIGH),
    ],
)
def test_detector_thresholds(jev_transport, danger, injection, risk):
    _, responses = jev_transport
    responses.append((200, {"answers": answers(danger, injection)}))
    assert JevSecurityAnalyzer(api_key=SecretStr("key")).security_risk(action()) == risk


@pytest.mark.parametrize(
    "response",
    [
        (500, {"error": "test-key"}),
        (302, {"location": "https://other.example"}),
        (200, {"answers": {}}),
        (200, {"answers": {"other": {"type": "noul", "noul": 0.0}}}),
        (200, {"answers": answers(float("nan"))}),
        (200, {"answers": answers(1.01)}),
        (200, {"answers": answers(-0.01)}),
        (200, {"answers": answers("0.0")}),
        (200, {"answers": answers(True)}),
        httpx.ReadTimeout("test-key"),
    ],
)
def test_errors_fail_closed(jev_transport, response):
    _, responses = jev_transport
    responses.append(response)
    analyzer = JevSecurityAnalyzer(api_key=SecretStr("test-key"))
    result = analyzer.analyze_action(action())
    assert result.risk == SecurityRisk.HIGH
    assert "test-key" not in result.model_dump_json()


def test_missing_key_fails_closed_in_ensemble(jev_transport):
    requests, _ = jev_transport
    analyzer = JevSecurityAnalyzer()
    assert (
        EnsembleSecurityAnalyzer(analyzers=[analyzer]).security_risk(action())
        == SecurityRisk.HIGH
    )
    assert not requests


def test_env_fallback_and_explicit_key_precedence(jev_transport, monkeypatch):
    requests, responses = jev_transport
    analyzer = JevSecurityAnalyzer()
    monkeypatch.setenv("TYPESAFE_API_KEY", "env-key")
    responses.extend([(200, {"answers": answers()})] * 2)
    assert analyzer.security_risk(action()) == SecurityRisk.LOW
    explicit = JevSecurityAnalyzer(api_key=SecretStr("explicit-key"))
    assert explicit.analyze_pending_actions([action()])[0][1] == SecurityRisk.LOW
    assert [r.headers["authorization"] for r in requests] == [
        "Bearer env-key",
        "Bearer explicit-key",
    ]
    assert "env-key" not in analyzer.model_dump_json(context={"expose_secrets": True})


def test_encrypted_polymorphic_roundtrip(jev_transport):
    cipher = Cipher("test-encryption-key")
    analyzer = JevSecurityAnalyzer(api_key=SecretStr("test-key"), policy="No uploads")
    dumped = analyzer.model_dump_json(context={"cipher": cipher})
    assert "test-key" not in dumped
    restored = SecurityAnalyzerBase.model_validate_json(
        dumped, context={"cipher": cipher}
    )
    assert isinstance(restored, JevSecurityAnalyzer)
    assert restored.api_key is not None
    assert restored.api_key.get_secret_value() == "test-key"
    assert restored.policy == "No uploads"
    assert "test-key" not in analyzer.model_dump_json()
    redacted = SecurityAnalyzerBase.model_validate_json(analyzer.model_dump_json())
    assert isinstance(redacted, JevSecurityAnalyzer)
    assert redacted.api_key is None


def test_invalid_thresholds_rejected():
    with pytest.raises(ValueError, match="low_threshold"):
        JevSecurityAnalyzer(low_threshold=0.8, high_threshold=0.5)
