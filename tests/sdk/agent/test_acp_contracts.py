"""Unit tests for typed ACP capability and session contracts."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from pydantic import BaseModel

from openhands.sdk.agent.acp_contracts import (
    ACPAuthMethod,
    ACPMcpCapabilities,
    ACPRevisionedCredentialBinding,
    extract_model_config_option,
    extract_session_models,
    is_model_dumpable,
    normalize_acp_error,
    normalize_auth_method,
    normalize_mcp_capabilities,
)
from openhands.sdk.agent.acp_models import ACPModelInfo


class TestAuthMethodContracts:
    def test_normalize_auth_method_with_contract_instance(self):
        m = ACPAuthMethod(id="api-key", type="terminal")
        assert normalize_auth_method(m) is m

    def test_normalize_auth_method_with_mapping(self):
        data = {"id": "oauth", "type": "terminal"}
        m = normalize_auth_method(data)
        assert m.id == "oauth"
        assert m.type == "terminal"

    def test_normalize_auth_method_with_protocol(self):
        obj = SimpleNamespace(id="pat", type="terminal")
        m = normalize_auth_method(obj)
        assert m.id == "pat"
        assert m.type == "terminal"

    def test_normalize_auth_method_without_type(self):
        obj = SimpleNamespace(id="token")
        m = normalize_auth_method(obj)
        assert m.id == "token"
        assert m.type is None

    def test_normalize_auth_method_invalid(self):
        m = normalize_auth_method("invalid")
        assert m.id == ""
        assert m.type is None


class TestSessionModelContracts:
    def test_extract_model_config_option_none(self):
        assert extract_model_config_option(None) is None

    def test_extract_model_config_option_empty(self):
        assert extract_model_config_option(SimpleNamespace(config_options=[])) is None
        assert extract_model_config_option({"config_options": []}) is None

    def test_extract_model_config_option_unwrapped(self):
        opt = SimpleNamespace(
            id="model",
            type="select",
            current_value="claude-3-5",
            options=[{"value": "claude-3-5", "name": "Claude 3.5"}],
        )
        response = SimpleNamespace(config_options=[opt])
        extracted = extract_model_config_option(response)
        assert extracted is not None
        assert extracted.id == "model"
        assert extracted.type == "select"
        assert extracted.current_value == "claude-3-5"
        assert len(extracted.options) == 1

    def test_extract_model_config_option_rootmodel_wrapped(self):
        opt = SimpleNamespace(
            id="model",
            type="select",
            current_value="gpt-4o",
            options=[],
        )
        wrapped = SimpleNamespace(root=opt)
        response = SimpleNamespace(config_options=[wrapped])
        extracted = extract_model_config_option(response)
        assert extracted is not None
        assert extracted.id == "model"
        assert extracted.current_value == "gpt-4o"

    def test_extract_model_config_option_mapping(self):
        response = {
            "config_options": [
                {
                    "id": "model",
                    "type": "select",
                    "current_value": "gemini-pro",
                    "options": [{"value": "gemini-pro"}],
                }
            ]
        }
        extracted = extract_model_config_option(response)
        assert extracted is not None
        assert extracted.current_value == "gemini-pro"

    def test_extract_model_config_option_ignores_other_options(self):
        response = SimpleNamespace(
            config_options=[
                SimpleNamespace(id="mode", type="select", current_value="fast"),
                SimpleNamespace(id="model", type="text", current_value="invalid"),
            ]
        )
        assert extract_model_config_option(response) is None

    def test_extract_session_models_none(self):
        state = extract_session_models(None, default_via_config_option=True)
        assert state.current_model_id is None
        assert state.available_models is None
        assert state.via_config_option is True

    def test_extract_session_models_config_option(self):
        opt = SimpleNamespace(
            id="model",
            type="select",
            current_value="gpt-5",
            options=[
                SimpleNamespace(value="gpt-5", name="GPT-5", description="Latest"),
                SimpleNamespace(value="gpt-4", name="GPT-4", description=None),
            ],
        )
        response = SimpleNamespace(config_options=[opt])
        state = extract_session_models(response)
        assert state.current_model_id == "gpt-5"
        assert state.via_config_option is True
        assert state.available_models == [
            ACPModelInfo(model_id="gpt-5", name="GPT-5", description="Latest"),
            ACPModelInfo(model_id="gpt-4", name="GPT-4", description=None),
        ]

    def test_extract_session_models_legacy_capability(self):
        models = SimpleNamespace(
            current_model_id="legacy-model",
            available_models=[
                SimpleNamespace(
                    model_id="legacy-model",
                    name="Legacy",
                    description=None,
                )
            ],
        )
        response = SimpleNamespace(models=models)
        state = extract_session_models(response)
        assert state.current_model_id == "legacy-model"
        assert state.via_config_option is False
        assert state.available_models == [
            ACPModelInfo(model_id="legacy-model", name="Legacy", description=None)
        ]

    def test_extract_session_models_config_option_precedence(self):
        models = SimpleNamespace(
            current_model_id="legacy",
            available_models=[],
        )
        opt = SimpleNamespace(
            id="model",
            type="select",
            current_value="modern",
            options=[SimpleNamespace(value="modern", name="Modern", description=None)],
        )
        response = SimpleNamespace(models=models, config_options=[opt])
        state = extract_session_models(response)
        assert state.current_model_id == "modern"
        assert state.via_config_option is True


class TestMcpCapabilitiesContracts:
    def test_normalize_mcp_capabilities_instance(self):
        caps = ACPMcpCapabilities(http=True, sse=True)
        assert normalize_mcp_capabilities(caps) is caps

    def test_normalize_mcp_capabilities_mapping(self):
        data = {"http": True, "sse": False}
        caps = normalize_mcp_capabilities(data)
        assert caps.http is True
        assert caps.sse is False

    def test_normalize_mcp_capabilities_protocol(self):
        obj = SimpleNamespace(http=False, sse=True)
        caps = normalize_mcp_capabilities(obj)
        assert caps.http is False
        assert caps.sse is True

    def test_normalize_mcp_capabilities_empty(self):
        caps = normalize_mcp_capabilities(None)
        assert caps.http is False
        assert caps.sse is False


class TestErrorContracts:
    def test_normalize_acp_error_with_code_and_data(self):
        exc = SimpleNamespace(code=-32000, data={"reason": "auth required"})
        info = normalize_acp_error(exc)  # type: ignore[arg-type]
        assert info.code == -32000
        assert info.data == {"reason": "auth required"}

    def test_normalize_acp_error_standard_exception(self):
        exc = RuntimeError("something broke")
        info = normalize_acp_error(exc)
        assert info.code is None
        assert info.data is None
        assert "something broke" in info.message

    def test_normalize_acp_error_non_int_code(self):
        exc = SimpleNamespace(code="not-an-int", data=None)
        info = normalize_acp_error(exc)  # type: ignore[arg-type]
        assert info.code is None


class TestCredentialRevisionContract:
    def test_revisioned_binding_protocol(self):
        class Revisioned:
            @property
            def authorization_revision(self) -> int:
                return 42

            async def load(self): ...
            async def replace(self, expected_version: str, value: str): ...

        binding = Revisioned()
        assert isinstance(binding, ACPRevisionedCredentialBinding)
        assert binding.authorization_revision == 42

    def test_unrevisioned_binding_protocol(self):
        class Unrevisioned:
            async def load(self): ...
            async def replace(self, expected_version: str, value: str): ...

        binding = Unrevisioned()
        assert not isinstance(binding, ACPRevisionedCredentialBinding)


class TestModelDumpableContract:
    def test_pydantic_model_is_dumpable(self):
        class DummyModel(BaseModel):
            x: int = 1

        assert is_model_dumpable(DummyModel())

    def test_custom_dumpable(self):
        class CustomDumpable:
            def model_dump(self):
                return {"a": 1}

        assert is_model_dumpable(CustomDumpable())

    def test_primitive_not_dumpable(self):
        assert not is_model_dumpable("string")
        assert not is_model_dumpable(123)
        assert not is_model_dumpable({"key": "val"})


class TestACPModelInfoFromProtocol:
    def test_from_protocol_with_model_info_instance(self):
        info = ACPModelInfo(model_id="m1", name="Model 1", description="Desc")
        assert ACPModelInfo.from_protocol(info) is info

    def test_from_protocol_mapping_with_value_attr(self):
        data = {"value": "opt1", "name": "Option 1", "description": "Opt desc"}
        info = ACPModelInfo.from_protocol(data, id_attr="value")
        assert info.model_id == "opt1"
        assert info.name == "Option 1"
        assert info.description == "Opt desc"

    def test_from_protocol_duck_typed_object(self):
        obj = SimpleNamespace(model_id="m2", name="Model 2", description=None)
        info = ACPModelInfo.from_protocol(obj)
        assert info.model_id == "m2"
        assert info.name == "Model 2"
        assert info.description is None

    def test_from_protocol_degrades_gracefully(self):
        obj = SimpleNamespace(model_id=123, name=456, description=["bad"])
        info = ACPModelInfo.from_protocol(obj)
        assert info.model_id == ""
        assert info.name is None
        assert info.description is None


class TestTaskCleanup:
    def test_task_cleanup_direct_access(self):
        task1 = MagicMock()
        task2 = MagicMock()
        tasks = [("task1", task1), ("task2", task2)]

        for _, task in tasks:
            task.cancel()

        assert task1.cancel.called
        assert task2.cancel.called
