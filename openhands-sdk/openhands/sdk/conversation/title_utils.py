"""Utility functions for generating conversation titles."""

from collections.abc import Sequence

from openhands.sdk.event import MessageEvent
from openhands.sdk.event.base import Event
from openhands.sdk.llm import LLM, Message, TextContent
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)


categories = [
    {"emoji": "💄", "name": "frontend", "description": "UI and style files"},
    {"emoji": "👔", "name": "backend", "description": "Business logic"},
    {"emoji": "✅", "name": "test", "description": "Tests"},
    {"emoji": "👷", "name": "devops", "description": "CI build system"},
    {"emoji": "🚀", "name": "deployment", "description": "Deploy stuff"},
    {"emoji": "📦️", "name": "dependencies", "description": "Packages and dependencies"},
    {"emoji": "🗃️", "name": "database", "description": "Database changes"},
    {"emoji": "🔧", "name": "chores", "description": "Configuration and maintenance"},
    {"emoji": "✨", "name": "features", "description": "New features"},
    {"emoji": "🐛", "name": "bugfix", "description": "Bug fixes"},
    {"emoji": "⚡️", "name": "performance", "description": "Performance improvements"},
    {"emoji": "🔒️", "name": "security", "description": "Security fixes"},
    {"emoji": "📝", "name": "documentation", "description": "Documentation"},
    {"emoji": "♻️", "name": "refactor", "description": "Code refactoring"},
]


def extract_message_text(event: MessageEvent) -> str | None:
    """Extract plain-text content from a message event."""
    if not event.llm_message.content:
        return None

    text_parts = []
    for content in event.llm_message.content:
        if isinstance(content, TextContent):
            text_parts.append(content.text)

    return " ".join(text_parts).strip() or None


def extract_first_user_message(events: Sequence[Event]) -> str | None:
    """Extract the first user message from conversation events.

    Args:
        events: List of conversation events.

    Returns:
        The first user message text, or None if no user message is found.
    """
    for event in events:
        if isinstance(event, MessageEvent) and event.source == "user":
            if text := extract_message_text(event):
                return text

    return None


def generate_title_with_llm(message: str, llm: LLM, max_length: int = 50) -> str | None:
    """Generate a conversation title using LLM.

    Args:
        message: The first user message to generate title from.
        llm: The LLM to use for title generation.
        max_length: Maximum length of the generated title.

    Returns:
        Generated title, or None if LLM fails or returns empty response.
    """
    # Truncate very long messages to avoid excessive token usage
    if len(message) > 1000:
        truncated_message = message[:1000] + "...(truncated)"
    else:
        truncated_message = message

    emojis_descriptions = "\n- ".join(
        f"{c['emoji']} {c['name']}: {c['description']}" for c in categories
    )

    try:
        # Create messages for the LLM to generate a title
        messages = [
            Message(
                role="system",
                content=[
                    TextContent(
                        text=(
                            "You are a helpful assistant that generates concise, "
                            "descriptive titles for conversations with OpenHands. "
                            "OpenHands is a helpful AI agent that can interact "
                            "with a computer to solve tasks using bash terminal, "
                            "file editor, and browser. Given a user message "
                            "(which may be truncated), generate a concise, "
                            "descriptive title for the conversation. Return only "
                            "the title, with no additional text, quotes, or "
                            "explanations."
                        )
                    )
                ],
            ),
            Message(
                role="user",
                content=[
                    TextContent(
                        text=(
                            f"Generate a title (maximum {max_length} characters) "
                            f"for a conversation that starts with this message:\n\n"
                            f"{truncated_message}."
                            "Also make sure to include ONE most relevant emoji at "
                            "the start of the title."
                            f" Choose the emoji from this list:{emojis_descriptions} "
                        )
                    )
                ],
            ),
        ]

        # Get completion from LLM
        response = llm.completion(messages)

        # Extract the title from the response
        if response.message.content and isinstance(
            response.message.content[0], TextContent
        ):
            title = response.message.content[0].text.strip()

            # Ensure the title isn't too long
            if len(title) > max_length:
                title = title[: max_length - 3] + "..."

            return title
        else:
            logger.warning("LLM returned empty response for title generation")
            return None

    except Exception as e:
        logger.warning(f"Error generating conversation title with LLM: {e}")
        return None


def generate_fallback_title(message: str, max_length: int = 50) -> str:
    """Generate a fallback title by truncating the first user message.

    Args:
        message: The first user message.
        max_length: Maximum length of the title.

    Returns:
        A truncated title.
    """
    title = message.strip()
    if len(title) > max_length:
        title = title[: max_length - 3] + "..."
    return title


def generate_title_from_message(
    message: str, llm: LLM | None = None, max_length: int = 50
) -> str:
    """Generate a title from an already-extracted user message."""
    llm_to_use = None if llm and llm.model == "acp-managed" else llm

    if llm_to_use:
        llm_title = generate_title_with_llm(message, llm_to_use, max_length)
        if llm_title:
            return llm_title

    return generate_fallback_title(message, max_length)


def generate_conversation_title(
    events: Sequence[Event], llm: LLM | None = None, max_length: int = 50
) -> str:
    """Generate a title for a conversation based on the first user message.

    This is the main utility function that orchestrates the title generation process:
    1. Extract the first user message from events
    2. Try to generate title using LLM
    3. Fall back to simple truncation if LLM fails

    Args:
        events: List of conversation events.
        llm: Optional LLM to use for title generation.
        max_length: Maximum length of the generated title.

    Returns:
        A generated title for the conversation.

    Raises:
        ValueError: If no user messages are found in the conversation events.
    """
    # Find the first user message in the events
    first_user_message = extract_first_user_message(events)

    if not first_user_message:
        raise ValueError("No user messages found in conversation events")

    return generate_title_from_message(first_user_message, llm, max_length)
