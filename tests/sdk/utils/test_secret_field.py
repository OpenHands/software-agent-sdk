"""Behaviour of ``SecretField`` and the typed secret contexts."""

from typing import Annotated

import pytest
from pydantic import BaseModel, SecretStr, ValidationError

from openhands.sdk.utils.cipher import Cipher
from openhands.sdk.utils.pydantic_secrets import (
    REDACTED_SECRET_VALUE,
    MissingCipherError,
    SecretDecryptionError,
    SecretField,
    cipher_context,
    client_context,
)


class Holder(BaseModel):
    key: Annotated[SecretStr | None, SecretField()] = None


class TextHolder(BaseModel):
    session: Annotated[str | None, SecretField()] = None


class MapHolder(BaseModel):
    env: Annotated[dict[str, SecretStr], SecretField()] = {}


class ClientRequest(BaseModel):
    key: Annotated[SecretStr | None, SecretField(from_client=True)] = None


@pytest.fixture
def cipher() -> Cipher:
    return Cipher("a" * 32)


@pytest.fixture
def other_cipher() -> Cipher:
    return Cipher("b" * 32)


def test_cipher_context_round_trips_through_ciphertext(cipher):
    stored = Holder(key=SecretStr("sk-1")).model_dump(
        mode="json", context=cipher_context(cipher)
    )
    assert stored["key"] != "sk-1"
    assert cipher.try_decrypt_str(stored["key"]) == "sk-1"

    loaded = Holder.model_validate(stored, context=cipher_context(cipher))
    assert loaded.key is not None
    assert loaded.key.get_secret_value() == "sk-1"


def test_cipher_context_without_cipher_redacts_on_dump():
    dumped = Holder(key=SecretStr("sk-1")).model_dump(
        mode="json", context=cipher_context(None)
    )
    assert dumped["key"] == REDACTED_SECRET_VALUE


@pytest.mark.parametrize(
    ("mode", "expected"),
    [("redact", REDACTED_SECRET_VALUE), ("plaintext", "sk-1")],
)
def test_client_context_modes(mode, expected):
    dumped = Holder(key=SecretStr("sk-1")).model_dump(
        mode="json", context=client_context(mode)
    )
    assert dumped["key"] == expected


def test_client_context_encrypted_needs_a_cipher(cipher):
    holder = Holder(key=SecretStr("sk-1"))
    encrypted = holder.model_dump(
        mode="json", context=client_context("encrypted", cipher)
    )
    assert cipher.try_decrypt_str(encrypted["key"]) == "sk-1"
    with pytest.raises(Exception) as excinfo:
        holder.model_dump(mode="json", context=client_context("encrypted"))
    assert isinstance(excinfo.value.__cause__ or excinfo.value, MissingCipherError)


@pytest.mark.parametrize("value", ["", "   ", REDACTED_SECRET_VALUE])
def test_unusable_value_reads_as_unset(value):
    assert Holder.model_validate({"key": value}).key is None


def test_undecryptable_token_reads_as_unset_unless_strict(cipher, other_cipher):
    token = other_cipher.encrypt(SecretStr("sk-1"))
    assert (
        Holder.model_validate({"key": token}, context=cipher_context(cipher)).key
        is None
    )
    with pytest.raises(SecretDecryptionError):
        Holder.model_validate(
            {"key": token}, context=cipher_context(cipher, strict=True)
        )


@pytest.mark.parametrize(
    ("undecryptable", "expected"),
    [("keep", "token"), ("raise", ValidationError)],
)
def test_undecryptable_policy(cipher, other_cipher, undecryptable, expected):
    class Model(BaseModel):
        key: Annotated[SecretStr | None, SecretField(undecryptable=undecryptable)]

    token = other_cipher.encrypt(SecretStr("sk-1"))
    if expected is ValidationError:
        with pytest.raises(ValidationError):
            Model.model_validate({"key": token}, context=cipher_context(cipher))
        return
    loaded = Model.model_validate({"key": token}, context=cipher_context(cipher))
    assert loaded.key is not None
    assert loaded.key.get_secret_value() == token


def test_str_typed_field_keeps_str(cipher):
    token = cipher.encrypt(SecretStr("sess-1"))
    loaded = TextHolder.model_validate(
        {"session": token}, context=cipher_context(cipher)
    )
    assert loaded.session == "sess-1"
    assert TextHolder(session="sess-1").model_dump(mode="json")["session"] == (
        REDACTED_SECRET_VALUE
    )


def test_map_field_handles_each_entry(cipher, other_cipher):
    data = {
        "env": {
            "A": cipher.encrypt(SecretStr("a")),
            "B": other_cipher.encrypt(SecretStr("b")),
            "C": "",
            "D": "plain",
        }
    }
    loaded = MapHolder.model_validate(data, context=cipher_context(cipher))
    assert {k: v.get_secret_value() for k, v in loaded.env.items()} == {
        "A": "a",
        "D": "plain",
    }
    dumped = loaded.model_dump(mode="json", context=client_context("plaintext"))
    assert dumped["env"] == {"A": "a", "D": "plain"}


def test_map_field_secret_keys_leaves_other_entries_alone():
    class Headers(BaseModel):
        headers: Annotated[
            dict[str, str], SecretField(secret_keys=lambda k: k == "Authorization")
        ]

    dumped = Headers(headers={"Authorization": "t", "Accept": "json"}).model_dump(
        mode="json"
    )
    assert dumped["headers"] == {
        "Authorization": REDACTED_SECRET_VALUE,
        "Accept": "json",
    }


@pytest.mark.parametrize("value", ["", " \t ", REDACTED_SECRET_VALUE])
def test_from_client_rejects_unusable_values(value):
    with pytest.raises(ValidationError) as excinfo:
        ClientRequest.model_validate({"key": value})
    assert REDACTED_SECRET_VALUE not in str(excinfo.value.errors()[0]["msg"])


def test_from_client_accepts_a_real_value_and_omission():
    assert ClientRequest().key is None
    request = ClientRequest.model_validate({"key": "sk-1"})
    assert request.key is not None
    assert request.key.get_secret_value() == "sk-1"


def test_json_validation_reads_secrets(cipher):
    class Model(BaseModel):
        key: Annotated[str | SecretStr | None, SecretField()] = None
        env: Annotated[dict[str, SecretStr] | None, SecretField()] = None

    payload = Model(key="sk-1", env={"A": SecretStr("a")}).model_dump_json(
        context=cipher_context(cipher)
    )
    loaded = Model.model_validate_json(payload, context=cipher_context(cipher))
    assert isinstance(loaded.key, SecretStr)
    assert loaded.key.get_secret_value() == "sk-1"
    assert loaded.env is not None
    assert loaded.env["A"].get_secret_value() == "a"
