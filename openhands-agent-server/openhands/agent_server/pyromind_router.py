"""Pyromind knowledge-base retrieval router.

This router wraps the lower-level conversation service, assembling the
system prompt, tools, and workspace on the server side so that the
frontend only needs to pass minimal configuration fields.
"""

import logging
import os
import uuid
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import AliasChoices, BaseModel, Field, SecretStr, field_validator

from openhands.agent_server.conversation_service import (
    ConversationForkAtEventConflictError,
    ConversationForkAtEventSourceNotFoundError,
    ConversationForkAtEventTargetNotFoundError,
    ConversationService,
)
from openhands.agent_server.dependencies import (
    _get_validation_cluster_header,
    get_conversation_service,
    get_current_user_id,
    get_event_service,
    resolve_pyromind_auth_token,
)
from openhands.agent_server.event_service import EventService
from openhands.agent_server.models import ConversationInfo, Success
from openhands.agent_server.pyromind_auth import (
    PYROMIND_AUTH_COOKIE_NAME,
    CurrentLoginUser,
    bind_debug_current_login_user_to_conversation,
    get_debug_current_login_user_by_conversation,
    is_dev,
    parse_auth_token_from_cookie_header,
)
from openhands.agent_server.pyromind_constants import (
    PYROMIND_APP_TAG_KEY,
    PYROMIND_APP_TAG_VALUE,
    PYROMIND_RUNTIME_CONTRACT,
    PYROMIND_TERMINAL_PARAMS,
)
from openhands.agent_server.run_workflow_callback import (
    RunWorkflowCallbackResult,
    deliver_run_workflow_status,
)
from openhands.agent_server.workflow_canvas_models import WorkflowCanvasEventSnapshot
from openhands.agent_server.workflow_canvas_store import (
    FileWorkflowCanvasStore,
    WorkflowCanvasEventSnapshotNotFoundError,
    WorkflowCanvasStoreError,
    WorkflowCanvasVersionNotFoundError,
)
from openhands.sdk import LLM, AgentContext, TextContent, Tool, register_tool
from openhands.sdk.conversation.request import (
    StartConversationRequest,
)
from openhands.sdk.llm.message import Message
from openhands.sdk.secret import SecretSource, SecretValue, StaticSecret
from openhands.sdk.security.confirmation_policy import ConfirmRisky
from openhands.sdk.security.defense_in_depth import PatternSecurityAnalyzer
from openhands.sdk.skills import Skill, load_skills_from_dir
from openhands.sdk.tool.builtins import SkillsListTool, SkillsReadTool
from openhands.sdk.workspace import LocalWorkspace
from openhands.tools.preset.codex import get_codex_agent
from openhands.tools.preset.default import register_default_tools
from openhands.tools.pyromind_cleaning import RunDatasetCleaningTool
from openhands.tools.pyromind_dataset import (
    PreviewDatasetTool,
    UploadFileToPyromindTool,
)
from openhands.tools.pyromind_dataset.definition import (
    PYROMIND_STORAGE_AUTH_COOKIE_SECRET,
    PYROMIND_STORAGE_HEADERS_STATE_KEY,
)
from openhands.tools.pyromind_debug import get_debug_result_broker
from openhands.tools.pyromind_remote_dataset import PreviewRemoteDatasetTool
from openhands.tools.utils import PUBLIC_READ_ALIASES
from openhands.tools.workflow import (
    RunWorkflowTool,
    ValidateWorkflowDslTool,
)
from openhands.tools.workflow.definition import WORKFLOW_RELATIVE_PATH
from openhands.tools.workflow.dsl_to_xyflow import convert_xyflow_to_dsl
from openhands.tools.workflow.validate_workflow_dsl import (
    PYROMIND_VALIDATE_AUTH_COOKIE_SECRET,
    PYROMIND_VALIDATE_HEADERS_STATE_KEY,
)
from openhands.tools.workflow_debug import WorkflowDebugTool


PYROMIND_AUTH_TOKEN_SECRET = "auth_token"
_OPENAI_CHAT_COMPLETIONS_SUFFIX = "/chat/completions"


logger = logging.getLogger(__name__)

# Register skill-runtime tools at module level so persisted conversations
# that reference them can resolve the tools on resume after a server restart.
register_tool(SkillsListTool.__name__, SkillsListTool)
register_tool(SkillsReadTool.__name__, SkillsReadTool)

_PUBLIC_READ_ALIAS_NAMES = {alias: alias for alias, _, _, _ in PUBLIC_READ_ALIASES}
_KNOWLEDGE_ALIAS = _PUBLIC_READ_ALIAS_NAMES["knowledge"]
_SKILLS_ALIAS = _PUBLIC_READ_ALIAS_NAMES[".agents/skills"]

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Default knowledge base path — resolved relative to this file's package root;
# can be overridden via the PYROMIND_KNOWLEDGE_BASE_PATH environment variable.
_DEFAULT_KNOWLEDGE_BASE_PATH = os.environ.get(
    "PYROMIND_KNOWLEDGE_BASE_PATH",
    str(Path(__file__).resolve().parents[3] / "knowledge"),
)

# Default skills path — .agents/skills/ at the repo root
_DEFAULT_SKILLS_PATH = os.environ.get(
    "PYROMIND_SKILLS_PATH",
    str(Path(__file__).resolve().parents[3] / ".agents" / "skills"),
)

# Only load these skills for Pyromind (avoids loading unrelated SDK skills)
_PYROMIND_SKILL_NAMES = [
    "generate-workflow-dsl",
    "debug-workflow",
    "data-cleaning",
    "data-preparation",
]
_PYROMIND_VALIDATE_AUTHORIZATION_SECRET = "PYROMIND_VALIDATE_AUTHORIZATION"
_PYROMIND_VALIDATE_FORWARD_HEADERS = ("x-cluster", "accept-language")
_PYROMIND_DEBUG_URL_TIMEOUT_SECONDS = 30.0
_PYROMIND_DEBUG_RESPONSE_BODY_LIMIT = 20000

