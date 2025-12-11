"""Tests for SecretSources class."""

import pytest

from openhands.sdk.secret import LookupSecret
from openhands.sdk.utils.cipher import Cipher


@pytest.fixture
def lookup_secret():
    return LookupSecret(
        url="https://my-oauth-service.com",
        headers={
            "authorization": "Bearer Token",
            "some-key": "a key",
            "not-sensitive": "hello there",
        },
    )


def test_lookup_secret_serialization_default(lookup_secret):
    """Test LookupSecret serialization"""
    dumped = lookup_secret.model_dump(mode="json")
    expected = {
        "kind": "LookupSecret",
        "description": None,
        "url": "https://my-oauth-service.com",
        "headers": {
            "authorization": "**********",
            "some-key": "**********",
            "not-sensitive": "hello there",
        },
    }
    assert dumped == expected


def test_lookup_secret_serialization_expose_secrets(lookup_secret):
    """Test LookupSecret serialization"""
    dumped = lookup_secret.model_dump(mode="json", context={"expose_secrets": True})
    expected = {
        "kind": "LookupSecret",
        "description": None,
        "url": "https://my-oauth-service.com",
        "headers": {
            "authorization": "Bearer Token",
            "some-key": "a key",
            "not-sensitive": "hello there",
        },
    }
    assert dumped == expected
    validated = LookupSecret.model_validate(dumped)
    assert validated == lookup_secret


def test_lookup_secret_serialization_encrypt(lookup_secret):
    """Test LookupSecret serialization"""
    cipher = Cipher(secret_key="some secret key")
    dumped = lookup_secret.model_dump(mode="json", context={"cipher": cipher})
    validated = LookupSecret.model_validate(dumped, context={"cipher": cipher})
    assert validated == lookup_secret
