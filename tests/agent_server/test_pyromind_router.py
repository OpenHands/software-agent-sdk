from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest
from fastapi import Response, status
from pydantic import ValidationError
from starlette.requests import Request

from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.dependencies import load_base_env
from openhands.agent_server.event_service import EventService
from openhands.agent_server.models import ConversationInfo
from openhands.agent_server.pyromind_auth import (
    PYROMIND_AUTH_COOKIE_NAME,
    CurrentLoginUser,
    get_debug_current_login_user_by_conversation,
)
from openhands.agent_server.pyromind_constants import (
    PYROMIND_APP_TAG_KEY,
    PYROMIND_APP_TAG_VALUE,
)
from openhands.agent_server.pyromind_router import (
    PYROMIND_AUTH_TOKEN_SECRET,
    PYROMIND_KB_INSTRUCTIONS,
    PyromindCreateConversationRequest,
    PyromindLLMConfig,
    PyromindSendMessageRequest,
    PyromindWorkflowRollbackRequest,
    _build_debug_context_headers,
    _build_pyromind_storage_tools,
    _build_workflow_run_tool,
    _build_workflow_validation_tool,
    _get_validation_cookie_header,
    _workflow_dsl_from_xyflow,
    apply_pyromind_validation_context,
    create_pyromind_conversation,
    rollback_pyromind_workflow_at_event,
    send_pyromind_message,
)
from openhands.agent_server.workflow_canvas_models import (
    SaveWorkflowCanvasEventSnapshotRequest,
)
from openhands.agent_server.workflow_canvas_store import FileWorkflowCanvasStore
from openhands.sdk.conversation.request import StartConversationRequest
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.llm.message import Message, TextContent
from openhands.sdk.tool.builtins import SkillsListTool, SkillsReadTool
from openhands.tools.data_preparation import (
    DfCheckProgressTool,
    DfStopTaskTool,
    DfSubmitPipelineTool,
)
from openhands.tools.pyromind_cleaning import RunDatasetCleaningTool
from openhands.tools.pyromind_dataset import (
    PreviewDatasetTool,
    UploadFileToPyromindTool,
)
from openhands.tools.pyromind_dataset.definition import (
    PYROMIND_STORAGE_AUTH_COOKIE_SECRET,
    PYROMIND_STORAGE_HEADERS_STATE_KEY,
)
from openhands.tools.pyromind_remote_dataset import PreviewRemoteDatasetTool
from openhands.tools.workflow import (
    RunWorkflowTool,
    ValidateWorkflowDslTool,
)
from openhands.tools.workflow.validate_workflow_dsl import (
    PYROMIND_VALIDATE_AUTH_COOKIE_SECRET,
    PYROMIND_VALIDATE_HEADERS_STATE_KEY,
)


_REMOVED_WORKFLOW_TOOL = "publish" + "_workflow"


def test_generate_workflow_skill_uses_progressive_reference_disclosure() -> None:
    repo_root = Path(__file__).parents[2]
    skill_root = repo_root / ".agents" / "skills" / "generate-workflow-dsl"
    skill_text = (skill_root / "SKILL.md").read_text(encoding="utf-8")
    references = {
        path.name: path.read_text(encoding="utf-8")
        for path in (skill_root / "references").glob("*.md")
    }

    frontmatter = skill_text.split("---", 2)[1]
    assert set(
        line.split(":", 1)[0]
        for line in frontmatter.splitlines()
        if line and not line.startswith(" ")
    ) == {"name", "description"}
    assert len(skill_text.splitlines()) <= 140
    assert "## 资料索引" in skill_text
    assert "## DSL 与资料边界" in skill_text
    assert 'skills_read(skill_name="generate-workflow-dsl"' in skill_text
    assert "### 0. 先判定局部修改" in skill_text
    assert "qwen3.5-2b" in skill_text
    assert "不读取 reference 或 `knowledge/`" in skill_text
    assert "retryable=false" in skill_text
    assert "只生成并校验 DSL" in skill_text
    assert "不写 import" in skill_text
    assert "禁止用 Agent 本地 terminal 替代平台运行时" in skill_text
    assert "只有校验明确暴露未覆盖的平台契约" in skill_text
    assert "同一轮不得重复读取同一路径" in skill_text
    assert "可程序验证只说明 reward 可构造" in skill_text
    assert "数据同时支持\n  SFT/GRPO 时默认 SFT" in skill_text
    assert "自动选择 GRPO 还要求当前模型是 SFT/指令 checkpoint" in skill_text
    assert skill_text.count("阶段锁定后") == 2
    assert "ModelTrainSFTNode(" not in skill_text
    assert set(references) == {
        "custom-python-assets.md",
        "data-routing.md",
        "parameter-decision.md",
        "workflow-contracts.md",
    }
    assert not (skill_root / "references" / "example-workflows.md").exists()
    assert "PathJoinNode → LoadDataset" in references["data-routing.md"]
    assert "sample_file_path" in references["data-routing.md"]
    assert "未返回时只用" in references["data-routing.md"]
    assert "不得假设副本存在或搜索本地工作区" in references["data-routing.md"]
    assert "去掉可选的 `/workspace/` 前缀和开头 `/`" in references["data-routing.md"]
    assert "同一路径已有成功 preview 时复用" in references["data-routing.md"]
    assert "可选统计为空不等于 preview 失败" in references["data-routing.md"]
    assert "## 监督信号分类" in references["data-routing.md"]
    assert "即使答案也能程序验证" in references["data-routing.md"]
    assert "本文件只识别数据形态" in references["data-routing.md"]
    assert "禁止调用 `run_dataset_cleaning`" in skill_text
    contracts = references["workflow-contracts.md"]
    assert "Benchmark | 数据配置 → 模型入口 → VLLM → Metric → Eval" in contracts
    assert "CloneAndCacheModel | model, target_path" in contracts
    assert "每个 Metrics Builder 只输出一个 `metrics_config`" in contracts
    assert "NVIDIA-H100-80GB-HBM3" in contracts
    assert "训练阶段由主 Skill 的“选择阶段”唯一决定" in contracts
    assert "有可程序化验证答案或 reward 时选 GRPO" not in contracts
    assert "只填 Secret 名" in contracts
    assert "Qwen/Qwen3" not in contracts
    assert "compute_gsm8k" not in references["custom-python-assets.md"]
    assert "geometry_vqa_thinking_reward" not in references["custom-python-assets.md"]
    assert "`public_data/<name>.py`" in references["custom-python-assets.md"]
    for removed in (
        "node-reference.md",
        "platform-contract-overrides.md",
        "stage-templates.md",
    ):
        assert not (skill_root / "references" / removed).exists()

    debug_skill_text = (
        repo_root / ".agents" / "skills" / "debug-workflow" / "SKILL.md"
    ).read_text(encoding="utf-8")
    assert "使用 generate-workflow-dsl" in debug_skill_text
    assert "本轮不得调用 workflow_debug" in debug_skill_text


