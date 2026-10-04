"""Tests for the standalone prompt-enhancement API."""

import asyncio
import threading
import time
from types import SimpleNamespace
from typing import Literal

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from filelock import FileLock
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import SecretStr

from openhands.agent_server import (
    profiles_router as profiles_router_module,
    prompt_enhancement_router as router_module,
)
from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config
from openhands.sdk.llm import LLM, Message, TextContent
from openhands.sdk.llm.exceptions import (
    LLMAuthenticationError,
    LLMBadRequestError,
    LLMError,
    LLMNoResponseError,
    LLMTimeoutError,
)
from openhands.sdk.llm.llm_profile_store import LLMProfileStore
from openhands.sdk.llm.provider_connection_store import ProviderConnectionNotFound


@pytest.fixture
def client_and_store(tmp_path, monkeypatch, request):
    store = LLMProfileStore(base_dir=tmp_path / "profiles")
    monkeypatch.setattr(router_module, "get_llm_profile_store", lambda: store)
    monkeypatch.setattr(profiles_router_module, "get_llm_profile_store", lambda: store)
    monkeypatch.setattr(router_module, "should_enable_observability", lambda: False)
    monkeypatch.delenv("LMNR_INSTRUMENTS", raising=False)
    monkeypatch.delenv("DEBUG_LLM", raising=False)
    root_path = getattr(request, "param", "")
    app = create_app(
        Config(
            static_files_path=None,
            session_api_keys=[],
            web_url=f"https://example.com{root_path}" if root_path else None,
        )
    )
    with TestClient(app) as client:
        yield client, store


def save_profile(
    store: LLMProfileStore,
    name: str = "draft-profile",
    api_mode: Literal["chat", "responses"] = "chat",
) -> None:
    store.save(
        name,
        LLM(
            model="openai/gpt-4.1-mini",
            api_mode=api_mode,
            api_key=SecretStr("sk-profile-secret"),
            log_completions=True,
        ),
        include_secrets=True,
    )


def mock_completion(monkeypatch, text: str, captured: list | None = None) -> None:
    async def complete(llm, messages, tools=None, **kwargs):
        if captured is not None:
            captured.append((llm, messages, tools, kwargs))
        return SimpleNamespace(
            message=Message(role="assistant", content=[TextContent(text=text)])
        )

    monkeypatch.setattr(LLM, "acompletion", complete)


def mock_responses(monkeypatch, text: str, captured: list) -> None:
    async def complete(llm, messages, tools=None, **kwargs):
        captured.append((llm, messages, tools, kwargs))
        return SimpleNamespace(
            message=Message(role="assistant", content=[TextContent(text=text)])
        )

    monkeypatch.setattr(LLM, "aresponses", complete)


def error_code(response) -> str:
    return response.json()["code"]


def test_enhance_uses_server_profile_without_side_effects_or_prompt_logs(
    client_and_store, monkeypatch, caplog
):
    client, store = client_and_store
    save_profile(store)
    client.post("/api/profiles/draft-profile/activate").raise_for_status()

    prompt = (
        "请改进这个请求，保持约束：只改 Vue 页面。\n"
        "保留路径 /src/views/Map.vue、命令 `pnpm test`、URL https://example.com，"
        "以及数字 30。\n```ts\nconst tenantId = 'acme';\n```"
    )
    output = (
        "请改进这个请求，同时保留全部约束：只改 Vue 页面。\n"
        "保留路径 /src/views/Map.vue、命令 `pnpm test`、URL https://example.com，"
        "以及数字 30。\n```ts\nconst tenantId = 'acme';\n```"
    )
    captured = []
    mock_completion(monkeypatch, output, captured)
    conversations_before = client.get("/api/conversations/count").json()
    profile_before = client.get("/api/profiles").json()["active_profile"]
    agent_profile_before = client.get("/api/agent-profiles").json()[
        "active_agent_profile_id"
    ]

    response = client.post(
        "/api/prompt-enhancement/enhance",
        json={"profile_name": "draft-profile", "text": prompt},
    )

    assert response.status_code == 200
    assert response.json() == {"enhanced_text": output}
    llm, messages, tools, kwargs = captured[0]
    assert [message.role for message in messages] == ["system", "user"]
    assert "same language" in messages[0].content[0].text
    assert messages[1].content[0].text == prompt
    assert tools == []
    assert kwargs["max_tokens"] == router_module.MAX_OUTPUT_TOKENS
    assert llm.log_completions is False
    assert llm.num_retries == 0
    assert llm.telemetry.log_enabled is False
    assert llm.telemetry._log_completions_callback is None
    assert store.load("draft-profile").log_completions is True
    assert "sk-profile-secret" not in response.text
    assert client.get("/api/conversations/count").json() == conversations_before
    assert client.get("/api/profiles").json()["active_profile"] == profile_before
    assert (
        client.get("/api/agent-profiles").json()["active_agent_profile_id"]
        == agent_profile_before
    )
    assert prompt not in caplog.text
    assert output not in caplog.text


