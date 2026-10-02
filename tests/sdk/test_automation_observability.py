from __future__ import annotations

import json

from openhands.sdk.automation import (
    automation_conversation_kwargs,
    automation_conversation_tags,
    automation_observability_context,
    automation_observability_headers,
)


def test_automation_observability_context_collects_generic_env(monkeypatch):
    monkeypatch.setenv(
        "OPENHANDS_OBSERVABILITY_METADATA",
        '{"automation.id":"auto-1","automation.run_id":"run-1"}',
    )
    monkeypatch.setenv("OPENHANDS_OBSERVABILITY_TAGS", "automation, manual")
    monkeypatch.setenv("OPENHANDS_OBSERVABILITY_SPAN_NAME", "automation.conversation")
    monkeypatch.setenv("OPENHANDS_OBSERVABILITY_PARENT_SPAN_CONTEXT", "parent-context")

    context = automation_observability_context(
        metadata={"automation.subject.key": "repo#123"}, tags=["candidate"]
    )

    assert context.get("observability_metadata") == {
        "automation.id": "auto-1",
        "automation.run_id": "run-1",
        "automation.subject.key": "repo#123",
    }
    assert context.get("observability_tags") == ["automation", "manual", "candidate"]
    assert context.get("observability_span_name") == "automation.conversation"
    assert context.get("observability_parent_span_context") == "parent-context"


def test_automation_conversation_tags_from_metadata(monkeypatch):
    monkeypatch.setenv(
        "OPENHANDS_OBSERVABILITY_METADATA",
        json.dumps(
            {
                "automation.id": "auto-1",
                "automation.name": "Trace me",
                "automation.run_id": "run-1",
                "automation.trigger_source": "cron",
            }
        ),
    )

    tags = automation_conversation_tags()

    assert tags == {
        "automationtrigger": "automation",
        "automationid": "auto-1",
        "automationname": "Trace me",
        "automationrunid": "run-1",
    }


def test_automation_conversation_kwargs_include_product_and_observability_tags(
    monkeypatch,
):
    monkeypatch.setenv(
        "OPENHANDS_OBSERVABILITY_METADATA",
        '{"automation.id":"auto-1","automation.run_id":"run-1"}',
    )
    monkeypatch.setenv("OPENHANDS_OBSERVABILITY_TAGS", "automation")

    kwargs = automation_conversation_kwargs(
        metadata={"automation.lookup.github.issue": "OpenHands/automation#543"},
        observability_tags=["polling"],
        conversation_tags={"custom": "tag"},
    )

    assert kwargs.get("tags") == {
        "automationtrigger": "automation",
        "automationid": "auto-1",
        "automationrunid": "run-1",
        "custom": "tag",
    }
    observability_metadata = kwargs.get("observability_metadata")
    assert observability_metadata is not None
    assert observability_metadata["automation.lookup.github.issue"] == (
        "OpenHands/automation#543"
    )
    assert kwargs.get("observability_tags") == ["automation", "polling"]
    assert kwargs.get("observability_span_name") == "automation.conversation"


def test_automation_observability_headers_for_direct_api_calls(monkeypatch):
    monkeypatch.setenv(
        "OPENHANDS_OBSERVABILITY_METADATA", '{"automation.run_id":"run-1"}'
    )
    monkeypatch.setenv("OPENHANDS_OBSERVABILITY_PARENT_SPAN_CONTEXT", "parent-context")

    headers = automation_observability_headers(tags=["automation"])

    assert headers["X-OpenHands-Observability-Metadata"] == (
        '{"automation.run_id":"run-1"}'
    )
    assert headers["X-OpenHands-Observability-Tags"] == "automation"
    assert headers["X-OpenHands-Observability-Parent-Span-Context"] == "parent-context"
