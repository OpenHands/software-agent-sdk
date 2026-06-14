"""Conversation router for OpenHands SDK."""

from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Body,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from pydantic import SecretStr

from openhands.agent_server._secrets_exposure import (
    decrypt_incoming_llm_secrets,
    get_cipher,
)
from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.dependencies import get_conversation_service
from openhands.agent_server.models import (
    INCLUDE_SKILLS_PARAM_TITLE,
    AgentResponseResult,
    AskAgentRequest,
    AskAgentResponse,
    ConversationInfo,
    ConversationPage,
    ConversationSortOrder,
    ForkConversationRequest,
    SendMessageRequest,
    SetConfirmationPolicyRequest,
    SetSecurityAnalyzerRequest,
    StartConversationRequest,
    Success,
    UpdateConversationRequest,
    UpdateSecretsRequest,
    trim_conversation_response_skills,
)
from openhands.sdk import LLM, Agent, TextContent
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.tool.client_tool import ClientToolRegistrationError
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
    include_skills: Annotated[bool, Query(title=INCLUDE_SKILLS_PARAM_TITLE)] = False,
    conversation_service: ConversationService = Depends(get_conversation_service),
) -> ConversationPage:
    """Search / List conversations"""
    assert limit > 0
    assert limit <= 100
    page = await conversation_service.search_conversations(
        page_id, limit, status, sort_order
    )
    if not include_skills:
        # ``model_copy`` rather than in-place mutation so we never
        # write back into whatever the upstream service handed us
        # (matters for services that cache their return value,
        # including the ``AsyncMock`` used in route tests).
        page = page.model_copy(
            update={
                "items": [
                    trim_conversation_response_skills(item) for item in page.items
                ]
            }
        )
    return page


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
    include_skills: Annotated[bool, Query(title=INCLUDE_SKILLS_PARAM_TITLE)] = False,
    conversation_service: ConversationService = Depends(get_conversation_service),
) -> ConversationInfo:
    """Given an id, get a conversation"""
    conversation = await conversation_service.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    if not include_skills:
        conversation = trim_conversation_response_skills(conversation)
    return conversation


@conversation_router.get(
    "/{conversation_id}/agent_final_response",
    responses={404: {"description": "Conversation not found"}},
)
async def get_conversation_agent_final_response(
    conversation_id: UUID,
    conversation_service: ConversationService = Depends(get_conversation_service),
) -> AgentResponseResult:
    """Get the agent's final response for a conversation.

    Returns the text of the last agent finish message (FinishAction) or
    the last agent text response (MessageEvent). Returns an empty string
    if the agent has not produced a final response yet.
    """
    event_service = await conversation_service.get_event_service(conversation_id)
    if event_service is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    response = await event_service.get_agent_final_response()
    return AgentResponseResult(response=response)


@conversation_router.get("")
async def batch_get_conversations(
    ids: Annotated[list[UUID], Query()],
    include_skills: Annotated[bool, Query(title=INCLUDE_SKILLS_PARAM_TITLE)] = False,
    conversation_service: ConversationService = Depends(get_conversation_service),
) -> list[ConversationInfo | None]:
    """Get a batch of conversations given their ids, returning null for
    any missing item"""
    assert len(ids) < 100
    conversations = await conversation_service.batch_get_conversations(ids)
    if not include_skills:
        return [
            trim_conversation_response_skills(c) if c is not None else None
            for c in conversations
        ]
    return conversations


# Write Methods


@conversation_router.post("")
async def start_conversation(
    request: Annotated[
        StartConversationRequest, Body(examples=START_CONVERSATION_EXAMPLES)
    ],
    response: Response,
    include_skills: Annotated[bool, Query(title=INCLUDE_SKILLS_PARAM_TITLE)] = False,
    conversation_service: ConversationService = Depends(get_conversation_service),
) -> ConversationInfo:
    """Start a conversation in the local environment."""
    try:
        info, is_new = await conversation_service.start_conversation(request)
    except ClientToolRegistrationError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)
        ) from e
    response.status_code = status.HTTP_201_CREATED if is_new else status.HTTP_200_OK
    if not include_skills:
        info = trim_conversation_response_skills(info)
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


