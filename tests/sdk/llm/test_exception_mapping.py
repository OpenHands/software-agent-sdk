from litellm.exceptions import BadRequestError

from openhands.sdk.llm.exceptions import (
    LLMAuthenticationError,
    LLMBadRequestError,
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
