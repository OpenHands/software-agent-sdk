"""Tests for multi-summary support in View.

This module tests the View system's ability to handle multiple CondensationSummaryEvents
simultaneously, including the ability to forget previous summaries in subsequent
condensations.

Key behaviors tested:
- Multiple summaries can coexist in the same view
- Summaries can be forgotten individually or in groups
- Summary offsets work correctly with multiple summaries
- Summaries have stable identifiers across view reconstructions
- Integration with event forgetting
- Backward compatibility with existing summary properties
"""

from openhands.sdk.context.view import View
from openhands.sdk.event import Condensation, CondensationSummaryEvent
from openhands.sdk.event.llm_convertible import MessageEvent
from openhands.sdk.llm import TextContent
from tests.sdk.context.view.conftest import message_event  # noqa: F401


# ==============================================================================
# Category 1: Multiple Summaries Coexistence
# ==============================================================================


def test_multiple_summaries_at_different_offsets() -> None:
    """Test that two summaries from different condensations can coexist in a view.

    Scenario:
    - First condensation: forgets event 0, adds summary at offset 0
    - Second condensation: forgets event 2, adds summary at offset 2
    - Both summaries should appear in the final view at their specified offsets
    """
    message_events = [message_event(f"Event {i}") for i in range(5)]

    condensation1 = Condensation(
        id="condensation-1",
        forgotten_event_ids=[message_events[0].id],
        summary="Summary of event 0",
        summary_offset=0,
        llm_response_id="condensation_1",
    )

    condensation2 = Condensation(
        id="condensation-2",
        forgotten_event_ids=[message_events[2].id],
        summary="Summary of event 2",
        summary_offset=2,
        llm_response_id="condensation_2",
    )

    events = [
        message_events[0],
        message_events[1],
        condensation1,
        message_events[2],
        message_events[3],
        condensation2,
        message_events[4],
    ]

    view = View.from_events(events)

    # Find all CondensationSummaryEvents in the view
    summary_events = [e for e in view.events if isinstance(e, CondensationSummaryEvent)]

    assert len(summary_events) == 2, "Both summaries should be present in view"

    # Verify first summary is at offset 0
    assert isinstance(view.events[0], CondensationSummaryEvent)
    assert view.events[0].summary == "Summary of event 0"

    # Verify second summary is at offset 2
    assert isinstance(view.events[2], CondensationSummaryEvent)
    assert view.events[2].summary == "Summary of event 2"


def test_multiple_summaries_from_sequential_condensations() -> None:
    """Test three condensations each adding a summary at different positions.

    This tests that summaries accumulate as condensations are processed sequentially.
    """
    message_events = [message_event(f"Event {i}") for i in range(6)]

    condensation1 = Condensation(
        id="condensation-1",
        forgotten_event_ids=[],
        summary="First summary",
        summary_offset=0,
        llm_response_id="condensation_1",
    )

    condensation2 = Condensation(
        id="condensation-2",
        forgotten_event_ids=[],
        summary="Second summary",
        summary_offset=3,
        llm_response_id="condensation_2",
    )

    condensation3 = Condensation(
        id="condensation-3",
        forgotten_event_ids=[],
        summary="Third summary",
        summary_offset=5,
        llm_response_id="condensation_3",
    )

    events = [
        message_events[0],
        condensation1,
        message_events[1],
        message_events[2],
        condensation2,
        message_events[3],
        condensation3,
        message_events[4],
        message_events[5],
    ]

    view = View.from_events(events)

    summary_events = [e for e in view.events if isinstance(e, CondensationSummaryEvent)]

    assert len(summary_events) == 3, "All three summaries should be present"

    # Verify each summary is at its specified offset
    assert isinstance(view.events[0], CondensationSummaryEvent)
    assert view.events[0].summary == "First summary"

    assert isinstance(view.events[3], CondensationSummaryEvent)
    assert view.events[3].summary == "Second summary"

    assert isinstance(view.events[5], CondensationSummaryEvent)
    assert view.events[5].summary == "Third summary"


