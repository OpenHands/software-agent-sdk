import asyncio
import inspect
import json
import threading
from importlib.resources import files
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from litellm.types.llms.openai import ResponseAPIUsage, ResponsesAPIResponse
from openai.types.responses import ResponseOutputMessage, ResponseOutputText

import openhands.sdk.llm.llm as llm_module
import openhands.sdk.llm.utils.telemetry as telemetry_module
from openhands.sdk import LLM, Message, TextContent
from openhands.sdk.llm.call_context import LLMCallContext
from openhands.sdk.llm.cost_budget import BudgetReservation, CostBudget
from openhands.sdk.llm.exceptions import LLMBudgetExceededError
from openhands.sdk.tool.builtins.finish import FinishTool
from tests.conftest import create_mock_litellm_response


def _settle(budget: CostBudget, amount: float) -> None:
    """Reserve and immediately settle ``amount``, as a completed call would."""
    reservation = budget.try_reserve(amount)
    assert reservation is not None
    budget.settle(reservation, amount)


def test_reserve_denies_when_it_would_exceed_limit():
    budget = CostBudget(1.0)

    assert budget.try_reserve(0.4) is not None
    assert budget.try_reserve(0.4) is not None
    # 0.4 + 0.4 + 0.4 > 1.0
    assert budget.try_reserve(0.4) is None
    assert budget.reserved == pytest.approx(0.8)


def test_settle_records_actual_cost_and_frees_capacity():
    budget = CostBudget(1.0)

    reservation = budget.try_reserve(0.5)
    assert reservation is not None
    assert budget.reserved == pytest.approx(0.5)

    # The provider charged less than the worst-case reservation.
    budget.settle(reservation, 0.2)
    assert budget.reserved == 0.0
    assert budget.spent == pytest.approx(0.2)
    assert budget.live_total == pytest.approx(0.2)

    # Freed capacity is usable again.
    assert budget.try_reserve(0.7) is not None


def test_live_total_includes_in_flight_reservations():
    budget = CostBudget(10.0)
    _settle(budget, 3.0)
    assert budget.try_reserve(2.0) is not None
    assert budget.live_total == pytest.approx(5.0)


def test_reseed_clears_prior_run_spend():
    budget = CostBudget(10.0)
    _settle(budget, 7.0)
    assert budget.spent == pytest.approx(7.0)

    budget.reseed()
    assert budget.spent == 0.0
    assert budget.reserved == 0.0
    # A fresh run gets the full allowance again.
    assert budget.try_reserve(7.0) is not None


def test_child_shares_reservation_pool_but_bounds_its_own_spend():
    parent = CostBudget(10.0)
    child = parent.child(1.0)

    # Child spend draws on the shared pool.
    reservation = child.try_reserve(0.9)
    assert reservation is not None
    child.settle(reservation, 0.9)
    assert parent.reserved == pytest.approx(0.0)
    assert parent.live_total == pytest.approx(0.9)

    # The child's own tighter ceiling now refuses more even though the parent
    # has plenty of capacity left.
    assert child.try_reserve(0.5) is None
    assert parent.try_reserve(0.5) is not None


def test_child_cannot_exceed_a_tighter_ancestor_ceiling():
    """A delegate with a larger budget still cannot breach its parent's cap."""
    parent = CostBudget(1.0)
    # The child declares a *larger* budget than the parent.
    child = parent.child(5.0)
    _settle(parent, 0.8)

    # The child's own $5 limit would admit $0.50, but the parent's $1 cap sees
    # $1.30 total and refuses. `limit` reports the tightest enforced ceiling.
    assert child.try_reserve(0.5) is None
    assert child.limit == pytest.approx(1.0)
    # Within the shared cap the child is admitted.
    assert child.try_reserve(0.1) is not None


def test_child_denial_reports_its_own_tighter_ceiling():
    child = CostBudget(10.0).child(1.0)

    assert child.try_reserve(2.0) is None
    assert "maximum budget limit ($1.0000)" in child.denial_detail(2.0)


