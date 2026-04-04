"""Tests for redact utility functions."""

from openhands.sdk.utils.redact import (
    SENSITIVE_URL_PARAMS,
    redact_url_params,
)


# ---------------------------------------------------------------------------
# SENSITIVE_URL_PARAMS constant
# ---------------------------------------------------------------------------


class TestSensitiveUrlParams:
    """Verify the SENSITIVE_URL_PARAMS constant."""

    def test_is_frozenset(self):
        assert isinstance(SENSITIVE_URL_PARAMS, frozenset)

    def test_contains_expected_entries(self):
        expected = {
            "tavilyapikey",
            "apikey",
            "api_key",
            "token",
            "access_token",
            "secret",
            "key",
        }
        assert SENSITIVE_URL_PARAMS == expected


# ---------------------------------------------------------------------------
# redact_url_params
# ---------------------------------------------------------------------------


class TestRedactUrlParams:
    """Tests for redact_url_params()."""

    # -- basic redaction ---------------------------------------------------

    def test_redacts_apikey_param(self):
        url = "https://example.com/search?q=hello&apikey=secret123"
        result = redact_url_params(url)
        assert "secret123" not in result
        assert "apikey=" in result
        assert "q=hello" in result

    def test_redacts_api_key_param(self):
        url = "https://api.example.com/v1/data?api_key=sk-abc123&format=json"
        result = redact_url_params(url)
        assert "sk-abc123" not in result
        assert "format=json" in result

    def test_redacts_token_param(self):
        url = "https://example.com/callback?token=jwt_xyz&state=abc"
        result = redact_url_params(url)
        assert "jwt_xyz" not in result
        assert "state=abc" in result

    def test_redacts_access_token_param(self):
        url = "https://example.com/api?access_token=ghp_xxxx"
        result = redact_url_params(url)
        assert "ghp_xxxx" not in result

    def test_redacts_secret_param(self):
        url = "https://example.com?secret=mysecret&other=value"
        result = redact_url_params(url)
        assert "mysecret" not in result
        assert "other=value" in result

    def test_redacts_key_param(self):
        url = "https://example.com?key=12345"
        result = redact_url_params(url)
        assert "12345" not in result

    def test_redacts_tavilyapikey_param(self):
        url = "https://api.tavily.com/search?tavilyApiKey=tvly-abc123&query=test"
        result = redact_url_params(url)
        assert "tvly-abc123" not in result
        assert "query=test" in result

    # -- case-insensitive matching -----------------------------------------

    def test_case_insensitive_exact_match(self):
        """SENSITIVE_URL_PARAMS matching is case-insensitive."""
        url = "https://example.com?ApiKey=val1&TOKEN=val2&Secret=val3"
        result = redact_url_params(url)
        assert "val1" not in result
        assert "val2" not in result
        assert "val3" not in result

    # -- is_secret_key pattern matching ------------------------------------

    def test_redacts_via_is_secret_key_pattern(self):
        """Params matching SECRET_KEY_PATTERNS via is_secret_key() get redacted."""
        url = "https://example.com?Authorization=Bearer+xyz&page=1"
        result = redact_url_params(url)
        assert "Bearer" not in result
        assert "xyz" not in result
        assert "page=1" in result

    def test_redacts_x_api_key_via_pattern(self):
        """'x-api-key' contains 'KEY' so is_secret_key matches."""
        url = "https://example.com?x-api-key=abc123&limit=10"
        result = redact_url_params(url)
        assert "abc123" not in result
        assert "limit=10" in result

    # -- edge cases --------------------------------------------------------

    def test_no_query_params(self):
        url = "https://example.com/path"
        assert redact_url_params(url) == url

    def test_empty_query_string(self):
        url = "https://example.com/path?"
        # urlparse treats trailing '?' as empty query; should return unchanged
        result = redact_url_params(url)
        assert result == "https://example.com/path?"

    def test_empty_string(self):
        assert redact_url_params("") == ""

    def test_non_url_string(self):
        """Non-URL strings should be returned as-is (no crash)."""
        text = "not a url at all"
        assert redact_url_params(text) == text

    def test_url_with_fragment(self):
        url = "https://example.com/page?apikey=secret#section"
        result = redact_url_params(url)
        assert "secret" not in result
        assert "#section" in result

    def test_url_with_port_and_path(self):
        url = "http://localhost:8080/api/v1?token=abc&debug=true"
        result = redact_url_params(url)
        assert "abc" not in result
        assert "debug=true" in result
        assert "localhost:8080" in result

    def test_preserves_non_sensitive_params(self):
        url = "https://example.com?page=1&limit=50&sort=asc"
        assert redact_url_params(url) == url

    def test_multiple_sensitive_params(self):
        url = "https://example.com?apikey=k1&token=t1&secret=s1&q=hello"
        result = redact_url_params(url)
        assert "k1" not in result
        assert "t1" not in result
        assert "s1" not in result
        assert "q=hello" in result

    def test_param_with_empty_value(self):
        url = "https://example.com?apikey=&other=value"
        result = redact_url_params(url)
        # Even empty values should be replaced with <redacted>
        assert "other=value" in result

    def test_param_with_multiple_values(self):
        """When a param appears multiple times, all values are redacted."""
        url = "https://example.com?token=FIRSTVAL&token=SECONDVAL&page=1"
        result = redact_url_params(url)
        assert "token=" in result
        assert "FIRSTVAL" not in result
        assert "SECONDVAL" not in result
        assert "page=1" in result

    def test_url_with_encoded_characters(self):
        url = "https://example.com/path?q=hello%20world&apikey=secret%20value"
        result = redact_url_params(url)
        assert "secret" not in result
        # The non-sensitive param value should be preserved (possibly re-encoded)
        assert "hello" in result