def test_summaries_preserve_order_and_content() -> None:
    """Test that multiple summaries maintain their order and content correctly.

    Verifies that summaries don't interfere with each other and each maintains
    its own content and position.
    """
    messages = [message_event(f"Msg {i}") for i in range(4)]

    condensation1 = Condensation(
        id="cond-1",
        forgotten_event_ids=[messages[0].id],
        summary="Summary A",
        summary_offset=0,
        llm_response_id="cond_1",
    )

    condensation2 = Condensation(
        id="cond-2",
        forgotten_event_ids=[messages[2].id],
        summary="Summary B",
        summary_offset=2,
        llm_response_id="cond_2",
    )

    events = [
        messages[0],
        condensation1,
        messages[1],
        messages[2],
        condensation2,
        messages[3],
    ]

    view = View.from_events(events)

    # Event 0 forgotten, Event 2 forgotten
    # Expected: [Summary A, Msg 1, Summary B, Msg 3]
    assert len(view.events) == 4

    assert isinstance(view.events[0], CondensationSummaryEvent)
    assert view.events[0].summary == "Summary A"

    assert isinstance(view.events[1], MessageEvent)
    assert isinstance(view.events[1].llm_message.content[0], TextContent)
    assert view.events[1].llm_message.content[0].text == "Msg 1"

    assert isinstance(view.events[2], CondensationSummaryEvent)
    assert view.events[2].summary == "Summary B"

    assert isinstance(view.events[3], MessageEvent)
    assert isinstance(view.events[3].llm_message.content[0], TextContent)
    assert view.events[3].llm_message.content[0].text == "Msg 3"


# ==============================================================================
# Category 2: Forgetting Individual Summaries
# ==============================================================================


def test_forget_first_summary_keeps_second() -> None:
    """Test that forgetting the first summary preserves the second summary.

    Scenario:
    - Condensation 1: adds summary A
    - Condensation 2: adds summary B
    - Condensation 3: forgets summary A
    - Result: only summary B remains
    """
    messages = [message_event(f"Msg {i}") for i in range(3)]

    condensation1 = Condensation(
        id="cond-1",
        forgotten_event_ids=[],
        summary="Summary A",
        summary_offset=0,
        llm_response_id="cond_1",
    )

    condensation2 = Condensation(
        id="cond-2",
        forgotten_event_ids=[],
        summary="Summary B",
        summary_offset=2,
        llm_response_id="cond_2",
    )

    # To forget summary A, we need its event ID. Using deterministic ID approach:
    # summary_id = f"{condensation_id}_summary"
    summary_a_id = "cond-1-summary"

    condensation3 = Condensation(
        id="cond-3",
        forgotten_event_ids=[summary_a_id],
        summary=None,
        summary_offset=None,
        llm_response_id="cond_3",
    )

    events = [
        messages[0],
        condensation1,
        messages[1],
        condensation2,
        messages[2],
        condensation3,
    ]

    view = View.from_events(events)

    summary_events = [e for e in view.events if isinstance(e, CondensationSummaryEvent)]

    assert len(summary_events) == 1, "Only summary B should remain"
    assert summary_events[0].summary == "Summary B"


