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
from openhands.sdk.secret import LookupSecret, StaticSecret


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
        assert isinstance(llm.api_key, LookupSecret)
        assert llm.api_key.description == "SaaS LLM key"

    def test_static_secret_accepted(self):
        static = StaticSecret(value=SecretStr("sk-direct-key"))
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
        static = StaticSecret(value=SecretStr("sk-direct"))
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
        static = StaticSecret(value=SecretStr("sk-static"))
        llm = LLM(model="test-model", api_key=static)
        assert llm._get_litellm_api_key_value() == "sk-static"

    def test_secretstr_resolved(self):
        llm = LLM(model="test-model", api_key=SecretStr("sk-secret"))
        assert llm._get_litellm_api_key_value() == "sk-secret"

    def test_none_returns_none(self):
        llm = LLM(model="test-model")
        assert llm._get_litellm_api_key_value() is None

    def test_bedrock_lookup_secret_not_forwarded(self):
        """Bedrock models should NOT forward api_key to litellm."""
        lookup = LookupSecret(
            url="https://saas.example.com/key",
            headers={"X-Session-API-Key": "sk"},
        )
        llm = LLM(
            model="bedrock/anthropic.claude-3-sonnet-20240229-v1:0", api_key=lookup
        )

        mock_response = httpx.Response(
            200,
            text="sk-resolved",
            request=httpx.Request("GET", "https://saas.example.com/key"),
        )
        with patch("httpx.get", return_value=mock_response):
            result = llm._get_litellm_api_key_value()

        assert result is None


# ---------------------------------------------------------------------------
# env_headers enforcement
# ---------------------------------------------------------------------------


class TestEnvHeadersEnforcement:
    """env_headers ensures secrets resolve only where the env var is set."""

    def test_env_headers_resolved_from_environment(self):
        """LookupSecret with env_headers reads the header from os.environ."""
        lookup = LookupSecret(
            url="https://saas.example.com/key",
            env_headers={"X-Session-API-Key": "SESSION_API_KEY"},
        )
        llm = LLM(model="test-model", api_key=lookup)

        mock_response = httpx.Response(
            200,
            text="sk-env-resolved",
            request=httpx.Request("GET", "https://saas.example.com/key"),
        )
        with (
            patch.dict("os.environ", {"SESSION_API_KEY": "sandbox-session-key-123"}),
            patch("httpx.get", return_value=mock_response) as mock_get,
        ):
            result = llm._get_litellm_api_key_value()

        assert result == "sk-env-resolved"
        # Verify the env-resolved header was passed
        call_headers = mock_get.call_args[1]["headers"]
        assert call_headers["X-Session-API-Key"] == "sandbox-session-key-123"

    def test_env_headers_not_in_serialized_output(self):
        """env_headers contain env var NAMES, not secret values."""
        lookup = LookupSecret(
            url="https://saas.example.com/key",
            env_headers={"X-Session-API-Key": "SESSION_API_KEY"},
        )
        llm = LLM(model="test-model", api_key=lookup)
        dumped = llm.model_dump(mode="json", context={"expose_secrets": True})

        api_key_dict = dumped["api_key"]
        assert api_key_dict["env_headers"] == {"X-Session-API-Key": "SESSION_API_KEY"}
        # No raw session key anywhere in the serialized output
        assert "headers" not in api_key_dict or not api_key_dict["headers"]

    def test_client_side_resolution_fails_without_env_var(self):
        """Without SESSION_API_KEY in env, the header is not sent → 401."""
        import os as _os

        lookup = LookupSecret(
            url="https://saas.example.com/key",
            env_headers={"X-Session-API-Key": "SESSION_API_KEY"},
        )
        llm = LLM(model="test-model", api_key=lookup)

        mock_401 = httpx.Response(
            401,
            text="Unauthorized",
            request=httpx.Request("GET", "https://saas.example.com/key"),
        )
        # Ensure SESSION_API_KEY is not in env
        saved = _os.environ.pop("SESSION_API_KEY", None)
        try:
            with patch("httpx.get", return_value=mock_401):
                with pytest.raises(httpx.HTTPStatusError):
                    llm._get_litellm_api_key_value()
        finally:
            if saved is not None:
                _os.environ["SESSION_API_KEY"] = saved


# ---------------------------------------------------------------------------
# Model info (should not eagerly resolve LookupSecret)
# ---------------------------------------------------------------------------


class TestModelInfoWithLookupSecret:
    """_init_model_info_and_caps should NOT resolve LookupSecret eagerly."""

    def test_no_network_call_during_init(self):
        """Creating an LLM with LookupSecret should not trigger HTTP calls."""
        lookup = LookupSecret(
            url="https://saas.example.com/key",
            env_headers={"X-Session-API-Key": "SESSION_API_KEY"},
        )
        # If get_value() is called during __init__, this would fail
        with patch("httpx.get", side_effect=RuntimeError("should not be called")):
            llm = LLM(model="test-model", api_key=lookup)
            assert llm.api_key is not None
