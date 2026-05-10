"""Tests for the SDK seatbelt utility module."""

from unittest.mock import patch

import pytest

from openhands.sdk.utils.seatbelt import (
    SANDBOX_EXEC_BIN,
    default_profile,
    is_seatbelt_supported,
    wrap_with_sandbox_exec,
)


def test_default_profile_includes_workspace_subpath():
    profile = default_profile("/Users/me/work")
    assert '(subpath "/Users/me/work")' in profile
    # Standard temp dirs are always allowed for writes.
    assert '(subpath "/tmp")' in profile
    assert '(subpath "/private/tmp")' in profile
    # Reads everywhere, network on, fork/exec allowed.
    assert "(allow file-read*)" in profile
    assert "(allow network*)" in profile
    assert "(allow process-fork)" in profile


def test_default_profile_rejects_double_quote_in_workspace():
    # A double-quote would terminate the TinyScheme string and produce an
    # invalid profile, so the helper rejects it explicitly rather than
    # silently emitting a broken profile.
    with pytest.raises(ValueError, match="double-quote"):
        default_profile('/some"path')


def test_wrap_with_sandbox_exec_uses_default_profile():
    argv = wrap_with_sandbox_exec(["/bin/bash", "-i"], "/Users/me/work")
    assert argv[-2:] == ["/bin/bash", "-i"]
    assert argv[0] in {"/usr/bin/sandbox-exec", SANDBOX_EXEC_BIN}
    assert argv[1] == "-p"
    assert '(subpath "/Users/me/work")' in argv[2]


def test_wrap_with_sandbox_exec_accepts_explicit_profile():
    argv = wrap_with_sandbox_exec(
        ["echo", "hi"], "/work", profile="(version 1)\n(allow default)"
    )
    assert argv[2] == "(version 1)\n(allow default)"
    assert argv[-2:] == ["echo", "hi"]


def test_is_seatbelt_supported_requires_darwin():
    # On non-Darwin hosts the helper short-circuits, regardless of whether
    # `sandbox-exec` happens to exist on PATH.
    with patch("openhands.sdk.utils.seatbelt.platform.system", return_value="Linux"):
        assert is_seatbelt_supported() is False


def test_is_seatbelt_supported_requires_sandbox_exec():
    with (
        patch("openhands.sdk.utils.seatbelt.platform.system", return_value="Darwin"),
        patch("openhands.sdk.utils.seatbelt.shutil.which", return_value=None),
        patch("openhands.sdk.utils.seatbelt._binary_exists", return_value=False),
    ):
        assert is_seatbelt_supported() is False


def test_is_seatbelt_supported_when_available():
    with (
        patch("openhands.sdk.utils.seatbelt.platform.system", return_value="Darwin"),
        patch(
            "openhands.sdk.utils.seatbelt.shutil.which",
            return_value="/usr/bin/sandbox-exec",
        ),
    ):
        assert is_seatbelt_supported() is True