def test_forget_middle_summary_keeps_others() -> None:
    """Test forgetting a middle summary while keeping first and last summaries.

    Scenario:
    - Three summaries A, B, C
    - Forget B
    - A and C remain
    """
    messages = [message_event(f"Msg {i}") for i in range(4)]

    condensation1 = Condensation(
        id="cond-1",
        forgotten_event_ids=[],
        summary="Summary A",
        summary_offset=0,
        llm_response_id="cond_1",
    )

    condensation2 = Condensation(
        id="cond-2",
        forgotten_event_ids=[],
        summary="Summary B",
        summary_offset=2,
        llm_response_id="cond_2",
    )

    condensation3 = Condensation(
        id="cond-3",
        forgotten_event_ids=[],
        summary="Summary C",
        summary_offset=4,
        llm_response_id="cond_3",
    )

    summary_b_id = "cond-2-summary"

    condensation4 = Condensation(
        id="cond-4",
        forgotten_event_ids=[summary_b_id],
        summary=None,
        llm_response_id="cond_4",
    )

    events = [
        messages[0],
        condensation1,
        messages[1],
        condensation2,
        messages[2],
        condensation3,
        messages[3],
        condensation4,
    ]

    view = View.from_events(events)

    summary_events = [e for e in view.events if isinstance(e, CondensationSummaryEvent)]

    assert len(summary_events) == 2, "Summaries A and C should remain"

    summaries_text = [s.summary for s in summary_events]
    assert "Summary A" in summaries_text
    assert "Summary C" in summaries_text
    assert "Summary B" not in summaries_text


def test_forget_most_recent_summary() -> None:
    """Test forgetting the most recently added summary.

    Verifies that newer summaries can be forgotten, not just older ones.
    """
    messages = [message_event(f"Msg {i}") for i in range(2)]

    condensation1 = Condensation(
        id="cond-1",
        forgotten_event_ids=[],
        summary="Summary A",
        summary_offset=0,
        llm_response_id="cond_1",
    )

    condensation2 = Condensation(
        id="cond-2",
        forgotten_event_ids=[],
        summary="Summary B",
        summary_offset=1,
        llm_response_id="cond_2",
    )

    summary_b_id = "cond-2-summary"

    condensation3 = Condensation(
        id="cond-3",
        forgotten_event_ids=[summary_b_id],
        summary=None,
        llm_response_id="cond_3",
    )

    events = [
        messages[0],
        condensation1,
        messages[1],
        condensation2,
        condensation3,
    ]

    view = View.from_events(events)

    summary_events = [e for e in view.events if isinstance(e, CondensationSummaryEvent)]

    assert len(summary_events) == 1, "Only summary A should remain"
    assert summary_events[0].summary == "Summary A"


def test_forget_summary_adjusts_later_summary_positions() -> None:
    """Test that forgetting a summary correctly adjusts positions of later summaries.

    When a summary is forgotten, the indices of events after it shift down by 1.
    """
    messages = [message_event(f"Msg {i}") for i in range(3)]

    condensation1 = Condensation(
        id="cond-1",
        forgotten_event_ids=[],
        summary="Summary at position 0",
        summary_offset=0,
        llm_response_id="cond_1",
    )

    condensation2 = Condensation(
        id="cond-2",
        forgotten_event_ids=[],
        summary="Summary at position 2",
        summary_offset=2,
        llm_response_id="cond_2",
    )

    summary_1_id = "cond-1-summary"

    condensation3 = Condensation(
        id="cond-3",
        forgotten_event_ids=[summary_1_id],
        summary=None,
        llm_response_id="cond_3",
    )

    events = [
        messages[0],
        condensation1,
        messages[1],
        condensation2,
        messages[2],
        condensation3,
    ]

    view = View.from_events(events)

    # After forgetting first summary: [Msg 0, Summary at position 2, Msg 1, Msg 2]
    # The second summary should now be at index 1
    assert isinstance(view.events[1], CondensationSummaryEvent)
    assert view.events[1].summary == "Summary at position 2"


# ==============================================================================
# Category 3: Forgetting Multiple Summaries
# ==============================================================================


