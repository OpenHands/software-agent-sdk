from unittest.mock import Mock

import httpx
import pytest

from openhands.sdk.conversation.impl.remote_conversation import _send_request
from openhands.sdk.utils.redact import (
    http_error_log_content,
    is_secret_key,
    sanitize_dict,
)


class TestIsSecretKey:
    """Tests for the unified is_secret_key function."""

    @pytest.mark.parametrize(
        "key",
        [
            "api_key",
            "API_KEY",
            "Api-Key",
            "x-api-key",
            "Authorization",
            "AUTHORIZATION",
            "x-access-token",
            "X-Token",
            "password",
            "PASSWORD",
            "user_password",
            "secret",
            "client_secret",
            "Cookie",
            "session_id",
            "credential",
        ],
    )
    def test_detects_secret_keys(self, key):
        assert is_secret_key(key) is True

    @pytest.mark.parametrize(
        "key",
        [
            "user_name",
            "email",
            "status",
            "detail",
            "message",
            "input",
            "output",
            "Author",  # Should NOT be redacted (false positive check)
        ],
    )
    def test_ignores_non_secret_keys(self, key):
        assert is_secret_key(key) is False


class TestSanitizeDict:
    """Tests for the sanitize_dict function."""

    def test_redacts_secret_keys(self):
        data = {"api_key": "my-secret", "name": "test"}
        result = sanitize_dict(data)
        assert result == {"api_key": "<redacted>", "name": "test"}

    def test_redacts_all_values_in_environment_keys(self):
        data = {
            "environment": {"VAR1": "val1", "VAR2": "val2"},
            "acp_env": {"NESTED": {"deep": "value"}},
        }
        result = sanitize_dict(data)
        assert result["environment"] == {"VAR1": "<redacted>", "VAR2": "<redacted>"}
        assert result["acp_env"] == {"NESTED": {"deep": "<redacted>"}}

    def test_preserves_structure_in_lists(self):
        data = [{"api_key": "secret"}, {"name": "test"}]
        result = sanitize_dict(data)
        assert result == [{"api_key": "<redacted>"}, {"name": "test"}]

    def test_handles_nested_structures(self):
        data = {
            "detail": [
                {
                    "input": {
                        "agent": {"llm": {"api_key": "secret"}},
                        "headers": {"X-Token": "token123"},
                    }
                }
            ]
        }
        result = sanitize_dict(data)
        assert result["detail"][0]["input"]["agent"]["llm"]["api_key"] == "<redacted>"
        assert result["detail"][0]["input"]["headers"] == {"X-Token": "<redacted>"}


class TestHttpErrorLogContent:
    """Tests for the http_error_log_content function."""

    def test_sanitizes_json_response(self):
        request = httpx.Request("POST", "http://example.com")
        response = httpx.Response(
            422, request=request, json={"api_key": "secret", "message": "error"}
        )
        result = http_error_log_content(response)
        assert result == {"api_key": "<redacted>", "message": "error"}

    def test_handles_non_json_response(self):
        request = httpx.Request("GET", "http://example.com")
        response = httpx.Response(500, request=request, text="Internal Server Error")
        result = http_error_log_content(response)
        assert "<non-JSON response body omitted" in result
        assert "21 chars" in result


def test_send_request_redacts_structured_error_content(caplog):
    request = httpx.Request("POST", "http://localhost:8000/api/conversations")
    response = httpx.Response(
        422,
        request=request,
        json={
            "detail": [
                {
                    "input": {
                        "agent": {
                            "llm": {"api_key": "secret-api-key"},
                            "acp_env": {"OPENAI_API_KEY": "secret-openai-key"},
                        },
                        "environment": {
                            "LMNR_PROJECT_API_KEY": "secret-lmnr-key",
                            "LMNR_SPAN_CONTEXT": "span-context",
                        },
                    }
                }
            ]
        },
    )
    client = Mock(spec=httpx.Client)
    client.request.return_value = response

    with pytest.raises(httpx.HTTPStatusError):
        with caplog.at_level("ERROR"):
            _send_request(client, "POST", "/api/conversations")

    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert "secret-api-key" not in log_text
    assert "secret-openai-key" not in log_text
    assert "secret-lmnr-key" not in log_text
    assert "span-context" not in log_text
    assert "'api_key': '<redacted>'" in log_text
    assert "'OPENAI_API_KEY': '<redacted>'" in log_text
    assert "'LMNR_PROJECT_API_KEY': '<redacted>'" in log_text


def test_send_request_omits_non_json_error_body(caplog):
    request = httpx.Request("GET", "http://localhost:8000/api/conversations")
    response = httpx.Response(
        500,
        request=request,
        text="Authorization: Bearer top-secret-token",
    )
    client = Mock(spec=httpx.Client)
    client.request.return_value = response

    with pytest.raises(httpx.HTTPStatusError):
        with caplog.at_level("ERROR"):
            _send_request(client, "GET", "/api/conversations")

    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert "top-secret-token" not in log_text
    assert "<non-JSON response body omitted" in log_text
