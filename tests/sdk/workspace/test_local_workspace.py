"""Tests for LocalWorkspace automation completion callback behavior."""

from unittest.mock import MagicMock, patch

from openhands.sdk.workspace import LocalWorkspace


def test_context_exit_sends_registered_cost_in_completion_callback(
    tmp_path, monkeypatch
):
    """Leaving the workspace context reports the registered cost, so a locally
    run automation is billed the same way a remote one is."""
    monkeypatch.setenv("AUTOMATION_CALLBACK_URL", "https://svc.test/complete")
    workspace = LocalWorkspace(working_dir=str(tmp_path))
    workspace.register_cost(0.4213)

    with patch("httpx.Client") as MockClient:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        MockClient.return_value = mock_client

        with workspace:
            pass

        payload = mock_client.post.call_args.kwargs["json"]
        assert payload["cost"] == 0.4213


def test_context_exit_omits_registered_task_outcome_without_support_flag(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("AUTOMATION_CALLBACK_URL", "https://svc.test/complete")
    monkeypatch.delenv("AUTOMATION_CALLBACK_SUPPORTS_AGENT_OUTCOME", raising=False)
    workspace = LocalWorkspace(working_dir=str(tmp_path))
    workspace.register_task_outcome({"status": "success", "summary": "Done."})

    with patch("httpx.Client") as MockClient:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        MockClient.return_value = mock_client

        with workspace:
            pass

        payload = mock_client.post.call_args.kwargs["json"]
        assert "agent_outcome" not in payload


def test_context_exit_sends_registered_task_outcome_with_support_flag(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("AUTOMATION_CALLBACK_URL", "https://svc.test/complete")
    monkeypatch.setenv("AUTOMATION_CALLBACK_SUPPORTS_AGENT_OUTCOME", "true")
    workspace = LocalWorkspace(working_dir=str(tmp_path))
    outcome = {"status": "success", "summary": "Done."}
    workspace.register_task_outcome(outcome)

    with patch("httpx.Client") as MockClient:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        MockClient.return_value = mock_client

        with workspace:
            pass

        payload = mock_client.post.call_args.kwargs["json"]
        assert payload["agent_outcome"] == outcome


def test_context_exit_sends_nothing_without_callback_url(tmp_path, monkeypatch):
    """Ordinary local usage stays offline: no automation callback is configured,
    so leaving the context must not reach the network."""
    monkeypatch.delenv("AUTOMATION_CALLBACK_URL", raising=False)
    workspace = LocalWorkspace(working_dir=str(tmp_path))
    workspace.register_cost(0.4213)

    with patch("httpx.Client") as MockClient:
        with workspace:
            pass

        assert MockClient.call_count == 0
