"""Fallback requests keep the caller's role and accounting session."""

import json
from contextlib import nullcontext
from pathlib import Path
from typing import Any, Literal
from unittest.mock import AsyncMock

import pytest
from litellm.exceptions import APIConnectionError
from litellm.types.llms.openai import ResponseAPIUsage, ResponsesAPIResponse
from litellm.types.utils import Choices, Message as LiteLLMMessage, ModelResponse, Usage
from pydantic import SecretStr

from openhands.sdk.llm import (
    LLM,
    FallbackStrategy,
    LLMCallContext,
    LLMRegistry,
    Message,
    TextContent,
)
from openhands.sdk.llm.call_context import llm_call_context_scope
from openhands.sdk.llm.llm_profile_store import LLMProfileStore


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,role,context_mode,collision,shared",
    [
        ("completion", "default", "explicit", False, False),
        ("acompletion", "default", "scoped", False, True),
        ("responses", "condenser", "explicit", True, False),
        ("aresponses", "condenser", "scoped", True, False),
        ("completion", "condenser", "explicit", False, False),
        ("acompletion", "condenser", "scoped", False, False),
        ("completion", "condenser", "standalone", False, False),
        ("aresponses", "default", "standalone", False, False),
    ],
)
async def test_fallback_preserves_caller_accounting(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    method: Literal["completion", "acompletion", "responses", "aresponses"],
    role: str,
    context_mode: Literal["explicit", "scoped", "standalone"],
    collision: bool,
    shared: bool,
):
    fallback = LLM(
        model="gpt-4o-mini",
        api_key=SecretStr("test-key"),
        num_retries=0,
        input_cost_per_token=0.01,
        output_cost_per_token=0.02,
        log_completions=True,
        log_completions_folder=str(tmp_path / "logs"),
    )
    LLMProfileStore(base_dir=tmp_path / "profiles").save(
        "backup", fallback, include_secrets=True
    )
    primary = LLM(
        model="gpt-4o",
        api_key=SecretStr("test-key"),
        usage_id=role,
        num_retries=0,
        input_cost_per_token=0.1,
        output_cost_per_token=0.2,
        log_completions=True,
        log_completions_folder=str(tmp_path / "logs"),
        fallback_strategy=FallbackStrategy(
            fallback_llms=["backup"], profile_store_dir=tmp_path / "profiles"
        ),
    )
    registry = LLMRegistry()
    if context_mode != "standalone":
        registry.add(primary)
    if collision:
        registry.add(LLM(model="gpt-4o-mini"))
    other_registry = LLMRegistry()
    if shared:
        other_registry.add(primary)
    metrics = (
        primary.metrics
        if context_mode == "standalone"
        else registry.sessions[role].metrics
    )
    notifications: list[tuple[int, float]] = []
    logs: list[tuple[str, str, dict[str, Any]]] = []

    def on_stats() -> None:
        usage = metrics.accumulated_token_usage
        assert usage is not None
        notifications.append((usage.prompt_tokens, metrics.accumulated_cost))

    def on_log(filename: str, data: str, usage_id: str, model: str) -> None:
        logs.append((usage_id, model, json.loads(data)))

    if context_mode == "standalone":
        primary.telemetry.set_stats_update_callback(on_stats)
        primary.telemetry.set_log_completions_callback(
            lambda filename, data: on_log(
                filename, data, role, primary.telemetry.model_name
            )
        )
    else:
        registry.set_stats_update_callback(on_stats)
        registry.set_log_completions_callback(on_log)

    context = LLMCallContext(
        prompt_cache_key="caller-cache",
        session_id="caller-conversation",
        sessions=registry.sessions if context_mode != "standalone" else None,
    )
    requests: list[dict[str, Any]] = []
    primary_attempts = 0

    def respond(**kwargs: Any) -> ModelResponse | ResponsesAPIResponse:
        nonlocal primary_attempts
        requests.append(kwargs)
        model = kwargs["model"]
        if model == primary.model:
            primary_attempts += 1
            if primary_attempts <= 2:
                raise APIConnectionError(
                    message="primary unavailable", llm_provider="openai", model=model
                )
        if method in ("responses", "aresponses"):
            return ResponsesAPIResponse(
                id=f"response-{len(requests)}",
                created_at=1,
                model=model,
                object="response",
                output=[
                    {
                        "type": "message",
                        "id": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [
                            {"type": "output_text", "text": "ok", "annotations": []}
                        ],
                    }
                ],
                parallel_tool_calls=False,
                tool_choice="auto",
                tools=[],
                usage=ResponseAPIUsage(
                    input_tokens=10, output_tokens=5, total_tokens=15
                ),
            )
        return ModelResponse(
            id=f"response-{len(requests)}",
            model=model,
            choices=[
                Choices(
                    index=0,
                    finish_reason="stop",
                    message=LiteLLMMessage(role="assistant", content="ok"),
                )
            ],
            usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )

    monkeypatch.setattr("openhands.sdk.llm.llm.litellm_completion", respond)
    monkeypatch.setattr("openhands.sdk.llm.llm.litellm_responses", respond)
    monkeypatch.setattr(
        "openhands.sdk.llm.llm.litellm_acompletion", AsyncMock(side_effect=respond)
    )
    monkeypatch.setattr(
        "openhands.sdk.llm.llm.litellm_aresponses", AsyncMock(side_effect=respond)
    )
    messages = [
        Message(role="system", content=[TextContent(text="Reply briefly.")]),
        Message(role="user", content=[TextContent(text="Hello.")]),
    ]
    supplied_context = context if context_mode == "explicit" else None
    scope = (
        llm_call_context_scope(context) if context_mode == "scoped" else nullcontext()
    )
    with scope:
        for count, cost in enumerate((0.2, 0.4, 2.4), start=1):
            if method == "completion":
                result = primary.completion(messages, call_context=supplied_context)
            elif method == "acompletion":
                result = await primary.acompletion(
                    messages, call_context=supplied_context
                )
            elif method == "responses":
                result = primary.responses(messages, call_context=supplied_context)
            else:
                result = await primary.aresponses(
                    messages, call_context=supplied_context
                )
            assert result.metrics.accumulated_token_usage is not None
            assert result.metrics.accumulated_token_usage.prompt_tokens == 10 * count
            assert result.metrics.accumulated_cost == pytest.approx(cost)
            assert metrics.accumulated_cost == pytest.approx(cost)
            assert primary.metrics.accumulated_cost == pytest.approx(cost)
            assert [tokens for tokens, _ in notifications] == [
                10 * n for n in range(1, count + 1)
            ]
            assert [total for _, total in notifications] == pytest.approx(
                (0.2, 0.4, 2.4)[:count]
            )

    assert [record.model for record in metrics.token_usages] == [
        "gpt-4o-mini",
        "gpt-4o-mini",
        "gpt-4o",
    ]
    assert [(uid, model) for uid, model, data in logs if "response" in data] == [
        (role, "gpt-4o-mini"),
        (role, "gpt-4o-mini"),
        (role, "gpt-4o"),
    ]
    if context_mode != "standalone":
        for request in requests:
            assert request["prompt_cache_key"] == "caller-cache"
            assert (
                request["extra_headers"]["x-litellm-session-id"]
                == "caller-conversation"
            )
    if collision:
        assert registry.sessions["default"].metrics.accumulated_cost == 0
    if shared:
        assert other_registry.sessions[role].metrics.accumulated_cost == 0


