"""Source path handling — **moved to** :mod:`openhands.sdk.extensions.source`.

.. deprecated:: 1.17.0
    Import from ``openhands.sdk.extensions.source`` instead.
    Will be removed in 1.22.0.
"""

from openhands.sdk.extensions.source import (
    DEFAULT_CACHE_DIR as DEFAULT_CACHE_DIR,
    GITHUB_URL_PATTERN as GITHUB_URL_PATTERN,
    GitHubURLComponents as GitHubURLComponents,
    is_local_path as is_local_path,
    parse_github_url as parse_github_url,
    resolve_source_path as resolve_source_path,
    validate_source_path as validate_source_path,
)
