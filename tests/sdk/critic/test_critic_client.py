"""Tests for CriticClient api_key handling, inference and response parsing."""

import time

import httpx
import pytest
from pydantic import SecretStr

from openhands.sdk import LLM, Agent
from openhands.sdk.critic.impl.api import APIBasedCritic
from openhands.sdk.critic.impl.api.client import (
    DEFAULT_CRITIC_MODEL_NAME,
    DEFAULT_CRITIC_SERVER_URL,
    ClassificationItem,
    ClassificationResponse,
    CriticClient,
)
from openhands.sdk.utils.cipher import Cipher


def test_critic_client_uses_current_default_route():
    """Default critic route should target the hosted proxy pass-through."""
    client = CriticClient(api_key="test_api_key_123")

    assert DEFAULT_CRITIC_SERVER_URL == "https://llm-proxy.app.all-hands.dev/vllm"
    assert DEFAULT_CRITIC_MODEL_NAME == "critic"
    assert client.server_url == DEFAULT_CRITIC_SERVER_URL
    assert client.model_name == DEFAULT_CRITIC_MODEL_NAME


def test_critic_client_with_str_api_key():
    """Test CriticClient accepts str api_key and converts to SecretStr."""
    client = CriticClient(api_key="test_api_key_123")

    assert isinstance(client.api_key, SecretStr)
    assert client.api_key.get_secret_value() == "test_api_key_123"


def test_critic_client_with_secret_str_api_key():
    """Test that CriticClient accepts a SecretStr api_key directly."""
    secret_key = SecretStr("secret_api_key_456")
    client = CriticClient(api_key=secret_key)

    assert isinstance(client.api_key, SecretStr)
    assert client.api_key.get_secret_value() == "secret_api_key_456"


def test_critic_client_empty_string_api_key():
    """Test that CriticClient normalizes an empty string api_key to None."""
    client = CriticClient(api_key="")

    assert client.api_key is None


def test_critic_client_whitespace_only_api_key():
    """Test that CriticClient normalizes a whitespace-only api_key to None."""
    client = CriticClient(api_key="   \t\n  ")

    assert client.api_key is None


def test_critic_client_empty_secret_str_api_key():
    """Test that CriticClient normalizes an empty SecretStr api_key to None."""
    client = CriticClient(api_key=SecretStr(""))

    assert client.api_key is None


def test_critic_client_normalizes_redacted_api_key_placeholder():
    """Test that redacted critic api_key placeholders become None."""
    client = CriticClient(api_key="**********")

    assert client.api_key is None


def test_critic_client_rejects_none_api_key_for_inference():
    """Test that missing api_key cannot be used as a runtime credential."""
    client = CriticClient(api_key="**********")

    with pytest.raises(ValueError, match="api_key must be non-empty"):
        client._get_api_key_value()


def test_critic_client_whitespace_secret_str_api_key():
    """Test that CriticClient normalizes a whitespace-only SecretStr api_key."""
    client = CriticClient(api_key=SecretStr("   \t\n  "))

    assert client.api_key is None


def test_critic_client_api_key_not_exposed_in_repr():
    """Test that the api_key is not exposed in the string representation."""
    client = CriticClient(api_key="super_secret_key")

    client_repr = repr(client)
    client_str = str(client)

    # SecretStr should hide the actual key value in repr/str
    assert "super_secret_key" not in client_repr
    assert "super_secret_key" not in client_str


def test_critic_client_api_key_preserved_after_validation():
    """Test that the api_key value is correctly preserved after validation."""
    test_key = "my_test_key_789"
    client = CriticClient(api_key=test_key)

    # Verify the key is preserved correctly
    assert isinstance(client.api_key, SecretStr)
    assert client.api_key.get_secret_value() == test_key

    # Verify it works with SecretStr input too
    secret_key = SecretStr("another_key_101112")
    client2 = CriticClient(api_key=secret_key)
    assert isinstance(client2.api_key, SecretStr)
    assert client2.api_key.get_secret_value() == "another_key_101112"


def test_critic_client_api_key_exposed_with_context():
    """Test that expose_secrets reveals the api_key for transport payloads."""
    client = CriticClient(api_key="critic-secret")

    dumped = client.model_dump(mode="json", context={"expose_secrets": True})

    assert dumped["api_key"] == "critic-secret"


def test_critic_client_api_key_encrypted_with_cipher():
    """Test that cipher context encrypts and restores the api_key."""
    cipher = Cipher(secret_key="test-secret-key")
    client = CriticClient(api_key="critic-secret")

    dumped = client.model_dump(mode="json", context={"cipher": cipher})

    assert dumped["api_key"] != "critic-secret"
    assert dumped["api_key"] != "**********"
    restored = CriticClient.model_validate(dumped, context={"cipher": cipher})
    assert isinstance(restored.api_key, SecretStr)
    assert restored.api_key.get_secret_value() == "critic-secret"


def test_agent_dump_exposes_nested_critic_api_key_with_context():
    """Test that Agent serialization preserves critic api_key with context."""
    agent = Agent(
        llm=LLM(model="test-model", api_key=SecretStr("llm-secret")),
        critic=APIBasedCritic(
            api_key=SecretStr("critic-secret"),
            server_url="https://critic.example.com",
            model_name="critic",
        ),
    )

    dumped = agent.model_dump(mode="json", context={"expose_secrets": True})

    assert dumped["llm"]["api_key"] == "llm-secret"
    assert dumped["critic"]["api_key"] == "critic-secret"


