import inspect
import logging
import threading
from abc import ABC
from typing import Annotated, Any, Self, Union

from pydantic import (
    BaseModel,
    Discriminator,
    ModelWrapValidatorHandler,
    SerializationInfo,
    SerializerFunctionWrapHandler,
    Tag,
    ValidationInfo,
    computed_field,
    model_serializer,
    model_validator,
)
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema


logger = logging.getLogger(__name__)

# Thread-local storage for tracking schemas currently being generated.
# This prevents infinite recursion when generating JSON schemas for
# discriminated unions that reference each other.
_thread_local = threading.local()


def _get_schemas_in_progress() -> dict[type, JsonSchemaValue]:
    """Get the thread-local dict for tracking in-progress schema generation."""
    if not hasattr(_thread_local, "schemas_in_progress"):
        _thread_local.schemas_in_progress = {}
    return _thread_local.schemas_in_progress


def _is_abstract(type_: type) -> bool:
    """Determine whether the class directly extends ABC or contains abstract methods"""
    try:
        return inspect.isabstract(type_) or ABC in type_.__bases__
    except Exception:
        return False


def get_handler_class_name(handler: SerializerFunctionWrapHandler) -> str:
    """Extract the class name from a Pydantic serializer handler's repr string.

    WARNING: This is a fragile approach that relies on Pydantic's internal
    repr format for SerializerFunctionWrapHandler. The handler is a Pydantic
    wrapper around a Rust function that provides no public API for determining
    which class it serializes. Parsing the repr string is the only available
    mechanism.

    Expected format: `SerializationCallable(serializer=<ClassName>)`

    If Pydantic changes this format, multiple unit tests will fail immediately,
    including tests in test_discriminated_union.py that verify serialization
    behavior across the class hierarchy.

    Args:
        handler: The Pydantic serializer function wrap handler

    Returns:
        The class name extracted from the handler's repr string
    """
    repr_str = str(handler)
    # Format is `SerializationCallable(serializer=<NAME>)`
    # Get everything after =
    _, name = repr_str.split("=", 1)
    # Cut off the trailing )
    return name[:-1]


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


# ---------------------------------------------------------------------------
# Subclass-hierarchy caching
#
# get_known_concrete_subclasses() and _get_checked_concrete_subclasses() are
# called on every event deserialization (via _validate_subtype).  Walking the
# full class hierarchy each time dominated per-step CPU (~47 % of self-time
# in wall profiles).
#
# The cache is keyed by (cls, _subclass_generation).  The generation counter
# is bumped automatically via DiscriminatedUnionMixin.__init_subclass__
# whenever a new subclass is defined, so callers never need to invalidate
# manually — the cache self-invalidates.
# ---------------------------------------------------------------------------
_subclass_generation: int = 0
_subclass_generation_lock = threading.Lock()
_concrete_cache: dict[type, tuple[int, tuple[type, ...]]] = {}
_checked_cache: dict[type, tuple[int, dict[str, type]]] = {}


def _bump_subclass_generation() -> None:
    global _subclass_generation
    with _subclass_generation_lock:
        _subclass_generation += 1


def get_known_concrete_subclasses(cls) -> tuple[type, ...]:
    """Recursively returns all concrete subclasses in a stable order,
    without deduping classes that share the same (module, name).

    Results are cached and automatically invalidated when new
    DiscriminatedUnionMixin subclasses are defined.
    """
    cached = _concrete_cache.get(cls)
    if cached is not None and cached[0] == _subclass_generation:
        return cached[1]

    out: list[type] = []
    for sub in cls.__subclasses__():
        # Recurse first so deeper classes appear after their parents
        out.extend(get_known_concrete_subclasses(sub))
        if not _is_abstract(sub):
            out.append(sub)

    # Use qualname to distinguish nested/local classes (like test-local Cat)
    out.sort(key=lambda t: (t.__module__, getattr(t, "__qualname__", t.__name__)))
    result = tuple(out)
    _concrete_cache[cls] = (_subclass_generation, result)
    return result


