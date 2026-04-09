"""Tests for View.append_event."""

from openhands.sdk.context.view import View
from openhands.sdk.event.condenser import (
    Condensation,
    CondensationRequest,
    CondensationSummaryEvent,
)
from openhands.sdk.event.conversation_state import ConversationStateUpdateEvent
from tests.sdk.context.view.conftest import (
    create_action_event,
    create_observation_event,
    message_event,
)


# --- LLMConvertibleEvent branch ---


class TestAppendLLMConvertibleEvent:
    def test_append_message_event_to_empty_view(self) -> None:
        view = View()
        msg = message_event("hello")
        view.append_event(msg)

        assert len(view) == 1
        assert view.events[0] is msg

    def test_append_multiple_message_events(self) -> None:
        view = View()
        msgs = [message_event(f"msg {i}") for i in range(3)]
        for msg in msgs:
            view.append_event(msg)

        assert len(view) == 3
        assert view.events == msgs

    def test_append_action_event(self) -> None:
        view = View()
        action = create_action_event(
            llm_response_id="resp_1", tool_call_id="tc_1", thinking="think"
        )
        view.append_event(action)

        assert len(view) == 1
        assert view.events[0] is action

    def test_append_observation_event(self) -> None:
        view = View()
        obs = create_observation_event(tool_call_id="tc_1")
        view.append_event(obs)

        assert len(view) == 1
        assert view.events[0] is obs

    def test_append_does_not_change_unhandled_flag(self) -> None:
        view = View()
        view.append_event(message_event("hello"))

        assert view.unhandled_condensation_request is False


# --- Condensation branch ---


class TestAppendCondensation:
    def test_condensation_forgets_events(self) -> None:
        view = View()
        msgs = [message_event(f"msg {i}") for i in range(3)]
        for msg in msgs:
            view.append_event(msg)

        condensation = Condensation(
            forgotten_event_ids=[msgs[0].id, msgs[2].id],
            llm_response_id="resp_1",
        )
        view.append_event(condensation)

        assert len(view) == 1
        assert view.events[0] is msgs[1]

    def test_condensation_forgets_all_events(self) -> None:
        view = View()
        msgs = [message_event(f"msg {i}") for i in range(3)]
        for msg in msgs:
            view.append_event(msg)

        condensation = Condensation(
            forgotten_event_ids=[m.id for m in msgs],
            llm_response_id="resp_1",
        )
        view.append_event(condensation)

        assert len(view) == 0
        assert view.events == []

    def test_condensation_on_empty_view(self) -> None:
        view = View()
        condensation = Condensation(
            forgotten_event_ids=[],
            llm_response_id="resp_1",
        )
        view.append_event(condensation)

        assert len(view) == 0

    def test_condensation_with_no_forgotten_ids(self) -> None:
        view = View()
        msgs = [message_event(f"msg {i}") for i in range(2)]
        for msg in msgs:
            view.append_event(msg)

        condensation = Condensation(
            forgotten_event_ids=[],
            llm_response_id="resp_1",
        )
        view.append_event(condensation)

        assert len(view) == 2
        assert view.events == msgs

    def test_condensation_inserts_summary(self) -> None:
        view = View()
        msgs = [message_event(f"msg {i}") for i in range(3)]
        for msg in msgs:
            view.append_event(msg)

        condensation = Condensation(
            forgotten_event_ids=[msgs[0].id],
            summary="Summary of msg 0",
            summary_offset=0,
            llm_response_id="resp_1",
        )
        view.append_event(condensation)

        assert len(view) == 3  # 2 remaining + 1 summary
        assert isinstance(view.events[0], CondensationSummaryEvent)
        assert view.events[0].summary == "Summary of msg 0"
        assert view.events[1] is msgs[1]
        assert view.events[2] is msgs[2]

    def test_condensation_inserts_summary_at_end(self) -> None:
        view = View()
        msgs = [message_event(f"msg {i}") for i in range(2)]
        for msg in msgs:
            view.append_event(msg)

        condensation = Condensation(
            forgotten_event_ids=[],
            summary="End summary",
            summary_offset=2,
            llm_response_id="resp_1",
        )
        view.append_event(condensation)

        assert len(view) == 3
        assert view.events[0] is msgs[0]
        assert view.events[1] is msgs[1]
        assert isinstance(view.events[2], CondensationSummaryEvent)
        assert view.events[2].summary == "End summary"

    def test_condensation_clears_unhandled_flag(self) -> None:
        view = View()
        view.append_event(message_event("msg"))
        view.append_event(CondensationRequest())

        assert view.unhandled_condensation_request is True

        view.append_event(
            Condensation(forgotten_event_ids=[], llm_response_id="resp_1")
        )

        assert view.unhandled_condensation_request is False

    def test_condensation_clears_flag_even_without_prior_request(self) -> None:
        view = View()
        view.append_event(message_event("msg"))

        assert view.unhandled_condensation_request is False

        view.append_event(
            Condensation(forgotten_event_ids=[], llm_response_id="resp_1")
        )

        assert view.unhandled_condensation_request is False

    def test_condensation_not_added_to_events(self) -> None:
        view = View()
        view.append_event(message_event("msg"))
        view.append_event(
            Condensation(forgotten_event_ids=[], llm_response_id="resp_1")
        )

        for event in view.events:
            assert not isinstance(event, Condensation)


