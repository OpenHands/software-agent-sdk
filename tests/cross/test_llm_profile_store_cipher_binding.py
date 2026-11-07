"""Every LLM profile store in package code is bound to its cipher.

Profiles are encrypted at rest under the server key. A store built without the
cipher, or a ``load`` that forgot to pass it, hands the Fernet ciphertext to the
provider as the API key (#4558): that happened once per call site, so the
cipher now lives on the store and this test keeps it there.

* ``LLMProfileStore(...)`` must state ``cipher=`` (``cipher=None`` is a valid,
  visible decision for a store that never reads a secret).
* ``load`` / ``save`` / ``resolve_provider_connection`` must not take a
  per-call ``cipher=``; that argument is deprecated and is how the bug spread.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOTS = [
    REPO_ROOT / "openhands-sdk" / "openhands",
    REPO_ROOT / "openhands-agent-server" / "openhands",
    REPO_ROOT / "openhands-tools" / "openhands",
    REPO_ROOT / "openhands-workspace" / "openhands",
]
PER_CALL_CIPHER_METHODS = {"load", "save", "resolve_provider_connection"}


def _modules() -> list[Path]:
    return sorted(path for root in PACKAGE_ROOTS for path in root.rglob("*.py"))


def _passes_cipher(call: ast.Call) -> bool:
    return any(keyword.arg == "cipher" for keyword in call.keywords)


def _violations(tree: ast.AST) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "LLMProfileStore":
            if not _passes_cipher(node):
                found.append((node.lineno, "LLMProfileStore(...) without cipher="))
        elif (
            isinstance(func, ast.Attribute)
            and func.attr in PER_CALL_CIPHER_METHODS
            and _passes_cipher(node)
        ):
            found.append(
                (node.lineno, f".{func.attr}(cipher=...): bind it on the store")
            )
    return found


def test_package_modules_are_scanned():
    assert len(_modules()) > 100


@pytest.mark.parametrize(
    "path", _modules(), ids=lambda path: str(path.relative_to(REPO_ROOT))
)
def test_llm_profile_stores_are_bound_to_their_cipher(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    violations = _violations(tree)
    assert not violations, "\n".join(
        f"{path.relative_to(REPO_ROOT)}:{line}: {message}"
        for line, message in violations
    )


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("LLMProfileStore()", 1),
        ("LLMProfileStore(base_dir)", 1),
        ("LLMProfileStore(base_dir, cipher=None)", 0),
        ("LLMProfileStore(cipher=cipher).load('x')", 0),
        ("store.load('x', cipher=cipher)", 1),
        ("store.save('x', llm, include_secrets=True, cipher=cipher)", 1),
        ("store.resolve_provider_connection(llm, cipher=cipher)", 1),
        ("store.load('x', strict=True)", 0),
    ],
)
def test_the_scanner_flags_unbound_stores(source: str, expected: int):
    assert len(_violations(ast.parse(source))) == expected
