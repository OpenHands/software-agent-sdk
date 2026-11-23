"""Environment helpers for terminal backends."""

import codecs
import locale
import os
import re
from collections.abc import Mapping

from openhands.sdk.logger import get_logger
from openhands.sdk.utils import sanitized_env


logger = get_logger(__name__)

ENV_VAR_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
UTF8_FALLBACK_LOCALE = "C.UTF-8"


def normalize_terminal_env(
    extra_env: Mapping[str, str] | None,
) -> dict[str, str] | None:
    """Validate and copy client-provided terminal environment variables."""
    if extra_env is None:
        return None

    normalized: dict[str, str] = {}
    for key, value in extra_env.items():
        if not isinstance(key, str) or not ENV_VAR_NAME_RE.match(key):
            raise ValueError(f"Invalid terminal environment variable name: {key!r}")
        if not isinstance(value, str):
            raise TypeError(
                "Terminal environment variable values must be strings: "
                f"{key!r}={value!r}"
            )
        normalized[key] = value
    return normalized


def build_terminal_env(extra_env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return the sanitized process environment plus client-provided overrides."""
    env = sanitized_env()
    normalized = normalize_terminal_env(extra_env)
    if normalized:
        env.update(normalized)
        env = sanitized_env(env)
    return env


def has_utf8_locale(environ: Mapping[str, str]) -> bool:
    """Apply tmux's check: TMUX is set, or the first non-empty of LC_ALL,
    LC_CTYPE, LANG names UTF-8."""
    if "TMUX" in environ:
        return True
    for name in ("LC_ALL", "LC_CTYPE", "LANG"):
        value = environ.get(name)
        if value:
            return "utf-8" in value.lower() or "utf8" in value.lower()
    return False


def ensure_utf8_locale() -> tuple[str, str | None] | None:
    """Fall back to a UTF-8 locale when the process has none.

    Outside a UTF-8 locale, tmux replaces non-ASCII characters in format output
    with ``_``, which breaks libtmux's parsing of the ``␞`` separator. As in
    PEP 538, only LC_CTYPE is set unless LC_ALL overrides it.

    Return the variable that was set and its previous value, or None.
    """
    if has_utf8_locale(os.environ):
        return None
    # libtmux decodes tmux output with the encoding Python picked at startup,
    # so a locale set now cannot help if that encoding is not UTF-8.
    encoding = locale.getpreferredencoding(False)
    if codecs.lookup(encoding).name != "utf-8":
        logger.warning(
            "tmux needs a UTF-8 locale, but Python decodes %s; start the "
            "process with a UTF-8 locale or PYTHONUTF8=1",
            encoding,
        )
        return None
    name = "LC_ALL" if os.environ.get("LC_ALL") else "LC_CTYPE"
    previous = os.environ.get(name)
    os.environ[name] = UTF8_FALLBACK_LOCALE
    logger.warning(
        "No UTF-8 locale set; using %s=%s for tmux", name, UTF8_FALLBACK_LOCALE
    )
    return name, previous
