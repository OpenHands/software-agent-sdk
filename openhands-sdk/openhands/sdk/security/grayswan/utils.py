"""Utility for converting OpenHands SDK events to OpenAI message format.

This module provides functions to convert SDK events into the OpenAI message
format required by the GraySwan Cygnal API.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from openhands.sdk.event import (
    ActionEvent,
    LLMConvertibleEvent,
    MessageEvent,
    ObservationBaseEvent,
    ObservationEvent,
    SystemPromptEvent,
)
from openhands.sdk.llm import ImageContent, TextContent, content_to_str
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)


def convert_events_to_openai_messages(
    events: Sequence[LLMConvertibleEvent],
) -> list[dict[str, Any]]:
    """Convert OpenHands SDK events to OpenAI message format for LLM APIs.

    This function transforms SDK events into the message format expected by
    OpenAI-compatible APIs, which is required by the GraySwan Cygnal API.

    Args:
        events: List of LLMConvertibleEvent objects to convert

    Returns:
        List of dictionaries in OpenAI message format
    """
    openai_messages: list[dict[str, Any]] = []

    logger.debug(f"Converting {len(events)} events to OpenAI messages")

    for event in events:
        event_type = type(event).__name__

        # Handle system prompts
        if isinstance(event, SystemPromptEvent):
            msg = {"role": "system", "content": event.system_prompt.text}
            openai_messages.append(msg)

        # Handle message events (user/agent messages)
        elif isinstance(event, MessageEvent):
            source = event.source
            llm_message = event.to_llm_message()

            # Extract text content from the message
            content_parts = []
            for content in llm_message.content:
                if isinstance(content, TextContent):
                    content_parts.append(content.text)
                elif isinstance(content, ImageContent):
                    # Skip images for security analysis
                    logger.debug("Skipping image content in security analysis")
                    continue

            content_str = " ".join(content_parts)

            if source == "user":
                msg = {"role": "user", "content": content_str}
                openai_messages.append(msg)
            elif source == "agent":
                msg = {"role": "assistant", "content": content_str}
                openai_messages.append(msg)

        # Handle action events (tool calls from agent)
        elif isinstance(event, ActionEvent):
            # Build the tool call structure
            tool_call_dict = {
                "id": event.tool_call_id,
                "type": "function",
                "function": {
                    "name": event.tool_name,
                    "arguments": event.tool_call.arguments,
                },
            }

            # Remove security_risk from arguments to avoid biasing the analysis
            try:
                args = json.loads(event.tool_call.arguments)
                if "security_risk" in args:
                    del args["security_risk"]
                    tool_call_dict["function"]["arguments"] = json.dumps(args)
            except (json.JSONDecodeError, KeyError) as e:
                logger.debug(f"Could not remove security_risk from arguments: {e}")

            # Extract thought content
            thought_text = " ".join([t.text for t in event.thought])

            assistant_msg: dict[str, Any] = {
                "role": "assistant",
                "content": thought_text,
                "tool_calls": [tool_call_dict],
            }
            openai_messages.append(assistant_msg)

        # Handle observation events (tool responses)
        elif isinstance(event, ObservationEvent):
            tool_call_id = event.tool_call_id

            if tool_call_id:
                # Get content from observation
                content_parts = content_to_str(event.observation.to_llm_content)
                content_str = " ".join(content_parts)

                msg = {
                    "role": "tool",
                    "content": content_str,
                    "tool_call_id": tool_call_id,
                }
                openai_messages.append(msg)
            else:
                logger.warning(
                    f"Could not find tool_call_id for observation {event_type}"
                )

        # Handle other observation base events (errors, rejections)
        elif isinstance(event, ObservationBaseEvent):
            tool_call_id = event.tool_call_id

            if tool_call_id:
                # Get content from the event's LLM message
                llm_message = event.to_llm_message()
                content_parts = content_to_str(llm_message.content)
                content_str = " ".join(content_parts)

                msg = {
                    "role": "tool",
                    "content": content_str,
                    "tool_call_id": tool_call_id,
                }
                openai_messages.append(msg)
            else:
                logger.warning(
                    f"Could not find tool_call_id for observation {event_type}"
                )

    return openai_messages
