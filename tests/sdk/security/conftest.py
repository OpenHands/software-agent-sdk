"""Load the defense-in-depth example module for test discovery.

The example file starts with a digit (45_...), making it unimportable
via normal Python import. This conftest loads it once at collection time
so both test_defense_in_depth.py and test_defense_in_depth_adversarial.py
can reference ``sys.modules["defense_in_depth"]`` without duplicating
the importlib boilerplate.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


_EXAMPLE_FILE = (
    Path(__file__).resolve().parents[3]
    / "examples"
    / "01_standalone_sdk"
    / "45_defense_in_depth_security.py"
)

if "defense_in_depth" not in sys.modules:
    _spec = importlib.util.spec_from_file_location("defense_in_depth", _EXAMPLE_FILE)
    assert _spec is not None and _spec.loader is not None
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules["defense_in_depth"] = _mod
    _spec.loader.exec_module(_mod)
