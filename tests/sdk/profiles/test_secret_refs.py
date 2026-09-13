"""``secret_refs`` — the profile's allow-list over a conversation's secrets."""

import pytest

from openhands.sdk.profiles import filter_profile_secrets, validate_agent_profile


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


class TestStrictForBothKinds:
    """The stored list is the whole allow-list — nothing is added back.

    Worth pinning for both variants. Every credential an OpenHands agent needs
    rides a channel ``secret_refs`` never sees (the LLM key and ``oracle`` from
    the LLM profile store, the critic key from ``verification``/the LLM, MCP env
    and headers from ``mcp_config``, a pod's ``GITHUB_TOKEN`` from the init
    request's ``env``), so it has nothing to add back. An ACP profile's provider
    credential *does* ride this channel, and is deliberately still not re-added:
    the picker offers it like any other saved secret, so omitting it is a
    choice, and the resulting auth failure is loud and recoverable.
    """

    @pytest.mark.parametrize(
        "profile", [_openhands(secret_refs=[]), _acp(secret_refs=[])]
    )
    def test_an_empty_list_yields_nothing(self, profile):
        assert filter_profile_secrets(profile, SECRETS) == {}

    def test_an_acp_provider_credential_is_not_re_added(self):
        profile = _acp(secret_refs=["GITHUB_TOKEN"])
        assert filter_profile_secrets(profile, SECRETS) == {"GITHUB_TOKEN": 1}

    def test_an_acp_profile_that_lists_its_credential_receives_it(self):
        profile = _acp(secret_refs=["ANTHROPIC_API_KEY"])
        assert filter_profile_secrets(profile, SECRETS) == {"ANTHROPIC_API_KEY": 3}

    def test_a_conventional_name_is_not_special_cased(self):
        profile = _openhands(secret_refs=["DATADOG_API_KEY"])
        assert filter_profile_secrets(profile, {"GITHUB_TOKEN": 1}) == {}


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
