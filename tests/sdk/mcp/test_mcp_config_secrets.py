from openhands.sdk.mcp.config import coerce_mcp_config, dump_mcp_config
from openhands.sdk.subagent.schema import AgentDefinition
from openhands.sdk.utils.cipher import Cipher
from openhands.sdk.utils.pydantic_secrets import REDACTED_SECRET_VALUE


def test_all_mcp_secret_fields_round_trip_through_encrypted_json() -> None:
    raw_config = {
        "stdio": {
            "command": "echo",
            "env": {"ENV_TOKEN": "env-secret"},
            "headers": {"X-Top-Level": "header-secret"},
        },
        "header": {
            "url": "https://example.com/header",
            "auth": {
                "strategy": "header",
                "headers": {"X-Auth": "auth-header-secret"},
            },
        },
        "api-key": {
            "url": "https://example.com/api-key",
            "auth": {"strategy": "api_key", "value": "api-key-secret"},
        },
        "bearer": {
            "url": "https://example.com/bearer",
            "auth": {"strategy": "bearer", "value": "bearer-secret"},
        },
        "basic": {
            "url": "https://example.com/basic",
            "auth": {
                "strategy": "basic",
                "username": "user",
                "password": "basic-secret",
            },
        },
        "oauth": {
            "url": "https://example.com/oauth",
            "auth": {
                "strategy": "oauth2",
                "authentication": {
                    "type": "oauth",
                    "client_id": "client-id",
                    "client_secret": "oauth-config-secret",
                },
                "state": {
                    "tokens": {
                        "access_token": "oauth-access-secret",
                        "refresh_token": "oauth-refresh-secret",
                    },
                    "client_info": {"client_secret": "oauth-state-secret"},
                },
            },
        },
    }
    cipher = Cipher("mcp-secret-round-trip")
    definition = AgentDefinition(
        name="all-mcp-secrets",
        mcp_config=coerce_mcp_config(raw_config),
    )

    payload = definition.model_dump_json(context={"cipher": cipher})
    restored = AgentDefinition.model_validate_json(
        payload,
        context={"cipher": cipher},
    )

    assert restored.mcp_config is not None
    assert dump_mcp_config(restored.mcp_config) == raw_config
    for secret in (
        "env-secret",
        "header-secret",
        "auth-header-secret",
        "api-key-secret",
        "bearer-secret",
        "basic-secret",
        "oauth-config-secret",
        "oauth-access-secret",
        "oauth-refresh-secret",
        "oauth-state-secret",
    ):
        assert secret not in payload


def test_mcp_secret_validation_drops_empty_and_redacted_values() -> None:
    config = coerce_mcp_config(
        {
            "stdio": {
                "command": "echo",
                "env": {
                    "EMPTY": "",
                    "SPACE": " ",
                    "MASK": REDACTED_SECRET_VALUE,
                    "KEEP": "env-value",
                },
                "headers": {
                    "EMPTY": "",
                    "SPACE": " ",
                    "MASK": REDACTED_SECRET_VALUE,
                    "KEEP": "header-value",
                },
            },
            "api-key": {
                "url": "https://example.com/api-key",
                "auth": {
                    "strategy": "api_key",
                    "value": REDACTED_SECRET_VALUE,
                },
            },
            "oauth": {
                "url": "https://example.com/oauth",
                "auth": {
                    "strategy": "oauth2",
                    "authentication": {
                        "type": "oauth",
                        "client_secret": REDACTED_SECRET_VALUE,
                    },
                    "state": {
                        "tokens": {"access_token": REDACTED_SECRET_VALUE},
                    },
                },
            },
        }
    )

    assert dump_mcp_config(config) == {
        "stdio": {
            "command": "echo",
            "env": {"KEEP": "env-value"},
            "headers": {"KEEP": "header-value"},
        },
        "api-key": {
            "url": "https://example.com/api-key",
            "auth": {"strategy": "api_key"},
        },
        "oauth": {
            "url": "https://example.com/oauth",
            "auth": {
                "strategy": "oauth2",
                "authentication": {"type": "oauth"},
            },
        },
    }
