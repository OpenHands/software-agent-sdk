"""Tests for df_logging.py and generate_report.py runtime scripts."""

import importlib.util
import json
import sys
import threading
from pathlib import Path
from typing import Any

import pytest
from PIL import Image


SCRIPTS_DIR = (
    Path(__file__).resolve().parents[3]
    / ".agents"
    / "skills"
    / "data-preparation"
    / "scripts"
)


def _import_from_scripts(module_name: str) -> Any:
    """Import a module from the data-preparation scripts directory."""
    path = SCRIPTS_DIR / f"{module_name}.py"
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def df_logging() -> Any:
    return _import_from_scripts("df_logging")


@pytest.fixture()
def generate_report_mod() -> Any:
    return _import_from_scripts("generate_report")


@pytest.fixture()
def preparation_runtime() -> Any:
    return _import_from_scripts("preparation_runtime")


@pytest.fixture()
def validate_prepared_data() -> Any:
    return _import_from_scripts("validate_prepared_data")


# ---------------------------------------------------------------------------
# LoggingLLMServing tests
# ---------------------------------------------------------------------------


class _FakeInnerLLM:
    """Mimics APILLMServing_request for testing."""

    def __init__(self, responses: list[str | None] | None = None):
        self.model_name = "test-model"
        self.api_url = "http://test/v1"
        self._responses = responses or []
        self._call_count = 0

    def _api_chat_id_retry(
        self,
        id: int,
        payload: Any,
        model: str,
        is_embedding: bool = False,
        json_schema: dict | None = None,
    ) -> tuple[int, str | None]:
        idx = self._call_count
        self._call_count += 1
        if idx < len(self._responses):
            return id, self._responses[idx]
        return id, f"response-{id}"

    def _run_threadpool(self, task_args_list: list[dict], desc: str = "") -> list[str]:
        results = []
        for args in task_args_list:
            _, response = self._api_chat_id_retry(
                args["id"],
                args["payload"],
                args["model"],
                args.get("is_embedding", False),
                args.get("json_schema"),
            )
            results.append(response or "")
        return results

    def start_serving(self) -> None:
        pass

    def cleanup(self) -> None:
        pass

    def format_response(self, response: dict, is_embedding: bool = False) -> str:
        return str(response)


def test_logging_basic(df_logging: Any, tmp_path: Path) -> None:
    inner = _FakeInnerLLM(["hello", "world"])
    llm = df_logging.LoggingLLMServing(inner, log_dir=str(tmp_path))

    results = llm.generate_from_input(["q1", "q2"], "system")
    assert results == ["hello", "world"]

    llm.close()
    log_path = tmp_path / "llm_calls.jsonl"
    assert log_path.exists()
    lines = log_path.read_text().strip().splitlines()
    assert len(lines) == 2

    record = json.loads(lines[0])
    assert record["seq"] == 0
    assert record["status"] == "success"
    assert record["response"] == "hello"
    assert record["model"] == "test-model"
    assert "latency_ms" in record
    assert "timestamp" in record


def test_logging_stats(df_logging: Any, tmp_path: Path) -> None:
    inner = _FakeInnerLLM(["ok", None, "ok2"])
    llm = df_logging.LoggingLLMServing(inner, log_dir=str(tmp_path))

    llm.generate_from_input(["a", "b", "c"])
    stats = llm.stats
    assert stats["total"] == 3
    assert stats["success"] == 2
    assert stats["failed"] == 1
    llm.close()


