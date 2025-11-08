from abc import ABC
from typing import Annotated, ClassVar

import pytest
from litellm import BaseModel
from pydantic import (
    ConfigDict,
    Discriminator,
    Field,
    Tag,
    TypeAdapter,
    computed_field,
    model_validator,
)

from openhands.sdk.utils.models import (
    DiscriminatedUnionMixin,
    kind_of,
)


class Animal(DiscriminatedUnionMixin, ABC):
    name: str


class Cat(Animal):
    pass


class Canine(Animal, ABC):
    pass


class Dog(Canine):
    barking: bool


class Wolf(Canine):
    @computed_field
    @property
    def genus(self) -> str:
        return "Canis"

    @model_validator(mode="before")
    @classmethod
    def _remove_genus(cls, data):
        # Remove the genus from input as it is generated
        if not isinstance(data, dict):
            return
        data = dict(data)
        data.pop("genus", None)
        return data

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")


class AnimalPack(BaseModel):
    members: list[Animal] = Field(default_factory=list)

    @computed_field
    @property
    def alpha(self) -> Animal | None:
        return self.members[0] if self.members else None

    @property
    def num_animals(self):
        return len(self.members)

    @model_validator(mode="before")
    @classmethod
    def _remove_alpha(cls, data):
        # Remove the genus from input as it is generated
        if not isinstance(data, dict):
            return
        data = dict(data)
        data.pop("alpha", None)
        return data

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")


def test_serializable_type_expected() -> None:
    serializable_type = Animal.get_serializable_type()
    expected_serializable_type = Annotated[
        Annotated[Cat, Tag("Cat")]
        | Annotated[Dog, Tag("Dog")]
        | Annotated[Wolf, Tag("Wolf")],
        Discriminator(kind_of),
    ]
    assert serializable_type == expected_serializable_type


def test_json_schema() -> None:
    serializable_type = Animal.model_json_schema()
    assert "oneOf" in serializable_type


def test_additional_field() -> None:
    original = Dog(name="Fido", barking=True)
    dumped = original.model_dump()
    loaded = Animal.model_validate(dumped)
    assert loaded == original
    assert isinstance(loaded, Dog)
    assert loaded.barking


def test_property() -> None:
    """There seems to be a real issue with @property decorators"""
    original = Wolf(name="Silver")
    dumped = original.model_dump()
    assert dumped["genus"] == "Canis"
    loaded = Animal.model_validate(dumped)
    assert loaded == original
    assert original.genus == "Canis"
    assert isinstance(loaded, Wolf)
    assert loaded.genus == "Canis"


def test_serialize_single_model() -> None:
    original = Cat(name="Felix")
    dumped = original.model_dump()
    loaded = Animal.model_validate(dumped)
    assert original == loaded
    dumped_json = original.model_dump_json()
    loaded_json = Animal.model_validate_json(dumped_json)
    assert original == loaded_json


def test_serialize_single_model_with_type_adapter() -> None:
    type_adapter = TypeAdapter(Animal)
    original = Cat(name="Felix")
    dumped = type_adapter.dump_python(original)
    loaded = type_adapter.validate_python(dumped)
    assert original == loaded
    dumped_json = type_adapter.dump_json(original)
    loaded_json = type_adapter.validate_json(dumped_json)
    assert original == loaded_json


def test_serialize_model_list() -> None:
    type_adapter = TypeAdapter(list[Animal])
    original = [Cat(name="Felix"), Dog(name="Fido", barking=True), Wolf(name="Bitey")]
    dumped = type_adapter.dump_python(original)
    loaded = type_adapter.validate_python(dumped)
    assert original == loaded


def test_model_containing_polymorphic_field():
    pack = AnimalPack(
        members=[
            Wolf(name="Larry"),
            Dog(name="Curly", barking=False),
            Cat(name="Moe"),
        ]
    )
    Animal.model_rebuild(force=True)
    AnimalPack.model_rebuild(force=True)
    dumped = pack.model_dump()
    assert dumped == {
        "members": [
            {"kind": "Wolf", "name": "Larry", "genus": "Canis"},
            {"kind": "Dog", "name": "Curly", "barking": False},
            {"kind": "Cat", "name": "Moe"},
        ],
        "alpha": {"kind": "Wolf", "name": "Larry", "genus": "Canis"},
    }
    loaded = AnimalPack.model_validate(dumped)
    assert loaded == pack


def test_duplicate_kind():
    # nAn error should be raised when a duplicate class name is detected

    with pytest.raises(ValueError):

        class Cat(Animal):
            pass