def test_pyromind_instructions_enforce_workflow_skill_reference_order() -> None:
    rendered = PYROMIND_KB_INSTRUCTIONS.format(
        knowledge_alias="knowledge",
        skills_alias=".agents/skills",
        working_dir="workspace/conversations/test",
    )

    assert "Immediately after that single workflow read" in rendered
    assert "Then read only the exact skill resource" in rendered
    assert "do not inspect general `knowledge/` before invoking the skill" in rendered
    assert "Treat any requested node, model, parameter" in rendered
    assert "do not\n  invoke `debug-workflow` or `workflow_debug`" in rendered
    assert "Pyromind platform nodes perform actual" in rendered
    assert "Use dedicated platform tools for preview/upload" in rendered
    assert "never use the local terminal as a substitute" in rendered
    assert "`public_data/` is the writable area" in rendered
    assert "do not follow terminal cwd" in rendered
    assert "every created file must use a `public_data/...` path" in rendered
    assert "terminal session starts at the conversation root" in rendered
    assert "Make its first command\n`cd public_data`" in rendered
    assert "reuse the persistent shell's current directory" in rendered
    assert "conversation-local auxiliary files" in rendered
    assert "Do not consult `knowledge/` before validation" in rendered
    assert "one\n  targeted knowledge-base lookup" in rendered
    assert "Do not run `pwd`, `cd`, or directory listings" not in rendered
    assert "Do not use `/dev/null`" not in rendered
    assert "Do not run `pwd`" not in rendered
    assert "do not access host-absolute paths, `/workspace`" not in rendered
    assert "workspace-discovery commands are rejected" not in rendered
    assert "exact, already-known conversation-local script" not in rendered
    assert "workspace/conversations/test" not in rendered


def test_pyromind_llm_config_normalizes_chat_completions_base_url() -> None:
    config = PyromindLLMConfig(
        model="openai/glm-5.2-fp8",
        api_key="test-key",
        base_url=" http://208.64.254.187:8000/v1/chat/completions/ ",
    )

    assert config.base_url == "http://208.64.254.187:8000/v1"


class _FakeConversationService:
    def __init__(self, conversations_dir: Path) -> None:
        self.conversations_dir = conversations_dir
        self.start_request: StartConversationRequest | None = None
        self.event_service = _FakeInitialMessageEventService()
        self.info: ConversationInfo | None = None

    async def start_conversation(
        self, request: StartConversationRequest
    ) -> tuple[ConversationInfo, bool]:
        self.start_request = request
        assert request.conversation_id is not None
        self.info = ConversationInfo(
            id=request.conversation_id,
            agent=request.agent,
            workspace=request.workspace,
            execution_status=ConversationExecutionStatus.IDLE,
        )
        return (self.info, True)

    async def get_event_service(
        self,
        conversation_id: UUID,
        user_id: str | None = None,
    ) -> EventService | None:
        assert self.info is not None
        assert conversation_id == self.info.id
        return cast(EventService, self.event_service)

    async def get_conversation(
        self,
        conversation_id: UUID,
        user_id: str | None = None,
    ) -> ConversationInfo | None:
        assert self.info is not None
        assert conversation_id == self.info.id
        return self.info


