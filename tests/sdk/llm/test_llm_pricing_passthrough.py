from unittest.mock import patch

from pydantic import SecretStr

from openhands.sdk.llm import LLM, Message, TextContent
from tests.conftest import create_mock_litellm_response


def test_llm_pricing_passthrough_custom_rates():
    """LLM should pass custom pricing to Telemetry (litellm cost calc).

    Verifies that when LLM is constructed with input/output cost per token,
    Telemetry._compute_cost forwards those via custom_cost_per_token to
    litellm.cost_calculator.completion_cost.
    """
    with (
        patch("openhands.sdk.llm.llm.litellm_acompletion") as mock_completion,
        patch("openhands.sdk.llm.utils.telemetry.litellm_completion_cost") as mock_cost,
    ):
        mock_completion.return_value = create_mock_litellm_response("ok")
        mock_cost.return_value = 0.123

        llm = LLM(
            usage_id="test-llm",
            model="gpt-4o",
            api_key=SecretStr("test_key"),
            input_cost_per_token=0.001,
            output_cost_per_token=0.002,
        )

        messages = [Message(role="user", content=[TextContent(text="Hello")])]
        llm.completion(messages=messages)

        assert mock_cost.called, "litellm completion_cost should be invoked"
        kwargs = mock_cost.call_args.kwargs
        assert "custom_cost_per_token" in kwargs
        cpt = kwargs["custom_cost_per_token"]
        assert cpt["input_cost_per_token"] == 0.001
        assert cpt["output_cost_per_token"] == 0.002
