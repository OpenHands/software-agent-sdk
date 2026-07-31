import ast
import importlib.util
import json
import sys
import types
from pathlib import Path
from typing import Any, cast

import pyarrow.parquet as pq
import pytest
from PIL import Image
from pydantic import SecretStr

from openhands.sdk.conversation.secret_registry import SecretRegistry
from openhands.sdk.workspace.workspace import LocalWorkspace
from openhands.tools.data_preparation.definition import (
    DfConvertAction,
    DfConvertExecutor,
    DfConvertObservation,
    DfRunPipelineAction,
    DfRunPipelineExecutor,
)
from openhands.tools.data_preparation.runner import (
    build_dataflow_env,
    openai_compatible_model_name,
    run_dataflow_python,
    runtime_public_names,
    summarize_dataflow_env,
    validate_managed_image_pipeline,
)


def _fake_conversation(
    tmp_path: Path,
    *,
    secret_registry: SecretRegistry | None = None,
    agent_state: dict[str, Any] | None = None,
):
    return type(
        "FakeConversation",
        (),
        {
            "workspace": LocalWorkspace(working_dir=str(tmp_path)),
            "state": type(
                "FakeState",
                (),
                {
                    "secret_registry": secret_registry or SecretRegistry(),
                    "agent_state": agent_state or {},
                },
            )(),
        },
    )()


def test_openai_compatible_model_name() -> None:
    assert openai_compatible_model_name("gpt-4o-mini") == "gpt-4o-mini"
    assert openai_compatible_model_name("openai/gpt-4o") == "gpt-4o"
    assert (
        openai_compatible_model_name("openrouter/anthropic/claude-3-5-sonnet")
        == "anthropic/claude-3-5-sonnet"
    )


def _conversation_with_llm() -> Any:
    llm = type(
        "FakeLlm",
        (),
        {
            "api_key": SecretStr("secret"),
            "base_url": "https://example.com/v1/",
            "model": "openai/vision-model",
        },
    )()
    return type(
        "FakeConversation",
        (),
        {
            "state": type(
                "FakeState",
                (),
                {"agent": type("FakeAgent", (), {"llm": llm})()},
            )()
        },
    )()


def test_build_dataflow_env_includes_vlm_base_url(
    monkeypatch,
) -> None:
    for name in (
        "DF_API_KEY",
        "DF_API_URL",
        "DF_API_BASE_URL",
        "DF_MODEL_NAME",
    ):
        monkeypatch.delenv(name, raising=False)

    env = build_dataflow_env(_conversation_with_llm())

    assert env == {
        "DF_API_KEY": "secret",
        "DF_API_URL": "https://example.com/v1/chat/completions",
        "DF_API_BASE_URL": "https://example.com/v1",
        "DF_MODEL_NAME": "vision-model",
    }


def test_build_dataflow_env_prefers_global_vision_model(monkeypatch) -> None:
    monkeypatch.setenv("DF_API_KEY", "openrouter-secret")
    monkeypatch.setenv("DF_API_BASE_URL", "https://openrouter.ai/api/v1/")
    monkeypatch.setenv(
        "DF_API_URL",
        "https://openrouter.ai/api/v1/chat/completions/",
    )
    monkeypatch.setenv("DF_MODEL_NAME", "google/gemma-4-31b-it")

    env = build_dataflow_env(_conversation_with_llm())

    assert env == {
        "DF_API_KEY": "openrouter-secret",
        "DF_API_URL": "https://openrouter.ai/api/v1/chat/completions",
        "DF_API_BASE_URL": "https://openrouter.ai/api/v1",
        "DF_MODEL_NAME": "google/gemma-4-31b-it",
    }
    summary = summarize_dataflow_env(env)
    assert "google/gemma-4-31b-it" in summary
    assert "openrouter-secret" not in summary


