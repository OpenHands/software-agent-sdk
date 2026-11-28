"""Persist a validated Pydantic form submission as one owner's sparse values."""

import types
from collections.abc import Iterable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Annotated, Any, Union, get_args, get_origin

from pydantic import BaseModel, Discriminator, SecretStr, TypeAdapter

from openhands.sdk.utils.cipher import Cipher
from openhands.sdk.utils.pydantic_secrets import (
    SecretField,
    cipher_context,
    client_context,
)


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
            if _is_relative(patch):
                raise ValueError(
                    "Relative list records must be read with resolve_operations"
                )
            existing = fields.get(name)
            if isinstance(existing, BaseModel) and isinstance(patch, Mapping):
                field = type(record).model_fields[name]
                discriminator = _field_discriminator(field)
                if isinstance(discriminator, str):
                    same_variant = discriminator not in patch or patch[
                        discriminator
                    ] == dict(existing).get(discriminator)
                elif discriminator is not None and callable(discriminator):
                    try:
                        same_variant = discriminator(existing) == discriminator(
                            {**dict(existing), **patch}
                        )
                    except Exception:
                        same_variant = False
                elif _record_types(field.annotation):
                    # Union/record field without a discoverable discriminator:
                    # recurse only when the patch names no foreign fields.
                    same_variant = set(patch) <= set(type(existing).model_fields)
                else:
                    same_variant = True
                if same_variant:
                    fields[name] = overlay(existing, patch)
                    continue
            fields[name] = patch
        return fields

    return type(parent).model_validate(
        overlay(parent, values), context=cipher_context(cipher, strict=True)
    )


# ---------------------------------------------------------------------------
# Typed generic operations: set / extend / remove / inherit with relative
# replay. A list touched only by Extend/Remove is stored as
# {"$list_ops": {"extend": [...], "remove": [...]}} instead of a pinned list,
# so it replays against whatever the parent provides on a later read.
# ---------------------------------------------------------------------------

LIST_OPS_KEY = "$list_ops"
_FORBIDDEN_SEGMENTS = frozenset({"schema_version"})
_SECRET_SEGMENTS = frozenset({"api_key", "api-key"})
_OP_NAMES = frozenset({"set", "extend", "remove", "inherit"})


@dataclass(frozen=True)
class Set:
    path: tuple[str, ...]
    value: Any


@dataclass(frozen=True)
class Extend:
    path: tuple[str, ...]
    items: tuple[Any, ...]


@dataclass(frozen=True)
class Remove:
    path: tuple[str, ...]
    items: tuple[Any, ...]


@dataclass(frozen=True)
class Inherit:
    path: tuple[str, ...]


Operation = Set | Extend | Remove | Inherit


def _op_name(op: Operation) -> str:
    if isinstance(op, Set):
        return "set"
    if isinstance(op, Extend):
        return "extend"
    if isinstance(op, Remove):
        return "remove"
    return "inherit"


def operation_to_dict(op: Operation) -> dict[str, Any]:
    name = _op_name(op)
    result: dict[str, Any] = {"op": name, "path": list(op.path)}
    if isinstance(op, Set):
        result["value"] = deepcopy(op.value)
    elif isinstance(op, (Extend, Remove)):
        result["items"] = deepcopy(list(op.items))
    return result


def operation_from_dict(data: Mapping[str, Any]) -> Operation:
    if not isinstance(data, Mapping):
        raise ValueError("An operation must be an object")
    name = data.get("op")
    if name not in _OP_NAMES:
        raise ValueError(f"Unknown operation: {name!r}")
    extra = set(data) - {"op", "path", "value", "items"}
    if extra:
        raise ValueError(f"Unknown operation keys: {sorted(extra)}")
    raw_path = data.get("path")
    if not isinstance(raw_path, (list, tuple)) or not raw_path:
        raise ValueError("An operation path must be a non-empty list")
    if not all(isinstance(seg, str) and seg for seg in raw_path):
        raise ValueError("An operation path must contain non-empty strings")
    path = tuple(raw_path)
    if name == "set":
        if "value" not in data or "items" in data:
            raise ValueError("A set operation requires exactly a value")
        return Set(path, deepcopy(data["value"]))
    if name in ("extend", "remove"):
        if "items" not in data or "value" in data:
            raise ValueError(f"A {name} operation requires exactly items")
        items = data["items"]
        if not isinstance(items, (list, tuple)):
            raise ValueError("Operation items must be a list")
        cls = Extend if name == "extend" else Remove
        return cls(path, tuple(deepcopy(list(items))))
    if "value" in data or "items" in data:
        raise ValueError("An inherit operation takes no value or items")
    return Inherit(path)