# Knowledge-base retrieval guidance layered on top of the codex base prompt via
# get_codex_agent(custom_instructions=...). Match the Codex flow: prefer the
# matching skill first; then use skill/runtime reads for skill docs; only fall
# back to grep + file_editor for free-form knowledge-base lookups.
PYROMIND_KB_INSTRUCTIONS = """\
The Pyromind platform knowledge base is available through the read-only logical
path `{knowledge_alias}/`. Do not use or request its host filesystem path.

Knowledge base layout:
- basic/: platform basics
- jupyterlab/: JupyterLab and script-based training
- sdk/: Python SDK and script-based workflow APIs
- studio/: Studio workflow documentation
- nodes/<NodeType>/<NodeType>.md: node parameters, I/O, and ports
- dataset_processing_workflow.py: workflow DSL example

The shared skill documents are available through the read-only logical path
`{skills_alias}/`. Do not use or request their host filesystem path.

Skill usage rules:
- A conversation may already contain a workflow at
  `public_data/workflow_canvas/workflow.py`. Before asking for information or
  answering any request that may inspect, modify, validate, test, or run the
  current workflow, read that file in full with `file_editor`. Apply this rule
  especially to short contextual requests such as "看数据", "改一下", or
  "换个模型". Reuse dataset/model identifiers and topology already present in
  the file instead of asking the user to provide them again.
- Immediately after that single workflow read, invoke the matching listed skill.
  Then read only the exact skill resource that the skill requires. For a local
  workflow edit, do not inspect general `knowledge/` before invoking the skill,
  and do not inspect it afterward unless the skill explicitly requires it.
- For requests that do not involve a current workflow, invoke a matching listed
  skill before searching the knowledge base.
- Data processing routing (`data-cleaning` vs `data-preparation`):
  Analyze the task semantics to decide which skill to invoke.
  * Use `data-cleaning` when the task is about FORMAT/STRUCTURE transformation:
    converting data to messages or DPO format, field renaming/mapping,
    dialogue structure parsing, format validation, structural deduplication.
    Also suitable for simple content filtering (regex, keyword, length) when
    platform reliability (checkpoint resume, error isolation, report) matters.
    Key signal: format conversion is the primary goal, or simple filtering
    needs reliable execution on large Pyromind Storage datasets.
  * Use `data-preparation` when the task is about CONTENT-level processing:
    - Rule-based content cleaning: word count filtering, language detection,
      MinHash/SimHash dedup, PII removal, toxicity filtering, emoji/HTML
      removal, blocklist, spelling correction.
    - LLM-intelligent processing: generating QA/CoT/summaries, semantic
      quality scoring, text rewriting/refinement, multi-field reasoning.
    Key signal: the data content itself is evaluated, filtered, or transformed.
  * If the user explicitly names a mode ("DataFlow"/"脚本清洗"/"格式转换"),
    follow their choice. If the intent is genuinely ambiguous after analysis,
    ask the user to clarify before invoking either skill.
- Treat any requested node, model, parameter, data, or topology change as a
  `generate-workflow-dsl` request, including phrases such as "换个模型跑一下"
  or "跑下 <model> 的效果". Modify and validate the DSL, then stop; do not
  invoke `debug-workflow` or `workflow_debug` in the same turn. Use
  `debug-workflow` only for an explicit test/debug request that contains no
  configuration change.
- For skill document lookup, use `skills_list` / `skills_read` style access,
  not grep or directory scanning.
- For skill-linked resources, read the exact relative path from the skill root;
  do not manually search `references/`, `scripts/`, or `assets/` with grep.
- Use `grep` and `file_editor` only as a fallback when the request is not skill-
  addressed or when you need a free-form knowledge-base article.

__PYROMIND_RUNTIME_CONTRACT__

Create and edit the workflow DSL at the relative path
`public_data/workflow_canvas/workflow.py` from the current working directory.
Prefer `apply_patch` for workflow changes. When using `apply_patch`, always
use the full relative path in the patch header, e.g.
`*** Add File: public_data/workflow_canvas/workflow.py` or
`*** Update File: public_data/workflow_canvas/workflow.py` — never use the
bare name `workflow.py`. If you use `file_editor` for this file, set its
`path` to `public_data/workflow_canvas/workflow.py`; the runtime resolves
workspace-relative paths to host-absolute paths. Do not hand-author long
absolute paths, and do not use `/workspace/...` or
`workspace/conversations/...` as a `file_editor.path` for the workflow file.
After creating or modifying the workflow file, stop normally; the server sends
the workflow to the frontend once the run finishes. Do not say the workflow has
been generated unless a tool call actually created or modified the workflow file.

- Invoke listed skills with `invoke_skill`. Do not invoke a workflow-generation
  skill for an article lookup alone.
- For knowledge-base or skill-document requests that are not skill-linked,
  prefer `grep` and `file_editor` with the logical `{knowledge_alias}/` or
  `{skills_alias}/` path. Do not use `apply_patch` to modify public knowledge or
  skill documents. Open matched files with `file_editor` before
answering or editing the workflow file; never infer APIs or operational facts from
filenames or directory listings.
- For "查看知识库有哪些信息" or similar inventory requests, use one `grep`
  call per top-level directory (`basic`, `jupyterlab`, `sdk`, `studio`, and
  `nodes`) with `include="*.mdx"` and pattern `^title:|^# `; do not use pattern
  `.` or `^` because those return document bodies instead of an index.
- For requests to output, summarize, or explain specific knowledge-base
  articles, first search with `grep` under `knowledge/<subdirectory>` using
  `include="*.mdx"`, then open only the matched files with `file_editor` using
  the same logical path. Use `*.md` only when an `.mdx` search has no matches.
- For a Pyromind knowledge-base answer:
  1. Split the user's request into explicit subquestions.
  2. From files you actually opened, make a short checklist of directly relevant
     headings, tables, warnings, alternatives, and ordered steps for each subquestion.
  3. Before answering, mark every checklist item as covered or intentionally omitted.
     Omit an item only when it is tangential, and do not omit a peer item from the
     same list or table without a reason.
  Do not show this internal checklist unless the user asks for sources.
- For workflow generation, use the matching skill and its exact resources.
  Do not consult `knowledge/` before validation. Only when validation identifies
  a specific platform contract missing from those resources may you make one
  targeted knowledge-base lookup for that error.
""".replace("__PYROMIND_RUNTIME_CONTRACT__", PYROMIND_RUNTIME_CONTRACT.rstrip())


# ---------------------------------------------------------------------------
# Skill loading utilities
# ---------------------------------------------------------------------------