def test_concurrent_reservations_cannot_overshoot():
    """A shared budget admits at most the calls that fit, even in parallel."""
    budget = CostBudget(1.0)
    barrier = threading.Barrier(8)
    granted: list[BudgetReservation] = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        reservation = budget.try_reserve(0.25)
        if reservation is not None:
            with lock:
                granted.append(reservation)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # 8 x 0.25 = 2.0 against a 1.0 ceiling: exactly four should be admitted.
    assert len(granted) == 4
    assert budget.reserved == pytest.approx(1.0)


def test_denial_detail_names_the_limit_and_attempted_call():
    budget = CostBudget(1.0)
    _settle(budget, 0.9)
    detail = budget.denial_detail(0.5)
    assert "$1.0000" in detail
    assert "$0.9000" in detail
    # 0.9 recorded + 0.5 attempted.
    assert "$1.4000" in detail


def test_non_positive_limit_is_rejected():
    with pytest.raises(ValueError):
        CostBudget(0.0)
    with pytest.raises(ValueError):
        CostBudget(1.0).child(0.0)


@pytest.fixture
def budget_call(monkeypatch):
    info = {
        "max_input_tokens": 20000,
        "max_output_tokens": 100,
        "input_cost_per_token": 0.0000005,
        "output_cost_per_token": 0.001,
    }
    monkeypatch.setattr(llm_module, "get_litellm_model_info", lambda **_: info)
    monkeypatch.setattr(llm_module, "token_counter", lambda **_: 10)
    chat_response = create_mock_litellm_response("ok")
    response = ResponsesAPIResponse(
        id="budget-response",
        created_at=0,
        output=[
            ResponseOutputMessage(
                id="budget-message",
                type="message",
                role="assistant",
                status="completed",
                content=[
                    ResponseOutputText(type="output_text", text="ok", annotations=[])
                ],
            )
        ],
        parallel_tool_calls=False,
        tool_choice="auto",
        top_p=None,
        tools=[],
        usage=ResponseAPIUsage(input_tokens=10, output_tokens=5, total_tokens=15),
        status="completed",
    )
    providers = (
        Mock(return_value=chat_response),
        AsyncMock(return_value=chat_response),
        Mock(return_value=response),
        AsyncMock(return_value=response),
    )
    for name, provider in zip(
        (
            "litellm_completion",
            "litellm_acompletion",
            "litellm_responses",
            "litellm_aresponses",
        ),
        providers,
        strict=True,
    ):
        monkeypatch.setattr(llm_module, name, provider)

    def make_llm(**kwargs):
        options = {
            "model": "openai/budget-fixture",
            "api_key": "test",
            "max_input_tokens": 20000,
            "max_output_tokens": 100,
            "num_retries": 0,
        }
        return LLM(**(options | kwargs))

    messages = [
        Message(role="system", content=[TextContent(text="Reply briefly.")]),
        Message(role="user", content=[TextContent(text="hi")]),
    ]
    return info, make_llm, messages, providers


@pytest.mark.parametrize("custom_prices", [{}, {"input_cost_per_token": 0.0}])
def test_budget_refuses_unknown_directional_prices(budget_call, custom_prices):
    info, make_llm, messages, providers = budget_call
    info.pop("input_cost_per_token")
    info.pop("output_cost_per_token")
    llm = make_llm(**custom_prices)

    with pytest.raises(LLMBudgetExceededError, match="price"):
        llm.completion(
            messages, call_context=LLMCallContext(cost_budget=CostBudget(100))
        )

    providers[0].assert_not_called()


@pytest.mark.parametrize(
    "field,value",
    [("input_cost_per_token", float("inf")), ("output_cost_per_token", float("nan"))],
)
def test_budget_refuses_nonfinite_prices(budget_call, field, value):
    info, make_llm, messages, providers = budget_call
    info[field] = value
    llm = make_llm()

    with pytest.raises(LLMBudgetExceededError, match="price"):
        llm.completion(
            messages, call_context=LLMCallContext(cost_budget=CostBudget(100))
        )

    providers[0].assert_not_called()


