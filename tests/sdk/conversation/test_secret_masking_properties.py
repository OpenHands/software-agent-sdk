"""Property tests for secret masking.

Every registered secret is fully hidden in output, whatever other, possibly
overlapping, secrets are registered, and the streaming masker produces the same
output as the batch masker however that output is split into chunks. Masking
an event redacts content only: its ids, timestamp and source are unchanged and
the masked event still loads back as a valid Event.
"""

import uuid

from hypothesis import given, settings, strategies as st

from openhands.sdk.conversation.secret_registry import SecretRegistry
from openhands.sdk.event import Event, MessageEvent, ObservationEvent
from openhands.sdk.event.security import SecurityAnalysisEvent
from openhands.sdk.llm import Message, TextContent
from openhands.sdk.security.risk import SecurityRisk
from openhands.sdk.tool.builtins.finish import FinishObservation


MASK = "<secret-hidden>"

# A small alphabet makes registered secrets nest in and overlap each other. The
# padding alphabet shares no character with the secrets or with MASK.
SECRET_VALUES = st.lists(
    st.text(alphabet="AB12", min_size=2, max_size=8),
    min_size=1,
    max_size=4,
    unique=True,
)
PADDING = st.text(alphabet="xyz ", max_size=5)

PARENT_IDS = st.none() | st.uuids().map(str)
TEXT = st.text(min_size=1, max_size=30)

STRUCTURAL_FIELDS = {
    "id",
    "timestamp",
    "source",
    "parent_id",
    "tool_call_id",
    "action_id",
}


def _registry(values: list[str]) -> SecretRegistry:
    registry = SecretRegistry()
    registry.update_secrets({f"SECRET_{i}": value for i, value in enumerate(values)})
    for name in registry.secret_sources:
        registry.get_secret_value(name)
    return registry


def _message_event(text: str, parent_id: str | None) -> MessageEvent:
    return MessageEvent(
        source="user",
        llm_message=Message(role="user", content=[TextContent(text=text)]),
        parent_id=parent_id,
    )


def _observation_event(
    text: str, action_id: uuid.UUID, parent_id: str | None
) -> ObservationEvent:
    return ObservationEvent(
        observation=FinishObservation.from_text(text),
        tool_name="finish",
        tool_call_id=f"call_{action_id.hex[:8]}",
        action_id=str(action_id),
        parent_id=parent_id,
    )


def _security_analysis_event(
    action_id: uuid.UUID, risk: SecurityRisk, parent_id: str | None
) -> SecurityAnalysisEvent:
    return SecurityAnalysisEvent(
        analyzer="llm",
        policy="never",
        risks={str(action_id): risk},
        parent_id=parent_id,
    )


EVENTS = st.one_of(
    st.builds(_message_event, TEXT, PARENT_IDS),
    st.builds(_observation_event, TEXT, st.uuids(), PARENT_IDS),
    st.builds(
        _security_analysis_event, st.uuids(), st.sampled_from(SecurityRisk), PARENT_IDS
    ),
)


def _strings_in(value: object) -> list[str]:
    match value:
        case str():
            return [value] if value else []
        case dict():
            return [s for item in value.values() for s in _strings_in(item)]
        case list():
            return [s for item in value for s in _strings_in(item)]
        case _:
            return []


@settings(max_examples=300)
@given(values=SECRET_VALUES, data=st.data())
def test_every_registered_secret_is_fully_hidden(
    values: list[str], data: st.DataObject
) -> None:
    registry = _registry(values)
    secret = data.draw(st.sampled_from(values), label="secret")
    prefix = data.draw(PADDING, label="prefix")
    suffix = data.draw(PADDING, label="suffix")

    masked = registry.mask_secrets_in_output(prefix + secret + suffix)

    assert masked == prefix + MASK + suffix


@settings(max_examples=300)
@given(values=SECRET_VALUES, data=st.data())
def test_stream_masker_matches_batch_masker_for_any_chunking(
    values: list[str], data: st.DataObject
) -> None:
    registry = _registry(values)
    secret = data.draw(st.sampled_from(values), label="secret")
    text = (
        data.draw(PADDING, label="prefix") + secret + data.draw(PADDING, label="suffix")
    )
    cuts = sorted(
        data.draw(st.sets(st.integers(0, len(text)), max_size=4), label="cuts")
    )
    chunks = [text[start:end] for start, end in zip([0, *cuts], [*cuts, len(text)])]

    stream = registry.compile_stream_mask()
    streamed = "".join(stream.feed(chunk) for chunk in chunks) + stream.flush()

    assert streamed == registry.mask_secrets_in_output(text)


@given(event=EVENTS, data=st.data())
def test_masking_an_event_only_redacts_content(
    event: Event, data: st.DataObject
) -> None:
    # The secret is a piece of some string in the event, so it collides with a
    # content or a structural value.
    source = data.draw(
        st.sampled_from(_strings_in(event.model_dump(mode="json"))), label="source"
    )
    start = data.draw(st.integers(0, len(source) - 1), label="start")
    end = data.draw(st.integers(start + 1, min(start + 8, len(source))), label="end")
    registry = SecretRegistry()
    registry.update_secrets({"SECRET": source[start:end]})

    masked = registry.mask_secrets_in_model(event)

    assert masked.model_dump(include=STRUCTURAL_FIELDS) == event.model_dump(
        include=STRUCTURAL_FIELDS
    )
    assert Event.model_validate_json(masked.model_dump_json()) == masked
