import asyncio
import importlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, cast
from uuid import UUID, uuid4

import httpx
from pydantic import BaseModel

from openhands.agent_server.config import Config, WebhookSpec
from openhands.agent_server.event_service import EventService
from openhands.agent_server.models import (
    ACPConversationInfo,
    ACPConversationPage,
    ConversationInfo,
    ConversationPage,
    ConversationSortOrder,
    StartACPConversationRequest,
    StartConversationRequest,
    StoredConversation,
    UpdateConversationRequest,
)
from openhands.agent_server.pub_sub import Subscriber
from openhands.agent_server.server_details_router import update_last_execution_time
from openhands.agent_server.utils import safe_rmtree, utc_now
from openhands.sdk import LLM, Agent, Event, Message
from openhands.sdk.conversation.state import (
    ConversationExecutionStatus,
    ConversationState,
)
from openhands.sdk.conversation.title_utils import (
    extract_message_text,
    generate_title_from_message,
)
from openhands.sdk.event import MessageEvent
from openhands.sdk.event.conversation_state import ConversationStateUpdateEvent
from openhands.sdk.utils.cipher import Cipher


if TYPE_CHECKING:
    from openhands.sdk.subagent.schema import AgentDefinition


logger = logging.getLogger(__name__)


class ConversationContractMismatchError(ValueError):
    """Raised when a conversation ID exists under a different REST contract."""


def _conversation_contract_mismatch_message(conversation_id: UUID) -> str:
    return (
        f"Conversation {conversation_id} exists but is only available through the "
        "ACP conversation contract. Use /api/acp/conversations or attach with "
        "ACPAgent."
    )


def _compose_conversation_info_v1(
    stored: StoredConversation, state: ConversationState
) -> ConversationInfo:
    assert isinstance(stored.agent, Agent)
    # Use mode='json' so SecretStr in nested structures (e.g. LookupSecret.headers,
    # agent.agent_context.secrets) serialize to strings. Without it, validation
    # fails because ConversationInfo expects dict[str, str] but receives SecretStr.
    return ConversationInfo(
        **state.model_dump(mode="json"),
        title=stored.title,
        metrics=stored.metrics,
        created_at=stored.created_at,
        updated_at=stored.updated_at,
    )


def _compose_acp_conversation_info(
    stored: StoredConversation, state: ConversationState
) -> ACPConversationInfo:
    return ACPConversationInfo(
        **state.model_dump(mode="json"),
        title=stored.title,
        metrics=stored.metrics,
        created_at=stored.created_at,
        updated_at=stored.updated_at,
    )


def _is_v1_conversation(stored: StoredConversation) -> bool:
    return isinstance(stored.agent, Agent)


def _compose_webhook_conversation_info(
    stored: StoredConversation, state: ConversationState
) -> ConversationInfo | ACPConversationInfo:
    if _is_v1_conversation(stored):
        return _compose_conversation_info_v1(stored, state)
    return _compose_acp_conversation_info(stored, state)


def _update_state_tags_sync(
    state: ConversationState, tags: dict[str, str]
) -> ConversationState:
    with state:
        state.tags = tags
    return state


def _compose_webhook_conversation_info_sync(
    stored: StoredConversation, state: ConversationState
) -> ConversationInfo | ACPConversationInfo:
    with state:
        return _compose_webhook_conversation_info(stored, state)


def _register_agent_definitions(
    agent_defs: list["AgentDefinition"],
    *,
    context: str,
) -> None:
    """Register agent definitions into the subagent registry.

    Used both when creating new conversations (definitions forwarded from the
    client) and when resuming persisted ones (definitions stored in meta.json).
    """
    from openhands.sdk.subagent.registry import (
        agent_definition_to_factory,
        register_agent_if_absent,
    )

    registered = 0
    for agent_def in agent_defs:
        try:
            factory = agent_definition_to_factory(agent_def)
            register_agent_if_absent(
                name=agent_def.name,
                factory_func=factory,
                description=agent_def,
            )
            registered += 1
        except Exception as e:
            logger.warning(
                f"Failed to register agent definition "
                f"'{agent_def.name}' ({context}): {e}"
            )
    logger.info(
        f"Registered {registered}/{len(agent_defs)} agent definition(s) ({context})"
    )


