from typing import Annotated

import pytest
from pydantic import BaseModel, Field, SecretStr
from pydantic_core import PydanticSerializationError

from openhands.sdk.utils.cipher import Cipher
from openhands.sdk.utils.model_overlay import reconcile_model
from openhands.sdk.utils.pydantic_secrets import SecretField, cipher_context


class Options(BaseModel):
    limit: int = 80
    note: str | None = None
    catalog: dict[str, int] = Field(default_factory=dict)


class Form(BaseModel):
    options: Options = Field(default_factory=Options)
    secret: Annotated[SecretStr | None, SecretField()] = None


def test_sparse_submission_changes_only_edited_fields():
    parent = Form(options=Options(note="parent", catalog={"x": 1}))
    changed = Form(options=Options(limit=40, catalog={}))
    submitted = {"options": {"limit": 40, "note": None, "catalog": {}}}
    local = reconcile_model(parent, parent, changed, {}, submitted)
    assert local == submitted
    assert reconcile_model(parent, changed, parent, local, submitted) == {}
    assert reconcile_model(parent, parent, parent, {}, submitted) == {}
    pin = {"options": {"limit": 80}}
    assert reconcile_model(parent, parent, parent, pin, submitted) == pin


def test_secret_uses_sdk_serializer_and_noop_preserves_ciphertext():
    empty = Form()
    changed = Form(secret=SecretStr("synthetic test value"))
    with pytest.raises(PydanticSerializationError):
        reconcile_model(empty, empty, changed, {}, {"secret": True})
    cipher = Cipher("synthetic-test-key")
    stored = reconcile_model(empty, empty, changed, {}, {"secret": True}, cipher=cipher)
    assert changed.secret is not None
    assert stored["secret"] != changed.secret.get_secret_value()
    assert (
        Form.model_validate(stored, context=cipher_context(cipher, strict=True))
        == changed
    )
    assert reconcile_model(empty, changed, changed, stored, {"secret": True}) == stored