def _get_checked_concrete_subclasses(cls: type) -> dict[str, type]:
    cached = _checked_cache.get(cls)
    if cached is not None and cached[0] == _subclass_generation:
        return cached[1]

    result: dict[str, type] = {}
    for sub in get_known_concrete_subclasses(cls):
        existing = result.get(sub.__name__)
        if existing:
            raise ValueError(
                f"Duplicate class definition for {cls.__module__}.{cls.__name__}: "
                f"{existing.__module__}.{existing.__name__} : "
                f"{sub.__module__}.{sub.__name__}"
            )
        if "<locals>" in sub.__qualname__:
            raise ValueError(
                f"Local classes not supported! {sub.__module__}.{sub.__name__} "
                f"/ {cls.__module__}.{cls.__name__} "
                "(Since they may not exist at deserialization time)"
            )
        result[sub.__name__] = sub
    _checked_cache[cls] = (_subclass_generation, result)
    return result


def clear_subclass_cache() -> None:
    """Invalidate cached results of :func:`get_known_concrete_subclasses`
    and :func:`_get_checked_concrete_subclasses`.

    Normally not needed — the cache auto-invalidates when new
    DiscriminatedUnionMixin subclasses are defined.  This function exists
    for edge cases involving non-DiscriminatedUnionMixin hierarchies.
    """
    _bump_subclass_generation()


class OpenHandsModel(BaseModel):
    """Deprecated: This class exists only for backward compatibility.

    This class is no longer required for discriminated union support.
    New code should extend pydantic.BaseModel directly instead of OpenHandsModel.

    Existing code that extends OpenHandsModel will continue to work, but
    migration to BaseModel is recommended.
    """


