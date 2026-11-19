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


def test_base_router_defaults_to_first_route_and_roundtrips():
    router = RouterLLM(
        llms_for_routing={
            "default": TestLLM.from_messages(
                [Message(role="assistant", content=[TextContent(text="default")])],
                usage_id="default",
            ),
            "second": TestLLM.from_messages(
                [Message(role="assistant", content=[TextContent(text="second")])],
                usage_id="second",
            ),
        }
    )

    response = router.completion(_message())
    restored = RouterLLM.model_validate_router(router.model_dump())

    assert _response_text(response) == "default"
    assert router.last_selected_route_name == "default"
    assert isinstance(restored, RouterLLM)
    assert restored.available_routes() == ("default", "second")


def test_router_auto_selects_and_records_selected_route_name():
    router = _router()

    response = router.completion(_message())

    assert _response_text(response) == "first"
    assert router.selection.mode == "auto"
    assert router.selection.manual_route_name is None
    assert router.last_selected_route_name == "first"
    assert router.active_llm is router.llms_for_routing["first"]


def test_router_manual_switch_returns_copy_and_routes_to_key():
    router = _router()

    switched = router.with_manual_route("second")
    response = switched.completion(_message())

    assert router.selection.mode == "auto"
    assert router.selection.manual_route_name is None
    assert router.last_selected_route_name is None
    assert switched.selection.mode == "manual"
    assert switched.selection.manual_route_name == "second"
    assert switched.last_selected_route_name == "second"
    assert _response_text(response) == "second"


def test_router_rejects_unknown_manual_key():
    router = _router()

    with pytest.raises(ValueError, match="missing"):
        router.with_manual_route("missing")


def test_router_async_generate_delegates_to_manual_key():
    router = _router().with_manual_route("second")

    response = asyncio.run(router.agenerate(_message()))

    assert _response_text(response) == "second"
    assert router.selection.manual_route_name == "second"
    assert router.last_selected_route_name == "second"
