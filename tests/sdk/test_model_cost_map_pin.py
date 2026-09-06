"""The LiteLLM model database is pinned to an immutable commit."""

import os
import re
import subprocess
import sys

from openhands.sdk.model_cost_map_pin import (
    MODEL_COST_MAP_COMMIT,
    MODEL_COST_MAP_URL,
    apply_model_cost_map_pin,
)


_ENV_VAR = "LITELLM_MODEL_COST_MAP_URL"


def test_pin_is_a_commit_sha_not_a_branch():
    """A branch name is not a pin — it moves under us, which is the whole
    failure this module exists to prevent (#4877, #4880)."""
    assert re.fullmatch(r"[0-9a-f]{40}", MODEL_COST_MAP_COMMIT), (
        f"{MODEL_COST_MAP_COMMIT!r} is not a full commit SHA; a branch or tag "
        "is mutable and does not pin anything"
    )
    assert MODEL_COST_MAP_COMMIT in MODEL_COST_MAP_URL
    assert "/main/" not in MODEL_COST_MAP_URL


def test_applies_the_pin_when_the_environment_is_silent(monkeypatch):
    monkeypatch.delenv(_ENV_VAR, raising=False)

    apply_model_cost_map_pin()

    assert os.environ[_ENV_VAR] == MODEL_COST_MAP_URL


def test_an_explicit_url_wins(monkeypatch):
    """Operators who want the live map, or an internal mirror, keep it."""
    monkeypatch.setenv(_ENV_VAR, "https://mirror.internal/model_prices.json")

    apply_model_cost_map_pin()

    assert os.environ[_ENV_VAR] == "https://mirror.internal/model_prices.json"


def test_importing_the_sdk_pins_the_map_litellm_actually_loads():
    """The end-to-end property, in a fresh interpreter.

    LiteLLM resolves the URL once at its own import, so the pin only works if
    it lands first. Asserting on the module in isolation would pass even if
    `openhands.sdk.__init__` imported it too late to matter.
    """
    source = (
        "import openhands.sdk\n"
        "from litellm.litellm_core_utils.get_model_cost_map import "
        "get_model_cost_map_source_info as s\n"
        "print(s()['url'])\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", source],
        capture_output=True,
        text=True,
        timeout=180,
        env={"PATH": "/usr/bin:/bin", "OPENHANDS_SUPPRESS_BANNER": "1"},
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert MODEL_COST_MAP_COMMIT in result.stdout, (
        f"litellm loaded {result.stdout.strip()!r}, not the pinned commit — "
        "something imports litellm before openhands.sdk applies the pin"
    )
