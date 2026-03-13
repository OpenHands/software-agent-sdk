"""Tests for LLM api_key accepting SecretSource (e.g. LookupSecret).

This enables the pattern where the SDK client creates an LLM with a
LookupSecret api_key that the agent-server inside the sandbox resolves
lazily — the raw API key never transits through the SDK client.
"""

from unittest.mock import patch

import httpx
import pytest
from pydantic import SecretStr

from openhands.sdk.llm import LLM
from openhands.sdk.secret import LookupSecret, SecretSource, StaticSecret


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestLookupSecretApiKeyConstruction:
    """LLM should accept LookupSecret as api_key."""

    def test_lookup_secret_accepted(self):
        lookup = LookupSecret(
            url="https://saas.example.com/api/v1/sandboxes/sb-1/settings/llm-key",
            headers={"X-Session-API-Key": "session-key-123"},
        )
        llm = LLM(model="test-model", api_key=lookup)
        assert isinstance(llm.api_key, LookupSecret)
        assert llm.api_key.url == lookup.url

    def test_lookup_secret_with_description(self):
        lookup = LookupSecret(
            url="https://example.com/key",
            headers={"X-Session-API-Key": "sk"},
            description="SaaS LLM key",
        )
        llm = LLM(model="test-model", api_key=lookup)
        assert llm.api_key.description == "SaaS LLM key"

    def test_static_secret_accepted(self):
        static = StaticSecret(value="sk-direct-key")
        llm = LLM(model="test-model", api_key=static)
        assert isinstance(llm.api_key, StaticSecret)

    def test_secretstr_still_works(self):
        llm = LLM(model="test-model", api_key=SecretStr("sk-normal"))
        assert isinstance(llm.api_key, SecretStr)
        assert llm.api_key.get_secret_value() == "sk-normal"

    def test_none_still_works(self):
        llm = LLM(model="test-model", api_key=None)
        assert llm.api_key is None

    def test_plain_string_still_converted_to_secretstr(self):
        llm = LLM(model="test-model", api_key="sk-plain")
        assert isinstance(llm.api_key, SecretStr)


# ---------------------------------------------------------------------------
# Serialization round-trip
# ---------------------------------------------------------------------------


class TestLookupSecretSerialization:
    """Serialization/deserialization must preserve LookupSecret."""

    def test_serialize_with_expose_secrets(self):
        lookup = LookupSecret(
            url="https://saas.example.com/key",
            headers={"X-Session-API-Key": "session-key"},
        )
        llm = LLM(model="test-model", api_key=lookup)
        dumped = llm.model_dump(mode="json", context={"expose_secrets": True})
        assert dumped["api_key"]["kind"] == "LookupSecret"
        assert dumped["api_key"]["url"] == "https://saas.example.com/key"
        assert dumped["api_key"]["headers"]["X-Session-API-Key"] == "session-key"

    def test_serialize_without_expose_secrets(self):
        lookup = LookupSecret(
            url="https://saas.example.com/key",
            headers={"X-Session-API-Key": "session-key"},
        )
        llm = LLM(model="test-model", api_key=lookup)
        dumped = llm.model_dump(mode="json")
        # LookupSecret should still serialize (no redaction — it contains
        # no raw secret value, only a URL)
        assert dumped["api_key"]["kind"] == "LookupSecret"

    def test_round_trip_from_dict(self):
        lookup = LookupSecret(
            url="https://saas.example.com/key",
            headers={"X-Session-API-Key": "sk"},
        )
        llm = LLM(model="test-model", api_key=lookup)
        dumped = llm.model_dump(mode="json", context={"expose_secrets": True})

        restored = LLM.model_validate(dumped)
        assert isinstance(restored.api_key, LookupSecret)
        assert restored.api_key.url == "https://saas.example.com/key"

    def test_round_trip_from_json(self):
        lookup = LookupSecret(
            url="https://saas.example.com/key",
            headers={"X-Session-API-Key": "sk"},
        )
        llm = LLM(model="test-model", api_key=lookup)
        json_str = llm.model_dump_json(context={"expose_secrets": True})

        restored = LLM.model_validate_json(json_str)
        assert isinstance(restored.api_key, LookupSecret)

    def test_static_secret_round_trip(self):
        static = StaticSecret(value="sk-direct")
        llm = LLM(model="test-model", api_key=static)
        dumped = llm.model_dump(mode="json", context={"expose_secrets": True})

        restored = LLM.model_validate(dumped)
        assert isinstance(restored.api_key, StaticSecret)
        assert restored.api_key.get_value() == "sk-direct"


# ---------------------------------------------------------------------------
# Key resolution (_get_litellm_api_key_value)
# ---------------------------------------------------------------------------


class TestLookupSecretResolution:
    """_get_litellm_api_key_value must resolve SecretSource."""

    def test_lookup_secret_resolved_via_http(self):
        """LookupSecret.get_value() makes an HTTP call to resolve the key."""
        lookup = LookupSecret(
            url="https://saas.example.com/key",
            headers={"X-Session-API-Key": "sk"},
        )
        llm = LLM(model="test-model", api_key=lookup)

        mock_response = httpx.Response(
            200,
            text="sk-resolved-key",
            request=httpx.Request("GET", "https://saas.example.com/key"),
        )
        with patch("httpx.get", return_value=mock_response):
            result = llm._get_litellm_api_key_value()

        assert result == "sk-resolved-key"

    def test_static_secret_resolved(self):
        static = StaticSecret(value="sk-static")
        llm = LLM(model="test-model", api_key=static)
        assert llm._get_litellm_api_key_value() == "sk-static"

    def test_secretstr_resolved(self):
        llm = LLM(model="test-model", api_key=SecretStr("sk-secret"))
        assert llm._get_litellm_api_key_value() == "sk-secret"

    def test_none_returns_none(self):
        llm = LLM(model="test-model")
        assert llm._get_litellm_api_key_value() is None

    def test_bedrock_lookup_secret_not_forwarded(self):
        """Bedrock models should NOT forward api_key to litellm, even with LookupSecret."""
        lookup = LookupSecret(
            url="https://saas.example.com/key",
            headers={"X-Session-API-Key": "sk"},
        )
        llm = LLM(model="bedrock/anthropic.claude-3-sonnet-20240229-v1:0", api_key=lookup)

        mock_response = httpx.Response(
            200,
            text="sk-resolved",
            request=httpx.Request("GET", "https://saas.example.com/key"),
        )
        with patch("httpx.get", return_value=mock_response):
            result = llm._get_litellm_api_key_value()

        assert result is None


# ---------------------------------------------------------------------------
# Model info (should not eagerly resolve LookupSecret)
# ---------------------------------------------------------------------------


class TestModelInfoWithLookupSecret:
    """_init_model_info_and_caps should NOT resolve LookupSecret eagerly."""

    def test_no_network_call_during_init(self):
        """Creating an LLM with LookupSecret should not trigger HTTP calls."""
        lookup = LookupSecret(
            url="https://saas.example.com/key",
            headers={"X-Session-API-Key": "sk"},
        )
        # If get_value() is called during __init__, this would fail
        with patch.object(
            LookupSecret, "get_value", side_effect=RuntimeError("should not be called")
        ):
            llm = LLM(model="test-model", api_key=lookup)
            assert llm.api_key is not None