def _load_agent_skills(
    skills_path: str, allow_list: list[str] | None = None
) -> list[Skill]:
    """Load AgentSkills-format skills as :class:`Skill` objects.

    Returns real ``Skill`` objects (not prompt text) so they can be placed on
    an :class:`AgentContext`. This is what makes the SDK auto-attach
    ``InvokeSkillTool`` — passing skills as prompt text alone advertises
    ``invoke_skill(...)`` without ever attaching the tool, so the model cannot
    call it and falls back to grep.

    When *allow_list* is provided, only skills whose name is in the list are
    returned.
    """
    skills_dir = Path(skills_path)
    if not skills_dir.is_dir():
        logger.warning(f"Skills directory not found: {skills_path}")
        return []

    # load_skills_from_dir returns (repo_skills, knowledge_skills, agent_skills);
    # AgentSkills-format SKILL.md directories land in the third dict.
    _, _, agent_skills = load_skills_from_dir(skills_dir)

    selected: list[Skill] = []
    for name, skill in sorted(agent_skills.items()):
        if allow_list and name not in allow_list:
            continue
        selected.append(skill)
        logger.info(f"Loaded skill: {name}")

    return selected


# ---------------------------------------------------------------------------
# Tool loading utilities
# ---------------------------------------------------------------------------


def _build_workflow_validation_tool(
    http_request: Request,
    extra: dict[str, Any],
) -> tuple[Tool, dict[str, SecretSource]]:
    headers = {
        name: value
        for name in _PYROMIND_VALIDATE_FORWARD_HEADERS
        if name != "x-cluster" and (value := http_request.headers.get(name))
    }
    if cluster := _get_validation_cluster_header(http_request, extra):
        headers["x-cluster"] = cluster

    params: dict[str, Any] = {}
    endpoint_url = extra.get("workflow_validation_endpoint_url")
    if isinstance(endpoint_url, str) and endpoint_url:
        params["endpoint_url"] = endpoint_url
    if headers:
        params["headers"] = headers

    secrets: dict[str, SecretSource] = {}
    secret_headers: dict[str, str] = {}
    cookie_header = _get_validation_cookie_header(http_request)
    if cookie_header:
        secret_headers["cookie"] = PYROMIND_VALIDATE_AUTH_COOKIE_SECRET
        secrets[PYROMIND_VALIDATE_AUTH_COOKIE_SECRET] = StaticSecret(
            value=SecretStr(cookie_header)
        )

    authorization = http_request.headers.get("authorization")
    if authorization:
        secret_headers["authorization"] = _PYROMIND_VALIDATE_AUTHORIZATION_SECRET
        secrets[_PYROMIND_VALIDATE_AUTHORIZATION_SECRET] = StaticSecret(
            value=SecretStr(authorization)
        )

    if secret_headers:
        params["secret_headers"] = secret_headers

    return Tool(name=ValidateWorkflowDslTool.name, params=params), secrets


def _load_env_to_tools(
    http_request: Request, params: dict[str, Any], secrets: dict[str, SecretSource]
) -> tuple[dict[str, Any], dict[str, SecretSource]]:
    """
    加载通用环境变量
    """
    params = params if params else {}
    secrets = secrets if secrets else {}

    params["current_user"] = _current_user_without_cookie(
        getattr(http_request.state, "current_user", None)
    )
    params["env"] = getattr(http_request.state, "env", None)
    params["cluster"] = getattr(http_request.state, "cluster", None)

    # 拷贝请求头，不包含认证信息，仅复制一般环境信息。
    headers = {
        name: _value
        for name in _PYROMIND_VALIDATE_FORWARD_HEADERS
        if (_value := http_request.headers.get(name))
    }
    if cluster := _get_validation_cluster_header(http_request, {}):
        headers["x-cluster"] = cluster
    headers["request-app"] = "openhands"
    params["headers"] = headers

    return params, secrets


def _current_user_without_cookie(current_user: Any) -> Any:
    if not isinstance(current_user, CurrentLoginUser):
        return current_user
    return current_user.model_copy(update={"cookie": None})


def _load_auth_token(
    http_request: Request, secrets: dict[str, SecretSource]
) -> dict[str, SecretSource]:
    """
    加载通用环境变量
    """
    secrets = secrets if secrets else {}
    if auth_token := resolve_pyromind_auth_token(
        cookies=http_request.cookies,
        cookie_header=http_request.headers.get("cookie"),
    ):
        secrets[PYROMIND_AUTH_TOKEN_SECRET] = StaticSecret(value=SecretStr(auth_token))
    return secrets


def _build_workflow_run_tool(
    http_request: Request,
) -> tuple[Tool, dict[str, SecretSource]]:
    params: dict[str, Any] = {}
    secrets: dict[str, SecretSource] = {}

    # 加载通用属性。
    params, secrets = _load_env_to_tools(
        http_request=http_request, params=params, secrets=secrets
    )

    # 加载用户认证 token。
    secrets = _load_auth_token(http_request=http_request, secrets=secrets)

    # 返回会话级工具参数。
    return Tool(name=RunWorkflowTool.name, params=params), secrets


def _build_workflow_debug_tool(
    http_request: Request,
) -> tuple[Tool, dict[str, SecretSource]]:
    """Build ``workflow_debug`` with the same env/auth params as ``run_workflow``."""
    params: dict[str, Any] = {}
    secrets: dict[str, SecretSource] = {}
    params, secrets = _load_env_to_tools(
        http_request=http_request, params=params, secrets=secrets
    )
    secrets = _load_auth_token(http_request=http_request, secrets=secrets)
    return Tool(name=WorkflowDebugTool.name, params=params), secrets


def _build_pyromind_storage_tools(
    http_request: Request,
    extra: dict[str, Any],
    skills_path: str | Path,
) -> tuple[list[Tool], dict[str, SecretSource]]:
    headers = {
        name: value
        for name in _PYROMIND_VALIDATE_FORWARD_HEADERS
        if name != "x-cluster" and (value := http_request.headers.get(name))
    }
    if cluster := _get_validation_cluster_header(http_request, extra):
        headers["x-cluster"] = cluster

    params: dict[str, Any] = {}
    storage_base_url = extra.get("storage_base_url", extra.get("storage_api_base_url"))
    if isinstance(storage_base_url, str) and storage_base_url:
        params["storage_base_url"] = storage_base_url
    if headers:
        params["headers"] = headers

    secrets: dict[str, SecretSource] = {}
    secret_headers: dict[str, str] = {}
    cookie_header = _get_validation_cookie_header(http_request)
    if cookie_header:
        secret_headers["cookie"] = PYROMIND_STORAGE_AUTH_COOKIE_SECRET
        secrets[PYROMIND_STORAGE_AUTH_COOKIE_SECRET] = StaticSecret(
            value=SecretStr(cookie_header)
        )
    if secret_headers:
        params["secret_headers"] = secret_headers

    cleaning_params, secrets = _load_env_to_tools(
        http_request=http_request,
        params={},
        secrets=secrets,
    )
    secrets = _load_auth_token(http_request=http_request, secrets=secrets)
    cleaning_output_root = extra.get("dataset_cleaning_output_root")
    if isinstance(cleaning_output_root, str) and cleaning_output_root:
        cleaning_params["output_root"] = cleaning_output_root
    cleaning_params["runtime_dir"] = str(
        Path(skills_path) / "data-cleaning" / "scripts"
    )
    if "storage_base_url" in params:
        cleaning_params["storage_base_url"] = params["storage_base_url"]
    if "headers" in params:
        cleaning_params["storage_headers"] = dict(params["headers"])
    if "secret_headers" in params:
        cleaning_params["storage_secret_headers"] = dict(params["secret_headers"])

    return (
        [
            Tool(name=PreviewDatasetTool.name, params=dict(params)),
            Tool(name=UploadFileToPyromindTool.name, params=dict(params)),
            Tool(name=RunDatasetCleaningTool.name, params=cleaning_params),
            Tool(name=PreviewRemoteDatasetTool.name, params={}),
        ],
        secrets,
    )