class _FakeInitialMessageEventService:
    def __init__(self) -> None:
        self.sent_message: Message | None = None
        self.run: bool | None = None
        self.workflow_dsl_snapshot: str | None = None
        self.workflow_xyflow_snapshot: dict[str, Any] | None = None
        self.extended_content: list[Any] | None = None

    async def send_message(
        self,
        message: Message,
        run: bool = False,
        _from_goal_loop: bool = False,
        extended_content: list[Any] | None = None,
        workflow_dsl_snapshot: str | None = None,
        workflow_xyflow_snapshot: dict[str, Any] | None = None,
    ) -> None:
        self.sent_message = message
        self.run = run
        self.extended_content = extended_content
        self.workflow_dsl_snapshot = workflow_dsl_snapshot
        self.workflow_xyflow_snapshot = workflow_xyflow_snapshot


class _FakeEventService:
    def __init__(
        self,
        tags: dict[str, str],
        conversation_id: UUID | None = None,
    ) -> None:
        self.stored = type(
            "FakeStoredConversation",
            (),
            {
                "id": conversation_id or UUID("00000000-0000-0000-0000-000000000001"),
                "tags": tags,
            },
        )()
        self.secrets: dict[str, str] = {}
        self.agent_state: dict[str, object] = {}

    async def update_secrets(self, secrets: dict[str, str]) -> None:
        self.secrets.update(secrets)

    async def update_agent_state(self, values: dict[str, object]) -> None:
        self.agent_state.update(values)


class _FakePyromindMessageEventService(_FakeEventService):
    def __init__(
        self,
        tags: dict[str, str],
        working_dir: Path,
        conversation_id: UUID | None = None,
        conversation_dir: Path | None = None,
    ) -> None:
        super().__init__(tags, conversation_id)
        self.conversation_dir = conversation_dir or working_dir / "conversation"
        self.sent_message: Message | None = None
        self.run: bool | None = None
        self.workflow_dsl_snapshot: str | None = None
        self.workflow_xyflow_snapshot: dict[str, Any] | None = None
        self.extended_content: list[Any] | None = None
        self.internal_context: list[TextContent] | None = None
        workspace = type("FakeWorkspace", (), {"working_dir": str(working_dir)})()
        self._conversation = type("FakeConversation", (), {"workspace": workspace})()

    def get_conversation(self):
        return self._conversation

    async def send_message(
        self,
        message: Message,
        run: bool = False,
        _from_goal_loop: bool = False,
        extended_content: list[Any] | None = None,
        workflow_dsl_snapshot: str | None = None,
        workflow_xyflow_snapshot: dict[str, Any] | None = None,
    ) -> None:
        self.sent_message = message
        self.run = run
        self.extended_content = extended_content
        self.workflow_dsl_snapshot = workflow_dsl_snapshot
        self.workflow_xyflow_snapshot = workflow_xyflow_snapshot

    async def send_internal_context(
        self,
        content: list[TextContent],
        run: bool = False,
        workflow_dsl_snapshot: str | None = None,
        workflow_xyflow_snapshot: dict[str, Any] | None = None,
    ) -> str:
        self.internal_context = content
        self.run = run
        self.workflow_dsl_snapshot = workflow_dsl_snapshot
        self.workflow_xyflow_snapshot = workflow_xyflow_snapshot
        return "internal-event"


def _make_request(headers: dict[str, str] | None = None) -> Request:
    raw_headers = [
        (name.lower().encode(), value.encode())
        for name, value in (headers or {}).items()
    ]
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/pyromind/conversations",
            "headers": raw_headers,
        }
    )


