"""Tests for GitHub autospawn functionality."""

import hashlib
import hmac
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config
from openhands.agent_server.github_autospawn_models import (
    GitHubAgentConfig,
    GitHubTriggerConfig,
    GitHubWebhookConfig,
)
from openhands.agent_server.github_autospawn_service import (
    get_pr_ref,
    match_triggers,
    verify_github_signature,
)
from openhands.sdk import Agent, LLM


class TestHMACVerification:
    """Tests for GitHub webhook signature verification."""

    def test_verify_valid_signature(self):
        """Test that valid HMAC signatures are accepted."""
        secret = SecretStr("my_secret_key")
        payload = b'{"key": "value"}'

        # Generate valid signature
        signature_hash = hmac.new(
            secret.get_secret_value().encode("utf-8"),
            payload,
            hashlib.sha256,
        ).hexdigest()
        signature_header = f"sha256={signature_hash}"

        assert verify_github_signature(payload, signature_header, secret) is True

    def test_verify_invalid_signature(self):
        """Test that invalid HMAC signatures are rejected."""
        secret = SecretStr("my_secret_key")
        payload = b'{"key": "value"}'
        signature_header = "sha256=invalid_signature_hash"

        assert verify_github_signature(payload, signature_header, secret) is False

    def test_verify_missing_signature(self):
        """Test that missing signatures are rejected."""
        secret = SecretStr("my_secret_key")
        payload = b'{"key": "value"}'

        assert verify_github_signature(payload, "", secret) is False
        assert verify_github_signature(payload, None, secret) is False

    def test_verify_wrong_format(self):
        """Test that incorrectly formatted signatures are rejected."""
        secret = SecretStr("my_secret_key")
        payload = b'{"key": "value"}'
        signature_header = "sha1=somehash"  # Wrong algorithm prefix

        assert verify_github_signature(payload, signature_header, secret) is False

    def test_verify_tampered_payload(self):
        """Test that tampered payloads are detected."""
        secret = SecretStr("my_secret_key")
        original_payload = b'{"key": "value"}'
        tampered_payload = b'{"key": "tampered"}'

        # Generate signature for original payload
        signature_hash = hmac.new(
            secret.get_secret_value().encode("utf-8"),
            original_payload,
            hashlib.sha256,
        ).hexdigest()
        signature_header = f"sha256={signature_hash}"

        # Verify with tampered payload should fail
        assert (
            verify_github_signature(tampered_payload, signature_header, secret) is False
        )


class TestTriggerMatching:
    """Tests for GitHub webhook trigger matching logic."""

    def test_match_event_type(self):
        """Test matching on event type."""
        triggers = [
            GitHubTriggerConfig(
                event="pull_request",
                repo="owner/repo",
                agent_config=GitHubAgentConfig(
                    task="Test task",
                    agent=Agent(llm=LLM(model="gpt-4o")),
                ),
            ),
        ]

        payload = {"repository": {"full_name": "owner/repo"}}

        # Should match
        matches = match_triggers("pull_request", payload, triggers)
        assert len(matches) == 1

        # Should not match different event
        matches = match_triggers("push", payload, triggers)
        assert len(matches) == 0

    def test_match_action(self):
        """Test matching on event action."""
        triggers = [
            GitHubTriggerConfig(
                event="pull_request",
                action="opened",
                repo="owner/repo",
                agent_config=GitHubAgentConfig(
                    task="Test task",
                    agent=Agent(llm=LLM(model="gpt-4o")),
                ),
            ),
        ]

        payload = {
            "repository": {"full_name": "owner/repo"},
            "action": "opened",
        }

        # Should match
        matches = match_triggers("pull_request", payload, triggers)
        assert len(matches) == 1

        # Should not match different action
        payload["action"] = "closed"
        matches = match_triggers("pull_request", payload, triggers)
        assert len(matches) == 0

    def test_match_repository(self):
        """Test matching on repository."""
        triggers = [
            GitHubTriggerConfig(
                event="pull_request",
                repo="owner/repo",
                agent_config=GitHubAgentConfig(
                    task="Test task",
                    agent=Agent(llm=LLM(model="gpt-4o")),
                ),
            ),
        ]

        payload = {"repository": {"full_name": "owner/repo"}}

        # Should match
        matches = match_triggers("pull_request", payload, triggers)
        assert len(matches) == 1

        # Should not match different repo
        payload["repository"]["full_name"] = "other/repo"
        matches = match_triggers("pull_request", payload, triggers)
        assert len(matches) == 0

    def test_match_branch(self):
        """Test matching on branch for push events."""
        triggers = [
            GitHubTriggerConfig(
                event="push",
                repo="owner/repo",
                branch="main",
                agent_config=GitHubAgentConfig(
                    task="Test task",
                    agent=Agent(llm=LLM(model="gpt-4o")),
                ),
            ),
        ]

        payload = {
            "repository": {"full_name": "owner/repo"},
            "ref": "refs/heads/main",
        }

        # Should match
        matches = match_triggers("push", payload, triggers)
        assert len(matches) == 1

        # Should not match different branch
        payload["ref"] = "refs/heads/develop"
        matches = match_triggers("push", payload, triggers)
        assert len(matches) == 0

    def test_match_multiple_triggers(self):
        """Test that multiple triggers can match the same event."""
        triggers = [
            GitHubTriggerConfig(
                event="pull_request",
                action="opened",
                repo="owner/repo",
                agent_config=GitHubAgentConfig(
                    task="Task 1",
                    agent=Agent(llm=LLM(model="gpt-4o")),
                ),
            ),
            GitHubTriggerConfig(
                event="pull_request",
                repo="owner/repo",
                agent_config=GitHubAgentConfig(
                    task="Task 2",
                    agent=Agent(llm=LLM(model="gpt-4o")),
                ),
            ),
        ]

        payload = {
            "repository": {"full_name": "owner/repo"},
            "action": "opened",
        }

        matches = match_triggers("pull_request", payload, triggers)
        assert len(matches) == 2


