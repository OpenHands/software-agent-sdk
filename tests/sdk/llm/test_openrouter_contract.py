"""OpenRouter contracts at the SDK / pinned LiteLLM boundary (no network)."""

import json
from unittest.mock import MagicMock

import httpx
from litellm.llms.openrouter.chat.transformation import OpenrouterConfig
from litellm.types.utils import ModelResponse

from openhands.sdk import LLM
from openhands.sdk.llm.options.chat_options import select_chat_options
from openhands.sdk.llm.utils.metrics import Metrics
from openhands.sdk.llm.utils.telemetry import Telemetry


def test_openrouter_routing_and_privacy_reach_the_wire_without_mutation():
    routing = {
        "provider": {
            "only": ["example-provider"],
            "allow_fallbacks": False,
            "require_parameters": True,
            "zdr": True,
            "data_collection": "deny",
        },
        "reasoning": {"effort": "high"},
    }
    llm = LLM(model="openrouter/vendor/model", litellm_extra_body=routing)
    options = select_chat_options(llm, user_kwargs={}, has_tools=True)
    request = OpenrouterConfig().transform_request(
        model="vendor/model",
        messages=[{"role": "user", "content": "test"}],
        optional_params={"extra_body": options["extra_body"]},
        litellm_params={},
        headers={},
    )
    assert request["provider"] == {
        "only": ["example-provider"],
        "allow_fallbacks": False,
        "require_parameters": True,
        "zdr": True,
        "data_collection": "deny",
    }
    assert request["reasoning"] == {"effort": "high"}
    assert llm.litellm_extra_body == routing


def test_openrouter_reported_cost_reaches_telemetry_for_an_unknown_model():
    body = {
        "id": "gen-openrouter-test",
        "object": "chat.completion",
        "created": 1,
        "model": "vendor/not-in-static-catalog",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "ok"},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 10,
            "total_tokens": 110,
            "cost": 0.012345,
        },
    }
    response = OpenrouterConfig().transform_response(
        model="vendor/not-in-static-catalog",
        raw_response=httpx.Response(200, text=json.dumps(body)),
        model_response=ModelResponse(),
        logging_obj=MagicMock(),
        request_data={},
        messages=[],
        optional_params={},
        litellm_params={},
        encoding=None,
    )
    telemetry = Telemetry(
        model_name="openrouter/vendor/not-in-static-catalog", metrics=Metrics()
    )
    assert telemetry.on_response(response).accumulated_cost == 0.012345