@pytest.mark.asyncio
async def test_pyromind_conversation_uses_conversation_workspace(tmp_path):
    knowledge_base = tmp_path / "knowledge"
    knowledge_base.mkdir()
    service = _FakeConversationService(tmp_path / "conversations")
    response = Response()
    cookie_header = f"{PYROMIND_AUTH_COOKIE_NAME}=session-token; other=value"

    request = _make_request(
        {
            "cookie": cookie_header,
            "x-cluster": "us-west-1#pre",
        }
    )
    load_base_env(request)

    info = await create_pyromind_conversation(
        request,
        PyromindCreateConversationRequest(
            llm=PyromindLLMConfig(model="gpt-4o", api_key="test-key"),
            extra={
                "knowledge_base_path": str(knowledge_base),
                "skills_path": str(tmp_path / "missing-skills"),
            },
        ),
        response,
        conversation_service=cast(ConversationService, service),
    )

    assert response.status_code == status.HTTP_201_CREATED
    assert service.start_request is not None
    assert service.start_request.conversation_id == info.id
    expected_dir = service.conversations_dir / info.id.hex
    assert Path(service.start_request.workspace.working_dir) == expected_dir
    assert expected_dir.is_dir()

    tool_names = {tool.name for tool in service.start_request.agent.tools}
    assert "terminal" in tool_names
    assert "grep" in tool_names
    assert "file_editor" in tool_names
    assert "update_plan" in tool_names
    assert SkillsListTool.__name__ not in tool_names
    assert SkillsReadTool.__name__ not in tool_names
    assert RunWorkflowTool.name not in tool_names
    assert ValidateWorkflowDslTool.name in tool_names
    assert PreviewDatasetTool.name in tool_names
    assert UploadFileToPyromindTool.name in tool_names
    assert RunDatasetCleaningTool.name in tool_names
    assert _REMOVED_WORKFLOW_TOOL not in tool_names
    terminal_tool = next(
        tool for tool in service.start_request.agent.tools if tool.name == "terminal"
    )
    assert "sandbox_mode" not in terminal_tool.params
    assert (expected_dir / "public_data").is_dir()
    validation_tool = next(
        tool
        for tool in service.start_request.agent.tools
        if tool.name == ValidateWorkflowDslTool.name
    )
    assert validation_tool.params == {
        "headers": {"x-cluster": "us-west-1#pre"},
        "secret_headers": {"cookie": "PYROMIND_VALIDATE_AUTH_COOKIE"},
    }
    expected_execution_params = {
        "current_user": None,
        "env": "pre",
        "cluster": "us-west-1",
        "headers": {
            "x-cluster": "us-west-1#pre",
            "request-app": "openhands",
        },
        "runtime_dir": str(tmp_path / "missing-skills" / "data-cleaning" / "scripts"),
        "storage_headers": {"x-cluster": "us-west-1#pre"},
        "storage_secret_headers": {"cookie": "PYROMIND_STORAGE_AUTH_COOKIE"},
    }
    assert "session-token" not in str(validation_tool.params)
    assert (
        service.start_request.secrets["PYROMIND_VALIDATE_AUTH_COOKIE"].get_value()
        == cookie_header
    )

    preview_tool = next(
        tool
        for tool in service.start_request.agent.tools
        if tool.name == PreviewDatasetTool.name
    )
    upload_tool = next(
        tool
        for tool in service.start_request.agent.tools
        if tool.name == UploadFileToPyromindTool.name
    )
    cleaning_tool = next(
        tool
        for tool in service.start_request.agent.tools
        if tool.name == RunDatasetCleaningTool.name
    )
    assert preview_tool.params == {
        "headers": {"x-cluster": "us-west-1#pre"},
        "secret_headers": {"cookie": "PYROMIND_STORAGE_AUTH_COOKIE"},
    }
    assert upload_tool.params == preview_tool.params
    assert cleaning_tool.params == expected_execution_params
    assert "secret_headers" not in cleaning_tool.params
    assert "session-token" not in str(preview_tool.params)
    assert (
        service.start_request.secrets["PYROMIND_STORAGE_AUTH_COOKIE"].get_value()
        == cookie_header
    )
    assert service.start_request.secrets["auth_token"].get_value() == "session-token"
    assert service.start_request.tags == {PYROMIND_APP_TAG_KEY: PYROMIND_APP_TAG_VALUE}
    dumped_agent = service.start_request.agent.model_dump(mode="json")
    assert "api_key" not in dumped_agent["llm"]
    assert "base_url" not in dumped_agent["llm"]
    assert "api_key" not in dumped_agent["condenser"]["llm"]
    assert "base_url" not in dumped_agent["condenser"]["llm"]


@pytest.mark.asyncio
async def test_pyromind_conversation_registers_skill_runtime_tools(tmp_path):
    skills_dir = tmp_path / "skills"
    skill_dir = skills_dir / "generate-workflow-dsl"
    (skill_dir / "references").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: generate-workflow-dsl\n"
        "description: Generate workflow DSL\n---\n# Generate",
        encoding="utf-8",
    )
    (skill_dir / "references" / "guide.md").write_text("guide", encoding="utf-8")
    service = _FakeConversationService(tmp_path / "conversations")
    response = Response()
    request = _make_request({})
    load_base_env(request)

    await create_pyromind_conversation(
        request,
        PyromindCreateConversationRequest(
            llm=PyromindLLMConfig(model="gpt-4o", api_key="test-key"),
            extra={"skills_path": str(skills_dir)},
        ),
        response,
        conversation_service=cast(ConversationService, service),
    )

    assert service.start_request is not None
    tools = {tool.name: tool for tool in service.start_request.agent.tools}
    assert SkillsListTool.__name__ in tools
    assert SkillsReadTool.__name__ in tools
    assert tools[SkillsListTool.__name__].params == {}
    assert tools[SkillsReadTool.__name__].params == {}
    service.start_request.model_dump(mode="json")


