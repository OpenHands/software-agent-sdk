"""Exercise managed-key registration and SDK recovery over real HTTP."""

import asyncio
import json
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread

import pytest

from openhands.agent_server.managed_llm_key import register_managed_llm_key_refresh
from openhands.sdk import LLM, Agent
from openhands.sdk.llm import Message, TextContent
from openhands.sdk.llm.exceptions import LLMAuthenticationError


@dataclass
class RecoveryProvider:
    url: str = ""
    refresh_status: int = 200
    refresh_key: str = "fresh-key"
    reject_fresh_key: bool = False
    refresh_timed_out: bool = False
    requests: list[str] = field(default_factory=list)
    refresh_headers: list[str | None] = field(default_factory=list)
    refresh_started: Event = field(default_factory=Event)
    release_refresh: Event = field(default_factory=Event)


@pytest.fixture
def recovery_provider(monkeypatch):
    provider = RecoveryProvider()
    provider.release_refresh.set()

    class Handler(BaseHTTPRequestHandler):
        def reply(self, status, body):
            payload = (
                body.encode() if isinstance(body, str) else json.dumps(body).encode()
            )
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            provider.refresh_headers.append(self.headers.get("X-Session-API-Key"))
            provider.refresh_started.set()
            if not provider.release_refresh.wait(timeout=5):
                provider.refresh_timed_out = True
            self.reply(provider.refresh_status, provider.refresh_key)

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            key = self.headers.get("Authorization", "")
            provider.requests.append(key)
            if key != "Bearer fresh-key" or provider.reject_fresh_key:
                self.reply(401, {"error": {"message": "token_not_found_in_db"}})
                return
            self.reply(
                200,
                {
                    "id": "recovered",
                    "object": "chat.completion",
                    "created": 1,
                    "model": "gpt-4o",
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {"role": "assistant", "content": "resumed"},
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 1,
                        "completion_tokens": 1,
                        "total_tokens": 2,
                    },
                },
            )

        def log_message(self, format, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        provider.url = f"http://127.0.0.1:{server.server_port}"
        monkeypatch.setenv("OH_LLM_API_KEY_REFRESH_URL", provider.url + "/current-key")
        monkeypatch.setenv("OH_LLM_API_KEY_REFRESH_BASE_URLS", provider.url + "/v1")
        monkeypatch.setenv(
            "OH_LLM_API_KEY_REFRESH_HEADERS",
            '{"X-Session-API-Key":"${OH_SESSION_API_KEYS_0}"}',
        )
        monkeypatch.setenv("OH_SESSION_API_KEYS_0", "fixture-session")
        try:
            yield provider
        finally:
            provider.release_refresh.set()
            server.shutdown()
            thread.join(timeout=5)


@pytest.fixture
def managed_llm(recovery_provider):
    llm = LLM(
        model="openai/gpt-4o",
        api_key="stale-key",
        base_url=recovery_provider.url + "/v1",
        num_retries=3,
        retry_min_wait=0,
        retry_max_wait=0,
        timeout=5,
    )
    assert register_managed_llm_key_refresh(Agent(llm=llm, tools=[])) == 1
    return llm


def messages():
    return [Message(role="user", content=[TextContent(text="continue")])]


@pytest.mark.parametrize("use_async", [False, True])
@pytest.mark.parametrize(
    "outcome", ["success", "unavailable", "empty", "same", "rejected"]
)
async def test_managed_refresh_is_bounded_over_http(
    recovery_provider, managed_llm, use_async, outcome
):
    provider = recovery_provider
    if outcome == "unavailable":
        provider.refresh_status = 503
    elif outcome == "empty":
        provider.refresh_key = "   "
    elif outcome == "same":
        provider.refresh_key = "stale-key"
    elif outcome == "rejected":
        provider.reject_fresh_key = True

    async def call():
        if use_async:
            return await managed_llm.acompletion(messages())
        return managed_llm.completion(messages())

    if outcome == "success":
        response = await call()
        assert response.message.content == [TextContent(text="resumed")]
    else:
        with pytest.raises(LLMAuthenticationError, match="token_not_found_in_db"):
            await call()

    expected = ["Bearer stale-key"]
    if outcome in {"success", "rejected"}:
        expected.append("Bearer fresh-key")
    assert provider.requests == expected
    assert provider.refresh_headers == ["fixture-session"]


async def test_concurrent_calls_recover_independently(recovery_provider, managed_llm):
    responses = await asyncio.gather(
        *(managed_llm.acompletion(messages()) for _ in range(4))
    )
    assert all(r.message.content == [TextContent(text="resumed")] for r in responses)
    assert recovery_provider.requests.count("Bearer stale-key") == 4
    assert recovery_provider.requests.count("Bearer fresh-key") == 4
    assert recovery_provider.refresh_headers == ["fixture-session"] * 4


async def test_refresh_does_not_block_event_loop(recovery_provider, managed_llm):
    recovery_provider.release_refresh.clear()
    task = asyncio.create_task(managed_llm.acompletion(messages()))
    try:
        started = await asyncio.to_thread(
            recovery_provider.refresh_started.wait, timeout=3
        )
        assert started
        assert not recovery_provider.refresh_timed_out
        assert not recovery_provider.release_refresh.is_set()
        assert not task.done()
        recovery_provider.release_refresh.set()
        response = await asyncio.wait_for(task, timeout=5)
        assert response.message.content == [TextContent(text="resumed")]
    finally:
        recovery_provider.release_refresh.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
