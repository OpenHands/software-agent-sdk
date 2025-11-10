"""Utility for converting environment variables into pydantic base models.
We couldn't use pydantic-settings for this as we need complex nested types
and polymorphism."""

import inspect
import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from io import StringIO
from pathlib import Path
from types import UnionType
from typing import IO, Annotated, Any, Literal, Union, cast, get_args, get_origin
from uuid import UUID

from pydantic import BaseModel, SecretStr, TypeAdapter

from openhands.sdk.utils.models import DiscriminatedUnionMixin


# Define Missing type
class MissingType:
    pass


MISSING = MissingType()
JsonType = str | int | float | bool | dict | list | None | MissingType


class EnvParser(ABC):
    """Event parser type"""

    @abstractmethod
    def from_env(self, key: str) -> JsonType:
        """Parse environment variables into a json like structure"""

    def to_env(self, key: str, value: Any, output: IO):
        """Produce a template based on this parser"""
        if value is None:
            value = ""
        output.write(f"{key}={value}\n")


class BoolEnvParser(EnvParser):
    def from_env(self, key: str) -> bool | MissingType:
        if key not in os.environ:
            return MISSING
        return os.environ[key].upper() in ["1", "TRUE"]  # type: ignore

    def to_env(self, key: str, value: Any, output: IO):
        output.write(f"{key}={1 if value else 0}\n")


class IntEnvParser(EnvParser):
    def from_env(self, key: str) -> int | MissingType:
        if key not in os.environ:
            return MISSING
        return int(os.environ[key])


class FloatEnvParser(EnvParser):
    def from_env(self, key: str) -> float | MissingType:
        if key not in os.environ:
            return MISSING
        return float(os.environ[key])


class StrEnvParser(EnvParser):
    def from_env(self, key: str) -> str | MissingType:
        if key not in os.environ:
            return MISSING
        return os.environ[key]


class NoneEnvParser(EnvParser):
    def from_env(self, key: str) -> None | MissingType:
        key = f"{key}_IS_NONE"
        value = (os.getenv(key) or "").upper()
        if value in ["1", "TRUE"]:
            return None
        return MISSING

    def to_env(self, key: str, value: Any, output: IO):
        if value is None:
            output.write(f"{key}_IS_NONE=1\n")


@dataclass
class LiteralEnvParser(EnvParser):
    values: tuple[str, ...]

    def from_env(self, key: str) -> str | MissingType:
        value = os.getenv(key)
        if value not in self.values:
            return MISSING
        return value

    def to_env(self, key: str, value: Any, output: IO):
        output.write(f"# Permitted Values: {', '.join(self.values)}\n")
        # For enums, use the value instead of the string representation
        if hasattr(value, "value"):
            output.write(f"{key}={value.value}\n")
        else:
            output.write(f"{key}={value}\n")


@dataclass
class ModelEnvParser(EnvParser):
    parsers: dict[str, EnvParser]
    descriptions: dict[str, str]

    def from_env(self, key: str) -> dict | MissingType:
        # First we see is there a base value defined as json...
        value = os.environ.get(key)
        if value:
            result = json.loads(value)
            assert isinstance(result, dict)
        else:
            result = MISSING

        # Check for overrides...
        for field_name, parser in self.parsers.items():
            env_var_name = f"{key}_{field_name.upper()}"

            # First we check that there are possible keys for this field to prevent
            # infinite recursion
            has_possible_keys = next(
                (k for k in os.environ if k.startswith(env_var_name)), False
            )
            if not has_possible_keys:
                continue

            field_value = parser.from_env(env_var_name)
            if field_value is MISSING:
                continue
            if result is MISSING:
                result = {}
            existing_field_value = result.get(field_name, MISSING)  # type: ignore
            new_field_value = merge(existing_field_value, field_value)
            if new_field_value is not MISSING:
                result[field_name] = new_field_value  # type: ignore

        return result

    def to_env(self, key: str, value: Any, output: IO):
        for field_name, parser in self.parsers.items():
            field_description = self.descriptions.get(field_name)
            if field_description:
                for line in field_description.split("\n"):
                    output.write("# ")
                    output.write(line)
                    output.write("\n")
            field_key = key + "_" + field_name.upper()
            field_value = getattr(value, field_name)
            parser.to_env(field_key, field_value, output)
            output.write("\n")


