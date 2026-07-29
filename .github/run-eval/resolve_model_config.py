"""Compatibility shim for model resolution now owned by OpenHands/evaluation.

The model registry was moved to OpenHands/evaluation. This file exists only so
base-branch `pull_request_target` workflows that still import this path can run
against PR branches while the workflow migration is in flight.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import ModuleType
from typing import Any


_REPO = "https://github.com/OpenHands/evaluation.git"
_RELATIVE_RESOLVER = Path("eval-job/model-config/resolve_model_config.py")
_FALLBACK_REF = "feat/port-model-resolution"


def _candidate_refs() -> list[str]:
    refs = [os.environ.get("EVALUATION_MODEL_CONFIG_REF", "main"), _FALLBACK_REF]
    seen = set()
    return [ref for ref in refs if ref and not (ref in seen or seen.add(ref))]


def _checkout_resolver(ref: str, target: Path) -> Path:
    if target.exists():
        shutil.rmtree(target)
    subprocess.run(
        [
            "git",
            "clone",
            "--depth",
            "1",
            "--filter=blob:none",
            "--sparse",
            "--branch",
            ref,
            _REPO,
            str(target),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    subprocess.run(
        ["git", "-C", str(target), "sparse-checkout", "set", "eval-job/model-config"],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    resolver = target / _RELATIVE_RESOLVER
    if not resolver.exists():
        raise FileNotFoundError(resolver)
    return resolver


def _fallback_resolver() -> ModuleType:
    module = ModuleType("_fallback_resolve_model_config")

    def find_models_by_id(model_ids: list[str]) -> list[dict[str, Any]]:
        return [
            {
                "id": model_id,
                "display_name": model_id,
                "llm_config": {"model": f"litellm_proxy/{model_id}"},
            }
            for model_id in model_ids
        ]

    module.find_models_by_id = find_models_by_id  # type: ignore[attr-defined]
    module.MODELS = {}  # type: ignore[attr-defined]
    return module


def _load_evaluation_resolver() -> ModuleType:
    base = Path(os.environ.get("RUNNER_TEMP", tempfile.gettempdir()))
    for ref in _candidate_refs():
        try:
            resolver = _checkout_resolver(ref, base / "evaluation-model-config")
            spec = importlib.util.spec_from_file_location(
                "_evaluation_resolve_model_config", resolver
            )
            if spec is None or spec.loader is None:
                raise ImportError(f"Could not load resolver from {resolver}")
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            return module
        except Exception:
            continue
    return _fallback_resolver()


_resolver = _load_evaluation_resolver()


def __getattr__(name: str) -> Any:
    return getattr(_resolver, name)
