"""Property test: a credential in an http(s) clone URL never reaches the output.

For any http or https URL (scheme in any letter case, since schemes are
case-insensitive) carrying a token, with or without a ``user:`` prefix, the
token appears in none of: a failed ``run_git_command``'s error message,
``GitCommandError`` fields and log records, or the dumps of a
``PluginSource`` and its ``ResolvedPluginSource``.
"""

import logging
import string
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from unittest.mock import patch

import pytest
from hypothesis import given, strategies as st

from openhands.sdk.git import utils as git_utils
from openhands.sdk.git.exceptions import GitCommandError
from openhands.sdk.plugin.types import PluginSource, ResolvedPluginSource


def any_case(word: str) -> st.SearchStrategy[str]:
    letters = [st.sampled_from([c.lower(), c.upper()]) for c in word]
    return st.tuples(*letters).map("".join)


SCHEMES = st.sampled_from(["http", "https"]).flatmap(any_case)
USERS = st.just("") | st.from_regex(r"[a-z0-9-]{1,12}:", fullmatch=True)
TOKENS = st.text(
    alphabet=string.ascii_letters + string.digits + "_", min_size=12, max_size=24
)


class _MessageCollector(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(self.format(record))


@contextmanager
def logged_messages(logger: logging.Logger) -> Iterator[list[str]]:
    collector = _MessageCollector()
    previous_level = logger.level
    logger.addHandler(collector)
    logger.setLevel(logging.DEBUG)
    try:
        yield collector.messages
    finally:
        logger.removeHandler(collector)
        logger.setLevel(previous_level)


@given(scheme=SCHEMES, user=USERS, token=TOKENS)
def test_url_credential_appears_on_no_output(
    scheme: str, user: str, token: str
) -> None:
    url = f"{scheme}://{user}{token}@git.example.com/org/repo.git"
    git_echoes_url = subprocess.CompletedProcess(
        args=[],
        returncode=128,
        stdout="",
        stderr=f"fatal: unable to access '{url}/': The requested URL returned 403\n",
    )
    with (
        logged_messages(git_utils.logger) as log_messages,
        patch("subprocess.run", return_value=git_echoes_url),
        pytest.raises(GitCommandError) as exc_info,
    ):
        git_utils.run_git_command(["git", "clone", url, "repo"])

    source = PluginSource(source=url)
    resolved = ResolvedPluginSource.from_plugin_source(source, "abc123")
    error = exc_info.value
    outputs = {
        "str(error)": str(error),
        "error.command": " ".join(error.command),
        "error.stderr": error.stderr,
        "logs": "\n".join(log_messages),
        "PluginSource json": source.model_dump_json(),
        "PluginSource dict": str(source.model_dump()),
        "ResolvedPluginSource json": resolved.model_dump_json(),
        "ResolvedPluginSource dict": str(resolved.model_dump()),
    }
    leaks = {where: text for where, text in outputs.items() if token in text}
    assert not leaks
