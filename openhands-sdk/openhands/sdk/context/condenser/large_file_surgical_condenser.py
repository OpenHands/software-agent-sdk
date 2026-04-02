import os

from pydantic import Field

from openhands.sdk.context.condenser.base import (
    CondenserBase,
)
from openhands.sdk.context.view import View
from openhands.sdk.event.condenser import Condensation
from openhands.sdk.event.llm_convertible import ActionEvent, ObservationEvent
from openhands.sdk.llm import LLM, ImageContent, TextContent
from openhands.sdk.tool.schema import Observation


class LargeFileSurgicalCondenser(CondenserBase):
    """A highly specific condenser that removes bloated data from observations.

    This condenser intercepts observations (like ImageContent or large TextContent from
    the 'file_editor' tool) and replaces them with a small summary string.
    This preserves KV cache efficiency by keeping the prefix stable and forcing early
    eviction of large branches.

    :param target_tool: Only condense observations from this tool
        (default: 'file_editor').
    :param threshold_bytes: For TextContent, the byte size threshold to
        trigger condensation (default: 10KB).
    """

    threshold_bytes: int = Field(
        default=10240
    )  # 10KB default threshold for condensation
    target_tool: str = Field(default="file_editor")

    def _should_condense_event(self, event: ObservationEvent) -> bool:
        if event.tool_name != self.target_tool:
            return False

        current_bytes = 0
        for content in event.observation.content:
            # If ImageContent, directly trigger condensation.
            if isinstance(content, ImageContent):
                return True
            # If TextContent, check if it exceeds the byte threshold.
            elif isinstance(content, TextContent):
                current_bytes += len(content.text.encode("utf-8"))
                if current_bytes > self.threshold_bytes:
                    return True
        return False

    def _get_summary(self, view: View, event: ObservationEvent, index: int) -> str:
        """Determines the summary for the condensed observation."""
        size_kb = 0.0
        has_image = False
        for content in event.observation.content:
            if isinstance(content, ImageContent):
                has_image = True
            elif isinstance(content, TextContent):
                size_kb += len(content.text.encode("utf-8")) / 1024.0

        # Attempt to find the filename from the preceding ActionEvent
        filename = ""
        for i in range(index - 1, -1, -1):
            prev_event = view.events[i]
            if (
                isinstance(prev_event, ActionEvent)
                and prev_event.id == event.action_id
            ):
                # We found the matching action. Extract the path.
                path = getattr(prev_event.action, "path", None)
                if path:
                    filename = f"{os.path.basename(path)}"
                break

        if filename:
            return f"[Condensation]Viewed {filename}"
        else:
            file_info = "image/data" if has_image else f"data ({size_kb:.2f}KB)"
            return f"[Condensation]Viewed {file_info}"

    def condense(self, view: View, agent_llm: LLM | None = None) -> View | Condensation:
        """Surgically condenses bloated observations in the view."""
        new_events = list(view.events)
        modified = False

        for index, event in enumerate(view.events):
            if isinstance(event, ObservationEvent):
                # Ensure it's not the last event, so the agent has seen it.
                if index < len(view.events) - 1:
                    next_event = view.events[index + 1]
                    # Look ahead to see if the next event is from the agent.
                    if next_event.source == "agent":
                        if self._should_condense_event(event):
                            summary = self._get_summary(view, event, index)
                            # Create a new Observation with the summary.
                            # Use the same type as the original observation to avoid ABC issues.
                            new_observation = type(event.observation)(
                                content=[TextContent(text=summary)]
                            )
                            # Create a new ObservationEvent with the same IDs to preserve pairing.
                            new_event = ObservationEvent(
                                id=event.id,
                                timestamp=event.timestamp,
                                source=event.source,
                                tool_name=event.tool_name,
                                tool_call_id=event.tool_call_id,
                                action_id=event.action_id,
                                observation=new_observation,
                            )
                            new_events[index] = new_event
                            modified = True

        if modified:
            return View(
                events=new_events,
                unhandled_condensation_request=view.unhandled_condensation_request,
                condensations=view.condensations,
            )
        return view