# --- CondensationRequest branch ---


class TestAppendCondensationRequest:
    def test_sets_unhandled_flag(self) -> None:
        view = View()
        view.append_event(CondensationRequest())

        assert view.unhandled_condensation_request is True

    def test_not_added_to_events(self) -> None:
        view = View()
        view.append_event(message_event("msg"))
        view.append_event(CondensationRequest())

        assert len(view) == 1
        for event in view.events:
            assert not isinstance(event, CondensationRequest)

    def test_multiple_requests_keep_flag_true(self) -> None:
        view = View()
        view.append_event(CondensationRequest())
        view.append_event(CondensationRequest())

        assert view.unhandled_condensation_request is True
        assert len(view) == 0


# --- Default (non-LLMConvertible, non-condensation) branch ---


class TestAppendNonLLMConvertibleEvent:
    def test_skipped_silently(self) -> None:
        view = View()
        view.append_event(message_event("msg"))
        view.append_event(ConversationStateUpdateEvent(key="k", value="v"))

        assert len(view) == 1

    def test_does_not_affect_unhandled_flag(self) -> None:
        view = View()
        view.append_event(ConversationStateUpdateEvent(key="k", value="v"))

        assert view.unhandled_condensation_request is False

    def test_events_unchanged_after_skip(self) -> None:
        view = View()
        msgs = [message_event(f"msg {i}") for i in range(2)]
        for msg in msgs:
            view.append_event(msg)

        view.append_event(ConversationStateUpdateEvent(key="k", value="v"))

        assert view.events == msgs


# --- Interaction sequences ---


class TestAppendEventInteractions:
    def test_request_then_condensation_clears_flag(self) -> None:
        view = View()
        view.append_event(message_event("msg 0"))
        view.append_event(CondensationRequest())

        assert view.unhandled_condensation_request is True

        view.append_event(
            Condensation(forgotten_event_ids=[], llm_response_id="resp_1")
        )

        assert view.unhandled_condensation_request is False

    def test_condensation_then_request_sets_flag(self) -> None:
        view = View()
        view.append_event(message_event("msg 0"))
        view.append_event(
            Condensation(forgotten_event_ids=[], llm_response_id="resp_1")
        )

        assert view.unhandled_condensation_request is False

        view.append_event(CondensationRequest())

        assert view.unhandled_condensation_request is True

    def test_multiple_condensations_in_sequence(self) -> None:
        view = View()
        msgs = [message_event(f"msg {i}") for i in range(4)]
        for msg in msgs:
            view.append_event(msg)

        view.append_event(
            Condensation(
                forgotten_event_ids=[msgs[0].id, msgs[1].id],
                llm_response_id="resp_1",
            )
        )
        assert len(view) == 2
        assert view.events == [msgs[2], msgs[3]]

        view.append_event(
            Condensation(
                forgotten_event_ids=[msgs[2].id],
                llm_response_id="resp_2",
            )
        )
        assert len(view) == 1
        assert view.events == [msgs[3]]

    def test_interleaved_messages_and_condensations(self) -> None:
        view = View()
        msg0 = message_event("msg 0")
        msg1 = message_event("msg 1")

        view.append_event(msg0)
        view.append_event(
            Condensation(
                forgotten_event_ids=[msg0.id],
                summary="Summary of msg 0",
                summary_offset=0,
                llm_response_id="resp_1",
            )
        )
        view.append_event(msg1)

        assert len(view) == 2
        assert isinstance(view.events[0], CondensationSummaryEvent)
        assert view.events[1] is msg1

    def test_non_llm_events_interspersed(self) -> None:
        """Non-LLMConvertible events mixed in don't affect the view."""
        view = View()
        msg0 = message_event("msg 0")
        msg1 = message_event("msg 1")

        view.append_event(msg0)
        view.append_event(ConversationStateUpdateEvent(key="k", value="v"))
        view.append_event(msg1)
        view.append_event(ConversationStateUpdateEvent(key="k2", value="v2"))

        assert len(view) == 2
        assert view.events == [msg0, msg1]

    def test_full_lifecycle(self) -> None:
        """Simulate a realistic sequence: messages, request, condensation, more
        messages.
        """
        view = View()

        # Initial messages
        msgs = [message_event(f"msg {i}") for i in range(3)]
        for msg in msgs:
            view.append_event(msg)
        assert len(view) == 3
        assert view.unhandled_condensation_request is False

        # Request condensation
        view.append_event(CondensationRequest())
        assert view.unhandled_condensation_request is True
        assert len(view) == 3  # request not in events

        # Condensation handles the request
        view.append_event(
            Condensation(
                forgotten_event_ids=[msgs[0].id, msgs[1].id],
                summary="Summary of early messages",
                summary_offset=0,
                llm_response_id="resp_1",
            )
        )
        assert view.unhandled_condensation_request is False
        assert len(view) == 2  # summary + msgs[2]
        assert isinstance(view.events[0], CondensationSummaryEvent)
        assert view.events[1] is msgs[2]

        # More messages after condensation
        msg3 = message_event("msg 3")
        view.append_event(msg3)
        assert len(view) == 3
        assert view.events[2] is msg3
