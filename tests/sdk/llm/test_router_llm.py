import asyncio

import pytest

from openhands.sdk.llm import Message, RouterLLM, TextContent
from openhands.sdk.llm.llm_response import LLMResponse
from openhands.sdk.testing import TestLLM


class FirstRouter(RouterLLM):
    router_name: str = "first_router"

    def select_llm(self, messages: list[Message]) -> str:  # noqa: ARG002
        return "first"


def _message(text: str = "hi") -> list[Message]:
    return [Message(role="user", content=[TextContent(text=text)])]


def _router() -> FirstRouter:
    first = TestLLM.from_messages(
        [Message(role="assistant", content=[TextContent(text="first")])],
        usage_id="first",
    )
    second = TestLLM.from_messages(
        [Message(role="assistant", content=[TextContent(text="second")])],
        usage_id="second",
    )
    return FirstRouter(llms_for_routing={"first": first, "second": second})


def _response_text(response: LLMResponse) -> str:
    content = response.message.content[0]
    assert isinstance(content, TextContent)
    return content.text


def test_router_auto_selects_and_records_selected_key():
    router = _router()

    response = router.completion(_message())

    assert _response_text(response) == "first"
    assert router.routing_mode == "auto"
    assert router.selected_llm_key == "first"
    assert router.active_llm is router.llms_for_routing["first"]


def test_router_manual_switch_returns_copy_and_routes_to_key():
    router = _router()

    switched = router.switch_llm("second")
    response = switched.completion(_message())

    assert router.routing_mode == "auto"
    assert router.selected_llm_key is None
    assert switched.routing_mode == "manual"
    assert switched.selected_llm_key == "second"
    assert _response_text(response) == "second"


def test_router_rejects_unknown_manual_key():
    router = _router()

    with pytest.raises(ValueError, match="missing"):
        router.switch_llm("missing")


def test_router_async_generate_delegates_to_manual_key():
    router = _router().switch_llm("second")

    response = asyncio.run(router.agenerate(_message()))

    assert _response_text(response) == "second"
    assert router.selected_llm_key == "second"