async def apply_pyromind_validation_context(
    event_service: EventService,
    current_user: CurrentLoginUser | None,
) -> None:
    """Refresh Pyromind portal auth context when a client (re)connects over WebSocket.

    Updates validate cookie secrets, the run_workflow ``auth_token`` secret, and
    forwarded ``x-cluster`` agent state from the current login session.
    """
    if current_user is None:
        return
    if event_service.stored.tags.get(PYROMIND_APP_TAG_KEY) != PYROMIND_APP_TAG_VALUE:
        return

    secrets_update: dict[str, SecretValue] = {}
    if current_user.cookie:
        secrets_update[PYROMIND_VALIDATE_AUTH_COOKIE_SECRET] = current_user.cookie
        secrets_update[PYROMIND_STORAGE_AUTH_COOKIE_SECRET] = current_user.cookie
    if auth_token := parse_auth_token_from_cookie_header(current_user.cookie):
        secrets_update[PYROMIND_AUTH_TOKEN_SECRET] = auth_token
    if secrets_update:
        await event_service.update_secrets(secrets_update)

    if current_user.x_cluster:
        await event_service.update_agent_state(
            {
                PYROMIND_VALIDATE_HEADERS_STATE_KEY: {
                    "x-cluster": current_user.x_cluster
                },
                PYROMIND_STORAGE_HEADERS_STATE_KEY: {
                    "x-cluster": current_user.x_cluster
                },
            }
        )


def _get_validation_cookie_header(http_request: Request) -> str | None:
    current_user = getattr(http_request.state, "current_user", None)
    if isinstance(current_user, CurrentLoginUser) and current_user.cookie:
        return current_user.cookie

    raw_cookie = http_request.headers.get("cookie")
    if raw_cookie:
        return raw_cookie

    if auth_token := resolve_pyromind_auth_token(
        cookies=http_request.cookies,
        cookie_header=http_request.headers.get("cookie"),
    ):
        return f"{PYROMIND_AUTH_COOKIE_NAME}={auth_token}"
    return None


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class PyromindLLMConfig(BaseModel):
    """LLM configuration passed from the frontend."""

    model: str = Field(
        default_factory=lambda: os.environ.get("LLM_MODEL", "gpt-4o"),
    )
    api_key: str | None = Field(
        default_factory=lambda: os.environ.get("OPENAI_API_KEY"),
    )
    base_url: str | None = Field(
        default_factory=lambda: os.environ.get("LLM_BASE_URL"),
    )

    @field_validator("base_url", mode="before")
    @classmethod
    def normalize_base_url(cls, value: Any) -> str | None:
        if not isinstance(value, str):
            return value

        base_url = value.strip().rstrip("/")
        if not base_url:
            return None

        if base_url.endswith(_OPENAI_CHAT_COMPLETIONS_SUFFIX):
            return base_url[: -len(_OPENAI_CHAT_COMPLETIONS_SUFFIX)]

        return base_url


class PyromindCreateConversationRequest(BaseModel):
    """Request body for creating a Pyromind knowledge-base conversation.

    Only essential fields are required; the server assembles the full agent
    configuration (system_prompt, tools, workspace) internally.

    The ``extra`` dict is intentionally open-ended to support future fields
    without breaking the API contract.
    """

    llm: PyromindLLMConfig
    message: str | None = Field(
        default=None,
        description="Optional initial user message to start the conversation.",
    )
    workflow_xyflow: dict[str, Any] | None = Field(
        default=None,
        validation_alias=AliasChoices("workflow_xyflow", "workflowXyflow"),
        description=(
            "Optional xyflow JSON of the workflow currently on the canvas. "
            "When provided, the server converts it to workflow DSL before "
            "seeding the workflow file."
        ),
    )
    extra: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Extensible JSON object for additional configuration. "
            "Supported keys: "
            "'knowledge_base_path' (str) - override default KB path; "
            "'language' (str) - preferred response language; "
            "'custom_instructions' (str) - extra instructions appended to prompt."
        ),
    )

    model_config = {"populate_by_name": True, "extra": "forbid"}


class PyromindSendMessageRequest(BaseModel):
    """Request body for sending a message in a Pyromind conversation.

    Unlike the generic ``POST /api/conversations/{id}/events`` endpoint, this
    also accepts the workflow currently shown on the canvas, so that the workflow file
    is synced to what the user actually sees *before* the agent acts on this
    message.
    """

    text: str = Field(description="The user's message text.")
    workflow_xyflow: dict[str, Any] | None = Field(
        default=None,
        validation_alias=AliasChoices("workflow_xyflow", "workflowXyflow"),
        description=(
            "xyflow JSON of the workflow currently on the canvas. When "
            "provided, the server converts it to workflow DSL before syncing "
            "the workflow file and saving the input snapshot."
        ),
    )
    run: bool = Field(
        default=True,
        description="Whether the agent loop should run after this message.",
    )

    model_config = {"populate_by_name": True, "extra": "forbid"}


class PyromindForkAtEventRequest(BaseModel):
    event_id: str = Field(
        alias="eventId",
        min_length=1,
        description="Workflow snapshot event id to branch from.",
    )
    title: str | None = Field(
        default=None,
        max_length=200,
        description="Optional title for the forked conversation.",
    )

    model_config = {"populate_by_name": True}


