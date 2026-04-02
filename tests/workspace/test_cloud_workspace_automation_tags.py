"""Tests for OpenHandsCloudWorkspace automation tags functionality."""

import json
import os
from unittest.mock import patch

import pytest


class TestDefaultConversationTags:
    """Tests for the default_conversation_tags property."""

    @pytest.fixture
    def workspace(self):
        """Create a workspace instance with mocked sandbox creation."""
        from openhands.workspace import OpenHandsCloudWorkspace

        with patch.object(OpenHandsCloudWorkspace, "_start_sandbox"):
            workspace = OpenHandsCloudWorkspace(
                cloud_api_url="https://cloud.example.com",
                cloud_api_key="test-api-key",
            )
            # Set up minimal state
            workspace._sandbox_id = "sandbox-123"
            workspace._session_api_key = "session-key"
            workspace.host = "https://agent.example.com"
            yield workspace
            workspace._sandbox_id = None
            workspace.cleanup()

    def test_empty_tags_when_no_env_vars(self, workspace):
        """Should return empty dict when no automation env vars are set."""
        with patch.dict(os.environ, {}, clear=True):
            # Clear any existing env vars
            os.environ.pop("AUTOMATION_EVENT_PAYLOAD", None)
            os.environ.pop("AUTOMATION_RUN_ID", None)
            workspace._automation_run_id = None

            tags = workspace.default_conversation_tags
            assert tags == {}

    def test_parses_trigger_from_payload(self, workspace):
        """Should extract automationtrigger from AUTOMATION_EVENT_PAYLOAD."""
        payload = {"trigger": "cron"}
        with patch.dict(os.environ, {"AUTOMATION_EVENT_PAYLOAD": json.dumps(payload)}):
            tags = workspace.default_conversation_tags
            assert tags["automationtrigger"] == "cron"

    def test_parses_automation_id_from_payload(self, workspace):
        """Should extract automationid from AUTOMATION_EVENT_PAYLOAD."""
        payload = {"automation_id": "auto-123"}
        with patch.dict(os.environ, {"AUTOMATION_EVENT_PAYLOAD": json.dumps(payload)}):
            tags = workspace.default_conversation_tags
            assert tags["automationid"] == "auto-123"

    def test_parses_automation_name_from_payload(self, workspace):
        """Should extract automationname from AUTOMATION_EVENT_PAYLOAD."""
        payload = {"automation_name": "Daily Report"}
        with patch.dict(os.environ, {"AUTOMATION_EVENT_PAYLOAD": json.dumps(payload)}):
            tags = workspace.default_conversation_tags
            assert tags["automationname"] == "Daily Report"

    def test_parses_run_id_from_env_var(self, workspace):
        """Should extract runid from AUTOMATION_RUN_ID env var."""
        with patch.dict(os.environ, {"AUTOMATION_RUN_ID": "run-456"}):
            workspace._automation_run_id = None
            tags = workspace.default_conversation_tags
            assert tags["automationrunid"] == "run-456"

    def test_prefers_env_var_run_id_over_private_attr(self, workspace):
        """Should prefer AUTOMATION_RUN_ID env var over _automation_run_id."""
        with patch.dict(os.environ, {"AUTOMATION_RUN_ID": "env-run-id"}):
            workspace._automation_run_id = "attr-run-id"
            tags = workspace.default_conversation_tags
            assert tags["automationrunid"] == "env-run-id"

    def test_uses_private_attr_run_id_when_no_env_var(self, workspace):
        """Should use _automation_run_id when env var not set."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("AUTOMATION_RUN_ID", None)
            workspace._automation_run_id = "attr-run-id"
            tags = workspace.default_conversation_tags
            assert tags["automationrunid"] == "attr-run-id"

    def test_handles_invalid_json_payload(self, workspace):
        """Should handle invalid JSON in AUTOMATION_EVENT_PAYLOAD gracefully."""
        with patch.dict(os.environ, {"AUTOMATION_EVENT_PAYLOAD": "not-valid-json"}):
            # Should not raise, just return empty tags
            tags = workspace.default_conversation_tags
            assert "trigger" not in tags

    def test_handles_non_dict_json_payload(self, workspace):
        """Should handle non-dict JSON payload gracefully."""
        with patch.dict(os.environ, {"AUTOMATION_EVENT_PAYLOAD": '"just a string"'}):
            # Should not raise
            tags = workspace.default_conversation_tags
            # Might raise AttributeError on .get() for string, ensure graceful handling
            assert isinstance(tags, dict)

    def test_parses_full_payload(self, workspace):
        """Should parse all fields from a complete payload."""
        payload = {
            "trigger": "webhook",
            "automation_id": "auto-abc",
            "automation_name": "PR Review Bot",
        }
        with patch.dict(
            os.environ,
            {
                "AUTOMATION_EVENT_PAYLOAD": json.dumps(payload),
                "AUTOMATION_RUN_ID": "run-xyz",
            },
        ):
            tags = workspace.default_conversation_tags
            assert tags["automationtrigger"] == "webhook"
            assert tags["automationid"] == "auto-abc"
            assert tags["automationname"] == "PR Review Bot"
            assert tags["automationrunid"] == "run-xyz"
            # Skills are NOT included in workspace tags
            assert "skills" not in tags


class TestConversationTagMerging:
    """Tests for automatic tag merging in Conversation factory."""

    def test_merges_default_tags_with_user_tags(self):
        """User tags should override workspace default tags."""
        from unittest.mock import MagicMock

        from openhands.sdk.conversation.conversation import Conversation
        from openhands.sdk.workspace import RemoteWorkspace

        # Create a mock workspace with default_conversation_tags
        mock_workspace = MagicMock(spec=RemoteWorkspace)
        mock_workspace.default_conversation_tags = {
            "automationtrigger": "cron",
            "automationid": "auto-123",
        }

        # Mock RemoteConversation at the impl module level (where it's imported from)
        with patch(
            "openhands.sdk.conversation.impl.remote_conversation.RemoteConversation"
        ) as mock_convo_class:
            mock_convo_class.return_value = MagicMock()

            # Create with user tags that override some defaults
            user_tags = {"automationtrigger": "manual", "custom": "value"}

            Conversation(
                agent=MagicMock(),
                workspace=mock_workspace,
                tags=user_tags,
            )

            # Check the tags passed to RemoteConversation
            call_kwargs = mock_convo_class.call_args.kwargs
            effective_tags = call_kwargs["tags"]

            # User's "trigger" should override workspace's "trigger"
            assert effective_tags["automationtrigger"] == "manual"
            # Workspace's automationid should be preserved
            assert effective_tags["automationid"] == "auto-123"
            # User's custom tag should be included
            assert effective_tags["custom"] == "value"

    def test_uses_only_default_tags_when_no_user_tags(self):
        """Should use workspace default tags when user provides none."""
        from unittest.mock import MagicMock

        from openhands.sdk.conversation.conversation import Conversation
        from openhands.sdk.workspace import RemoteWorkspace

        mock_workspace = MagicMock(spec=RemoteWorkspace)
        mock_workspace.default_conversation_tags = {
            "automationtrigger": "cron",
            "automationid": "auto-123",
        }

        with patch(
            "openhands.sdk.conversation.impl.remote_conversation.RemoteConversation"
        ) as mock_convo_class:
            mock_convo_class.return_value = MagicMock()

            Conversation(
                agent=MagicMock(),
                workspace=mock_workspace,
                tags=None,
            )

            call_kwargs = mock_convo_class.call_args.kwargs
            effective_tags = call_kwargs["tags"]

            assert effective_tags["automationtrigger"] == "cron"
            assert effective_tags["automationid"] == "auto-123"

    def test_no_merge_when_workspace_returns_none_for_default_tags(self):
        """Should not merge when workspace returns None for default tags."""
        from unittest.mock import MagicMock

        from openhands.sdk.conversation.conversation import Conversation
        from openhands.sdk.workspace import RemoteWorkspace

        # Create mock with default_conversation_tags returning None
        mock_workspace = MagicMock(spec=RemoteWorkspace)
        mock_workspace.default_conversation_tags = None

        with patch(
            "openhands.sdk.conversation.impl.remote_conversation.RemoteConversation"
        ) as mock_convo_class:
            mock_convo_class.return_value = MagicMock()

            user_tags = {"custom": "value"}
            Conversation(
                agent=MagicMock(),
                workspace=mock_workspace,
                tags=user_tags,
            )

            call_kwargs = mock_convo_class.call_args.kwargs
            effective_tags = call_kwargs["tags"]

            # Should just use user tags
            assert effective_tags == {"custom": "value"}

    def test_no_merge_when_default_tags_empty(self):
        """Should not merge when workspace returns empty default tags."""
        from unittest.mock import MagicMock

        from openhands.sdk.conversation.conversation import Conversation
        from openhands.sdk.workspace import RemoteWorkspace

        mock_workspace = MagicMock(spec=RemoteWorkspace)
        mock_workspace.default_conversation_tags = {}

        with patch(
            "openhands.sdk.conversation.impl.remote_conversation.RemoteConversation"
        ) as mock_convo_class:
            mock_convo_class.return_value = MagicMock()

            user_tags = {"custom": "value"}
            Conversation(
                agent=MagicMock(),
                workspace=mock_workspace,
                tags=user_tags,
            )

            call_kwargs = mock_convo_class.call_args.kwargs
            # When default tags are empty, effective_tags should be user_tags
            assert call_kwargs["tags"] == user_tags


class TestPluginSourceUrl:
    """Tests for PluginSource.source_url property."""

    def test_github_shorthand_basic(self):
        """Should convert github:owner/repo to full URL."""
        from openhands.sdk.plugin import PluginSource

        plugin = PluginSource(source="github:OpenHands/skills")
        assert plugin.source_url == "https://github.com/OpenHands/skills"

    def test_github_shorthand_with_ref(self):
        """Should add tree/ref for github: sources with ref."""
        from openhands.sdk.plugin import PluginSource

        plugin = PluginSource(source="github:OpenHands/skills", ref="v1.0.0")
        assert plugin.source_url == "https://github.com/OpenHands/skills/tree/v1.0.0"

    def test_github_shorthand_with_repo_path(self):
        """Should add tree/main/path for github: sources with repo_path."""
        from openhands.sdk.plugin import PluginSource

        plugin = PluginSource(
            source="github:OpenHands/monorepo", repo_path="plugins/security"
        )
        assert (
            plugin.source_url
            == "https://github.com/OpenHands/monorepo/tree/main/plugins/security"
        )

    def test_github_shorthand_with_ref_and_path(self):
        """Should include both ref and path in URL."""
        from openhands.sdk.plugin import PluginSource

        plugin = PluginSource(
            source="github:OpenHands/monorepo",
            ref="feature-branch",
            repo_path="plugins/security",
        )
        assert (
            plugin.source_url
            == "https://github.com/OpenHands/monorepo/tree/feature-branch/plugins/security"
        )

    def test_full_github_url_preserved(self):
        """Should preserve full GitHub URLs."""
        from openhands.sdk.plugin import PluginSource

        plugin = PluginSource(source="https://github.com/OpenHands/skills")
        assert plugin.source_url == "https://github.com/OpenHands/skills"

    def test_github_blob_url_preserved(self):
        """Should preserve GitHub blob URLs as-is."""
        from openhands.sdk.plugin import PluginSource

        plugin = PluginSource(
            source="https://github.com/OpenHands/skills/blob/main/SKILL.md"
        )
        assert (
            plugin.source_url
            == "https://github.com/OpenHands/skills/blob/main/SKILL.md"
        )

    def test_local_path_returns_none(self):
        """Should return None for local paths (not portable)."""
        from openhands.sdk.plugin import PluginSource

        for path in ["/absolute/path", "./relative", "../parent", "~/home"]:
            plugin = PluginSource(source=path)
            assert plugin.source_url is None, f"Expected None for {path}"

    def test_gitlab_url_with_ref(self):
        """Should use GitLab's /-/tree/ format for refs."""
        from openhands.sdk.plugin import PluginSource

        plugin = PluginSource(source="https://gitlab.com/owner/repo", ref="v2.0.0")
        assert plugin.source_url == "https://gitlab.com/owner/repo/-/tree/v2.0.0"

    def test_gitlab_url_with_ref_and_path(self):
        """Should include path in GitLab URL."""
        from openhands.sdk.plugin import PluginSource

        plugin = PluginSource(
            source="https://gitlab.com/owner/repo",
            ref="main",
            repo_path="plugins/skill",
        )
        assert (
            plugin.source_url
            == "https://gitlab.com/owner/repo/-/tree/main/plugins/skill"
        )

    def test_bitbucket_url_with_ref(self):
        """Should use Bitbucket's /src/ format for refs."""
        from openhands.sdk.plugin import PluginSource

        plugin = PluginSource(source="https://bitbucket.org/owner/repo", ref="v1.0.0")
        assert plugin.source_url == "https://bitbucket.org/owner/repo/src/v1.0.0/"

    def test_bitbucket_url_with_ref_and_path(self):
        """Should include path in Bitbucket URL."""
        from openhands.sdk.plugin import PluginSource

        plugin = PluginSource(
            source="https://bitbucket.org/owner/repo",
            ref="develop",
            repo_path="lib/plugin",
        )
        assert (
            plugin.source_url
            == "https://bitbucket.org/owner/repo/src/develop/lib/plugin"
        )

    def test_other_git_url_returns_as_is(self):
        """Should return other git URLs as-is without ref appended."""
        from openhands.sdk.plugin import PluginSource

        # Unknown git hosts can't have refs appended safely
        plugin = PluginSource(source="https://git.example.com/repo.git", ref="v1.0")
        assert plugin.source_url == "https://git.example.com/repo.git"


