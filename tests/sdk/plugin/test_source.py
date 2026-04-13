"""Tests that deprecated plugin.source imports still work.

The canonical tests live in ``tests/sdk/extensions/test_source.py``.
This file verifies backward compatibility of the deprecated re-export.
"""

from openhands.sdk.extensions.source import (
    is_local_path as canonical_is_local_path,
    parse_github_url as canonical_parse_github_url,
    resolve_source_path as canonical_resolve_source_path,
    validate_source_path as canonical_validate_source_path,
)


def test_deprecated_reexports_resolve_to_canonical():
    """Importing from plugin.source still returns the same objects."""
    from openhands.sdk.plugin.source import (
        is_local_path,
        parse_github_url,
        resolve_source_path,
        validate_source_path,
    )

    assert is_local_path is canonical_is_local_path
    assert parse_github_url is canonical_parse_github_url
    assert resolve_source_path is canonical_resolve_source_path
    assert validate_source_path is canonical_validate_source_path
