"""Structural guarantees about what a diagnostic event can physically hold.

These tests are the enforcement mechanism described in the module docstring of
``telemetry/models.py``: rather than trusting a reviewer to notice that a new
property leaks, the type system is made to reject it.
"""

import typing
from typing import Annotated, Literal, get_args, get_origin

import pytest
from pydantic import BaseModel, ValidationError

from openhands.agent_server.telemetry import models as m


PROPERTY_MODELS: list[type[BaseModel]] = [
    m.RuntimeProperties,
    m.ServerLifecycleProperties,
    m.ConversationStartedProperties,
    m.ConversationOutcomeProperties,
    m.ErrorProperties,
    m.RequestFailedProperties,
]


def _is_constrained_str(annotation: object) -> bool:
    """True if the annotation is a *constrained* string, not a bare ``str``."""
    if get_origin(annotation) is Annotated:
        args = get_args(annotation)
        return args[0] is str and len(args) > 1
    return False


def _permitted(annotation: object) -> bool:
    # Unwrap optionals / unions.
    origin = get_origin(annotation)
    if origin is typing.Union or str(origin) == "<class 'types.UnionType'>":
        return all(
            _permitted(arg) for arg in get_args(annotation) if arg is not type(None)
        )
    if origin is Literal:
        return all(isinstance(a, str) for a in get_args(annotation))
    if _is_constrained_str(annotation):
        return True
    return annotation in (bool, int)


@pytest.mark.parametrize("model", PROPERTY_MODELS, ids=lambda mo: mo.__name__)
def test_no_unconstrained_property_fields(model: type[BaseModel]):
    """No property may be a bare str, Any, dict or list.

    A bare ``str`` can hold a prompt, a traceback, or an API key. Every field
    must therefore be a bool, an int, a Literal, or a pattern-constrained str.
    """
    hints = typing.get_type_hints(model, include_extras=True)
    offenders = [
        name
        for name, annotation in hints.items()
        if name != "kind" and not _permitted(annotation)
    ]
    assert offenders == [], (
        f"{model.__name__} has unconstrained field(s) {offenders}; a bare "
        "str/Any/dict can carry a prompt, path or secret into analytics."
    )


@pytest.mark.parametrize("model", PROPERTY_MODELS, ids=lambda mo: mo.__name__)
def test_property_models_are_frozen_and_closed(model: type[BaseModel]):
    assert model.model_config.get("frozen") is True
    assert model.model_config.get("extra") == "forbid"


def test_property_names_match_the_declared_allowlist():
    """Adding a property must be a deliberate act, not a silent one."""
    actual: set[str] = {"schema_version"}
    for model in PROPERTY_MODELS:
        actual.update(n for n in model.model_fields if n != "kind")

    assert actual == set(m.EXPECTED_PROPERTY_NAMES), (
        "Diagnostic property set changed. Update EXPECTED_PROPERTY_NAMES "
        "only after confirming the new property cannot carry user data.\n"
        f"  added:   {sorted(actual - set(m.EXPECTED_PROPERTY_NAMES))}\n"
        f"  removed: {sorted(set(m.EXPECTED_PROPERTY_NAMES) - actual)}"
    )


@pytest.mark.parametrize(
    "leak",
    [
        "Summarize the following private document: Dear Bob, ...",
        "sk-ant-api03-AAAABBBBCCCCDDDD",
        "/Users/alice/src/secret-project/main.py",
        "Traceback (most recent call last):\n  File ...",
        "user@example.com",
    ],
    ids=["prompt", "api_key", "path", "traceback", "email"],
)
def test_sensitive_values_are_rejected_at_construction(leak: str):
    """A leak becomes a ValidationError, not a review miss."""
    with pytest.raises(ValidationError):
        m.ErrorProperties(
            conversation_ref="a" * 32,
            error_class=leak,
            error_category="internal",
            error_fingerprint="b" * 16,
            is_first_party=True,
            is_terminal=True,
        )


def test_extra_properties_are_forbidden():
    with pytest.raises(ValidationError):
        m.ServerLifecycleProperties(error_message="boom")  # type: ignore[call-arg]


def test_route_template_rejects_a_concrete_path_with_an_id():
    """The 500 handler must send the route template, not the real URL."""
    m.RequestFailedProperties(
        route_template="/api/conversations/{conversation_id}",
        method="POST",
        status_code=500,
        error_class="ValueError",
        error_category="internal",
        error_fingerprint="c" * 16,
    )
    with pytest.raises(ValidationError):
        m.RequestFailedProperties(
            route_template="/api/conversations/not a route!",
            method="POST",
            status_code=500,
            error_class="ValueError",
            error_category="internal",
            error_fingerprint="c" * 16,
        )


def test_payload_carries_schema_version_and_excludes_distinct_id():
    from datetime import UTC, datetime

    event = m.DiagnosticEvent(
        event_name=m.EventName.SERVER_STARTED,
        occurred_at=datetime.now(UTC),
        distinct_id="user-123",
        runtime=m.RuntimeProperties(
            server_version="1.0.0",
            sdk_version="1.0.0",
            tools_version="1.0.0",
            build_git_sha="unknown",
            build_git_ref="unknown",
            python_version="3.13",
            platform="darwin",
            deferred_init=False,
        ),
        properties=m.ServerLifecycleProperties(),
    )
    payload = event.to_payload()

    assert payload["schema_version"] == m.TELEMETRY_SCHEMA_VERSION
    # distinct_id is the transport's addressing field, not a property.
    assert "distinct_id" not in payload
    assert "kind" not in payload
    assert set(payload) <= set(m.EXPECTED_PROPERTY_NAMES)