class DiscriminatedUnionMixin(OpenHandsModel):
    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        _bump_subclass_generation()

    @computed_field
    @property
    def kind(self) -> str:
        return self.__class__.__name__

    @model_validator(mode="wrap")
    @classmethod
    def _validate_subtype(
        cls, data: Any, handler: ModelWrapValidatorHandler[Self], info: ValidationInfo
    ) -> Self:
        if isinstance(data, cls):
            return data
        if not _is_abstract(cls):
            has_kind_alias_field = any(
                field_name != "kind" and field_info.alias == "kind"
                for field_name, field_info in cls.model_fields.items()
            )
            if has_kind_alias_field:
                # Concrete persisted dumps can include both the real aliased
                # argument and this mixin's computed discriminator.
                if isinstance(data, dict) and data.get("kind") == cls.__name__:
                    internal_kind_field = next(
                        (
                            field_name
                            for field_name, field_info in cls.model_fields.items()
                            if field_name != "kind" and field_info.alias == "kind"
                        ),
                        None,
                    )
                    if internal_kind_field in data:
                        data = data.copy()
                        data.pop("kind", None)
                return handler(data)
            kind = data.pop("kind", None)
            # Sanity check: if we're validating a concrete class directly,
            # the kind (if provided) should match the class name. This should
            # always be true at this point since resolve_kind() would have
            # already routed to the correct subclass.
            assert kind is None or kind == cls.__name__
            return handler(data)
        kind = data.pop("kind", None)
        if kind is None:
            subclasses = _get_checked_concrete_subclasses(cls)
            if not subclasses:
                raise ValueError(
                    f"No kinds defined for {cls.__module__}.{cls.__name__}"
                )
            elif len(subclasses) == 1:
                # If there is ony 1 possible implementation, then we do not need
                # to state the kind explicitly - it can only be this!
                kind = next(iter(subclasses))
            else:
                # There is more than 1 kind defined but the input did not specify
                # This will cause an error to be raised
                kind = ""
        subclass = cls.resolve_kind(kind)
        return subclass.model_validate(data, context=info.context)

    @model_serializer(mode="wrap")
    def _serialize_by_kind(
        self, handler: SerializerFunctionWrapHandler, info: SerializationInfo
    ):
        if isinstance(self, dict):
            # Sometimes pydantic passes a dict in here.
            return self
        if self._is_handler_for_current_class(handler):
            result = handler(self)
            return result

        # Delegate to the implementing class
        result = self.model_dump(
            mode=info.mode,
            context=info.context,
            by_alias=info.by_alias,
            exclude_unset=info.exclude_unset,
            exclude_defaults=info.exclude_defaults,
            exclude_none=info.exclude_none,
            exclude_computed_fields=info.exclude_computed_fields,
            round_trip=info.round_trip,
            serialize_as_any=info.serialize_as_any,
        )
        return result

    def _is_handler_for_current_class(
        self, handler: SerializerFunctionWrapHandler
    ) -> bool:
        """Check if the handler is for this class.

        See get_handler_class_name() for details on the fragile string parsing
        this relies on.
        """
        return self.__class__.__name__ == get_handler_class_name(handler)

    @classmethod
    def __get_pydantic_json_schema__(
        cls, core_schema: CoreSchema, handler: Any
    ) -> JsonSchemaValue:
        schemas_in_progress = _get_schemas_in_progress()

        # First we check if we are already generating a schema
        schema = schemas_in_progress.get(cls)
        if schema:
            return schema

        # Set a temp schema to prevent infinite recursion
        schemas_in_progress[cls] = {"$ref": f"#/$defs/{cls.__name__}"}
        try:
            if _is_abstract(cls):
                subclasses = _get_checked_concrete_subclasses(cls)
                if not subclasses:
                    raise ValueError(f"No subclasses defined for {cls.__name__}")
                if len(subclasses) == 1:
                    # Use the shared generator for single subclass too
                    gen = handler.generate_json_schema
                    sub_schema = gen.generate_inner(
                        next(iter(subclasses.values())).__pydantic_core_schema__
                    )
                    return sub_schema

                # Use the shared generator to properly register definitions
                gen = handler.generate_json_schema
                schemas = []
                for sub in subclasses.values():
                    sub_schema = gen.generate_inner(sub.__pydantic_core_schema__)
                    schemas.append(sub_schema)

                # Build discriminator mapping from $ref schemas
                mapping = {}
                for option in schemas:
                    if "$ref" in option:
                        kind = option["$ref"].split("/")[-1]
                        mapping[kind] = option["$ref"]

                schema = {
                    "oneOf": schemas,
                    "discriminator": {"propertyName": "kind", "mapping": mapping},
                }
            else:
                schema = handler(core_schema)
                schema["properties"]["kind"] = {
                    "const": cls.__name__,
                    "title": "Kind",
                    "type": "string",
                }
        finally:
            # Reset temp schema
            schemas_in_progress.pop(cls)
        return schema

    @classmethod
    def resolve_kind(cls, kind: str) -> type[Self]:
        subclasses = _get_checked_concrete_subclasses(cls)
        subclass = subclasses.get(kind)
        if subclass:
            return subclass
        raise ValueError(
            f"Unknown kind '{kind}' for {cls.__module__}.{cls.__name__}; "
            f"Expected one of: {list(subclasses)}"
        )

    @classmethod
    def get_serializable_type(cls) -> type:
        """
        Custom method to get the union of all currently loaded
        non absract subclasses
        """

        # If the class is not abstract return self
        if not _is_abstract(cls):
            return cls

        subclasses = _get_checked_concrete_subclasses(cls)
        if not subclasses:
            return cls

        if len(subclasses) == 1:
            # Returning the concrete type ensures Pydantic instantiates the subclass
            # (e.g. Agent) rather than the abstract base (e.g. AgentBase) when there is
            # only ONE concrete subclass.
            return next(iter(subclasses.values()))

        serializable_type = Annotated[
            Union[*tuple(Annotated[t, Tag(n)] for n, t in subclasses.items())],
            Discriminator(kind_of),
        ]
        return serializable_type  # type: ignore
