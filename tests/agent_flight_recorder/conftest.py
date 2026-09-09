import os
from pathlib import Path

import pytest
from flight_recorder.models.database import TraceIndex


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture
def trace_index(tmp_path: Path) -> TraceIndex:
    return TraceIndex(tmp_path / "index.db")