def test_build_dataflow_env_text_profile_uses_conversation_model(
    monkeypatch,
) -> None:
    monkeypatch.setenv("DF_API_KEY", "vision-secret")
    monkeypatch.setenv("DF_API_BASE_URL", "https://vision.example/v1")
    monkeypatch.setenv(
        "DF_API_URL",
        "https://vision.example/v1/chat/completions",
    )
    monkeypatch.setenv("DF_MODEL_NAME", "gemma")

    env = build_dataflow_env(_conversation_with_llm(), "text")

    assert env["DF_MODEL_NAME"] == "vision-model"
    assert env["DF_API_BASE_URL"] == "https://example.com/v1"
    assert env["DF_API_KEY"] == "secret"


def test_build_dataflow_env_derives_missing_url_pair(monkeypatch) -> None:
    monkeypatch.setenv("DF_API_KEY", "secret")
    monkeypatch.setenv(
        "DF_API_URL",
        "https://openrouter.ai/api/v1/chat/completions",
    )
    monkeypatch.delenv("DF_API_BASE_URL", raising=False)
    monkeypatch.setenv("DF_MODEL_NAME", "vision-model")

    env = build_dataflow_env(_conversation_with_llm())

    assert env["DF_API_BASE_URL"] == "https://openrouter.ai/api/v1"


def test_build_dataflow_env_rejects_mismatched_urls(monkeypatch) -> None:
    monkeypatch.setenv("DF_API_KEY", "secret")
    monkeypatch.setenv("DF_API_BASE_URL", "https://one.example/v1")
    monkeypatch.setenv(
        "DF_API_URL",
        "https://two.example/v1/chat/completions",
    )
    monkeypatch.setenv("DF_MODEL_NAME", "vision-model")

    with pytest.raises(ValueError, match="do not describe the same endpoint"):
        build_dataflow_env(_conversation_with_llm())


def test_run_dataflow_python_redacts_api_key(tmp_path: Path) -> None:
    api_key = "secret-that-must-not-leak"

    return_code, stdout, stderr = run_dataflow_python(
        sys.executable,
        [
            "-c",
            (
                "import os, sys; "
                "print(os.environ['DF_API_KEY']); "
                "print(os.environ['DF_API_KEY'], file=sys.stderr)"
            ),
        ],
        cwd=str(tmp_path),
        env_extra={"DF_API_KEY": api_key},
        timeout=60,
    )

    assert return_code == 0
    assert api_key not in stdout + stderr
    assert "<redacted>" in stdout
    assert "<redacted>" in stderr


def test_df_run_pipeline_validates_output_and_writes_local_report(
    tmp_path: Path,
) -> None:
    pipeline_dir = tmp_path / "public_data" / "data-preparation"
    pipeline_dir.mkdir(parents=True)
    pipeline = pipeline_dir / "pipeline.py"
    pipeline.write_text(
        "\n".join(
            [
                "import json, sys",
                "row = {",
                "  'id': 'text-1',",
                "  'system_prompt': 'system',",
                "  'user_prompt': 'question',",
                "  'gt': 'answer',",
                "}",
                "with open(sys.argv[2], 'w', encoding='utf-8') as f:",
                "    f.write(json.dumps(row) + '\\n')",
            ]
        ),
        encoding="utf-8",
    )
    input_path = pipeline_dir / "input.jsonl"
    input_path.write_text("{}\n", encoding="utf-8")
    scripts_dir = (
        Path(__file__).parents[3]
        / ".agents"
        / "skills"
        / "data-preparation"
        / "scripts"
    )
    conversation = cast(Any, _fake_conversation(tmp_path))
    conversation.state.agent = _conversation_with_llm().state.agent

    observation = DfRunPipelineExecutor(runtime_dir=str(scripts_dir))(
        DfRunPipelineAction(
            pipeline_path="public_data/data-preparation/pipeline.py",
            args=[
                "public_data/data-preparation/input.jsonl",
                "public_data/data-preparation/processed.sample.jsonl",
            ],
            output_schema="text",
            model_profile="text",
        ),
        conversation,
    )

    assert not observation.is_error
    assert observation.exit_code == 0
    assert observation.report_path is not None
    report = json.loads(Path(observation.report_path).read_text())
    assert report["status"] == "succeeded"
    assert report["validation"]["status"] == "passed"
    assert report["total_records_output"] == 1
    assert (pipeline_dir / "processed.sample.jsonl").is_file()
    assert not (pipeline_dir / "public_data").exists()


