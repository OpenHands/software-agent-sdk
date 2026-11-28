"""Independent proof of generic operations on an unrelated Pydantic model."""

from typing import Annotated, Literal

import pytest
from pydantic import BaseModel, Discriminator, Field, SecretStr, Tag, ValidationError
from pydantic_core import PydanticSerializationError

from openhands.sdk.utils.cipher import Cipher
from openhands.sdk.utils.model_overlay import (
    Extend,
    Inherit,
    PolicyDenied,
    PolicyRule,
    Remove,
    Set,
    SettingsPolicy,
    apply_operations,
    operation_from_dict,
    operation_to_dict,
    overlay_model,
    resolve_operations,
)
from openhands.sdk.utils.pydantic_secrets import SecretField


class Item(BaseModel):
    name: str
    weight: int = 1


class Limits(BaseModel):
    limit: int = 80
    tags: list[str] = Field(default_factory=list)


def _discriminator(value: object) -> str:
    return value.get("kind") if isinstance(value, dict) else value.kind  # type: ignore[attr-defined]


class Alpha(BaseModel):
    kind: Literal["alpha"] = "alpha"
    x: int = 1
    tags: list[str] = Field(default_factory=list)


class Beta(BaseModel):
    kind: Literal["beta"] = "beta"
    y: int = 2
    tags: list[str] = Field(default_factory=list)


class Form(BaseModel):
    options: Limits = Field(default_factory=Limits)
    tags: list[str] = Field(default_factory=list)
    items: list[Item] = Field(default_factory=list)
    maybe: list[int] | None = None
    secret: Annotated[SecretStr | None, SecretField()] = None
    child: Annotated[
        Annotated[Alpha, Tag("alpha")] | Annotated[Beta, Tag("beta")],
        Discriminator(_discriminator),
    ] = Field(default_factory=Alpha)


def parent() -> Form:
    return Form(
        options=Limits(limit=30, tags=["A", "B"]),
        tags=["A", "B"],
        items=[Item(name="a")],
    )


def test_extend_replays_after_parent_adds_items():
    owned = apply_operations(parent(), {}, [Extend(("tags",), ("C",))])
    assert owned == {"tags": {"$list_ops": {"extend": ["C"], "remove": []}}}
    grown = parent().model_copy(update={"tags": ["A", "B", "D"]})
    assert resolve_operations(grown, owned).tags == ["A", "B", "D", "C"]
    shrunk = parent().model_copy(update={"tags": ["B"]})
    assert resolve_operations(shrunk, owned).tags == ["B", "C"]


def test_remove_replays_after_parent_changes_and_duplicates():
    base = parent().model_copy(update={"tags": ["A", "A", "B"]})
    owned = apply_operations(base, {}, [Remove(("tags",), ("A",))])
    assert resolve_operations(base, owned).tags == ["B"]
    grown = parent().model_copy(update={"tags": ["A", "B", "D"]})
    assert resolve_operations(grown, owned).tags == ["B", "D"]


def test_extend_of_inherited_item_is_not_a_pin_and_remove_then_extend_readds():
    owned = apply_operations(parent(), {}, [Extend(("tags",), ("A",))])
    assert owned == {}
    removed = apply_operations(parent(), {}, [Remove(("tags",), ("A",))])
    readded = apply_operations(parent(), removed, [Extend(("tags",), ("A",))])
    assert readded == {}
    assert resolve_operations(parent(), readded).tags == ["A", "B"]


def test_record_items_extend_and_remove_by_value():
    owned = apply_operations(parent(), {}, [Extend(("items",), ({"name": "b"},))])
    grown = parent().model_copy(update={"items": [Item(name="a"), Item(name="c")]})
    assert [i.name for i in resolve_operations(grown, owned).items] == ["a", "c", "b"]
    owned = apply_operations(parent(), owned, [Remove(("items",), (Item(name="a"),))])
    assert [i.name for i in resolve_operations(grown, owned).items] == ["c", "b"]