class PyromindForkAtEventResponse(BaseModel):
    conversation_id: UUID = Field(alias="conversationId")
    source_conversation_id: UUID = Field(alias="sourceConversationId")
    forked_at_event_id: str = Field(alias="forkedAtEventId")
    workflow_version_id: str = Field(alias="workflowVersionId")
    conversation: ConversationInfo

    model_config = {"populate_by_name": True}


class PyromindWorkflowRollbackRequest(BaseModel):
    event_id: str = Field(
        alias="eventId",
        min_length=1,
        description="Workflow snapshot event id to restore.",
    )
    run: bool = Field(
        default=False,
        description="Whether the agent loop should run after the correction context.",
    )
    message: str | None = Field(
        default=None,
        max_length=2000,
        description=(
            "Optional correction message to append after applying the snapshot."
        ),
    )

    model_config = {"populate_by_name": True, "extra": "forbid"}


class PyromindWorkflowRollbackResponse(BaseModel):
    conversation_id: UUID = Field(alias="conversationId")
    rolled_back_to_event_id: str | None = Field(
        default=None, alias="rolledBackToEventId"
    )
    workflow_version_id: str | None = Field(default=None, alias="workflowVersionId")
    snapshot_role: Literal["in", "out"] | None = Field(
        default=None, alias="snapshotRole"
    )
    workflow_file_action: Literal["updated", "removed"] | None = Field(
        default=None, alias="workflowFileAction"
    )
    correction_message: str | None = Field(default=None, alias="correctionMessage")
    snapshot: WorkflowCanvasEventSnapshot | None = None

    model_config = {"populate_by_name": True}


class PyromindDebugCallbackRequest(BaseModel):
    """Webhook payload the debug platform posts when an async run finishes."""

    task_id: str = Field(description="The task id returned when the run was submitted.")
    status: Literal["passed", "failed"] = Field(description="Outcome of the run.")
    error_log: str | None = Field(
        default=None, description="Runtime error output when status='failed'."
    )


class PyromindWorkflowCallbackRequest(BaseModel):
    """Temporary webhook payload simulating a Kafka run_workflow status message."""

    task_id: str = Field(description="Platform task id from studio.create().")
    status: str = Field(
        description="Raw workflow status from the platform or Kafka message."
    )
    conversation_id: str | None = Field(
        default=None,
        description=(
            "Conversation to resume. Dataset cleaning callbacks may omit this "
            "because task association is persisted at submission time."
        ),
    )
    error_log: str | None = Field(
        default=None,
        description="Runtime error log when status is Failed or Error.",
    )
    auto_run: bool = Field(
        default=True,
        description="Restart the agent on the target conversation after delivery.",
    )
    from_workflow_debug: bool = Field(
        default=False,
        description=(
            "True when simulating a workflow_debug (test) terminal callback. "
            "Kafka derives this from out_id agent1#debug#..."
        ),
    )


class PyromindWorkflowCallbackResponse(BaseModel):
    """Result of the temporary run_workflow webhook (for manual / Kafka simulation)."""

    success: bool = True
    outcome: str
    task_id: str
    normalized_status: str | None = None
    conversation_id: str | None = None


def _normalize_dsl(text: str) -> str:
    return text.strip()


def _is_empty_xyflow(workflow_xyflow: dict[str, Any]) -> bool:
    return workflow_xyflow.get("nodes") == [] and workflow_xyflow.get("edges") == []


def _workflow_dsl_from_xyflow(workflow_xyflow: dict[str, Any] | None) -> str | None:
    if workflow_xyflow is None:
        return None
    if _is_empty_xyflow(workflow_xyflow):
        return ""
    try:
        return convert_xyflow_to_dsl(workflow_xyflow)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Failed to convert workflow_xyflow to workflow DSL: "
                f"{type(exc).__name__}: {exc}"
            ),
        ) from exc


def _empty_workflow_reminder() -> TextContent:
    return TextContent(
        text=(
            "<system_reminder>\n"
            "The current canvas is empty and workflow.py does not exist. "
            "Treat this as authoritative: start from scratch, invoke the "
            "matching skill immediately, and do not inspect state.json or "
            "use terminal commands to verify the missing file.\n"
            "</system_reminder>"
        )
    )


def _sync_workflow_with_canvas(
    working_dir: Path, workflow_dsl: str | None
) -> TextContent | None:
    """Reconcile workflow.py with the DSL converted from the canvas xyflow.

    The user can edit the canvas between agent turns, so workflow.py must be
    re-synced from the converted canvas state before each new user message is
    processed -- otherwise the agent would keep editing a stale version. Returns a
    ``<system_reminder>`` TextContent to inject into the LLM's context (via
    ``extended_content``) when workflow.py changed or the attached canvas is
    confirmed empty, or None when no canvas state was attached.

    `workflow_dsl=None` means the caller attached no xyflow canvas state at all
    and is a deliberate no-op, distinct from `workflow_dsl=""` which means the
    canvas is genuinely empty.
    """
    if workflow_dsl is None:
        return None

    workflow_path = working_dir / WORKFLOW_RELATIVE_PATH
    existed = workflow_path.is_file()
    current = workflow_path.read_text(encoding="utf-8") if existed else ""

    normalized_canvas = _normalize_dsl(workflow_dsl)
    normalized_current = _normalize_dsl(current)
    if normalized_canvas == normalized_current:
        if normalized_canvas:
            return None
        return _empty_workflow_reminder()

    workflow_path.parent.mkdir(parents=True, exist_ok=True)

    if not normalized_canvas:
        # The user cleared the canvas. Remove workflow.py entirely (rather
        # than writing an empty file) so downstream logic -- the debug tool,
        # the generate-workflow-dsl skill's "does workflow.py exist?" check,
        # etc. -- treats this identically to "never created".
        workflow_path.unlink(missing_ok=True)
        return TextContent(
            text=(
                "<system_reminder>\n"
                "The user has cleared the workflow on the canvas. workflow.py "
                "has been removed to match. If asked to continue building a "
                "workflow, start fresh rather than assuming the previous "
                "workflow still exists.\n"
                "</system_reminder>"
            )
        )

    workflow_path.write_text(workflow_dsl, encoding="utf-8")
    if existed:
        reminder_text = (
            "<system_reminder>\n"
            "The user has modified the workflow on the canvas since your last "
            "turn. workflow.py has been overwritten to match the canvas "
            "exactly. Treat the current file content as the source of truth "
            "-- do not rely on your memory of a previous version when reading "
            "or editing it.\n"
            "</system_reminder>"
        )
    else:
        reminder_text = (
            "<system_reminder>\n"
            "The user already had a workflow on the canvas from before this "
            "message. It has been loaded into workflow.py as the current "
            "state -- read it before making further changes.\n"
            "</system_reminder>"
        )
    return TextContent(text=reminder_text)


