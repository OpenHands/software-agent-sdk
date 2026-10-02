"""Encoding management for file operations."""

import codecs
import functools
import inspect
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

import charset_normalizer
from cachetools import LRUCache


if TYPE_CHECKING:
    from openhands.tools.file_editor.impl import FileEditor


# Control bytes that never appear in ordinary text files.
BINARY_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0e-\x1f\x7f]")

# Candidate codecs for whole-file decoding when the detector cannot resolve a
# file's encoding. Short non-UTF-8 files fall below the detector's confidence
# threshold and are reported as the default encoding, so the detected value
# alone is not enough to recover them. Ordered most to least likely for source
# files; the detector's own guess is tried first when it is one of these.
TEXT_DECODE_CANDIDATES = ("utf-8", "cp1252", "latin-1", "cp1251")

# charset_normalizer reports some codecs under IANA names; map them to the
# Python codec names used above so their guesses decode correctly.
TEXT_DECODE_ALIASES = {
    "windows-1251": "cp1251",
    "windows-1252": "cp1252",
    "iso-8859-1": "latin-1",
    "iso-8859-15": "latin-1",
}


class EncodingManager:
    """Manages file encodings across multiple operations to ensure consistency."""

    # Default maximum number of entries in the cache
    DEFAULT_MAX_CACHE_SIZE: int = 1000  # ~= 300 KB
    default_encoding: str
    confidence_threshold: float

    def __init__(self, max_cache_size=None):
        # Cache detected encodings to avoid repeated detection on the same file
        # Format: {path_str: (encoding, mtime)}
        self._encoding_cache: LRUCache[str, tuple[str, float]] = LRUCache(
            maxsize=max_cache_size or self.DEFAULT_MAX_CACHE_SIZE
        )
        # Default fallback encoding
        self.default_encoding = "utf-8"
        # Confidence threshold for encoding detection
        self.confidence_threshold = 0.9

    def detect_encoding(self, path: Path) -> str:
        """Detect the encoding of a file without handling caching logic.
        Args:
            path: Path to the file
        Returns:
            The detected encoding or default encoding if detection fails
        """
        # Handle non-existent files
        if not path.exists():
            return self.default_encoding

        # Read a sample of the file to detect encoding
        sample_size = min(os.path.getsize(path), 1024 * 1024)  # Max 1MB sample
        with open(path, "rb") as f:
            raw_data = f.read(sample_size)

        if raw_data.startswith(codecs.BOM_UTF8):
            return "utf-8-sig"

        try:
            raw_data.decode(self.default_encoding)
        except UnicodeDecodeError:
            pass
        else:
            return self.default_encoding

        # Use charset_normalizer instead of chardet
        results = charset_normalizer.detect(raw_data)

        # Get the best match if any exists
        if (
            results
            and results["confidence"]
            and results["confidence"] > self.confidence_threshold
            and results["encoding"]
        ):
            encoding = results["encoding"]
            # Always use utf-8 instead of ascii for text files to support
            # non-ASCII characters. This ensures files initially containing only
            # ASCII can later accept non-ASCII content
            if encoding.lower() == "ascii":
                encoding = self.default_encoding
        else:
            encoding = self.default_encoding

        return encoding

    def _best_guess_encoding(self, path: Path) -> str | None:
        """Return charset_normalizer's guess when it is a codec we trust.

        Short non-UTF-8 files score below ``confidence_threshold`` even when
        the guess is correct, so the raw guess is worth trying — but only when
        it names a common codec, since the detector also proposes exotic
        candidates (e.g. cp1006 for latin-1 text) that decode to mojibake.
        """
        try:
            sample_size = min(os.path.getsize(path), 1024 * 1024)
            with open(path, "rb") as f:
                raw_data = f.read(sample_size)
        except OSError:
            return None
        guess = charset_normalizer.detect(raw_data) or {}
        encoding = guess.get("encoding")
        if not encoding:
            return None
        normalized = str(encoding).lower().replace("_", "-")
        if normalized in TEXT_DECODE_ALIASES:
            return TEXT_DECODE_ALIASES[normalized]
        return normalized if normalized in TEXT_DECODE_CANDIDATES else None

    def _recover_encoding(self, path: Path) -> str | None:
        """Return a codec that decodes ``path`` to control-byte-free text."""
        candidates = (self._best_guess_encoding(path), *TEXT_DECODE_CANDIDATES)
        for candidate in dict.fromkeys(c for c in candidates if c):
            try:
                text = path.read_text(encoding=candidate)
            except (OSError, UnicodeDecodeError, LookupError):
                continue
            if BINARY_CONTROL_CHARS.search(text) is None:
                return candidate
        return None

    def resolve_text_encoding(self, path: Path) -> str | None:
        """Return an encoding that decodes ``path`` to clean text, else None."""
        encoding = self.get_encoding(path)
        try:
            text = path.read_text(encoding=encoding)
        except (UnicodeDecodeError, LookupError):
            return None
        return encoding if BINARY_CONTROL_CHARS.search(text) is None else None

    def get_encoding(self, path: Path) -> str:
        """Get encoding for a file, using cache or detecting if necessary.
        Args:
            path: Path to the file
        Returns:
            The encoding for the file
        """
        path_str = str(path)
        # If file doesn't exist, return default encoding
        if not path.exists():
            return self.default_encoding

        # Get current modification time
        current_mtime = os.path.getmtime(path)

        # Check cache for valid entry
        if path_str in self._encoding_cache:
            cached_encoding, cached_mtime = self._encoding_cache[path_str]
            if cached_mtime == current_mtime:
                return cached_encoding

        # No valid cache entry, detect encoding
        encoding = self.detect_encoding(path)

        # The detector reports the default encoding for short non-UTF-8 files
        # that fall below its confidence threshold. Recover a decodable
        # encoding so reads and validation agree on the same codec.
        if encoding == self.default_encoding:
            recovered = self._recover_encoding(path)
            if recovered is not None:
                encoding = recovered

        # Cache the result with current modification time
        self._encoding_cache[path_str] = (encoding, current_mtime)
        return encoding


def with_encoding(method):
    """Decorator to handle file encoding for file operations.
    This decorator automatically detects and applies the correct encoding
    for file operations, ensuring consistency between read and write operations.
    Args:
        method: The method to decorate
    Returns:
        The decorated method
    """

    @functools.wraps(method)
    def wrapper(self: "FileEditor", path: Path, *args, **kwargs):
        # Skip encoding handling for directories
        if path.is_dir():
            return method(self, path, *args, **kwargs)

        # Check if the method accepts an encoding parameter
        sig = inspect.signature(method)
        accepts_encoding = "encoding" in sig.parameters

        if accepts_encoding:
            # For files that don't exist yet (like in 'create' command),
            # use the default encoding
            if not path.exists():
                if "encoding" not in kwargs:
                    kwargs["encoding"] = self._encoding_manager.default_encoding
            else:
                # Get encoding from the encoding manager for existing files
                encoding = self._encoding_manager.get_encoding(path)
                # Add encoding to kwargs if the method accepts it
                if "encoding" not in kwargs:
                    kwargs["encoding"] = encoding

        return method(self, path, *args, **kwargs)

    return wrapper
