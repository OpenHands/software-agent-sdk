from pydantic import SecretStr

from openhands.sdk.mcp.config import MCPServer
from openhands.sdk.subagent.schema import AgentDefinition
from openhands.sdk.utils.cipher import Cipher


def test_mcp_config_json_deserialization_with_cipher():
    """Verify that AgentDefinition can be deserialized from JSON under a cipher context

    when mcp_config contains encrypted SecretStr env variables.
    """
    cipher = Cipher("test-key")
    agent_def = AgentDefinition(
        name="web-researcher",
        description="test",
        mcp_config={
            "tavily": MCPServer(
                command="npx",
                args=["-y", "tavily-mcp@0.2.1"],
                env={"TAVILY_API_KEY": SecretStr("test-key")},
            )
        },
        tools=["browser_tool_set"],
        system_prompt="prompt",
    )

    # Serialize with cipher context (which encrypts SecretStr using cipher)
    json_data = agent_def.model_dump_json(context={"cipher": cipher})

    # Validate/deserialize with cipher context (which decrypts and validates them)
    loaded = AgentDefinition.model_validate_json(json_data, context={"cipher": cipher})

    assert loaded.mcp_config is not None
    assert "tavily" in loaded.mcp_config
    tavily_env = loaded.mcp_config["tavily"].env
    assert tavily_env is not None
    assert isinstance(tavily_env["TAVILY_API_KEY"], SecretStr)
    assert tavily_env["TAVILY_API_KEY"].get_secret_value() == "test-key"
