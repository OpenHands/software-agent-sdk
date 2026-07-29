import importlib.util
import json
import sys
import types
from pathlib import Path
from typing import Any

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
)
from openhands.tools.data_preparation.runner import (
    build_dataflow_env,
    openai_compatible_model_name,
    run_dataflow_python,
    summarize_dataflow_env,
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


def test_multimodal_pipeline_supports_arbitrary_field_policy(tmp_path: Path) -> None:
    pipeline = _load_skill_reference(
        "multimodal_pipeline.py",
        stub_dataflow=True,
    )
    for index in range(3):
        _write_image(tmp_path / f"image-{index}.jpg", (index, index, index))
    record = {
        "sample_id": "sample-1",
        "image_paths": [f"image-{index}.jpg" for index in range(3)],
        "image_labels": ["one", "two", "three"],
        "prompt": "Compare.",
        "reference_annotations": {
            "review_state": "keep",
            "review_basis": "ground truth",
        },
        "metadata": {"batch": "b1"},
    }

    normalized = pipeline._normalize_record(record, tmp_path / "manifest.jsonl")
    assert normalized["_resolved_image_labels"] == ["one", "two", "three"]
    assert len(normalized["_resolved_image_paths"]) == 3

    setattr(pipeline, "REFERENCE_INPUT_FIELDS", ("review_state",))
    setattr(
        pipeline,
        "PROTECTED_REFERENCE_FIELDS",
        ("review_state", "review_basis"),
    )
    assert "keep" in pipeline.build_user_prompt(normalized)
    assert "ground truth" not in pipeline.build_user_prompt(normalized)
    setattr(pipeline, "REASONING_FIELD", "explanation")
    setattr(pipeline, "ANSWER_FIELD", "review_state")
    assert (
        pipeline.assemble_training_response(
            normalized,
            {"explanation": "generated"},
        )
        == "<think>generated</think>\n\n<answer>keep</answer>"
    )


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("A", "A"),
        ("plain text", "plain text"),
        ({"decision": "keep"}, '{"decision":"keep"}'),
    ],
)
def test_multimodal_pipeline_assembles_task_selected_response_fields(
    answer: Any,
    expected: str,
) -> None:
    pipeline = _load_skill_reference(
        "multimodal_pipeline.py",
        stub_dataflow=True,
    )
    setattr(pipeline, "REASONING_FIELD", "analysis_trace")
    setattr(pipeline, "ANSWER_FIELD", "final_decision")
    if isinstance(answer, dict):
        setattr(pipeline, "ANSWER_IS_JSON", True)
    record = {"reference_annotations": {}}

    response = pipeline.assemble_training_response(
        record,
        {
            "analysis_trace": "Task-specific reasoning.",
            "final_decision": answer,
        },
    )

    assert response == (
        f"<think>Task-specific reasoning.</think>\n\n<answer>{expected}</answer>"
    )
    pipeline.validate_training_response(response)


def test_multimodal_pipeline_preserves_batch_order_and_isolates_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline = _load_skill_reference(
        "multimodal_pipeline.py",
        stub_dataflow=True,
    )

    class FakeVlm:
        def generate_from_input_multi_images(
            self,
            image_paths,
            image_labels,
            **kwargs,
        ):
            del image_labels, kwargs
            if any("bad" in paths[0] for paths in image_paths):
                raise RuntimeError("bad sample secret-key")
            return [f"response:{Path(paths[0]).name}" for paths in image_paths]

    records = [
        {
            "sample_id": "first",
            "prompt": "one",
            "_resolved_image_paths": ["/tmp/first.jpg"],
            "_resolved_image_labels": ["first"],
            "reference_annotations": {},
        },
        {
            "sample_id": "bad",
            "prompt": "bad",
            "_resolved_image_paths": ["/tmp/bad.jpg"],
            "_resolved_image_labels": ["bad"],
            "reference_annotations": {},
        },
        {
            "sample_id": "last",
            "prompt": "last",
            "_resolved_image_paths": ["/tmp/last.jpg"],
            "_resolved_image_labels": ["last"],
            "reference_annotations": {},
        },
    ]

    monkeypatch.setenv("DF_API_KEY", "secret-key")
    generated, failed = pipeline._generate_with_isolation(FakeVlm(), records)

    assert list(generated) == ["first", "last"]
    assert generated["first"] == "response:first.jpg"
    assert generated["last"] == "response:last.jpg"
    assert failed == {"bad": "bad sample <redacted>"}


