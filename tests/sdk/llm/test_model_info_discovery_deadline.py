"""A stalled optional model-info probe must not freeze profile requests."""

import time
from threading import Event

import pytest

from openhands.sdk.llm.utils import model_info


def test_model_info_discovery_returns_without_waiting_for_stalled_probe(monkeypatch):
    release = Event()
    finished = Event()

    def stalled_probe(*_args):
        try:
            release.wait(timeout=5)
            return {"never": "used"}
        finally:
            finished.set()

    monkeypatch.setattr(model_info, "_get_litellm_model_info_sync", stalled_probe)
    monkeypatch.setattr(model_info, "MODEL_INFO_DISCOVERY_TIMEOUT_SECONDS", 0.05)

    try:
        started = time.monotonic()
        assert (
            model_info.get_litellm_model_info(None, None, "openhands/gpt-6-luna")
            is None
        )
        assert time.monotonic() - started < 1
    finally:
        release.set()
        assert finished.wait(timeout=1)


def test_model_info_discovery_keeps_fast_result(monkeypatch):
    expected = {"max_input_tokens": 100000}
    monkeypatch.setattr(model_info, "_get_litellm_model_info_sync", lambda *_: expected)

    assert (
        model_info.get_litellm_model_info(None, None, "openhands/gpt-6-luna")
        == expected
    )


def test_model_info_discovery_preserves_exception(monkeypatch):
    expected = RuntimeError("discovery failed")

    def fail(*_args):
        raise expected

    monkeypatch.setattr(model_info, "_get_litellm_model_info_sync", fail)

    with pytest.raises(RuntimeError) as exc_info:
        model_info.get_litellm_model_info(None, None, "openhands/gpt-6-luna")
    assert exc_info.value is expected


def test_proxy_failure_does_not_start_unbounded_provider_fallback(monkeypatch):
    monkeypatch.setattr(
        model_info, "_get_model_info_from_litellm_proxy", lambda **_: None
    )
    monkeypatch.setattr(model_info, "model_cost", {})

    def unexpected_fallback(*_args):
        raise AssertionError("proxy route must not call provider discovery")

    monkeypatch.setattr(model_info, "get_model_info", unexpected_fallback)
    assert (
        model_info.get_litellm_model_info(
            None,
            "http://litellm-codex.local-llm.svc.cluster.local:4000",
            "openhands/gpt-6-luna",
        )
        is None
    )


def test_proxy_failure_keeps_known_local_model_metadata(monkeypatch):
    monkeypatch.setattr(
        model_info, "_get_model_info_from_litellm_proxy", lambda **_: None
    )
    monkeypatch.setattr(
        model_info,
        "model_cost",
        {"gpt-4o": {"max_input_tokens": 128000, "mode": "chat"}},
    )

    assert model_info.get_litellm_model_info(
        None, "http://litellm.invalid", "litellm_proxy/gpt-4o"
    ) == {"max_input_tokens": 128000, "mode": "chat"}


def test_saturated_probes_recover_capacity(monkeypatch):
    release = Event()
    finished = [Event() for _ in range(4)]

    def probe(_key, _base, model):
        if model == "healthy":
            return {"max_input_tokens": 100000}
        try:
            release.wait(timeout=5)
            return None
        finally:
            finished[int(model.removeprefix("stalled-"))].set()

    monkeypatch.setattr(model_info, "_get_litellm_model_info_sync", probe)
    monkeypatch.setattr(model_info, "MODEL_INFO_DISCOVERY_TIMEOUT_SECONDS", 0.05)
    try:
        for index in range(4):
            assert (
                model_info.get_litellm_model_info(None, None, f"stalled-{index}")
                is None
            )
        monkeypatch.setattr(model_info, "MODEL_INFO_DISCOVERY_TIMEOUT_SECONDS", 0.5)
        started = time.monotonic()
        assert model_info.get_litellm_model_info(None, None, "healthy") is None
        assert time.monotonic() - started < 0.2
    finally:
        release.set()
        assert all(done.wait(timeout=1) for done in finished)

    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if model_info.get_litellm_model_info(None, None, "healthy") == {
            "max_input_tokens": 100000
        }:
            break
        time.sleep(0.01)
    else:
        raise AssertionError("model-info capacity did not recover")