@dataclass
class ConversationService:
    """
    Conversation service which stores to a local file store. When the context starts
    all event_services are loaded into memory, and stored when it stops.
    """

    conversations_dir: Path = field()
    webhook_specs: list[WebhookSpec] = field(default_factory=list)
    session_api_key: str | None = field(default=None)
    cipher: Cipher | None = None
    _event_services: dict[UUID, EventService] | None = field(default=None, init=False)
    _conversation_webhook_subscribers: list["ConversationWebhookSubscriber"] = field(
        default_factory=list, init=False
    )

    async def get_conversation(self, conversation_id: UUID) -> ConversationInfo | None:
        if self._event_services is None:
            raise ValueError("inactive_service")
        event_service = self._event_services.get(conversation_id)
        if event_service is None:
            return None
        if not _is_v1_conversation(event_service.stored):
            return None
        state = await event_service.get_state()
        return _compose_conversation_info_v1(event_service.stored, state)

    async def get_acp_conversation(
        self, conversation_id: UUID
    ) -> ACPConversationInfo | None:
        if self._event_services is None:
            raise ValueError("inactive_service")
        event_service = self._event_services.get(conversation_id)
        if event_service is None:
            return None
        state = await event_service.get_state()
        return _compose_acp_conversation_info(event_service.stored, state)

    async def search_conversations(
        self,
        page_id: str | None = None,
        limit: int = 100,
        execution_status: ConversationExecutionStatus | None = None,
        sort_order: ConversationSortOrder = ConversationSortOrder.CREATED_AT_DESC,
    ) -> ConversationPage:
        items, next_page_id = await self._search_conversations(
            page_id=page_id,
            limit=limit,
            execution_status=execution_status,
            sort_order=sort_order,
            include_acp=False,
        )
        return ConversationPage(
            items=cast(list[ConversationInfo], items),
            next_page_id=next_page_id,
        )

    async def search_acp_conversations(
        self,
        page_id: str | None = None,
        limit: int = 100,
        execution_status: ConversationExecutionStatus | None = None,
        sort_order: ConversationSortOrder = ConversationSortOrder.CREATED_AT_DESC,
    ) -> ACPConversationPage:
        items, next_page_id = await self._search_conversations(
            page_id=page_id,
            limit=limit,
            execution_status=execution_status,
            sort_order=sort_order,
            include_acp=True,
        )
        return ACPConversationPage(
            items=cast(list[ACPConversationInfo], items),
            next_page_id=next_page_id,
        )

    async def _search_conversations(
        self,
        page_id: str | None,
        limit: int,
        execution_status: ConversationExecutionStatus | None,
        sort_order: ConversationSortOrder,
        *,
        include_acp: bool,
    ) -> tuple[list[ConversationInfo | ACPConversationInfo], str | None]:
        if self._event_services is None:
            raise ValueError("inactive_service")

        # Collect all conversations with their info
        all_conversations = []
        for id, event_service in self._event_services.items():
            if not include_acp and not _is_v1_conversation(event_service.stored):
                continue
            state = await event_service.get_state()
            conversation_info = (
                _compose_acp_conversation_info(event_service.stored, state)
                if include_acp
                else _compose_conversation_info_v1(event_service.stored, state)
            )
            # Apply status filter if provided
            if (
                execution_status is not None
                and conversation_info.execution_status != execution_status
            ):
                continue

            all_conversations.append((id, conversation_info))

        # Sort conversations based on sort_order
        if sort_order == ConversationSortOrder.CREATED_AT:
            all_conversations.sort(key=lambda x: x[1].created_at)
        elif sort_order == ConversationSortOrder.CREATED_AT_DESC:
            all_conversations.sort(key=lambda x: x[1].created_at, reverse=True)
        elif sort_order == ConversationSortOrder.UPDATED_AT:
            all_conversations.sort(key=lambda x: x[1].updated_at)
        elif sort_order == ConversationSortOrder.UPDATED_AT_DESC:
            all_conversations.sort(key=lambda x: x[1].updated_at, reverse=True)

        # Handle pagination
        items = []
        start_index = 0

        # Find the starting point if page_id is provided
        if page_id:
            for i, (id, _) in enumerate(all_conversations):
                if id.hex == page_id:
                    start_index = i
                    break

        # Collect items for this page
        next_page_id = None
        for i in range(start_index, len(all_conversations)):
            if len(items) >= limit:
                # We have more items, set next_page_id
                if i < len(all_conversations):
                    next_page_id = all_conversations[i][0].hex
                break
            items.append(all_conversations[i][1])

        return items, next_page_id

    async def count_conversations(
        self,
        execution_status: ConversationExecutionStatus | None = None,
    ) -> int:
        return await self._count_conversations(
            execution_status=execution_status,
            include_acp=False,
        )

    async def count_acp_conversations(
        self,
        execution_status: ConversationExecutionStatus | None = None,
    ) -> int:
        return await self._count_conversations(
            execution_status=execution_status,
            include_acp=True,
        )

    async def _count_conversations(
        self,
        execution_status: ConversationExecutionStatus | None,
        *,
        include_acp: bool,
    ) -> int:
        """Count conversations matching the given filters."""
        if self._event_services is None:
            raise ValueError("inactive_service")

        count = 0
        for event_service in self._event_services.values():
            if not include_acp and not _is_v1_conversation(event_service.stored):
                continue
            state = await event_service.get_state()

            # Apply status filter if provided
            if (
                execution_status is not None
                and state.execution_status != execution_status
            ):
                continue

            count += 1

        return count

    async def batch_get_conversations(
        self, conversation_ids: list[UUID]
    ) -> list[ConversationInfo | None]:
        """Given a list of ids, get a batch of conversation info, returning
        None for any that were not found."""
        results = await asyncio.gather(
            *[
                self.get_conversation(conversation_id)
                for conversation_id in conversation_ids
            ]
        )
        return results

    async def batch_get_acp_conversations(
        self, conversation_ids: list[UUID]
    ) -> list[ACPConversationInfo | None]:
        results = await asyncio.gather(
            *[
                self.get_acp_conversation(conversation_id)
                for conversation_id in conversation_ids
            ]
        )
        return results

    async def _notify_conversation_webhooks(self, conversation_info: BaseModel):
        """Notify all conversation webhook subscribers about conversation changes."""
        if not self._conversation_webhook_subscribers:
            return

        # Send notifications to all conversation webhook subscribers in the background
        async def _notify_and_log_errors():
            results = await asyncio.gather(
                *[
                    subscriber.post_conversation_info(conversation_info)
                    for subscriber in self._conversation_webhook_subscribers
                ],
                return_exceptions=True,  # Don't fail if one webhook fails
            )

            # Log any exceptions that occurred
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    subscriber = self._conversation_webhook_subscribers[i]
                    logger.error(
                        (
                            f"Failed to notify conversation webhook "
                            f"{subscriber.spec.base_url}: {result}"
                        ),
                        exc_info=result,
                    )

        # Create task to run in background without awaiting
        asyncio.create_task(_notify_and_log_errors())

    # Write Methods

    async def start_conversation(
        self, request: StartConversationRequest
    ) -> tuple[ConversationInfo, bool]:
        conversation_info, is_new = await self._start_conversation(request)
        assert isinstance(conversation_info, ConversationInfo)
        return conversation_info, is_new

    async def start_acp_conversation(
        self, request: StartACPConversationRequest
    ) -> tuple[ACPConversationInfo, bool]:
        conversation_info, is_new = await self._start_conversation(request)
        assert isinstance(conversation_info, ACPConversationInfo)
        return conversation_info, is_new

    async def _start_conversation(
        self, request: StartConversationRequest | StartACPConversationRequest
    ) -> tuple[ConversationInfo | ACPConversationInfo, bool]:
        """Start a local event_service and return its id."""
        if self._event_services is None:
            raise ValueError("inactive_service")
        conversation_id = request.conversation_id or uuid4()
        use_acp_contract = isinstance(request, StartACPConversationRequest)

        existing_event_service = self._event_services.get(conversation_id)
        if (
            existing_event_service is not None
            and not use_acp_contract
            and not _is_v1_conversation(existing_event_service.stored)
        ):
            raise ConversationContractMismatchError(
                _conversation_contract_mismatch_message(conversation_id)
            )
        if existing_event_service and existing_event_service.is_open():
            state = await existing_event_service.get_state()
            conversation_info = (
                _compose_acp_conversation_info(existing_event_service.stored, state)
                if use_acp_contract
                else _compose_conversation_info_v1(existing_event_service.stored, state)
            )
            return conversation_info, False

        # Dynamically register tools from client's registry
        if request.tool_module_qualnames:
            import importlib

            for tool_name, module_qualname in request.tool_module_qualnames.items():
                try:
                    # Import the module to trigger tool auto-registration
                    importlib.import_module(module_qualname)
                    logger.debug(
                        f"Tool '{tool_name}' registered via module '{module_qualname}'"
                    )
                except ImportError as e:
                    logger.warning(
                        f"Failed to import module '{module_qualname}' for tool "
                        f"'{tool_name}': {e}. Tool will not be available."
                    )
                    # Continue even if some tools fail to register
                    # The agent will fail gracefully if it tries to use unregistered
                    # tools
            if request.tool_module_qualnames:
                logger.info(
                    f"Dynamically registered {len(request.tool_module_qualnames)} "
                    f"tools for conversation {conversation_id}: "
                    f"{list(request.tool_module_qualnames.keys())}"
                )

        # Register subagent definitions forwarded from the client
        if request.agent_definitions:
            _register_agent_definitions(
                request.agent_definitions,
                context=f"conversation {conversation_id}",
            )

        # Plugin loading is now handled lazily by LocalConversation.
        # Just pass the plugin specs through to StoredConversation.
        # LocalConversation will:
        # 1. Fetch and load plugins on first run()/send_message()
        # 2. Resolve refs to commit SHAs for deterministic resume
        # 3. Merge plugin skills/MCP/hooks into the agent
        #
        # Use mode='json' so SecretStr in nested structures (e.g. LookupSecret.headers)
        # serialize to plain strings. Pass expose_secrets=True so StaticSecret values
        # are preserved through the round-trip; the dict is only used in-process to
        # construct StoredConversation, not sent over the network.
        stored = StoredConversation(
            id=conversation_id,
            **request.model_dump(
                mode="json",
                context={"expose_secrets": True, "raw_mcp_config": True},
            ),
        )
        event_service = await self._start_event_service(stored)
        initial_message = request.initial_message
        if initial_message:
            message = Message(
                role=initial_message.role, content=initial_message.content
            )
            await event_service.send_message(message, True)

        state = await event_service.get_state()
        conversation_info = (
            _compose_acp_conversation_info(event_service.stored, state)
            if use_acp_contract
            else _compose_conversation_info_v1(event_service.stored, state)
        )

        # Notify conversation webhooks about the started conversation
        await self._notify_conversation_webhooks(
            _compose_webhook_conversation_info(event_service.stored, state)
        )

        return conversation_info, True

    async def pause_conversation(self, conversation_id: UUID) -> bool:
        if self._event_services is None:
            raise ValueError("inactive_service")
        event_service = self._event_services.get(conversation_id)
        if event_service:
            await event_service.pause()
            # Notify conversation webhooks about the paused conversation
            state = await event_service.get_state()
            conversation_info = _compose_webhook_conversation_info(
                event_service.stored, state
            )
            await self._notify_conversation_webhooks(conversation_info)
        return bool(event_service)

    async def resume_conversation(self, conversation_id: UUID) -> bool:
        if self._event_services is None:
            raise ValueError("inactive_service")
        event_service = self._event_services.get(conversation_id)
        if event_service:
            await event_service.start()
        return bool(event_service)

    async def delete_conversation(self, conversation_id: UUID) -> bool:
        if self._event_services is None:
            raise ValueError("inactive_service")
        event_service = self._event_services.pop(conversation_id, None)
        if event_service:
            # Notify conversation webhooks about the stopped conversation before closing
            try:
                state = await event_service.get_state()
                conversation_info = _compose_webhook_conversation_info(
                    event_service.stored, state
                )
                conversation_info.execution_status = (
                    ConversationExecutionStatus.DELETING
                )
                await self._notify_conversation_webhooks(conversation_info)
            except Exception as e:
                logger.warning(
                    f"Failed to notify webhooks for conversation {conversation_id}: {e}"
                )

            # Close the event service
            try:
                await event_service.close()
            except Exception as e:
                logger.warning(
                    f"Failed to close event service for conversation "
                    f"{conversation_id}: {e}"
                )

            # Safely remove only the conversation directory (workspace is preserved).
            # This operation may fail due to permission issues, but we don't want that
            # to prevent the conversation from being marked as deleted.
            safe_rmtree(
                event_service.conversation_dir,
                f"conversation directory for {conversation_id}",
            )

            logger.info(f"Successfully deleted conversation {conversation_id}")
            return True
        return False

    async def update_conversation(
        self, conversation_id: UUID, request: UpdateConversationRequest
    ) -> bool:
        """Update conversation metadata.

        Args:
            conversation_id: The ID of the conversation to update
            request: Request object containing fields to update (e.g., title, tags)

        Returns:
            bool: True if the conversation was updated successfully, False if not found
        """
        if self._event_services is None:
            raise ValueError("inactive_service")
        event_service = self._event_services.get(conversation_id)
        if event_service is None:
            return False

        loop = asyncio.get_running_loop()
        state = await event_service.get_state()
        if request.title is not None:
            event_service.stored.title = request.title.strip()
        if request.tags is not None:
            event_service.stored.tags = request.tags
            # Keep the persisted ConversationState update under the state lock so
            # autosave and state-change callbacks observe a consistent mutation.
            state = await loop.run_in_executor(
                None, _update_state_tags_sync, state, request.tags
            )
        event_service.stored.updated_at = utc_now()
        # Save the updated metadata to disk
        await event_service.save_meta()

        # Notify conversation webhooks about the updated conversation. Compose the
        # full-state snapshot under the state lock, but do the synchronous wait in a
        # worker thread so metadata updates cannot block the FastAPI event loop.
        conversation_info = await loop.run_in_executor(
            None, _compose_webhook_conversation_info_sync, event_service.stored, state
        )
        await self._notify_conversation_webhooks(conversation_info)

        updated_fields = []
        if request.title is not None:
            updated_fields.append(f"title: {request.title}")
        if request.tags is not None:
            updated_fields.append(f"tags: {request.tags}")
        logger.info(
            f"Successfully updated conversation {conversation_id} "
            f"with {', '.join(updated_fields)}"
        )
        return True

    async def get_event_service(self, conversation_id: UUID) -> EventService | None:
        if self._event_services is None:
            raise ValueError("inactive_service")
        return self._event_services.get(conversation_id)

    async def generate_conversation_title(
        self, conversation_id: UUID, max_length: int = 50, llm: LLM | None = None
    ) -> str | None:
        """Generate a title for the conversation using LLM."""
        if self._event_services is None:
            raise ValueError("inactive_service")
        event_service = self._event_services.get(conversation_id)
        if event_service is None:
            return None

        # Delegate to EventService to avoid accessing private conversation internals
        title = await event_service.generate_title(llm=llm, max_length=max_length)
        return title

    async def ask_agent(self, conversation_id: UUID, question: str) -> str | None:
        """Ask the agent a simple question without affecting conversation state."""
        if self._event_services is None:
            raise ValueError("inactive_service")
        event_service = self._event_services.get(conversation_id)
        if event_service is None:
            return None

        # Delegate to EventService to avoid accessing private conversation internals
        response = await event_service.ask_agent(question)
        return response

    async def condense(self, conversation_id: UUID) -> bool:
        """Force condensation of the conversation history."""
        if self._event_services is None:
            raise ValueError("inactive_service")
        event_service = self._event_services.get(conversation_id)
        if event_service is None:
            return False

        # Delegate to EventService to avoid accessing private conversation internals
        await event_service.condense()
        return True

    async def __aenter__(self):
        self.conversations_dir.mkdir(parents=True, exist_ok=True)
        self._event_services = {}
        for conversation_dir in self.conversations_dir.iterdir():
            try:
                meta_file = conversation_dir / "meta.json"
                if not meta_file.exists():
                    continue
                json_str = meta_file.read_text()
                stored = StoredConversation.model_validate_json(
                    json_str,
                    context={
                        "cipher": self.cipher,
                    },
                )
                # Dynamically register tools when resuming persisted conversations
                if stored.tool_module_qualnames:
                    for (
                        tool_name,
                        module_qualname,
                    ) in stored.tool_module_qualnames.items():
                        try:
                            # Import the module to trigger tool auto-registration
                            importlib.import_module(module_qualname)
                            logger.debug(
                                f"Tool '{tool_name}' registered via module "
                                f"'{module_qualname}' when resuming conversation "
                                f"{stored.id}"
                            )
                        except ImportError as e:
                            logger.warning(
                                f"Failed to import module '{module_qualname}' for "
                                f"tool '{tool_name}' when resuming conversation "
                                f"{stored.id}: {e}. Tool will not be available."
                            )
                            # Continue even if some tools fail to register
                    if stored.tool_module_qualnames:
                        logger.info(
                            f"Dynamically registered "
                            f"{len(stored.tool_module_qualnames)} tools when "
                            f"resuming conversation {stored.id}: "
                            f"{list(stored.tool_module_qualnames.keys())}"
                        )
                # Register agent definitions when resuming
                if stored.agent_definitions:
                    _register_agent_definitions(
                        stored.agent_definitions,
                        context=f"resuming conversation {stored.id}",
                    )
                await self._start_event_service(stored)
            except Exception:
                logger.exception(
                    f"error_loading_event_service:{conversation_dir}", stack_info=True
                )

        # Initialize conversation webhook subscribers
        self._conversation_webhook_subscribers = [
            ConversationWebhookSubscriber(
                spec=webhook_spec,
                session_api_key=self.session_api_key,
            )
            for webhook_spec in self.webhook_specs
        ]

        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        event_services = self._event_services
        if event_services is None:
            return
        self._event_services = None
        # This stops conversations and saves meta
        await asyncio.gather(
            *[
                event_service.__aexit__(exc_type, exc_value, traceback)
                for event_service in event_services.values()
            ]
        )

    @classmethod
    def get_instance(cls, config: Config) -> "ConversationService":
        return ConversationService(
            conversations_dir=config.conversations_path,
            webhook_specs=config.webhooks,
            session_api_key=(
                config.session_api_keys[0] if config.session_api_keys else None
            ),
            cipher=config.cipher,
        )

    async def _start_event_service(self, stored: StoredConversation) -> EventService:
        event_services = self._event_services
        if event_services is None:
            raise ValueError("inactive_service")

        event_service = EventService(
            stored=stored,
            conversations_dir=self.conversations_dir,
            cipher=self.cipher,
        )
        # Create subscribers...
        await event_service.subscribe_to_events(_EventSubscriber(service=event_service))
        if stored.autotitle and stored.title is None:
            await event_service.subscribe_to_events(
                AutoTitleSubscriber(service=event_service)
            )
        asyncio.gather(
            *[
                event_service.subscribe_to_events(
                    WebhookSubscriber(
                        conversation_id=stored.id,
                        service=event_service,
                        spec=webhook_spec,
                        session_api_key=self.session_api_key,
                    )
                )
                for webhook_spec in self.webhook_specs
            ]
        )

        try:
            await event_service.start()
            # Save metadata immediately after successful start to ensure persistence
            # even if the system is not shut down gracefully
            await event_service.save_meta()
        except Exception:
            # Clean up the event service if startup fails
            await event_service.close()
            raise

        event_services[stored.id] = event_service
        return event_service