@dataclass(frozen=True)
class PolicyRule:
    """Deny ``operations`` at ``path`` and every descendant of it."""

    path: tuple[str, ...]
    operations: frozenset[str]


@dataclass(frozen=True)
class SettingsPolicy:
    rules: tuple[PolicyRule, ...] = ()

    def __post_init__(self) -> None:
        for rule in self.rules:
            _check_path(rule.path)
            bad = set(rule.operations) - _OP_NAMES
            if bad or not rule.operations:
                raise ValueError(f"Unknown policy operations: {sorted(bad)}")

    def check(self, op: Operation) -> None:
        name = _op_name(op)
        for rule in self.rules:
            denied = name in rule.operations
            at_or_below = op.path[: len(rule.path)] == rule.path
            # An ancestor write replaces the subtree, so it must not smuggle
            # a denied descendant; an ancestor inherit removes it likewise.
            above = rule.path[: len(op.path)] == op.path
            if denied and (at_or_below or above):
                raise PolicyDenied(op.path, name)

    def check_all(self, operations: Iterable[Operation]) -> None:
        for op in operations:
            self.check(op)


class PolicyDenied(ValueError):
    def __init__(self, path: tuple[str, ...], operation: str) -> None:
        self.path = tuple(path)
        self.operation = operation
        super().__init__(
            f"Operation {operation!r} is denied at path {list(self.path)!r}"
        )


def _check_path(path: Any) -> tuple[str, ...]:
    if not isinstance(path, tuple) or not path:
        raise ValueError("A settings operation must name a non-empty path")
    for seg in path:
        if not isinstance(seg, str) or not seg:
            raise ValueError("Path segments must be non-empty strings")
        if seg in _FORBIDDEN_SEGMENTS:
            raise ValueError(f"Path segment is not writable: {seg}")
        if seg.isdigit():
            raise ValueError("List indices are not valid path segments")
    return path


def _field_discriminator(field: Any) -> str | Any | None:
    disc = field.discriminator
    if disc is not None:
        return disc.discriminator if isinstance(disc, Discriminator) else disc

    def search(tp: Any) -> Any | None:
        if isinstance(tp, Discriminator):
            return tp.discriminator
        for arg in get_args(tp):
            found = search(arg)
            if found is not None:
                return found
        return None

    return search(field.annotation)


def _type_members(tp: Any) -> list[Any]:
    origin = get_origin(tp)
    if origin is Annotated:
        return _type_members(get_args(tp)[0])
    if origin is Union or origin is types.UnionType:
        return [m for arg in get_args(tp) for m in _type_members(arg)]
    return [tp]


def _is_secret_annotation(tp: Any) -> bool:
    return any(
        member is SecretStr for member in _type_members(tp)
    ) or _mentions_secret_field(tp)


def _mentions_secret_field(tp: Any) -> bool:
    if isinstance(tp, SecretField):
        return True
    return any(_mentions_secret_field(arg) for arg in get_args(tp))


def _field_is_secret(model_type: type[BaseModel], name: str) -> bool:
    field = model_type.model_fields[name]
    if _is_secret_annotation(field.annotation):
        return True
    return any(isinstance(meta, SecretField) for meta in field.metadata)


def _list_element_type(tp: Any) -> Any | None:
    for member in _type_members(tp):
        if member is type(None):
            continue
        origin = get_origin(member)
        if origin in (list, Sequence) or (
            isinstance(member, type)
            and issubclass(member, Sequence)
            and member is not str
        ):
            args = get_args(member)
            return args[0] if args else Any
    return None


def _record_types(tp: Any) -> list[type[BaseModel]]:
    return [
        m for m in _type_members(tp) if isinstance(m, type) and issubclass(m, BaseModel)
    ]


