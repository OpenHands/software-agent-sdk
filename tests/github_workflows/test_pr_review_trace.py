"""Test that trace data from PR review can be serialized to JSON."""

import json
import uuid

from lmnr.sdk.types import LaminarSpanContext


def test_trace_data_with_span_context_is_json_serializable():
    """Ensure span_context uses model_dump(mode='json') for JSON compatibility.

    Laminar's get_laminar_span_context_dict() returns uuid.UUID objects which
    are not JSON serializable. The fix uses model_dump(mode='json') instead.
    """
    # Simulate Laminar.get_laminar_span_context() return value
    laminar_span_context = LaminarSpanContext(
        trace_id=uuid.uuid4(),
        span_id=uuid.uuid4(),
        is_remote=False,
        span_path=["conversation"],
        span_ids_path=["span_123"],
    )

    # This is the pattern used in agent_script.py
    span_context = laminar_span_context.model_dump(mode="json")

    trace_data = {
        "trace_id": str(uuid.uuid4()),
        "span_context": span_context,
        "pr_number": "1234",
        "repo_name": "OpenHands/software-agent-sdk",
        "commit_id": "abc123",
        "review_style": "roasted",
    }

    # Must be JSON serializable for artifact storage
    result = json.dumps(trace_data)
    parsed = json.loads(result)
    assert isinstance(parsed["span_context"]["trace_id"], str)
