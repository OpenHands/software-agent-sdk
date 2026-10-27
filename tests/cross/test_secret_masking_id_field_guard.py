"""Every id field on an Event or Observation must opt out of secret masking.

Type a new id field as ``EventID``, ``ToolCallID`` or ``UnmaskedID`` from
``openhands.sdk.event.types``.
"""

import importlib
import pkgutil
from typing import get_args

from pydantic import BaseModel

from openhands.sdk.conversation.secret_registry import SecretRegistry
from openhands.sdk.event import Event
from openhands.sdk.tool import Observation
from openhands.sdk.utils.masking import SkipSecretMasking, field_has_marker
from openhands.tools.child_conversation.definition import (
    StartChildConversationObservation,
)
from openhands.tools.task.definition import TaskObservation


SECRET = "a1b"
MASK = "<secret-hidden>"
ID = "fa1b4e36-bdd7-42bf-99df-e184c9dc0d9a"

# "Class.field" names of id fields that can really hold a secret.
_ALLOWLIST: set[str] = set()


def _registry() -> SecretRegistry:
    registry = SecretRegistry()
    registry.update_secrets({"SHORT": SECRET})
    registry.get_secret_value("SHORT")  # resolve -> tracked for masking
    return registry


def _is_id_name(name: str) -> bool:
    return name == "id" or name.endswith(("_id", "_ids"))


def _can_hold_str(annotation: object) -> bool:
    # ``list[int]`` token ids are never masked, so they need no marker.
    return annotation is str or any(_can_hold_str(a) for a in get_args(annotation))


def _unmarked_id_fields(cls: type[BaseModel]) -> list[str]:
    """Return the id fields of ``cls`` that secret masking would rewrite."""
    return [
        f"{cls.__qualname__}.{name}"
        for name, field in cls.model_fields.items()
        if _is_id_name(name)
        and _can_hold_str(field.annotation)
        and not field_has_marker(field, SkipSecretMasking)
    ]


def _all_subclasses(*bases: type[BaseModel]) -> set[type[BaseModel]]:
    """Import every shipped sdk and tools module, then collect subclasses."""
    import openhands.sdk
    import openhands.tools

    for package in (openhands.sdk, openhands.tools):
        for module in pkgutil.walk_packages(
            package.__path__, prefix=f"{package.__name__}."
        ):
            try:
                importlib.import_module(module.name)
            except Exception:  # optional extras must not fail the guard
                continue

    def walk(cls):
        for sub in cls.__subclasses__():
            yield sub
            yield from walk(sub)

    return {cls for base in bases for cls in (base, *walk(base))}


def test_every_event_and_observation_id_field_skips_masking():
    offenders = sorted(
        name
        for cls in _all_subclasses(Event, Observation)
        if cls.__module__.startswith("openhands.")
        for name in _unmarked_id_fields(cls)
        if name not in _ALLOWLIST
    )

    assert not offenders, (
        "These id fields are secret-masked, so a secret inside the id rewrites "
        "it. Type them as EventID, ToolCallID or UnmaskedID: "
        f"{offenders}"
    )


def test_guard_flags_an_unmarked_id_field():
    class _NewEvent(Event):
        foo_id: str
        bar_ids: list[str] | None = None

    assert _unmarked_id_fields(_NewEvent) == [
        "test_guard_flags_an_unmarked_id_field.<locals>._NewEvent.foo_id",
        "test_guard_flags_an_unmarked_id_field.<locals>._NewEvent.bar_ids",
    ]


def test_tool_observation_ids_are_not_masked():
    child = StartChildConversationObservation.from_text(
        f"started {SECRET}", conversation_id=ID, parent_conversation_id=ID
    )
    task = TaskObservation.from_text(
        f"done {SECRET}", task_id=ID, subagent="sub", status="completed"
    )

    masked_child = _registry().mask_secrets_in_model(child)
    masked_task = _registry().mask_secrets_in_model(task)

    assert masked_child.conversation_id == ID
    assert masked_child.parent_conversation_id == ID
    assert masked_child.text == f"started {MASK}"
    assert masked_task.task_id == ID
    assert masked_task.text == f"done {MASK}"