def test_df_run_pipeline_rejects_handwritten_vision_transport(
    tmp_path: Path,
) -> None:
    pipeline_dir = tmp_path / "public_data" / "data-preparation"
    pipeline_dir.mkdir(parents=True)
    (pipeline_dir / "pipeline.py").write_text(
        "import base64\n"
        "from image_utils import ImagePipelineConfig, "
        "run_image_pipeline_from_cli\n"
    )
    (pipeline_dir / "input.jsonl").write_text("{}\n")
    scripts_dir = (
        Path(__file__).parents[3]
        / ".agents"
        / "skills"
        / "data-preparation"
        / "scripts"
    )
    conversation = cast(Any, _fake_conversation(tmp_path))
    conversation.state.agent = _conversation_with_llm().state.agent

    observation = DfRunPipelineExecutor(runtime_dir=str(scripts_dir))(
        DfRunPipelineAction(
            pipeline_path="public_data/data-preparation/pipeline.py",
            args=[
                "public_data/data-preparation/input.jsonl",
                "public_data/data-preparation/processed.sample.jsonl",
            ],
            output_schema="vision",
            model_profile="vision",
        ),
        conversation,
    )

    assert observation.is_error
    assert "cannot import base64" in observation.text


def test_df_convert_messages_format(tmp_path: Path) -> None:
    # Prepare input
    input_file = tmp_path / "input.jsonl"
    input_file.write_text(
        json.dumps({"problem": "1+1=?", "reasoning": "1+1=2", "answer": "2"})
        + "\n"
        + json.dumps({"problem": "2+2=?", "answer": "4"})
        + "\n"
        + json.dumps({"no_field": "x"})
    )
    output_file = tmp_path / "messages.jsonl"

    # Run convert
    action = DfConvertAction(
        input_path=str(input_file),
        output_path=str(output_file),
        format="messages",
        text_field="problem",
        reasoning_field="reasoning",
        answer_field="answer",
        system_prompt="Solve the problem step by step.",
    )
    executor = DfConvertExecutor()
    conv = _fake_conversation(tmp_path)
    obs = executor(action, conv)

    # Check output
    assert isinstance(obs, DfConvertObservation)
    assert obs.converted == 2
    assert obs.skipped == 1
    assert output_file.exists()
    records = [json.loads(line) for line in output_file.read_text().splitlines()]
    assert len(records) == 2
    assert records[0]["messages"][0]["content"] == "Solve the problem step by step."
    assert records[0]["messages"][1]["content"] == "1+1=?"
    assert records[0]["messages"][2]["content"] == "1+1=2\n\n2"
    assert records[1]["messages"][0]["content"] == "Solve the problem step by step."
    assert records[1]["messages"][1]["content"] == "2+2=?"
    assert records[1]["messages"][2]["content"] == "4"


