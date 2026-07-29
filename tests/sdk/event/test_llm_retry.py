from openhands.sdk.event import Event, LLMRetryEvent


def test_llm_retry_event_round_trip_and_visualization() -> None:
    event = LLMRetryEvent(
        attempt=1,
        max_attempts=5,
        error_type="InternalServerError",
        detail="Connection error",
    )

    dumped = event.model_dump(mode="json")
    assert dumped["kind"] == "LLMRetryEvent"
    restored = Event.model_validate(dumped)
    assert restored == event
    rendered = str(event.visualize)
    assert "1/5" in rendered
    assert "Connection error" in rendered
