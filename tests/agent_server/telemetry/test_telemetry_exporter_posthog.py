"""PostHog exporter behaviour, especially what it must *never* call.

Uses a fake ``posthog`` module so these run whether or not the optional extra
is installed.
"""

import sys
import types
from datetime import UTC, datetime
from unittest.mock import Mock

import pytest

from openhands.agent_server.telemetry import models as m


@pytest.fixture
def fake_posthog(monkeypatch):
    """Install a fake ``posthog`` module and return the constructed client.

    ``Mock(spec=...)`` is the point: the client exposes *only* the methods the
    exporter is allowed to use, so any attempt to call ``identify`` or
    ``alias`` raises ``AttributeError`` instead of silently creating or merging
    a person profile.
    """
    client = Mock(spec=["capture", "flush", "shutdown"])
    constructor_kwargs = {}

    def _posthog_ctor(**kwargs):
        constructor_kwargs.update(kwargs)
        return client

    module = types.ModuleType("posthog")
    module.Posthog = _posthog_ctor  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "posthog", module)

    return client, constructor_kwargs


def make_event(distinct_id: str) -> m.DiagnosticEvent:
    return m.DiagnosticEvent(
        event_name=m.EventName.CONVERSATION_FINISHED,
        occurred_at=datetime.now(UTC),
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


async def test_identified_events_reuse_the_host_distinct_id_verbatim(fake_posthog):
    """Correlation depends on byte-for-byte reuse of the host's identity."""
    from openhands.agent_server.telemetry.posthog_exporter import PostHogExporter

    client, _ = fake_posthog
    exporter = PostHogExporter("phc_test")

    await exporter.send([make_event("user-abc-123")])

    assert client.capture.call_count == 1
    kwargs = client.capture.call_args.kwargs
    assert kwargs["distinct_id"] == "user-abc-123"


async def test_exporter_never_creates_or_merges_an_identity(fake_posthog):
    """identify()/alias() would duplicate or irreversibly merge a person."""
    from openhands.agent_server.telemetry.posthog_exporter import PostHogExporter

    client, _ = fake_posthog
    exporter = PostHogExporter("phc_test")

    await exporter.send([make_event("user-abc"), make_event("anon:deadbeef")])

    # The Mock spec omits these entirely; assert the intent explicitly too.
    assert not hasattr(client, "identify")
    assert not hasattr(client, "alias")
    assert not hasattr(client, "group_identify")
    assert client.capture.called


async def test_anonymous_events_do_not_create_person_profiles(fake_posthog):
    from openhands.agent_server.telemetry.posthog_exporter import PostHogExporter

    client, _ = fake_posthog
    exporter = PostHogExporter("phc_test")

    await exporter.send([make_event("anon:deadbeef")])
    properties = client.capture.call_args.kwargs["properties"]
    assert properties["$process_person_profile"] is False


async def test_identified_events_do_attach_to_the_existing_person(fake_posthog):
    from openhands.agent_server.telemetry.posthog_exporter import PostHogExporter

    client, _ = fake_posthog
    exporter = PostHogExporter("phc_test")

    await exporter.send([make_event("user-abc")])
    properties = client.capture.call_args.kwargs["properties"]
    # Absent, so the event lands on the person the host already identified.
    assert "$process_person_profile" not in properties


async def test_vendor_side_collection_is_disabled(fake_posthog):
    """Autocapture would ship tracebacks, defeating the sanitizer entirely."""
    from openhands.agent_server.telemetry.posthog_exporter import PostHogExporter

    _, kwargs = fake_posthog
    PostHogExporter("phc_test")

    assert kwargs["enable_exception_autocapture"] is False
    assert kwargs["log_captured_exceptions"] is False
    assert kwargs["disable_geoip"] is True


async def test_sent_properties_stay_within_the_allowlist(fake_posthog):
    from openhands.agent_server.telemetry.posthog_exporter import PostHogExporter

    client, _ = fake_posthog
    exporter = PostHogExporter("phc_test")

    await exporter.send([make_event("user-abc")])
    properties = client.capture.call_args.kwargs["properties"]

    assert set(properties) <= set(m.EXPECTED_PROPERTY_NAMES)
    assert "distinct_id" not in properties


async def test_host_is_configurable(fake_posthog):
    from openhands.agent_server.telemetry.posthog_exporter import PostHogExporter

    _, kwargs = fake_posthog
    PostHogExporter("phc_test", host="https://eu.i.posthog.com")
    assert kwargs["host"] == "https://eu.i.posthog.com"
