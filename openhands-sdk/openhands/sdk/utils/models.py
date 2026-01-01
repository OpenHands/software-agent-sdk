import inspect
import logging
from abc import ABC
from typing import Annotated, Any, Self, Union

from pydantic import (
    BaseModel,
    Discriminator,
    ModelWrapValidatorHandler,
    Tag,
    TypeAdapter,
    ValidationInfo,
    model_serializer,
    model_validator,
)
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema


logger = logging.getLogger(__name__)


def _is_abstract(type_: type) -> bool:
    """Determine whether the class directly extends ABC or contains abstract methods"""
    try:
        return inspect.isabstract(type_) or ABC in type_.__bases__
    except Exception:
        return False


def kind_of(obj) -> str:
    """Get the string value for the kind tag"""
    if isinstance(obj, dict):
        return obj["kind"]
    if not hasattr(obj, "__name__"):
        obj = obj.__class__
    return obj.__name__


def _get_all_subclasses(cls) -> set[type]:
    """
    Recursively finds and returns all (loaded) subclasses of a given class.
    """
    result = set()
    for subclass in cls.__subclasses__():
        result.add(subclass)
        result.update(_get_all_subclasses(subclass))
    return result


def get_known_concrete_subclasses(cls) -> list[type]:
    """Recursively returns all concrete subclasses in a stable order,
    without deduping classes that share the same (module, name)."""
    out: list[type] = []
    for sub in cls.__subclasses__():
        # Recurse first so deeper classes appear after their parents
        out.extend(get_known_concrete_subclasses(sub))
        if not _is_abstract(sub):
            out.append(sub)

    # Use qualname to distinguish nested/local classes (like test-local Cat)
    out.sort(key=lambda t: (t.__module__, getattr(t, "__qualname__", t.__name__)))
    return out


class OpenHandsModel(BaseModel):
    """This class is in place only for backward compatibility"""


class DiscriminatedUnionMixin(OpenHandsModel):
    @model_validator(mode="wrap")
    @classmethod
    def _validate_subtype(
        cls, data: Any, handler: ModelWrapValidatorHandler[Self], info: ValidationInfo
    ) -> Self:
        if isinstance(data, cls):
            return data
        kind = data.pop("kind")
        if not _is_abstract(cls):
            assert kind == cls.__name__
            return handler(data)
        subclasses = get_known_concrete_subclasses(cls)
        for subclass in subclasses:
            if subclass.__name__ == kind:
                result = subclass.model_validate(data, context=info.context)
                return result
        kinds = [subclass.__name__ for subclass in subclasses]
        raise ValueError(f"Unknown kind: {kind}; Expected one of: {kinds}")

    @model_serializer(mode="wrap")
    def _serialize_by_kind(self, handler):
        result = handler(self)
        result["kind"] = self.__class__.__name__
        return result

    @classmethod
    def __get_pydantic_json_schema__(
        cls, core_schema: CoreSchema, handler: Any
    ) -> JsonSchemaValue:
        if _is_abstract(cls):
            subclasses = get_known_concrete_subclasses(cls)
            if not subclasses:
                raise ValueError(f"No subclasses defined for {cls.__name__}")
            if len(subclasses) == 1:
                return subclasses[0].model_json_schema()
            serializable_type = cls.get_serializable_type()
            type_adapter = TypeAdapter(serializable_type)
            schema = type_adapter.json_schema()
            return schema
        else:
            schema = handler(core_schema)
            schema["properties"]["kind"] = {
                "const": cls.__name__,
                "title": "Kind",
                "type": "string",
            }
            return schema

    @classmethod
    def resolve_kind(cls, kind: str) -> type:
        subclasses = get_known_concrete_subclasses(cls)
        for subclass in subclasses:
            if subclass.__name__ == kind:
                return subclass
        kinds = [subclass.__name__ for subclass in subclasses]
        raise ValueError(f"Unknown kind: {kind}; Expected one of: {kinds}")

    @classmethod
    def get_serializable_type(cls) -> type:
        """
        Custom method to get the union of all currently loaded
        non absract subclasses
        """

        # If the class is not abstract return self
        if not _is_abstract(cls):
            return cls

        subclasses = list(get_known_concrete_subclasses(cls))
        if not subclasses:
            return cls

        if len(subclasses) == 1:
            # Returning the concrete type ensures Pydantic instantiates the subclass
            # (e.g. Agent) rather than the abstract base (e.g. AgentBase) when there is
            # only ONE concrete subclass.
            return subclasses[0]

        serializable_type = Annotated[
            Union[*tuple(Annotated[t, Tag(t.__name__)] for t in subclasses)],
            Discriminator(kind_of),
        ]
        return serializable_type  # type: ignore
