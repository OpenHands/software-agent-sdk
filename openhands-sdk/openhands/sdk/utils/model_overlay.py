"""Persist a validated Pydantic form submission as one owner's sparse values."""

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from pydantic import BaseModel

from openhands.sdk.utils.cipher import Cipher
from openhands.sdk.utils.pydantic_secrets import client_context


def reconcile_model(
    parent: BaseModel,
    before: BaseModel,
    after: BaseModel,
    owned: Mapping[str, Any],
    submitted: Mapping[str, Any],
    *,
    cipher: Cipher | None = None,
) -> dict[str, Any]:
    """Return the new local JSON; inputs are not modified.

    Models are authorized, validated effective views. The submitted shape selects
    fields, never values: comparisons use typed attributes and persistence uses
    Pydantic's JSON serializers. Lists and maps are replaced as units. Nested
    records recurse only within the same concrete variant. Unchanged secrets
    retain their original ciphertext. A changed SDK secret requires a cipher.
    """
    if not (type(before) is type(after) is type(parent)):
        raise ValueError("Expected models of the same type at the root")
    result = deepcopy(dict(owned))
    previous, current, defaults = (dict(model) for model in (before, after, parent))
    for name, shape in submitted.items():
        field = type(after).model_fields.get(name)
        if field is None or field.exclude:
            raise ValueError(f"Not a persistable field: {name}")
        old, new, inherited = previous[name], current[name], defaults[name]
        if (
            isinstance(new, BaseModel)
            and isinstance(shape, Mapping)
            and type(old) is type(new) is type(inherited)
        ):
            child = reconcile_model(
                inherited, old, new, owned.get(name, {}), shape, cipher=cipher
            )
            if child:
                result[name] = child
            elif child != owned.get(name, {}):
                result.pop(name, None)
        elif new != old:
            if new == inherited:
                result.pop(name, None)
            else:
                result[name] = after.model_dump(
                    mode="json",
                    include={name},
                    context=client_context("encrypted", cipher),
                )[name]
    return result
