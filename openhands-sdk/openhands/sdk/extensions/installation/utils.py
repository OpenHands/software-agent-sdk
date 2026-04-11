import re
from re import Pattern


_EXTENSION_NAME_PATTERN: Pattern[str] = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def validate_extension_name(name: str) -> None:
    """Validate extension name is Claude-like kebab-case.

    This protects filesystem operations (install/uninstall) from path traversal.
    """
    if not _EXTENSION_NAME_PATTERN.fullmatch(name):
        raise ValueError(f"Invalid extension name. Expected kebab-case, got {name!r}.")
