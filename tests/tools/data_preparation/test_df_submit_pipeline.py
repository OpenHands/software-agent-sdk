"""Tests for the DataFlow platform submission tool."""

import uuid
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic import SecretStr

from openhands.tools.data_preparation.platform_submit import (
    RUNTIME_FILENAMES,
    DataPreparationTaskAssociation,
    DataPreparationTaskStore,
    DfSubmitPipelineAction,
    DfSubmitPipelineExecutor,
    ReuseAssessment,
    _build_dataflow_command,
    _build_dataflow_workflow,
    _build_llm_env,
    _file_sha256,
    _model_fingerprint,
    _normalize_storage_path,
    _pod_path,
)


# ---------------------------------------------------------------------------
# _normalize_storage_path
# ---------------------------------------------------------------------------


def test_normalize_storage_path_basic() -> None:
    assert _normalize_storage_path("/foo/bar", "x") == "/foo/bar"
    assert _normalize_storage_path("foo/bar", "x") == "/foo/bar"
    assert _normalize_storage_path("/foo//bar/", "x") == "/foo/bar"
    assert _normalize_storage_path("/./foo/./bar", "x") == "/foo/bar"


def test_normalize_storage_path_errors() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        _normalize_storage_path("", "field")
    with pytest.raises(ValueError, match="non-empty"):
        _normalize_storage_path("   ", "field")
    with pytest.raises(ValueError, match="control characters"):
        _normalize_storage_path("/foo\x00bar", "field")
    with pytest.raises(ValueError, match="'\\.\\.'"):
        _normalize_storage_path("/foo/../bar", "field")
    with pytest.raises(ValueError, match="root"):
        _normalize_storage_path("/", "field")


# ---------------------------------------------------------------------------
# _pod_path
# ---------------------------------------------------------------------------


def test_pod_path() -> None:
    assert _pod_path("/agentTest/data/file.py") == (
        "/target-workspace/agentTest/data/file.py"
    )


# ---------------------------------------------------------------------------
# _build_dataflow_command
# ---------------------------------------------------------------------------


def test_build_dataflow_command_structure() -> None:
    cmd = _build_dataflow_command(
        input_path="/data/input.jsonl",
        output_dir="/output/run1",
        llm_env={
            "DF_API_KEY": "sk-test",
            "DF_API_URL": "http://localhost/v1/chat/completions",
            "DF_MODEL_NAME": "gpt-4o",
        },
        convert_format="none",
        runtime_dir_name="runtime-r1",
        runtime_fingerprint="runtime-sha",
        image_utils_api_version="1",
    )
    assert "python3 -m venv /tmp/df-venv" in cmd
    assert "pip install --use-deprecated=legacy-resolver open-dataflow==1.0.10" in cmd
    assert "mkdir -p" in cmd
    assert "/target-workspace/data/input.jsonl" in cmd
    assert "/target-workspace/output/run1/pipeline.py" in cmd
    assert "DF_API_KEY=" in cmd
    assert "DF_API_URL=" in cmd
    assert "DF_MODEL_NAME=" in cmd
    assert "DF_LOG_DIR=" in cmd
    assert "generate_report.py" in cmd
    assert "runtime-r1/image_utils.py" in cmd
    assert "PYTHONPATH=" in cmd
    assert "DF_RUNTIME_FINGERPRINT=runtime-sha" in cmd
    assert "--image-utils-api-version 1" in cmd
    assert " && " in cmd
    assert "cp " not in cmd


def test_build_dataflow_command_validates_dpo_schema() -> None:
    cmd = _build_dataflow_command(
        input_path="/data/input.jsonl",
        output_dir="/output/run1",
        llm_env={
            "DF_API_KEY": "sk-test",
            "DF_API_URL": "http://localhost/v1/chat/completions",
            "DF_MODEL_NAME": "gpt-4o",
        },
        convert_format="none",
        runtime_dir_name="runtime-r1",
        image_utils_api_version="1",
        output_schema="dpo",
    )

    assert "validate_prepared_data.py" in cmd
    assert "--schema dpo" in cmd
    assert "--image-root" not in cmd