def test_responses_api_disables_provider_side_storage(client_and_store, monkeypatch):
    client, store = client_and_store
    save_profile(store, api_mode="responses")
    captured = []
    mock_responses(monkeypatch, "Improved draft.", captured)

    response = client.post(
        "/api/prompt-enhancement/enhance",
        json={"profile_name": "draft-profile", "text": "Draft."},
    )

    assert response.status_code == 200
    assert response.json() == {"enhanced_text": "Improved draft."}
    _llm, messages, tools, kwargs = captured[0]
    assert [message.role for message in messages] == ["system", "user"]
    assert tools == []
    assert kwargs["max_tokens"] == router_module.MAX_OUTPUT_TOKENS
    assert kwargs["store"] is False


@pytest.mark.parametrize(
    ("draft", "edited"),
    [
        pytest.param(
            "Improve this request. Keep tenantId=acme, /src/views/Map.vue, "
            "`pnpm test`, https://example.com, 30, and this code:\n"
            "```ts\nconst count = 30;\n```",
            "Clarify this request while keeping tenantId=acme, "
            "/src/views/Map.vue, `pnpm test`, https://example.com, 30, and this "
            "code:\n```ts\nconst count = 30;\n```",
            id="english",
        ),
        pytest.param(
            "次の依頼を明確にしてください。tenantId=acme、/src/views/Map.vue、"
            "`pnpm test`、https://example.com、数値30と次のコードを保持してください。\n"
            "```ts\nconst count = 30;\n```",
            "次の依頼をより明確にしてください。tenantId=acme、/src/views/Map.vue、"
            "`pnpm test`、https://example.com、数値30と次のコードを保持してください。\n"
            "```ts\nconst count = 30;\n```",
            id="japanese",
        ),
    ],
)
def test_enhance_preserves_multilingual_constraints(
    client_and_store, monkeypatch, draft, edited
):
    client, store = client_and_store
    save_profile(store)
    captured = []
    mock_completion(monkeypatch, edited, captured)

    response = client.post(
        "/api/prompt-enhancement/enhance",
        json={"profile_name": "draft-profile", "text": draft},
    )

    assert response.status_code == 200
    assert response.json() == {"enhanced_text": edited}
    _llm, messages, tools, _kwargs = captured[0]
    assert messages[1].content[0].text == draft
    assert "same language" in messages[0].content[0].text
    assert tools == []
    for preserved in (
        "tenantId=acme",
        "/src/views/Map.vue",
        "pnpm test",
        "https://example.com",
        "30",
        "const count = 30;",
    ):
        assert preserved in response.json()["enhanced_text"]


def test_enhance_rejects_empty_and_oversized_input(client_and_store):
    client, _store = client_and_store

    empty = client.post(
        "/api/prompt-enhancement/enhance",
        json={"profile_name": "draft-profile", "text": " \n "},
    )
    oversized = client.post(
        "/api/prompt-enhancement/enhance",
        json={
            "profile_name": "draft-profile",
            "text": "x" * (router_module.MAX_PROMPT_CHARS + 1),
        },
    )

    assert empty.status_code == 400
    assert error_code(empty) == "empty_input"
    assert oversized.status_code == 413
    assert error_code(oversized) == "input_too_large"


