import json
import stat
from uuid import uuid4

from openhands.agent_server.stream_capture import StreamCapture
from openhands.sdk import Message, TextContent
from openhands.sdk.agent.stream_context import StreamAborted, StreamDelta, StreamStarted
from openhands.sdk.event import MessageEvent


def test_retry_capture_binds_ordered_deltas_to_durable_response(tmp_path):
    directory = tmp_path / "stream-captures"
    capture = StreamCapture(directory)
    response_id = str(uuid4())
    capture.progress(StreamStarted(response_id, 1, 3))
    capture.progress(StreamDelta(response_id, 1, 0, "text", "retry me"))
    capture.progress(StreamStarted(response_id, 2, 3))
    capture.progress(StreamDelta(response_id, 2, 0, "reasoning", "inspect", None, 0))
    capture.progress(StreamDelta(response_id, 2, 1, "text", "done", None, 0))
    event = MessageEvent(
        id=response_id,
        source="agent",
        llm_message=Message(role="assistant", content=[TextContent(text="done")]),
    )
    capture.committed(event)
    capture.committed(event)

    previous, current = sorted(directory.glob("*.jsonl"))
    assert stat.S_IMODE(current.stat().st_mode) == 0o600
    assert json.loads(previous.read_text().splitlines()[-1])["status"] == "superseded"
    rows = [json.loads(line) for line in current.read_text().splitlines()]
    assert [row["type"] for row in rows] == ["start", "delta", "delta", "end"]
    assert {row["response_id"] for row in rows} == {event.id}
    assert [row["sequence"] for row in rows[1:3]] == [0, 1]
    assert [row["channel"] for row in rows[1:3]] == ["reasoning", "text"]
    assert rows[-1]["status"] == "committed"


def test_aborted_capture_has_no_committed_end(tmp_path):
    capture = StreamCapture(tmp_path)
    response_id = str(uuid4())
    capture.progress(StreamStarted(response_id, 1, None))
    capture.progress(StreamAborted(response_id, 1, "cancelled"))

    path = next(tmp_path.glob("*.jsonl"))
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert [row["type"] for row in rows] == ["start", "end"]
    assert rows[-1]["status"] == "aborted"
