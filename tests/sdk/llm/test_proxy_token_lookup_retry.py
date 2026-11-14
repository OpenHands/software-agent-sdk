"""Offline HTTP coverage of bounded proxy token-lookup recovery."""

import hashlib
import json
import logging
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
from litellm.exceptions import AuthenticationError
from litellm.types.llms.openai import OutputTextDeltaEvent, ResponsesAPIStreamEvents
from litellm.types.utils import Delta, ModelResponseStream, StreamingChoices
from pydantic import SecretStr

from openhands.sdk.event.conversation_error import ConversationErrorEvent
from openhands.sdk.llm import LLM, Message, TextContent
from openhands.sdk.llm.exceptions import LLMAuthenticationError, LLMRateLimitError
from openhands.sdk.llm.fallback_strategy import FallbackStrategy


KEY = "sk-fabricated-lookup-regression-token"
KEY_HASH = hashlib.sha256(KEY.encode()).hexdigest()
LOOKUP_ERROR = (
    "Authentication Error, Invalid proxy server token passed. "
    f"Received API Key = {KEY}, Key Hash (Token) = {KEY_HASH}. "
    "Unable to find token in cache or `LiteLLM_VerificationTokenTable`"
)


@pytest.fixture
def recovery_logs(caplog, suppress_logging, monkeypatch):
    logger = logging.getLogger("openhands.sdk.llm.llm")
    monkeypatch.setattr("openhands.sdk.llm.llm.logger", logger)
    caplog.set_level(logging.DEBUG, logger=logger.name)
    return caplog


@pytest.fixture
def proxy():
    replies = deque()
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.path, self.headers.get("Authorization"), body))
            status, payload = replies.popleft() if replies else (500, {})
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", replies, requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.fixture(params=["completion", "acompletion", "responses", "aresponses"])
def api(request):
    return request.param


def success(api):
    if "responses" not in api:
        return {
            "id": "chat-1",
            "object": "chat.completion",
            "created": 1,
            "model": "gpt-4o",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "recovered"},
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
    return {
        "id": "resp-1",
        "object": "response",
        "created_at": 1,
        "model": "gpt-4o",
        "status": "completed",
        "parallel_tool_calls": False,
        "tool_choice": "auto",
        "tools": [],
        "output": [
            {
                "id": "msg-1",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [
                    {"type": "output_text", "text": "recovered", "annotations": []}
                ],
            }
        ],
        "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
    }


def error(message=LOOKUP_ERROR, status=401):
    return status, {
        "error": {
            "message": message,
            "type": "authentication_error",
            "code": str(status),
        }
    }


def make_llm(base_url, **kwargs):
    return LLM(
        model="litellm_proxy/gpt-4o",
        base_url=base_url,
        api_key=SecretStr(KEY),
        usage_id="lookup-http",
        num_retries=kwargs.pop("num_retries", 3),
        retry_min_wait=0,
        retry_max_wait=0,
        retry_multiplier=0,
        **kwargs,
    )


async def call(llm, api, **kwargs):
    messages = [
        Message(role="system", content=[TextContent(text="Be helpful.")]),
        Message(role="user", content=[TextContent(text="Hello")]),
    ]
    if api == "completion":
        return llm.completion(messages, **kwargs)
    if api == "acompletion":
        return await llm.acompletion(messages, **kwargs)
    if api == "responses":
        return llm.responses(messages, **kwargs)
    return await llm.aresponses(messages, **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize("hook", ["absent", "unchanged", "changed"])
async def test_lookup_recovers_without_exposing_credentials(
    proxy, api, hook, recovery_logs
):
    base_url, replies, requests = proxy
    llm = make_llm(base_url)
    refreshed_key = "sk-fabricated-refreshed-token"
    if hook != "absent":
        llm.set_api_key_refresh_hook(
            lambda: KEY if hook == "unchanged" else refreshed_key
        )
    caplog = recovery_logs
    # A second public call has its own one-attempt allowance.
    for _ in range(2):
        replies.extend([error(), (200, success(api))])
        result = await call(llm, api)
        assert result.message.content == [TextContent(text="recovered")]
    assert len(requests) == 4
    assert [r[1] for r in requests] == [
        f"Bearer {KEY}",
        f"Bearer {refreshed_key if hook == 'changed' else KEY}",
    ] * 2
    if hook != "changed":
        assert (
            caplog.text.count("Proxy token lookup authentication retry: attempt=1") == 2
        )
        assert (
            caplog.text.count("Proxy token lookup authentication recovered: attempt=1")
            == 2
        )
    assert all(value not in caplog.text for value in [KEY, KEY_HASH, refreshed_key])


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["persistent", "disabled", "generic"])
async def test_terminal_auth_keeps_classification(proxy, api, scenario):
    base_url, replies, requests = proxy
    llm = make_llm(base_url, num_retries=0 if scenario == "disabled" else 3)
    message = "Incorrect API key provided" if scenario == "generic" else LOOKUP_ERROR
    replies.extend([error(message), error(message), (200, success(api))])
    with pytest.raises(LLMAuthenticationError) as caught:
        await call(llm, api)
    assert len(requests) == (2 if scenario == "persistent" else 1)
    event = ConversationErrorEvent(
        source="environment", code=type(caught.value).__name__, detail=str(caught.value)
    )
    assert event.classification is not None
    assert event.classification.kind == "auth"
    assert event.classification.retryable is False
    assert event.classification.user_action == "settings"


