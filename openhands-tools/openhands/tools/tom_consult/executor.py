"""Executor for Tom consultation tool."""

import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from openhands.sdk.conversation.event_store import EventLog
from openhands.sdk.conversation.events_list_base import EventsListBase
from openhands.sdk.event import (
    ActionEvent,
    LLMConvertibleEvent,
    ObservationEvent,
)
from openhands.sdk.io import FileStore
from openhands.sdk.logger import get_logger
from openhands.sdk.tool import Observation, ToolExecutor
from openhands.tools.tom_consult.definition import (
    ConsultTomAction,
    ConsultTomObservation,
    SleeptimeComputeAction,
    SleeptimeComputeObservation,
)


if TYPE_CHECKING:
    from tom_swe.tom_agent import ToMAgent

    from openhands.sdk.conversation.base import BaseConversation

logger = get_logger(__name__)


class TomConsultExecutor(
    ToolExecutor[ConsultTomAction | SleeptimeComputeAction, Observation]
):
    """Executor for consulting Tom agent.

    This executor wraps the tom-swe package to provide Theory of Mind
    capabilities for understanding user intent and preferences.
    """

    def __init__(
        self,
        file_store: FileStore,
        enable_rag: bool = True,
        llm_model: str | None = None,
        api_key: str | None = None,
        api_base: str | None = None,
    ):
        """Initialize Tom consultation executor.

        Args:
            file_store: File store for accessing user modeling data
            enable_rag: Whether to enable RAG in Tom agent
            llm_model: LLM model to use for Tom agent
            api_key: API key for Tom agent's LLM
            api_base: Base URL for Tom agent's LLM
        """
        self.file_store: FileStore = file_store
        self.enable_rag: bool = enable_rag
        self.llm_model: str | None = llm_model
        self.api_key: str | None = api_key
        self.api_base: str | None = api_base
        self._tom_agent: ToMAgent | None = None
        self.user_id: str = ""
        self.conversations_dir: str = "conversations"

    def _get_tom_agent(self) -> "ToMAgent":
        """Lazy initialization of Tom agent."""
        if self._tom_agent is None:
            from typing import cast

            from tom_swe.tom_agent import create_tom_agent

            self._tom_agent = create_tom_agent(
                file_store=cast(Any, self.file_store),
                enable_rag=self.enable_rag,
                llm_model=self.llm_model,
                api_key=self.api_key,
                api_base=self.api_base,
            )
        logger.info("Tom agent initialized successfully")
        return self._tom_agent

    def __call__(
        self,
        action: ConsultTomAction | SleeptimeComputeAction,
        conversation: "BaseConversation | None" = None,
    ) -> ConsultTomObservation | SleeptimeComputeObservation:
        """Execute Tom operation.

        Args:
            action: The action to execute (consultation or sleeptime compute)
            conversation: Conversation context for accessing state and history

        Returns:
            Observation with results
        """
        if isinstance(action, SleeptimeComputeAction):
            return self._sleeptime_compute(conversation)
        else:
            return self._consult_tom(action, conversation)

    def _format_events(
        self,
        event_log: EventLog | EventsListBase,
        conversation: "BaseConversation | None" = None,
    ) -> list[dict[str, Any]]:
        """Format events into messages for Tom agent.

        Args:
            event_log: Events to format
            conversation: Optional conversation for LLM formatting

        Returns:
            List of formatted messages (skips system messages)
        """
        events = list(event_log)
        # Get only completed action-observation pairs
        matched_action_ids = {
            obs_event.action_id
            for obs_event in events
            if isinstance(obs_event, ObservationEvent)
        }

        llm_convertible_events = [
            e
            for e in events
            if isinstance(e, LLMConvertibleEvent)
            and (not isinstance(e, ActionEvent) or e.id in matched_action_ids)
        ]

        if not llm_convertible_events:
            return []

        # Convert to messages
        messages = LLMConvertibleEvent.events_to_messages(llm_convertible_events)

        # Format messages - use conversation's LLM if available, otherwise manual format
        if conversation is not None:
            # Skip system message (first message)
            return conversation.state.agent.llm.format_messages_for_llm(messages)[1:]
        else:
            # If no conversation, format messages directly from events
            from openhands.sdk.llm import TextContent

            formatted_messages = []
            for msg in messages:
                if msg.role != "system":  # Skip system messages
                    text_contents = [
                        {"text": c.text}
                        for c in msg.content
                        if isinstance(c, TextContent)
                    ]
                    if text_contents:
                        formatted_messages.append(
                            {"role": msg.role, "content": text_contents}
                        )
            return formatted_messages

    def _consult_tom(
        self, action: ConsultTomAction, conversation: "BaseConversation | None" = None
    ) -> ConsultTomObservation:
        """Execute Tom consultation.

        Args:
            action: The consultation action with query details
            conversation: Conversation context for accessing history

        Returns:
            ConsultTomObservation with Tom's suggestions
        """
        try:
            tom_agent = self._get_tom_agent()

            # Build query text using exact format from original implementation
            if action.use_user_message:
                query_text = f"I am SWE agent. {action.reason} I need to consult ToM agent about the user's message: [USER MESSAGE PLACEHOLDER]"  # noqa: E501
            elif action.custom_query:
                query_text = f"I am SWE agent. {action.reason} I need to consult ToM agent: {action.custom_query}"  # noqa: E501
            else:
                logger.warning("⚠️ Tom: No query specified for consultation")
                return ConsultTomObservation(
                    suggestions="[CRITICAL] Tom agent cannot provide consultation for this user message. Do not consult ToM agent again for this message and use other actions instead."  # noqa: E501
                )

            # Get conversation history if available
            formatted_messages = []
            if conversation is not None:
                formatted_messages = self._format_events(
                    conversation.state.events, conversation
                )

                # Get last user message for query text
                if formatted_messages:
                    last_user_message = [
                        m for m in formatted_messages if m["role"] == "user"
                    ][-1]
                    query_text = query_text.replace(
                        "[USER MESSAGE PLACEHOLDER]",
                        last_user_message["content"][0]["text"],
                    )

                    logger.info(
                        f"Consulting Tom agent with "
                        f"{len(formatted_messages)} history messages"
                    )

            logger.info(f"Consulting Tom agent: {query_text[:100]}...")
            result = tom_agent.give_suggestions(
                user_id=self.user_id,
                query=query_text,
                formatted_messages=formatted_messages,
            )

            if result and hasattr(result, "suggestions"):
                logger.info(
                    "✅ Tom: Requesting observation update with consultation result"
                )

                # Format the response exactly like the original implementation
                query_description = action.custom_query or "the user's message"
                formatted_response = (
                    f"{action.reason}\n"
                    f"I need to consult Tom agent about {query_description}\n\n"
                    "[Starting consultation with Tom agent...]\n"
                    f"{result.suggestions}\n\n"
                    "[Finished consulting with ToM Agent...]"
                )

                return ConsultTomObservation(
                    suggestions=formatted_response,
                    confidence=getattr(result, "confidence", None),
                    reasoning=getattr(result, "reasoning", None),
                )
            else:
                logger.warning("⚠️ Tom: No consultation result received")
                return ConsultTomObservation(
                    suggestions="[CRITICAL] Tom agent cannot provide consultation for this user message. Do not consult ToM agent again for this message and use other actions instead."  # noqa: E501
                )

        except Exception as e:
            logger.error(f"❌ Tom: Error in consultation: {e}")
            return ConsultTomObservation(
                suggestions="[CRITICAL] Tom agent cannot provide consultation for this user message. Do not consult ToM agent again for this message and use other actions instead."  # noqa: E501
            )

    def _sleeptime_compute(
        self, conversation: "BaseConversation | None" = None
    ) -> SleeptimeComputeObservation:
        """Execute sleeptime compute to index conversations for user modeling.

        This processes all unprocessed conversations from the file store,
        similar to the OpenHands implementation.

        Args:
            conversation: Conversation context (used for LLM formatting)

        Returns:
            SleeptimeComputeObservation with indexing results
        """
        tom_agent = self._get_tom_agent()

        logger.info("🔄 Tom: Starting sleeptime compute")

        session_paths = self.file_store.list(self.conversations_dir)
        all_sessions = [
            Path(path).name
            for path in session_paths
            if not Path(path).name.startswith(".")
        ]

        if not all_sessions:
            logger.info("📭 Tom: No conversation sessions found")
            return SleeptimeComputeObservation(
                message="No conversation sessions found", sessions_processed=0
            )

        # Load processing history to find unprocessed sessions
        processing_history = self._load_processing_history()

        # Find sessions that need processing
        sessions_to_process = []
        for session_id in all_sessions:
            events_dir = f"{self.conversations_dir}/{session_id}/events"
            event_files = self.file_store.list(events_dir)  # type: ignore
            if not event_files:
                continue

            current_event_count = len(event_files)

            # Check if needs processing (new or has new events)
            if session_id not in processing_history:
                sessions_to_process.append(session_id)
                logger.info(f"📋 Tom: Session {session_id} needs processing (new)")
            elif current_event_count > processing_history[session_id].get(
                "last_event_count", 0
            ):
                sessions_to_process.append(session_id)
                logger.info(
                    f"📋 Tom: Session {session_id} has new events "
                    f"({current_event_count} events)"
                )

        if not sessions_to_process:
            logger.info("📭 Tom: No sessions need processing")
            return SleeptimeComputeObservation(
                message="All conversations already indexed", sessions_processed=0
            )

        logger.info(f"📊 Tom: Found {len(sessions_to_process)} sessions to process")
        # Collect session data for each conversation
        sessions_data = []
        for session_id in sessions_to_process:
            session_data = self._extract_session_data(session_id, conversation)
            if session_data:
                sessions_data.append(session_data)
        if not sessions_data:
            logger.info("📭 Tom: No valid session data extracted")
            return SleeptimeComputeObservation(
                message="No valid conversations to index", sessions_processed=0
            )

        logger.info(
            f"📊 Tom: Extracted {len(sessions_data)} sessions, calling Tom agent"
        )
        # Call sleeptime_compute
        tom_agent.sleeptime_compute(
            sessions_data=sessions_data,
            user_id=self.user_id,
        )

        # Update processing history
        self._save_processing_history(sessions_to_process)

        logger.info(f"✅ Tom: Successfully indexed {len(sessions_data)} conversations")
        return SleeptimeComputeObservation(
            message=f"Indexed {len(sessions_data)} conversations for user modeling",  # noqa: E501
            sessions_processed=len(sessions_data),
        )

    def _extract_session_data(
        self, session_id: str, conversation: "BaseConversation | None"
    ) -> dict[str, Any] | None:
        """Extract session data from a conversation directory."""

        # Load events from the session using file_store
        events_dir = f"{self.conversations_dir}/{session_id}/events"
        events = EventLog(self.file_store, events_dir)

        # Format events into messages
        formatted_messages = self._format_events(events, conversation)
        if not formatted_messages:
            return None

        # Convert to tom-swe format
        conversation_messages = []
        for msg in formatted_messages:
            if isinstance(msg, dict) and "role" in msg and "content" in msg:
                text_parts = []
                if isinstance(msg["content"], list):
                    for content in msg["content"]:
                        if isinstance(content, dict) and "text" in content:
                            text_parts.append(content["text"])
                if text_parts:
                    conversation_messages.append(
                        {"role": msg["role"], "content": "\n".join(text_parts)}
                    )

        if not conversation_messages:
            return None

        return {
            "session_id": session_id,
            "start_time": events[0].timestamp if events else "",  # type: ignore
            "end_time": events[-1].timestamp if events else "",  # type: ignore
            "event_count": len(events),
            "message_count": len(conversation_messages),
            "conversation_messages": conversation_messages,
        }

    def _load_processing_history(self) -> dict[str, Any]:
        """Load processing history for this user."""
        try:
            from tom_swe.memory.locations import get_usermodeling_dir

            history_file = f"{get_usermodeling_dir(self.user_id)}/processed_sessions_timestamps.json"  # noqa: E501
            content = self.file_store.read(history_file)
            return json.loads(content)
        except FileNotFoundError:
            return {}
        except Exception as e:
            logger.debug(f"Could not load processing history: {e}")
            return {}

    def _save_processing_history(self, session_ids: list[str]) -> None:
        """Save processing history for processed sessions."""
        try:
            from tom_swe.memory.locations import get_usermodeling_dir

            history = self._load_processing_history()
            timestamp = datetime.now().isoformat()

            for session_id in session_ids:
                events_dir = f"{self.conversations_dir}/{session_id}/events"
                try:
                    event_files = self.file_store.list(events_dir)
                    event_count = len(event_files)
                except Exception:
                    event_count = 0

                history[session_id] = {
                    "processed_at": timestamp,
                    "last_event_count": event_count,
                }

            history_file = f"{get_usermodeling_dir(self.user_id)}/processed_sessions_timestamps.json"  # noqa: E501

            self.file_store.write(history_file, json.dumps(history, indent=2))
            logger.info(
                f"📝 Tom: Updated processing history for {len(session_ids)} sessions"
            )  # noqa: E501
        except Exception as e:
            logger.error(f"Failed to save processing history: {e}")
