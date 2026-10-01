#!/usr/bin/env python3
"""Reproduce the direct-routing classifier MiniMax bug end to end.

Runs the real SDK router tool (``route_task_to_model``) against a
user-supplied litellm proxy whose ``classifier_model`` is a MiniMax-backed
model group. Prints the classifier request messages and the provider
response/error, so the same script on base vs HEAD shows the bug and the fix.

Env:
  OH_PROXY_URL        litellm proxy base URL, e.g. http://127.0.0.1:4000
  OH_PROXY_KEY        virtual key with access to OH_CLASSIFIER_MODEL
  OH_CLASSIFIER_MODEL model group name on the proxy (default: minimax-m3)
  OH_TARGET_MODEL     routed target model (default: gpt-5.5)

This script writes NO secrets to stdout: the Authorization header is stripped
from any echoed request payloads.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path


# Ensure the repo's openhands.sdk is importable when run via `uv run`.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "openhands-sdk"))

from openhands.sdk import LLM, LocalConversation  # noqa: E402
from openhands.sdk.agent import Agent  # noqa: E402
from openhands.sdk.llm.meta_profile_store import (  # noqa: E402
    MetaProfile,
)
from openhands.sdk.tool.builtins import (  # noqa: E402
    ClassifyAndSwitchLLMAction,
    ClassifyAndSwitchLLMTool,
)
from openhands.sdk.tool.builtins.classify_and_switch_llm import (  # noqa: E402
    build_classifier_messages,
)


PROXY_URL = os.environ.get("OH_PROXY_URL", "").rstrip("/")
PROXY_KEY = os.environ.get("OH_PROXY_KEY", "")
CLASSIFIER_MODEL = os.environ.get("OH_CLASSIFIER_MODEL", "minimax-m3")
TARGET_MODEL = os.environ.get("OH_TARGET_MODEL", "gpt-5.5")

DIRECT_TEMPLATE = (
    "Pick one model.\n\n"
    "{{ model_table }}\n\n"
    "Task:\n{{ instance_text }}\n\n"
    'Return ONLY JSON: {"model": "<exact model name>", "reason": "<short>"}'
)

# A transcript the classifier will route. Keep it short and deterministic.
TRANSCRIPT = "user: write a unit test for the parser function"


def _check_env() -> None:
    missing = [
        k
        for k, v in {
            "OH_PROXY_URL": PROXY_URL,
            "OH_PROXY_KEY": PROXY_KEY,
        }.items()
        if not v
    ]
    if missing:
        print(f"ERROR: missing env: {', '.join(missing)}", file=sys.stderr)
        sys.exit(2)


def _make_llm(model: str, usage_id: str) -> LLM:
    return LLM(
        model=model,
        api_base=PROXY_URL,
        api_key=PROXY_KEY,
        usage_id=usage_id,
    )


def _strip_auth(payload: dict) -> dict:
    """Remove anything that looks like a secret from a captured payload."""
    redacted = json.loads(json.dumps(payload))
    if isinstance(redacted.get("headers"), dict):
        for k in list(redacted["headers"]):
            if k.lower() in {"authorization", "api-key", "x-api-key"}:
                redacted["headers"][k] = "<redacted>"
    return redacted


def main() -> int:
    _check_env()

    meta = MetaProfile.model_validate(
        {
            "classifier_model": CLASSIFIER_MODEL,
            "prompt_template": DIRECT_TEMPLATE,
            "model_table": f"- {TARGET_MODEL}",
        }
    )

    # Show the exact classifier request messages this revision builds.
    messages = build_classifier_messages(meta, TRANSCRIPT)
    print("=== classifier request messages (this revision) ===")
    for i, m in enumerate(messages):
        text = "".join(c.text for c in m.content if getattr(c, "text", None))
        print(f"[{i}] role={m.role} text={text!r}")
    print(f"roles = {[m.role for m in messages]}")
    print()

    # Build a minimal conversation with the router tool and run it.
    agent = Agent(llm=_make_llm(TARGET_MODEL, "target"), tools=[])
    conversation = LocalConversation(agent=agent, workspace=Path.cwd())
    conversation._ensure_agent_ready()

    # The classifier profile must resolve to our MiniMax-backed model group.
    real_load = conversation._profile_store.load

    def fake_load(name: str, *, cipher=None):
        if name == CLASSIFIER_MODEL:
            return _make_llm(CLASSIFIER_MODEL, "classifier")
        return real_load(name, cipher=cipher)

    conversation._profile_store.load = fake_load  # type: ignore[assignment]

    tool = ClassifyAndSwitchLLMTool.create(
        active_meta_profile=None, inline_meta_profile=meta
    )[0]
    conversation.agent.add_runtime_tools([tool])

    print("=== running route_task_to_model ===")
    try:
        obs = conversation.execute_tool(
            "route_task_to_model", ClassifyAndSwitchLLMAction()
        )
    except Exception as exc:
        print(f"EXC: {type(exc).__name__}: {exc}")
        traceback.print_exc()
        return 1

    print(f"is_error = {obs.is_error}")
    print(f"model    = {getattr(obs, 'model', None)}")
    print(f"chosen   = {getattr(obs, 'chosen_class', None)}")
    text = getattr(obs, "text", None) or str(obs)
    # Scrub the proxy key just in case it appears in an error string.
    if PROXY_KEY:
        text = text.replace(PROXY_KEY, "<redacted>")
    print(f"obs     = {text}")
    return 0 if not obs.is_error else 1


if __name__ == "__main__":
    raise SystemExit(main())