def _workflow_canvas_store(event_service: EventService) -> FileWorkflowCanvasStore:
    return FileWorkflowCanvasStore(
        conversation_dir=event_service.conversation_dir,
        session_id=event_service.stored.id.hex,
    )


def _apply_workflow_snapshot_to_workspace(
    working_dir: Path,
    workflow_dsl: str,
) -> Literal["updated", "removed"]:
    workflow_path = working_dir / WORKFLOW_RELATIVE_PATH
    if not _normalize_dsl(workflow_dsl):
        workflow_path.unlink(missing_ok=True)
        return "removed"

    workflow_path.parent.mkdir(parents=True, exist_ok=True)
    workflow_path.write_text(workflow_dsl, encoding="utf-8")
    return "updated"


def _workflow_rollback_correction_message(
    snapshot: WorkflowCanvasEventSnapshot,
    workflow_file_action: Literal["updated", "removed"],
) -> str:
    workflow_state = (
        "workflow.py has been removed because the restored snapshot is empty"
        if workflow_file_action == "removed"
        else "workflow.py has been overwritten with the restored snapshot"
    )
    return (
        "Workflow rollback applied by the user. "
        f"Restored event {snapshot.event_id} "
        f"({snapshot.snapshot_role} snapshot, version {snapshot.version_id}). "
        f"{workflow_state}. Treat the current workflow.py state as authoritative "
        "and ignore workflow state from before this correction."
    )


class PyromindDebugUrlRequest(BaseModel):
    url: str = Field(description="HTTP or HTTPS URL to call with dev auth context.")
    conversation_id: UUID = Field(
        description=(
            "Conversation to scope the debug request to. The server verifies the "
            "conversation belongs to the current user before forwarding the "
            "current request's login context."
        ),
    )


class PyromindDebugUrlResponse(BaseModel):
    url: str
    context_source: str
    conversation_id: UUID | None = None
    status_code: int
    forwarded_headers: dict[str, str]
    response_headers: dict[str, str]
    body: str


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

pyromind_router = APIRouter(prefix="/pyromind", tags=["Pyromind"])


def _get_current_login_user(http_request: Request) -> CurrentLoginUser:
    current_user = getattr(http_request.state, "current_user", None)
    if not isinstance(current_user, CurrentLoginUser):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Pyromind user context is not available.",
        )
    return current_user


def _validate_debug_url(url: str) -> str:
    try:
        parsed = httpx.URL(url)
    except httpx.InvalidURL as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid URL: {exc}",
        ) from exc

    if parsed.scheme not in {"http", "https"} or not parsed.host:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="URL must be an absolute HTTP or HTTPS URL.",
        )
    return str(parsed)


def _build_debug_context_headers(current_user: CurrentLoginUser) -> dict[str, str]:
    headers: dict[str, str] = {}
    if current_user.cookie:
        headers["cookie"] = current_user.cookie
    if current_user.x_cluster:
        headers["x-cluster"] = current_user.x_cluster
    return headers


@pyromind_router.post(
    "/debug/request-url",
    response_model=PyromindDebugUrlResponse,
)
async def request_debug_url(
    request: PyromindDebugUrlRequest,
) -> PyromindDebugUrlResponse:
    if not is_dev():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    url = _validate_debug_url(request.url)
    conversation_user = get_debug_current_login_user_by_conversation(
        request.conversation_id
    )
    if conversation_user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pyromind conversation login context is not available.",
        )
    headers = _build_debug_context_headers(conversation_user)

    try:
        async with httpx.AsyncClient(
            timeout=_PYROMIND_DEBUG_URL_TIMEOUT_SECONDS,
            follow_redirects=False,
        ) as client:
            response = await client.get(url, headers=headers)
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to request debug URL: {type(exc).__name__}: {exc}",
        ) from exc

    return PyromindDebugUrlResponse(
        url=url,
        context_source="conversation",
        conversation_id=request.conversation_id,
        status_code=response.status_code,
        forwarded_headers=headers,
        response_headers=dict(response.headers),
        body=response.text[:_PYROMIND_DEBUG_RESPONSE_BODY_LIMIT],
    )


