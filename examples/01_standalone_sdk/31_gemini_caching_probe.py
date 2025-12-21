"""
Probe Gemini caching behavior via:
- Implicit caching on Vertex through the LiteLLM proxy (LITELLM_API_KEY)
- Explicit caching on Gemini API (AI Studio) using google-genai (GEMINI_API_KEY)

It writes Telemetry logs under logs/caching for the implicit path using the SDK LLM.

Usage:
  uv run python examples/01_standalone_sdk/31_gemini_caching_probe.py --mode implicit
  uv run python examples/01_standalone_sdk/31_gemini_caching_probe.py --mode explicit

Notes:
- For implicit (Vertex): set LITELLM_API_KEY in env. We'll call gemini-3-pro-preview.
- For explicit (AI Studio): set GEMINI_API_KEY in env. We'll create a short-lived
  cache and reference it.
"""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

from pydantic import SecretStr

from openhands.sdk.llm import LLM
from openhands.sdk.llm.message import Message, TextContent
from openhands.sdk.logger import ENV_LOG_DIR


LOG_DIR = os.path.join(ENV_LOG_DIR, "caching")


def run_implicit_via_proxy() -> None:
    api_key = os.getenv("LITELLM_API_KEY")
    if not api_key:
        raise SystemExit(
            "LITELLM_API_KEY is required for implicit mode (Vertex via proxy)"
        )

    # Use Gemini 3 Pro preview; see /v1/models on the proxy for exact names
    model = "gemini-3-pro-preview"
    base_url = "https://llm-proxy.eval.all-hands.dev"

    long_prefix = "\n".join(
        [
            (
                "You are a policy analyzer. The following policy is stable across"
                " requests."
            ),
            "".join(
                ["A" * 2000]
            ),  # Ensure request comfortably exceeds 2,048 tokens overall
            "When asked later questions, refer to this policy.",
        ]
    )

    llm = LLM(
        model=model,
        api_key=SecretStr(api_key),
        base_url=base_url,
        custom_llm_provider="openai",
        log_completions=True,
        log_completions_folder=LOG_DIR,
        usage_id="gemini-caching-probe-implicit",
    )

    def mk_msgs(question: str) -> list[Message]:
        return [
            Message(role="system", content=[TextContent(text=long_prefix)]),
            Message(role="user", content=[TextContent(text=question)]),
        ]

    q = "Summarize the stable policy from earlier in ~50 words."

    # First call - cache write expected
    r1 = llm.completion(mk_msgs(q))
    print("First call done. Cost=$", r1.metrics.accumulated_cost)

    # Short sleep to improve chance of hit while staying in the implicit cache horizon
    time.sleep(1.0)

    # Second call - cache read expected
    r2 = llm.completion(mk_msgs(q))
    print("Second call done. Total cost=$", r2.metrics.accumulated_cost)
    print(
        "Inspect logs in",
        LOG_DIR,
        "for usage_summary.cache_read_tokens > 0 on the second call.",
    )


def run_explicit_via_aistudio() -> None:
    """Create a short-lived explicit cache using google-genai and consume it."""
    gemini_key = os.getenv("GEMINI_API_KEY")
    if not gemini_key:
        raise SystemExit("GEMINI_API_KEY is required for explicit mode (AI Studio)")

    # Import locally to avoid dependency for implicit only runs
    from google import genai  # type: ignore
    from google.genai import types  # type: ignore

    client = genai.Client(api_key=gemini_key)

    model = "gemini-2.5-flash"

    large_text = "Stable corpus for caching.\n" + (
        "Lorem ipsum dolor sit amet, consectetur adipiscing elit. " * 200
    )

    cache = client.caches.create(
        model=model,
        config=types.CreateCachedContentConfig(
            display_name="OH explicit cache demo",
            system_instruction=(
                "You are an expert summarizer; answer only using the provided"
                " cached corpus."
            ),
            contents=[types.Content(role="user", parts=[types.Part(text=large_text)])],
            ttl="300s",
        ),
    )
    print("Created cache:", cache.name)
    print("Cache expire_time:", getattr(cache, "expire_time", None))

    resp = client.models.generate_content(
        model=model,
        contents="Summarize the cached corpus in one paragraph.",
        config=types.GenerateContentConfig(cached_content=cache.name),
    )

    # Best-effort print usage metadata
    usage = getattr(resp, "usage_metadata", None)
    print("Response text:\n", getattr(resp, "text", "<no text>"))
    print("Usage metadata:", usage)


def main() -> None:
    Path(LOG_DIR).mkdir(parents=True, exist_ok=True)
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["implicit", "explicit"], required=True)
    args = parser.parse_args()

    if args.mode == "implicit":
        run_implicit_via_proxy()
    else:
        run_explicit_via_aistudio()


if __name__ == "__main__":
    main()
