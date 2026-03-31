from unittest.mock import Mock

import httpx
import pytest

from openhands.sdk.conversation.impl.remote_conversation import _send_request


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