@pytest.mark.asyncio
async def test_pyromind_conversation_request_serializes_without_local_paths(tmp_path):
    """Persisted request JSON (meta.json) must not contain host-absolute paths."""
    knowledge_base = tmp_path / "knowledge"
    knowledge_base.mkdir()
    service = _FakeConversationService(tmp_path / "conversations")
    response = Response()

    info = await create_pyromind_conversation(
        _make_request(),
        PyromindCreateConversationRequest(
            llm=PyromindLLMConfig(model="gpt-4o", api_key="test-key"),
            extra={
                "knowledge_base_path": str(knowledge_base),
                "skills_path": str(tmp_path / "missing-skills"),
            },
        ),
        response,
        conversation_service=cast(ConversationService, service),
    )

    assert response.status_code == status.HTTP_201_CREATED
    assert service.start_request is not None
    request_json = service.start_request.model_dump_json(exclude_none=True)

    assert str(knowledge_base) not in request_json
    assert str(service.conversations_dir / info.id.hex) not in request_json
    assert "<REDACTED_WORKSPACE_PATH>" in request_json
    assert "<REDACTED_PATH>" in request_json


@pytest.mark.asyncio
async def test_pyromind_conversation_binds_login_context(tmp_path):
    knowledge_base = tmp_path / "knowledge"
    knowledge_base.mkdir()
    service = _FakeConversationService(tmp_path / "conversations")
    response = Response()
    request = _make_request()
    request.state.current_user = CurrentLoginUser(
        username="debug-user-42",
        email="debug-user-42@example.test",
        user_id=42,
        cookie="auth_token=context-token",
        x_cluster="context-cluster",
    )

    info = await create_pyromind_conversation(
        request,
        PyromindCreateConversationRequest(
            llm=PyromindLLMConfig(model="gpt-4o", api_key="test-key"),
            extra={
                "knowledge_base_path": str(knowledge_base),
                "skills_path": str(tmp_path / "missing-skills"),
            },
        ),
        response,
        conversation_service=cast(ConversationService, service),
    )

    assert service.start_request is not None
    assert service.start_request.user_id == "42"
    bound_user = get_debug_current_login_user_by_conversation(info.id)
    assert bound_user == request.state.current_user


@pytest.mark.asyncio
async def test_pyromind_conversation_converts_xyflow_before_seeding_workflow(
    tmp_path, monkeypatch
):
    knowledge_base = tmp_path / "knowledge"
    knowledge_base.mkdir()
    service = _FakeConversationService(tmp_path / "conversations")
    response = Response()
    xyflow = {"name": "Canvas", "nodes": [{"id": "n1"}], "edges": []}
    monkeypatch.setattr(
        "openhands.agent_server.pyromind_router.convert_xyflow_to_dsl",
        lambda workflow: "# workflow: Canvas\nnode = Example()\n",
    )

    info = await create_pyromind_conversation(
        _make_request(),
        PyromindCreateConversationRequest(
            llm=PyromindLLMConfig(model="gpt-4o", api_key="test-key"),
            workflow_xyflow=xyflow,
            extra={
                "knowledge_base_path": str(knowledge_base),
                "skills_path": str(tmp_path / "missing-skills"),
            },
        ),
        response,
        conversation_service=cast(ConversationService, service),
    )

    workflow_path = (
        service.conversations_dir
        / info.id.hex
        / "public_data"
        / "workflow_canvas"
        / "workflow.py"
    )
    assert workflow_path.read_text(encoding="utf-8") == (
        "# workflow: Canvas\nnode = Example()\n"
    )


@pytest.mark.asyncio
async def test_pyromind_conversation_initial_message_saves_workflow_snapshot(
    tmp_path, monkeypatch
):
    knowledge_base = tmp_path / "knowledge"
    knowledge_base.mkdir()
    service = _FakeConversationService(tmp_path / "conversations")
    response = Response()
    xyflow = {"name": "Canvas", "nodes": [{"id": "n1"}], "edges": []}
    monkeypatch.setattr(
        "openhands.agent_server.pyromind_router.convert_xyflow_to_dsl",
        lambda workflow: "# workflow: Canvas\nnode = Example()\n",
    )

    await create_pyromind_conversation(
        _make_request(),
        PyromindCreateConversationRequest(
            llm=PyromindLLMConfig(model="gpt-4o", api_key="test-key"),
            message="帮我继续改工作流",
            workflow_xyflow=xyflow,
            extra={
                "knowledge_base_path": str(knowledge_base),
                "skills_path": str(tmp_path / "missing-skills"),
            },
        ),
        response,
        conversation_service=cast(ConversationService, service),
    )

    assert service.start_request is not None
    assert service.start_request.initial_message is None
    assert service.event_service.sent_message is not None
    assert service.event_service.sent_message.role == "user"
    assert service.event_service.run is True
    assert service.event_service.workflow_dsl_snapshot == (
        "# workflow: Canvas\nnode = Example()\n"
    )
    assert service.event_service.workflow_xyflow_snapshot == xyflow
    assert service.event_service.extended_content is not None
    reminder = service.event_service.extended_content[0]
    assert isinstance(reminder, TextContent)
    assert "workflow.py" in reminder.text
    assert "Read the full file with file_editor" in reminder.text


