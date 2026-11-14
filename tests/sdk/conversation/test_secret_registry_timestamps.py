"""Framework event timestamps survive masking; payload timestamps do not."""

import json
from typing import Annotated

import pytest
from pydantic import BaseModel, SecretStr

from openhands.sdk.conversation.event_store import EventLog
from openhands.sdk.conversation.secret_registry import SecretRegistry
from openhands.sdk.event import Event, MessageEvent, ObservationEvent
from openhands.sdk.event.security import SecurityAnalysisEvent
from openhands.sdk.io.local import LocalFileStore
from openhands.sdk.llm import Message, TextContent
from openhands.sdk.secret import StaticSecret
from openhands.sdk.tool.builtins.finish import FinishObservation
from openhands.sdk.utils.masking import SkipSecretMasking


TIMESTAMP = "2026-10-27T12:34:56.123456"
SECRET = TIMESTAMP[:10]
MASK = "<secret-hidden>"


@pytest.mark.parametrize(
    "event",
    [
        SecurityAnalysisEvent(
            timestamp=TIMESTAMP,
            analyzer=SECRET,
            policy=SECRET,
            risks={},
            details={"action": {"timestamp": SECRET, "nested": [{"id": SECRET}]}},
        ),
        MessageEvent(
            timestamp=TIMESTAMP,
            source="agent",
            llm_message=Message(role="assistant", content=[TextContent(text=SECRET)]),
        ),
        ObservationEvent(
            timestamp=TIMESTAMP,
            observation=FinishObservation.from_text(SECRET),
            tool_name="finish",
            tool_call_id="call-finish",
            action_id="action-finish",
        ),
    ],
    ids=["security-analysis", "message", "observation"],
)
def test_event_timestamp_survives_masking_and_reload(event, tmp_path):
    registry = SecretRegistry(
        secret_sources={"DATE": StaticSecret(value=SecretStr(SECRET))}
    )
    masked = registry.mask_secrets_in_model(event)

    assert masked.timestamp == event.timestamp == TIMESTAMP
    expected = event.model_dump(mode="json")
    expected.pop("timestamp")
    assert masked.model_dump(mode="json", exclude={"timestamp"}) == json.loads(
        json.dumps(expected).replace(SECRET, MASK)
    )
    assert Event.model_validate_json(masked.model_dump_json()) == masked

    log = EventLog(LocalFileStore(str(tmp_path)))
    log.append(masked)
    (path,) = tmp_path.glob("events/*.json")
    assert json.loads(path.read_text())["timestamp"] == TIMESTAMP
    assert EventLog(LocalFileStore(str(tmp_path)))[0].timestamp == TIMESTAMP


class _Envelope(BaseModel):
    event: SecurityAnalysisEvent
    timestamp: str
    credential: SecretStr
    opaque: Annotated[str, SkipSecretMasking()]


def test_timestamp_skip_is_inherited_without_skipping_surrounding_payload():
    event = SecurityAnalysisEvent(
        timestamp=TIMESTAMP, analyzer=SECRET, policy="p", risks={}
    )
    envelope = _Envelope(
        event=event, timestamp=SECRET, credential=SecretStr(SECRET), opaque=SECRET
    )
    registry = SecretRegistry(
        secret_sources={"DATE": StaticSecret(value=SecretStr(SECRET))}
    )

    masked = registry.mask_secrets_in_model(envelope)

    assert masked.event.timestamp == TIMESTAMP
    assert masked.event.analyzer == MASK
    assert masked.timestamp == MASK
    assert masked.opaque == SECRET
    assert masked.credential.get_secret_value() == SECRET
    assert masked.model_dump(mode="json")["credential"] == "**********"
    assert envelope.event.analyzer == SECRET
