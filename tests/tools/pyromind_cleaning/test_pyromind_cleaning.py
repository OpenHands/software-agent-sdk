from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock
from uuid import UUID

import pytest
from pyromind_sdk.client.models import TrainingTaskCreateResponse

from openhands.sdk.conversation.secret_registry import SecretRegistry
from openhands.tools.pyromind_cleaning import (
    DatasetCleaningTaskAssociation,
    DatasetCleaningTaskStore,
    RunDatasetCleaningAction,
    RunDatasetCleaningExecutor,
    RunDatasetCleaningTool,
)


_CONVERSATION_ID = UUID("00000000-0000-0000-0000-000000000123")


def _secret_registry() -> SecretRegistry:
    registry = SecretRegistry()
    registry.update_secrets({"auth_token": "session-token"})
    return registry


def _fake_conversation(tmp_path: Path):
    workspace = type(
        "FakeWorkspace",
        (),
        {"working_dir": str(tmp_path / "conversations" / _CONVERSATION_ID.hex)},
    )()
    state = type(
        "FakeState",
        (),
        {
            "secret_registry": _secret_registry(),
            "agent_state": {},
        },
    )()
    return type(
        "FakeConversation",
        (),
        {"id": _CONVERSATION_ID, "workspace": workspace, "state": state},
    )()


def _patch_valid_script_preflight(monkeypatch) -> MagicMock:
    download = MagicMock(return_value=b"def main():\n    return 0\n")
    monkeypatch.setattr(
        "openhands.tools.pyromind_cleaning.definition.download_file_from_pyromind",
        download,
    )
    return download


def test_cleaning_tool_derives_execution_target_from_legacy_headers():
    tool = RunDatasetCleaningTool.create(
        headers={"x-cluster": "us-west-1#pre"},
        secret_headers={"cookie": "PYROMIND_STORAGE_AUTH_COOKIE"},
        endpoint_url="https://legacy.test/std2/studio_api/api/prompt",
    )[0]

    executor = tool.executor
    assert isinstance(executor, RunDatasetCleaningExecutor)
    assert executor._env == "pre"
    assert executor._cluster == "us-west-1"