@pytest.mark.asyncio
async def test_pyromind_conversation_initial_message_marks_empty_canvas(tmp_path):
    service = _FakeConversationService(tmp_path / "conversations")
    response = Response()
    empty_xyflow = {"name": "Empty", "nodes": [], "edges": []}

    await create_pyromind_conversation(
        _make_request(),
        PyromindCreateConversationRequest(
            llm=PyromindLLMConfig(model="gpt-4o", api_key="test-key"),
            message="直接生成评测工作流",
            workflow_xyflow=empty_xyflow,
            extra={"skills_path": str(tmp_path / "missing-skills")},
        ),
        response,
        conversation_service=cast(ConversationService, service),
    )

    assert service.event_service.workflow_dsl_snapshot == ""
    assert service.event_service.workflow_xyflow_snapshot == empty_xyflow
    assert service.event_service.extended_content is not None
    reminder = service.event_service.extended_content[0]
    assert isinstance(reminder, TextContent)
    assert "current canvas is empty" in reminder.text
    assert "invoke the matching skill immediately" in reminder.text


def test_workflow_dsl_from_xyflow_treats_empty_xyflow_as_empty_canvas():
    assert _workflow_dsl_from_xyflow({"name": "Empty", "nodes": [], "edges": []}) == ""


def test_pyromind_requests_reject_workflow_dsl_field():
    with pytest.raises(ValidationError):
        PyromindCreateConversationRequest.model_validate(
            {
                "llm": {"model": "gpt-4o", "api_key": "test-key"},
                "workflow_dsl": "# workflow: old\n",
            }
        )

    with pytest.raises(ValidationError):
        PyromindSendMessageRequest.model_validate(
            {
                "text": "继续",
                "workflow_dsl": "# workflow: old\n",
            }
        )


@pytest.mark.asyncio
async def test_pyromind_message_refreshes_storage_auth_context(tmp_path):
    service = _FakePyromindMessageEventService(
        {PYROMIND_APP_TAG_KEY: PYROMIND_APP_TAG_VALUE},
        tmp_path,
    )
    http_request = _make_request(
        {
            "cookie": "auth_token=request-token; other=value",
            "x-cluster": "request-cluster",
        }
    )
    http_request.state.current_user = CurrentLoginUser(
        username="debug-user-42",
        email="debug-user-42@example.test",
        user_id=42,
        cookie="auth_token=context-token; other=value",
        x_cluster="context-cluster",
    )

    await send_pyromind_message(
        http_request,
        PyromindSendMessageRequest(text="帮我预览 /start-hook.sh"),
        event_service=cast(EventService, service),
    )

    assert service.secrets == {
        PYROMIND_VALIDATE_AUTH_COOKIE_SECRET: "auth_token=context-token; other=value",
        PYROMIND_AUTH_TOKEN_SECRET: "context-token",
        PYROMIND_STORAGE_AUTH_COOKIE_SECRET: "auth_token=context-token; other=value",
    }
    assert service.agent_state == {
        PYROMIND_VALIDATE_HEADERS_STATE_KEY: {"x-cluster": "context-cluster"},
        PYROMIND_STORAGE_HEADERS_STATE_KEY: {"x-cluster": "context-cluster"},
    }
    assert service.sent_message is not None
    assert service.run is True


@pytest.mark.asyncio
async def test_pyromind_workflow_rollback_restores_snapshot_and_sends_correction(
    tmp_path,
):
    conversation_id = UUID("00000000-0000-0000-0000-000000000123")
    conversation_dir = tmp_path / "conversation"
    working_dir = tmp_path / "workspace"
    working_dir.mkdir()
    workflow_path = working_dir / "public_data" / "workflow_canvas" / "workflow.py"
    workflow_path.parent.mkdir(parents=True, exist_ok=True)
    workflow_path.write_text("# workflow: current\nstep = 2\n", encoding="utf-8")
    service = _FakePyromindMessageEventService(
        {PYROMIND_APP_TAG_KEY: PYROMIND_APP_TAG_VALUE},
        working_dir,
        conversation_id=conversation_id,
        conversation_dir=conversation_dir,
    )
    xyflow = {"name": "Rollback", "nodes": [{"id": "n1"}], "edges": []}
    FileWorkflowCanvasStore(conversation_dir, conversation_id.hex).save_event_snapshot(
        SaveWorkflowCanvasEventSnapshotRequest(
            eventId="event-rollback",
            snapshotRole="out",
            workflowDslData="# workflow: rollback\nstep = 1\n",
            workflowXyflowData=xyflow,
            summary="rollback target",
            createdBy="test",
        )
    )

    result = await rollback_pyromind_workflow_at_event(
        _make_request(),
        conversation_id,
        PyromindWorkflowRollbackRequest(eventId="event-rollback"),
        event_service=cast(EventService, service),
    )

    assert workflow_path.read_text(encoding="utf-8") == (
        "# workflow: rollback\nstep = 1\n"
    )
    assert service.sent_message is None
    assert service.internal_context is not None
    first_content = service.internal_context[0]
    assert isinstance(first_content, TextContent)
    correction_text = first_content.text
    assert "Workflow rollback applied by the user" in correction_text
    assert "event-rollback" in correction_text
    assert service.run is False
    assert service.workflow_dsl_snapshot == "# workflow: rollback\nstep = 1\n"
    assert service.workflow_xyflow_snapshot == xyflow
    assert result.conversation_id == conversation_id
    assert result.rolled_back_to_event_id == "event-rollback"
    assert result.workflow_version_id == "v000001"
    assert result.snapshot_role == "out"
    assert result.workflow_file_action == "updated"
    assert result.snapshot is not None
    assert result.snapshot.workflow_dsl_data == "# workflow: rollback\nstep = 1\n"


