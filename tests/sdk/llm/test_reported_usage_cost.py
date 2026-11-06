"""Provider-reported charges must reach SDK metrics even for unknown aliases."""

import pytest
from litellm import stream_chunk_builder
from litellm.types.llms.openai import ResponseAPIUsage, ResponsesAPIResponse
from litellm.types.utils import (
    Delta,
    ModelResponse,
    ModelResponseStream,
    StreamingChoices,
)

from openhands.sdk.llm.utils.litellm_provider import LLMProvider
from openhands.sdk.llm.utils.metrics import Metrics
from openhands.sdk.llm.utils.telemetry import Telemetry


@pytest.mark.parametrize("alias", ["gpt-luna", "cost-auto"])
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("cost", [0.04])
def test_reported_usage_cost_reaches_metrics(alias, streaming, cost):
    usage = {
        "prompt_tokens": 100,
        "completion_tokens": 10,
        "total_tokens": 110,
        "cost": cost,
    }
    if streaming:
        chunks = [
            ModelResponseStream(
                id="synthetic",
                model=alias,
                choices=[
                    StreamingChoices(
                        index=0,
                        delta=Delta(role="assistant", content="ok"),
                        finish_reason=None,
                    )
                ],
            ),
            ModelResponseStream(
                id="synthetic",
                model=alias,
                choices=[
                    StreamingChoices(index=0, delta=Delta(), finish_reason="stop")
                ],
            ),
            ModelResponseStream(id="synthetic", model=alias, choices=[], usage=usage),
        ]
        response = stream_chunk_builder(
            chunks, messages=[{"role": "user", "content": "synthetic"}]
        )
        assert isinstance(response, ModelResponse)
    else:
        response = ModelResponse(id="synthetic", model=alias, choices=[], usage=usage)

    telemetry = Telemetry(model_name=alias, metrics=Metrics())
    provider = LLMProvider(model=alias, name="openai", api_base=None)
    assert telemetry._compute_cost(response, provider_info=provider) == cost
    metrics = telemetry.on_response(response, provider_info=provider)
    assert metrics.accumulated_cost == cost
    assert len(metrics.costs) == 1  # Count this reported charge exactly once.
    assert metrics.token_usages[0].prompt_tokens == 100


@pytest.mark.parametrize("header", ["0", "0.09"])
def test_authoritative_header_precedes_usage_cost(header):
    response = ModelResponse(model="cost-auto", choices=[], usage={"cost": 0.04})
    response._hidden_params["additional_headers"] = {
        "llm_provider-x-litellm-response-cost": header
    }
    telemetry = Telemetry(model_name="cost-auto", metrics=Metrics())
    metrics = telemetry.on_response(response)
    assert metrics.accumulated_cost == float(header)
    assert len(metrics.costs) == 1


@pytest.mark.parametrize("header", ["", "bad", "nan", "inf", "-0.1"])
def test_invalid_header_falls_back_to_reported_usage(header):
    response = ModelResponse(model="cost-auto", choices=[], usage={"cost": 0.04})
    response._hidden_params["additional_headers"] = {
        "llm_provider-x-litellm-response-cost": header
    }
    telemetry = Telemetry(model_name="cost-auto", metrics=Metrics())
    assert telemetry._compute_cost(response) == 0.04


@pytest.mark.parametrize("cost", [0.0, -0.1, float("nan"), float("inf"), None])
def test_invalid_or_absent_usage_cost_stays_unavailable(cost):
    response = ModelResponse(model="cost-auto", choices=[], usage={"cost": cost})
    telemetry = Telemetry(model_name="cost-auto", metrics=Metrics())
    provider = LLMProvider(model="cost-auto", name="openai", api_base=None)
    with pytest.warns(UserWarning, match="Cost calculation failed"):
        assert telemetry._compute_cost(response, provider_info=provider) is None
    with pytest.warns(UserWarning, match="Cost calculation failed"):
        metrics = telemetry.on_response(response, provider_info=provider)
    assert metrics.accumulated_cost == 0.0
    assert not metrics.costs


def test_explicit_token_rates_override_usage_cost():
    response = ModelResponse(
        model="gpt-4o",
        choices=[],
        usage={"prompt_tokens": 100, "completion_tokens": 10, "cost": 0.04},
    )
    telemetry = Telemetry(
        model_name="gpt-4o",
        metrics=Metrics(),
        input_cost_per_token=0.001,
        output_cost_per_token=0.002,
    )
    assert telemetry._compute_cost(response) == pytest.approx(0.12)


def test_zero_usage_cost_preserves_known_model_pricing():
    telemetry = Telemetry(model_name="gpt-4o", metrics=Metrics())
    tokens = {"prompt_tokens": 100, "completion_tokens": 10}
    without_reported_cost = ModelResponse(model="gpt-4o", choices=[], usage=tokens)
    expected = telemetry._compute_cost(without_reported_cost)
    assert expected is not None and expected > 0
    with_placeholder_zero = ModelResponse(
        model="gpt-4o", choices=[], usage={**tokens, "cost": 0.0}
    )
    assert telemetry._compute_cost(with_placeholder_zero) == expected


@pytest.mark.parametrize("cost", [0.04])
def test_responses_api_reported_cost(cost):
    response = ResponsesAPIResponse(
        id="responses-cost",
        status="completed",
        created_at=1,
        output=[],
        usage=ResponseAPIUsage(
            input_tokens=100, output_tokens=10, total_tokens=110, cost=cost
        ),
    )
    telemetry = Telemetry(model_name="cost-auto", metrics=Metrics())
    metrics = telemetry.on_response(response)
    assert metrics.accumulated_cost == cost
    assert len(metrics.costs) == 1
