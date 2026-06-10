"""Tests for Databricks FMAPI model discovery.

Covers: filter logic (endpoint_type, task, state.ready), User-Agent header (PWAF),
TTL cache (hit/miss/expiry), error handling (returns [] silently), and
list_models_from_env env-var handling.
"""

from __future__ import annotations

import time
from unittest.mock import patch

import httpx
import pytest

import openhands.sdk.llm.providers.databricks.discovery as discovery_module
from openhands.sdk.llm.providers.databricks.auth import DatabricksCredentials
from openhands.sdk.llm.providers.databricks.discovery import (
    CURATED_DATABRICKS_MODELS,
    DiscoveredEndpoint,
    ModelPickerEntry,
    get_picker_entries,
    list_chat_endpoints,
    list_foundation_models,
    list_models_from_env,
)
from openhands.sdk.llm.providers.databricks.models import ProviderFamily
from openhands.sdk.llm.providers.databricks.utils import USER_AGENT


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HOST = "https://adb-123.azuredatabricks.net"
_DISCOVERY_URL = f"{_HOST}/api/2.0/serving-endpoints"


def _discovery_response(status: int, body: dict) -> httpx.Response:
    """Build a Response httpx can run raise_for_status() on (needs bound request)."""
    req = httpx.Request("GET", _DISCOVERY_URL)
    return httpx.Response(status, json=body, request=req)


def _make_credentials(token: str = "test-token") -> DatabricksCredentials:
    return DatabricksCredentials(
        host=_HOST,
        get_token=lambda: token,
        auth_method="pat",
    )


def _make_endpoints_payload(endpoints: list[dict]) -> dict:
    return {"endpoints": endpoints}


def _fmapi_ep(name: str, ready: bool = True) -> dict:
    return {
        "name": name,
        "endpoint_type": "FOUNDATION_MODEL_API",
        "task": "llm/v1/chat",
        "state": {"ready": "READY" if ready else "NOT_READY"},
    }


def _non_fmapi_ep(name: str) -> dict:
    return {
        "name": name,
        "endpoint_type": "CUSTOM_MODEL",
        "task": "llm/v1/chat",
        "state": {"ready": "READY"},
    }


def _embedding_ep(name: str) -> dict:
    return {
        "name": name,
        "endpoint_type": "FOUNDATION_MODEL_API",
        "task": "llm/v1/embeddings",  # not chat
        "state": {"ready": "READY"},
    }


@pytest.fixture(autouse=True)
def reset_discovery_cache() -> None:
    """Reset module-level cache between tests to prevent interference."""
    discovery_module._CACHED_MODELS = []
    discovery_module._CACHE_EXPIRES_AT = 0.0
    yield
    discovery_module._CACHED_MODELS = []
    discovery_module._CACHE_EXPIRES_AT = 0.0


# ---------------------------------------------------------------------------
# list_foundation_models — filter logic
# ---------------------------------------------------------------------------

def test_list_foundation_models_returns_fmapi_chat_endpoints() -> None:
    """Only FOUNDATION_MODEL_API + llm/v1/chat + READY endpoints are returned."""
    payload = _make_endpoints_payload([
        _fmapi_ep("databricks-meta-llama-3-3-70b-instruct"),
        _fmapi_ep("databricks-dbrx-instruct"),
    ])
    with patch("httpx.get", return_value=_discovery_response(200, payload)):
        models = list_foundation_models(_make_credentials())

    assert "databricks/databricks-meta-llama-3-3-70b-instruct" in models
    assert "databricks/databricks-dbrx-instruct" in models
    assert len(models) == 2


def test_list_foundation_models_excludes_non_fmapi_endpoints() -> None:
    """CUSTOM_MODEL endpoints must not be returned."""
    payload = _make_endpoints_payload([
        _fmapi_ep("llama-model"),
        _non_fmapi_ep("custom-agent-endpoint"),
    ])
    with patch("httpx.get", return_value=_discovery_response(200, payload)):
        models = list_foundation_models(_make_credentials())

    assert "databricks/llama-model" in models
    assert "databricks/custom-agent-endpoint" not in models
    assert len(models) == 1


def test_list_foundation_models_excludes_embedding_endpoints() -> None:
    """llm/v1/embeddings task must not be included (only llm/v1/chat)."""
    payload = _make_endpoints_payload([
        _fmapi_ep("chat-model"),
        _embedding_ep("embed-model"),
    ])
    with patch("httpx.get", return_value=_discovery_response(200, payload)):
        models = list_foundation_models(_make_credentials())

    assert "databricks/chat-model" in models
    assert "databricks/embed-model" not in models