def test_df_convert_preference_format(tmp_path: Path) -> None:
    # Prepare input
    input_file = tmp_path / "input.jsonl"
    input_file.write_text(
        json.dumps({"prompt": "1+1=?", "chosen": "2", "rejected": "3"})
        + "\n"
        + json.dumps({"prompt": "2+2=?", "chosen": "4"})
    )
    output_file = tmp_path / "preference.jsonl"

    # Run convert
    action = DfConvertAction(
        input_path=str(input_file),
        output_path=str(output_file),
        format="preference",
        text_field="prompt",
        chosen_field="chosen",
        rejected_field="rejected",
    )
    executor = DfConvertExecutor()
    conv = _fake_conversation(tmp_path)
    obs = executor(action, conv)

    assert isinstance(obs, DfConvertObservation)
    assert obs.converted == 1
    assert obs.skipped == 1
    assert obs.output_path == str(output_file)
    assert obs.columns == ["prompt", "chosen", "rejected"]
    assert output_file.exists()
    records = [json.loads(line) for line in output_file.read_text().splitlines()]
    assert len(records) == 1
    assert records[0]["prompt"] == "1+1=?"
    assert records[0]["chosen"] == "2"
    assert records[0]["rejected"] == "3"


def _write_image(path: Path, color: tuple[int, int, int]) -> None:
    Image.new("RGB", (4, 4), color).save(path)


def test_df_convert_trl_vision_sft_parquet(tmp_path: Path) -> None:
    _write_image(tmp_path / "front.jpg", (255, 0, 0))
    _write_image(tmp_path / "detail.png", (0, 255, 0))
    input_file = tmp_path / "processed.jsonl"
    input_file.write_text(
        json.dumps(
            {
                "training_prompt": "Compare the images.",
                "training_response": {"label": "ok", "reason": "They match."},
                "image_paths": ["front.jpg", "detail.png"],
                "image_labels": ["Front view", "Detail view"],
            }
        )
        + "\n"
        + json.dumps(
            {
                "training_prompt": "Missing response.",
                "image_paths": ["front.jpg"],
            }
        ),
        encoding="utf-8",
    )
    output_file = tmp_path / "train.parquet"
    action = DfConvertAction(
        input_path=str(input_file),
        output_path=str(output_file),
        format="trl_vision_sft",
    )

    obs = DfConvertExecutor()(action, _fake_conversation(tmp_path))

    assert not obs.is_error
    assert obs.converted == 1
    assert obs.skipped == 1
    assert obs.output_path == str(output_file)
    assert obs.columns == ["messages", "images"]
    table = pq.read_table(output_file)
    assert b"huggingface" in (table.schema.metadata or {})
    row = table.to_pylist()[0]
    assert [image["path"] for image in row["images"]] == ["front.jpg", "detail.png"]
    assert all(image["bytes"] for image in row["images"])
    user = row["messages"][0]
    assert user["role"] == "user"
    assert user["content"] == [
        {"type": "text", "text": "Compare the images."},
        {"type": "text", "text": "Front view"},
        {"type": "image", "text": None},
        {"type": "text", "text": "Detail view"},
        {"type": "image", "text": None},
    ]
    assistant = row["messages"][1]
    assert assistant["content"][0]["text"] == ('{"label":"ok","reason":"They match."}')

    try:
        from datasets import load_dataset
    except ImportError:
        return
    loaded: Any = load_dataset(
        "parquet",
        data_files=str(output_file),
        split="train",
        cache_dir=str(tmp_path / "hf-cache"),
    )
    decoded: Any = loaded[0]
    assert len(decoded["images"]) == 2
    assert all(isinstance(image, Image.Image) for image in decoded["images"])


def test_df_convert_trl_vision_rejects_mismatched_labels(tmp_path: Path) -> None:
    _write_image(tmp_path / "image.jpg", (0, 0, 255))
    input_file = tmp_path / "processed.jsonl"
    input_file.write_text(
        json.dumps(
            {
                "training_prompt": "Describe.",
                "training_response": "Done.",
                "image_paths": ["image.jpg"],
                "image_labels": ["one", "extra"],
            }
        ),
        encoding="utf-8",
    )
    output_file = tmp_path / "train.parquet"
    action = DfConvertAction(
        input_path=str(input_file),
        output_path=str(output_file),
        format="trl_vision_sft",
    )

    obs = DfConvertExecutor()(action, _fake_conversation(tmp_path))

    assert not obs.is_error
    assert obs.converted == 0
    assert obs.skipped == 1
    assert pq.read_table(output_file).num_rows == 0


