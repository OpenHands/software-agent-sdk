"""``secret_refs`` — the profile's allow-list over a conversation's secrets."""

import pytest

from openhands.sdk.profiles import (
    allowed_secret_names,
    filter_profile_secrets,
    validate_agent_profile,
)


def _openhands(**overrides):
    return validate_agent_profile(
        {"name": "explorer", "llm_profile_ref": "default", **overrides}
    )


def _acp(**overrides):
    return validate_agent_profile(
        {
            "name": "claude",
            "agent_kind": "acp",
            "acp_server": "claude-code",
            **overrides,
        }
    )


SECRETS = {"GITHUB_TOKEN": 1, "DATADOG_API_KEY": 2, "ANTHROPIC_API_KEY": 3}


class TestDefault:
    def test_defaults_to_unrestricted(self):
        assert _openhands().secret_refs is None
        assert _acp().secret_refs is None

    @pytest.mark.parametrize("profile", [_openhands(), _acp()])
    def test_unrestricted_passes_everything_through(self, profile):
        assert allowed_secret_names(profile) is None
        assert filter_profile_secrets(profile, SECRETS) == SECRETS

    def test_filter_returns_a_copy(self):
        profile = _openhands()
        result = filter_profile_secrets(profile, SECRETS)
        result["EXTRA"] = 9
        assert "EXTRA" not in SECRETS


class TestOpenHandsScope:
    def test_a_list_keeps_only_the_named_secrets(self):
        profile = _openhands(secret_refs=["GITHUB_TOKEN"])
        assert filter_profile_secrets(profile, SECRETS) == {"GITHUB_TOKEN": 1}

    def test_empty_list_keeps_nothing(self):
        assert filter_profile_secrets(_openhands(secret_refs=[]), SECRETS) == {}

    def test_a_ref_matching_no_secret_is_a_no_op(self):
        # Unlike mcp_server_refs this can't dangle: it filters what a launch
        # supplies, so an unmatched name simply never matches.
        profile = _openhands(secret_refs=["GITHUB_TOKEN", "DELETED_SECRET"])
        assert filter_profile_secrets(profile, SECRETS) == {"GITHUB_TOKEN": 1}


class TestNoImplicitOpenHandsCarveOut:
    """An OpenHands profile's allow-list is exactly its ``secret_refs``.

    Deliberate, and worth pinning: every other credential an OpenHands agent
    needs rides a channel ``secret_refs`` never sees — the LLM key and the
    ``oracle`` model come from the LLM profile store, the critic key from
    ``verification``/the LLM, MCP env and headers from ``mcp_config`` (already
    scoped by ``mcp_server_refs``), and a pod's ``GITHUB_TOKEN`` from the init
    request's ``env``. If a credential ever starts riding ``request.secrets``
    instead, this test fails and the carve-out has to be argued for explicitly
    rather than appearing by accident.
    """

    def test_no_name_is_allowed_implicitly(self):
        assert allowed_secret_names(_openhands(secret_refs=[])) == set()

    def test_a_conventional_name_is_not_special_cased(self):
        profile = _openhands(secret_refs=["DATADOG_API_KEY"])
        assert filter_profile_secrets(profile, {"GITHUB_TOKEN": 1}) == {}


class TestAcpProviderCredentials:
    def test_provider_credentials_survive_an_empty_list(self):
        # Filtering these out would leave the subprocess unable to authenticate.
        allowed = allowed_secret_names(_acp(secret_refs=[]))
        assert allowed == {"ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL"}

    def test_provider_credentials_ride_alongside_the_named_secrets(self):
        profile = _acp(secret_refs=["GITHUB_TOKEN"])
        assert filter_profile_secrets(profile, SECRETS) == {
            "GITHUB_TOKEN": 1,
            "ANTHROPIC_API_KEY": 3,
        }

    def test_a_custom_server_contributes_no_provider_credentials(self):
        profile = _acp(
            acp_server="custom", acp_command="my-agent", secret_refs=["GITHUB_TOKEN"]
        )
        assert allowed_secret_names(profile) == {"GITHUB_TOKEN"}


class TestPersistence:
    def test_a_profile_without_the_key_loads_unrestricted(self):
        # Profiles written before the field existed carry no `secret_refs`.
        profile = validate_agent_profile(
            {"schema_version": 2, "name": "old", "llm_profile_ref": "default"}
        )
        assert profile.secret_refs is None

    def test_the_field_round_trips(self):
        profile = _openhands(secret_refs=["A", "B"])
        assert validate_agent_profile(profile.model_dump()).secret_refs == ["A", "B"]
