import re
from re import Pattern


_EXTENSION_NAME_PATTERN: Pattern[str] = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def validate_extension_name(name: str) -> None:
    """Validate that *name* is kebab-case (``^[a-z0-9]+(-[a-z0-9]+)*$``).

    Raises:
        ValueError: If *name* does not match the pattern.
    """
    if not _EXTENSION_NAME_PATTERN.fullmatch(name):
        raise ValueError(f"Invalid extension name. Expected kebab-case, got {name!r}.")