def test_df_convert_trl_vision_rejects_unsafe_or_invalid_image(
    tmp_path: Path,
) -> None:
    outside = tmp_path.parent / "outside.jpg"
    _write_image(outside, (0, 0, 0))
    input_file = tmp_path / "processed.jsonl"
    input_file.write_text(
        json.dumps(
            {
                "training_prompt": "Describe.",
                "training_response": "Done.",
                "image_paths": [str(outside)],
            }
        ),
        encoding="utf-8",
    )
    output_file = tmp_path / "train.parquet"
    action = DfConvertAction(
        input_path=str(input_file),
        output_path=str(output_file),
        format="trl_vision_sft",
    )

    obs = DfConvertExecutor()(action, _fake_conversation(tmp_path))

    assert obs.is_error
    assert "outside the workspace" in obs.text

    corrupt = tmp_path / "corrupt.jpg"
    corrupt.write_bytes(b"not an image")
    input_file.write_text(
        json.dumps(
            {
                "training_prompt": "Describe.",
                "training_response": "Done.",
                "image_paths": ["corrupt.jpg"],
            }
        ),
        encoding="utf-8",
    )
    obs = DfConvertExecutor()(action, _fake_conversation(tmp_path))
    assert obs.is_error


def test_df_convert_vision_sft_flat_preserves_ordered_paths(tmp_path: Path) -> None:
    _write_image(tmp_path / "front.jpg", (255, 0, 0))
    _write_image(tmp_path / "detail.png", (0, 255, 0))
    input_file = tmp_path / "processed.jsonl"
    input_file.write_text(
        json.dumps(
            {
                "sample_id": "sample-001",
                "training_system_prompt": "Return the requested judgment.",
                "training_prompt": "Compare the images.",
                "training_response": (
                    "<think>The details agree.</think>\n\n"
                    '<answer>{"status":"ok"}</answer>'
                ),
                "image_paths": ["front.jpg", "detail.png"],
            }
        ),
        encoding="utf-8",
    )
    output_file = tmp_path / "train.parquet"
    action = DfConvertAction(
        input_path=str(input_file),
        output_path=str(output_file),
        format="vision_sft_flat",
        id_field="sample_id",
        system_prompt_field="training_system_prompt",
        prompt_field="training_prompt",
        response_field="training_response",
        images_field="image_paths",
    )

    obs = DfConvertExecutor()(action, _fake_conversation(tmp_path))

    assert not obs.is_error
    assert obs.converted == 1
    assert obs.skipped == 0
    assert obs.columns == [
        "id",
        "image_path",
        "images",
        "system_prompt",
        "user_prompt",
        "gt",
    ]
    assert obs.column_schema == {
        "id": "string",
        "image_path": "string",
        "images": "list<string>",
        "system_prompt": "string",
        "user_prompt": "string",
        "gt": "string",
    }
    assert obs.image_path_mode == "ordered paths (not embedded)"
    table = pq.read_table(output_file)
    assert table.column_names == obs.columns
    assert (
        table.schema.field("images").type.value_type
        == table.schema.field("image_path").type
    )
    assert table.to_pylist() == [
        {
            "id": "sample-001",
            "image_path": "front.jpg",
            "images": ["front.jpg", "detail.png"],
            "system_prompt": "Return the requested judgment.",
            "user_prompt": "Compare the images.",
            "gt": (
                '<think>The details agree.</think>\n\n<answer>{"status":"ok"}</answer>'
            ),
        }
    ]


