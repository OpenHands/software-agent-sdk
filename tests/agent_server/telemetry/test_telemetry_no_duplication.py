"""Guards against duplicated or drifting hardcoded data.

The telemetry package deliberately re-uses definitions that already exist
elsewhere rather than restating them. These tests fail if a copy reappears, or
if a hardcoded name silently stops matching the library it came from.
"""

import builtins

import pytest

from openhands.agent_server.config import TelemetryMode, TelemetrySpec
from openhands.agent_server.persistence.models import PersistedSettings
from openhands.agent_server.telemetry import models as m, policy
from openhands.agent_server.telemetry.factory import build_runtime_properties
from openhands.agent_server.telemetry.sanitizer import (
    _FIRST_PARTY_ROOT,
    _MODEL_FAMILY_HINTS,
    _known_providers,
)
from openhands.agent_server.telemetry.subscriber import (
    _FAILURE_STATUSES,
    _TERMINAL_STATUSES,
)
from openhands.sdk.conversation.state import ConversationExecutionStatus


# ── single definitions ────────────────────────────────────────────────────


def test_telemetry_mode_has_one_definition():
    """config.TelemetryMode is canonical; policy re-exports the same object."""
    assert policy.TelemetryMode is TelemetryMode
    # And the config field uses it rather than restating the literal.
    assert TelemetrySpec.model_fields["mode"].annotation is TelemetryMode


def test_telemetry_consent_has_one_definition():
    """persistence.models owns it (it is persisted); policy re-exports it."""
    from openhands.agent_server.persistence.models import TelemetryConsent

    assert policy.TelemetryConsent is TelemetryConsent
    assert PersistedSettings.model_fields["telemetry_consent"].annotation is (
        TelemetryConsent
    )


def test_runtime_deployment_mode_reuses_the_config_type():
    assert m.RuntimeProperties.model_fields["deployment_mode"].annotation is (
        TelemetryMode
    )


# ── derived, not hardcoded ────────────────────────────────────────────────


def test_terminal_statuses_come_from_the_sdk_enum():
    """A new terminal status must not silently stop being reported."""
    expected = {s.value for s in ConversationExecutionStatus if s.is_terminal()}
    assert _TERMINAL_STATUSES == expected
    assert _FAILURE_STATUSES == expected - {ConversationExecutionStatus.FINISHED.value}


def test_full_state_key_comes_from_the_sdk():
    import openhands.agent_server.telemetry.subscriber as sub
    from openhands.sdk.event.conversation_state import FULL_STATE_KEY

    assert "_FULL_STATE_KEY" not in vars(sub), (
        "the full_state key is defined in the SDK; do not restate it here"
    )
    assert FULL_STATE_KEY == "full_state"


def test_first_party_root_is_derived_not_hardcoded():
    """It must track the package it lives in, not a literal that can go stale."""
    import openhands.agent_server.telemetry.sanitizer as sanitizer

    assert _FIRST_PARTY_ROOT == sanitizer.__name__.split(".", 1)[0]
    assert _FIRST_PARTY_ROOT == "openhands"  # current value, for readability


def test_versions_match_server_info():
    """Telemetry and /server_info must never disagree about what is running."""
    from openhands.agent_server.server_details_router import ServerInfo

    info = ServerInfo(uptime=0.0, idle_time=0.0)
    runtime = build_runtime_properties(mode="cloud_locked", deferred_init=False)

    assert runtime.server_version == info.version
    assert runtime.sdk_version == info.sdk_version
    assert runtime.tools_version == info.tools_version
    assert runtime.build_git_sha == info.build_git_sha
    assert runtime.build_git_ref == info.build_git_ref


# ── hardcoded names still match their source ──────────────────────────────

_LITELLM_NAMES = {
    "APIConnectionError",
    "APIError",
    "AuthenticationError",
    "BadRequestError",
    "ContextWindowExceededError",
    "InternalServerError",
    "PermissionDeniedError",
    "RateLimitError",
    "ServiceUnavailableError",
    "Timeout",
    "UnprocessableEntityError",
}
_HTTPX_NAMES = {"ConnectError", "ReadTimeout", "HTTPStatusError"}


@pytest.mark.parametrize("name", sorted(_LITELLM_NAMES))
def test_litellm_error_names_still_exist(name: str):
    """A litellm rename would silently drop the error into 'unknown'."""
    import litellm.exceptions

    assert hasattr(litellm.exceptions, name)
    assert name in m.ERROR_CATEGORY_BY_CLASS_NAME


@pytest.mark.parametrize("name", sorted(_HTTPX_NAMES))
def test_httpx_error_names_still_exist(name: str):
    import httpx

    assert hasattr(httpx, name)
    assert name in m.ERROR_CATEGORY_BY_CLASS_NAME


def test_builtin_error_names_still_exist():
    named = set(m.ERROR_CATEGORY_BY_CLASS_NAME) & set(dir(builtins))
    assert named, "expected some builtin exceptions in the category table"
    for name in named:
        assert issubclass(getattr(builtins, name), BaseException)


def test_model_family_hints_target_real_providers():
    """Every hint must resolve to a provider litellm actually knows."""
    providers = _known_providers()
    unknown = sorted({family for _, family in _MODEL_FAMILY_HINTS} - providers)
    assert unknown == [], f"model family hints not in litellm.provider_list: {unknown}"


def test_provider_list_is_sourced_from_litellm():
    import litellm

    assert _known_providers() >= {"anthropic", "openai"}
    assert len(_known_providers()) == len(
        {str(getattr(p, "value", p)).lower() for p in litellm.provider_list}
    )
