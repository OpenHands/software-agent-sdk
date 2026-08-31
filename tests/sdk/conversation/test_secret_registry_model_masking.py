"""Unit tests for SecretRegistry.mask_secrets_in_model (issue #4677)."""

from enum import Enum
from typing import NamedTuple

from pydantic import BaseModel, SecretStr

from openhands.sdk.conversation.secret_registry import SecretRegistry


SECRET = "sk-supersecret-value"
MASK = "<secret-hidden>"


class Kind(str, Enum):
    ADD = "add"


class Span(NamedTuple):
    label: str
    line: int


class Inner(BaseModel):
    note: str
    kind: Kind = Kind.ADD


class Outer(BaseModel):
    title: str
    inner: Inner
    items: list[str] = []
    mapping: dict[str, str] = {}
    count: int = 0
    blob: bytes = b""
    opaque: SecretStr | None = None
    tags: set[str] = set()
    span: Span | None = None


def _registry() -> SecretRegistry:
    registry = SecretRegistry()
    registry.update_secrets({"TOKEN": SECRET})
    registry.get_secret_value("TOKEN")  # resolve -> tracked for masking
    return registry


def test_masks_nested_models_lists_and_dict_values():
    model = Outer(
        title=f"title {SECRET}",
        inner=Inner(note=f"note {SECRET}"),
        items=[f"a {SECRET}", "b"],
        mapping={"k": f"v {SECRET}"},
    )

    masked = _registry().mask_secrets_in_model(model)

    assert masked.title == f"title {MASK}"
    assert masked.inner.note == f"note {MASK}"
    assert masked.items == [f"a {MASK}", "b"]
    assert masked.mapping == {"k": f"v {MASK}"}
    assert SECRET not in masked.model_dump_json()


def test_leaves_non_string_values_alone():
    model = Outer(title="clean", inner=Inner(note="clean"), count=7, blob=b"raw")

    masked = _registry().mask_secrets_in_model(model)

    assert masked.count == 7
    assert masked.blob == b"raw"


def test_preserves_str_subclass_enum_members():
    """A str-subclass enum must stay an enum member.

    Masking it would downgrade it to a plain ``str`` and break the field's
    serialization, and its vocabulary can never hold a secret anyway.
    """
    model = Outer(title="clean", inner=Inner(note="clean", kind=Kind.ADD))

    masked = _registry().mask_secrets_in_model(model)

    assert masked.inner.kind is Kind.ADD
    assert isinstance(masked.inner.kind, Kind)


def test_secretstr_is_not_walked_as_a_string():
    model = Outer(title="clean", inner=Inner(note="clean"), opaque=SecretStr(SECRET))

    masked = _registry().mask_secrets_in_model(model)

    assert isinstance(masked.opaque, SecretStr)
    assert masked.opaque.get_secret_value() == SECRET


def test_returns_input_unchanged_when_nothing_is_registered():
    model = Outer(title=f"title {SECRET}", inner=Inner(note="clean"))

    masked = SecretRegistry().mask_secrets_in_model(model)

    assert masked.title == f"title {SECRET}"


def test_masks_set_members():
    """A ``set`` field is walked like any other container."""
    model = Outer(
        title="clean", inner=Inner(note="clean"), tags={f"t {SECRET}", "plain"}
    )

    masked = _registry().mask_secrets_in_model(model)

    assert masked.tags == {f"t {MASK}", "plain"}


def test_preserves_namedtuple_type():
    """A NamedTuple keeps its type, like an Enum keeps its member."""
    model = Outer(title="clean", inner=Inner(note="clean"), span=Span(f"s {SECRET}", 3))

    masked = _registry().mask_secrets_in_model(model)

    assert isinstance(masked.span, Span)
    assert masked.span.label == f"s {MASK}"
    assert masked.span.line == 3