@pytest.mark.parametrize("axis", ["input", "output"])
def test_budget_refuses_unknown_token_bound(budget_call, axis):
    info, make_llm, messages, providers = budget_call
    info.pop(f"max_{axis}_tokens")
    llm = make_llm(**{f"max_{axis}_tokens": None})

    with pytest.raises(LLMBudgetExceededError, match=axis):
        llm.completion(
            messages, call_context=LLMCallContext(cost_budget=CostBudget(100))
        )

    providers[0].assert_not_called()


@pytest.mark.parametrize(
    "invoke,cap",
    [
        (LLM.completion, "max_completion_tokens"),
        (LLM.acompletion, "max_completion_tokens"),
        (LLM.responses, "max_output_tokens"),
        (LLM.aresponses, "max_output_tokens"),
    ],
)
async def test_budget_prices_actual_request_output_cap(budget_call, invoke, cap):
    _, make_llm, messages, providers = budget_call
    llm = make_llm()

    with pytest.raises(LLMBudgetExceededError):
        result = invoke(
            llm,
            messages,
            call_context=LLMCallContext(cost_budget=CostBudget(0.2)),
            **{cap: 1000},
        )
        if inspect.isawaitable(result):
            await result

    for provider in providers:
        provider.assert_not_called()


@pytest.mark.parametrize("limit,admitted", [(0.2, False), (1.1, True)])
def test_budget_accounts_for_published_context_tiers(
    budget_call, monkeypatch, limit, admitted
):
    info, make_llm, messages, providers = budget_call
    packaged = files("litellm").joinpath("model_prices_and_context_window_backup.json")
    info.update(json.loads(packaged.read_text())["xai/grok-4-fast-reasoning"])
    monkeypatch.setattr(llm_module, "token_counter", lambda **_: 128001)
    llm = make_llm(
        model="xai/grok-4-fast-reasoning",
        max_input_tokens=2000000,
        max_output_tokens=200000,
    )
    providers[0].return_value = create_mock_litellm_response(
        "ok",
        model="xai/grok-4-fast-reasoning",
        prompt_tokens=128001,
        completion_tokens=200000,
    )
    budget = CostBudget(limit)

    if admitted:
        result = llm.completion(
            messages, call_context=LLMCallContext(cost_budget=budget)
        )
        assert result.message.content == [TextContent(text="ok")]
        providers[0].assert_called_once()
    else:
        with pytest.raises(LLMBudgetExceededError):
            llm.completion(messages, call_context=LLMCallContext(cost_budget=budget))
        providers[0].assert_not_called()


def test_budget_accounts_for_published_cache_write_tiers(budget_call):
    info, make_llm, messages, providers = budget_call
    info["cache_creation_input_token_cost_above_1hr"] = 0.05
    llm = make_llm()

    with pytest.raises(LLMBudgetExceededError):
        llm.completion(
            messages, call_context=LLMCallContext(cost_budget=CostBudget(0.2))
        )

    providers[0].assert_not_called()


@pytest.mark.parametrize("invoke", [LLM.completion, LLM.responses])
def test_budget_does_not_treat_input_hint_as_provider_limit(budget_call, invoke):
    _, make_llm, messages, providers = budget_call
    llm = make_llm(max_input_tokens=16384)

    with pytest.raises(LLMBudgetExceededError):
        invoke(
            llm, messages, call_context=LLMCallContext(cost_budget=CostBudget(0.109))
        )

    for provider in providers:
        provider.assert_not_called()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"n": 2},
        {"extra_body": {"max_tokens": 1000}},
        {"model": "another-model"},
        {"client": Mock()},
        {"web_search_options": {}},
        {"retry_policy": {"TimeoutErrorRetries": 2}},
        {"additional_drop_params": ["max_completion_tokens"]},
    ],
)
def test_budget_refuses_unbounded_request_overrides(budget_call, kwargs):
    _, make_llm, messages, providers = budget_call
    llm = make_llm()

    with pytest.raises(LLMBudgetExceededError):
        llm.completion(
            messages,
            call_context=LLMCallContext(cost_budget=CostBudget(0.2)),
            **kwargs,
        )

    providers[0].assert_not_called()


