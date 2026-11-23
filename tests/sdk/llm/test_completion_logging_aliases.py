"""Regression tests for the log_completions_to_file / completion_logging_enabled
rename and its deprecated aliases (log_completions, Telemetry.log_enabled).
"""

import contextlib
import json
import warnings

import pytest
from deprecation import DeprecatedWarning
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from openhands.sdk import Message, TextContent
from openhands.sdk.llm import LLM
from openhands.sdk.llm.utils.metrics import Metrics
from openhands.sdk.llm.utils.telemetry import Telemetry
from tests.conftest import create_mock_litellm_response


@pytest.fixture(autouse=True)
def deprecation_release(monkeypatch):
    monkeypatch.setattr(
        "openhands.sdk.observability.laminar.should_enable_observability", lambda: False
    )
    monkeypatch.setattr(
        "openhands.sdk.utils.deprecation._current_version", lambda: "1.63.0"
    )


def _llm(**kwargs) -> LLM:
    return LLM(model="gpt-4o", usage_id="test", **kwargs)


def test_canonical_name_defaults_false_no_warning():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        llm = _llm()
    assert llm.log_completions_to_file is False
    assert llm.log_completions is False


def test_canonical_name_only_no_warning():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        llm = _llm(log_completions_to_file=True)
    assert llm.log_completions_to_file is True
    assert llm.log_completions is True


def test_old_name_only_sets_new_field_and_warns():
    with pytest.warns(DeprecatedWarning):
        llm = LLM(model="gpt-4o", usage_id="test", log_completions=True)
    assert llm.log_completions_to_file is True


def test_old_and_new_equal_values_allowed_without_warning():
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecatedWarning)
        llm = _llm(log_completions=True, log_completions_to_file=True)
    assert llm.log_completions_to_file is True


def test_old_and_new_conflicting_values_raise():
    with pytest.raises(ValueError, match="Conflicting values"):
        _llm(log_completions=True, log_completions_to_file=False)


def test_serialization_includes_legacy_key_for_old_consumers():
    llm = _llm(log_completions_to_file=True)
    dumped = llm.model_dump(mode="json")
    assert dumped["log_completions_to_file"] is True
    assert dumped["log_completions"] is True


def test_old_round_trip_from_persisted_json():
    old_payload = {
        "model": "gpt-4o",
        "usage_id": "test",
        "log_completions": True,
    }
    with pytest.warns(DeprecatedWarning):
        llm = LLM.model_validate(old_payload)
    assert llm.log_completions_to_file is True


@pytest.mark.parametrize("deep", [False, True])
def test_model_copy_with_new_name_updates_telemetry(deep: bool):
    llm = _llm()
    callback_calls: list[tuple[str, str]] = []
    llm.telemetry.set_log_completions_callback(
        lambda filename, data: callback_calls.append((filename, data))
    )
    copied = llm.model_copy(deep=deep, update={"log_completions_to_file": True})
    assert copied.log_completions_to_file is True
    assert copied.telemetry.completion_logging_enabled is True
    # Deep copies own independent metrics; both modes retain the callback.
    if deep:
        assert copied.telemetry.metrics is not llm.telemetry.metrics
    else:
        assert copied.telemetry.metrics is llm.telemetry.metrics
    copied.telemetry.on_request({})
    copied.telemetry.on_response(create_mock_litellm_response(content="COPIED_OK"))
    assert len(callback_calls) == 1
    assert "COPIED_OK" in callback_calls[0][1]
    assert llm.telemetry.completion_logging_enabled is False


@pytest.mark.parametrize("deep", [False, True])
def test_model_copy_with_old_name_updates_new_field_and_warns(deep: bool):
    llm = _llm()
    with pytest.warns(DeprecatedWarning):
        copied = llm.model_copy(deep=deep, update={"log_completions": True})
    assert copied.log_completions_to_file is True
    assert copied.telemetry.completion_logging_enabled is True
    assert llm.telemetry.completion_logging_enabled is False


def test_model_copy_conflicting_names_raise():
    llm = _llm()
    with pytest.raises(ValueError, match="Conflicting values"):
        llm.model_copy(
            update={"log_completions": True, "log_completions_to_file": False}
        )


def test_telemetry_old_kwarg_sets_new_field_and_warns():
    with pytest.warns(DeprecatedWarning):
        telemetry = Telemetry(
            model_name="gpt-4o", log_enabled=True, metrics=Metrics(model_name="gpt-4o")
        )
    assert telemetry.completion_logging_enabled is True
    assert telemetry.log_enabled is True


def test_telemetry_conflicting_names_raise():
    with pytest.raises(ValueError, match="Conflicting values"):
        Telemetry.model_validate(
            {
                "model_name": "gpt-4o",
                "log_enabled": True,
                "completion_logging_enabled": False,
                "metrics": Metrics(model_name="gpt-4o"),
            }
        )


def test_telemetry_callback_path_independent_of_tracing():
    """Completion logging via callback fires regardless of OTel tracing config."""
    calls: list[tuple[str, str]] = []
    telemetry = Telemetry(
        model_name="gpt-4o",
        completion_logging_enabled=True,
        metrics=Metrics(model_name="gpt-4o"),
    )
    telemetry.set_log_completions_callback(
        lambda filename, data: calls.append((filename, data))
    )
    telemetry.on_request(telemetry_ctx={})
    telemetry.on_response(create_mock_litellm_response(content="CALLBACK_OK"))
    assert len(calls) == 1
    assert "CALLBACK_OK" in calls[0][1]