# ---------------------------------------------------------------------------
# _build_dataflow_workflow
# ---------------------------------------------------------------------------


def test_build_dataflow_workflow() -> None:
    run_id = uuid.uuid4()
    action = DfSubmitPipelineAction(
        script_path="/scripts/pipeline.py",
        input_path="/data/input.jsonl",
        cpu=8,
        memory=64,
    )
    wf = _build_dataflow_workflow(action, run_id, "echo hello")
    assert wf["id"] == str(run_id)
    assert wf["name"].startswith("agent-data-prep-")
    assert len(wf["nodes"]) == 1
    node_data = wf["nodes"][0]["data"]
    assert node_data["nodeType"] == "CustomCommandNode"
    config = node_data["config"]
    assert config["command"] == "echo hello"
    assert config["cpu"] == 8
    assert config["memory"] == 64
    assert config["gpu_count"] == 0


# ---------------------------------------------------------------------------
# _build_llm_env
# ---------------------------------------------------------------------------


def _make_conversation_with_llm(
    api_key: str | SecretStr | None = "sk-123",
    base_url: str | None = "http://my-llm/v1",
    model: str = "openai/gpt-4o",
) -> Any:
    llm = MagicMock()
    llm.api_key = api_key
    llm.base_url = base_url
    llm.model = model

    state = MagicMock()
    state.agent.llm = llm

    conv = MagicMock()
    conv.state = state
    return conv


def test_build_llm_env_secret_str() -> None:
    conv = _make_conversation_with_llm(api_key=SecretStr("sk-secret"))
    env = _build_llm_env(conv)
    assert env["DF_API_KEY"] == "sk-secret"
    assert env["DF_API_URL"] == "http://my-llm/v1/chat/completions"
    assert env["DF_MODEL_NAME"] == "gpt-4o"


def test_build_llm_env_plain_str() -> None:
    conv = _make_conversation_with_llm(api_key="sk-plain")
    env = _build_llm_env(conv)
    assert env["DF_API_KEY"] == "sk-plain"


def test_build_llm_env_no_key() -> None:
    conv = _make_conversation_with_llm(api_key=None)
    env = _build_llm_env(conv)
    assert "DF_API_KEY" not in env


def test_build_llm_env_no_base_url() -> None:
    conv = _make_conversation_with_llm(base_url=None)
    env = _build_llm_env(conv)
    assert env["DF_API_URL"] == ("https://api.openai.com/v1/chat/completions")


def test_build_llm_env_model_no_prefix() -> None:
    conv = _make_conversation_with_llm(model="gpt-4o-mini")
    env = _build_llm_env(conv)
    assert env["DF_MODEL_NAME"] == "gpt-4o-mini"


# ---------------------------------------------------------------------------
# DataPreparationTaskStore
# ---------------------------------------------------------------------------


def test_task_store_roundtrip(tmp_path: Path) -> None:
    store = DataPreparationTaskStore(tmp_path / "tasks")
    assoc = DataPreparationTaskAssociation(
        task_id="task-1",
        conversation_id="conv-1",
        run_id="run-1",
        output_dir="/out/run-1",
        input_path="/data/in.jsonl",
        script_path="/scripts/p.py",
        runtime_fingerprint="runtime-sha",
        runtime_dir_name="runtime-r1",
        image_utils_api_version="1",
        status="Running",
    )
    store.save(assoc)

    loaded = store.get("task-1")
    assert loaded is not None
    assert loaded.task_id == "task-1"
    assert loaded.conversation_id == "conv-1"
    assert loaded.run_id == "run-1"
    assert loaded.output_dir == "/out/run-1"
    assert loaded.runtime_fingerprint == "runtime-sha"
    assert loaded.runtime_dir_name == "runtime-r1"
    assert loaded.image_utils_api_version == "1"
    assert loaded.schema_version == 3
    assert loaded.status == "Running"