def test_logging_error_propagation(df_logging: Any, tmp_path: Path) -> None:
    class _ErrorLLM(_FakeInnerLLM):
        def _api_chat_id_retry(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("API down")

    inner = _ErrorLLM()
    llm = df_logging.LoggingLLMServing(inner, log_dir=str(tmp_path))

    with pytest.raises(RuntimeError, match="API down"):
        llm._api_chat_id_retry(0, [{"role": "user", "content": "x"}], "m")

    llm.close()
    lines = (tmp_path / "llm_calls.jsonl").read_text().strip().splitlines()
    record = json.loads(lines[0])
    assert record["status"] == "error"
    assert "API down" in record["error"]


def test_logging_thread_safety(df_logging: Any, tmp_path: Path) -> None:
    inner = _FakeInnerLLM()
    llm = df_logging.LoggingLLMServing(inner, log_dir=str(tmp_path))

    def worker() -> None:
        for _ in range(10):
            llm._api_chat_id_retry(0, [{"role": "user", "content": "t"}], "m")

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    llm.close()
    lines = (tmp_path / "llm_calls.jsonl").read_text().strip().splitlines()
    assert len(lines) == 40
    seqs = {json.loads(line)["seq"] for line in lines}
    assert len(seqs) == 40


def test_logging_attr_passthrough(df_logging: Any, tmp_path: Path) -> None:
    inner = _FakeInnerLLM()
    llm = df_logging.LoggingLLMServing(inner, log_dir=str(tmp_path))
    assert llm.model_name == "test-model"
    assert llm.api_url == "http://test/v1"
    llm.close()


# ---------------------------------------------------------------------------
# generate_report tests
# ---------------------------------------------------------------------------


def test_report_no_calls(generate_report_mod: Any, tmp_path: Path) -> None:
    report = generate_report_mod.generate_report(str(tmp_path))
    assert report["status"] == "no_llm_calls"
    assert report["llm_calls"]["total"] == 0
    assert report["total_records_output"] == 0


def test_report_all_success(generate_report_mod: Any, tmp_path: Path) -> None:
    calls = [
        {
            "seq": 0,
            "status": "success",
            "latency_ms": 100,
            "timestamp": "2025-01-01T00:00:00+00:00",
        },
        {
            "seq": 1,
            "status": "success",
            "latency_ms": 200,
            "timestamp": "2025-01-01T00:00:05+00:00",
        },
    ]
    _write_jsonl(tmp_path / "llm_calls.jsonl", calls)
    _write_jsonl(tmp_path / "processed.jsonl", [{"a": 1}, {"b": 2}])

    report = generate_report_mod.generate_report(str(tmp_path))
    assert report["status"] == "succeeded"
    assert report["llm_calls"]["total"] == 2
    assert report["llm_calls"]["success"] == 2
    assert report["llm_calls"]["failed"] == 0
    assert report["llm_calls"]["success_rate"] == 100.0
    assert report["llm_calls"]["avg_latency_ms"] == 150.0
    assert report["total_records_output"] == 2
    assert report["duration_seconds"] == 5.0


def test_report_partial_failure(generate_report_mod: Any, tmp_path: Path) -> None:
    calls = [
        {
            "seq": 0,
            "status": "success",
            "latency_ms": 50,
            "timestamp": "2025-01-01T00:00:00+00:00",
            "request_messages": [{"role": "user", "content": "hello world"}],
        },
        {
            "seq": 1,
            "status": "error",
            "latency_ms": 300,
            "timestamp": "2025-01-01T00:00:01+00:00",
            "error": "timeout",
            "request_messages": [{"role": "user", "content": "fail input"}],
        },
    ]
    _write_jsonl(tmp_path / "llm_calls.jsonl", calls)

    report = generate_report_mod.generate_report(str(tmp_path))
    assert report["status"] == "partial_failure"
    assert report["llm_calls"]["failed"] == 1
    assert len(report["error_samples"]) == 1
    sample = report["error_samples"][0]
    assert sample["status"] == "error"
    assert sample["error"] == "timeout"
    assert sample["input_preview"] == "fail input"


def test_report_all_failed(generate_report_mod: Any, tmp_path: Path) -> None:
    calls = [
        {"seq": 0, "status": "error", "latency_ms": 10, "error": "e1"},
        {"seq": 1, "status": "error", "latency_ms": 20, "error": "e2"},
    ]
    _write_jsonl(tmp_path / "llm_calls.jsonl", calls)

    report = generate_report_mod.generate_report(str(tmp_path))
    assert report["status"] == "failed"
    assert report["llm_calls"]["success"] == 0
    assert report["llm_calls"]["failed"] == 2


def test_report_merges_checkpoint_validation_and_revision(
    generate_report_mod: Any,
    tmp_path: Path,
) -> None:
    (tmp_path / "checkpoint.json").write_text(
        json.dumps(
            {
                "next_source_index": 3,
                "committed_records": 3,
                "output_offset": 120,
            }
        )
    )
    (tmp_path / "validation.json").write_text(
        json.dumps({"status": "failed", "error": "duplicate id"})
    )

    report = generate_report_mod.generate_report(
        str(tmp_path),
        execution_revision=2,
        resumed=True,
    )

    assert report["status"] == "failed"
    assert report["execution_revision"] == 2
    assert report["resumed"] is True
    assert report["checkpoint"]["next_source_index"] == 3
    assert report["validation"]["error"] == "duplicate id"


def test_report_reads_dataflow_batch_checkpoint_and_runtime_metadata(
    generate_report_mod: Any,
    tmp_path: Path,
) -> None:
    (tmp_path / "processed.jsonl").write_text('{"id":"one"}\n')
    (tmp_path / "image_pipeline_last_success_step.txt").write_text("0,1\n")
    (tmp_path / "runtime_metadata.json").write_text(
        json.dumps(
            {
                "batch_size": 8,
                "record_count": 20,
                "manifest_fingerprint": "manifest-sha",
                "runtime_fingerprint": "runtime-sha",
                "image_utils_api_version": "1",
            }
        )
    )

    report = generate_report_mod.generate_report(
        str(tmp_path),
        pipeline_exit_code=1,
        runtime_dir_name="runtime-r2",
    )

    assert report["checkpoint"] == {
        "kind": "dataflow_batch",
        "path": "image_pipeline_last_success_step.txt",
        "operator_step": 0,
        "next_batch": 1,
        "batch_size": 8,
        "next_source_index": 8,
        "committed_records": 1,
    }
    assert report["resumable"] is True
    assert report["runtime_fingerprint"] == "runtime-sha"
    assert report["runtime_dir_name"] == "runtime-r2"
    assert report["image_utils_api_version"] == "1"


def test_checkpoint_commit_and_resume_truncates_uncommitted_tail(
    preparation_runtime: Any,
    tmp_path: Path,
) -> None:
    output = tmp_path / "processed.jsonl"
    checkpoint = preparation_runtime.commit_record(
        output_path=output,
        state_dir=tmp_path,
        source_index=0,
        row={"id": "one"},
    )
    committed_content = output.read_bytes()
    with output.open("ab") as target:
        target.write(b'{"id":"uncommitted"}\n')

    loaded = preparation_runtime.load_checkpoint(tmp_path)
    assert loaded == checkpoint
    preparation_runtime.prepare_resume_output(output, loaded)

    assert output.read_bytes() == committed_content
    assert loaded.next_source_index == 1


def test_vision_client_retries_transient_failure_and_repairs_output(
    monkeypatch,
    preparation_runtime: Any,
    tmp_path: Path,
) -> None:
    image = tmp_path / "image.png"
    image.write_bytes(b"png")

    class Response:
        def __init__(
            self,
            status_code: int,
            payload: dict[str, Any],
            text: str = "",
        ) -> None:
            self.status_code = status_code
            self._payload = payload
            self.text = text

        def json(self) -> dict[str, Any]:
            return self._payload

    responses = iter(
        [
            Response(429, {}, "rate limited"),
            Response(
                200,
                {"choices": [{"message": {"content": "invalid"}}]},
            ),
            Response(
                200,
                {"choices": [{"message": {"content": "valid"}}]},
            ),
        ]
    )
    monkeypatch.setattr(
        preparation_runtime.httpx,
        "post",
        lambda *args, **kwargs: next(responses),
    )
    monkeypatch.setattr(preparation_runtime.time, "sleep", lambda seconds: None)

    client = preparation_runtime.RetryingVisionClient(
        api_url="https://vision.test/chat/completions",
        model="gemma",
        max_attempts=3,
    )

    def validate(value: str) -> None:
        if value != "valid":
            raise ValueError("bad schema")

    result, attempts = client.generate(
        prompt="label",
        image_paths=[image],
        validate=validate,
    )

    assert result == "valid"
    assert attempts == 3


def test_validate_canonical_text_jsonl(
    validate_prepared_data: Any,
    tmp_path: Path,
) -> None:
    output = tmp_path / "processed.jsonl"
    _write_jsonl(
        output,
        [
            {
                "id": "text-1",
                "system_prompt": "You are helpful.",
                "user_prompt": "Question",
                "gt": "Answer",
            }
        ],
    )

    result = validate_prepared_data.validate_jsonl(output, schema="text")
    assert result == {"status": "passed", "schema": "text", "rows": 1}


def test_validate_canonical_dpo_jsonl(
    validate_prepared_data: Any,
    tmp_path: Path,
) -> None:
    output = tmp_path / "processed.jsonl"
    _write_jsonl(
        output,
        [
            {
                "id": "dpo-1",
                "system_prompt": "You are helpful.",
                "user_prompt": "Question",
                "gt": "Chosen answer",
                "rejected_answer": "Rejected answer",
            }
        ],
    )

    result = validate_prepared_data.validate_jsonl(output, schema="dpo")
    assert result == {"status": "passed", "schema": "dpo", "rows": 1}


def test_validate_dpo_rejects_equal_answers(
    validate_prepared_data: Any,
    tmp_path: Path,
) -> None:
    output = tmp_path / "processed.jsonl"
    _write_jsonl(
        output,
        [
            {
                "id": "dpo-1",
                "system_prompt": "system",
                "user_prompt": "user",
                "gt": "same",
                "rejected_answer": " same ",
            }
        ],
    )

    with pytest.raises(ValueError, match="gt and rejected_answer"):
        validate_prepared_data.validate_jsonl(output, schema="dpo")


def test_validate_canonical_vision_jsonl(
    validate_prepared_data: Any,
    tmp_path: Path,
) -> None:
    images = tmp_path / "images"
    images.mkdir()
    Image.new("RGB", (1, 1), "white").save(images / "one.png")
    output = tmp_path / "processed.jsonl"
    _write_jsonl(
        output,
        [
            {
                "id": "vision-1",
                "image_path": "images/one.png",
                "images": ["images/one.png"],
                "system_prompt": "You are helpful.",
                "user_prompt": "Question",
                "gt": "<think>reason</think>\n\n<answer>A</answer>",
            }
        ],
    )

    result = validate_prepared_data.validate_jsonl(
        output,
        schema="vision",
        image_root=tmp_path,
    )
    assert result["rows"] == 1


def test_validate_jsonl_rejects_extra_fields(
    validate_prepared_data: Any,
    tmp_path: Path,
) -> None:
    output = tmp_path / "processed.jsonl"
    _write_jsonl(
        output,
        [
            {
                "id": "text-1",
                "system_prompt": "system",
                "user_prompt": "user",
                "gt": "answer",
                "source_path": "must-not-leak",
            }
        ],
    )

    with pytest.raises(ValueError, match="extra=.*source_path"):
        validate_prepared_data.validate_jsonl(output, schema="text")


def test_validate_dpo_rejects_extra_fields(
    validate_prepared_data: Any,
    tmp_path: Path,
) -> None:
    output = tmp_path / "processed.jsonl"
    _write_jsonl(
        output,
        [
            {
                "id": "dpo-1",
                "system_prompt": "system",
                "user_prompt": "user",
                "gt": "chosen",
                "rejected_answer": "rejected",
                "source_path": "must-not-leak",
            }
        ],
    )

    with pytest.raises(ValueError, match="extra=.*source_path"):
        validate_prepared_data.validate_jsonl(output, schema="dpo")


def _write_jsonl(path: Path, records: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
