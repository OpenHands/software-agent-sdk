import gc
from abc import ABC, abstractmethod
from typing import ClassVar

import pytest
from litellm import BaseModel
from pydantic import (
    ConfigDict,
    Field,
    TypeAdapter,
    computed_field,
    model_validator,
)

from openhands.sdk.utils.models import (
    DiscriminatedUnionMixin,
    OpenHandsModel,
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


class Mythical(DiscriminatedUnionMixin, ABC):
    """Mythical beasts have no implementations - they do not exist!"""

    @abstractmethod
    def get_description(self) -> str:
        """Get a discription of the mythical beast"""


class MythicalPack(OpenHandsModel):
    mythical: Mythical


class SomeBase(DiscriminatedUnionMixin, ABC):
    """Base class for duplicate test"""


class SomeImpl(SomeBase):
    """Implementation for duplicate test"""


def test_json_schema_expected() -> None:
    json_schema = Animal.model_json_schema()

    # Verify the schema has the expected structure
    assert "$defs" in json_schema
    assert "oneOf" in json_schema
    assert "discriminator" in json_schema

    # Check discriminator structure
    discriminator = json_schema["discriminator"]
    assert discriminator["propertyName"] == "kind"
    assert "mapping" in discriminator

    # Check the oneOf variants
    assert json_schema["oneOf"] == [
        {"$ref": "#/$defs/Cat"},
        {"$ref": "#/$defs/Dog"},
        {"$ref": "#/$defs/Wolf"},
    ]

    # Check the $defs structure
    assert json_schema["$defs"]["Cat"] == {
        "properties": {
            "name": {"title": "Name", "type": "string"},
            "kind": {"const": "Cat", "title": "Kind", "type": "string"},
        },
        "required": ["name"],
        "title": "Cat",
        "type": "object",
    }
    assert json_schema["$defs"]["Dog"] == {
        "properties": {
            "name": {"title": "Name", "type": "string"},
            "barking": {"title": "Barking", "type": "boolean"},
            "kind": {"const": "Dog", "title": "Kind", "type": "string"},
        },
        "required": ["name", "barking"],
        "title": "Dog",
        "type": "object",
    }
    assert json_schema["$defs"]["Wolf"] == {
        "additionalProperties": False,
        "properties": {
            "name": {"title": "Name", "type": "string"},
            "kind": {"const": "Wolf", "title": "Kind", "type": "string"},
        },
        "required": ["name"],
        "title": "Wolf",
        "type": "object",
    }


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
    # An error should be raised when a duplicate class definition is detected.

    with pytest.raises(ValueError) as exc_info:

        class SomeImpl(SomeBase):
            """Duplicate implementation name"""

        SomeBase.model_json_schema()

    error_message = str(exc_info.value)
    expected = (
        "Duplicate class definition for "
        "tests.sdk.utils.test_discriminated_union.SomeBase: "
        "tests.sdk.utils.test_discriminated_union.SomeImpl : "
        "tests.sdk.utils.test_discriminated_union.SomeImpl"
    )
    assert expected in error_message

    # Ensure the failed subclass definition does not leak into subsequent tests.
    gc.collect()


def test_enhanced_error_message_with_validation():
    """Test that the enhanced error message appears during model validation."""
    # Create invalid data with unknown kind
    invalid_data = {"kind": "UnknownAnimal", "name": "Test"}

    with pytest.raises(ValueError) as exc_info:
        Animal.model_validate(invalid_data)

    error_message = str(exc_info.value)

    # Check that the error message contains expected components
    expected = (
        "Unknown kind 'UnknownAnimal' for "
        "tests.sdk.utils.test_discriminated_union.Animal; "
        "Expected one of: ['Cat', 'Dog', 'Wolf']"
    )
    assert expected in error_message


def test_dynamic_field_error():
    class Tiger(Cat):
        pass

    with pytest.raises(ValueError) as exc_info:
        AnimalPack.model_json_schema()

    error_message = str(exc_info.value)
    expected = (
        "Local classes not supported! "
        "tests.sdk.utils.test_discriminated_union.Tiger / "
        "tests.sdk.utils.test_discriminated_union.Animal "
        "(Since they may not exist at deserialization time)"
    )
    assert expected in error_message


def test_enhanced_error_message_for_no_kinds():
    with pytest.raises(ValueError) as exc_info:
        Mythical.model_validate({"kind": "Unicorn"})

    error_message = str(exc_info.value)

    # Check that the error message contains all expected components
    expected = (
        "Unknown kind 'Unicorn' for tests.sdk.utils.test_discriminated_union.Mythical; "
        "Expected one of: []"
    )
    assert expected in error_message


def test_enhanced_error_message_for_nested_no_kinds():
    with pytest.raises(Exception) as exc_info:
        MythicalPack.model_validate({"mythical": {"kind": "Unicorn"}})

    error_message = str(exc_info.value)

    # Check that the error message contains all expected components
    expected = (
        "Unknown kind 'Unicorn' for tests.sdk.utils.test_discriminated_union.Mythical; "
        "Expected one of: []"
    )
    assert expected in error_message


def test_enhanced_error_message_for_nested_no_kinds_type_adapter():
    type_adapter = TypeAdapter(MythicalPack)
    with pytest.raises(Exception) as exc_info:
        type_adapter.validate_python({"mythical": {"kind": "Unicorn"}})

    error_message = str(exc_info.value)

    # Check that the error message contains all expected components
    expected = (
        "Unknown kind 'Unicorn' for tests.sdk.utils.test_discriminated_union.Mythical; "
        "Expected one of: []"
    )
    assert expected in error_message