def test_task_store_get_by_run_id(tmp_path: Path) -> None:
    store = DataPreparationTaskStore(tmp_path / "tasks")
    assoc = DataPreparationTaskAssociation(
        task_id="task-2",
        conversation_id="conv-2",
        run_id="run-abc",
        output_dir="/out/run-abc",
        input_path="/data/in.jsonl",
        script_path="/scripts/p.py",
    )
    store.save(assoc)
    found = store.get_by_run_id("run-abc")
    assert found is not None
    assert found.task_id == "task-2"
    assert store.get_by_run_id("nonexistent") is None


def test_task_store_get_by_output_dir(tmp_path: Path) -> None:
    store = DataPreparationTaskStore(tmp_path / "tasks")
    assoc = DataPreparationTaskAssociation(
        task_id="task-3",
        conversation_id="conv-3",
        run_id="run-xyz",
        output_dir="/out/run-xyz",
        input_path="/data/in.jsonl",
        script_path="/scripts/p.py",
    )
    store.save(assoc)
    found = store.get_by_output_dir("/out/run-xyz/")
    assert found is not None
    assert found.task_id == "task-3"
    assert store.get_by_output_dir("/out/missing") is None


def test_task_store_get_missing(tmp_path: Path) -> None:
    store = DataPreparationTaskStore(tmp_path / "tasks")
    assert store.get("nonexistent") is None


def test_task_association_reads_legacy_runtime_location() -> None:
    loaded = DataPreparationTaskAssociation.from_dict(
        {
            "schema_version": 2,
            "task_id": "legacy-task",
            "conversation_id": "conv",
            "run_id": "run",
            "output_dir": "/out/run",
            "input_path": "/data/in.jsonl",
            "script_path": "/scripts/pipeline.py",
        }
    )

    assert loaded.runtime_dir_name == ""
    assert loaded.runtime_fingerprint is None
    assert loaded.image_utils_api_version is None


# ---------------------------------------------------------------------------
# DfSubmitPipelineExecutor — validation errors
# ---------------------------------------------------------------------------


def _make_executor(tmp_path: Path, **kwargs: Any) -> DfSubmitPipelineExecutor:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(exist_ok=True)
    for filename in RUNTIME_FILENAMES:
        content = (
            "__all__ = ['ImagePipelineConfig', "
            "'MultiImageSemanticLabelOperator', 'run_image_pipeline', "
            "'run_image_pipeline_from_cli']\n"
            if filename == "image_utils.py"
            else "# runtime\n"
        )
        (runtime_dir / filename).write_text(content)
    defaults: dict[str, Any] = {
        "runtime_dir": str(runtime_dir),
        "storage_base_url": "http://storage.test",
        "task_store_dir": str(tmp_path / "tasks"),
    }
    defaults.update(kwargs)
    return DfSubmitPipelineExecutor(**defaults)


def test_executor_no_conversation(tmp_path: Path) -> None:
    executor = _make_executor(tmp_path)
    action = DfSubmitPipelineAction(
        script_path="/scripts/p.py",
        input_path="/data/in.jsonl",
    )
    obs = executor(action, conversation=None)
    assert obs.status == "Failed"
    assert obs.is_error


def test_executor_invalid_script_ext(tmp_path: Path) -> None:
    executor = _make_executor(tmp_path)
    script = tmp_path / "pipeline.txt"
    script.write_text("print('hi')")
    action = DfSubmitPipelineAction(
        script_path=str(script),
        input_path="/data/in.jsonl",
    )
    conv = _make_conversation_with_llm()
    conv.id = "conv-1"
    obs = executor(action, conversation=conv)
    assert obs.status == "Failed"
    assert ".py" in obs.text


def test_executor_missing_runtime_dir(tmp_path: Path) -> None:
    executor = _make_executor(tmp_path, runtime_dir=None)
    script = tmp_path / "p.py"
    script.write_text("print('hi')")
    action = DfSubmitPipelineAction(
        script_path=str(script),
        input_path="/data/in.jsonl",
    )
    conv = _make_conversation_with_llm()
    conv.id = "conv-1"
    obs = executor(action, conversation=conv)
    assert obs.status == "Failed"
    assert "runtime_dir" in obs.text