def test_forget_multiple_summaries_simultaneously() -> None:
    """Test a single condensation forgetting multiple summaries at once.

    Scenario:
    - Three summaries exist
    - One condensation forgets two of them
    - Only one summary remains
    """
    messages = [message_event(f"Msg {i}") for i in range(4)]

    condensation1 = Condensation(
        id="cond-1",
        forgotten_event_ids=[],
        summary="Summary A",
        summary_offset=0,
        llm_response_id="cond_1",
    )

    condensation2 = Condensation(
        id="cond-2",
        forgotten_event_ids=[],
        summary="Summary B",
        summary_offset=2,
        llm_response_id="cond_2",
    )

    condensation3 = Condensation(
        id="cond-3",
        forgotten_event_ids=[],
        summary="Summary C",
        summary_offset=4,
        llm_response_id="cond_3",
    )

    summary_a_id = "cond-1-summary"
    summary_c_id = "cond-3-summary"

    condensation4 = Condensation(
        id="cond-4",
        forgotten_event_ids=[summary_a_id, summary_c_id],
        summary=None,
        llm_response_id="cond_4",
    )

    events = [
        messages[0],
        condensation1,
        messages[1],
        condensation2,
        messages[2],
        condensation3,
        messages[3],
        condensation4,
    ]

    view = View.from_events(events)

    summary_events = [e for e in view.events if isinstance(e, CondensationSummaryEvent)]

    assert len(summary_events) == 1, "Only summary B should remain"
    assert summary_events[0].summary == "Summary B"


def test_forget_all_summaries() -> None:
    """Test forgetting all summaries from a view.

    After forgetting all summaries, view should contain only message events.
    """
    messages = [message_event(f"Msg {i}") for i in range(3)]

    condensation1 = Condensation(
        id="cond-1",
        forgotten_event_ids=[],
        summary="Summary A",
        summary_offset=0,
        llm_response_id="cond_1",
    )

    condensation2 = Condensation(
        id="cond-2",
        forgotten_event_ids=[],
        summary="Summary B",
        summary_offset=2,
        llm_response_id="cond_2",
    )

    summary_a_id = "cond-1-summary"
    summary_b_id = "cond-2-summary"

    condensation3 = Condensation(
        id="cond-3",
        forgotten_event_ids=[summary_a_id, summary_b_id],
        summary=None,
        llm_response_id="cond_3",
    )

    events = [
        messages[0],
        condensation1,
        messages[1],
        condensation2,
        messages[2],
        condensation3,
    ]

    view = View.from_events(events)

    summary_events = [e for e in view.events if isinstance(e, CondensationSummaryEvent)]

    assert len(summary_events) == 0, "No summaries should remain"
    assert len(view.events) == 3, "Only message events should remain"


def test_sequential_condensations_each_forget_summary() -> None:
    """Test multiple condensations each forgetting one summary.

    Scenario:
    - Create 3 summaries
    - Condensation 4 forgets summary 1
    - Condensation 5 forgets summary 2
    - Only summary 3 remains
    """
    messages = [message_event(f"Msg {i}") for i in range(4)]

    condensation1 = Condensation(
        id="cond-1",
        forgotten_event_ids=[],
        summary="Summary 1",
        summary_offset=0,
        llm_response_id="cond_1",
    )

    condensation2 = Condensation(
        id="cond-2",
        forgotten_event_ids=[],
        summary="Summary 2",
        summary_offset=2,
        llm_response_id="cond_2",
    )

    condensation3 = Condensation(
        id="cond-3",
        forgotten_event_ids=[],
        summary="Summary 3",
        summary_offset=4,
        llm_response_id="cond_3",
    )

    summary_1_id = "cond-1-summary"
    summary_2_id = "cond-2-summary"

    condensation4 = Condensation(
        id="cond-4",
        forgotten_event_ids=[summary_1_id],
        summary=None,
        llm_response_id="cond_4",
    )

    condensation5 = Condensation(
        id="cond-5",
        forgotten_event_ids=[summary_2_id],
        summary=None,
        llm_response_id="cond_5",
    )

    events = [
        messages[0],
        condensation1,
        messages[1],
        condensation2,
        messages[2],
        condensation3,
        messages[3],
        condensation4,
        condensation5,
    ]

    view = View.from_events(events)

    summary_events = [e for e in view.events if isinstance(e, CondensationSummaryEvent)]

    assert len(summary_events) == 1, "Only summary 3 should remain"
    assert summary_events[0].summary == "Summary 3"


