"""Conversation failures from different sites do not share a grouping key."""

import pytest

from openhands.sdk.agent.base import AgentBase
from openhands.sdk.conversation import Conversation
from openhands.sdk.conversation.exceptions import ConversationRunError
from openhands.sdk.conversation.types import (
    ConversationCallbackType,
    ConversationTokenCallbackType,
)
from openhands.sdk.event.conversation_error import ConversationErrorEvent
from openhands.sdk.event.error_classification import FailureKind
from openhands.sdk.event.error_origin import origin_from_exception
from openhands.sdk.llm import LLM


_MESSAGE = "MSGMARKER-conv summarize the memo"
_PATH = "/home/alice/private/repo/main.py"
_SECRET = "sk-ant-api03-CONVSECRET"


def _raiser(module: str, function: str):
    namespace: dict[str, object] = {"__name__": module}
    exec(
        f"def {function}():\n"
        f"    raise TypeError({_MESSAGE!r} + ' ' + {_PATH!r} + ' ' + {_SECRET!r})\n",
        namespace,
    )
    raiser = namespace[function]
    assert callable(raiser)
    return raiser


def _event_for(raiser) -> ConversationErrorEvent:
    try:
        raiser()
    except TypeError as exc:
        return ConversationErrorEvent(
            source="environment",
            code=type(exc).__name__,
            detail=str(exc),
            origin=origin_from_exception(exc),
        )
    raise AssertionError("expected TypeError")


def test_distinct_sdk_origins_get_distinct_grouping_keys() -> None:
    site_one = _raiser("openhands.sdk._conv_origin_a", "fail_a")
    site_two = _raiser("openhands.sdk._conv_origin_b", "fail_b")

    first = _event_for(site_one)
    repeat = _event_for(site_one)
    second = _event_for(site_two)

    assert first.origin == "openhands.sdk._conv_origin_a.fail_a"
    assert second.origin == "openhands.sdk._conv_origin_b.fail_b"
    assert first.grouping_key == repeat.grouping_key
    assert first.grouping_key != second.grouping_key
    for event in (first, repeat, second):
        assert event.classification is not None
        assert event.classification.kind == FailureKind.INTERNAL
        blob = f"{event.origin} {event.grouping_key}"
        assert _MESSAGE not in blob
        assert _PATH not in blob
        assert _SECRET not in blob


class _MarkedFailingAgent(AgentBase):
    def step(
        self,
        conversation,
        on_event: ConversationCallbackType,
        on_token: ConversationTokenCallbackType | None = None,
    ):  # noqa: D401, ARG002
        """Raise a marked TypeError from this agent."""
        raise TypeError(f"{_MESSAGE} {_PATH} {_SECRET}")


@pytest.mark.parametrize("method", ["run", "arun"])
async def test_run_handlers_attach_sanitized_origin(tmp_path, method: str) -> None:
    llm = LLM(model="gpt-4o-mini", api_key=None, usage_id="test-llm")
    keys: list[str | None] = []
    for index in range(2):
        directory = tmp_path / str(index)
        directory.mkdir()
        conv = Conversation(
            agent=_MarkedFailingAgent(llm=llm, tools=[]),
            persistence_dir=str(directory),
            workspace=str(directory),
        )
        with pytest.raises(ConversationRunError):
            if method == "run":
                conv.run()
            else:
                await conv.arun()
        errors = [
            event
            for event in conv.state.events
            if isinstance(event, ConversationErrorEvent)
        ]
        assert len(errors) == 1
        event = errors[0]
        assert event.code == "TypeError"
        assert event.classification is not None
        assert event.classification.kind == FailureKind.INTERNAL
        assert event.origin is not None
        assert event.grouping_key is not None
        blob = f"{event.origin} {event.grouping_key}"
        assert _MESSAGE not in blob
        assert _PATH not in blob
        assert _SECRET not in blob
        keys.append(event.grouping_key)
    assert keys[0] == keys[1]