@pytest.mark.asyncio
async def test_pyromind_workflow_rollback_returns_null_when_snapshot_is_missing(
    tmp_path,
):
    conversation_id = UUID("00000000-0000-0000-0000-000000000123")
    working_dir = tmp_path / "workspace"
    working_dir.mkdir()
    workflow_path = working_dir / "public_data" / "workflow_canvas" / "workflow.py"
    current_workflow = "# workflow: current\nstep = 2\n"
    workflow_path.parent.mkdir(parents=True, exist_ok=True)
    workflow_path.write_text(current_workflow, encoding="utf-8")
    service = _FakePyromindMessageEventService(
        {PYROMIND_APP_TAG_KEY: PYROMIND_APP_TAG_VALUE},
        working_dir,
        conversation_id=conversation_id,
        conversation_dir=tmp_path / "conversation",
    )

    result = await rollback_pyromind_workflow_at_event(
        _make_request(),
        conversation_id,
        PyromindWorkflowRollbackRequest(eventId="event-without-snapshot"),
        event_service=cast(EventService, service),
    )

    assert result.snapshot is None
    assert result.rolled_back_to_event_id is None
    assert result.workflow_version_id is None
    assert result.workflow_file_action is None
    assert result.model_dump(mode="json", by_alias=True)["snapshot"] is None
    assert workflow_path.read_text(encoding="utf-8") == current_workflow
    assert service.sent_message is None
    assert service.internal_context is None


def test_validation_cookie_header_keeps_full_cookie_in_prod(monkeypatch):
    monkeypatch.setenv("APP_ENV", "prod")

    cookie_header = f"{PYROMIND_AUTH_COOKIE_NAME}=session-token; other=value"
    request = _make_request({"cookie": cookie_header})

    assert _get_validation_cookie_header(request) == cookie_header


def test_workflow_validation_tool_uses_user_context_headers():
    request = _make_request(
        {
            "cookie": f"{PYROMIND_AUTH_COOKIE_NAME}=request-token",
            "x-cluster": "request-cluster",
        }
    )
    request.state.current_user = CurrentLoginUser(
        username="debug-user-42",
        email="debug-user-42@example.test",
        user_id=42,
        cookie=f"{PYROMIND_AUTH_COOKIE_NAME}=context-token; other=value",
        x_cluster="context-cluster",
    )

    tool, secrets = _build_workflow_validation_tool(request, {})

    assert tool.params == {
        "headers": {"x-cluster": "context-cluster"},
        "secret_headers": {"cookie": "PYROMIND_VALIDATE_AUTH_COOKIE"},
    }
    assert (
        secrets["PYROMIND_VALIDATE_AUTH_COOKIE"].get_value()
        == f"{PYROMIND_AUTH_COOKIE_NAME}=context-token; other=value"
    )


def test_pyromind_storage_tools_use_user_context_headers():
    request = _make_request(
        {
            "cookie": f"{PYROMIND_AUTH_COOKIE_NAME}=request-token",
            "x-cluster": "request-cluster",
        }
    )
    request.state.current_user = CurrentLoginUser(
        username="debug-user-42",
        email="debug-user-42@example.test",
        user_id=42,
        cookie=f"{PYROMIND_AUTH_COOKIE_NAME}=context-token; other=value",
        x_cluster="context-cluster",
    )
    load_base_env(request)

    tools, secrets = _build_pyromind_storage_tools(
        request,
        {
            "storage_base_url": "https://storage.test/api",
            "dataset_cleaning_output_root": "/agentTest/clean-results",
        },
        "/srv/skills",
    )

    assert [tool.name for tool in tools] == [
        PreviewDatasetTool.name,
        UploadFileToPyromindTool.name,
        RunDatasetCleaningTool.name,
        DfSubmitPipelineTool.name,
        DfCheckProgressTool.name,
        DfStopTaskTool.name,
        PreviewRemoteDatasetTool.name,
    ]
    assert tools[0].params == {
        "storage_base_url": "https://storage.test/api",
        "headers": {"x-cluster": "context-cluster"},
        "secret_headers": {"cookie": "PYROMIND_STORAGE_AUTH_COOKIE"},
    }
    assert tools[1].params == tools[0].params
    cleaning_params = tools[2].params
    assert cleaning_params == {
        "current_user": CurrentLoginUser(
            username="debug-user-42",
            email="debug-user-42@example.test",
            user_id=42,
            cookie=None,
            x_cluster="context-cluster",
        ),
        "env": "prod",
        "cluster": "context-cluster",
        "headers": {
            "x-cluster": "context-cluster",
            "request-app": "openhands",
        },
        "output_root": "/agentTest/clean-results",
        "runtime_dir": "/srv/skills/data-cleaning/scripts",
        "storage_base_url": "https://storage.test/api",
        "storage_headers": {"x-cluster": "context-cluster"},
        "storage_secret_headers": {"cookie": "PYROMIND_STORAGE_AUTH_COOKIE"},
    }
    assert (
        secrets["PYROMIND_STORAGE_AUTH_COOKIE"].get_value()
        == f"{PYROMIND_AUTH_COOKIE_NAME}=context-token; other=value"
    )
    assert secrets["auth_token"].get_value() == "request-token"


