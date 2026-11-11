"""Secret masking must leave id fields unchanged.

A short secret can occur inside a random UUID. If masking rewrites the id, the
event file name no longer matches ``EVENT_NAME_RE``, so ``EventLog`` cannot
reload the conversation. A masked ``tool_call_id`` or ``action_id`` also
breaks tool-call pairing in the LLM request.
"""

from collections.abc import Callable
from typing import Any

import pytest
from pydantic import BaseModel

from openhands.sdk.conversation.event_store import EventLog
from openhands.sdk.conversation.secret_registry import SecretRegistry
from openhands.sdk.event import (
    ActionEvent,
    AgentErrorEvent,
    Condensation,
    EventID,
    HookExecutionEvent,
    LLMCompletionLogEvent,
    MessageEvent,
    ObservationEvent,
    UserRejectObservation,
)
from openhands.sdk.event.acp_tool_call import ACPToolCallEvent
from openhands.sdk.event.security import SecurityAnalysisEvent
from openhands.sdk.io.local import LocalFileStore
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.tool.builtins.finish import FinishObservation


SECRET = "a1b"
MASK = "<secret-hidden>"
ID = "fa1b4e36-bdd7-42bf-99df-e184c9dc0d9a"
TEXT = f"text {SECRET}"


def _registry() -> SecretRegistry:
    registry = SecretRegistry()
    registry.update_secrets({"SHORT": SECRET})
    registry.get_secret_value("SHORT")  # resolve -> tracked for masking
    return registry


def _message(parent_id: str | None) -> MessageEvent:
    return MessageEvent(
        source="user",
        parent_id=parent_id,
        llm_message=Message(role="user", content=[TextContent(text="hi")]),
    )


def _tool_call() -> MessageToolCall:
    return MessageToolCall(id=ID, name="tool", arguments="{}", origin="completion")


def test_masked_event_reloads_from_disk(tmp_path):
    """The repro from the bug report: the log must reload after masking."""
    log = EventLog(LocalFileStore(str(tmp_path)))
    first = _message(None)
    log.append(first)
    analysis = _registry().mask_secrets_in_model(
        SecurityAnalysisEvent(
            id=ID,
            parent_id=first.id,
            analyzer="LLMSecurityAnalyzer",
            policy="NeverConfirm",
            risks={},
        )
    )
    log.append(analysis)
    last = _message(analysis.id)
    log.append(last)

    assert analysis.id == ID
    assert EventLog(LocalFileStore(str(tmp_path))).get_index(last.id) == 2


# Each case: an event with ``ID`` in every id field, the id field names, and a
# getter for a text field that holds ``TEXT`` and must still be masked.
_CASES: list[tuple[Any, list[str], Callable[[Any], Any]]] = [
    (
        SecurityAnalysisEvent(
            id=ID, parent_id=ID, analyzer=TEXT, policy="NeverConfirm", risks={}
        ),
        ["id", "parent_id"],
        lambda e: e.analyzer,
    ),
    (
        ActionEvent(
            thought=[TextContent(text="thinking")],
            reasoning_content=TEXT,
            tool_name="tool",
            tool_call_id=ID,
            tool_call=_tool_call(),
            llm_response_id=ID,
        ),
        ["tool_call_id", "llm_response_id"],
        lambda e: e.reasoning_content,
    ),
    (
        MessageEvent(
            source="agent",
            llm_message=Message(role="assistant", content=[TextContent(text=TEXT)]),
            llm_response_id=ID,
        ),
        ["llm_response_id"],
        lambda e: e.llm_message.content[0].text,
    ),
    (
        ObservationEvent(
            observation=FinishObservation.from_text(TEXT),
            tool_name="tool",
            tool_call_id=ID,
            action_id=ID,
        ),
        ["tool_call_id", "action_id"],
        lambda e: e.observation.text,
    ),
    (
        UserRejectObservation(
            tool_name="tool", tool_call_id=ID, action_id=ID, rejection_reason=TEXT
        ),
        ["tool_call_id", "action_id"],
        lambda e: e.rejection_reason,
    ),
    (
        AgentErrorEvent(tool_name="tool", tool_call_id=ID, error=TEXT),
        ["tool_call_id"],
        lambda e: e.error,
    ),
    (
        ACPToolCallEvent(tool_call_id=ID, title=TEXT),
        ["tool_call_id"],
        lambda e: e.title,
    ),
    (
        Condensation(forgotten_event_ids={ID}, llm_response_id=ID, summary=TEXT),
        ["forgotten_event_ids", "llm_response_id"],
        lambda e: e.summary,
    ),
    (
        HookExecutionEvent(
            hook_event_type="PreToolUse",
            hook_command="hook.sh",
            success=True,
            exit_code=0,
            stdout=TEXT,
            action_id=ID,
            message_id=ID,
        ),
        ["action_id", "message_id"],
        lambda e: e.stdout,
    ),
    (
        LLMCompletionLogEvent(filename="log.json", log_data=TEXT, usage_id=ID),
        ["usage_id"],
        lambda e: e.log_data,
    ),
]


@pytest.mark.parametrize(
    ("event", "id_fields", "get_text"),
    _CASES,
    ids=[type(event).__name__ for event, _, _ in _CASES],
)
def test_masks_text_but_not_id_fields(event, id_fields, get_text):
    masked = _registry().mask_secrets_in_model(event)

    for name in id_fields:
        assert getattr(masked, name) == getattr(event, name), name
    assert get_text(masked) == f"text {MASK}"


def test_security_analysis_details_keyed_by_event_id_are_still_masked():
    """``dict[EventID, ...]`` marks only the keys; the values hold analyzer text."""
    event = SecurityAnalysisEvent(
        analyzer="LLMSecurityAnalyzer",
        policy="NeverConfirm",
        risks={},
        details={ID: {"reason": TEXT}},
    )

    masked = _registry().mask_secrets_in_model(event)

    assert masked.details == {ID: {"reason": f"text {MASK}"}}


class _IdContainers(BaseModel):
    optional: EventID | None = None
    many: list[EventID] = []
    unique: set[EventID] = set()
    fixed: tuple[EventID, ...] = ()
    by_name: dict[str, EventID] = {}


def test_id_types_inside_containers_are_not_masked():
    model = _IdContainers(
        optional=ID, many=[ID], unique={ID}, fixed=(ID,), by_name={"k": ID}
    )

    masked = _registry().mask_secrets_in_model(model)

    assert masked == model


class _MixedContainers(BaseModel):
    either: EventID | list[str]
    pair: tuple[EventID, str]
    by_name: dict[str, EventID | str]


def test_text_beside_an_id_type_is_still_masked():
    """A type is skipped only when every str-bearing part of it is an id."""
    model = _MixedContainers(either=[TEXT], pair=(ID, TEXT), by_name={"k": TEXT})

    masked = _registry().mask_secrets_in_model(model)

    assert masked.either == [f"text {MASK}"]
    assert masked.pair[1] == f"text {MASK}"
    assert masked.by_name == {"k": f"text {MASK}"}
