import os
from pathlib import Path


# Rebuildable caches under $HOME: XDG tools (uv, pip, yarn, go) use .cache,
# npm ignores XDG and uses .npm.
CACHE_DIRS = (".cache", ".npm")


def caches(home: Path) -> list[Path]:
    # lexists: a symlinked cache is still discarded (unlinked, never followed).
    return [home / name for name in CACHE_DIRS if os.path.lexists(home / name)]
