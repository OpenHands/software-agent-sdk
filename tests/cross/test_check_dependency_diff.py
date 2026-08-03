"""Tests for the supply-chain dependency-diff release check.

Exercises the pure source-classification helpers — the part that decides
whether a resolved dependency is trusted (PyPI / first-party) or must block a
release (mirror, alternate index, git/url, or an unrecognized shape). No
network: OSV and GitHub are never touched here.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_script_module(name: str):
    repo_root = Path(__file__).resolve().parents[2]
    scripts_dir = repo_root / ".github" / "scripts"
    # The check scripts import their sibling ``security_scan_common``; make it
    # importable by putting the scripts dir on the path before executing.
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    script_path = scripts_dir / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, script_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_dep = _load_script_module("check_dependency_diff")

_is_trusted_registry = _dep._is_trusted_registry
_registry_host = _dep._registry_host
_source_label = _dep._source_label


# --- trusted sources ------------------------------------------------------


def test_canonical_pypi_registry_is_trusted():
    assert _is_trusted_registry({"registry": "https://pypi.org/simple"}) is True


def test_files_pythonhosted_is_trusted():
    assert (
        _is_trusted_registry({"registry": "https://files.pythonhosted.org/x"}) is True
    )


def test_first_party_workspace_members_are_trusted():
    assert _is_trusted_registry({"editable": "."}) is True
    assert _is_trusted_registry({"virtual": "."}) is True


# --- sources that must block ----------------------------------------------


def test_mirror_registry_blocks():
    # An alternate index / mirror is not the canonical host -> not trusted.
    assert _is_trusted_registry({"registry": "https://mirror.internal/simple"}) is False


def test_lookalike_host_is_not_trusted():
    # The classic substring-match bypass must not slip through.
    assert (
        _is_trusted_registry({"registry": "https://pypi.org.evil.example/simple"})
        is False
    )


def test_git_source_blocks():
    assert _is_trusted_registry({"git": "https://github.com/foo/bar"}) is False


def test_url_source_blocks():
    assert _is_trusted_registry({"url": "https://example.com/pkg.whl"}) is False


def test_missing_or_empty_source_is_not_trusted():
    # Deliberate: an unknown/empty source shape fails closed (blocks) rather
    # than being waved through as a default-registry package.
    assert _is_trusted_registry({}) is False
    assert _is_trusted_registry(None) is False
    assert _is_trusted_registry("weird") is False


# --- helper: registry host extraction -------------------------------------


def test_registry_host_extracts_and_lowercases():
    assert _registry_host({"registry": "https://PyPI.org/simple"}) == "pypi.org"


def test_registry_host_none_for_non_registry():
    assert _registry_host({"git": "https://x"}) is None
    assert _registry_host({}) is None


# --- helper: readable source label ----------------------------------------


def test_source_label_prefers_specific_keys():
    assert _source_label({"git": "https://github.com/foo/bar"}).startswith("git:")
    assert _source_label({"url": "https://x/pkg.whl"}).startswith("url:")
    assert _source_label({"registry": "https://pypi.org/simple"}).startswith(
        "registry:"
    )


def test_source_label_handles_non_dict():
    assert _source_label("plain") == "plain"