def test_stage_runtime_files_uses_revision_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    executor = _make_executor(tmp_path)
    uploads: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "openhands.tools.data_preparation.platform_submit."
        "upload_local_file_to_pyromind",
        lambda *, local_path, target_dir, **kwargs: uploads.append(
            (Path(local_path).name, target_dir)
        ),
    )

    executor._stage_runtime_files(
        "/agentTest/data_preparation/run",
        _make_conversation_with_llm(),
        runtime_dir_name="runtime-r2",
    )

    assert {name for name, _ in uploads} == set(RUNTIME_FILENAMES)
    assert {target for _, target in uploads} == {
        "/agentTest/data_preparation/run/runtime-r2"
    }


def _saved_prior_run(
    tmp_path: Path,
    *,
    conversation: Any,
    script: Path,
) -> DataPreparationTaskAssociation:
    llm_env = _build_llm_env(conversation, "text")
    association = DataPreparationTaskAssociation(
        task_id="task-prior",
        conversation_id="conv-1",
        run_id="11111111-1111-1111-1111-111111111111",
        output_dir=("/agentTest/data_preparation/11111111-1111-1111-1111-111111111111"),
        input_path="/data/in.jsonl",
        script_path=str(script),
        pipeline_fingerprint=_file_sha256(script),
        model_fingerprint=_model_fingerprint(llm_env),
        model_profile="text",
        output_schema="text",
    )
    DataPreparationTaskStore(tmp_path / "tasks").save(association)
    return association


def test_resume_changed_pipeline_requires_reuse_assessment(
    tmp_path: Path,
) -> None:
    executor = _make_executor(tmp_path)
    original = tmp_path / "original.py"
    original.write_text("print('old')")
    changed = tmp_path / "changed.py"
    changed.write_text("print('new')")
    conversation = _make_conversation_with_llm()
    conversation.id = "conv-1"
    _saved_prior_run(tmp_path, conversation=conversation, script=original)

    observation = executor(
        DfSubmitPipelineAction(
            mode="resume",
            resume_run_id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
            input_path="/data/in.jsonl",
            script_path=str(changed),
        ),
        conversation=conversation,
    )

    assert observation.is_error
    assert "Provide reuse_assessment" in observation.text


def test_compatible_resume_accepts_agent_assessment(
    monkeypatch,
    tmp_path: Path,
) -> None:
    executor = _make_executor(tmp_path)
    original = tmp_path / "original.py"
    original.write_text("print('old')")
    changed = tmp_path / "changed.py"
    changed.write_text("print('new')")
    conversation = _make_conversation_with_llm()
    conversation.id = "conv-1"
    conversation.workspace = MagicMock()
    conversation.workspace.working_dir = str(tmp_path / "conversation")
    _saved_prior_run(tmp_path, conversation=conversation, script=original)

    staged: list[str] = []
    monkeypatch.setattr(
        executor,
        "_stage_runtime_files",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        executor,
        "_stage_script",
        lambda *args, **kwargs: staged.append(kwargs["frozen_script_name"]),
    )
    monkeypatch.setattr(
        "openhands.tools.data_preparation.platform_submit.create_workflow_api_client",
        lambda **kwargs: object(),
    )
    response = MagicMock()
    response.task_id = "task-resume"
    response.status = "Pending"
    monkeypatch.setattr(
        "openhands.tools.data_preparation.platform_submit.submit_workflow_task",
        lambda **kwargs: response,
    )

    observation = executor(
        DfSubmitPipelineAction(
            mode="resume",
            resume_run_id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
            input_path="/data/in.jsonl",
            script_path=str(changed),
            reuse_assessment=ReuseAssessment(
                changed_dimensions=["pipeline", "runtime"],
                change_summary="Only fixes the corrupt-image branch.",
                reason="Committed rows do not execute the changed branch.",
                verification_samples=["failed", "previous", "same-kind"],
                verification_result="passed",
            ),
        ),
        conversation=conversation,
    )

    assert not observation.is_error
    assert observation.resumed is True
    assert observation.execution_revision == 2
    assert staged == ["pipeline-r2.py"]
    saved = DataPreparationTaskStore(tmp_path / "tasks").get("task-resume")
    assert saved is not None
    assert saved.reuse_assessment is not None
    assert saved.reuse_assessment["decision"] == "compatible_resume"
