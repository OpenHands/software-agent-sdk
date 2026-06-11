"""Tests for MCP tool parameter secret/environment variable expansion.

Covers the behavior described in GitHub issue #3277: MCP tool parameters should
expand secrets the same way terminal commands do, instead of passing the literal
"$SECRET_NAME" through to the MCP server. Secret values echoed back by the server
are masked in the observation returned to the agent.
"""

from typing import Any
from unittest.mock import MagicMock

import mcp.types
import pytest

from openhands.sdk.conversation.secret_registry import SecretRegistry
from openhands.sdk.llm import ImageContent
from openhands.sdk.mcp.definition import MCPToolAction, MCPToolObservation
from openhands.sdk.mcp.tool import MCPToolExecutor


@pytest.fixture
def secret_registry() -> SecretRegistry:
    registry = SecretRegistry()
    registry.update_secrets(
        {
            "CUSTOMER_ID": "expanded-customer",
            "API_KEY": "expanded-api-key",
        }
    )
    return registry


@pytest.fixture
def conversation(secret_registry: SecretRegistry) -> MagicMock:
    conv = MagicMock()
    conv.state.secret_registry = secret_registry
    return conv


@pytest.fixture
def mock_client() -> MagicMock:
    client = MagicMock()
    client.is_connected.return_value = True
    return client


@pytest.fixture
def executor(mock_client: MagicMock) -> MCPToolExecutor:
    return MCPToolExecutor(tool_name="test_tool", client=mock_client)


def _make_result(content=None, is_error=False) -> MagicMock:
    """Build a mock CallToolResult with the given content blocks."""
    result = MagicMock(spec=mcp.types.CallToolResult)
    result.content = content or [mcp.types.TextContent(type="text", text="Success")]
    result.isError = is_error
    return result


def _call_and_capture(executor, mock_client, action, conversation, result=None):
    """Run the executor, returning (action_sent_to_server, observation_returned)."""
    captured: dict[str, Any] = {}

    def fake_call(coro_func, **kwargs):
        captured["action"] = kwargs.get("action")
        return MCPToolObservation.from_call_tool_result(
            tool_name="test_tool",
            result=result if result is not None else _make_result(),
        )

    mock_client.call_async_from_sync = fake_call
    observation = executor(action, conversation=conversation)
    assert "action" in captured, "MCP client was not called"
    return captured["action"], observation


@pytest.mark.parametrize(
    "input_data, expected",
    [
        pytest.param(
            {"customer_id": "$CUSTOMER_ID", "api_key": "${API_KEY}"},
            {"customer_id": "expanded-customer", "api_key": "expanded-api-key"},
            id="unbraced-and-braced",
        ),
        pytest.param(
            {
                "existing": "${CUSTOMER_ID:-fallback}",
                "missing": "${NONEXISTENT:-default-value}",
            },
            {"existing": "expanded-customer", "missing": "default-value"},
            id="braced-with-default",
        ),
        pytest.param(
            {
                "auth": {
                    "customer_id": "$CUSTOMER_ID",
                    "credentials": {"token": "${API_KEY}"},
                },
                "ids": ["$CUSTOMER_ID", "static-id"],
            },
            {
                "auth": {
                    "customer_id": "expanded-customer",
                    "credentials": {"token": "expanded-api-key"},
                },
                "ids": ["expanded-customer", "static-id"],
            },
            id="nested-dicts-and-lists",
        ),
        pytest.param(
            {
                "url": "https://$CUSTOMER_ID.example.com/v1",
                "auth": "Bearer ${API_KEY}",
                "pair": "$CUSTOMER_ID:${API_KEY}",
            },
            {
                "url": "https://expanded-customer.example.com/v1",
                "auth": "Bearer expanded-api-key",
                "pair": "expanded-customer:expanded-api-key",
            },
            id="embedded-and-multiple-refs-in-one-string",
        ),
        pytest.param(
            {"price": "$5", "doubled": "$$", "bare": "$", "shell": "$ ls"},
            {"price": "$5", "doubled": "$$", "bare": "$", "shell": "$ ls"},
            id="literal-dollar-is-not-corrupted",
        ),
        pytest.param(
            {"a": "$UNKNOWN", "b": "${ALSO_UNKNOWN}"},
            {"a": "$UNKNOWN", "b": "${ALSO_UNKNOWN}"},
            id="unknown-var-without-default-is-preserved",
        ),
        pytest.param(
            {"count": 5, "ok": True, "ratio": 1.5, "nothing": None},
            {"count": 5, "ok": True, "ratio": 1.5, "nothing": None},
            id="non-string-scalars-pass-through-untouched",
        ),
    ],
)
def test_executor_expands_secrets_in_action_data(
    executor, mock_client, conversation, input_data, expected
):
    """Secret references are expanded before the action is sent to the server."""
    sent, _ = _call_and_capture(
        executor, mock_client, MCPToolAction(data=input_data), conversation
    )
    assert sent.data == expected


def test_executor_without_conversation_passes_literal_values(executor, mock_client):
    """Without a conversation there is no secret registry, so values stay literal."""
    action = MCPToolAction(
        data={"customer_id": "$CUSTOMER_ID", "api_key": "${API_KEY}"}
    )
    sent, _ = _call_and_capture(executor, mock_client, action, conversation=None)
    assert sent.data == {"customer_id": "$CUSTOMER_ID", "api_key": "${API_KEY}"}


