"""Managed-proxy model-info lookups without credentials emit no request."""

from unittest.mock import MagicMock, patch

from openhands.sdk.llm.utils.model_info import _get_model_info_from_litellm_proxy
from openhands.sdk.llm.utils.openhands_provider import (
    OPENHANDS_LLM_PROXY_BASE_URL,
)


def _proxy_response(payload):
    response = MagicMock()
    response.json.return_value = payload
    return response


def _lookup(secret_api_key, base_url, cache_key):
    return _get_model_info_from_litellm_proxy(
        secret_api_key=secret_api_key,
        base_url=base_url,
        model="litellm_proxy/some-model",
        cache_key=cache_key,
    )


def test_managed_keyless_lookup_skips_request():
    with patch("openhands.sdk.llm.utils.model_info.httpx.get") as mock_get:
        result = _lookup(None, OPENHANDS_LLM_PROXY_BASE_URL, 201)
    mock_get.assert_not_called()
    assert result is None


def test_managed_empty_key_lookup_skips_request():
    with patch("openhands.sdk.llm.utils.model_info.httpx.get") as mock_get:
        result = _lookup("", OPENHANDS_LLM_PROXY_BASE_URL, 202)
    mock_get.assert_not_called()
    assert result is None


def test_managed_blank_key_lookup_skips_request():
    with patch("openhands.sdk.llm.utils.model_info.httpx.get") as mock_get:
        result = _lookup("   ", OPENHANDS_LLM_PROXY_BASE_URL, 203)
    mock_get.assert_not_called()
    assert result is None


def test_managed_v1_suffixed_url_lookup_skips_request():
    with patch("openhands.sdk.llm.utils.model_info.httpx.get") as mock_get:
        result = _lookup(None, f"{OPENHANDS_LLM_PROXY_BASE_URL}/v1/", 204)
    mock_get.assert_not_called()
    assert result is None


def test_managed_keyed_lookup_sends_authorization_header():
    payload = {"data": [{"model_name": "some-model", "model_info": {"key": "abc"}}]}
    with patch(
        "openhands.sdk.llm.utils.model_info.httpx.get",
        return_value=_proxy_response(payload),
    ) as mock_get:
        result = _lookup("secret", OPENHANDS_LLM_PROXY_BASE_URL, 205)
    mock_get.assert_called_once_with(
        f"{OPENHANDS_LLM_PROXY_BASE_URL}/v1/model/info",
        headers={"Authorization": "Bearer secret"},
    )
    assert result == {"key": "abc"}


def test_generic_proxy_keyless_lookup_still_emits_request():
    """Generic proxies are untouched: an open proxy may serve model info
    without credentials, so the lookup must still be attempted."""
    with patch(
        "openhands.sdk.llm.utils.model_info.httpx.get",
        return_value=_proxy_response({"data": []}),
    ) as mock_get:
        result = _lookup(None, "http://proxy.local:4000", 206)
    mock_get.assert_called_once_with(
        "http://proxy.local:4000/v1/model/info", headers={}
    )
    assert result is None
