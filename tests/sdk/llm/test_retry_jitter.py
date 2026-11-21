import random
from unittest.mock import patch

import httpx
import pytest
from litellm.exceptions import ServiceUnavailableError
from pydantic import ValidationError

from openhands.sdk import LLM, Message, TextContent
from openhands.sdk.llm.exceptions import LLMServiceUnavailableError


@pytest.fixture
def retry_waits():
    def collect(llm, hint=None):
        response = httpx.Response(
            503,
            request=httpx.Request("POST", "https://test.invalid"),
            headers={} if hint is None else {"Retry-After": hint},
        )
        error = ServiceUnavailableError(
            "capacity occupied", "openai", "gpt-4o", response=response
        )
        waits = []
        with (
            patch(
                "openhands.sdk.llm.llm.litellm_completion", side_effect=error
            ) as transport,
            patch("tenacity.nap.time.sleep", side_effect=waits.append),
        ):
            with pytest.raises(LLMServiceUnavailableError):
                llm.completion(
                    messages=[
                        Message(role="system", content=[TextContent(text="Be brief.")]),
                        Message(role="user", content=[TextContent(text="hello")]),
                    ]
                )
        return waits, transport.call_count

    return collect


def make_llm(**kwargs):
    return LLM(
        model="openai/gpt-4o",
        api_key="test-key",
        base_url="http://127.0.0.1:1",
        **kwargs,
    )


@pytest.mark.parametrize("jitter", [None, 0.25, 2.0])
def test_independent_llms_spread_retries_including_at_the_ceiling(retry_waits, jitter):
    rng = random.Random(6938)
    config = {} if jitter is None else {"retry_jitter": jitter}
    with patch("random.uniform", side_effect=rng.uniform):
        sequences = [
            retry_waits(make_llm(num_retries=6, **config))[0] for _ in range(8)
        ]
    bounds = {
        None: [(8, 9), (15, 17), (31, 33), (63, 64), (63, 64)],
        0.25: [(8, 8.25), (15.75, 16.25), (31.75, 32.25), (63.75, 64), (63.75, 64)],
        2.0: [(8, 10), (14, 18), (30, 34), (62, 64), (62, 64)],
    }
    for sequence in sequences:
        assert len(sequence) == 5
        for wait, (low, high) in zip(sequence, bounds[jitter], strict=True):
            assert low <= wait <= high
    for attempt in range(5):
        assert len({sequence[attempt] for sequence in sequences}) == 8


def test_zero_jitter_restores_the_exact_exponential_schedule(retry_waits):
    waits, attempts = retry_waits(make_llm(num_retries=6, retry_jitter=0))
    assert waits == [8, 16, 32, 64, 64]
    assert attempts == 6


@pytest.mark.parametrize(
    ("minimum", "maximum", "expected"), [(0, 0, 0), (3, 3, 3), (8, 0, 8)]
)
def test_fixed_or_inverted_bounds_preserve_existing_waits(
    retry_waits, minimum, maximum, expected
):
    waits, attempts = retry_waits(
        make_llm(num_retries=3, retry_min_wait=minimum, retry_max_wait=maximum)
    )
    assert waits == [expected, expected]
    assert attempts == 3


@pytest.mark.parametrize(("hint", "expected"), [("20", 20), ("2", 8.75), ("bad", 8.75)])
def test_provider_hint_is_a_floor_not_an_extra_jittered_delay(
    retry_waits, hint, expected
):
    with patch("random.uniform", return_value=8.75):
        waits, attempts = retry_waits(make_llm(num_retries=2), hint)
    assert waits == [expected]
    assert attempts == 2


def test_over_limit_provider_hint_still_stops_without_waiting(retry_waits):
    waits, attempts = retry_waits(make_llm(), "65")
    assert waits == []
    assert attempts == 1


def test_legacy_json_loads_with_jitter_and_round_trips_an_opt_out(retry_waits):
    legacy = '{"model":"openai/gpt-4o","num_retries":2}'
    restored = LLM.model_validate_json(legacy)
    with patch("random.uniform", return_value=8.5):
        assert retry_waits(restored)[0] == [8.5]
    disabled = LLM.model_validate_json(
        make_llm(num_retries=2, retry_jitter=0).model_dump_json()
    )
    assert retry_waits(disabled)[0] == [8]


@pytest.mark.parametrize("jitter", [-1, float("nan"), float("inf")])
def test_invalid_jitter_is_rejected(jitter):
    with pytest.raises(ValidationError):
        make_llm(retry_jitter=jitter)