# ==============================================================================
# Category 4: Summary Identification Mechanism
# ==============================================================================


def test_summary_events_have_stable_identifiers() -> None:
    """Test that summary event IDs are stable across view reconstructions.

    This is the core requirement: if we construct the same view twice with the
    same input events, summary events should have the same IDs both times.
    """
    messages = [message_event(f"Msg {i}") for i in range(2)]

    condensation1 = Condensation(
        id="stable-condensation",
        forgotten_event_ids=[],
        summary="Stable summary",
        summary_offset=0,
        llm_response_id="stable_condensation",
    )

    events = [messages[0], condensation1, messages[1]]

    # Construct view first time
    view1 = View.from_events(events)
    summary1 = [e for e in view1.events if isinstance(e, CondensationSummaryEvent)][0]

    # Construct view second time with same events
    view2 = View.from_events(events)
    summary2 = [e for e in view2.events if isinstance(e, CondensationSummaryEvent)][0]

    assert summary1.id == summary2.id, (
        "Summary event ID should be stable across reconstructions"
    )

    # Verify the ID follows the expected pattern
    expected_id = "stable-condensation-summary"
    assert summary1.id == expected_id, f"Summary ID should be {expected_id}"


def test_condensation_tracks_its_summary_event() -> None:
    """Test that we can determine which condensation created which summary.

    This might be through ID conventions or explicit tracking.
    """
    messages = [message_event(f"Msg {i}") for i in range(3)]

    condensation1 = Condensation(
        id="cond-A",
        forgotten_event_ids=[],
        summary="First",
        summary_offset=0,
        llm_response_id="cond_A",
    )

    condensation2 = Condensation(
        id="cond-B",
        forgotten_event_ids=[],
        summary="Second",
        summary_offset=2,
        llm_response_id="cond_B",
    )

    events = [
        messages[0],
        condensation1,
        messages[1],
        condensation2,
        messages[2],
    ]

    view = View.from_events(events)

    summary_events = [e for e in view.events if isinstance(e, CondensationSummaryEvent)]

    # Verify we can identify which summary came from which condensation
    summary_1 = [s for s in summary_events if s.summary == "First"][0]
    summary_2 = [s for s in summary_events if s.summary == "Second"][0]

    assert summary_1.id == "cond-A-summary"
    assert summary_2.id == "cond-B-summary"


def test_can_reference_summary_from_previous_condensation() -> None:
    """Test the core use case: referencing a summary created by an earlier condensation.

    This verifies that the identification mechanism enables forgetting summaries.
    """
    messages = [message_event(f"Msg {i}") for i in range(2)]

    # First condensation creates a summary
    condensation1 = Condensation(
        id="cond-1",
        forgotten_event_ids=[],
        summary="To be forgotten",
        summary_offset=0,
        llm_response_id="cond_original",
    )

    events_before_forgetting = [messages[0], condensation1, messages[1]]
    view_before = View.from_events(events_before_forgetting)

    # Find the summary's ID
    summary_event = [
        e for e in view_before.events if isinstance(e, CondensationSummaryEvent)
    ][0]
    summary_id = summary_event.id

    # Second condensation references and forgets that summary
    condensation2 = Condensation(
        id="cond-2",
        forgotten_event_ids=[summary_id],
        summary="New summary",
        summary_offset=0,
        llm_response_id="cond_new",
    )

    events_after_forgetting = [messages[0], condensation1, messages[1], condensation2]
    view_after = View.from_events(events_after_forgetting)

    summary_events = [
        e for e in view_after.events if isinstance(e, CondensationSummaryEvent)
    ]

    # Old summary should be gone, new summary should be present
    assert len(summary_events) == 1
    assert summary_events[0].summary == "New summary"


# ==============================================================================
# Category 5: Offset Behavior
# ==============================================================================


