"""Codebase search tools powered by Morph's WarpGrep.

Provides natural-language code search via the ``@morphllm/morphmcp`` MCP server.
Two tools are registered:

- ``codebase_search`` — search a local repository
- ``github_codebase_search`` — search a public GitHub repository

Requires ``MORPH_API_KEY`` (get one at https://morphllm.com/dashboard/api-keys)
and Node.js 18+ (for the MCP server process).
"""

from openhands.tools.codebase_search.definition import register_codebase_search_tools

__all__ = ["register_codebase_search_tools"]