def test_executor_does_not_expand_host_env_vars(
    executor, mock_client, conversation, monkeypatch
):
    """Host environment variables must NOT be expanded into tool params.

    Tool-parameter expansion runs with check_env=False, so secret values reach
    MCP servers but arbitrary host env vars never do. A reference to an env var
    that is not a registered secret stays literal, while a registered secret in
    the same payload is still expanded.
    """
    monkeypatch.setenv("SOME_HOST_ENV", "host-env-value")
    action = MCPToolAction(
        data={"from_env": "$SOME_HOST_ENV", "from_secret": "$CUSTOMER_ID"}
    )
    sent, _ = _call_and_capture(executor, mock_client, action, conversation)
    # Env var is not a registered secret -> left as a literal placeholder.
    assert sent.data["from_env"] == "$SOME_HOST_ENV"
    # Registered secret -> expanded as usual.
    assert sent.data["from_secret"] == "expanded-customer"


def test_executor_masks_secrets_in_returned_observation(
    executor, mock_client, conversation
):
    """End-to-end: a secret echoed by the server is masked on the way back.

    The action references $API_KEY, the MCP server echoes the resolved value,
    and the observation returned to the agent must have it masked. Non-text
    blocks and other observation fields must survive the masking rebuild.
    """
    secret_value = "expanded-api-key"
    result = _make_result(
        content=[
            mcp.types.TextContent(
                type="text", text=f"authenticated with {secret_value}"
            ),
            mcp.types.ImageContent(type="image", data="aGVsbG8=", mimeType="image/png"),
        ],
    )
    action = MCPToolAction(data={"api_key": "$API_KEY"})

    _, observation = _call_and_capture(
        executor, mock_client, action, conversation, result=result
    )

    # The secret value echoed by the server is masked in the observation.
    assert secret_value not in observation.text
    assert "<secret-hidden>" in observation.text
    # Non-text (image) blocks pass through untouched.
    image_blocks = [b for b in observation.content if isinstance(b, ImageContent)]
    assert len(image_blocks) == 1
    assert image_blocks[0].image_urls == ["data:image/png;base64,aGVsbG8="]
    # Other observation fields survive the model_copy rebuild.
    assert observation.tool_name == "test_tool"
    assert observation.is_error is False


@pytest.mark.parametrize("secret_name", ["CUSTOMER_ID", "API_KEY"])
def test_registry_masks_secret_values_in_text(secret_registry, secret_name):
    """Registered secret values are replaced with <secret-hidden> in text."""
    value = secret_registry.get_secret_value(secret_name)  # also tracks for masking
    assert value is not None
    masked = secret_registry.mask_secrets_in_output(f"leaked {value} here")
    assert value not in masked
    assert "<secret-hidden>" in masked


# --- Failure / non-happy paths ------------------------------------------------


def test_executor_returns_error_observation_on_timeout(
    executor, mock_client, conversation
):
    """A client timeout is turned into an error observation, not raised."""

    def raise_timeout(coro_func, **kwargs):
        raise TimeoutError

    mock_client.call_async_from_sync = raise_timeout

    observation = executor(MCPToolAction(data={"q": "x"}), conversation=conversation)

    assert observation.is_error is True
    assert "timed out" in observation.text
    assert observation.tool_name == "test_tool"


def test_executor_falls_back_to_literal_when_expansion_raises(executor, mock_client):
    """If secret lookup blows up, the literal action is sent and the call proceeds."""
    conv = MagicMock()
    conv.state.secret_registry.get_secret_value.side_effect = RuntimeError("down")

    action = MCPToolAction(data={"customer_id": "$CUSTOMER_ID"})
    sent, observation = _call_and_capture(executor, mock_client, action, conv)

    # Expansion failed -> original placeholder is sent unchanged, no exception.
    assert sent.data == {"customer_id": "$CUSTOMER_ID"}
    assert observation.is_error is False


def test_executor_returns_unmasked_observation_when_masking_raises(
    executor, mock_client
):
    """If masking blows up, the original observation is returned, not raised."""
    conv = MagicMock()
    conv.state.secret_registry.mask_secrets_in_output.side_effect = RuntimeError("down")

    result = _make_result(
        content=[mcp.types.TextContent(type="text", text="hello world")]
    )
    # No references -> expansion is a no-op, isolating the masking failure.
    action = MCPToolAction(data={"q": "noop"})
    _, observation = _call_and_capture(
        executor, mock_client, action, conv, result=result
    )

    assert "hello world" in observation.text


def test_executor_without_conversation_does_not_mask(executor, mock_client):
    """Without a conversation there is no registry, so output is not masked."""
    result = _make_result(
        content=[mcp.types.TextContent(type="text", text="token=expanded-api-key")]
    )
    _, observation = _call_and_capture(
        executor, mock_client, MCPToolAction(data={"q": "x"}), None, result=result
    )

    assert "expanded-api-key" in observation.text
    assert "<secret-hidden>" not in observation.text
