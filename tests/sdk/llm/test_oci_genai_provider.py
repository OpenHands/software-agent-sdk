from unittest.mock import patch

import pytest
from litellm.types.llms.openai import ResponsesAPIResponse
from pydantic import SecretStr, ValidationError

from openhands.sdk.llm import LLM, Message, TextContent
from openhands.sdk.utils.cipher import Cipher


OCI_MODEL = "oci_genai/xai.grok-4.6"
OCI_REGION = "us-chicago-1"
OCI_PROJECT_ID = "ocid1.generativeaiproject.oc1.us-chicago-1.example"
OCI_BASE_URL = (
    "https://inference.generativeai.us-chicago-1.oci.oraclecloud.com/openai/v1"
)


def _oci_llm(**kwargs) -> LLM:
    return LLM(
        usage_id="test-oci-genai",
        model=OCI_MODEL,
        api_key=SecretStr("oci-api-key"),
        oci_region=OCI_REGION,
        oci_project_id=OCI_PROJECT_ID,
        num_retries=0,
        **kwargs,
    )


def _response_with_text() -> ResponsesAPIResponse:
    return ResponsesAPIResponse.model_validate(
        {
            "id": "resp-text-1",
            "created_at": 1,
            "model": "xai.grok-4.6",
            "object": "response",
            "output": [
                {
                    "id": "message-1",
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "Hello from OCI",
                            "annotations": [],
                        }
                    ],
                }
            ],
        }
    )


def _response_with_tool_call() -> ResponsesAPIResponse:
    return ResponsesAPIResponse.model_validate(
        {
            "id": "resp-1",
            "created_at": 1,
            "model": "xai.grok-4.6",
            "object": "response",
            "output": [
                {
                    "id": "call-1",
                    "type": "function_call",
                    "call_id": "call-1",
                    "name": "terminal",
                    "arguments": '{"command":"pwd"}',
                    "status": "completed",
                }
            ],
        }
    )


def test_oci_genai_profile_derives_openai_compatible_transport() -> None:
    llm = _oci_llm()

    assert llm.uses_responses_api() is True
    assert llm.api_mode == "responses"
    assert llm.model == OCI_MODEL
    assert llm.base_url is None

    kwargs = llm._build_responses_call_kwargs([], None, None, {})

    assert kwargs["model"] == "xai.grok-4.6"
    assert kwargs["custom_llm_provider"] == "openai"
    assert kwargs["api_base"] == OCI_BASE_URL
    assert kwargs["api_key"] == "oci-api-key"
    assert kwargs["extra_headers"]["openai-project"] == OCI_PROJECT_ID


def test_oci_genai_provider_headers_cannot_be_overridden_per_call() -> None:
    llm = _oci_llm(extra_headers={"X-Trace": "profile"})

    kwargs = llm._build_responses_call_kwargs(
        [],
        None,
        None,
        {
            "extra_headers": {
                "X-Trace": "request",
                "openai-project": "wrong-project",
            }
        },
    )

    assert kwargs["extra_headers"] == {
        "X-Trace": "request",
        "openai-project": OCI_PROJECT_ID,
    }


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"oci_region": None}, "oci_region"),
        ({"oci_project_id": None}, "oci_project_id"),
        ({"oci_region": "https://attacker.example"}, "valid OCI region"),
        ({"oci_project_id": "not-an-ocid"}, "project OCID"),
    ],
)
def test_oci_genai_profile_validates_required_provider_fields(
    overrides: dict[str, str | None], message: str
) -> None:
    values: dict[str, object] = {
        "model": OCI_MODEL,
        "oci_region": OCI_REGION,
        "oci_project_id": OCI_PROJECT_ID,
    }
    values.update(overrides)

    with pytest.raises(ValidationError, match=message):
        LLM.model_validate(values)


def test_oci_genai_profile_normalizes_region_and_persists_provider_fields() -> None:
    llm = LLM(
        model=OCI_MODEL,
        api_key=SecretStr("oci-api-key"),
        oci_region=" US-CHICAGO-1 ",
        oci_project_id=f" {OCI_PROJECT_ID} ",
        base_url="https://ignored.example/v1",
        api_mode="chat",
    )

    assert llm.oci_region == OCI_REGION
    assert llm.oci_project_id == OCI_PROJECT_ID
    assert llm.base_url is None
    assert llm.api_mode == "responses"

    cipher = Cipher("oci-profile-encryption-test-key")
    persisted = llm.to_persisted(
        context={"cipher": cipher, "expose_secrets": "encrypted"}
    )
    restored = LLM.from_persisted(persisted, context={"cipher": cipher})
    assert restored.oci_region == OCI_REGION
    assert restored.oci_project_id == OCI_PROJECT_ID
    assert restored.api_key == SecretStr("oci-api-key")
    assert str(persisted["api_key"]).startswith("gAAAAA")


def test_oci_genai_profile_requires_a_model_id() -> None:
    with pytest.raises(ValidationError, match="model ID"):
        LLM(
            model="oci_genai/",
            oci_region=OCI_REGION,
            oci_project_id=OCI_PROJECT_ID,
        )


def test_oci_genai_model_copy_refreshes_region_route() -> None:
    copied = _oci_llm().model_copy(update={"oci_region": "us-ashburn-1"})

    kwargs = copied._build_responses_call_kwargs([], None, None, {})

    assert kwargs["api_base"] == (
        "https://inference.generativeai.us-ashburn-1.oci.oraclecloud.com/openai/v1"
    )


def test_oci_genai_respects_explicit_canonical_model_for_capabilities() -> None:
    llm = _oci_llm(model_canonical_name="openai/gpt-5-mini")

    assert llm._model_name_for_capabilities() == "openai/gpt-5-mini"


@patch("openhands.sdk.llm.llm.litellm_responses")
def test_oci_genai_normal_response_uses_responses_api(mock_responses) -> None:
    mock_responses.return_value = _response_with_text()
    llm = _oci_llm()

    result = llm.responses(
        messages=[Message(role="user", content=[TextContent(text="Say hello")])]
    )

    assert result.message.content == [TextContent(text="Hello from OCI")]
    request = mock_responses.call_args.kwargs
    assert request["model"] == "xai.grok-4.6"
    assert request["custom_llm_provider"] == "openai"
    assert request["api_base"] == OCI_BASE_URL
    assert request["extra_headers"]["openai-project"] == OCI_PROJECT_ID


@patch("openhands.sdk.llm.llm.litellm_responses")
def test_oci_genai_tool_call_uses_responses_api(mock_responses) -> None:
    mock_responses.return_value = _response_with_tool_call()
    llm = _oci_llm()

    result = llm.responses(
        messages=[Message(role="user", content=[TextContent(text="Run pwd")])]
    )

    assert result.message.tool_calls
    assert result.message.tool_calls[0].name == "terminal"
    request = mock_responses.call_args.kwargs
    assert request["model"] == "xai.grok-4.6"
    assert request["api_base"] == OCI_BASE_URL
    assert request["extra_headers"]["openai-project"] == OCI_PROJECT_ID