def test_run_dataset_cleaning_submits_fixed_workflow_and_persists_task(
    monkeypatch,
    tmp_path,
):
    mock_client = MagicMock()
    mock_client.studio.create.return_value = TrainingTaskCreateResponse(
        task_id="9876",
        name="agent-data-clean",
        status="Pending",
    )
    client_factory = MagicMock(return_value=mock_client)
    monkeypatch.setattr(
        "openhands.tools.pyromind_cleaning.definition.create_workflow_api_client",
        client_factory,
    )
    download_script = _patch_valid_script_preflight(monkeypatch)
    upload_runtime = MagicMock(
        side_effect=lambda **kwargs: (
            f"{kwargs['target_dir']}/{kwargs['local_path'].name}"
        )
    )
    monkeypatch.setattr(
        "openhands.tools.pyromind_cleaning.definition.upload_local_file_to_pyromind",
        upload_runtime,
    )
    task_store_dir = tmp_path / "tasks"
    conversation = _fake_conversation(tmp_path)
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    (runtime_dir / "cleaning_utils.py").write_text("__all__ = []\n")
    (runtime_dir / "validate_format.py").write_text("# validator\n")

    observation = RunDatasetCleaningExecutor(
        env="pre",
        cluster="us-west-1",
        headers={"x-cluster": "us-west-1#pre", "request-app": "openhands"},
        runtime_dir=str(runtime_dir),
        storage_base_url="https://storage.test/api",
        storage_headers={"x-cluster": "us-west-1"},
        task_store_dir=str(task_store_dir),
        timeout=5,
    )(
        RunDatasetCleaningAction(
            input_path="datasets/source.jsonl",
            script_path="/agentTest/clean.py",
            limit=25,
        ),
        cast(Any, conversation),
    )

    assert not observation.is_error
    assert observation.task_id == "9876"
    assert observation.run_id is not None
    assert observation.output_dir == (
        f"/.pyromind-agent/{_CONVERSATION_ID}/data_cleaning/{observation.run_id}"
    )
    assert observation.resumed is False
    assert f"{observation.output_dir}/report.json" in observation.text
    assert "then output.jsonl" in observation.text
    assert upload_runtime.call_count == 2
    download_script.assert_called_once()
    assert {
        call.kwargs["local_path"].name for call in upload_runtime.call_args_list
    } == {"cleaning_utils.py", "validate_format.py"}
    assert all(
        call.kwargs["target_dir"] == observation.output_dir
        for call in upload_runtime.call_args_list
    )
    client_factory.assert_called_once_with(
        env="pre",
        cluster="us-west-1",
        auth_token="session-token",
        headers={"x-cluster": "us-west-1#pre", "request-app": "openhands"},
        timeout=5,
    )

    request = mock_client.studio.create.call_args.args[0]
    assert request.out_id == f"agent1#{_CONVERSATION_ID}"
    workflow = request.workflow
    assert workflow["id"] == observation.run_id
    assert workflow["edges"] == []
    assert len(workflow["nodes"]) == 1
    node = workflow["nodes"][0]
    assert node["data"]["nodeType"] == "CustomCommandNode"
    assert node["data"]["config"]["cpu"] == 4
    command = node["data"]["config"]["command"]
    pod_output_dir = f"/target-workspace{observation.output_dir}"
    assert (
        f"mkdir -p /target-workspace/.pyromind-agent/{_CONVERSATION_ID}/data_cleaning"
    ) in command
    assert f"mkdir -p {pod_output_dir}" in command
    assert (
        f"cp /target-workspace/agentTest/clean.py {pod_output_dir}/clean_script.py"
    ) in command
    assert "--input /target-workspace/datasets/source.jsonl" in command
    assert f"--output {pod_output_dir}/output.jsonl" in command
    assert f"--state-dir {pod_output_dir}" in command
    assert "--limit 25" in command
    assert "--resume" not in command
    assert (
        f"python3 {pod_output_dir}/validate_format.py "
        f"--input {pod_output_dir}/output.jsonl "
        f"--report {pod_output_dir}/report.json"
    ) in command

    association = DatasetCleaningTaskStore(task_store_dir).get("9876")
    assert association is not None
    assert association.conversation_id == str(_CONVERSATION_ID)
    assert association.run_id == observation.run_id
    assert association.output_dir == observation.output_dir
    assert association.input_path == "/datasets/source.jsonl"
    assert association.script_path == "/agentTest/clean.py"
    assert association.limit == 25
    assert association.resumed is False


def test_run_dataset_cleaning_resume_uses_frozen_script(monkeypatch, tmp_path):
    mock_client = MagicMock()
    mock_client.studio.create.return_value = TrainingTaskCreateResponse(
        task_id="task-2",
        name="agent-data-clean",
        status="Pending",
    )
    monkeypatch.setattr(
        "openhands.tools.pyromind_cleaning.definition.create_workflow_api_client",
        MagicMock(return_value=mock_client),
    )
    run_id = UUID("10000000-0000-0000-0000-000000000001")
    task_store_dir = tmp_path / "tasks"
    task_store = DatasetCleaningTaskStore(task_store_dir)
    task_store.save(
        DatasetCleaningTaskAssociation(
            task_id="task-1",
            conversation_id=str(_CONVERSATION_ID),
            run_id=str(run_id),
            output_dir=f"/agentTest/data_cleaning/{run_id}",
            input_path="/datasets/source.jsonl",
            script_path="/agentTest/original.py",
        )
    )

    observation = RunDatasetCleaningExecutor(
        env="pre",
        cluster="us-west-1",
        task_store_dir=str(task_store_dir),
    )(
        RunDatasetCleaningAction(
            input_path="/datasets/source.jsonl",
            resume_run_id=run_id,
        ),
        cast(Any, _fake_conversation(tmp_path)),
    )

    assert not observation.is_error
    assert observation.run_id == str(run_id)
    assert observation.resumed is True
    request = mock_client.studio.create.call_args.args[0]
    assert request.out_id == f"agent1#{_CONVERSATION_ID}"
    command = request.workflow["nodes"][0]["data"]["config"]["command"]
    frozen_script = (
        f"/target-workspace/agentTest/data_cleaning/{run_id}/clean_script.py"
    )
    assert command.startswith(f"test -f {frozen_script}")
    assert f"test -f {frozen_script.rsplit('/', 1)[0]}/cleaning_utils.py" in command
    assert f"test -f {frozen_script.rsplit('/', 1)[0]}/validate_format.py" in command
    assert f"python3 {frozen_script}" in command
    assert "--resume" in command
    assert "cp " not in command
    assert "--report" in command
    association = task_store.get("task-2")
    assert association is not None
    assert association.script_path == "/agentTest/original.py"