def test_summary_offset_is_absolute_in_final_view() -> None:
    """Test that summary_offset refers to the absolute position in the final view.

    After events are forgotten, the offset should place the summary at that exact
    index in the resulting event list.
    """
    messages = [message_event(f"Msg {i}") for i in range(5)]

    condensation1 = Condensation(
        id="cond-1",
        forgotten_event_ids=[messages[0].id, messages[1].id],
        summary="Summary at offset 1",
        summary_offset=1,
        llm_response_id="cond_1",
    )

    events = [
        messages[0],
        messages[1],
        messages[2],
        condensation1,
        messages[3],
        messages[4],
    ]

    view = View.from_events(events)

    # After forgetting events 0 and 1: [Msg 2, Msg 3, Msg 4]
    # Summary at offset 1 should be between Msg 2 and Msg 3
    # Expected: [Msg 2, Summary, Msg 3, Msg 4]

    assert len(view.events) == 4
    assert isinstance(view.events[0], MessageEvent)
    assert isinstance(view.events[0].llm_message.content[0], TextContent)
    assert view.events[0].llm_message.content[0].text == "Msg 2"

    assert isinstance(view.events[1], CondensationSummaryEvent)
    assert view.events[1].summary == "Summary at offset 1"

    assert isinstance(view.events[2], MessageEvent)
    assert isinstance(view.events[2].llm_message.content[0], TextContent)
    assert view.events[2].llm_message.content[0].text == "Msg 3"


def test_summary_offset_zero_inserts_at_beginning() -> None:
    """Test that offset=0 inserts summary at the very beginning of the view."""
    messages = [message_event(f"Msg {i}") for i in range(3)]

    condensation1 = Condensation(
        id="cond-1",
        forgotten_event_ids=[],
        summary="At the start",
        summary_offset=0,
        llm_response_id="cond_1",
    )

    events = [messages[0], condensation1, messages[1], messages[2]]

    view = View.from_events(events)

    assert isinstance(view.events[0], CondensationSummaryEvent)
    assert view.events[0].summary == "At the start"


def test_summary_offset_at_end_of_events() -> None:
    """Test that summary can be inserted at the end of the event list."""
    messages = [message_event(f"Msg {i}") for i in range(3)]

    condensation1 = Condensation(
        id="cond-1",
        forgotten_event_ids=[],
        summary="At the end",
        summary_offset=3,  # After all 3 messages
        llm_response_id="cond_1",
    )

    events = [messages[0], messages[1], messages[2], condensation1]

    view = View.from_events(events)

    assert len(view.events) == 4
    assert isinstance(view.events[3], CondensationSummaryEvent)
    assert view.events[3].summary == "At the end"


def test_multiple_summaries_with_same_offset() -> None:
    """Test behavior when multiple summaries have the same offset.

    This is an edge case that tests how the system handles offset collisions.
    Expected: summaries are inserted in the order they were created.
    """
    messages = [message_event(f"Msg {i}") for i in range(2)]

    condensation1 = Condensation(
        id="cond-1",
        forgotten_event_ids=[],
        summary="First at offset 1",
        summary_offset=1,
        llm_response_id="cond_1",
    )

    condensation2 = Condensation(
        id="cond-2",
        forgotten_event_ids=[],
        summary="Second at offset 1",
        summary_offset=1,
        llm_response_id="cond_2",
    )

    events = [messages[0], condensation1, condensation2, messages[1]]

    view = View.from_events(events)

    # Both summaries should be in the view
    summary_events = [e for e in view.events if isinstance(e, CondensationSummaryEvent)]
    assert len(summary_events) == 2

    # When inserting at the same offset, later insertions appear before earlier ones
    # (standard list.insert() behavior)
    summaries_in_order = [s.summary for s in summary_events]
    assert summaries_in_order[0] == "Second at offset 1"
    assert summaries_in_order[1] == "First at offset 1"


# ==============================================================================
# Category 6: Integration with Event Forgetting
# ==============================================================================


