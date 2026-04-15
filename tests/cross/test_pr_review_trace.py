"""Test that trace data from PR review can be serialized to JSON."""

import json
import uuid

import pytest
from lmnr.sdk.types import LaminarSpanContext


def test_span_context_requires_json_mode_for_serialization():
    """Verify model_dump(mode='json') is required for JSON serialization.

    model_dump() returns uuid.UUID objects which are not JSON serializable.
    model_dump(mode='json') converts them to strings.
    """
    ctx = LaminarSpanContext(
        trace_id=uuid.uuid4(),
        span_id=uuid.uuid4(),
        is_remote=False,
        span_path=["conversation"],
        span_ids_path=["span_123"],
    )

    # Without mode='json': UUIDs are not serializable
    with pytest.raises(TypeError, match="not JSON serializable"):
        json.dumps({"span_context": ctx.model_dump()})

    # With mode='json': UUIDs become strings, serialization works
    result = json.dumps({"span_context": ctx.model_dump(mode="json")})
    assert isinstance(json.loads(result)["span_context"]["trace_id"], str)
