"""Tests for the DataFlow platform submission tool."""

import uuid
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic import SecretStr

from openhands.tools.data_preparation.platform_submit import (
    DataPreparationTaskAssociation,
    DataPreparationTaskStore,
    DfSubmitPipelineAction,
    DfSubmitPipelineExecutor,
    _build_dataflow_command,
    _build_dataflow_workflow,
    _build_llm_env,
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
    assert " && " in cmd
    assert "cp " not in cmd


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
        status="Running",
    )
    store.save(assoc)

    loaded = store.get("task-1")
    assert loaded is not None
    assert loaded.task_id == "task-1"
    assert loaded.conversation_id == "conv-1"
    assert loaded.run_id == "run-1"
    assert loaded.output_dir == "/out/run-1"
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


def test_task_store_get_missing(tmp_path: Path) -> None:
    store = DataPreparationTaskStore(tmp_path / "tasks")
    assert store.get("nonexistent") is None


# ---------------------------------------------------------------------------
# DfSubmitPipelineExecutor — validation errors
# ---------------------------------------------------------------------------


def _make_executor(tmp_path: Path, **kwargs: Any) -> DfSubmitPipelineExecutor:
    defaults: dict[str, Any] = {
        "runtime_dir": str(tmp_path / "runtime"),
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