@pytest.mark.parametrize(
    "model,kwargs,output_cap,limit",
    [
        (
            "gpt-4o-mini-audio-preview",
            {
                "modalities": ["text", "audio"],
                "audio": {"voice": "alloy", "format": "wav"},
            },
            16384,
            0.05,
        ),
        ("gpt-4o-search-preview", {}, 100, 1),
    ],
)
def test_budget_refuses_non_token_request_costs(
    budget_call, model, kwargs, output_cap, limit
):
    info, make_llm, messages, providers = budget_call
    packaged = files("litellm").joinpath("model_prices_and_context_window_backup.json")
    info.update(json.loads(packaged.read_text())[model])
    llm = make_llm(model=f"openai/{model}", max_output_tokens=output_cap)

    with pytest.raises(LLMBudgetExceededError):
        llm.completion(
            messages,
            call_context=LLMCallContext(cost_budget=CostBudget(limit)),
            **kwargs,
        )

    providers[0].assert_not_called()


@pytest.mark.parametrize(
    "invoke,provider_index",
    [
        (LLM.completion, 0),
        (LLM.acompletion, 1),
        (LLM.responses, 2),
        (LLM.aresponses, 3),
    ],
)
async def test_budget_allows_plain_function_calls_with_optional_search_prices(
    budget_call, invoke, provider_index
):
    info, make_llm, messages, providers = budget_call
    fixture = Path(__file__).with_name("fixtures") / "optional-search-model-info.json"
    info.update(json.loads(fixture.read_text())["model_info"])
    llm = make_llm(
        model="openai/gpt-4o-mini",
        native_tool_calling=True,
        input_cost_per_token=0.0000005,
        output_cost_per_token=0.001,
    )

    result = invoke(
        llm,
        messages,
        tools=list(FinishTool.create()),
        add_security_risk_prediction=True,
        call_context=LLMCallContext(cost_budget=CostBudget(0.2)),
    )
    if inspect.isawaitable(result):
        result = await result

    assert result.message.content == [TextContent(text="ok")]
    providers[provider_index].assert_called_once()
    assert providers[provider_index].call_args.kwargs["tools"][0]["type"] == "function"


@pytest.mark.parametrize(
    "model,catalog_key",
    [
        ("anthropic/claude-sonnet-4-5", "claude-sonnet-4-5"),
        ("gemini/gemini-2.5-pro", "gemini/gemini-2.5-pro"),
        (
            "bedrock/anthropic.claude-sonnet-4-5-20250929-v1:0",
            "anthropic.claude-sonnet-4-5-20250929-v1:0",
        ),
        ("vertex_ai/gemini-2.5-pro", "gemini-2.5-pro"),
        ("vertex_ai/claude-opus-4-6", "vertex_ai/claude-opus-4-6"),
        ("vertex_ai/gemini-3-pro-preview", "vertex_ai/gemini-3-pro-preview"),
        ("azure_ai/claude-opus-4-6", "azure_ai/claude-opus-4-6"),
        ("openai/gpt-5.3-codex", "gpt-5.3-codex"),
        ("openhands/gpt-5.3-codex", "gpt-5.3-codex"),
    ],
)
@pytest.mark.parametrize(
    "invoke,provider_index",
    [
        (LLM.completion, 0),
        (LLM.acompletion, 1),
        (LLM.responses, 2),
        (LLM.aresponses, 3),
    ],
)
async def test_budget_allows_ordinary_provider_calls_with_optional_search_prices(
    budget_call, monkeypatch, model, catalog_key, invoke, provider_index
):
    info, make_llm, messages, providers = budget_call
    fixture = (
        Path(__file__).with_name("fixtures") / "optional-search-provider-info.json"
    )
    info.clear()
    info.update(json.loads(fixture.read_text())["model_info"][catalog_key])
    # The underlying provider functions are test doubles; optional Vertex
    # package availability is checked separately by test_vertex_preflight.
    monkeypatch.setattr(llm_module, "assert_vertex_sdk_available", lambda _: None)
    llm = make_llm(model=model, native_tool_calling=True)

    result = invoke(
        llm,
        messages,
        tools=list(FinishTool.create()),
        call_context=LLMCallContext(cost_budget=CostBudget(50)),
    )
    if inspect.isawaitable(result):
        result = await result

    assert result.message.content == [TextContent(text="ok")]
    providers[provider_index].assert_called_once()
    assert providers[provider_index].call_args.kwargs["tools"][0]["type"] == "function"