def _resolve_field(model_type: type[BaseModel], name: str) -> tuple[Any, bool]:
    field = model_type.model_fields.get(name)
    if field is None or field.exclude:
        raise ValueError(f"Not a persistable field: {name}")
    return field, _field_is_secret(model_type, name)


def _walk_schema(
    root: type[BaseModel],
    path: tuple[str, ...],
    effective: BaseModel | None = None,
) -> Any:
    """Return the leaf annotation, rejecting unsafe traversal."""
    current: Any = root
    current_model: BaseModel | None = effective
    for depth, seg in enumerate(path):
        records = _record_types(current)
        if len(records) == 1:
            model_type = records[0]
        elif current_model is not None:
            model_type = type(current_model)
        else:
            raise ValueError(f"Path does not address a single record at: {seg}")
        field, secret = _resolve_field(model_type, seg)
        if secret and depth < len(path) - 1:
            raise ValueError("Cannot traverse through a secret field")
        if seg in _SECRET_SEGMENTS and depth < len(path) - 1:
            raise ValueError("Cannot traverse through a credential field")
        current = field.annotation
        if current_model is not None:
            child = dict(current_model).get(seg)
            current_model = child if isinstance(child, BaseModel) else None
    return current


def _validate_op_schema(
    root: type[BaseModel], op: Operation, effective: BaseModel | None = None
) -> None:
    if not op.path:
        raise ValueError("Operation path must not be empty")
    leaf_name = op.path[-1]
    leaf = _walk_schema(root, op.path, effective)
    if len(op.path) > 1:
        parent_type = (
            type(_navigate(effective, op.path[:-1])) if effective is not None else None
        )
        parent_records = (
            [parent_type]
            if parent_type is not None
            else _record_types(_walk_schema(root, op.path[:-1], effective))
        )
    else:
        parent_records = [root]
    leaf_secret = (
        _field_is_secret(parent_records[0], leaf_name)
        if len(parent_records) == 1 and parent_records[0] is not None
        else False
    ) or _is_secret_annotation(leaf)
    if isinstance(op, (Extend, Remove)):
        if leaf_secret or leaf_name in _SECRET_SEGMENTS:
            raise ValueError("List operations are not allowed on secret fields")
        if _list_element_type(leaf) is None:
            raise ValueError(f"Not a list field: {leaf_name}")
    elif isinstance(op, Set):
        if leaf_name in _SECRET_SEGMENTS and not leaf_secret:
            # A credential-named leaf is owned by dedicated flows, not generic ops.
            raise ValueError("Credential paths are not writable here")
    elif isinstance(op, Inherit) and leaf_secret:
        raise ValueError("Secret fields cannot be reset via generic operations")


def _json_value(model: BaseModel | Any, cipher: Cipher | None) -> Any:
    if isinstance(model, BaseModel):
        return model.model_dump(
            mode="json", context=client_context("encrypted", cipher)
        )
    if isinstance(model, list):
        return [_json_value(item, cipher) for item in model]
    if isinstance(model, dict):
        return {key: _json_value(value, cipher) for key, value in model.items()}
    return deepcopy(model)


def _is_relative(value: Any) -> bool:
    return isinstance(value, Mapping) and set(value) == {LIST_OPS_KEY}


def _relative_parts(value: Mapping[str, Any]) -> tuple[list[Any], list[Any]]:
    record = value[LIST_OPS_KEY]
    if not isinstance(record, Mapping) or set(record) - {"extend", "remove"}:
        raise ValueError("Malformed relative list record")
    extend, remove = record.get("extend", []), record.get("remove", [])
    if not isinstance(extend, list) or not isinstance(remove, list):
        raise ValueError("Malformed relative list record")
    return deepcopy(extend), deepcopy(remove)


def _contains(items: Iterable[Any], needle: Any) -> bool:
    return any(item == needle for item in items)


def _dedup(items: Iterable[Any]) -> list[Any]:
    out: list[Any] = []
    for item in items:
        if not _contains(out, item):
            out.append(deepcopy(item))
    return out


def _replay_list(
    parent_list: list[Any], extend: list[Any], remove: list[Any]
) -> list[Any]:
    result = [deepcopy(x) for x in parent_list if not _contains(remove, x)]
    for item in extend:
        if not _contains(result, item):
            result.append(deepcopy(item))
    return result