def test_set_is_absolute_and_set_equal_to_parent_unpins():
    pinned = apply_operations(parent(), {}, [Set(("options", "limit"), 40)])
    assert pinned == {"options": {"limit": 40}}
    assert apply_operations(parent(), pinned, [Set(("options", "limit"), 30)]) == {}
    absolute = apply_operations(parent(), {}, [Set(("tags",), ["X"])])
    assert absolute == {"tags": ["X"]}
    grown = parent().model_copy(update={"tags": ["A", "B", "D"]})
    assert resolve_operations(grown, absolute).tags == ["X"]


def test_inherit_drops_only_local_contribution():
    owned = apply_operations(
        parent(), {}, [Set(("options", "limit"), 40), Extend(("tags",), ("C",))]
    )
    inherited = apply_operations(parent(), owned, [Inherit(("options", "limit"))])
    assert inherited == {"tags": {"$list_ops": {"extend": ["C"], "remove": []}}}
    assert resolve_operations(parent(), inherited).options.limit == 30
    assert apply_operations(parent(), owned, [Inherit(("tags",))]) == {
        "options": {"limit": 40}
    }


def test_nested_list_and_none_list_and_order_of_mixed_ops():
    owned = apply_operations(parent(), {}, [Extend(("options", "tags"), ("C",))])
    assert resolve_operations(parent(), owned).options.tags == ["A", "B", "C"]
    none_parent = Form(maybe=None)
    owned = apply_operations(none_parent, {}, [Extend(("maybe",), (1,))])
    assert resolve_operations(Form(maybe=[9]), owned).maybe == [9, 1]
    assert resolve_operations(none_parent, owned).maybe == [1]
    set_then_extend = apply_operations(
        parent(), {}, [Set(("tags",), ["X"]), Extend(("tags",), ("C",))]
    )
    grown = parent().model_copy(update={"tags": ["A", "B", "D"]})
    assert resolve_operations(grown, set_then_extend).tags == ["D", "X", "C"]
    extend_then_set = apply_operations(
        parent(), {}, [Extend(("tags",), ("C",)), Set(("tags",), ["X"])]
    )
    assert resolve_operations(grown, extend_then_set).tags == ["X"]


def test_empty_absent_and_null_are_distinct():
    assert resolve_operations(parent(), {}).tags == ["A", "B"]
    empty = apply_operations(parent(), {}, [Set(("tags",), [])])
    assert empty == {"tags": []}
    assert resolve_operations(parent(), empty).tags == []
    nulled = apply_operations(parent(), {}, [Set(("maybe",), None)])
    assert resolve_operations(parent(), nulled).maybe is None
    pinned_none = apply_operations(Form(maybe=[1]), {}, [Set(("maybe",), None)])
    assert pinned_none == {"maybe": None}


def test_variant_switch_and_same_variant_nested_ops():
    switched = apply_operations(
        parent(), {}, [Set(("child",), {"kind": "beta", "y": 9})]
    )
    assert switched == {"child": {"kind": "beta", "y": 9}}
    assert resolve_operations(parent(), switched).child.y == 9  # type: ignore[attr-defined]
    with pytest.raises(ValueError, match="Not a persistable field"):
        apply_operations(parent(), {}, [Set(("child", "y"), 3)])
    beta_parent = parent().model_copy(update={"child": Beta(tags=["A"])})
    owned = apply_operations(beta_parent, {}, [Extend(("child", "tags"), ("C",))])
    grown = parent().model_copy(update={"child": Beta(tags=["A", "D"])})
    assert resolve_operations(grown, owned).child.tags == ["A", "D", "C"]


def test_safe_paths_are_rejected_before_any_result():
    bad_ops = [
        Set(("nope",), 1),
        Set(("tags", "0"), "x"),
        Set((), 1),
        Set(("schema_version",), 1),
        Extend(("options", "limit"), (1,)),
        Extend(("secret",), ("x",)),
        Remove(("secret",), ("x",)),
        Extend(("tags",), ()),
        Set(("options", "nope"), 1),
    ]
    for op in bad_ops:
        with pytest.raises((ValueError, ValidationError)):
            apply_operations(parent(), {}, [op])


