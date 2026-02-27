"""Conversation router for OpenHands SDK."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response, status
from pydantic import SecretStr

from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.dependencies import get_conversation_service
from openhands.agent_server.models import (
    AskAgentRequest,
    AskAgentResponse,
    ConversationInfo,
    ConversationPage,
    ConversationSortOrder,
    GenerateTitleRequest,
    GenerateTitleResponse,
    SendMessageRequest,
    SetConfirmationPolicyRequest,
    SetSecurityAnalyzerRequest,
    StartConversationRequest,
    Success,
    UpdateConversationRequest,
    UpdateSecretsRequest,
)
from openhands.sdk import LLM, Agent, TextContent
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.workspace import LocalWorkspace
from openhands.tools.preset.default import get_default_tools


conversation_router = APIRouter(prefix="/conversations", tags=["Conversations"])

# Examples

START_CONVERSATION_EXAMPLES = [
    StartConversationRequest(
        agent=Agent(
            llm=LLM(
                usage_id="your-llm-service",
                model="your-model-provider/your-model-name",
                api_key=SecretStr("your-api-key-here"),
            ),
            tools=get_default_tools(enable_browser=True),
        ),
        workspace=LocalWorkspace(working_dir="workspace/project"),
        initial_message=SendMessageRequest(
            role="user", content=[TextContent(text="Flip a coin!")]
        ),
    ).model_dump(exclude_defaults=True, mode="json")
]


# Read methods


@conversation_router.get("/search")
async def search_conversations(
    page_id: Annotated[
        str | None,
        Query(title="Optional next_page_id from the previously returned page"),
    ] = None,
    limit: Annotated[
        int,
        Query(title="The max number of results in the page", gt=0, lte=100),
    ] = 100,
    status: Annotated[
        ConversationExecutionStatus | None,
        Query(title="Optional filter by conversation execution status"),
    ] = None,
    sort_order: Annotated[
        ConversationSortOrder,
        Query(title="Sort order for conversations"),
    ] = ConversationSortOrder.CREATED_AT_DESC,
    conversation_service: ConversationService = Depends(get_conversation_service),
) -> ConversationPage:
    """Search / List conversations"""
    assert limit > 0
    assert limit <= 100
    return await conversation_service.search_conversations(
        page_id, limit, status, sort_order
    )


@conversation_router.get("/count")
async def count_conversations(
    status: Annotated[
        ConversationExecutionStatus | None,
        Query(title="Optional filter by conversation execution status"),
    ] = None,
    conversation_service: ConversationService = Depends(get_conversation_service),
) -> int:
    """Count conversations matching the given filters"""
    count = await conversation_service.count_conversations(status)
    return count


@conversation_router.get(
    "/{conversation_id}", responses={404: {"description": "Item not found"}}
)
async def get_conversation(
    conversation_id: UUID,
    conversation_service: ConversationService = Depends(get_conversation_service),
) -> ConversationInfo:
    """Given an id, get a conversation"""
    conversation = await conversation_service.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return conversation


@conversation_router.get("")
async def batch_get_conversations(
    ids: Annotated[list[UUID], Query()],
    conversation_service: ConversationService = Depends(get_conversation_service),
) -> list[ConversationInfo | None]:
    """Get a batch of conversations given their ids, returning null for
    any missing item"""
    assert len(ids) < 100
    conversations = await conversation_service.batch_get_conversations(ids)
    return conversations


# Write Methods


@conversation_router.post("")
async def start_conversation(
    request: Annotated[
        StartConversationRequest, Body(examples=START_CONVERSATION_EXAMPLES)
    ],
    response: Response,
    conversation_service: ConversationService = Depends(get_conversation_service),
) -> ConversationInfo:
    """Start a conversation in the local environment."""
    info, is_new = await conversation_service.start_conversation(request)
    response.status_code = status.HTTP_201_CREATED if is_new else status.HTTP_200_OK
    return info


@conversation_router.post(
    "/{conversation_id}/pause", responses={404: {"description": "Item not found"}}
)
async def pause_conversation(
    conversation_id: UUID,
    conversation_service: ConversationService = Depends(get_conversation_service),
) -> Success:
    """Pause a conversation, allowing it to be resumed later."""
    paused = await conversation_service.pause_conversation(conversation_id)
    if not paused:
        raise HTTPException(status.HTTP_400_BAD_REQUEST)
    return Success()


@conversation_router.delete(
    "/{conversation_id}", responses={404: {"description": "Item not found"}}
)
async def delete_conversation(
    conversation_id: UUID,
    conversation_service: ConversationService = Depends(get_conversation_service),
) -> Success:
    """Permanently delete a conversation."""
    deleted = await conversation_service.delete_conversation(conversation_id)
    if not deleted:
        raise HTTPException(status.HTTP_400_BAD_REQUEST)
    return Success()


@conversation_router.post(
    "/{conversation_id}/run",
    responses={
        404: {"description": "Item not found"},
        409: {"description": "Conversation is already running"},
    },
)
async def run_conversation(
    conversation_id: UUID,
    conversation_service: ConversationService = Depends(get_conversation_service),
) -> Success:
    """Start running the conversation in the background."""
    event_service = await conversation_service.get_event_service(conversation_id)
    if event_service is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)

    try:
        await event_service.run()
    except ValueError as e:
        if str(e) == "conversation_already_running":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Conversation already running. Wait for completion or pause first."
                ),
            )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    return Success()


@conversation_router.post(
    "/{conversation_id}/secrets", responses={404: {"description": "Item not found"}}
)
async def update_conversation_secrets(
    conversation_id: UUID,
    request: UpdateSecretsRequest,
    conversation_service: ConversationService = Depends(get_conversation_service),
) -> Success:
    """Update secrets for a conversation."""
    event_service = await conversation_service.get_event_service(conversation_id)
    if event_service is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    # Strings are valid SecretValue (SecretValue = str | SecretProvider)
    from typing import cast

    from openhands.sdk.conversation.secret_registry import SecretValue

    secrets = cast(dict[str, SecretValue], request.secrets)
    await event_service.update_secrets(secrets)
    return Success()


@conversation_router.post(
    "/{conversation_id}/confirmation_policy",
    responses={404: {"description": "Item not found"}},
)
async def set_conversation_confirmation_policy(
    conversation_id: UUID,
    request: SetConfirmationPolicyRequest,
    conversation_service: ConversationService = Depends(get_conversation_service),
) -> Success:
    """Set the confirmation policy for a conversation."""
    event_service = await conversation_service.get_event_service(conversation_id)
    if event_service is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    await event_service.set_confirmation_policy(request.policy)
    return Success()


@conversation_router.post(
    "/{conversation_id}/security_analyzer",
    responses={404: {"description": "Item not found"}},
)
async def set_conversation_security_analyzer(
    conversation_id: UUID,
    request: SetSecurityAnalyzerRequest,
    conversation_service: ConversationService = Depends(get_conversation_service),
) -> Success:
    """Set the security analyzer for a conversation."""
    event_service = await conversation_service.get_event_service(conversation_id)
    if event_service is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    await event_service.set_security_analyzer(request.security_analyzer)
    return Success()


@conversation_router.patch(
    "/{conversation_id}", responses={404: {"description": "Item not found"}}
)
async def update_conversation(
    conversation_id: UUID,
    request: UpdateConversationRequest,
    conversation_service: ConversationService = Depends(get_conversation_service),
) -> Success:
    """Update conversation metadata.

    This endpoint allows updating conversation details like title.
    """
    updated = await conversation_service.update_conversation(conversation_id, request)
    if not updated:
        return Success(success=False)
    return Success()


@conversation_router.post(
    "/{conversation_id}/generate_title",
    responses={404: {"description": "Item not found"}},
)
async def generate_conversation_title(
    conversation_id: UUID,
    request: GenerateTitleRequest,
    conversation_service: ConversationService = Depends(get_conversation_service),
) -> GenerateTitleResponse:
    """Generate a title for the conversation using LLM."""
    title = await conversation_service.generate_conversation_title(
        conversation_id, request.max_length, request.llm
    )
    if title is None:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR)
    return GenerateTitleResponse(title=title)


@conversation_router.post(
    "/{conversation_id}/ask_agent",
    responses={404: {"description": "Item not found"}},
)
async def ask_agent(
    conversation_id: UUID,
    request: AskAgentRequest,
    conversation_service: ConversationService = Depends(get_conversation_service),
) -> AskAgentResponse:
    """Ask the agent a simple question without affecting conversation state."""
    response = await conversation_service.ask_agent(conversation_id, request.question)
    if response is None:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR)
    return AskAgentResponse(response=response)


@conversation_router.post(
    "/{conversation_id}/condense",
    responses={404: {"description": "Item not found"}},
)
async def condense_conversation(
    conversation_id: UUID,
    conversation_service: ConversationService = Depends(get_conversation_service),
) -> Success:
    """Force condensation of the conversation history."""
    success = await conversation_service.condense(conversation_id)
    if not success:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    return Success()