def _navigate(model: BaseModel, path: tuple[str, ...]) -> Any:
    current: Any = model
    for seg in path:
        current = dict(current)[seg]
    return current


def _navigate_parent(model: BaseModel, path: tuple[str, ...]) -> BaseModel:
    return _navigate(model, path[:-1]) if len(path) > 1 else model


def _set_typed(model: BaseModel, path: tuple[str, ...], value: Any) -> dict[str, Any]:
    """Return a raw field dict for ``model`` with the leaf at ``path`` replaced."""
    fields = dict(model)
    if len(path) == 1:
        fields[path[0]] = value
        return fields
    child = fields[path[0]]
    if not isinstance(child, BaseModel):
        raise ValueError(f"Path does not address a record at: {path[0]}")
    fields[path[0]] = _set_typed(child, path[1:], value)
    return fields


def _plain_owned(owned: Mapping[str, Any]) -> dict[str, Any]:
    """Strip relative records to their replay over an empty base."""
    result: dict[str, Any] = {}
    for key, value in owned.items():
        if _is_relative(value):
            extend, _remove = _relative_parts(value)
            result[key] = extend
        elif isinstance(value, Mapping):
            result[key] = _plain_owned(value)
        else:
            result[key] = deepcopy(value)
    return result


def resolve_operations[M: BaseModel](
    parent: M, owned: Mapping[str, Any], *, cipher: Cipher | None = None
) -> M:
    """Effective model from ``parent`` plus a sparse owned document.

    Identical to :func:`overlay_model` for plain documents; relative list
    records replay against the parent's current list at the addressed leaf.
    """
    plain = _plain_owned(owned)
    # overlay_model would use absolute lists; correct relative leaves after.
    result = overlay_model(parent, plain, cipher=cipher)
    relatives: list[tuple[tuple[str, ...], list[Any], list[Any]]] = []

    def collect(node: Mapping[str, Any], prefix: tuple[str, ...]) -> None:
        for key, value in node.items():
            if _is_relative(value):
                extend, remove = _relative_parts(value)
                relatives.append((prefix + (key,), extend, remove))
            elif isinstance(value, Mapping):
                collect(value, prefix + (key,))

    collect(dict(owned), ())
    for path, extend, remove in relatives:
        parent_list = _json_value(_navigate(parent, path), cipher)
        if parent_list is None:
            parent_list = []
        if not isinstance(parent_list, list):
            raise ValueError(f"Relative record on a non-list field: {path[-1]}")
        effective = _replay_list(parent_list, extend, remove)
        raw = _set_typed(result, path, effective)
        result = type(parent).model_validate(
            raw, context=cipher_context(cipher, strict=True)
        )
    return result