@pytest.mark.parametrize(
    "invoke", [LLM.completion, LLM.acompletion, LLM.responses, LLM.aresponses]
)
async def test_budget_reserves_vertex_regional_endpoint_premium(
    budget_call, monkeypatch, invoke
):
    info, make_llm, messages, providers = budget_call
    fixture = (
        Path(__file__).with_name("fixtures") / "optional-search-provider-info.json"
    )
    info.clear()
    info.update(
        json.loads(fixture.read_text())["model_info"]["vertex_ai/claude-opus-4-6"]
    )
    monkeypatch.setattr(llm_module, "assert_vertex_sdk_available", lambda _: None)
    llm = make_llm(model="vertex_ai/claude-opus-4-6")

    # Published context/cache tiers require $10.0025 before the endpoint's
    # 10% regional premium. $10.50 must not admit the $11.00275 bound.
    with pytest.raises(LLMBudgetExceededError):
        result = invoke(
            llm, messages, call_context=LLMCallContext(cost_budget=CostBudget(10.5))
        )
        if inspect.isawaitable(result):
            await result

    for provider in providers:
        provider.assert_not_called()


@pytest.mark.parametrize(
    "invoke,provider_index",
    [
        (LLM.completion, 0),
        (LLM.acompletion, 1),
        (LLM.responses, 2),
        (LLM.aresponses, 3),
    ],
)
@pytest.mark.parametrize("custom_prices", [False, True])
async def test_budget_does_not_refund_unverified_endpoint_premium(
    budget_call, monkeypatch, invoke, provider_index, custom_prices
):
    info, make_llm, messages, providers = budget_call
    fixture = (
        Path(__file__).with_name("fixtures") / "optional-search-provider-info.json"
    )
    info.clear()
    info.update(
        json.loads(fixture.read_text())["model_info"]["vertex_ai/claude-opus-4-6"]
    )
    monkeypatch.setattr(llm_module, "assert_vertex_sdk_available", lambda _: None)
    pricing = (
        {"input_cost_per_token": 0.000001, "output_cost_per_token": 0.000001}
        if custom_prices
        else {}
    )
    llm = make_llm(model="vertex_ai/claude-opus-4-6", **pricing)
    budget = CostBudget(12)

    async def call():
        result = invoke(llm, messages, call_context=LLMCallContext(cost_budget=budget))
        return await result if inspect.isawaitable(result) else result

    result = await call()
    assert result.message.content == [TextContent(text="ok")]
    recorded_cost = llm.metrics.accumulated_cost
    assert recorded_cost > 0
    if custom_prices:
        await call()
        assert budget.spent == pytest.approx(llm.metrics.accumulated_cost)
        assert providers[provider_index].call_count == 2
    else:
        # Positive token telemetry does not establish that the endpoint's
        # premium was included. Keep the inclusive bound for this run.
        with pytest.raises(LLMBudgetExceededError):
            await call()
        assert llm.metrics.accumulated_cost == recorded_cost
        assert budget.spent > recorded_cost
        providers[provider_index].assert_called_once()


