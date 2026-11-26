"""Persist a validated Pydantic form submission as one owner's sparse values."""

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from pydantic import BaseModel, Discriminator

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
            elif isinstance(new, BaseModel) and isinstance(shape, Mapping):
                # A different concrete variant replaces the outgoing record.
                # Keep its explicit shape, not a materialization of SDK defaults.
                keys = set(shape)
                if type(old) is type(new) and isinstance(owned.get(name), Mapping):
                    keys.update(owned[name])
                result[name] = new.model_dump(
                    mode="json",
                    include=keys,
                    context=client_context("encrypted", cipher),
                )
            else:
                result[name] = after.model_dump(
                    mode="json",
                    include={name},
                    context=client_context("encrypted", cipher),
                )[name]
    return result


def overlay_model[M: BaseModel](
    parent: M, values: Mapping[str, Any], *, cipher: Cipher | None = None
) -> M:
    """Validate one sparse JSON layer over a parent, treating maps as values.

    Inspect raw typed fields rather than JSON-dumping the inherited model: the
    latter can mask or re-encrypt credentials that the caller never edited.
    """
    from openhands.sdk.utils.pydantic_secrets import cipher_context

    def overlay(record: BaseModel, layer: Mapping[str, Any]) -> dict[str, Any]:
        fields = dict(record)
        for name, patch in layer.items():
            if name not in type(record).model_fields:
                raise ValueError(f"Unknown model field: {name}")
            existing = fields.get(name)
            if isinstance(existing, BaseModel) and isinstance(patch, Mapping):
                discriminator = type(record).model_fields[name].discriminator
                same_variant = not isinstance(discriminator, str) or (
                    discriminator not in patch
                    or patch[discriminator] == dict(existing).get(discriminator)
                )
                if isinstance(discriminator, Discriminator) and callable(
                    discriminator.discriminator
                ):
                    tag = discriminator.discriminator
                    same_variant = tag(existing) == tag({**dict(existing), **patch})
                if same_variant:
                    fields[name] = overlay(existing, patch)
                    continue
            fields[name] = patch
        return fields

    return type(parent).model_validate(
        overlay(parent, values), context=cipher_context(cipher, strict=True)
    )