def test_prompt_and_provider_error_text_are_not_returned_or_logged(
    client_and_store, monkeypatch, caplog
):
    client, store = client_and_store
    save_profile(store)
    sentinel = "private prompt sentinel 7f87d2"

    async def fail(_llm, _messages, **_kwargs):
        raise RuntimeError(sentinel)

    monkeypatch.setattr(LLM, "acompletion", fail)
    response = client.post(
        "/api/prompt-enhancement/enhance",
        json={"profile_name": "draft-profile", "text": sentinel},
    )

    assert response.status_code == 502
    assert error_code(response) == "provider_error"
    assert sentinel not in response.text
    assert sentinel not in caplog.text


@pytest.mark.parametrize(
    ("failure", "status_code", "code"),
    [
        pytest.param(
            LLMAuthenticationError("credential sentinel 5fd1"),
            422,
            "profile_unavailable",
            id="provider-authentication",
        ),
        pytest.param(
            LLMBadRequestError("configuration sentinel 5fd1"),
            422,
            "unsupported_configuration",
            id="unsupported-configuration",
        ),
        pytest.param(
            LLMNoResponseError("empty output sentinel 5fd1"),
            502,
            "invalid_model_output",
            id="invalid-output",
        ),
        pytest.param(
            LLMTimeoutError("timeout sentinel 5fd1"),
            504,
            "enhancement_timeout",
            id="provider-timeout",
        ),
        pytest.param(
            LLMError("upstream sentinel 5fd1"),
            502,
            "provider_error",
            id="upstream-error",
        ),
    ],
)
def test_provider_errors_have_stable_codes_without_exposing_details(
    client_and_store, monkeypatch, caplog, failure, status_code, code
):
    client, store = client_and_store
    save_profile(store)
    seen = []

    async def fail(llm, **_kwargs):
        seen.append((llm.api_mode, type(failure)))
        raise failure

    monkeypatch.setattr(LLM, "acompletion", fail)
    monkeypatch.setattr(LLM, "aresponses", fail)
    response = client.post(
        "/api/prompt-enhancement/enhance",
        json={"profile_name": "draft-profile", "text": "Private draft."},
    )

    assert seen, "The mocked provider method was not called."
    assert response.status_code == status_code, response.text
    assert error_code(response) == code
    assert "5fd1" not in response.text
    assert "5fd1" not in caplog.text


@pytest.mark.parametrize(
    ("failure", "status_code", "code"),
    [
        pytest.param(
            TimeoutError("profile lock sentinel 8a41"),
            503,
            "profile_store_timeout",
            id="store-timeout",
        ),
        pytest.param(
            ProviderConnectionNotFound("provider connection sentinel 8a41"),
            422,
            "profile_unavailable",
            id="missing-provider-connection",
        ),
        pytest.param(
            ValueError("decrypt sentinel 8a41"),
            422,
            "profile_unavailable",
            id="profile-resolution-error",
        ),
    ],
)
def test_profile_store_errors_have_stable_codes_without_exposing_details(
    client_and_store, monkeypatch, caplog, failure, status_code, code
):
    client, _store = client_and_store

    class FailingProfileStore:
        def load(self, _name, *, cipher=None):
            raise failure

    monkeypatch.setattr(router_module, "get_llm_profile_store", FailingProfileStore)
    response = client.post(
        "/api/prompt-enhancement/enhance",
        json={"profile_name": "draft-profile", "text": "Private draft."},
    )

    assert response.status_code == status_code
    assert error_code(response) == code
    assert "8a41" not in response.text
    assert "8a41" not in caplog.text


