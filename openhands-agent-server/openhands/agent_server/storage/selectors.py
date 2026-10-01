"""What each tier may drop: paths a stopped runtime can rebuild."""

import os
from pathlib import Path
from typing import Final


# Rebuildable caches under $HOME: XDG tools (uv, pip, yarn, go) use .cache,
# npm ignores XDG and uses .npm.
CACHE_DIRS: Final[tuple[str, ...]] = (".cache", ".npm")


def caches(home: Path) -> list[Path]:
    # lexists: a symlinked cache is still discarded (unlinked, never followed).
    return [home / name for name in CACHE_DIRS if os.path.lexists(home / name)]