@pytest.mark.asyncio
async def test_budget_after_lookup_stops_without_fallback(proxy, api, tmp_path):
    base_url, replies, requests = proxy
    llm = make_llm(
        base_url,
        fallback_strategy=FallbackStrategy(
            fallback_llms=["must-not-run"], profile_store_dir=tmp_path
        ),
    )
    replies.extend(
        [error(), error("Budget has been exceeded!", status=429), (200, success(api))]
    )
    with pytest.raises(LLMRateLimitError):
        await call(llm, api)
    assert len(requests) == 2


@pytest.mark.asyncio
async def test_streaming_lookup_does_not_replay(proxy, api):
    base_url, replies, requests = proxy
    llm = make_llm(base_url, stream=True)
    replies.extend([error(), (200, success(api))])
    chunks = []
    with pytest.raises(LLMAuthenticationError):
        await call(llm, api, on_token=chunks.append)
    assert len(requests) == 1
    assert chunks == []


@pytest.mark.asyncio
async def test_transient_after_lookup_can_fall_back(proxy, api, tmp_path):
    base_url, replies, requests = proxy
    fallback = make_llm(base_url)
    fallback_profile = tmp_path / "fallback.json"
    fallback_profile.write_text(
        fallback.model_dump_json(context={"expose_secrets": True})
    )
    llm = make_llm(
        base_url,
        num_retries=1,
        fallback_strategy=FallbackStrategy(
            fallback_llms=["fallback"], profile_store_dir=tmp_path
        ),
    )
    replies.extend(
        [error(), error("Provider unavailable", status=503), (200, success(api))]
    )
    result = await call(llm, api)
    assert result.message.content == [TextContent(text="recovered")]
    assert len(requests) == 3


@pytest.mark.asyncio
async def test_subscription_lookup_has_no_same_key_retry(proxy, api, monkeypatch):
    base_url, replies, requests = proxy

    def auth_values(self):
        return KEY, {}

    async def async_auth_values(self):
        return KEY, {}

    # Keep this test independent of the host's subscription credential store.
    monkeypatch.setattr(LLM, "_get_litellm_auth_values", auth_values)
    monkeypatch.setattr(LLM, "_aget_litellm_auth_values", async_auth_values)
    llm = make_llm(base_url, auth_type="subscription")
    replies.extend([error(), (200, success(api))])
    with pytest.raises(LLMAuthenticationError):
        await call(llm, api)
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_partial_stream_is_not_replayed(proxy, api, monkeypatch):
    base_url, _, _ = proxy
    llm = make_llm(base_url, stream=True)
    attempts = []

    def stream():
        if "responses" in api:
            yield OutputTextDeltaEvent(
                type=ResponsesAPIStreamEvents.OUTPUT_TEXT_DELTA,
                item_id="msg-1",
                output_index=0,
                content_index=0,
                delta="partial",
            )
        else:
            yield ModelResponseStream(
                choices=[StreamingChoices(delta=Delta(content="partial"))]
            )
        raise AuthenticationError(
            message=LOOKUP_ERROR, llm_provider="litellm_proxy", model="gpt-4o"
        )

    def transport(**kwargs):
        attempts.append(kwargs)
        return stream()

    async def async_stream():
        for chunk in stream():
            yield chunk

    async def async_transport(**kwargs):
        attempts.append(kwargs)
        return async_stream()

    target = {
        "completion": "litellm_completion",
        "acompletion": "litellm_acompletion",
        "responses": "litellm_responses",
        "aresponses": "litellm_aresponses",
    }[api]
    monkeypatch.setattr(
        "openhands.sdk.llm.llm." + target,
        async_transport if api.startswith("a") else transport,
    )
    chunks = []
    with pytest.raises(LLMAuthenticationError):
        await call(llm, api, on_token=chunks.append)
    assert len(attempts) == 1
    assert len(chunks) == 1
    assert chunks[0].choices[0].delta.content == "partial"


@pytest.mark.asyncio
async def test_unmapped_failure_after_lookup_preserves_latest_error(
    proxy, api, monkeypatch
):
    base_url, _, _ = proxy
    llm = make_llm(base_url)
    terminal_error = ValueError("response validation failed")
    errors = deque(
        [
            AuthenticationError(
                message=LOOKUP_ERROR, llm_provider="litellm_proxy", model="gpt-4o"
            ),
            terminal_error,
        ]
    )

    def transport(**kwargs):
        raise errors.popleft()

    async def async_transport(**kwargs):
        return transport(**kwargs)

    target = {
        "completion": "litellm_completion",
        "acompletion": "litellm_acompletion",
        "responses": "litellm_responses",
        "aresponses": "litellm_aresponses",
    }[api]
    monkeypatch.setattr(
        "openhands.sdk.llm.llm." + target,
        async_transport if api.startswith("a") else transport,
    )
    with pytest.raises(ValueError) as caught:
        await call(llm, api)
    assert caught.value is terminal_error
    assert not errors
