"""Field metadata used by secret masking."""

from collections.abc import Mapping
from typing import Annotated, Any, Literal, get_args, get_origin

from pydantic.fields import FieldInfo


class SkipSecretMasking:
    """Mark a field whose value must not be secret-masked."""

    __slots__ = ()


class PreserveDataUrls:
    """Mark string values that contain opaque base64 data URLs."""

    __slots__ = ()


def field_has_marker(field: FieldInfo, marker: type) -> bool:
    """Return whether ``marker`` annotates ``field`` or a part of its type.

    Pydantic moves ``Annotated`` metadata to ``field.metadata`` only for a bare
    ``Annotated[...]``. For ``EventID | None`` or ``set[EventID]`` the marker
    stays in ``field.annotation``, so this also walks the type arguments.
    A generic type counts only when every argument carries the marker, so text
    next to an id (``EventID | list[str]``) is still masked.
    """
    return any(isinstance(item, marker) for item in field.metadata) or (
        _type_has_marker(field.annotation, marker)
    )


def _type_has_marker(annotation: Any, marker: type) -> bool:
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin is Annotated:
        base, *metadata = args
        return any(isinstance(item, marker) for item in metadata) or (
            _type_has_marker(base, marker)
        )
    if isinstance(origin, type) and issubclass(origin, Mapping):
        # Masking never touches dict keys, so only the value type counts.
        args = args[1:]
    args = tuple(arg for arg in args if arg not in (type(None), Ellipsis))
    return bool(args) and all(_type_has_marker(arg, marker) for arg in args)


def field_is_literal(field: FieldInfo) -> bool:
    """Return whether ``field`` only holds ``Literal`` values.

    Like an ``Enum``, a ``Literal`` has a fixed vocabulary, so it cannot hold a
    secret, and a masked value fails validation when the model is loaded back.
    ``Literal[...] | None`` and containers of ``Literal`` count as well.
    """
    return _type_is_literal(field.annotation)


def _type_is_literal(annotation: Any) -> bool:
    origin = get_origin(annotation)
    if origin is Literal:
        return True
    args = get_args(annotation)
    if origin is Annotated:
        return _type_is_literal(args[0])
    if isinstance(origin, type) and issubclass(origin, Mapping):
        args = args[1:]
    args = tuple(arg for arg in args if arg not in (type(None), Ellipsis))
    return bool(args) and all(_type_is_literal(arg) for arg in args)