def test_budget_counts_reported_cost_above_endpoint_reservation(
    budget_call, monkeypatch
):
    info, make_llm, messages, providers = budget_call
    fixture = (
        Path(__file__).with_name("fixtures") / "optional-search-provider-info.json"
    )
    info.clear()
    info.update(
        json.loads(fixture.read_text())["model_info"]["vertex_ai/claude-opus-4-6"]
    )
    monkeypatch.setattr(llm_module, "assert_vertex_sdk_available", lambda _: None)
    monkeypatch.setattr(telemetry_module, "litellm_completion_cost", lambda **_: 20)
    llm = make_llm(model="vertex_ai/claude-opus-4-6")
    context = LLMCallContext(cost_budget=CostBudget(31))

    llm.completion(messages, call_context=context)
    with pytest.raises(LLMBudgetExceededError):
        llm.completion(messages, call_context=context)

    assert llm.metrics.accumulated_cost == 20
    providers[0].assert_called_once()


@pytest.mark.parametrize("custom_prices", [False, True])
def test_vertex_budget_requires_known_endpoint_premium_or_custom_rates(
    budget_call, monkeypatch, custom_prices
):
    info, make_llm, messages, providers = budget_call
    # The pinned LiteLLM fallback contains token prices and the context cap
    # but lacks the regional endpoint premium in the current public catalog.
    packaged = files("litellm").joinpath("model_prices_and_context_window_backup.json")
    info.clear()
    info.update(
        key="vertex_ai/claude-opus-4-6",
        **json.loads(packaged.read_text())["vertex_ai/claude-opus-4-6"],
    )
    monkeypatch.setattr(llm_module, "assert_vertex_sdk_available", lambda _: None)
    pricing = (
        {"input_cost_per_token": 0.000001, "output_cost_per_token": 0.000001}
        if custom_prices
        else {}
    )
    llm = make_llm(model="vertex_ai/claude-opus-4-6", **pricing)
    context = LLMCallContext(cost_budget=CostBudget(10.5))

    if custom_prices:
        result = llm.completion(messages, call_context=context)
        assert result.message.content == [TextContent(text="ok")]
        providers[0].assert_called_once()
    else:
        with pytest.raises(LLMBudgetExceededError, match="endpoint premium"):
            llm.completion(messages, call_context=context)
        providers[0].assert_not_called()


@pytest.mark.parametrize(
    "invoke,tool_type",
    [
        (LLM.completion, "web_search"),
        (LLM.acompletion, "web_search"),
        (LLM.responses, "web_search_preview"),
        (LLM.aresponses, "web_search"),
    ],
)
async def test_budget_refuses_prepared_native_search_tools(
    budget_call, monkeypatch, invoke, tool_type
):
    info, make_llm, messages, providers = budget_call
    fixture = (
        Path(__file__).with_name("fixtures") / "optional-search-provider-info.json"
    )
    info.clear()
    info.update(json.loads(fixture.read_text())["model_info"]["gpt-5.3-codex"])
    method = (
        "to_openai_tool"
        if invoke in (LLM.completion, LLM.acompletion)
        else "to_responses_tool"
    )
    monkeypatch.setattr(FinishTool, method, lambda *args, **kwargs: {"type": tool_type})
    llm = make_llm(model="openai/gpt-5.3-codex", native_tool_calling=True)

    with pytest.raises(LLMBudgetExceededError, match="native/legacy server tools"):
        result = invoke(
            llm,
            messages,
            tools=list(FinishTool.create()),
            call_context=LLMCallContext(cost_budget=CostBudget(50)),
        )
        if inspect.isawaitable(result):
            await result

    for provider in providers:
        provider.assert_not_called()