class TestPluginsTagInConversation:
    """Tests for automatic plugins tag generation in Conversation factory."""

    def test_plugins_added_as_urls_in_tags(self):
        """Should serialize plugins to URLs in the tags."""
        from unittest.mock import MagicMock

        from openhands.sdk.conversation.conversation import Conversation
        from openhands.sdk.plugin import PluginSource
        from openhands.sdk.workspace import RemoteWorkspace

        mock_workspace = MagicMock(spec=RemoteWorkspace)
        mock_workspace.default_conversation_tags = {}

        plugins = [
            PluginSource(source="github:OpenHands/security-skill", ref="v1.0.0"),
            PluginSource(source="github:OpenHands/review-skill"),
        ]

        with patch(
            "openhands.sdk.conversation.impl.remote_conversation.RemoteConversation"
        ) as mock_convo_class:
            mock_convo_class.return_value = MagicMock()

            Conversation(
                agent=MagicMock(),
                workspace=mock_workspace,
                plugins=plugins,
            )

            call_kwargs = mock_convo_class.call_args.kwargs
            effective_tags = call_kwargs["tags"]

            assert "plugins" in effective_tags
            plugin_urls = effective_tags["plugins"].split(",")
            assert len(plugin_urls) == 2
            assert (
                "https://github.com/OpenHands/security-skill/tree/v1.0.0" in plugin_urls
            )
            assert "https://github.com/OpenHands/review-skill" in plugin_urls

    def test_local_plugins_not_included_in_tags(self):
        """Should not include local path plugins in tags."""
        from unittest.mock import MagicMock

        from openhands.sdk.conversation.conversation import Conversation
        from openhands.sdk.plugin import PluginSource
        from openhands.sdk.workspace import RemoteWorkspace

        mock_workspace = MagicMock(spec=RemoteWorkspace)
        mock_workspace.default_conversation_tags = {}

        plugins = [
            PluginSource(source="github:OpenHands/skill"),
            PluginSource(source="/local/path/to/plugin"),  # Should be skipped
        ]

        with patch(
            "openhands.sdk.conversation.impl.remote_conversation.RemoteConversation"
        ) as mock_convo_class:
            mock_convo_class.return_value = MagicMock()

            Conversation(
                agent=MagicMock(),
                workspace=mock_workspace,
                plugins=plugins,
            )

            call_kwargs = mock_convo_class.call_args.kwargs
            effective_tags = call_kwargs["tags"]

            # Should only have one plugin (the GitHub one)
            assert effective_tags["plugins"] == "https://github.com/OpenHands/skill"

    def test_plugins_tag_merges_with_other_tags(self):
        """Plugins tag should merge with workspace and user tags."""
        from unittest.mock import MagicMock

        from openhands.sdk.conversation.conversation import Conversation
        from openhands.sdk.plugin import PluginSource
        from openhands.sdk.workspace import RemoteWorkspace

        mock_workspace = MagicMock(spec=RemoteWorkspace)
        mock_workspace.default_conversation_tags = {
            "automationtrigger": "webhook",
            "automationid": "auto-123",
        }

        plugins = [PluginSource(source="github:OpenHands/skill")]

        with patch(
            "openhands.sdk.conversation.impl.remote_conversation.RemoteConversation"
        ) as mock_convo_class:
            mock_convo_class.return_value = MagicMock()

            Conversation(
                agent=MagicMock(),
                workspace=mock_workspace,
                plugins=plugins,
                tags={"custom": "value"},
            )

            call_kwargs = mock_convo_class.call_args.kwargs
            effective_tags = call_kwargs["tags"]

            # All tags should be present
            assert effective_tags["automationtrigger"] == "webhook"
            assert effective_tags["automationid"] == "auto-123"
            assert effective_tags["plugins"] == "https://github.com/OpenHands/skill"
            assert effective_tags["custom"] == "value"