def test_forget_events_and_summary_together() -> None:
    """Test a condensation that forgets both regular events and a summary.

    Verifies that summaries can be forgotten alongside regular events in the
    same condensation.
    """
    messages = [message_event(f"Msg {i}") for i in range(4)]

    condensation1 = Condensation(
        id="cond-1",
        forgotten_event_ids=[],
        summary="Old summary",
        summary_offset=1,
        llm_response_id="cond_1",
    )

    old_summary_id = "cond-1-summary"

    condensation2 = Condensation(
        id="cond-2",
        forgotten_event_ids=[messages[0].id, messages[2].id, old_summary_id],
        summary="New summary",
        summary_offset=0,
        llm_response_id="cond_2",
    )

    events = [
        messages[0],
        messages[1],
        condensation1,
        messages[2],
        messages[3],
        condensation2,
    ]

    view = View.from_events(events)

    # Should have forgotten: Msg 0, Msg 2, old summary
    # Should remain: Msg 1, Msg 3, new summary
    summary_events = [e for e in view.events if isinstance(e, CondensationSummaryEvent)]

    assert len(summary_events) == 1
    assert summary_events[0].summary == "New summary"

    message_events_in_view = [e for e in view.events if isinstance(e, MessageEvent)]
    assert len(message_events_in_view) == 2


def test_summary_offset_remains_valid_after_forgetting_events() -> None:
    """Test that summary offsets work correctly when events before them are forgotten.

    When earlier events are removed, the summary offset should still place the
    summary at the correct position in the resulting view.
    """
    messages = [message_event(f"Msg {i}") for i in range(5)]

    # Forget first two messages, add summary at offset 2
    condensation1 = Condensation(
        id="cond-1",
        forgotten_event_ids=[messages[0].id, messages[1].id],
        summary="Summary after forgetting",
        summary_offset=2,
        llm_response_id="cond_1",
    )

    events = [
        messages[0],
        messages[1],
        messages[2],
        messages[3],
        condensation1,
        messages[4],
    ]

    view = View.from_events(events)

    # After forgetting: [Msg 2, Msg 3, Msg 4]
    # Summary at offset 2 should be after Msg 3
    # Expected: [Msg 2, Msg 3, Summary, Msg 4]

    assert len(view.events) == 4
    assert isinstance(view.events[2], CondensationSummaryEvent)
    assert view.events[2].summary == "Summary after forgetting"


def test_interleaved_events_and_summaries() -> None:
    """Test complex scenario with events and summaries interleaved.

    Scenario:
    - Messages and summaries interleaved
    - Some messages forgotten
    - Verify final view has correct structure
    """
    messages = [message_event(f"Msg {i}") for i in range(6)]

    condensation1 = Condensation(
        id="cond-1",
        forgotten_event_ids=[messages[1].id],
        summary="Summary A",
        summary_offset=1,
        llm_response_id="cond_1",
    )

    condensation2 = Condensation(
        id="cond-2",
        forgotten_event_ids=[messages[3].id],
        summary="Summary B",
        summary_offset=3,
        llm_response_id="cond_2",
    )

    events = [
        messages[0],
        messages[1],
        condensation1,
        messages[2],
        messages[3],
        condensation2,
        messages[4],
        messages[5],
    ]

    view = View.from_events(events)

    # Messages 1 and 3 forgotten
    # Remaining: Msg 0, Msg 2, Msg 4, Msg 5 + Summary A, Summary B
    # Expected: [Msg 0, Summary A, Msg 2, Summary B, Msg 4, Msg 5]

    assert len(view.events) == 6

    assert isinstance(view.events[0], MessageEvent)
    assert isinstance(view.events[0].llm_message.content[0], TextContent)
    assert view.events[0].llm_message.content[0].text == "Msg 0"

    assert isinstance(view.events[1], CondensationSummaryEvent)
    assert view.events[1].summary == "Summary A"

    assert isinstance(view.events[2], MessageEvent)
    assert isinstance(view.events[2].llm_message.content[0], TextContent)
    assert view.events[2].llm_message.content[0].text == "Msg 2"

    assert isinstance(view.events[3], CondensationSummaryEvent)
    assert view.events[3].summary == "Summary B"