@pyromind_router.post("/conversations", response_model=ConversationInfo)
async def create_pyromind_conversation(
    http_request: Request,
    request: PyromindCreateConversationRequest,
    response: Response,
    conversation_service: ConversationService = Depends(get_conversation_service),
) -> ConversationInfo:
    """Create a new conversation configured for Pyromind knowledge-base retrieval.

    The server assembles:
    - A codex-style base agent (prompt + tools) via ``get_codex_agent``
    - Pyromind KB-retrieval instructions layered on top via ``custom_instructions``
    - Tools: codex set (terminal + apply_patch + task_tracker) + grep and
      file_editor for KB search (grep finds files, file_editor views them)
    - Workspace pointing to a conversation-private directory
    """
    # Register default tools before agent resolution.
    import openhands.tools.data_preparation.definition  # noqa: F401

    register_default_tools(enable_browser=False)

    # 1. Resolve knowledge base path (extra can override the default)
    knowledge_base_path = request.extra.get(
        "knowledge_base_path", _DEFAULT_KNOWLEDGE_BASE_PATH
    )
    knowledge_base_path = os.path.abspath(knowledge_base_path)

    if not os.path.isdir(knowledge_base_path):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Knowledge base path does not exist: {knowledge_base_path}",
        )

    # Generate the conversation id here so the workspace directory and persisted
    # conversation id stay aligned.
    conversation_id = uuid.uuid4()
    conversation_dir = conversation_service.conversations_dir / conversation_id.hex
    conversation_dir.mkdir(parents=True, exist_ok=True)
    conversation_dir.chmod(0o700)
    (conversation_dir / "public_data").mkdir(mode=0o700, exist_ok=True)

    # 2. Assemble the pyromind KB instructions (layered on the codex base prompt)
    custom_instructions = PYROMIND_KB_INSTRUCTIONS.format(
        working_dir=str(conversation_dir),
        knowledge_alias=_KNOWLEDGE_ALIAS,
        skills_alias=_SKILLS_ALIAS,
    )

    # Append extra custom instructions from the request if provided
    extra_instructions = request.extra.get("custom_instructions")
    if extra_instructions:
        custom_instructions += f"\n\n补充说明：{extra_instructions}"

    # 3. Load available skills as real Skill objects. Placing them on an
    #    AgentContext makes the SDK auto-attach InvokeSkillTool so the model can
    #    actually call invoke_skill(...) (prompt text alone does not attach it).
    skills_path = request.extra.get("skills_path", _DEFAULT_SKILLS_PATH)
    skills = _load_agent_skills(skills_path, allow_list=_PYROMIND_SKILL_NAMES)
    agent_context = AgentContext(skills=skills) if skills else None
    validation_tool, validation_secrets = _build_workflow_validation_tool(
        http_request, request.extra
    )

    # run_workflow / workflow_debug reuse validate auth/header wiring
    run_tool, run_secrets = _build_workflow_run_tool(http_request)
    debug_tool, debug_secrets = _build_workflow_debug_tool(http_request)
    # storage
    storage_tools, storage_secrets = _build_pyromind_storage_tools(
        http_request, request.extra, skills_path
    )

    # 4. Build LLM config
    llm = LLM(
        usage_id="pyromind-agent",
        model=request.llm.model,
        api_key=request.llm.api_key,
        base_url=request.llm.base_url,
        persist_runtime_config=False,
    )

    # 5. Build the codex-style agent with the KB instructions + KB retrieval
    #    tools (grep to find files, file_editor to view their content).
    agent = get_codex_agent(
        llm=llm,
        cli_mode=True,
        agent_context=agent_context,
        custom_instructions=custom_instructions,
        terminal_params=dict(PYROMIND_TERMINAL_PARAMS),
        extra_tools=[
            *(
                [
                    Tool(name=SkillsListTool.__name__),
                    Tool(name=SkillsReadTool.__name__),
                ]
                if skills
                else []
            ),
            Tool(name="grep"),
            Tool(name="file_editor"),
            Tool(name=WorkflowDebugTool.name, params=debug_tool.params),
            *storage_tools,
            Tool(name="dataset_download"),
            Tool(name="df_run_pipeline"),
            Tool(name="df_convert"),
            validation_tool,
        ],
    )

    # 6. Build a conversation-private workspace. The knowledge base is accessed
    #    separately via its absolute path in the prompt.
    workspace = LocalWorkspace(working_dir=str(conversation_dir))

    # Seed workflow.py from a canvas the user already had before starting this
    # conversation (e.g. they sketched something, then opened chat).
    workflow_dsl = _workflow_dsl_from_xyflow(request.workflow_xyflow)
    if workflow_dsl:
        workflow_file = conversation_dir / WORKFLOW_RELATIVE_PATH
        workflow_file.parent.mkdir(parents=True, exist_ok=True)
        workflow_file.write_text(workflow_dsl, encoding="utf-8")

    # 7. Assemble StartConversationRequest. Pyromind sends the initial message
    # after startup through EventService so the workflow snapshot hook can bind
    # the input snapshot to the generated user MessageEvent.id.
    user_id = get_current_user_id(http_request)
    start_request = StartConversationRequest(
        agent=agent,
        workspace=workspace,
        conversation_id=conversation_id,
        initial_message=None,
        secrets={
            **validation_secrets,
            **run_secrets,
            **debug_secrets,
            **storage_secrets,
        },
        tags={PYROMIND_APP_TAG_KEY: PYROMIND_APP_TAG_VALUE},
        user_id=user_id,
        # Pyromind exposes terminal, patch, and workflow execution tools. Treat
        # unknown-risk actions as requiring user confirmation by default.
        confirmation_policy=ConfirmRisky(),
        security_analyzer=PatternSecurityAnalyzer(),
    )

    # 8. Delegate to conversation service
    try:
        info, is_new = await conversation_service.start_conversation(start_request)
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    current_user = getattr(http_request.state, "current_user", None)
    if is_dev() and isinstance(current_user, CurrentLoginUser):
        bind_debug_current_login_user_to_conversation(info.id, current_user)
    if is_new and request.message:
        event_service = await conversation_service.get_event_service(
            info.id,
            user_id=user_id,
        )
        if event_service is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Conversation event service not found: {info.id}",
            )
        if workflow_dsl:
            initial_reminder = TextContent(
                text=(
                    "<system_reminder>\n"
                    "A workflow from the current canvas is already loaded at "
                    "public_data/workflow_canvas/workflow.py. Treat it as "
                    "authoritative context. Read the full file with file_editor "
                    "before interpreting this request or asking for dataset, "
                    "model, or topology details already present there.\n"
                    "</system_reminder>"
                )
            )
        elif request.workflow_xyflow is not None:
            initial_reminder = _empty_workflow_reminder()
        else:
            initial_reminder = None

        await event_service.send_message(
            Message(role="user", content=[TextContent(text=request.message)]),
            run=True,
            extended_content=[initial_reminder] if initial_reminder else None,
            workflow_dsl_snapshot=workflow_dsl,
            workflow_xyflow_snapshot=request.workflow_xyflow,
        )
        refreshed_info = await conversation_service.get_conversation(
            info.id,
            user_id=user_id,
        )
        if refreshed_info is not None:
            info = refreshed_info
    response.status_code = status.HTTP_201_CREATED if is_new else status.HTTP_200_OK
    return info


@pyromind_router.post(
    "/conversations/{conversation_id}/messages",
    response_model=Success,
    responses={404: {"description": "Conversation not found"}},
)
async def send_pyromind_message(
    http_request: Request,
    request: PyromindSendMessageRequest,
    event_service: EventService = Depends(get_event_service),
) -> Success:
    """Send a user message, first syncing workflow.py to the canvas if needed.

    The frontend attaches the xyflow JSON currently shown on the canvas via
    ``workflow_xyflow``. The server converts it to DSL before syncing
    workflow.py. If the converted DSL disagrees with workflow.py (the user
    edited the canvas, or cleared it), workflow.py is overwritten/removed to
    match and a ``<system_reminder>`` is injected into this turn's LLM context
    (not into the user's visible message) so the agent knows to treat the
    current file as authoritative.
    """
    current_user = getattr(http_request.state, "current_user", None)
    if isinstance(current_user, CurrentLoginUser):
        await apply_pyromind_validation_context(event_service, current_user)

    conversation = event_service.get_conversation()
    working_dir = Path(conversation.workspace.working_dir)
    workflow_dsl = _workflow_dsl_from_xyflow(request.workflow_xyflow)
    reminder = _sync_workflow_with_canvas(working_dir, workflow_dsl)

    message = Message(role="user", content=[TextContent(text=request.text)])
    await event_service.send_message(
        message,
        run=request.run,
        extended_content=[reminder] if reminder else None,
        workflow_dsl_snapshot=workflow_dsl,
        workflow_xyflow_snapshot=request.workflow_xyflow,
    )
    return Success()