def _owned_at(owned: Mapping[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = owned
    for seg in path:
        if not isinstance(current, Mapping) or seg not in current:
            return None
        current = current[seg]
    return current


def _drop_owned(owned: dict[str, Any], path: tuple[str, ...]) -> None:
    if len(path) == 1:
        owned.pop(path[0], None)
        return
    child = owned.get(path[0])
    if isinstance(child, dict):
        _drop_owned(child, path[1:])
        if not child:
            owned.pop(path[0], None)


def _put_owned(owned: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    node = owned
    for seg in path[:-1]:
        child = node.get(seg)
        if not isinstance(child, dict) or _is_relative(child):
            child = {}
            node[seg] = child
        node = child
    node[path[-1]] = value


def apply_operations(
    parent: BaseModel,
    owned: Mapping[str, Any],
    operations: Iterable[Operation],
    *,
    cipher: Cipher | None = None,
    policy: SettingsPolicy | None = None,
) -> dict[str, Any]:
    """Apply typed operations and return the new sparse owned document.

    Relative list intent is persisted (see module contract); everything else
    follows :func:`reconcile_model` ownership: a value equal to the parent's
    is not pinned, and an unchanged secret keeps its ciphertext. The composed
    model is validated as a whole; any error leaves the caller with no new
    document to persist. Inputs are not modified.
    """
    ops = list(operations)
    for op in ops:
        if not isinstance(op, (Set, Extend, Remove, Inherit)):
            raise ValueError(f"Not a settings operation: {op!r}")
        _check_path(op.path)
        if isinstance(op, (Extend, Remove)) and not op.items:
            raise ValueError("List operations require at least one item")
    if policy is not None:
        policy.check_all(ops)

    before = resolve_operations(parent, owned, cipher=cipher)
    for op in ops:
        _validate_op_schema(type(parent), op, before)
    result_owned: dict[str, Any] = deepcopy(dict(owned))
    after = before
    # Typed item values per list path, accumulated across ops in order.
    for op in ops:
        if isinstance(op, Inherit):
            _drop_owned(result_owned, op.path)
            continue
        leaf_parent = _navigate_parent(after, op.path)
        if isinstance(op, Set):
            raw = _set_typed(after, op.path, deepcopy(op.value))
            after = type(parent).model_validate(
                raw, context=cipher_context(cipher, strict=True)
            )
            continue
        current = _navigate(after, op.path)
        if current is None:
            current = []
        if not isinstance(current, list):
            raise ValueError(f"Not a list field: {op.path[-1]}")
        current_json = [
            _json_value(item, cipher) if isinstance(item, BaseModel) else deepcopy(item)
            for item in current
        ]
        elem = _list_element_type(
            type(leaf_parent).model_fields[op.path[-1]].annotation
        )
        typed_items = [
            TypeAdapter(elem).validate_python(
                deepcopy(item), context=cipher_context(cipher, strict=True)
            )
            if elem is not None and elem is not Any
            else deepcopy(item)
            for item in op.items
        ]
        item_json = [
            _json_value(item, cipher) if isinstance(item, BaseModel) else deepcopy(item)
            for item in typed_items
        ]
        if isinstance(op, Extend):
            new_list = list(current_json)
            for item in item_json:
                if not _contains(new_list, item):
                    new_list.append(deepcopy(item))
        else:
            new_list = [x for x in current_json if not _contains(item_json, x)]
        raw = _set_typed(after, op.path, new_list)
        after = type(parent).model_validate(
            raw, context=cipher_context(cipher, strict=True)
        )

    # Persist: relative records for list paths touched by Extend/Remove,
    # reconcile semantics for every other touched path.
    list_paths = [op.path for op in ops if isinstance(op, (Extend, Remove))]
    for idx, op in enumerate(ops):
        if not isinstance(op, Set):
            continue
        path = op.path
        # When a list op follows this Set at/below it, the final persistence
        # for that subtree is derived from the typed after-model below.
        if any(
            isinstance(o, (Extend, Remove))
            and (o.path == path or o.path[: len(path)] == path)
            and j > idx
            for j, o in enumerate(ops)
        ):
            continue
        parent_value = _navigate(parent, path)
        after_value = _navigate(after, path)
        if after_value == parent_value:
            _drop_owned(result_owned, path)
        elif isinstance(after_value, BaseModel) and isinstance(parent_value, BaseModel):
            submitted = (
                op.value
                if isinstance(op.value, Mapping)
                else _submitted_shape(after_value, parent_value)
            )
            before_value = _navigate(before, path)
            if type(before_value) is type(after_value) is type(parent_value):
                child = reconcile_model(
                    parent_value,
                    before_value,
                    after_value,
                    _mapping_at(result_owned, path),
                    submitted,
                    cipher=cipher,
                )
            elif isinstance(submitted, Mapping):
                # Variant switch: keep only the explicitly submitted shape.
                child = after_value.model_dump(
                    mode="json",
                    include=set(submitted),
                    context=client_context("encrypted", cipher),
                )
            else:
                child = after_value.model_dump(
                    mode="json", context=client_context("encrypted", cipher)
                )
            if child:
                _put_owned(result_owned, path, child)
            else:
                _drop_owned(result_owned, path)
        else:
            holder = _navigate_parent(after, path)
            dumped = holder.model_dump(
                mode="json",
                include={path[-1]},
                context=client_context("encrypted", cipher),
            )
            _put_owned(result_owned, path, dumped[path[-1]])
    set_indices = [i for i, o in enumerate(ops) if isinstance(o, Set)]
    for path in dict.fromkeys(list_paths):
        relevant_sets = [
            i
            for i in set_indices
            if ops[i].path == path or path[: len(ops[i].path)] == ops[i].path  # type: ignore[attr-defined]
        ]
        if relevant_sets and not any(
            isinstance(o, (Extend, Remove))
            and o.path == path
            and idx > max(relevant_sets)
            for idx, o in enumerate(ops)
        ):
            continue
        parent_list = _json_value(_navigate(parent, path), cipher)
        if parent_list is None:
            parent_list = []
        if not isinstance(parent_list, list):
            raise ValueError(f"Not a list field: {path[-1]}")
        # Replay the ops for this path, in order, over the before-state record.
        # If a Set at/above the path precedes the first list op, that Set's
        # typed value (in `before`-at-that-moment terms) is the new absolute
        # base: derive relative intent from the final typed list instead.
        last_set = max(relevant_sets) if relevant_sets else -1
        first_list_op = min(
            idx
            for idx, o in enumerate(ops)
            if isinstance(o, (Extend, Remove)) and o.path == path
        )
        if relevant_sets and last_set < first_list_op:
            final_list = _json_value(_navigate(after, path), cipher) or []
            extend = [x for x in final_list if not _contains(parent_list, x)]
            remove = [x for x in parent_list if not _contains(final_list, x)]
            extend, remove = _dedup(extend), _dedup(remove)
            if _replay_list(parent_list, extend, remove) == parent_list:
                _drop_owned(result_owned, path)
            else:
                _put_owned(
                    result_owned,
                    path,
                    {LIST_OPS_KEY: {"extend": extend, "remove": remove}},
                )
            continue
        existing = _owned_at(result_owned, path)
        if _is_relative(existing):
            extend, remove = _relative_parts(existing)
        elif isinstance(existing, list):
            # Convert an absolute pin into relative intent against the parent
            # as it was when this call started, preserving its meaning.
            extend = [x for x in existing if not _contains(parent_list, x)]
            remove = [x for x in parent_list if not _contains(existing, x)]
        else:
            extend, remove = [], []
        for op in ops:
            if not isinstance(op, (Extend, Remove)) or op.path != path:
                continue
            items = [
                _json_value(item, cipher)
                if isinstance(item, BaseModel)
                else deepcopy(item)
                for item in op.items
            ]
            # Normalise typed model items through the element schema.
            elem = _list_element_type(
                type(_navigate_parent(before, path)).model_fields[path[-1]].annotation
            )
            if elem is not None and elem is not Any:
                items = [
                    _json_value(
                        TypeAdapter(elem).validate_python(
                            item, context=cipher_context(cipher, strict=True)
                        ),
                        cipher,
                    )
                    for item in items
                ]
            if isinstance(op, Extend):
                remove = [x for x in remove if not _contains(items, x)]
                for item in items:
                    if not _contains(extend, item) and not (
                        _contains(parent_list, item) and not _contains(remove, item)
                    ):
                        extend.append(deepcopy(item))
            else:
                extend = [x for x in extend if not _contains(items, x)]
                for item in items:
                    if _contains(parent_list, item) and not _contains(remove, item):
                        remove.append(deepcopy(item))
        extend, remove = _dedup(extend), _dedup(remove)
        effective = _replay_list(parent_list, extend, remove)
        if effective == parent_list:
            _drop_owned(result_owned, path)
        else:
            _put_owned(
                result_owned,
                path,
                {LIST_OPS_KEY: {"extend": extend, "remove": remove}},
            )
    # A Set that replaces an ancestor discards relative records below it that
    # the set already accounts for in the typed after-model.
    for op in ops:
        if isinstance(op, Set):
            existing = _owned_at(result_owned, op.path)
            if isinstance(existing, dict):
                _strip_relatives(existing)
    return result_owned


def _submitted_shape(after: BaseModel, parent: BaseModel) -> dict[str, Any]:
    after_fields, parent_fields = dict(after), dict(parent)
    return {
        name: True
        for name in type(after).model_fields
        if after_fields[name] != parent_fields[name]
    }


def _mapping_at(owned: Mapping[str, Any], path: tuple[str, ...]) -> dict[str, Any]:
    value = _owned_at(owned, path)
    return dict(value) if isinstance(value, Mapping) and not _is_relative(value) else {}


def _strip_relatives(node: dict[str, Any]) -> None:
    for key in list(node):
        value = node[key]
        if _is_relative(value):
            extend, _remove = _relative_parts(value)
            node[key] = extend
        elif isinstance(value, dict):
            _strip_relatives(value)