def test_list_foundation_models_excludes_not_ready_endpoints() -> None:
    """Endpoints with state.ready != 'READY' must be excluded."""
    payload = _make_endpoints_payload([
        _fmapi_ep("ready-model", ready=True),
        _fmapi_ep("loading-model", ready=False),
    ])
    with patch("httpx.get", return_value=_discovery_response(200, payload)):
        models = list_foundation_models(_make_credentials())

    assert "databricks/ready-model" in models
    assert "databricks/loading-model" not in models


def test_list_foundation_models_empty_workspace() -> None:
    """Empty endpoints list returns empty list."""
    with patch("httpx.get", return_value=_discovery_response(200, {"endpoints": []})):
        models = list_foundation_models(_make_credentials())
    assert models == []


# ---------------------------------------------------------------------------
# PWAF: User-Agent header on discovery calls
# ---------------------------------------------------------------------------

def test_list_foundation_models_sends_user_agent() -> None:
    """PWAF: User-Agent header must be present on the discovery GET request."""
    captured_headers: dict = {}

    def mock_get(url, headers=None, timeout=None):
        captured_headers.update(headers or {})
        return _discovery_response(200, {"endpoints": []})

    with patch("httpx.get", side_effect=mock_get):
        list_foundation_models(_make_credentials())

    assert captured_headers.get("User-Agent") == USER_AGENT


def test_list_foundation_models_sends_authorization() -> None:
    """Authorization header must be present with Bearer scheme."""
    captured_headers: dict = {}

    def mock_get(url, headers=None, timeout=None):
        captured_headers.update(headers or {})
        return _discovery_response(200, {"endpoints": []})

    with patch("httpx.get", side_effect=mock_get):
        list_foundation_models(_make_credentials(token="test-tok"))

    assert captured_headers.get("Authorization") == "Bearer test-tok"


# ---------------------------------------------------------------------------
# list_models_from_env — TTL cache
# ---------------------------------------------------------------------------

def test_list_models_from_env_returns_empty_without_env_vars(monkeypatch) -> None:
    """Returns [] when DATABRICKS_HOST or DATABRICKS_TOKEN are not set."""
    monkeypatch.delenv("DATABRICKS_HOST", raising=False)
    monkeypatch.delenv("DATABRICKS_TOKEN", raising=False)
    monkeypatch.delenv("DATABRICKS_ACCESS_TOKEN", raising=False)

    models = list_models_from_env()
    assert models == []


def test_list_models_from_env_uses_cache_on_second_call(monkeypatch) -> None:
    """Second call within TTL must use cache, not make a new HTTP request."""
    monkeypatch.setenv("DATABRICKS_HOST", _HOST)
    monkeypatch.setenv("DATABRICKS_TOKEN", "test-tok")

    with patch(
        "httpx.get",
        return_value=_discovery_response(200, {"endpoints": [_fmapi_ep("m1")]}),
    ) as mock_get:
        first = list_models_from_env()
        second = list_models_from_env()

    # Should only call httpx.get once; second call uses cache
    assert mock_get.call_count == 1
    assert first == second
    assert "databricks/m1" in first


def test_list_models_from_env_refreshes_after_ttl_expiry(monkeypatch) -> None:
    """After TTL expires, a new HTTP call should be made."""
    monkeypatch.setenv("DATABRICKS_HOST", _HOST)
    monkeypatch.setenv("DATABRICKS_TOKEN", "tok")

    # Pre-populate cache with an expired timestamp
    discovery_module._CACHED_MODELS = ["databricks/old-model"]
    discovery_module._CACHE_EXPIRES_AT = time.time() - 1  # already expired

    with patch(
        "httpx.get",
        return_value=_discovery_response(200, {"endpoints": [_fmapi_ep("new-model")]}),
    ):
        models = list_models_from_env()

    assert "databricks/new-model" in models
    assert "databricks/old-model" not in models


def test_list_models_from_env_returns_empty_on_http_error(monkeypatch) -> None:
    """HTTP errors must be swallowed and return [] (never raise)."""
    monkeypatch.setenv("DATABRICKS_HOST", _HOST)
    monkeypatch.setenv("DATABRICKS_TOKEN", "tok")

    with patch("httpx.get", side_effect=httpx.ConnectError("connection refused")):
        models = list_models_from_env()

    assert models == []