class DictEnvParser(EnvParser):
    def from_env(self, key: str) -> dict | MissingType:
        # Read json from an environment variable
        value = os.environ.get(key)
        if value:
            result = json.loads(value)
            assert isinstance(result, dict)
        else:
            result = MISSING

        return result


@dataclass
class ListEnvParser(EnvParser):
    item_parser: EnvParser
    item_type: type

    def from_env(self, key: str) -> list | MissingType:
        if key not in os.environ:
            # Try to read sequentially, starting with 0
            # Return MISSING if there are no items
            result = MISSING
            index = 0
            while True:
                sub_key = f"{key}_{index}"
                item = self.item_parser.from_env(sub_key)
                if item is MISSING:
                    return result
                if result is MISSING:
                    result = []
                result.append(item)  # type: ignore
                index += 1

        # Assume the value is json
        value = os.environ.get(key)
        result = json.loads(value)  # type: ignore
        # A number indicates that the result should be N items long
        if isinstance(result, int):
            result = [MISSING] * result
        else:
            # Otherwise assume the item is a list
            assert isinstance(result, list)

        for index in range(len(result)):
            sub_key = f"{key}_{index}"
            item = self.item_parser.from_env(sub_key)
            item = merge(result[index], item)
            # We permit missing items in the list because these may be filled
            # in later when merged with the output of another parser
            result[index] = item  # type: ignore

        return result

    def to_env(self, key: str, value: Any, output: IO):
        if len(value):
            for index, sub_value in enumerate(value):
                sub_key = f"{key}_{index}"
                self.item_parser.to_env(sub_key, sub_value, output)
        else:
            # Try to produce a sample value based on the defaults...
            try:
                sub_key = f"{key}_0"
                sample_output = StringIO()
                self.item_parser.to_env(
                    sub_key, _create_sample(self.item_type), sample_output
                )
                for line in sample_output.getvalue().strip().split("\n"):
                    output.write("# ")
                    output.write(line)
                    output.write("\n")
            except Exception:
                # Couldn't create a sample value. Skip
                pass


@dataclass
class UnionEnvParser(EnvParser):
    parsers: dict[type, EnvParser]

    def from_env(self, key: str) -> JsonType:
        result = MISSING
        for parser in self.parsers.values():
            parser_result = parser.from_env(key)
            result = merge(result, parser_result)
        return result

    def to_env(self, key: str, value: Any, output: IO):
        for type_, parser in self.parsers.items():
            if not isinstance(value, type_):
                # Try to produce a sample value based on the defaults...
                try:
                    sample_value = _create_sample(type_)
                    sample_output = StringIO()
                    sample_output.write(f"{sample_value.__class__.__name__}\n")
                    parser.to_env(key, sample_value, sample_output)
                    for line in sample_output.getvalue().split("\n"):
                        output.write("# ")
                        output.write(line)
                        output.write("\n")
                except Exception:
                    # Couldn't create a sample value. Skip
                    pass
        for type_, parser in self.parsers.items():
            if isinstance(value, type_):
                output.write(f"# {value.__class__.__name__}\n")
                parser.to_env(key, value, output)
                output.write("\n")


@dataclass
class DelayedParser(EnvParser):
    """Delayed parser for circular dependencies"""

    parser: EnvParser | None = None

    def from_env(self, key: str) -> JsonType:
        assert self.parser is not None
        return self.parser.from_env(key)

    def to_env(self, key: str, value: Any, output: IO):
        assert self.parser is not None
        return self.parser.to_env(key, value, output)