class TestPRRefExtraction:
    """Tests for extracting git refs from PR payloads."""

    def test_get_pr_ref(self):
        """Test extracting PR head SHA from payload."""
        payload = {
            "pull_request": {
                "head": {
                    "sha": "abc123def456",
                    "ref": "feature-branch",
                }
            }
        }

        ref = get_pr_ref(payload)
        assert ref == "abc123def456"

    def test_get_pr_ref_no_pull_request(self):
        """Test that non-PR events return None."""
        payload = {
            "repository": {"full_name": "owner/repo"},
        }

        ref = get_pr_ref(payload)
        assert ref is None


@pytest.fixture
def test_config():
    """Create test configuration."""
    return Config(
        github_autospawn=GitHubWebhookConfig(
            github_secret=SecretStr("test_secret"),
            triggers=[
                GitHubTriggerConfig(
                    event="pull_request",
                    action="opened",
                    repo="test/repo",
                    agent_config=GitHubAgentConfig(
                        task="Review PR",
                        agent=Agent(llm=LLM(model="gpt-4o")),
                    ),
                ),
            ],
        ),
    )


class TestWebhookEndpoint:
    """Integration tests for the webhook endpoint."""

    def test_webhook_missing_signature(self, test_config):
        """Test that requests without signature are rejected when secret is configured."""
        app = create_app(test_config)
        client = TestClient(app)

        payload = {
            "repository": {"full_name": "test/repo"},
            "action": "opened",
        }

        response = client.post(
            "/webhooks/github",
            json=payload,
            headers={"X-GitHub-Event": "pull_request"},
        )

        assert response.status_code == 401

    def test_webhook_invalid_signature(self, test_config):
        """Test that requests with invalid signature are rejected."""
        app = create_app(test_config)
        client = TestClient(app)

        payload = {
            "repository": {"full_name": "test/repo"},
            "action": "opened",
        }

        response = client.post(
            "/webhooks/github",
            json=payload,
            headers={
                "X-GitHub-Event": "pull_request",
                "X-Hub-Signature-256": "sha256=invalid",
            },
        )

        assert response.status_code == 401

    def test_webhook_missing_event_header(self, test_config):
        """Test that requests without event header are rejected."""
        app = create_app(test_config)
        client = TestClient(app)

        payload = {"repository": {"full_name": "test/repo"}}
        payload_bytes = json.dumps(payload).encode("utf-8")

        # Generate valid signature
        signature_hash = hmac.new(
            b"test_secret",
            payload_bytes,
            hashlib.sha256,
        ).hexdigest()

        response = client.post(
            "/webhooks/github",
            json=payload,
            headers={"X-Hub-Signature-256": f"sha256={signature_hash}"},
        )

        assert response.status_code == 400

    @patch("openhands.agent_server.github_autospawn_service.process_github_webhook")
    def test_webhook_valid_request(self, mock_process, test_config):
        """Test that valid webhook requests are accepted."""
        mock_process.return_value = AsyncMock(return_value=1)

        app = create_app(test_config)
        client = TestClient(app)

        payload = {
            "repository": {"full_name": "test/repo", "clone_url": "https://github.com/test/repo"},
            "action": "opened",
        }
        payload_bytes = json.dumps(payload).encode("utf-8")

        # Generate valid signature
        signature_hash = hmac.new(
            b"test_secret",
            payload_bytes,
            hashlib.sha256,
        ).hexdigest()

        response = client.post(
            "/webhooks/github",
            json=payload,
            headers={
                "X-GitHub-Event": "pull_request",
                "X-Hub-Signature-256": f"sha256={signature_hash}",
            },
        )

        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_webhook_no_secret_configured(self):
        """Test that webhooks work without signature verification when no secret is configured."""
        config = Config(
            github_autospawn=GitHubWebhookConfig(
                github_secret=None,  # No secret
                triggers=[
                    GitHubTriggerConfig(
                        event="pull_request",
                        repo="test/repo",
                        agent_config=GitHubAgentConfig(
                            task="Review PR",
                            agent=Agent(llm=LLM(model="gpt-4o")),
                        ),
                    ),
                ],
            ),
        )

        app = create_app(config)
        client = TestClient(app)

        payload = {"repository": {"full_name": "test/repo"}}

        # Should accept without signature
        response = client.post(
            "/webhooks/github",
            json=payload,
            headers={"X-GitHub-Event": "pull_request"},
        )

        assert response.status_code == 200