def test_list_models_from_env_model_names_prefixed(monkeypatch) -> None:
    """All returned model names must be prefixed with 'databricks/'."""
    monkeypatch.setenv("DATABRICKS_HOST", _HOST)
    monkeypatch.setenv("DATABRICKS_TOKEN", "tok")

    endpoints = [
        _fmapi_ep("databricks-meta-llama-3-3-70b-instruct"),
        _fmapi_ep("databricks-dbrx-instruct"),
    ]
    with patch(
        "httpx.get",
        return_value=_discovery_response(200, {"endpoints": endpoints}),
    ):
        models = list_models_from_env()

    for m in models:
        assert m.startswith("databricks/"), f"Model {m!r} missing 'databricks/' prefix"


# ---------------------------------------------------------------------------
# External-model inclusion (AI Gateway parity with FM endpoints)
# ---------------------------------------------------------------------------

def _external_ep(name: str, ready: bool = True) -> dict:
    """EXTERNAL_MODEL chat endpoint — e.g. customer-configured gpt-5 / gemini proxy."""
    return {
        "name": name,
        "endpoint_type": "EXTERNAL_MODEL",
        "task": "llm/v1/chat",
        "state": {"ready": "READY" if ready else "NOT_READY"},
    }


def test_list_foundation_models_includes_external_model_endpoints() -> None:
    """EXTERNAL_MODEL chat endpoints must be returned alongside FOUNDATION_MODEL_API.

    External-model endpoints (e.g. customer-configured gpt-5 / gemini proxies)
    are AI-Gateway-shaped and routed through the same native-API paths.
    """
    payload = _make_endpoints_payload([
        _fmapi_ep("databricks-llama-3-3-70b"),
        _external_ep("my-gpt5-proxy"),
    ])
    with patch("httpx.get", return_value=_discovery_response(200, payload)):
        models = list_foundation_models(_make_credentials())

    assert "databricks/databricks-llama-3-3-70b" in models
    assert "databricks/my-gpt5-proxy" in models, (
        "EXTERNAL_MODEL endpoints must be discoverable via list_foundation_models"
    )
    assert len(models) == 2


def test_list_foundation_models_excludes_custom_model_endpoints() -> None:
    """CUSTOM_MODEL endpoints are still excluded — payload shape not guaranteed."""
    payload = _make_endpoints_payload([
        _fmapi_ep("fm-endpoint"),
        _external_ep("external-endpoint"),
        _non_fmapi_ep("custom-agent"),  # endpoint_type=CUSTOM_MODEL
    ])
    with patch("httpx.get", return_value=_discovery_response(200, payload)):
        models = list_foundation_models(_make_credentials())

    assert "databricks/fm-endpoint" in models
    assert "databricks/external-endpoint" in models
    assert "databricks/custom-agent" not in models


# ---------------------------------------------------------------------------
# list_chat_endpoints — structured output
# ---------------------------------------------------------------------------

def test_list_chat_endpoints_returns_dataclass_records() -> None:
    """Structured API returns DiscoveredEndpoint records with metadata intact."""
    payload = _make_endpoints_payload([
        {
            "name": "fm-llama",
            "endpoint_type": "FOUNDATION_MODEL_API",
            "task": "llm/v1/chat",
            "state": {"ready": "READY"},
            "creator": "alice@example.com",
        },
        {
            "name": "ext-gpt5",
            "endpoint_type": "EXTERNAL_MODEL",
            "task": "llm/v1/chat",
            "state": {"ready": "READY"},
            "creator": "bob@example.com",
        },
    ])
    with patch("httpx.get", return_value=_discovery_response(200, payload)):
        eps = list_chat_endpoints(_make_credentials())

    assert len(eps) == 2
    assert all(isinstance(e, DiscoveredEndpoint) for e in eps)

    by_name = {e.name: e for e in eps}
    assert by_name["fm-llama"].endpoint_type == "FOUNDATION_MODEL_API"
    assert by_name["fm-llama"].qualified_name == "databricks/fm-llama"
    assert by_name["fm-llama"].ready is True
    assert by_name["fm-llama"].creator == "alice@example.com"

    assert by_name["ext-gpt5"].endpoint_type == "EXTERNAL_MODEL"
    assert by_name["ext-gpt5"].qualified_name == "databricks/ext-gpt5"