def test_direct_fallback_preserves_supplied_metrics(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    primary = LLM(model="gpt-4o", usage_id="condenser")
    registry, other_registry = LLMRegistry(), LLMRegistry()
    registry.add(primary)
    other_registry.add(primary)
    metrics = registry.sessions["condenser"].metrics
    LLMProfileStore(base_dir=tmp_path).save(
        "backup", LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"))
    )
    strategy = FallbackStrategy(fallback_llms=["backup"], profile_store_dir=tmp_path)
    response = ModelResponse(
        id="fallback-response",
        model="gpt-4o-mini",
        choices=[
            Choices(
                index=0,
                finish_reason="stop",
                message=LiteLLMMessage(role="assistant", content="ok"),
            )
        ],
        usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )
    monkeypatch.setattr(
        "openhands.sdk.llm.llm.litellm_completion", lambda **kwargs: response
    )
    messages = [
        Message(role="system", content=[TextContent(text="Reply briefly.")]),
        Message(role="user", content=[TextContent(text="Hello.")]),
    ]

    result = strategy.try_fallback(
        primary.model,
        APIConnectionError(
            message="primary unavailable", llm_provider="openai", model=primary.model
        ),
        metrics,
        lambda fallback: fallback.completion(messages),
    )

    assert result is not None
    assert metrics.accumulated_token_usage is not None
    assert primary.metrics.accumulated_token_usage is not None
    assert metrics.accumulated_token_usage.prompt_tokens == 10
    assert primary.metrics.accumulated_token_usage.prompt_tokens == 10
    assert len(metrics.token_usages) == 1
    assert other_registry.sessions["condenser"].metrics.token_usages == []
