"""Model-info discovery must never block the caller indefinitely.

Some providers (self-hosted / OpenAI-compatible such as ``lemonade``,
``ollama`` and ``vllm``) resolve model info via a live HTTP request to the
model server. litellm makes that request synchronously with no timeout, so an
unreachable endpoint -- or one that loops back to the caller through a reverse
proxy -- hangs forever. Because ``LLM`` construction runs this probe in
``_post_init``, that previously froze any caller, including an async server's
event loop.

These tests pin the bounded behavior: discovery returns ``None`` within the
deadline instead of hanging.
"""

import time
from types import SimpleNamespace

from openhands.sdk.llm.utils import model_info
from openhands.sdk.llm.utils.model_info import (
    _run_with_deadline,
    get_litellm_model_info,
)


def test_run_with_deadline_returns_none_on_timeout():
    def _hang(*_a, **_kw):
        time.sleep(30)
        return "never"

    started = time.time()
    result = _run_with_deadline(_hang, timeout=0.2)
    elapsed = time.time() - started

    assert result is None
    # Returned promptly rather than waiting for the (abandoned) call.
    assert elapsed < 5


def test_run_with_deadline_returns_value_when_fast():
    assert _run_with_deadline(lambda: 42, timeout=5) == 42


def test_get_litellm_model_info_does_not_hang_on_slow_probe(monkeypatch):
    """A self-hosted model whose model-info probe hangs must not block.

    Regression test for the deadlock where ``litellm.get_model_info`` for an
    unreachable self-hosted endpoint blocked ``LLM`` construction (and any
    async caller) forever.
    """

    def _hang(*_a, **_kw):
        time.sleep(30)
        raise AssertionError("should have been abandoned by the deadline")

    monkeypatch.setattr(model_info, "get_model_info", _hang)
    monkeypatch.setattr(model_info, "MODEL_INFO_DISCOVERY_TIMEOUT", 0.2)

    started = time.time()
    info = get_litellm_model_info(
        secret_api_key=None,
        base_url="http://localhost:9",
        model="lemonade/some-self-hosted-model",
    )
    elapsed = time.time() - started

    assert info is None
    # Two bounded fallback probes at ~0.2s each must still return promptly.
    assert elapsed < 5


def test_litellm_proxy_underlying_probe_does_not_hang(monkeypatch):
    """The proxy alias-pricing probe must also be bounded.

    ``_get_model_info_from_litellm_proxy`` resolves a proxy alias, then probes
    the *underlying* model via ``get_model_info`` to make the alias priceable.
    That nested probe is a synchronous, network-bound litellm call with no
    timeout and runs inside ``LLM._post_init``; an unreachable underlying
    endpoint there would reintroduce the deadlock this PR targets.
    """

    def _fake_proxy_response(*_a, **_kw):
        # Advertise an alias whose underlying model differs from the request,
        # so the underlying-model probe branch runs.
        return SimpleNamespace(
            json=lambda: {
                "data": [
                    {
                        "model_name": "my-alias",
                        "litellm_params": {"model": "some/underlying-model"},
                        "model_info": {"max_input_tokens": 100},
                    }
                ]
            }
        )

    def _hang(*_a, **_kw):
        time.sleep(30)
        raise AssertionError("underlying probe should have been abandoned")

    monkeypatch.setattr(model_info.httpx, "get", _fake_proxy_response)
    monkeypatch.setattr(model_info, "get_model_info", _hang)
    monkeypatch.setattr(model_info, "MODEL_INFO_DISCOVERY_TIMEOUT", 0.2)
    # Bypass the hourly lru_cache so this probe actually runs.
    model_info._get_model_info_from_litellm_proxy.cache_clear()

    started = time.time()
    info = get_litellm_model_info(
        secret_api_key=None,
        base_url="http://localhost:9",
        model="litellm_proxy/my-alias",
    )
    elapsed = time.time() - started

    # The alias's own model_info is still returned; only the underlying probe
    # was abandoned, and it did so without blocking construction.
    assert info == {"max_input_tokens": 100}
    assert elapsed < 5
