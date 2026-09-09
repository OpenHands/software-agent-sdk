from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ContextDifference:
    added: tuple[str, ...]
    removed: tuple[str, ...]
    changed: tuple[str, ...]


def diff_contexts(
    previous: Sequence[dict[str, Any]], current: Sequence[dict[str, Any]]
) -> ContextDifference:
    previous_by_id = {
        str(message.get("id", index)): message for index, message in enumerate(previous)
    }
    current_by_id = {
        str(message.get("id", index)): message for index, message in enumerate(current)
    }
    previous_ids = set(previous_by_id)
    current_ids = set(current_by_id)
    return ContextDifference(
        added=tuple(sorted(current_ids - previous_ids)),
        removed=tuple(sorted(previous_ids - current_ids)),
        changed=tuple(
            sorted(
                message_id
                for message_id in previous_ids & current_ids
                if previous_by_id[message_id] != current_by_id[message_id]
            )
        ),
    )
