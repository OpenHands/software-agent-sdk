from openhands.sdk.context.condenser.large_file_surgical_condenser import (
    LargeFileSurgicalCondenser,
)
from openhands.sdk.context.view import View
from openhands.sdk.event.llm_convertible import (
    ActionEvent,
    MessageEvent,
    ObservationEvent,
)
from openhands.sdk.llm import ImageContent, Message, MessageToolCall, TextContent
from openhands.sdk.tool import (
    Action,
    Observation,
)


class DummyAction(Action):
    action_type: str = "dummy"


class DummyObservation(Observation):
    pass


def create_observation_event(
    tool_name: str, content: list[TextContent | ImageContent], id: str = "obs1"
) -> ObservationEvent:
    return ObservationEvent(
        id=id,
        source="environment",
        tool_name=tool_name,
        tool_call_id="call1",
        action_id="act1",
        observation=DummyObservation(content=content),
    )


def create_action_event(id: str = "act1") -> ActionEvent:
    return ActionEvent(
        id=id,
        source="agent",
        action=DummyAction(),
        thought=[TextContent(text="Thinking...")],
        llm_response_id="response_id_1",
        tool_call_id="call1",
        tool_name="dummy_tool",
        tool_call=MessageToolCall(
            id="call1", name="dummy_tool", arguments="{}", origin="completion"
        ),
    )


def create_agent_event(id: str = "msg2") -> MessageEvent:
    return MessageEvent(
        id=id,
        source="agent",
        llm_message=Message(
            role="assistant", content=[TextContent(text="Thinking...")]
        ),
    )


def test_no_condensation_if_wrong_tool():
    condenser = LargeFileSurgicalCondenser(threshold_bytes=100)
    act_event = create_action_event()
    obs_event = create_observation_event("other_tool", [TextContent(text="A" * 200)])
    msg_event = create_agent_event()
    view = View.from_events([act_event, obs_event, msg_event])

    result = condenser.condense(view)
    assert result == view


def test_no_condensation_if_size_below_threshold():
    condenser = LargeFileSurgicalCondenser(threshold_bytes=100)
    act_event = create_action_event()
    obs_event = create_observation_event("file_editor", [TextContent(text="A" * 50)])
    msg_event = create_agent_event()
    view = View.from_events([act_event, obs_event, msg_event])

    result = condenser.condense(view)
    assert result == view


def test_condensation_triggered_for_large_text():
    condenser = LargeFileSurgicalCondenser(threshold_bytes=100)
    act_event = create_action_event()
    obs_event = create_observation_event(
        "file_editor", [TextContent(text="A" * 200)], id="large_obs"
    )
    msg_event = create_agent_event()
    view = View.from_events([act_event, obs_event, msg_event])

    result = condenser.condense(view)
    assert isinstance(result, View)
    assert len(result.events) == 3
    
    condensed_obs = result.events[1]
    assert isinstance(condensed_obs, ObservationEvent)
    assert condensed_obs.id == "large_obs"
    assert "[Condensation]Viewed" in condensed_obs.observation.content[0].text
    assert "data (0.20KB)" in condensed_obs.observation.content[0].text
    
    # Verify pairing is preserved
    assert condensed_obs.tool_name == obs_event.tool_name
    assert condensed_obs.tool_call_id == obs_event.tool_call_id
    assert condensed_obs.action_id == obs_event.action_id


def test_condensation_triggered_for_image_content():
    condenser = LargeFileSurgicalCondenser()
    act_event = create_action_event()
    obs_event = create_observation_event(
        "file_editor",
        [ImageContent(image_urls=["data:image/png;base64,123"])],
        id="img_obs",
    )
    msg_event = create_agent_event()
    view = View.from_events([act_event, obs_event, msg_event])

    result = condenser.condense(view)
    assert isinstance(result, View)
    condensed_obs = result.events[1]
    assert isinstance(condensed_obs, ObservationEvent)
    assert condensed_obs.id == "img_obs"
    assert "image/data" in condensed_obs.observation.content[0].text


def test_no_condensation_if_last_event():
    # If the observation is the very last event, we shouldn't condense yet
    condenser = LargeFileSurgicalCondenser(threshold_bytes=100)
    act_event = create_action_event()
    obs_event = create_observation_event("file_editor", [TextContent(text="A" * 200)])
    view = View.from_events([act_event, obs_event])

    result = condenser.condense(view)
    assert result == view
