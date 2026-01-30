from litellm.exceptions import (
    APIConnectionError,
    BadRequestError,
    InternalServerError,
    ServiceUnavailableError,
)

from openhands.sdk.llm.exceptions import (
    LLMAuthenticationError,
    LLMBadRequestError,
    LLMServiceUnavailableError,
    map_provider_exception,
)


MODEL = "test-model"
PROVIDER = "test-provider"


def test_map_auth_error_from_bad_request():
    e = BadRequestError("Invalid API key provided", MODEL, PROVIDER)
    mapped = map_provider_exception(e)
    assert isinstance(mapped, LLMAuthenticationError)


def test_map_auth_error_from_openai_error():
    # OpenAIError has odd behavior; create a BadRequestError that wraps an
    # auth-like message instead, as providers commonly route auth issues
    # through BadRequestError in LiteLLM
    e = BadRequestError("status 401 Unauthorized: missing API key", MODEL, PROVIDER)
    mapped = map_provider_exception(e)
    assert isinstance(mapped, LLMAuthenticationError)


def test_map_generic_bad_request():
    e = BadRequestError("Some client-side error not related to auth", MODEL, PROVIDER)
    mapped = map_provider_exception(e)
    assert isinstance(mapped, LLMBadRequestError)


def test_passthrough_unknown_exception():
    class MyCustom(Exception):
        pass

    e = MyCustom("random")
    mapped = map_provider_exception(e)
    assert mapped is e


def test_map_internal_server_error_to_service_unavailable():
    """Test that InternalServerError is mapped to LLMServiceUnavailableError.

    This covers the case where an LLM provider returns a 500 error,
    such as 'Error code: 500 - {"error": "Error processing the request"}'.
    """
    e = InternalServerError(
        message="InternalServerError: OpenAIException - Error code: 500 - "
        "{'error': 'Error processing the request'}",
        model=MODEL,
        llm_provider=PROVIDER,
    )
    mapped = map_provider_exception(e)
    assert isinstance(mapped, LLMServiceUnavailableError)
    assert "Error code: 500" in str(mapped)


def test_map_service_unavailable_error():
    """Test that ServiceUnavailableError is mapped to LLMServiceUnavailableError."""
    e = ServiceUnavailableError(
        message="Service temporarily unavailable",
        model=MODEL,
        llm_provider=PROVIDER,
    )
    mapped = map_provider_exception(e)
    assert isinstance(mapped, LLMServiceUnavailableError)


def test_map_api_connection_error():
    """Test that APIConnectionError is mapped to LLMServiceUnavailableError."""
    e = APIConnectionError(
        message="Connection refused",
        model=MODEL,
        llm_provider=PROVIDER,
    )
    mapped = map_provider_exception(e)
    assert isinstance(mapped, LLMServiceUnavailableError)