def test_enhance_enforces_output_validation_limits_and_timeout(
    client_and_store, monkeypatch
):
    client, store = client_and_store
    save_profile(store)

    mock_completion(monkeypatch, " \n ")
    invalid = client.post(
        "/api/prompt-enhancement/enhance",
        json={"profile_name": "draft-profile", "text": "valid input"},
    )
    assert invalid.status_code == 502
    assert error_code(invalid) == "invalid_model_output"

    mock_completion(monkeypatch, "x" * (router_module.MAX_OUTPUT_CHARS + 1))
    oversized = client.post(
        "/api/prompt-enhancement/enhance",
        json={"profile_name": "draft-profile", "text": "valid input"},
    )
    assert oversized.status_code == 502
    assert error_code(oversized) == "output_too_large"

    mock_completion(monkeypatch, "x" * router_module.MAX_PROMPT_CHARS)
    at_limit = client.post(
        "/api/prompt-enhancement/enhance",
        json={
            "profile_name": "draft-profile",
            "text": "x" * router_module.MAX_PROMPT_CHARS,
        },
    )
    assert at_limit.status_code == 200

    async def hang(_llm, messages, **_kwargs):
        await asyncio.sleep(0.05)

    monkeypatch.setattr(LLM, "acompletion", hang)
    monkeypatch.setattr(router_module, "PROMPT_ENHANCEMENT_TIMEOUT_SECONDS", 0.001)
    timeout = client.post(
        "/api/prompt-enhancement/enhance",
        json={"profile_name": "draft-profile", "text": "valid input"},
    )
    assert timeout.status_code == 504
    assert error_code(timeout) == "enhancement_timeout"

    async def hang_while_loading_profile(_name, _request):
        await asyncio.sleep(0.05)

    monkeypatch.setattr(router_module, "_load_profile", hang_while_loading_profile)
    timeout_during_profile_load = client.post(
        "/api/prompt-enhancement/enhance",
        json={"profile_name": "draft-profile", "text": "valid input"},
    )
    assert timeout_during_profile_load.status_code == 504
    assert error_code(timeout_during_profile_load) == "enhancement_timeout"


def test_missing_profile_is_a_typed_unavailable_result(client_and_store):
    client, _store = client_and_store

    response = client.get("/api/prompt-enhancement/availability/missing-profile")

    assert response.status_code == 200
    assert response.json() == {
        "available": False,
        "code": "profile_not_found",
        "message": "The selected LLM profile was not found.",
    }


def test_openapi_describes_prompt_enhancement_and_input_limit(client_and_store):
    client, _store = client_and_store

    schema = client.get("/openapi.json").json()

    assert "/api/prompt-enhancement/enhance" in schema["paths"]
    text_schema = schema["components"]["schemas"]["PromptEnhancementRequest"][
        "properties"
    ]["text"]
    assert text_schema["maxLength"] == router_module.MAX_PROMPT_CHARS


def test_prompt_enhancement_is_unavailable_when_payload_tracing_is_enabled(
    client_and_store, monkeypatch
):
    client, store = client_and_store
    save_profile(store)
    monkeypatch.delenv("LMNR_INSTRUMENTS", raising=False)
    monkeypatch.delenv("DEBUG_LLM", raising=False)
    called = False

    async def complete(_llm, messages, **_kwargs):
        nonlocal called
        called = True
        return SimpleNamespace(
            message=Message(
                role="assistant",
                content=[TextContent(text=messages[1].content[0].text)],
            )
        )

    monkeypatch.setattr(router_module, "should_enable_observability", lambda: True)
    monkeypatch.setattr(LLM, "acompletion", complete)
    sentinel = "analytics must not receive this 5e93a1"

    availability = client.get("/api/prompt-enhancement/availability/draft-profile")
    response = client.post(
        "/api/prompt-enhancement/enhance",
        json={"profile_name": "draft-profile", "text": sentinel},
    )

    assert availability.status_code == 422
    assert availability.json()["code"] == "unsupported_configuration"
    assert response.status_code == 422
    assert response.json()["code"] == "unsupported_configuration"
    assert sentinel not in response.text
    assert called is False

    monkeypatch.setattr(router_module, "should_enable_observability", lambda: False)
    monkeypatch.setenv("DEBUG_LLM", "true")
    debug_sentinel = "debug prompt must remain private 88071a"
    debug_response = client.post(
        "/api/prompt-enhancement/enhance",
        json={"profile_name": "draft-profile", "text": debug_sentinel},
    )
    assert debug_response.status_code == 422
    assert debug_response.json()["code"] == "unsupported_configuration"
    assert debug_sentinel not in debug_response.text
    assert called is False


