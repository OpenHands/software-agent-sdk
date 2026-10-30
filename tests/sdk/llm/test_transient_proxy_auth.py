"""Regression tests for transient LiteLLM proxy auth 401s (#18951).

The LiteLLM proxy emits typed AuthenticationError on a cache miss
("Unable to find token in cache or `LiteLLM_VerificationTokenTable`") even
though the token is valid and the same call succeeds on retry. These must be
classified as transient (retryable), not fatal auth errors.
"""

from litellm.exceptions import AuthenticationError

from openhands.sdk.llm.exceptions import (
    is_transient_proxy_auth_error,
    looks_like_auth_error,
    map_provider_exception,
)


MODEL = "test-model"
PROVIDER = "test-provider"

TRANSIENT_MESSAGE = (
    "AuthenticationError: Litellm_proxyException - Authentication Error, "
    "Invalid proxy server token passed. Received API Key = sk-...8fe4, "
    "Key Hash (Token) = b991ad5c550d. "
    "Unable to find token in cache or `LiteLLM_VerIFICATIONTokenTable`"
)

FATAL_MESSAGE = "AuthenticationError: Invalid API key provided"


def test_transient_proxy_cache_miss_detected():
    e = AuthenticationError(TRANSIENT_MESSAGE, MODEL, PROVIDER)
    assert is_transient_proxy_auth_error(e) is True


def test_fatal_auth_not_flagged_transient():
    e = AuthenticationError(FATAL_MESSAGE, MODEL, PROVIDER)
    assert is_transient_proxy_auth_error(e) is False


def test_typed_auth_error_with_proxy_pattern_not_fatal():
    """Typed 401 with proxy cache-miss text must not look like fatal auth."""
    e = AuthenticationError(TRANSIENT_MESSAGE, MODEL, PROVIDER)
    assert looks_like_auth_error(e) is False


def test_typed_auth_error_without_proxy_pattern_still_fatal():
    e = AuthenticationError(FATAL_MESSAGE, MODEL, PROVIDER)
    assert looks_like_auth_error(e) is True


def test_mapping_passes_proxy_miss_through_unmapped():
    """map_provider_exception leaves the proxy cache-miss unmapped so the
    retry loop's is_transient_proxy_auth_error can pick it up."""
    e = AuthenticationError(TRANSIENT_MESSAGE, MODEL, PROVIDER)
    mapped = map_provider_exception(e)
    # Not wrapped into the fatal LLMAuthenticationError
    assert not isinstance(mapped, __import__(
        "openhands.sdk.llm.exceptions", fromlist=["LLMAuthenticationError"]
    ).LLMAuthenticationError)