@dataclass
class _EventSubscriber(Subscriber):
    service: EventService

    async def __call__(self, _event: Event):
        # Skip updating timestamp for ConversationStateUpdateEvent, which is
        # published during startup/state changes and doesn't represent actual
        # conversation activity. This prevents updated_at from being reset
        # on every server restart.
        if isinstance(_event, ConversationStateUpdateEvent):
            return
        self.service.stored.updated_at = utc_now()
        update_last_execution_time()


@dataclass
class AutoTitleSubscriber(Subscriber):
    service: EventService

    async def __call__(self, event: Event) -> None:
        # Only act on incoming user messages
        if not isinstance(event, MessageEvent) or event.source != "user":
            return
        # Guard: skip if a title was already set (e.g. by a concurrent task)
        if self.service.stored.title is not None:
            return

        # Extract the message text now, before spawning the background task,
        # to avoid a race where the event hasn't been persisted to the events
        # list yet when title generation tries to read it.
        message_text = extract_message_text(event)
        if not message_text:
            return

        conversation = self.service._conversation
        llm = conversation.agent.llm if conversation else None

        async def _generate_and_save() -> None:
            try:
                loop = asyncio.get_running_loop()
                title = await loop.run_in_executor(
                    None,
                    generate_title_from_message,
                    message_text,
                    llm,
                    50,
                )
                if title and self.service.stored.title is None:
                    self.service.stored.title = title
                    self.service.stored.updated_at = utc_now()
                    await self.service.save_meta()
            except Exception:
                logger.warning(
                    f"Auto-title generation failed for "
                    f"conversation {self.service.stored.id}",
                    exc_info=True,
                )

        asyncio.create_task(_generate_and_save())