def test_prompt_enhancement_blocks_exception_tracing(client_and_store, monkeypatch):
    from openhands.sdk.observability.laminar import should_enable_observability

    client, store = client_and_store
    save_profile(store)
    monkeypatch.setattr(
        router_module, "should_enable_observability", should_enable_observability
    )
    monkeypatch.setenv(
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "http://localhost:4318/v1/traces"
    )
    monkeypatch.setenv("LMNR_INSTRUMENTS", "opentelemetry")
    monkeypatch.delenv("DEBUG_LLM", raising=False)
    sentinel = "Private draft sentinel f840"
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("prompt-enhancement-test")
    monkeypatch.setattr(
        "openhands.sdk.observability.laminar.llm_call_span",
        tracer.start_as_current_span,
    )

    async def fail_transport(_llm, **_kwargs):
        raise RuntimeError(sentinel)

    monkeypatch.setattr(LLM, "_atransport_call", fail_transport)

    response = client.post(
        "/api/prompt-enhancement/enhance",
        json={"profile_name": "draft-profile", "text": sentinel},
    )

    events = [
        dict(event.attributes or {})
        for span in exporter.get_finished_spans()
        for event in span.events
    ]
    assert sentinel not in str(events)
    assert response.status_code == 422
    assert error_code(response) == "unsupported_configuration"
    assert sentinel not in response.text


def test_profile_lock_wait_respects_endpoint_timeout(client_and_store, monkeypatch):
    client, store = client_and_store
    save_profile(store)
    acquired = threading.Event()

    def hold_lock():
        with FileLock(store.base_dir / ".profiles.lock"):
            acquired.set()
            time.sleep(0.3)

    worker = threading.Thread(target=hold_lock)
    worker.start()
    try:
        assert acquired.wait(timeout=2)
        monkeypatch.setattr(router_module, "PROMPT_ENHANCEMENT_TIMEOUT_SECONDS", 0.02)
        mock_completion(monkeypatch, "Edited.")
        response = client.post(
            "/api/prompt-enhancement/enhance",
            json={"profile_name": "draft-profile", "text": "Private draft."},
        )
        assert response.status_code == 504
        assert error_code(response) == "enhancement_timeout"
    finally:
        worker.join()


@pytest.mark.parametrize("instruments", ["", "opentelemetry,litellm"])
def test_prompt_enhancement_fails_closed_for_unsafe_instrumentation(
    client_and_store, monkeypatch, instruments
):
    from openhands.sdk.observability.laminar import should_enable_observability

    client, store = client_and_store
    save_profile(store)
    monkeypatch.setattr(
        router_module, "should_enable_observability", should_enable_observability
    )
    monkeypatch.setenv(
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "http://localhost:4318/v1/traces"
    )
    monkeypatch.setenv("LMNR_INSTRUMENTS", instruments)
    captured = []
    mock_completion(monkeypatch, "Must not be called.", captured)

    response = client.post(
        "/api/prompt-enhancement/enhance",
        json={"profile_name": "draft-profile", "text": "Private draft."},
    )

    assert response.status_code == 422
    assert error_code(response) == "unsupported_configuration"
    assert captured == []