def test_build_debug_context_headers_uses_current_user_context():
    current_user = CurrentLoginUser(
        username="debug-user-42",
        email="debug-user-42@example.test",
        user_id=42,
        cookie="auth_token=session-token; other=value",
        x_cluster="us-west-1#pre",
    )

    assert _build_debug_context_headers(current_user) == {
        "cookie": "auth_token=session-token; other=value",
        "x-cluster": "us-west-1#pre",
    }


@pytest.mark.asyncio
async def test_pyromind_validation_context_uses_websocket_user_headers():
    service = _FakeEventService({PYROMIND_APP_TAG_KEY: PYROMIND_APP_TAG_VALUE})
    current_user = CurrentLoginUser(
        username="debug-user-42",
        email="debug-user-42@example.test",
        user_id=42,
        cookie="auth_token=websocket-token; other=value",
        x_cluster="websocket-cluster",
    )

    await apply_pyromind_validation_context(cast(EventService, service), current_user)

    assert service.secrets == {
        PYROMIND_VALIDATE_AUTH_COOKIE_SECRET: "auth_token=websocket-token; other=value",
        PYROMIND_AUTH_TOKEN_SECRET: "websocket-token",
        PYROMIND_STORAGE_AUTH_COOKIE_SECRET: "auth_token=websocket-token; other=value",
    }
    assert service.agent_state == {
        PYROMIND_VALIDATE_HEADERS_STATE_KEY: {"x-cluster": "websocket-cluster"},
        PYROMIND_STORAGE_HEADERS_STATE_KEY: {"x-cluster": "websocket-cluster"},
    }


def test_build_workflow_run_tool_wires_env_headers_and_auth_token():
    request = _make_request(
        {
            "cookie": f"{PYROMIND_AUTH_COOKIE_NAME}=jwt-token",
            "x-cluster": "us-west-1#pre",
            "accept-language": "en-US",
        }
    )
    load_base_env(request)

    tool, secrets = _build_workflow_run_tool(request)

    assert tool.name == RunWorkflowTool.name
    assert tool.params == {
        "current_user": None,
        "env": "pre",
        "cluster": "us-west-1",
        "headers": {
            "x-cluster": "us-west-1#pre",
            "accept-language": "en-US",
            "request-app": "openhands",
        },
    }
    assert secrets["auth_token"].get_value() == "jwt-token"


def test_build_workflow_run_tool_does_not_persist_current_user_cookie():
    request = _make_request(
        {
            "cookie": f"{PYROMIND_AUTH_COOKIE_NAME}=jwt-token",
            "x-cluster": "us-west-1#pre",
        }
    )
    request.state.current_user = CurrentLoginUser(
        username="debug-user",
        email="debug-user@example.test",
        user_id=100,
        cookie="auth_token=jwt-token; other=value",
        x_cluster="us-west-1#pre",
    )
    load_base_env(request)

    tool, secrets = _build_workflow_run_tool(request)

    current_user = tool.params["current_user"]
    assert isinstance(current_user, CurrentLoginUser)
    assert current_user.cookie is None
    assert secrets["auth_token"].get_value() == "jwt-token"


def test_parse_auth_token_from_cookie_header_extracts_jwt():
    from openhands.agent_server.pyromind_auth import parse_auth_token_from_cookie_header

    assert (
        parse_auth_token_from_cookie_header("auth_token=jwt-token; other=value")
        == "jwt-token"
    )
    assert parse_auth_token_from_cookie_header(None) is None


@pytest.mark.asyncio
async def test_pyromind_validation_context_ignores_non_pyromind_conversations():
    service = _FakeEventService({})
    current_user = CurrentLoginUser(
        username="debug-user-42",
        email="debug-user-42@example.test",
        user_id=42,
        cookie="auth_token=websocket-token",
        x_cluster="websocket-cluster",
    )

    await apply_pyromind_validation_context(cast(EventService, service), current_user)

    assert service.secrets == {}
    assert service.agent_state == {}