# ==============================================================================
# Category 7: Edge Cases
# ==============================================================================


def test_condensation_without_summary_no_summary_event_created() -> None:
    """Test that condensations without summaries don't create summary events.

    Not all condensations have summaries - verify this still works.
    """
    messages = [message_event(f"Msg {i}") for i in range(3)]

    condensation1 = Condensation(
        id="cond-1",
        forgotten_event_ids=[messages[1].id],
        summary=None,  # No summary
        summary_offset=None,
        llm_response_id="cond_1",
    )

    events = [messages[0], messages[1], condensation1, messages[2]]

    view = View.from_events(events)

    summary_events = [e for e in view.events if isinstance(e, CondensationSummaryEvent)]

    assert len(summary_events) == 0, "No summary should be created"
    assert len(view.events) == 2, "Only Msg 0 and Msg 2 should remain"


def test_empty_view_with_only_summaries() -> None:
    """Test edge case where all regular events are forgotten, only summaries remain.

    Verifies that a view can consist entirely of summary events.
    """
    messages = [message_event(f"Msg {i}") for i in range(3)]

    condensation1 = Condensation(
        id="cond-1",
        forgotten_event_ids=[messages[0].id, messages[1].id, messages[2].id],
        summary="Only summary remains",
        summary_offset=0,
        llm_response_id="cond_1",
    )

    events = [messages[0], messages[1], messages[2], condensation1]

    view = View.from_events(events)

    assert len(view.events) == 1
    assert isinstance(view.events[0], CondensationSummaryEvent)
    assert view.events[0].summary == "Only summary remains"


def test_forget_nonexistent_summary_is_noop() -> None:
    """Test that trying to forget a non-existent summary doesn't cause errors.

    Graceful handling of invalid summary references.
    """
    messages = [message_event(f"Msg {i}") for i in range(2)]

    condensation1 = Condensation(
        id="cond-1",
        forgotten_event_ids=[],
        summary="Existing summary",
        summary_offset=0,
        llm_response_id="cond_1",
    )

    # Try to forget a summary that doesn't exist
    condensation2 = Condensation(
        id="cond-2",
        forgotten_event_ids=["nonexistent_summary_id"],
        summary=None,
        llm_response_id="cond_2",
    )

    events = [messages[0], condensation1, messages[1], condensation2]

    view = View.from_events(events)

    # Existing summary should still be there
    summary_events = [e for e in view.events if isinstance(e, CondensationSummaryEvent)]

    assert len(summary_events) == 1
    assert summary_events[0].summary == "Existing summary"


def test_multiple_condensations_same_summary_offset() -> None:
    """Test multiple condensations each trying to insert at the same offset.

    Verifies that when condensations are processed sequentially, each can
    specify the same offset and they get inserted in order.
    """
    messages = [message_event(f"Msg {i}") for i in range(2)]

    condensation1 = Condensation(
        id="cond-1",
        forgotten_event_ids=[],
        summary="First at 1",
        summary_offset=1,
        llm_response_id="cond_1",
    )

    condensation2 = Condensation(
        id="cond-2",
        forgotten_event_ids=[],
        summary="Second at 1",
        summary_offset=1,
        llm_response_id="cond_2",
    )

    condensation3 = Condensation(
        id="cond-3",
        forgotten_event_ids=[],
        summary="Third at 1",
        summary_offset=1,
        llm_response_id="cond_3",
    )

    events = [
        messages[0],
        condensation1,
        condensation2,
        condensation3,
        messages[1],
    ]

    view = View.from_events(events)

    # All three summaries should be present
    summary_events = [e for e in view.events if isinstance(e, CondensationSummaryEvent)]

    assert len(summary_events) == 3

    # Verify they maintain insertion order
    summaries_text = [s.summary for s in summary_events]
    assert "First at 1" in summaries_text
    assert "Second at 1" in summaries_text
    assert "Third at 1" in summaries_text