@conversation_router.post(
    "/{conversation_id}/interrupt",
    responses={404: {"description": "Item not found"}},
)
async def interrupt_conversation(
    conversation_id: UUID,
    conversation_service: ConversationService = Depends(get_conversation_service),
) -> Success:
    """Immediately interrupt a running conversation.

    Unlike ``/pause``, which waits for the current LLM call to finish,
    ``/interrupt`` cancels the in-flight request so the effect is instant.
    The conversation transitions to *paused* and can be resumed later.
    """
    interrupted = await conversation_service.interrupt_conversation(conversation_id)
    if not interrupted:
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


@conversation_router.post(
    "/{conversation_id}/switch_profile",
    responses={
        400: {"description": "Invalid or corrupted profile"},
        404: {"description": "Conversation or profile not found"},
    },
)
async def switch_conversation_profile(
    conversation_id: UUID,
    profile_name: str = Body(..., embed=True),
    conversation_service: ConversationService = Depends(get_conversation_service),
) -> Success:
    """Switch the conversation's LLM profile to a named profile."""
    event_service = await conversation_service.get_event_service(conversation_id)
    if event_service is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    conversation = event_service.get_conversation()
    try:
        conversation.switch_profile(profile_name)
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Profile '{profile_name}' not found",
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    return Success()


@conversation_router.post(
    "/{conversation_id}/switch_llm",
    responses={404: {"description": "Conversation not found"}},
)
async def switch_conversation_llm(
    request: Request,
    conversation_id: UUID,
    llm: LLM = Body(..., embed=True),  # noqa: B008
    conversation_service: ConversationService = Depends(get_conversation_service),
) -> Success:
    """Swap the conversation's LLM to a caller-supplied object.

    Used by app-servers that own the LLM directly and don't push profiles
    to the agent-server's filesystem (see #3017).
    """
    event_service = await conversation_service.get_event_service(conversation_id)
    if event_service is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    conversation = event_service.get_conversation()
    cipher = get_cipher(request)
    if cipher is not None:
        llm = decrypt_incoming_llm_secrets(llm, cipher)
    conversation.switch_llm(llm)
    return Success()


@conversation_router.post(
    "/{conversation_id}/switch_acp_model",
    responses={
        400: {"description": "Agent is not ACP, or provider can't switch models"},
        404: {"description": "Conversation not found"},
        409: {"description": "ACP session not initialized yet"},
        504: {"description": "ACP server did not answer the model switch in time"},
    },
)
async def switch_conversation_acp_model(
    conversation_id: UUID,
    model: str = Body(..., embed=True),
    conversation_service: ConversationService = Depends(get_conversation_service),
) -> Success:
    """Switch the model of a running ACP conversation, mid-conversation.

    Issues the provider's protocol-level model-selection call to the ACP
    subprocess so the new model applies to subsequent turns without losing
    context. Only valid for ACP conversations whose provider supports runtime
    switching.
    """
    event_service = await conversation_service.get_event_service(conversation_id)
    if event_service is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    try:
        await event_service.switch_acp_model(model)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except TimeoutError as e:
        # The bounded model-selection round-trip expired. The ACP server is
        # wedged/slow rather than rejecting the request, so surface a 504 instead
        # of an opaque 500.
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=str(e),
        )
    except RuntimeError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        )
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


@conversation_router.post(
    "/{conversation_id}/fork",
    responses={
        201: {"description": "Forked conversation created"},
        404: {"description": "Source conversation not found"},
        409: {"description": "Fork ID already in use"},
    },
    status_code=status.HTTP_201_CREATED,
)
async def fork_conversation(
    conversation_id: UUID,
    request: Annotated[ForkConversationRequest, Body()] = ForkConversationRequest(),  # noqa: B008
    include_skills: Annotated[bool, Query(title=INCLUDE_SKILLS_PARAM_TITLE)] = False,
    conversation_service: ConversationService = Depends(get_conversation_service),
) -> ConversationInfo:
    """Fork a conversation, deep-copying its event history.

    The fork starts in ``idle`` status with a fresh event loop.
    Calling ``run`` on the fork resumes from the copied state, meaning
    the agent has full event memory of the source conversation.
    """
    try:
        info = await conversation_service.fork_conversation(
            conversation_id,
            fork_id=request.id,
            title=request.title,
            tags=request.tags if request.tags is not None else None,
            reset_metrics=request.reset_metrics,
        )
    except ValueError as exc:
        if "already exists" in str(exc):
            raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        raise
    if info is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail="Source conversation not found",
        )
    if not include_skills:
        info = trim_conversation_response_skills(info)
    return info
