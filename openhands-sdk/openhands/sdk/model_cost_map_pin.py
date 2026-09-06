"""Pin the LiteLLM model database to an immutable commit.

LiteLLM resolves ``model_cost_map_url`` at import time and, left at its
default, fetches ``model_prices_and_context_window.json`` from the tip of
``BerriAI/litellm@main``. Pinning ``litellm`` in ``uv.lock`` therefore pins its
*code* and not the data that code reads: the same build answers capability and
pricing questions differently on different days, and an unreviewed upstream
merge reaches every process on its next import.

A branch name is not a pin — only a commit SHA is immutable (the tj-actions
tag-rewrite attack is the standing example). :data:`MODEL_COST_MAP_URL` is
therefore SHA-addressed, and moving it is an ordinary reviewable diff.

Deliberately ``setdefault``: an operator who wants the live map, or an internal
mirror, sets ``LITELLM_MODEL_COST_MAP_URL`` and this stays out of the way.

See OpenHands/software-agent-sdk#4880 and #4308.
"""

from __future__ import annotations

import os


MODEL_COST_MAP_COMMIT = "42d8360f297db9057c205ea0281f15a7c2e4f67e"
"""Pinned ``BerriAI/litellm`` commit supplying the model database.

Bumping this is a data update: it can change context windows, pricing and
capability flags, so the capability suites in ``tests/sdk/llm`` are the
regression net for it. Pick a commit old enough to clear the repository's
supply-chain freshness window rather than the newest one available.
"""

MODEL_COST_MAP_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/"
    f"{MODEL_COST_MAP_COMMIT}/model_prices_and_context_window.json"
)

_ENV_VAR = "LITELLM_MODEL_COST_MAP_URL"


def apply_model_cost_map_pin() -> None:
    """Point LiteLLM at the pinned map, unless the environment already says.

    Must run before anything imports ``litellm``: the URL is read once, at
    LiteLLM's own import, so a later assignment silently does nothing.
    """
    os.environ.setdefault(_ENV_VAR, MODEL_COST_MAP_URL)


# Applied on import rather than by a call in ``openhands.sdk.__init__``: twenty
# modules across the package import litellm, so the package __init__ is the
# only reliable point, and a statement between its imports would push every
# one of them past E402.
apply_model_cost_map_pin()
