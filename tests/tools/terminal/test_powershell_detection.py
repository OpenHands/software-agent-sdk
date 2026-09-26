"""PowerShell detection must not depend on the user's PowerShell profile.

The detection probe launches PowerShell to check availability. If it loads the
user's profile, a profile that runs e.g. ``conda`` init can take several seconds
to start -- exceeding the probe timeout -- so detection wrongly concludes
PowerShell is unavailable and the agent cannot run any command. The probe must
use ``-NoProfile`` (matching how ``WindowsTerminal`` actually launches the
shell).
"""

import subprocess
from unittest.mock import patch

from openhands.tools.terminal.terminal import factory


class _FakeCompleted:
    def __init__(self, returncode: int = 0):
        self.returncode = returncode
        self.stdout = "PowerShell Available"
        self.stderr = ""


def test_detection_probe_uses_no_profile():
    """The availability probe must pass ``-NoProfile`` so a slow user profile
    cannot make PowerShell look unavailable."""
    calls: list[list[str]] = []

    def _fake_run(cmd, *args, **kwargs):
        calls.append(cmd)
        return _FakeCompleted(returncode=0)

    with (
        patch.object(factory.platform, "system", return_value="Windows"),
        patch.object(factory.subprocess, "run", _fake_run),
    ):
        resolved = factory._get_powershell_command()

    assert resolved is not None
    assert calls, "detection probe was never invoked"
    first = calls[0]
    assert "-NoProfile" in first
    # The probe should run before any positional command argument.
    assert first.index("-NoProfile") < first.index("-Command")


def test_detection_timeout_is_treated_as_unavailable():
    """A probe that times out should be skipped, not crash detection."""

    def _fake_run(cmd, *args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout", 0))

    with (
        patch.object(factory.platform, "system", return_value="Windows"),
        patch.object(factory.subprocess, "run", _fake_run),
    ):
        assert factory._get_powershell_command() is None