@pytest.mark.parametrize(
    ("records", "message"),
    [
        (
            [
                {
                    "sample_id": "same",
                    "training_system_prompt": "system",
                    "training_prompt": "prompt",
                    "training_response": "<think>why</think>\n\n<answer>A</answer>",
                    "image_paths": ["image.jpg"],
                },
                {
                    "sample_id": "same",
                    "training_system_prompt": "system",
                    "training_prompt": "prompt",
                    "training_response": "<think>why</think>\n\n<answer>B</answer>",
                    "image_paths": ["image.jpg"],
                },
            ],
            "duplicate sample_id",
        ),
        (
            [
                {
                    "sample_id": "sample",
                    "training_system_prompt": "system",
                    "training_prompt": "prompt",
                    "training_response": "<answer>A</answer><think>why</think>",
                    "image_paths": ["image.jpg"],
                }
            ],
            "invalid tag order",
        ),
    ],
)
def test_df_convert_vision_sft_flat_rejects_invalid_records(
    tmp_path: Path,
    records: list[dict[str, Any]],
    message: str,
) -> None:
    _write_image(tmp_path / "image.jpg", (0, 0, 255))
    input_file = tmp_path / "processed.jsonl"
    input_file.write_text(
        "\n".join(json.dumps(record) for record in records),
        encoding="utf-8",
    )
    action = DfConvertAction(
        input_path=str(input_file),
        output_path=str(tmp_path / "train.parquet"),
        format="vision_sft_flat",
    )

    obs = DfConvertExecutor()(action, _fake_conversation(tmp_path))

    assert obs.is_error
    assert message in obs.text


def _load_skill_reference(name: str, *, stub_dataflow: bool = False):
    root = Path(__file__).parents[3]
    path = root / ".agents" / "skills" / "data-preparation" / "references" / name
    previous_dataflow = sys.modules.get("dataflow")
    if stub_dataflow:
        dataflow = types.ModuleType("dataflow")
        dataflow.__file__ = str(root / "fake-dataflow" / "__init__.py")
        sys.modules["dataflow"] = dataflow
    try:
        module_name = f"test_data_preparation_{path.stem}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if stub_dataflow:
            if previous_dataflow is None:
                sys.modules.pop("dataflow", None)
            else:
                sys.modules["dataflow"] = previous_dataflow


def _load_image_utils() -> Any:
    root = Path(__file__).parents[3]
    path = (
        root / ".agents" / "skills" / "data-preparation" / "scripts" / "image_utils.py"
    )
    module_name = "test_data_preparation_image_utils"
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(module_name, None)
    return module