@pyromind_router.post(
    "/conversations/{conversation_id}/rollback-workflow-at-event",
    response_model=PyromindWorkflowRollbackResponse,
    response_model_by_alias=True,
    responses={409: {"description": "Workflow snapshot state is inconsistent"}},
)
async def rollback_pyromind_workflow_at_event(
    http_request: Request,
    conversation_id: UUID,
    request: PyromindWorkflowRollbackRequest,
    event_service: EventService = Depends(get_event_service),
) -> PyromindWorkflowRollbackResponse:
    """Restore a workflow snapshot when the event has one."""
    current_user = getattr(http_request.state, "current_user", None)
    if isinstance(current_user, CurrentLoginUser):
        await apply_pyromind_validation_context(event_service, current_user)

    try:
        snapshot = _workflow_canvas_store(event_service).get_event_snapshot(
            request.event_id
        )
    except WorkflowCanvasEventSnapshotNotFoundError:
        return PyromindWorkflowRollbackResponse(
            conversationId=conversation_id,
            snapshot=None,
        )
    except WorkflowCanvasVersionNotFoundError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except WorkflowCanvasStoreError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    conversation = event_service.get_conversation()
    working_dir = Path(conversation.workspace.working_dir)
    workflow_file_action = _apply_workflow_snapshot_to_workspace(
        working_dir,
        snapshot.workflow_dsl_data,
    )
    correction_message = request.message or _workflow_rollback_correction_message(
        snapshot,
        workflow_file_action,
    )

    await event_service.send_internal_context(
        [TextContent(text=correction_message)],
        run=request.run,
        workflow_dsl_snapshot=snapshot.workflow_dsl_data,
        workflow_xyflow_snapshot=snapshot.workflow_xyflow_data,
    )

    return PyromindWorkflowRollbackResponse(
        conversationId=conversation_id,
        rolledBackToEventId=snapshot.event_id,
        workflowVersionId=snapshot.version_id,
        snapshotRole=snapshot.snapshot_role,
        workflowFileAction=workflow_file_action,
        correctionMessage=correction_message,
        snapshot=snapshot,
    )


@pyromind_router.post(
    "/conversations/{conversation_id}/fork-at-event",
    response_model=PyromindForkAtEventResponse,
    response_model_by_alias=True,
    responses={
        404: {"description": "Source conversation or workflow event not found"},
        409: {"description": "Conversation cannot be forked at this event"},
    },
)
async def fork_pyromind_conversation_at_event(
    http_request: Request,
    conversation_id: UUID,
    request: PyromindForkAtEventRequest,
    conversation_service: ConversationService = Depends(get_conversation_service),
) -> PyromindForkAtEventResponse:
    """Create a new Pyromind conversation branch at a workflow checkpoint."""
    try:
        (
            conversation,
            workflow_version_id,
        ) = await conversation_service.fork_conversation_at_event(
            conversation_id,
            event_id=request.event_id,
            title=request.title,
            tags={PYROMIND_APP_TAG_KEY: PYROMIND_APP_TAG_VALUE},
            user_id=get_current_user_id(http_request),
        )
    except ConversationForkAtEventSourceNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ConversationForkAtEventTargetNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ConversationForkAtEventConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    current_user = get_debug_current_login_user_by_conversation(conversation_id)
    if current_user is not None:
        bind_debug_current_login_user_to_conversation(conversation.id, current_user)

    return PyromindForkAtEventResponse(
        conversationId=conversation.id,
        sourceConversationId=conversation_id,
        forkedAtEventId=request.event_id,
        workflowVersionId=workflow_version_id,
        conversation=conversation,
    )


# ---------------------------------------------------------------------------
# Debug webhook router
#
# Deliberately a *separate* router from ``pyromind_router`` above and mounted
# directly on the app in api.py, bypassing the global
# ``Depends(check_session_api_key)`` applied to every other /api/* route.
# The caller here is the external debug platform (or, today, the in-process
# MockDebugPlatform's timer thread making a real HTTP call), not a logged-in
# user -- it has no session key and no Pyromind login cookie to present.
#
# Known trade-off (accepted for now): this endpoint has NO authentication.
# It is only safe to expose this server on a trusted/internal network. If
# the debug platform is ever reachable from a less trusted network, add a
# shared-secret header check here before going further.
# ---------------------------------------------------------------------------

pyromind_debug_webhook_router = APIRouter(prefix="/api/pyromind", tags=["Pyromind"])


@pyromind_debug_webhook_router.post("/debug/callback", response_model=Success)
async def pyromind_debug_callback(request: PyromindDebugCallbackRequest) -> Success:
    """Webhook the debug platform calls when an async debug run finishes.

    Resolves the in-process :class:`DebugResultBroker`, waking the
    ``debug_workflow`` tool-executor thread that is blocked waiting for this
    task's result. See ``openhands.tools.pyromind_debug`` for the tool side
    and ``MockDebugPlatform`` for the local stand-in used until the real
    platform integration is wired up.
    """
    broker = get_debug_result_broker()
    resolved = broker.resolve(
        request.task_id, status=request.status, error_log=request.error_log
    )
    if not resolved:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown or already-resolved debug task: {request.task_id}",
        )
    return Success()


@pyromind_debug_webhook_router.post(
    "/workflow/callback",
    response_model=PyromindWorkflowCallbackResponse,
)
async def pyromind_workflow_callback(
    request: PyromindWorkflowCallbackRequest,
) -> PyromindWorkflowCallbackResponse:
    """Temporary webhook to simulate Kafka run_workflow terminal status delivery.

    Calls :func:`deliver_run_workflow_status` so manual HTTP clients can test the
    async resume path without a Kafka consumer. Mounted on the same unauthenticated
    webhook router as ``/debug/callback`` — internal/trusted network only.
    """
    result: RunWorkflowCallbackResult = await deliver_run_workflow_status(
        task_id=request.task_id,
        status=request.status,
        error_log=request.error_log,
        conversation_id=request.conversation_id,
        auto_run=request.auto_run,
        from_workflow_debug=request.from_workflow_debug,
    )
    if result.outcome in {"unknown_task", "unknown_conversation"}:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"run_workflow callback failed for task_id={request.task_id}: "
                f"{result.outcome}"
            ),
        )
    return PyromindWorkflowCallbackResponse(
        outcome=result.outcome,
        task_id=result.task_id,
        normalized_status=result.normalized_status,
        conversation_id=result.conversation_id,
    )