@pytest.mark.parametrize("value", ["false", "0", 0, False])
def test_old_false_values_keep_logging_disabled(value):
    with pytest.warns(DeprecatedWarning):
        llm = _llm(log_completions=value)
    assert llm.log_completions_to_file is False
    with pytest.warns(DeprecatedWarning):
        telemetry = Telemetry.model_validate(
            {"log_enabled": value, "metrics": Metrics(model_name="test")}
        )
    assert telemetry.completion_logging_enabled is False


def test_old_attributes_remain_assignable():
    llm = _llm()
    with pytest.warns(DeprecatedWarning):
        llm.log_completions = True
    assert llm.log_completions_to_file is True
    telemetry = Telemetry(metrics=Metrics(model_name="test"))
    with pytest.warns(DeprecatedWarning):
        telemetry.log_enabled = True
    assert telemetry.completion_logging_enabled is True


def test_persisted_configuration_retains_legacy_enabled_key():
    llm = _llm(log_completions_to_file=True)
    saved = json.loads(json.dumps(llm.to_persisted()))
    assert saved["log_completions"] is True
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecatedWarning)
        restored = LLM.from_persisted(saved)
    assert restored.log_completions_to_file is True


def test_new_public_option_writes_completion_file(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "openhands.sdk.llm.llm.litellm_completion",
        lambda **kwargs: create_mock_litellm_response(content="RENAMED_FILE_OK"),
    )
    llm = _llm(log_completions_to_file=True, log_completions_folder=str(tmp_path))
    llm.completion(
        [
            Message(role="system", content=[TextContent(text="SYSTEM")]),
            Message(role="user", content=[TextContent(text="REQUEST")]),
        ]
    )
    files = list(tmp_path.iterdir())
    assert len(files) == 1
    assert "RENAMED_FILE_OK" in files[0].read_text()


def test_telemetry_model_copy_accepts_old_update_name():
    telemetry = Telemetry(metrics=Metrics(model_name="test"))
    with pytest.warns(DeprecatedWarning):
        copied = telemetry.model_copy(update={"log_enabled": True})
    assert copied.completion_logging_enabled is True
    assert telemetry.completion_logging_enabled is False


def test_copied_telemetry_preserves_original_active_span(monkeypatch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer(__name__)

    @contextlib.contextmanager
    def call_span(name):
        with tracer.start_as_current_span(name) as span:
            yield span

    monkeypatch.setattr("openhands.sdk.observability.laminar.llm_call_span", call_span)
    original = Telemetry(metrics=Metrics(model_name="test"))
    try:
        original.on_request({})
        copied = original.model_copy(update={"completion_logging_enabled": False})
        copied.on_request({})
        copied.on_error(RuntimeError("synthetic copied request failure"))
        assert len(exporter.get_finished_spans()) == 1
        original.on_error(RuntimeError("synthetic original request failure"))
        assert len(exporter.get_finished_spans()) == 2
    finally:
        provider.shutdown()


@pytest.mark.parametrize("mode", ["validation", "serialization"])
@pytest.mark.parametrize(
    "model,canonical,legacy",
    [
        (LLM, "log_completions_to_file", "log_completions"),
        (Telemetry, "completion_logging_enabled", "log_enabled"),
    ],
)
def test_schemas_keep_deprecated_aliases(model, canonical, legacy, mode):
    properties = model.model_json_schema(mode=mode)["properties"]
    assert properties[canonical]["type"] == "boolean"
    assert properties[legacy]["type"] == "boolean"
    assert properties[legacy]["deprecated"] is True


def test_omitted_and_default_aliases_stay_excluded_when_requested():
    llm = _llm()
    for data in (
        llm.model_dump(exclude_unset=True),
        llm.model_dump(exclude_defaults=True),
    ):
        assert "log_completions" not in data
        assert "log_completions_to_file" not in data


def test_canonical_assignment_keeps_old_wire_value_enabled():
    llm = _llm()
    llm.log_completions_to_file = True
    assert llm.model_dump()["log_completions"] is True


@pytest.mark.parametrize("enabled", [False, True])
def test_canonical_configuration_round_trips_without_alias_warning(enabled):
    llm = _llm(log_completions_to_file=enabled)
    telemetry = Telemetry(
        metrics=Metrics(model_name="test"), completion_logging_enabled=enabled
    )
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecatedWarning)
        restored_llm = LLM.model_validate(llm.model_dump())
        copied_llm = llm.model_copy(
            update={"log_completions_to_file": enabled, "log_completions": enabled}
        )
        restored_telemetry = Telemetry.model_validate(telemetry.model_dump())
        copied_telemetry = telemetry.model_copy(
            update={"completion_logging_enabled": enabled, "log_enabled": enabled}
        )
    assert restored_llm.log_completions_to_file is enabled
    assert copied_llm.log_completions_to_file is enabled
    assert restored_telemetry.completion_logging_enabled is enabled
    assert copied_telemetry.completion_logging_enabled is enabled
