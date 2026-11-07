"""Property-based tests for the redaction helpers.

The property is the same for every helper: a secret placed where the helper
promises to look for one never appears in its output.
"""

import json
from urllib.parse import quote

import pytest
from hypothesis import assume, example, given, strategies as st

from openhands.sdk.utils.redact import (
    SECRET_KEY_PATTERNS,
    SENSITIVE_URL_PARAMS,
    redact_text_secrets,
    redact_url_credentials,
    redact_url_credentials_in_text,
    redact_url_params,
    sanitize_dict,
)


# A secret long enough that it cannot appear by accident in the output.
secret = st.text(
    st.characters(codec="utf-8", exclude_categories=("Cs", "Cc")), min_size=8
).filter(lambda s: s.strip() == s and "redacted" not in s)
# Printable ASCII secrets, as API keys and passwords usually are.
ascii_secret = st.text(
    st.characters(min_codepoint=0x21, max_codepoint=0x7E), min_size=8
).filter(lambda s: "redacted" not in s)

_ident = st.text("abcdefghijklmnopqrstuvwxyz_-", max_size=6)


def _key_with(patterns: list[str]) -> st.SearchStrategy[str]:
    """A key name such as ``x-api-key`` or ``db_PASSWORD`` around a pattern."""
    return st.builds(
        lambda pre, pattern, post, upper: (
            (pre + pattern + post).upper() if upper else pre + pattern + post
        ),
        _ident,
        st.sampled_from(patterns),
        _ident,
        st.booleans(),
    )


# The five patterns redact_text_secrets documents.
text_secret_key = _key_with(["key", "secret", "token", "password", "authorization"])
# Every pattern the SDK treats as secret-bearing (``is_secret_key``).
any_secret_key = _key_with(sorted(p.lower() for p in SECRET_KEY_PATTERNS))


def _json_like(leaf: st.SearchStrategy) -> st.SearchStrategy:
    keys = st.text("abcdefghij_", min_size=1, max_size=5)
    return st.recursive(
        leaf,
        lambda children: (
            st.lists(children, max_size=3) | st.dictionaries(keys, children, max_size=3)
        ),
        max_leaves=8,
    )


@given(text_secret_key, ascii_secret.filter(lambda s: not set("\"'\\") & set(s)))
def test_redact_text_secrets_hides_json_entries(key, value):
    assume(value not in key)
    for rendered in (json.dumps({key: value}), str({key: value})):
        assert value not in redact_text_secrets(rendered), rendered


@pytest.mark.xfail(
    strict=True,
    reason=(
        "the lazy value match stops at an escaped quote, so the rest of a "
        "secret holding a quote is left in cleartext"
    ),
)
@given(text_secret_key, ascii_secret)
@example("key", '"0000000')
def test_redact_text_secrets_hides_values_with_quotes(key, value):
    assume(value not in key)
    for rendered in (json.dumps({key: value}), str({key: value})):
        assert value not in redact_text_secrets(rendered), rendered


@pytest.mark.xfail(
    strict=True,
    reason=(
        "redact_text_secrets only knows key/secret/token/password/authorization; "
        "sanitize_dict also redacts cookie, credential and session keys"
    ),
)
@given(any_secret_key, ascii_secret)
@example("cookie", "00000000")
def test_redact_text_secrets_hides_every_key_sanitize_dict_hides(key, value):
    assume(value not in key)
    assert value not in json.dumps(sanitize_dict({key: value}))
    assert value not in redact_text_secrets(json.dumps({key: value}))


@given(
    _json_like(st.none() | st.integers() | st.text(max_size=5)), any_secret_key, secret
)
def test_sanitize_dict_hides_secret_values_at_any_depth(tree, key, value):
    document = {"outer": [tree, {key: value}]}
    assume(value not in json.dumps(tree, ensure_ascii=False))
    assert value not in json.dumps(sanitize_dict(document), ensure_ascii=False)


# RFC 3986 userinfo: unreserved, sub-delims, ":" and percent-encoded bytes.
_userinfo_chars = st.sampled_from(
    list(
        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._~!$&'()*+,;="
    )
)
userinfo_secret = st.lists(
    _userinfo_chars | st.text(min_size=1, max_size=1).map(lambda c: quote(c, safe="")),
    min_size=8,
).map("".join)
host = st.from_regex(r"[a-z0-9]{1,10}(\.[a-z]{2,5}){0,2}", fullmatch=True)
path = st.from_regex(r"(/[A-Za-z0-9._~-]{0,8}){0,3}", fullmatch=True)
scheme = st.sampled_from(["http://", "https://", "HTTPS://"])


@given(
    scheme,
    st.sampled_from(["", "oauth2:", "x-access-token:"]),
    userinfo_secret,
    host,
    path,
)
def test_redact_url_credentials_hides_userinfo(scheme, user, token, host, path):
    url = f"{scheme}{user}{token}@{host}{path}"
    assert token not in redact_url_credentials(url)


@given(
    st.text(max_size=20),
    scheme,
    userinfo_secret,
    host,
    path,
    st.text(max_size=20),
)
def test_redact_url_credentials_in_text_hides_userinfo(
    before, scheme, token, host, path, after
):
    text = f"{before} '{scheme}{token}@{host}{path}' {after}"
    assume(token not in before and token not in after)
    assert token not in redact_url_credentials_in_text(text)


@given(
    host,
    path,
    st.lists(
        st.tuples(st.text("abcdefgh", min_size=1, max_size=5), st.text(max_size=5)),
        max_size=3,
    ),
    st.sampled_from(sorted(SENSITIVE_URL_PARAMS)),
    ascii_secret,
)
def test_redact_url_params_hides_sensitive_query_values(
    host, path, other_params, name, value
):
    params = [*other_params, (name, value)]
    query = "&".join(f"{quote(k)}={quote(v, safe='')}" for k, v in params)
    url = f"https://{host}{path}?{query}"
    public = f"https://{host}{path}?" + "&".join(f"{k}={v}" for k, v in other_params)
    assume(value not in public and quote(value, safe="") not in public + name)
    redacted = redact_url_params(url)
    assert quote(value, safe="") not in redacted
    assert value not in redacted