def test_data_preparation_runtime_is_python_310_compatible() -> None:
    scripts_dir = (
        Path(__file__).parents[3]
        / ".agents"
        / "skills"
        / "data-preparation"
        / "scripts"
    )
    for path in sorted(scripts_dir.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(
            source,
            filename=str(path),
            feature_version=(3, 10),
        )
        direct_datetime_utc = {
            alias.name
            for node in tree.body
            if isinstance(node, ast.ImportFrom) and node.module == "datetime"
            for alias in node.names
            if alias.name == "UTC"
        }
        assert not direct_datetime_utc, (
            f"{path.name} imports datetime.UTC without a Python 3.10 fallback"
        )
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "typing":
                imported_names = {alias.name for alias in node.names}
                unsupported_typing = imported_names & {"Self", "TypeAliasType"}
                assert not unsupported_typing, (
                    f"{path.name} imports Python 3.11+ typing API(s): "
                    f"{sorted(unsupported_typing)}"
                )
            if isinstance(node, ast.Attribute):
                assert not (
                    isinstance(node.value, ast.Name)
                    and node.value.id == "datetime"
                    and node.attr == "UTC"
                ), f"{path.name} uses datetime.UTC, which requires Python 3.11"


def test_image_utils_multi_image_prompt_retry_and_output_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_utils = _load_image_utils()
    for name, color in (
        ("front.jpg", (1, 2, 3)),
        ("back.jpg", (4, 5, 6)),
        ("single.jpg", (7, 8, 9)),
    ):
        _write_image(tmp_path / name, color)
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "id": "first",
                        "images": ["front.jpg", "back.jpg"],
                        "user_prompt": "Compare both.",
                    }
                ),
                json.dumps(
                    {
                        "id": "second",
                        "images": ["single.jpg"],
                        "user_prompt": "Inspect one.",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    class FakeServing:
        def __init__(self) -> None:
            self.request_sizes: list[int] = []

        def generate_from_input_multi_images(
            self,
            image_paths,
            image_labels,
            *,
            user_prompts,
            **kwargs,
        ):
            del kwargs
            self.request_sizes.append(len(image_paths))
            if len(self.request_sizes) == 1:
                assert image_labels[0] == ["Image 1", "Image 2"]
                assert "Compare both." in user_prompts[0]
                return [
                    '{"reasoning":"first","answer":"A"}',
                    "not-json",
                ]
            assert len(image_paths) == 1
            return ['{"reasoning":"second","answer":"B"}']

        def cleanup(self):
            return None

    state_dir = tmp_path / "state"
    monkeypatch.setenv("DF_STATE_DIR", str(state_dir))
    monkeypatch.setenv("DF_RESUME", "0")
    fake = FakeServing()
    monkeypatch.setattr(image_utils, "_create_vlm_serving", lambda config: fake)
    config = image_utils.ImagePipelineConfig(
        labeling_system_prompt="Label.",
        training_system_prompt="Train.",
        batch_size=2,
    )
    output = tmp_path / "processed.sample.jsonl"

    image_utils.run_image_pipeline(config, str(manifest), str(output))

    rows = [
        json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()
    ]
    assert fake.request_sizes == [2, 1]
    assert [row["id"] for row in rows] == ["first", "second"]
    assert rows[0]["images"] == ["front.jpg", "back.jpg"]
    assert rows[1]["gt"] == "<think>second</think>\n\n<answer>B</answer>"
    assert set(rows[0]) == {
        "id",
        "image_path",
        "images",
        "system_prompt",
        "user_prompt",
        "gt",
    }


def test_image_utils_dataflow_checkpoint_resume_without_duplicates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_utils = _load_image_utils()
    for name in ("first.jpg", "second.jpg"):
        _write_image(tmp_path / name, (1, 2, 3))
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text(
        "\n".join(
            json.dumps(
                {
                    "id": name,
                    "images": [f"{name}.jpg"],
                    "user_prompt": f"Inspect {name}.",
                }
            )
            for name in ("first", "second")
        )
        + "\n",
        encoding="utf-8",
    )

    class FailingServing:
        calls = 0

        def generate_from_input_multi_images(self, *args, **kwargs):
            del args, kwargs
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("boundary failure")
            return ['{"reasoning":"ok","answer":"A"}']

        def cleanup(self):
            return None

    state_dir = tmp_path / "state"
    output = tmp_path / "processed.jsonl"
    config = image_utils.ImagePipelineConfig(
        labeling_system_prompt="Label.",
        training_system_prompt="Train.",
        batch_size=1,
        max_attempts=1,
    )
    monkeypatch.setenv("DF_STATE_DIR", str(state_dir))
    monkeypatch.setenv("DF_RESUME", "0")
    monkeypatch.setattr(
        image_utils,
        "_create_vlm_serving",
        lambda config: FailingServing(),
    )
    with pytest.raises(ValueError, match="boundary failure"):
        image_utils.run_image_pipeline(config, str(manifest), str(output))

    first_rows = [json.loads(line) for line in output.read_text().splitlines()]
    assert [row["id"] for row in first_rows] == ["first"]
    assert (state_dir / "image_pipeline_last_success_step.txt").read_text() == "0,1\n"

    class SuccessfulServing:
        def generate_from_input_multi_images(self, *args, **kwargs):
            del args, kwargs
            return ['{"reasoning":"fixed","answer":"B"}']

        def cleanup(self):
            return None

    monkeypatch.setenv("DF_RESUME", "1")
    monkeypatch.setattr(
        image_utils,
        "_create_vlm_serving",
        lambda config: SuccessfulServing(),
    )
    image_utils.run_image_pipeline(config, str(manifest), str(output))
    resumed = [json.loads(line) for line in output.read_text().splitlines()]
    assert [row["id"] for row in resumed] == ["first", "second"]


def test_image_utils_converts_webp_for_dataflow(
    tmp_path: Path,
) -> None:
    image_utils = _load_image_utils()
    source = tmp_path / "image.webp"
    Image.new("RGB", (2, 2), "red").save(source)
    converted = image_utils._prepare_vlm_image(source, tmp_path / "state")
    assert converted.suffix == ".png"
    with Image.open(converted) as image:
        assert image.format == "PNG"


def test_image_utils_discards_uncheckpointed_batch_part(tmp_path: Path) -> None:
    image_utils = _load_image_utils()
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text('{"id":"one"}\n')
    state_dir = tmp_path / "state"
    output = tmp_path / "processed.jsonl"
    storage = image_utils.ManagedStreamBatchedFileStorage(
        first_entry_file_name=str(manifest),
        state_dir=state_dir,
        output_path=output,
    )
    storage.parts_dir.mkdir(parents=True)
    (storage.parts_dir / "part-00000000.jsonl").write_text('{"id":"one"}\n')
    (storage.parts_dir / "part-00000001.jsonl").write_text('{"id":"uncommitted"}\n')
    storage.checkpoint_path.write_text("0,1\n")

    storage.materialize_output()

    assert output.read_text() == '{"id":"one"}\n'
    assert not (storage.parts_dir / "part-00000001.jsonl").exists()


def test_managed_image_pipeline_static_contract(tmp_path: Path) -> None:
    image_utils_path = (
        Path(__file__).parents[3]
        / ".agents"
        / "skills"
        / "data-preparation"
        / "scripts"
        / "image_utils.py"
    )
    public_names = runtime_public_names(image_utils_path)
    valid = tmp_path / "valid.py"
    valid.write_text(
        "from image_utils import ImagePipelineConfig, "
        "run_image_pipeline_from_cli\n"
        "CONFIG = ImagePipelineConfig("
        "labeling_system_prompt='x', training_system_prompt='y')\n"
        "run_image_pipeline_from_cli(CONFIG)\n"
    )
    validate_managed_image_pipeline(valid, public_names)

    invalid = tmp_path / "invalid.py"
    invalid.write_text(
        "import base64\n"
        "from image_utils import ImagePipelineConfig, "
        "run_image_pipeline_from_cli\n"
    )
    with pytest.raises(ValueError, match="cannot import base64"):
        validate_managed_image_pipeline(invalid, public_names)


def test_avi_manifest_adapter_is_only_a_boundary_example(tmp_path: Path) -> None:
    adapter = _load_skill_reference("avi_manifest_adapter.py")
    sample = tmp_path / "1_B1"
    sample.mkdir()
    for name, color in zip(
        ("defect.jpg", "diff.jpg", "gt.jpg"),
        ((255, 0, 0), (0, 255, 0), (0, 0, 255)),
        strict=True,
    ):
        _write_image(sample / name, color)
    (sample / "meta.json").write_text(
        json.dumps(
            {
                "id": "1/B1",
                "label": "skip",
                "note": "same as reference",
                "part": "part-1",
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "manifest.jsonl"

    assert adapter.convert(str(sample), str(output)) == 1

    record = json.loads(output.read_text(encoding="utf-8"))
    assert record["sample_id"] == "1/B1"
    assert record["image_labels"] == ["AOI检出图", "差分图", "GT参考图"]
    assert record["reference_annotations"] == {
        "label": "skip",
        "note": "same as reference",
    }
    assert record["metadata"]["part"] == "part-1"