def test_multimodal_pipeline_limit_is_not_a_runtime_gate(tmp_path: Path) -> None:
    pipeline = _load_skill_reference(
        "multimodal_pipeline.py",
        stub_dataflow=True,
    )
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text(
        "\n".join(
            json.dumps(
                {
                    "sample_id": f"sample-{index}",
                    "prompt": "Inspect.",
                    "image_paths": ["image.jpg"],
                }
            )
            for index in range(5)
        ),
        encoding="utf-8",
    )

    assert len(pipeline._read_manifest(manifest, 3)) == 3
    assert len(pipeline._read_manifest(manifest, 4)) == 4
    assert len(pipeline._read_manifest(manifest, None)) == 5


def test_multimodal_pipeline_reports_resolved_field_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline = _load_skill_reference(
        "multimodal_pipeline.py",
        stub_dataflow=True,
    )
    _write_image(tmp_path / "image.jpg", (1, 2, 3))
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text(
        json.dumps(
            {
                "sample_id": "sample",
                "image_paths": ["image.jpg"],
                "prompt": "Inspect.",
                "reference_annotations": {
                    "review_state": "accepted",
                    "private_note": "do not show",
                },
            }
        ),
        encoding="utf-8",
    )

    class FakeVlm:
        def __init__(self, **kwargs):
            del kwargs

        def generate_from_input_multi_images(self, *args, **kwargs):
            del args, kwargs
            return ['{"explanation":"looks correct"}']

        def cleanup(self):
            return None

    setattr(pipeline, "_load_vlm_class", lambda: FakeVlm)
    setattr(pipeline, "OUTPUT_JSON_SCHEMA", {"type": "object"})
    setattr(pipeline, "REFERENCE_INPUT_FIELDS", ("review_state",))
    setattr(pipeline, "PROTECTED_REFERENCE_FIELDS", ("review_state",))
    setattr(pipeline, "REASONING_FIELD", "explanation")
    setattr(pipeline, "ANSWER_FIELD", "review_state")
    setattr(pipeline, "FIELD_POLICY_RATIONALE", "Derived from the request.")
    monkeypatch.setenv("DF_API_BASE_URL", "https://example.com/v1")
    monkeypatch.setenv("DF_MODEL_NAME", "vision-model")
    output = tmp_path / "processed.sample.jsonl"

    pipeline.run(str(manifest), str(output), limit=3)

    report = json.loads(
        (tmp_path / "processed.sample.report.json").read_text(encoding="utf-8")
    )
    assert report["succeeded"] == 1
    assert report["field_policy"] == {
        "model_reads": {
            "images": True,
            "sample_prompt": True,
            "reference_annotation_fields": ["review_state"],
            "metadata": False,
        },
        "generated_fields": ["explanation"],
        "preserved_reference_fields": ["review_state"],
        "training_prompt": "sample prompt",
        "training_response": (
            "<think> from explanation; <answer> from review_state; "
            "protected reference values win when fields overlap"
        ),
        "rationale": "Derived from the request.",
    }
    processed = json.loads(output.read_text(encoding="utf-8"))
    assert processed["training_system_prompt"] == pipeline.TRAINING_SYSTEM_PROMPT
    assert processed["generated_annotations"] == {"explanation": "looks correct"}
    assert processed["training_response"] == (
        "<think>looks correct</think>\n\n<answer>accepted</answer>"
    )


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