@dataclass
class WebhookSubscriber(Subscriber):
    conversation_id: UUID
    service: EventService
    spec: WebhookSpec
    session_api_key: str | None = None
    queue: list[Event] = field(default_factory=list)
    _flush_timer: asyncio.Task | None = field(default=None, init=False)

    async def __call__(self, event: Event):
        """Add event to queue and post to webhook when buffer size is reached."""
        self.queue.append(event)

        if len(self.queue) >= self.spec.event_buffer_size:
            # Cancel timer since we're flushing due to buffer size
            self._cancel_flush_timer()
            await self._post_events()
        elif not self._flush_timer:
            self._flush_timer = asyncio.create_task(self._flush_after_delay())

    async def close(self):
        """Post any remaining items in the queue to the webhook."""
        # Cancel any pending flush timer
        self._cancel_flush_timer()

        if self.queue:
            await self._post_events()

    async def _post_events(self):
        """Post queued events to the webhook with retry logic."""
        if not self.queue:
            return

        events_to_post = self.queue.copy()
        self.queue.clear()

        # Prepare headers
        headers = self.spec.headers.copy()
        if self.session_api_key:
            headers["X-Session-API-Key"] = self.session_api_key

        # Convert events to serializable format
        event_data = [
            event.model_dump() if hasattr(event, "model_dump") else event.__dict__
            for event in events_to_post
        ]

        # Construct events URL
        events_url = (
            f"{self.spec.base_url.rstrip('/')}/events/{self.conversation_id.hex}"
        )

        # Retry logic
        for attempt in range(self.spec.num_retries + 1):
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.request(
                        method="POST",
                        url=events_url,
                        json=event_data,
                        headers=headers,
                        timeout=30.0,
                    )
                    response.raise_for_status()
                    logger.debug(
                        f"Successfully posted {len(event_data)} events "
                        f"to webhook {events_url}"
                    )
                    return
            except Exception as e:
                logger.warning(f"Webhook post attempt {attempt + 1} failed: {e}")
                if attempt < self.spec.num_retries:
                    await asyncio.sleep(self.spec.retry_delay)
                else:
                    logger.error(
                        f"Failed to post events to webhook {events_url} "
                        f"after {self.spec.num_retries + 1} attempts"
                    )
                    # Re-queue events for potential retry later
                    self.queue.extend(events_to_post)

    def _cancel_flush_timer(self):
        """Cancel the current flush timer if it exists."""
        if self._flush_timer and not self._flush_timer.done():
            self._flush_timer.cancel()
        self._flush_timer = None

    async def _flush_after_delay(self):
        """Wait for flush_delay seconds then flush events if any exist."""
        try:
            await asyncio.sleep(self.spec.flush_delay)
            # Only flush if there are events in the queue
            if self.queue:
                await self._post_events()
        except asyncio.CancelledError:
            # Timer was cancelled, which is expected behavior
            pass
        finally:
            self._flush_timer = None


