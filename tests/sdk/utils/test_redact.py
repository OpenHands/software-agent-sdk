"""Tests for redact utility functions."""

from openhands.sdk.utils.redact import (
    SENSITIVE_URL_PARAMS,
    redact_url_credentials,
    redact_url_credentials_in_text,
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


# ---------------------------------------------------------------------------
# redact_url_credentials_in_text
# ---------------------------------------------------------------------------


class TestRedactUrlCredentialsInText:
    """Tests for redact_url_credentials_in_text() (substring-capable)."""

    def test_redacts_credentials_embedded_in_larger_string(self):
        """The key limitation of the anchored helper: creds inside a message."""
        s = "fatal: unable to access 'https://oauth2:SECRET@github.com/o/r.git/': 403"
        result = redact_url_credentials_in_text(s)
        assert "SECRET" not in result
        assert "oauth2" not in result
        assert result == (
            "fatal: unable to access 'https://****@github.com/o/r.git/': 403"
        )

    def test_redacts_token_only_credential(self):
        s = "Cloning https://ghp_supersecrettoken@github.com/o/r.git failed"
        result = redact_url_credentials_in_text(s)
        assert "ghp_supersecrettoken" not in result
        assert "https://****@github.com/o/r.git" in result

    def test_redacts_http_scheme(self):
        s = "warn http://user:pw@internal.example.com/x done"
        result = redact_url_credentials_in_text(s)
        assert "user:pw" not in result
        assert "http://****@internal.example.com/x" in result

    def test_redacts_multiple_embedded_urls(self):
        s = "a https://t1@github.com/o/r.git b https://user:t2@gitlab.com/o/r.git c"
        result = redact_url_credentials_in_text(s)
        assert "t1" not in result
        assert "t2" not in result
        assert result.count("****@") == 2

    def test_redacts_url_encoded_credentials(self):
        s = "url 'https://user%40domain:p%40ss@github.com/repo.git'"
        result = redact_url_credentials_in_text(s)
        assert "user%40domain" not in result
        assert "p%40ss" not in result
        assert "https://****@github.com/repo.git" in result

    def test_leaves_credential_free_url_untouched(self):
        s = "Cloning https://github.com/owner/repo.git into ./repo"
        assert redact_url_credentials_in_text(s) == s

    def test_does_not_match_at_sign_in_path(self):
        """An '@' after a path segment is not userinfo and must be left alone."""
        s = "see https://github.com/owner/repo/blob/main/x@v1.txt"
        assert redact_url_credentials_in_text(s) == s

    def test_leaves_ssh_url_untouched(self):
        s = "remote git@github.com:owner/repo.git fetched"
        assert redact_url_credentials_in_text(s) == s

    def test_empty_string(self):
        assert redact_url_credentials_in_text("") == ""

    def test_no_url_string(self):
        assert redact_url_credentials_in_text("nothing to redact here") == (
            "nothing to redact here"
        )

    def test_matches_whole_url_like_anchored_helper(self):
        """For a bare whole-URL string both helpers agree."""
        url = "https://oauth2:SECRET@gitlab.com/org/repo.git"
        assert redact_url_credentials_in_text(url) == redact_url_credentials(url)
