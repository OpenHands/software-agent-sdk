"""Generic HTTP exporter.

Mirrors the PostHog exporter tests: payload shape, auth, allowlist compliance,
and failure isolation. The retry/drop/shutdown guarantees live in
``BufferedTelemetrySink`` and are covered there, so this asserts only that
failures propagate to it rather than being swallowed.
"""

import json
from datetime import UTC, datetime

import httpx
import pytest

from openhands.agent_server.telemetry import models as m
from openhands.agent_server.telemetry.http_exporter import HttpExporter


ENDPOINT = "https://telemetry.example.test/v1/events"


def make_event(distinct_id: str = "user-1") -> m.DiagnosticEvent:
    return m.DiagnosticEvent(
        event_name=m.EventName.CONVERSATION_FINISHED,
        occurred_at=datetime(2026, 7, 21, 10, 0, 0, tzinfo=UTC),
        distinct_id=distinct_id,
        runtime=m.RuntimeProperties(
            server_version="1.36.1",
            sdk_version="1.36.1",
            tools_version="1.36.1",
            build_git_sha="unknown",
            build_git_ref="unknown",
            python_version="3.13",
            platform="darwin",
            deferred_init=False,
        ),
        properties=m.ConversationOutcomeProperties(
            conversation_ref="a" * 32,
            terminal_status="finished",
            duration_bucket="5-15",
            event_count_bucket="5-20",
            total_tokens_bucket="1000-10000",
            cost_bucket="lt-0p01",
            llm_model_family="anthropic",
        ),
    )


def exporter_with(handler, **kw) -> HttpExporter:
    exporter = HttpExporter(ENDPOINT, **kw)
    exporter._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return exporter


async def test_posts_a_batch_in_the_documented_shape():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        captured["headers"] = dict(request.headers)
        return httpx.Response(202)

    await exporter_with(handler).send([make_event(), make_event("user-2")])

    assert captured["url"] == ENDPOINT
    body = captured["body"]
    assert body["schema_version"] == m.TELEMETRY_SCHEMA_VERSION
    assert len(body["events"]) == 2

    first = body["events"][0]
    assert first["event"] == "agent_server.conversation_finished"
    assert first["distinct_id"] == "user-1"
    assert first["occurred_at"].startswith("2026-07-21T10:00:00")
    assert set(first) == {"event", "distinct_id", "occurred_at", "properties"}


async def test_sent_properties_stay_within_the_allowlist():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200)

    await exporter_with(handler).send([make_event()])

    props = captured["body"]["events"][0]["properties"]
    assert set(props) <= set(m.EXPECTED_PROPERTY_NAMES)
    # distinct_id is the addressing field, not a property.
    assert "distinct_id" not in props


async def test_bearer_token_is_sent_when_configured():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200)

    await exporter_with(handler, token="secret-token").send([make_event()])
    assert captured["auth"] == "Bearer secret-token"


async def test_no_auth_header_without_a_token():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200)

    await exporter_with(handler).send([make_event()])
    assert captured["auth"] is None


@pytest.mark.parametrize("status", [400, 401, 429, 500, 503])
async def test_error_responses_propagate_to_the_sink(status: int):
    """The sink owns retry and drop; the exporter must not swallow failures."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status)

    with pytest.raises(httpx.HTTPStatusError):
        await exporter_with(handler).send([make_event()])


async def test_transport_errors_propagate_to_the_sink():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("endpoint unreachable")

    with pytest.raises(httpx.ConnectError):
        await exporter_with(handler).send([make_event()])


async def test_aclose_is_safe_to_call_twice():
    exporter = exporter_with(lambda request: httpx.Response(200))
    await exporter.aclose()
    await exporter.aclose()


async def test_the_payload_carries_no_raw_identifiers():
    """Same guarantee as PostHog: the transport does not re-add anything."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["raw"] = request.content.decode()
        return httpx.Response(200)

    await exporter_with(handler).send([make_event()])

    for forbidden in ("Traceback", "/Users/", "sk-ant"):
        assert forbidden not in captured["raw"]
