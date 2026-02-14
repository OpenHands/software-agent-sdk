"""Runtime tools package.

This package intentionally avoids re-exporting tool classes at the top-level to
keep imports explicit and prevent accidental API growth.

The curated public surface is tracked via ``__all__`` so CI can detect breaking
changes in the published ``openhands-tools`` distribution.
"""

from importlib.metadata import PackageNotFoundError, version


try:
    __version__ = version("openhands-tools")
except PackageNotFoundError:
    __version__ = "0.0.0"  # fallback for editable/unbuilt environments


__all__ = [
    "__version__",
]
