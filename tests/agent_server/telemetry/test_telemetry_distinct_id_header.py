"""The ``X-OpenHands-Telemetry-Distinct-Id`` request header.

Request-scoped activity (a failed request, and later e.g. LLM-profile creation)
has no conversation ``user_id``. The frontend attaches its PostHog distinct id
as this header so that activity attributes to the same person instead of an
anonymous per-process id.
"""

import pytest

from openhands.agent_server.telemetry.factory import (
    ANONYMOUS_PREFIX,
    DISTINCT_ID_HEADER,
    DiagnosticEventFactory,
    build_runtime_properties,
    distinct_id_from_header,
)


def test_header_name_is_the_documented_one():
    assert DISTINCT_ID_HEADER == "X-OpenHands-Telemetry-Distinct-Id"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("phc_user_abc123", "phc_user_abc123"),
        ("  spaced-id  ", "spaced-id"),
        (
            "01234567-89ab-cdef-0123-456789abcdef",
            "01234567-89ab-cdef-0123-456789abcdef",
        ),
    ],
)
def test_valid_header_is_passed_through(raw, expected):
    assert distinct_id_from_header(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "   ", "\t\n", "\x00\x01"])
def test_empty_or_control_only_header_falls_back_to_none(raw):
    assert distinct_id_from_header(raw) is None


def test_control_characters_are_stripped():
    assert distinct_id_from_header("ab\ncd\tef") == "abcdef"


def test_oversized_header_is_bounded():
    result = distinct_id_from_header("x" * 500)
    assert result is not None
    assert len(result) == 256


def _factory() -> DiagnosticEventFactory:
    return DiagnosticEventFactory(
        runtime=build_runtime_properties(deferred_init=False), salt="s"
    )


def test_supplied_header_becomes_the_event_distinct_id():
    from openhands.agent_server.telemetry import models as m

    factory = _factory()
    distinct_id = distinct_id_from_header("phc_user_42")
    event = factory.build(
        m.EventName.REQUEST_FAILED,
        m.RequestFailedProperties(
            route_template="/api/x",
            method="GET",
            status_code=500,
            error_class="ValueError",
            error_category="internal",
            error_fingerprint="a" * 16,
        ),
        user_id=distinct_id,
    )
    assert event.distinct_id == "phc_user_42"


def test_absent_header_falls_back_to_the_anonymous_id():
    from openhands.agent_server.telemetry import models as m

    factory = _factory()
    event = factory.build(
        m.EventName.REQUEST_FAILED,
        m.RequestFailedProperties(
            route_template="/api/x",
            method="GET",
            status_code=500,
            error_class="ValueError",
            error_category="internal",
            error_fingerprint="a" * 16,
        ),
        user_id=distinct_id_from_header(None),
    )
    assert event.distinct_id.startswith(ANONYMOUS_PREFIX)