def test_list_chat_endpoints_include_not_ready_opt_in() -> None:
    """Not-ready endpoints are excluded by default, included on opt-in."""
    payload = _make_endpoints_payload([
        _fmapi_ep("ready-one", ready=True),
        _fmapi_ep("loading-one", ready=False),
    ])

    with patch("httpx.get", return_value=_discovery_response(200, payload)):
        default = list_chat_endpoints(_make_credentials())
    assert [e.name for e in default] == ["ready-one"]

    with patch("httpx.get", return_value=_discovery_response(200, payload)):
        with_loading = list_chat_endpoints(_make_credentials(), include_not_ready=True)
    names = sorted(e.name for e in with_loading)
    assert names == ["loading-one", "ready-one"]
    loading = next(e for e in with_loading if e.name == "loading-one")
    assert loading.ready is False


def test_list_chat_endpoints_skips_unnamed_rows() -> None:
    """Endpoints missing a ``name`` field must be silently skipped."""
    payload = _make_endpoints_payload([
        {
            "endpoint_type": "FOUNDATION_MODEL_API",
            "task": "llm/v1/chat",
            "state": {"ready": "READY"},
        },
        _fmapi_ep("good-one"),
    ])
    with patch("httpx.get", return_value=_discovery_response(200, payload)):
        eps = list_chat_endpoints(_make_credentials())
    assert [e.name for e in eps] == ["good-one"]


# ---------------------------------------------------------------------------
# Two-tier picker: CURATED_DATABRICKS_MODELS + get_picker_entries
# ---------------------------------------------------------------------------


def test_curated_list_is_claude_gpt_gemini_only() -> None:
    """Curated tier-1 set covers only the three native-API families we target.

    Llama / DBRX / legacy endpoints must *not* appear in the curated list —
    they surface automatically via discovery only if the workspace has them.
    """
    families = {e.family for e in CURATED_DATABRICKS_MODELS}
    assert families == {
        ProviderFamily.ANTHROPIC,
        ProviderFamily.OPENAI,
        ProviderFamily.OPENAI_RESPONSES,
        ProviderFamily.GEMINI,
    }

    names = [e.name for e in CURATED_DATABRICKS_MODELS]
    assert all(n.startswith("databricks-") for n in names)
    forbidden = ("llama", "dbrx", "mixtral", "qwen", "deepseek")
    for n in names:
        for token in forbidden:
            assert token not in n.lower(), (
                f"{n!r} leaked a non-curated family — curated tier is Claude/GPT/Gemini only"
            )


def test_curated_list_has_one_recommended_per_family() -> None:
    """Exactly one ``recommended`` entry per family — the fast-and-good default."""
    recs_by_family: dict[ProviderFamily, list[str]] = {}
    for e in CURATED_DATABRICKS_MODELS:
        if e.recommended:
            recs_by_family.setdefault(e.family, []).append(e.name)

    # GPT-5 (Responses) gets the recommended OpenAI slot because it's the
    # gold-path OpenAI native API on Databricks. Plain OPENAI (gpt-oss) is
    # listed but not recommended.
    assert set(recs_by_family) == {
        ProviderFamily.ANTHROPIC,
        ProviderFamily.OPENAI_RESPONSES,
        ProviderFamily.GEMINI,
    }
    for family, picks in recs_by_family.items():
        assert len(picks) == 1, f"{family} has >1 recommended pick: {picks}"


def test_curated_entries_have_qualified_name_prefix() -> None:
    """Every curated entry must use the ``databricks/`` prefix — that's what
    ``create_llm`` sees and routes on."""
    for e in CURATED_DATABRICKS_MODELS:
        assert e.qualified_name == f"databricks/{e.name}"
        assert e.source == "curated"
        assert e.ready is True
        assert e.endpoint_type == "FOUNDATION_MODEL_API"


def test_get_picker_entries_returns_curated_without_credentials() -> None:
    """No creds → pure curated tier, no HTTP call."""
    with patch("httpx.get") as mock_get:
        entries = get_picker_entries(credentials=None)
    mock_get.assert_not_called()

    assert len(entries) == len(CURATED_DATABRICKS_MODELS)
    assert all(isinstance(e, ModelPickerEntry) for e in entries)
    assert all(e.source == "curated" for e in entries)


def test_get_picker_entries_sort_order_recommended_first() -> None:
    """Recommended picks come first; then family (alpha), then name."""
    entries = get_picker_entries(credentials=None)

    # 1. All recommended come before any non-recommended.
    first_non_rec = next(
        (i for i, e in enumerate(entries) if not e.recommended), len(entries)
    )
    rec_section = entries[:first_non_rec]
    rest_section = entries[first_non_rec:]
    assert all(e.recommended for e in rec_section)
    assert all(not e.recommended for e in rest_section)

    # 2. Within each section, family alpha-sorted.
    def _family_order(section: list[ModelPickerEntry]) -> list[str]:
        return [e.family.value for e in section]

    for section in (rec_section, rest_section):
        fams = _family_order(section)
        assert fams == sorted(fams), f"Family ordering broken in section: {fams}"