def test_batch_is_atomic_and_inputs_are_not_modified():
    owned = {"tags": ["Z"]}
    with pytest.raises((ValueError, ValidationError)):
        apply_operations(parent(), owned, [Set(("options", "limit"), "bad")])
    assert owned == {"tags": ["Z"]}
    assert parent().tags == ["A", "B"]
    with pytest.raises((ValueError, ValidationError)):
        apply_operations(parent(), {}, [Extend(("tags",), ("C",)), Set(("nope",), 1)])


def test_policy_denies_descendant_ancestor_and_batch_writes():
    policy = SettingsPolicy(
        rules=(PolicyRule(("options",), frozenset({"set", "inherit"})),)
    )
    with pytest.raises(PolicyDenied):
        apply_operations(parent(), {}, [Set(("options", "limit"), 1)], policy=policy)
    with pytest.raises(PolicyDenied):
        apply_operations(parent(), {}, [Set(("options",), {"limit": 1})], policy=policy)
    with pytest.raises(PolicyDenied):
        apply_operations(parent(), {}, [Inherit(("options",))], policy=policy)
    with pytest.raises(PolicyDenied):
        apply_operations(
            parent(),
            {},
            [Set(("tags",), ["X"]), Set(("options", "limit"), 1)],
            policy=policy,
        )
    allowed = apply_operations(parent(), {}, [Extend(("tags",), ("C",))], policy=policy)
    assert resolve_operations(parent(), allowed).tags == ["A", "B", "C"]
    list_policy = SettingsPolicy(rules=(PolicyRule(("tags",), frozenset({"extend"})),))
    with pytest.raises(PolicyDenied):
        apply_operations(parent(), {}, [Extend(("tags",), ("C",))], policy=list_policy)


def test_secret_set_is_encrypted_and_wrong_key_fails():
    empty = Form()
    with pytest.raises(PydanticSerializationError):
        apply_operations(empty, {}, [Set(("secret",), "synthetic test value")])
    cipher = Cipher("synthetic-test-key")
    stored = apply_operations(
        empty, {}, [Set(("secret",), "synthetic test value")], cipher=cipher
    )
    assert stored["secret"] != "synthetic test value"
    resolved = resolve_operations(empty, stored, cipher=cipher)
    assert resolved.secret is not None
    assert resolved.secret.get_secret_value() == "synthetic test value"
    # Unchanged secret keeps its ciphertext exactly.
    assert (
        apply_operations(empty, stored, [Set(("tags",), ["X"])], cipher=cipher)[
            "secret"
        ]
        == stored["secret"]
    )
    with pytest.raises(Exception) as excinfo:
        resolve_operations(empty, stored, cipher=Cipher("wrong-test-key"))
    assert "decrypt" in str(excinfo.value).lower()


def test_operation_dict_round_trip_and_strict_validation():
    ops = [
        Set(("a",), 1),
        Extend(("t",), ("x",)),
        Remove(("t",), ("x",)),
        Inherit(("t",)),
    ]
    for op in ops:
        assert operation_from_dict(operation_to_dict(op)) == op
    for bad in (
        {"op": "delete", "path": ["t"]},
        {"op": "set", "path": ["t"]},
        {"op": "extend", "path": "t", "items": []},
        {"op": "inherit", "path": ["t"], "value": 1},
        {"op": "set", "path": [], "value": 1},
        {"op": "set", "path": ["t"], "value": 1, "extra": 2},
    ):
        with pytest.raises(ValueError):
            operation_from_dict(bad)


def test_overlay_model_rejects_relative_records_and_plain_docs_still_resolve():
    owned = apply_operations(parent(), {}, [Extend(("tags",), ("C",))])
    with pytest.raises(ValueError, match="resolve_operations"):
        overlay_model(parent(), owned)
    plain = {"options": {"limit": 40}}
    assert resolve_operations(parent(), plain) == overlay_model(parent(), plain)


def test_generic_ops_smoke_on_agent_settings():
    from openhands.sdk.settings import OpenHandsAgentSettings

    base = OpenHandsAgentSettings()
    owned = apply_operations(base, {}, [Set(("llm", "timeout"), 123)])
    assert resolve_operations(base, owned).llm.timeout == 123
    assert apply_operations(base, owned, [Inherit(("llm", "timeout"))]) == {}