def test_enhance_propagates_cancellation(monkeypatch):
    monkeypatch.setattr(router_module, "should_enable_observability", lambda: False)
    monkeypatch.delenv("DEBUG_LLM", raising=False)

    async def load_profile(_name, _request):
        return LLM(model="openai/gpt-4.1-mini", api_mode="chat")

    called = False

    async def cancel(_llm, **_kwargs):
        nonlocal called
        called = True
        raise asyncio.CancelledError

    monkeypatch.setattr(router_module, "_load_profile", load_profile)
    monkeypatch.setattr(LLM, "acompletion", cancel)
    monkeypatch.setattr(LLM, "aresponses", cancel)
    request = Request({"type": "http"})
    body = router_module.PromptEnhancementRequest(
        profile_name="draft-profile", text="Private draft."
    )

    async def invoke():
        await router_module.enhance_prompt(request, body)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(invoke())
    assert called


def test_prompt_enhancement_requires_the_agent_server_session_key():
    app = create_app(
        Config(static_files_path=None, session_api_keys=["test-session-key"])
    )
    with TestClient(app) as client:
        unauthenticated = client.get(
            "/api/prompt-enhancement/availability/missing-profile"
        )
        authenticated = client.get(
            "/api/prompt-enhancement/availability/missing-profile",
            headers={"X-Session-API-Key": "test-session-key"},
        )

    assert unauthenticated.status_code == 401
    assert authenticated.status_code == 200
    assert authenticated.json()["code"] == "profile_not_found"


@pytest.mark.parametrize(
    "client_and_store", ["", "/runtime/123", "/runtime/456/api"], indirect=True
)
def test_invalid_request_fields_are_rejected_without_echoing_values(
    client_and_store, caplog
):
    client, _store = client_and_store
    sentinel = "invalid untrusted value a11c02"

    wrong_type = client.post(
        f"{client.app.root_path}/api/prompt-enhancement/enhance",
        json={"profile_name": "draft-profile", "text": {"bad": sentinel}},
    )
    unexpected_field = client.post(
        f"{client.app.root_path}/api/prompt-enhancement/enhance",
        json={
            "profile_name": "draft-profile",
            "text": "valid draft",
            "admin": sentinel,
        },
    )

    for response in (wrong_type, unexpected_field):
        assert response.status_code == 422
        assert all("input" not in error for error in response.json()["detail"])
    assert sentinel not in wrong_type.text + unexpected_field.text + caplog.text


@pytest.mark.parametrize(
    "client_and_store", ["", "/runtime/123", "/runtime/456/api"], indirect=True
)
@pytest.mark.parametrize(
    ("payload", "expected_status"),
    [
        (b'{"profile_name":"draft-profile","text":"parser sentinel"', 422),
        (b'{"profile_name":"draft-profile","text":"parser sentinel\xff"}', 400),
        (b'{"profile_name":"draft-profile","text":' + b"9" * 5_000 + b"}", 400),
        (
            b'{"profile_name":"draft-profile","text":'
            + b"[" * 1_200
            + b"0"
            + b"]" * 1_200
            + b"}",
            422,
        ),
    ],
)
def test_malformed_or_resource_heavy_json_does_not_echo_request_body(
    client_and_store, caplog, payload, expected_status
):
    client, _store = client_and_store

    response = client.post(
        f"{client.app.root_path}/api/prompt-enhancement/enhance",
        content=payload,
        headers={"content-type": "application/json"},
    )

    assert response.status_code == expected_status
    if expected_status == 422:
        assert all("input" not in error for error in response.json()["detail"])
    assert "parser sentinel" not in response.text
    assert b"9" * 5_000 not in response.content
    assert "parser sentinel" not in caplog.text
    assert "9" * 5_000 not in caplog.text


def test_duplicate_json_fields_follow_the_json_parser_and_do_not_echo_values(
    client_and_store,
):
    client, _store = client_and_store

    response = client.post(
        "/api/prompt-enhancement/enhance",
        content=(
            b'{"profile_name":"sentinel-profile",'
            b'"profile_name":"missing-profile","text":"draft"}'
        ),
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 404
    assert error_code(response) == "profile_not_found"
    assert "sentinel-profile" not in response.text
