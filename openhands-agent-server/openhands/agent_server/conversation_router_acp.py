"""ACP-capable conversation routes for the schema-sensitive endpoints."""

# Deprecated REST contract: all /api/acp/conversations routes were deprecated
# in v1.22.0 and are scheduled for removal in v1.27.0. The standard
# FastAPI/OpenAPI deprecation marker for routes is ``deprecated=True`` on each
# route decorator; keep matching docstring notices for CI deprecation checks.

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response, status
from pydantic import SecretStr

from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.dependencies import get_conversation_service
from openhands.agent_server.models import (
    INCLUDE_SKILLS_PARAM_TITLE,
    ACPConversationInfo,
    ACPConversationPage,
    ConversationSortOrder,
    SendMessageRequest,
    StartACPConversationRequest,
    trim_conversation_response_skills,
)
from openhands.sdk import LLM, Agent, TextContent
from openhands.sdk.agent.acp_agent import ACPAgent
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.workspace import LocalWorkspace
from openhands.tools.preset.default import get_default_tools


conversation_router_acp = APIRouter(
    prefix="/acp/conversations",
    tags=["ACP Conversations"],
)

START_ACP_CONVERSATION_EXAMPLES = [
    StartACPConversationRequest(
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
    ).model_dump(exclude_defaults=True, mode="json"),
    StartACPConversationRequest(
        agent=ACPAgent(acp_command=["npx", "-y", "claude-agent-acp"]),
        workspace=LocalWorkspace(working_dir="workspace/project"),
        initial_message=SendMessageRequest(
            role="user",
            content=[TextContent(text="Inspect the repository and summarize it.")],
        ),
    ).model_dump(exclude_defaults=True, mode="json"),
]


@conversation_router_acp.get("/search", deprecated=True)
async def search_acp_conversations(
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
) -> ACPConversationPage:
    """Search conversations using the ACP-capable contract.

    Deprecated since v1.22.0 and scheduled for removal in v1.27.0.
    Use ``/api/conversations/search`` instead.
    """
    assert limit > 0
    assert limit <= 100
    page = await conversation_service.search_acp_conversations(
        page_id, limit, status, sort_order
    )
    if not include_skills:
        page = page.model_copy(
            update={
                "items": [
                    trim_conversation_response_skills(item) for item in page.items
                ]
            }
        )
    return page


@conversation_router_acp.get("/count", deprecated=True)
async def count_acp_conversations(
    status: Annotated[
        ConversationExecutionStatus | None,
        Query(title="Optional filter by conversation execution status"),
    ] = None,
    conversation_service: ConversationService = Depends(get_conversation_service),
) -> int:
    """Count conversations using the ACP-capable contract.

    Deprecated since v1.22.0 and scheduled for removal in v1.27.0.
    Use ``/api/conversations/count`` instead.
    """
    return await conversation_service.count_conversations(status)


@conversation_router_acp.get(
    "/{conversation_id}",
    responses={404: {"description": "Item not found"}},
    deprecated=True,
)
async def get_acp_conversation(
    conversation_id: UUID,
    include_skills: Annotated[bool, Query(title=INCLUDE_SKILLS_PARAM_TITLE)] = False,
    conversation_service: ConversationService = Depends(get_conversation_service),
) -> ACPConversationInfo:
    """Get a conversation using the ACP-capable contract.

    Deprecated since v1.22.0 and scheduled for removal in v1.27.0.
    Use ``/api/conversations/{conversation_id}`` instead.
    """
    conversation = await conversation_service.get_acp_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    if not include_skills:
        conversation = trim_conversation_response_skills(conversation)
    return conversation


@conversation_router_acp.get("", deprecated=True)
async def batch_get_acp_conversations(
    ids: Annotated[list[UUID], Query()],
    include_skills: Annotated[bool, Query(title=INCLUDE_SKILLS_PARAM_TITLE)] = False,
    conversation_service: ConversationService = Depends(get_conversation_service),
) -> list[ACPConversationInfo | None]:
    """Batch get conversations using the ACP-capable contract.

    Deprecated since v1.22.0 and scheduled for removal in v1.27.0.
    Use ``/api/conversations`` instead.
    """
    assert len(ids) < 100
    conversations = await conversation_service.batch_get_acp_conversations(ids)
    if not include_skills:
        return [
            trim_conversation_response_skills(c) if c is not None else None
            for c in conversations
        ]
    return conversations


@conversation_router_acp.post("", deprecated=True)
async def start_acp_conversation(
    request: Annotated[
        StartACPConversationRequest,
        Body(examples=START_ACP_CONVERSATION_EXAMPLES),
    ],
    response: Response,
    include_skills: Annotated[bool, Query(title=INCLUDE_SKILLS_PARAM_TITLE)] = False,
    conversation_service: ConversationService = Depends(get_conversation_service),
) -> ACPConversationInfo:
    """Start a conversation using the ACP-capable contract.

    Deprecated since v1.22.0 and scheduled for removal in v1.27.0.
    Use ``/api/conversations`` instead; it now accepts ACP agents and
    ``agent_settings`` payloads.
    """
    info, is_new = await conversation_service.start_acp_conversation(request)
    response.status_code = status.HTTP_201_CREATED if is_new else status.HTTP_200_OK
    if not include_skills:
        info = trim_conversation_response_skills(info)
    return info