def test_get_picker_entries_merges_discovered_on_top_of_curated() -> None:
    """Discovery adds non-curated endpoints; curated entries get live signals."""
    # One endpoint overlaps curated (claude-sonnet-4-6), two are new.
    payload = _make_endpoints_payload([
        _fmapi_ep("databricks-claude-sonnet-4-6"),   # overlaps curated
        _fmapi_ep("databricks-meta-llama-4-maverick"),   # discovered only
        _external_ep("customer-private-gpt"),   # external-model discovered only
    ])

    with patch("httpx.get", return_value=_discovery_response(200, payload)):
        entries = get_picker_entries(credentials=_make_credentials())

    by_qn = {e.qualified_name: e for e in entries}

    # Overlap: curated entry kept recommended + its opinionated family, but
    # gained "curated+discovered" source and the live endpoint_type/ready.
    overlap = by_qn["databricks/databricks-claude-sonnet-4-6"]
    assert overlap.source == "curated+discovered"
    assert overlap.family is ProviderFamily.ANTHROPIC
    assert overlap.recommended is True
    assert overlap.endpoint_type == "FOUNDATION_MODEL_API"
    assert overlap.ready is True

    # Discovered-only FMAPI entry — family inferred via detect_family.
    llama = by_qn["databricks/databricks-meta-llama-4-maverick"]
    assert llama.source == "discovered"
    assert llama.family is ProviderFamily.OPENAI  # llama → OpenAI Chat default
    assert llama.recommended is False
    assert llama.endpoint_type == "FOUNDATION_MODEL_API"

    # External-model endpoint shows up as "discovered" with its live type.
    ext = by_qn["databricks/customer-private-gpt"]
    assert ext.source == "discovered"
    assert ext.endpoint_type == "EXTERNAL_MODEL"

    # Count: curated ∪ discovered, deduped by qualified_name.
    expected = {e.qualified_name for e in CURATED_DATABRICKS_MODELS} | {
        "databricks/databricks-meta-llama-4-maverick",
        "databricks/customer-private-gpt",
    }
    assert {e.qualified_name for e in entries} == expected


def test_get_picker_entries_swallows_discovery_errors() -> None:
    """If discovery blows up, curated tier is still returned intact."""
    with patch("httpx.get", side_effect=RuntimeError("workspace down")):
        entries = get_picker_entries(credentials=_make_credentials())

    # Curated list is returned as-is (no "curated+discovered" upgrades).
    assert {e.qualified_name for e in entries} == {
        c.qualified_name for c in CURATED_DATABRICKS_MODELS
    }
    assert all(e.source == "curated" for e in entries)


def test_get_picker_entries_include_curated_false_returns_only_discovered() -> None:
    """Opt out of curated to get a pure live list — useful for admin UIs."""
    payload = _make_endpoints_payload([_fmapi_ep("live-only-endpoint")])
    with patch("httpx.get", return_value=_discovery_response(200, payload)):
        entries = get_picker_entries(
            credentials=_make_credentials(),
            include_curated=False,
        )
    assert [e.qualified_name for e in entries] == ["databricks/live-only-endpoint"]
    assert entries[0].source == "discovered"


def test_get_picker_entries_include_discovered_false_skips_http() -> None:
    """Opt out of discovery even with creds — no HTTP fired."""
    with patch("httpx.get") as mock_get:
        entries = get_picker_entries(
            credentials=_make_credentials(),
            include_discovered=False,
        )
    mock_get.assert_not_called()
    assert len(entries) == len(CURATED_DATABRICKS_MODELS)


def test_get_picker_entries_user_agent_propagates_through_discovery() -> None:
    """PWAF: every Databricks HTTP call (including discovery triggered by the
    picker) must carry the ``OpenHandsOSS/<ver>`` User-Agent."""
    payload = _make_endpoints_payload([_fmapi_ep("anything")])
    with patch(
        "httpx.get", return_value=_discovery_response(200, payload)
    ) as mock_get:
        get_picker_entries(credentials=_make_credentials())

    assert mock_get.called
    _, kwargs = mock_get.call_args
    assert kwargs["headers"]["User-Agent"] == USER_AGENT