@pytest.mark.parametrize(
    "invoke", [LLM.completion, LLM.acompletion, LLM.responses, LLM.aresponses]
)
@pytest.mark.parametrize(
    "kwargs",
    [
        {"web_search_options": {}},
        {"functions": [{"google_search": {}}]},
        {"cached_content": "cachedContents/opaque-hosted-tools"},
    ],
)
async def test_budget_refuses_explicit_or_legacy_provider_search(
    budget_call, invoke, kwargs
):
    info, make_llm, messages, providers = budget_call
    fixture = (
        Path(__file__).with_name("fixtures") / "optional-search-provider-info.json"
    )
    info.clear()
    info.update(json.loads(fixture.read_text())["model_info"]["gemini/gemini-2.5-pro"])
    llm = make_llm(model="gemini/gemini-2.5-pro")

    with pytest.raises(LLMBudgetExceededError):
        result = invoke(
            llm,
            messages,
            call_context=LLMCallContext(cost_budget=CostBudget(50)),
            **kwargs,
        )
        if inspect.isawaitable(result):
            await result

    for provider in providers:
        provider.assert_not_called()


@pytest.mark.parametrize(
    "model,canonical,key,provider",
    [
        ("openai/gpt-4o-mini-search-preview-2025-03-11", None, None, "openai"),
        ("litellm_proxy/gpt-5-search-api", None, None, "openai"),
        ("openai/generated-alias", "gpt-4o-search-preview", "gpt-4o-mini", "openai"),
        ("openai/gpt-4o-search-preview", "gpt-4o-mini", "gpt-4o-mini", "openai"),
        ("openai/generated-alias", None, "gpt-4o-search-preview", "openai"),
        ("perplexity/sonar", None, "perplexity/sonar", "perplexity"),
        ("perplexity/sonar", None, None, "perplexity"),
        ("litellm_proxy/sonar", None, None, None),
        ("openrouter/perplexity/sonar-pro", None, None, None),
        (
            "gemini/deep-research-pro-preview",
            None,
            "gemini/deep-research-pro-preview",
            "gemini",
        ),
        ("unknown/gemini-2.5-pro", None, "gemini-2.5-pro", "unknown"),
        ("gemini/gemini-live-preview", None, "gemini-live-preview", "gemini"),
    ],
)
def test_budget_keeps_intrinsic_or_unresolved_search_fees_closed(
    budget_call, model, canonical, key, provider
):
    info, make_llm, messages, providers = budget_call
    if key is not None:
        info.update(
            key=key,
            litellm_provider=provider,
            mode="realtime" if "live" in key else "chat",
            search_context_cost_per_query={"search_context_size_medium": 0.025},
        )
    llm = make_llm(
        model=model,
        model_canonical_name=canonical,
        input_cost_per_token=0.0000005,
        output_cost_per_token=0.001,
    )

    with pytest.raises(LLMBudgetExceededError):
        llm.completion(
            messages, call_context=LLMCallContext(cost_budget=CostBudget(0.2))
        )

    providers[0].assert_not_called()


@pytest.mark.parametrize(
    "model",
    [
        "openai/o3-deep-research",
        "openai/o4-mini-deep-research",
        "o3-deep-research-2025-06-26",
        "perplexity/preset/deep-research",
        "perplexity/preset/advanced-deep-research",
        "vertex_ai/deep-research-pro-preview-12-2025",
        "aihubmix/gemini-3-flash-preview-search",
    ],
)
@pytest.mark.parametrize(
    "invoke", [LLM.completion, LLM.acompletion, LLM.responses, LLM.aresponses]
)
async def test_budget_refuses_intrinsic_search_when_catalog_omits_the_fee(
    budget_call, model, invoke
):
    """A dated entry omitting the search fee must not open a search-only route.

    ``has_unbounded_search_cost`` early-returns on an absent
    ``search_context_cost_per_query``. If that shortcut runs before the
    search-name check, a token-only reservation admits these routes while the
    intrinsic search charge goes unbounded.
    """
    info, make_llm, messages, providers = budget_call
    fixture = Path(__file__).with_name("fixtures") / "omitted-search-fee-info.json"
    catalog = json.loads(fixture.read_text())["model_info"]
    info.clear()
    info.update(catalog[model])
    assert info.get("search_context_cost_per_query") is None
    llm = make_llm(
        model=model,
        input_cost_per_token=0.00001,
        output_cost_per_token=0.001,
    )

    with pytest.raises(
        LLMBudgetExceededError, match="non-token audio/search request costs"
    ):
        result = invoke(
            llm,
            messages,
            call_context=LLMCallContext(cost_budget=CostBudget(50)),
        )
        if inspect.isawaitable(result):
            await result

    for provider in providers:
        provider.assert_not_called()