def merge(a, b):
    if a is MISSING:
        return b
    if b is MISSING:
        return a
    if isinstance(a, dict) and isinstance(b, dict):
        result = {**a}
        for key, value in b.items():
            result[key] = merge(result.get(key), value)
        return result
    if isinstance(a, list) and isinstance(b, list):
        result = a.copy()
        for index, value in enumerate(b):
            if index >= len(a):
                result[index] = value
            else:
                result[index] = merge(result[index], value)
        return result
    # Favor present values over missing ones
    if b is None:
        return a
    # Later values overwrite earier ones
    return b


def get_env_parser(target_type: type, parsers: dict[type, EnvParser]) -> EnvParser:
    # Check if we have already defined a parser
    if target_type in parsers:
        return parsers[target_type]

    # Check origin
    origin = get_origin(target_type)
    if origin is Annotated:
        # Strip annotations...
        return get_env_parser(get_args(target_type)[0], parsers)
    if origin is UnionType or origin is Union:
        union_parsers = {
            t: get_env_parser(t, parsers)  # type: ignore
            for t in get_args(target_type)
        }
        return UnionEnvParser(union_parsers)
    if origin is list:
        item_type = get_args(target_type)[0]
        parser = get_env_parser(item_type, parsers)
        return ListEnvParser(parser, item_type)
    if origin is dict:
        args = get_args(target_type)
        assert args[0] is str
        assert args[1] in (str, int, float, bool)
        return DictEnvParser()
    if origin is Literal:
        args = cast(tuple[str, ...], get_args(target_type))
        return LiteralEnvParser(args)
    if origin and issubclass(origin, BaseModel):
        target_type = origin
    if issubclass(target_type, DiscriminatedUnionMixin) and (
        inspect.isabstract(target_type) or ABC in target_type.__bases__
    ):
        serializable_type = target_type.get_serializable_type()
        if serializable_type != target_type:
            return get_env_parser(target_type.get_serializable_type(), parsers)
    if issubclass(target_type, BaseModel):  # type: ignore
        delayed = DelayedParser()
        parsers[target_type] = delayed  # Prevent circular dependency
        field_parsers = {}
        descriptions = {}
        for name, field in target_type.model_fields.items():
            field_parsers[name] = get_env_parser(field.annotation, parsers)  # type: ignore
            description = field.description
            if description:
                descriptions[name] = description

        parser = ModelEnvParser(field_parsers, descriptions)
        delayed.parser = parser
        parsers[target_type] = parser
        return parser
    if issubclass(target_type, Enum):
        values = tuple(e.value for e in target_type)
        return LiteralEnvParser(values)
    raise ValueError(f"unknown_type:{target_type}")


def _get_default_parsers() -> dict[type, EnvParser]:
    return {
        str: StrEnvParser(),
        int: IntEnvParser(),
        float: FloatEnvParser(),
        bool: BoolEnvParser(),
        type(None): NoneEnvParser(),
        UUID: StrEnvParser(),
        Path: StrEnvParser(),
        datetime: StrEnvParser(),
        SecretStr: StrEnvParser(),
    }


def _create_sample(type_: type):
    if type_ is None:
        return None
    if type_ is str:
        return "..."
    if type_ is int:
        return 0
    if type_ is float:
        return 0.0
    if type_ is bool:
        return False
    try:
        if issubclass(type_, Enum):
            return next(iter(type_))
    except Exception:
        pass
    # Try to initialize and raise exception if failure.
    return type_()


def from_env(
    target_type: type,
    prefix: str = "",
    parsers: dict[type, EnvParser] | None = None,
):
    if parsers is None:
        parsers = _get_default_parsers()
    parser = get_env_parser(target_type, parsers)
    json_data = parser.from_env(prefix)
    if json_data is MISSING:
        result = target_type()
    else:
        json_str = json.dumps(json_data)
        type_adapter = TypeAdapter(target_type)
        result = type_adapter.validate_json(json_str)
    return result


def to_env(
    value: Any,
    prefix: str = "",
    parsers: dict[type, EnvParser] | None = None,
) -> str:
    if parsers is None:
        parsers = _get_default_parsers()
    parser = get_env_parser(value.__class__, parsers)
    output = StringIO()
    parser.to_env(prefix, value, output)
    return output.getvalue()
