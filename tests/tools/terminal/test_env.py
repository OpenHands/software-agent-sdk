"""Tests for the tmux locale helpers in ``openhands.tools.terminal.env``."""

import locale
import os

import pytest

from openhands.tools.terminal.env import ensure_utf8_locale, has_utf8_locale


@pytest.mark.parametrize(
    ("environ", "expected"),
    [
        ({"LANG": "C.UTF-8"}, True),
        ({"LANG": "en_US.utf8"}, True),
        ({"LC_CTYPE": "UTF-8"}, True),
        ({"LC_ALL": "C", "LANG": "C.UTF-8"}, False),
        ({"LC_ALL": "", "LANG": "C.UTF-8"}, True),
        ({"LANG": "C"}, False),
        ({"LANG": "C", "TMUX": "/tmp/tmux-0/default,1,0"}, True),
        ({}, False),
    ],
)
def test_has_utf8_locale_follows_tmux(environ, expected):
    assert has_utf8_locale(environ) is expected


@pytest.mark.parametrize(
    ("inherited", "expected"),
    [
        ({"LANG": "C"}, ("LC_CTYPE", None)),
        ({"LC_CTYPE": "C"}, ("LC_CTYPE", "C")),
        ({"LC_ALL": "POSIX"}, ("LC_ALL", "POSIX")),
        ({"LANG": "en_US.UTF-8"}, None),
    ],
)
def test_ensure_utf8_locale_sets_the_variable_tmux_reads(
    monkeypatch, no_locale_env, inherited, expected
):
    for name, value in inherited.items():
        monkeypatch.setenv(name, value)

    assert ensure_utf8_locale() == expected
    assert has_utf8_locale(os.environ)


def test_ensure_utf8_locale_keeps_env_when_python_cannot_decode_utf8(
    monkeypatch, no_locale_env
):
    monkeypatch.setenv("LANG", "C")
    monkeypatch.setattr(
        locale, "getpreferredencoding", lambda do_setlocale=True: "ISO8859-1"
    )

    assert ensure_utf8_locale() is None
    assert "LC_ALL" not in os.environ
    assert "LC_CTYPE" not in os.environ
