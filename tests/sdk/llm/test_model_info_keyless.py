"""Keyless proxy model-info lookups must not emit unauthenticated requests."""

from unittest.mock import MagicMock, patch

from openhands.sdk.llm.utils.model_info import _get_model_info_from_litellm_proxy


def _proxy_response(payload):
    response = MagicMock()
    response.json.return_value = payload
    return response


def test_keyless_lookup_skips_request():
    with patch(
        "openhands.sdk.llm.utils.model_info.httpx.get"
    ) as mock_get:
        result = _get_model_info_from_litellm_proxy(
            secret_api_key=None,
            base_url="http://proxy.local:4000",
            model="litellm_proxy/some-model",
            cache_key=101,
        )
    mock_get.assert_not_called()
    assert result is None


def test_keyless_empty_string_lookup_skips_request():
    with patch(
        "openhands.sdk.llm.utils.model_info.httpx.get"
    ) as mock_get:
        result = _get_model_info_from_litellm_proxy(
            secret_api_key="",
            base_url="http://proxy.local:4000",
            model="litellm_proxy/some-model",
            cache_key=102,
        )
    mock_get.assert_not_called()
    assert result is None


def test_keyed_lookup_sends_authorization_header():
    payload = {
        "data": [
            {"model_name": "some-model", "model_info": {"key": "abc"}}
        ]
    }
    with patch(
        "openhands.sdk.llm.utils.model_info.httpx.get",
        return_value=_proxy_response(payload),
    ) as mock_get:
        result = _get_model_info_from_litellm_proxy(
            secret_api_key="secret",
            base_url="http://proxy.local:4000",
            model="litellm_proxy/some-model",
            cache_key=103,
        )
    mock_get.assert_called_once_with(
        "http://proxy.local:4000/v1/model/info",
        headers={"Authorization": "Bearer secret"},
    )
    assert result == {"key": "abc"}