def test_agent_dump_encrypts_nested_critic_api_key_with_cipher():
    """Test that Agent serialization encrypts nested critic api_key with cipher."""
    cipher = Cipher(secret_key="test-secret-key")
    agent = Agent(
        llm=LLM(model="test-model", api_key=SecretStr("llm-secret")),
        critic=APIBasedCritic(
            api_key=SecretStr("critic-secret"),
            server_url="https://critic.example.com",
            model_name="critic",
        ),
    )

    dumped = agent.model_dump(mode="json", context={"cipher": cipher})

    assert dumped["llm"]["api_key"] != "llm-secret"
    assert dumped["critic"]["api_key"] != "critic-secret"
    assert dumped["critic"]["api_key"] != "**********"

    restored = Agent.model_validate(dumped, context={"cipher": cipher})
    assert isinstance(restored.critic, APIBasedCritic)
    assert isinstance(restored.critic.api_key, SecretStr)
    assert restored.critic.api_key.get_secret_value() == "critic-secret"


def _client_with_transport(monkeypatch, handler) -> tuple[CriticClient, list[int]]:
    """A client whose /classify calls go to ``handler``; returns the call log."""
    calls: list[int] = []

    def recording_handler(request: httpx.Request) -> httpx.Response:
        response = handler(len(calls))
        calls.append(response.status_code)
        return response

    monkeypatch.setattr(
        CriticClient, "apply_chat_template", lambda self, messages, tools=None: "p"
    )
    # Retries back off with real sleeps; skip them.
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    client = CriticClient(api_key="key", server_url="http://critic.invalid")
    client._client = httpx.Client(transport=httpx.MockTransport(recording_handler))
    return client, calls


def test_classify_trace_retries_a_500_then_succeeds(monkeypatch):
    ok = {"data": [{"probs": [0.5], "num_classes": 1}]}
    client, calls = _client_with_transport(
        monkeypatch,
        lambda n: httpx.Response(500) if n == 0 else httpx.Response(200, json=ok),
    )

    response = client.classify_trace([{"role": "user", "content": "hi"}])

    assert calls == [500, 200]
    assert response.data[0].probs == [0.5]


def test_classify_trace_gives_up_after_three_500s(monkeypatch):
    client, calls = _client_with_transport(monkeypatch, lambda n: httpx.Response(500))

    with pytest.raises(httpx.HTTPStatusError):
        client.classify_trace([])

    assert calls == [500, 500, 500]


@pytest.mark.parametrize("status", [400, 401, 429, 503])
def test_classify_trace_does_not_retry_other_errors(monkeypatch, status):
    client, calls = _client_with_transport(
        monkeypatch, lambda n: httpx.Response(status)
    )

    with pytest.raises(httpx.HTTPStatusError):
        client.classify_trace([])

    assert calls == [status]


def test_classify_trace_without_key_fails_before_any_request(monkeypatch):
    client, calls = _client_with_transport(monkeypatch, lambda n: httpx.Response(200))
    client.api_key = None

    with pytest.raises(ValueError, match="api_key must be non-empty"):
        client.classify_trace([])

    assert calls == []


def _response(probs: list[float], num_classes: int | None = None):
    return ClassificationResponse(
        data=[ClassificationItem(probs=probs, num_classes=num_classes)]
    )


def test_extract_prob_map_aligns_probs_to_labels():
    client = CriticClient(api_key="key")
    probs = [i / 100 for i in range(len(client.all_labels))]

    prob_map = client.extract_prob_map(_response(probs, num_classes=len(probs)))

    assert prob_map.order == list(client.all_labels)
    assert prob_map.probs["success"] == 0.0
    assert prob_map.probs[client.all_labels[-1]] == probs[-1]


@pytest.mark.parametrize(
    ("response", "match"),
    [
        (ClassificationResponse(data=[]), "empty response.data"),
        (_response([]), "empty 'probs'"),
        (_response([0.1, 0.2], num_classes=3), "num_classes"),
        (_response([0.1, 0.2]), r"len\(probs\)"),
    ],
)
def test_extract_prob_map_rejects_malformed_responses(response, match):
    client = CriticClient(api_key="key")

    with pytest.raises(ValueError, match=match):
        client.extract_prob_map(response)


def test_extract_prob_map_without_success_label():
    client = CriticClient(api_key="key", has_success_label=False)
    probs = [0.1] * len(client.all_labels)

    prob_map = client.extract_prob_map(_response(probs))

    assert "success" not in prob_map.probs
    assert len(prob_map.probs) == len(probs)


def test_normalize_messages_flattens_odd_content_shapes():
    messages = [
        {"role": "user", "content": None},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "a"},
                {"type": "image_url", "image_url": {"url": "x"}},
                "not-a-block",
                {"type": "text", "text": "b"},
            ],
        },
        {"role": "tool", "content": 42},
        {"content": "no role"},
    ]

    assert CriticClient.normalize_messages(messages) == [
        {"role": "user", "content": ""},
        {"role": "assistant", "content": "a\nb"},
        {"role": "tool", "content": "42"},
        {"role": "", "content": "no role"},
    ]