def test_run_dataset_cleaning_rejects_unknown_resume(tmp_path):
    run_id = UUID("20000000-0000-0000-0000-000000000001")

    observation = RunDatasetCleaningExecutor(
        env="pre",
        cluster="us-west-1",
        task_store_dir=str(tmp_path / "tasks"),
    )(
        RunDatasetCleaningAction(
            input_path="/datasets/source.jsonl",
            resume_run_id=run_id,
        ),
        cast(Any, _fake_conversation(tmp_path)),
    )

    assert observation.is_error
    assert f"unknown dataset cleaning run {run_id}" in observation.text


@pytest.mark.parametrize(
    ("input_path", "script_path", "expected_error"),
    [
        ("/datasets/../secret.jsonl", "/agentTest/clean.py", "contain '..'"),
        ("/datasets/source.jsonl", "/agentTest/clean.sh", "Python .py file"),
    ],
)
def test_run_dataset_cleaning_rejects_invalid_paths(
    input_path,
    script_path,
    expected_error,
    tmp_path,
):
    observation = RunDatasetCleaningExecutor(
        env="pre",
        cluster="us-west-1",
        task_store_dir=str(tmp_path / "tasks"),
    )(
        RunDatasetCleaningAction(
            input_path=input_path,
            script_path=script_path,
        ),
        cast(Any, _fake_conversation(tmp_path)),
    )

    assert observation.is_error
    assert expected_error in observation.text


def test_run_dataset_cleaning_reports_runtime_storage_auth_failure(
    monkeypatch,
    tmp_path,
):
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    (runtime_dir / "cleaning_utils.py").write_text("__all__ = []\n")
    (runtime_dir / "validate_format.py").write_text("# validator\n")
    _patch_valid_script_preflight(monkeypatch)
    monkeypatch.setattr(
        "openhands.tools.pyromind_cleaning.definition.upload_local_file_to_pyromind",
        MagicMock(side_effect=ValueError("login required")),
    )

    observation = RunDatasetCleaningExecutor(
        runtime_dir=str(runtime_dir),
        task_store_dir=str(tmp_path / "tasks"),
    )(
        RunDatasetCleaningAction(
            input_path="/datasets/source.jsonl",
            script_path="/agentTest/clean.py",
        ),
        cast(Any, _fake_conversation(tmp_path)),
    )

    assert observation.is_error
    assert (
        "Failed to stage dataset cleaning runtime cleaning_utils.py" in observation.text
    )
    assert "login required" in observation.text


@pytest.mark.parametrize(
    ("script", "expected"),
    [
        (b"def main():\n", "syntax error"),
        (
            b"from cleaning_utils import undocumented_helper\n",
            "unsupported cleaning_utils APIs: undocumented_helper",
        ),
        (
            b"from cleaning_utils import *\n",
            "must import explicit cleaning_utils APIs",
        ),
        (
            b"import cleaning_utils\n",
            "must use explicit from cleaning_utils import",
        ),
    ],
)
def test_run_dataset_cleaning_preflights_script_before_submission(
    monkeypatch,
    tmp_path,
    script,
    expected,
):
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    (runtime_dir / "cleaning_utils.py").write_text(
        '__all__ = ["run_cleaning"]\n', encoding="utf-8"
    )
    (runtime_dir / "validate_format.py").write_text("# validator\n")
    monkeypatch.setattr(
        "openhands.tools.pyromind_cleaning.definition.download_file_from_pyromind",
        MagicMock(return_value=script),
    )
    submit = MagicMock()
    monkeypatch.setattr(
        "openhands.tools.pyromind_cleaning.definition.create_workflow_api_client",
        submit,
    )

    observation = RunDatasetCleaningExecutor(
        runtime_dir=str(runtime_dir),
        task_store_dir=str(tmp_path / "tasks"),
    )(
        RunDatasetCleaningAction(
            input_path="/datasets/source.jsonl",
            script_path="/agentTest/clean.py",
        ),
        cast(Any, _fake_conversation(tmp_path)),
    )

    assert observation.is_error
    assert expected in observation.text
    submit.assert_not_called()