@pytest.mark.parametrize(
    "name,value",
    [("num_retries", 2), ("model_fallbacks", ["another-model"]), ("drop_params", True)],
)
def test_budget_refuses_unsafe_global_transport_options(
    budget_call, monkeypatch, name, value
):
    _, make_llm, messages, providers = budget_call
    monkeypatch.setattr(llm_module.litellm, name, value)
    llm = make_llm()

    with pytest.raises(LLMBudgetExceededError, match="global LiteLLM"):
        llm.completion(
            messages, call_context=LLMCallContext(cost_budget=CostBudget(0.2))
        )

    providers[0].assert_not_called()


def test_budget_allows_known_free_custom_prices(budget_call):
    info, make_llm, messages, providers = budget_call
    info.pop("input_cost_per_token")
    info.pop("output_cost_per_token")
    llm = make_llm(input_cost_per_token=0.0, output_cost_per_token=0.0)

    result = llm.completion(
        messages, call_context=LLMCallContext(cost_budget=CostBudget(0.01))
    )

    assert result.message.content == [TextContent(text="ok")]
    providers[0].assert_called_once()


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"functions": [{"google_search": {}}]},
        {"cached_content": "cachedContents/opaque-hosted-tools"},
    ],
)
def test_unbudgeted_call_does_not_require_price_or_caps(budget_call, kwargs):
    info, make_llm, messages, providers = budget_call
    info.clear()
    llm = make_llm(max_input_tokens=None, max_output_tokens=None)

    result = llm.completion(messages, **kwargs)

    assert result.message.content == [TextContent(text="ok")]
    providers[0].assert_called_once()


def test_budget_retains_recorded_cost_before_validation_retry(budget_call):
    _, make_llm, messages, providers = budget_call
    response = create_mock_litellm_response("", prompt_tokens=10, completion_tokens=100)
    response.choices = []
    providers[0].return_value = response
    llm = make_llm(
        input_cost_per_token=0.0000005,
        output_cost_per_token=0.001,
        num_retries=2,
        retry_min_wait=0,
        retry_max_wait=0,
    )
    budget = CostBudget(0.15)

    with pytest.raises(LLMBudgetExceededError):
        llm.completion(messages, call_context=LLMCallContext(cost_budget=budget))

    providers[0].assert_called_once()
    assert llm.metrics.accumulated_cost == pytest.approx(0.100005)
    assert budget.spent == pytest.approx(0.11)
    assert budget.reserved == 0


async def test_budget_retains_unknown_work_on_cancellation(budget_call):
    _, make_llm, messages, providers = budget_call
    providers[1].side_effect = asyncio.CancelledError()
    llm = make_llm()
    budget = CostBudget(0.15)

    with pytest.raises(asyncio.CancelledError):
        await llm.acompletion(messages, call_context=LLMCallContext(cost_budget=budget))

    providers[1].assert_called_once()
    assert budget.spent == pytest.approx(0.11)
    assert budget.reserved == 0


def test_budget_retains_successful_work_when_cost_is_unknown(budget_call, monkeypatch):
    _, make_llm, messages, providers = budget_call
    monkeypatch.setattr(
        telemetry_module,
        "litellm_completion_cost",
        Mock(side_effect=ValueError("Fixture: response price unavailable")),
    )
    llm = make_llm()
    budget = CostBudget(0.15)
    context = LLMCallContext(cost_budget=budget)

    result = llm.completion(messages, call_context=context)
    with pytest.raises(LLMBudgetExceededError):
        llm.completion(messages, call_context=context)

    assert result.message.content == [TextContent(text="ok")]
    providers[0].assert_called_once()
    assert llm.metrics.accumulated_cost == 0
    assert budget.spent == pytest.approx(0.11)