@dataclass
class ConversationWebhookSubscriber:
    """Webhook subscriber for conversation lifecycle events (start, pause, stop)."""

    spec: WebhookSpec
    session_api_key: str | None = None

    async def post_conversation_info(self, conversation_info: BaseModel):
        """Post conversation info to the webhook immediately (no batching)."""
        # Prepare headers
        headers = self.spec.headers.copy()
        if self.session_api_key:
            headers["X-Session-API-Key"] = self.session_api_key

        # Construct conversations URL
        conversations_url = f"{self.spec.base_url.rstrip('/')}/conversations"

        # Convert conversation info to serializable format
        conversation_data = conversation_info.model_dump(mode="json")

        # Retry logic
        response = None
        for attempt in range(self.spec.num_retries + 1):
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.request(
                        method="POST",
                        url=conversations_url,
                        json=conversation_data,
                        headers=headers,
                        timeout=30.0,
                    )
                    response.raise_for_status()
                    logger.debug(
                        f"Successfully posted conversation info "
                        f"to webhook {conversations_url}"
                    )
                    return
            except Exception as e:
                logger.warning(
                    f"Conversation webhook post attempt {attempt + 1} failed: {e}"
                )
                if attempt < self.spec.num_retries:
                    await asyncio.sleep(self.spec.retry_delay)
                else:
                    # Log response content for debugging failures
                    response_content = (
                        response.text if response is not None else "No response"
                    )
                    logger.error(
                        f"Failed to post conversation info to webhook "
                        f"{conversations_url} after {self.spec.num_retries + 1} "
                        f"attempts. Response: {response_content}"
                    )


_conversation_service: ConversationService | None = None


def get_default_conversation_service() -> ConversationService:
    global _conversation_service
    if _conversation_service:
        return _conversation_service

    from openhands.agent_server.config import (
        get_default_config,
    )

    config = get_default_config()
    _conversation_service = ConversationService.get_instance(config)
    return _conversation_service
