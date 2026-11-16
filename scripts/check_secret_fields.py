"""Keep secret handling on ``SecretField`` and the typed secret contexts.

Rejects, outside ``openhands/sdk/utils/pydantic_secrets.py``:

- any reference to ``serialize_secret``, ``validate_secret`` or
  ``validate_secret_dict``: a secret field is declared with ``SecretField``,
  which installs its serializer and validator together;
- a dict literal or item assignment keyed by ``"cipher"``, ``"expose_secrets"``
  or ``"strict_decrypt"``: build the context with ``cipher_context`` or
  ``client_context``. HTTP query parameters (``params={...}``) are exempt.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path


OWNER = Path("openhands-sdk/openhands/sdk/utils/pydantic_secrets.py")
HELPERS = {"serialize_secret", "validate_secret", "validate_secret_dict"}
CONTEXT_KEYS = {"cipher", "expose_secrets", "strict_decrypt"}


def _is_context_key(node: ast.expr | None) -> bool:
    return isinstance(node, ast.Constant) and node.value in CONTEXT_KEYS


def _query_param_dicts(tree: ast.AST) -> set[int]:
    return {
        id(keyword.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        for keyword in node.keywords
        if keyword.arg == "params" and isinstance(keyword.value, ast.Dict)
    }


def violations(path: Path) -> list[tuple[int, str]]:
    """Return ``(lineno, message)`` for each violation in ``path``."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    exempt = _query_param_dicts(tree)
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name in HELPERS:
                    found.append((node.lineno, f"imports {alias.name}"))
        elif isinstance(node, ast.Name) and node.id in HELPERS:
            found.append((node.lineno, f"uses {node.id}"))
        elif isinstance(node, ast.Attribute) and node.attr in HELPERS:
            found.append((node.lineno, f"uses {node.attr}"))
        elif isinstance(node, ast.Dict) and id(node) not in exempt:
            if any(_is_context_key(key) for key in node.keys):
                found.append((node.lineno, "builds a secret context by hand"))
        elif isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store):
            if _is_context_key(node.slice):
                found.append((node.lineno, "builds a secret context by hand"))
    return sorted(found)


def main(argv: list[str]) -> int:
    failures = 0
    for name in argv:
        path = Path(name)
        if path.suffix != ".py" or path.as_posix().endswith(OWNER.as_posix()):
            continue
        for lineno, message in violations(path):
            print(
                f"{name}:{lineno}: {message}; declare secret fields with "
                "SecretField and build contexts with cipher_context / "
                "client_context (openhands.sdk.utils.pydantic_secrets)"
            )
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
