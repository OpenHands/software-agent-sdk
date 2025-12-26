# Design: Gemini Prompt Context Caching (Vertex AI and Gemini API/AI Studio)

Author: OpenHands
Date: 2025-12-18
Target repo issue: #1427

## Goal

Identify concrete, non-automatic caching strategies we can implement for Gemini models to reduce cost and latency, beyond relying on implicit (automatic) caching. Provide verified guidance for Gemini 2.x and 3.x models on Vertex AI and Gemini API (AI Studio), and add a runnable example to verify cache behavior (TTL, cached token counts, logs).

## TL;DR (What we can do beyond implicit caching)

- Adopt explicit context caching via the official control plane APIs:
  - Vertex AI: Create and manage CachedContent resources, then reference them in generate requests.
  - Gemini API (AI Studio): Use google-genai SDK caches with a Gemini API key.
- Add SDK-level affordances so callers can:
  - Provide a cachedContent id (Vertex) or cache name (AI Studio) via kwargs/extra_body to the underlying provider when available.
  - Record cache read/write tokens in telemetry (already supported via usage fields).
- Provide guidance and utilities for cache construction:
  - Canonicalize and chunk large, stable context; upload via Files API where appropriate; attach a TTL matched to update cadence.
  - Structure prompts with large shared prefix so implicit caching still helps when explicit cache is not referenced.

## Sources and verification

Authoritative docs reviewed (Dec 2025):
- Vertex AI context caching overview: https://cloud.google.com/vertex-ai/generative-ai/docs/context-cache/context-cache-overview
- Use context cache: https://cloud.google.com/vertex-ai/generative-ai/docs/context-cache/context-cache-use
- Update context cache (TTL): https://cloud.google.com/vertex-ai/generative-ai/docs/context-cache/context-cache-update
- Model pages (e.g., Gemini 3 Flash): https://docs.cloud.google.com/vertex-ai/generative-ai/docs/models/gemini/3-flash
- Google GenAI SDK (python) reference: https://googleapis.github.io/python-genai/genai.html
- Go SDK types show CachedContentUsageMetadata, TTL, etc.: https://pkg.go.dev/google.golang.org/genai

Community/forum posts sometimes cite model-specific minima (e.g., 1024 tokens for Flash). The current official overview states a single minimum of 2,048 tokens for caching requests. We prioritize official docs when conflicts arise.

## Feature matrix (verified)

- Implicit caching:
  - Enabled by default for Vertex AI Gemini and provides ~90% discount on cached input tokens when a cache hit occurs.
  - Supported models (Vertex docs): Gemini 3 Flash (Preview), Gemini 3 Pro (Preview), Gemini 2.5 Pro, Gemini 2.5 Flash (Preview), Gemini 2.5 Flash-Lite (Preview). Aliases gemini-flash-latest, gemini-flash-lite-latest.
- Explicit caching:
  - Manual cache creation, management, and reference. Guarantees discounted rate when referencing an existing cache.
  - Discounts: 90% on Gemini 2.5+, 75% on Gemini 2.0.
  - Supported models include all implicit-supporting models plus Gemini 2.0 Flash / Flash-Lite. Docs also list Gemini 3 Flash/Pro (Preview) as supported for explicit caching.

## Limits and billing (verified)

- Minimum request size for caching eligibility: 2,048 tokens (official overview). Practical thresholds may vary by model; use this as the safe baseline.
- Upper bound: up to model context window (e.g., 2.5 Pro supports >1M tokens). File size limits follow model and upload path limits (see model pages). The prior “10 MB max” claim is not in current docs.
- Implicit cache retention: Managed by platform; caches are cleared within 24 hours, and persistence depends on recency and frequency of reuse.
- Explicit cache TTL:
  - Default expire time is 60 minutes.
  - Minimum TTL is 1 minute; official docs show extending TTL well beyond 1 hour; no documented maximum (billable storage applies while cache lives).
- Usage accounting/telemetry:
  - Vertex response metadata includes cachedContentTokenCount.
  - SDKs surface usage_metadata; for request responses, input token details may show cached tokens.

## Practical design for OpenHands SDK

We already support:
- Implicit caching: No code change required. Our Telemetry captures cached token reads when providers include them (prompt/input token details). To maximize hits, keep large shared content at the beginning of prompts and send similar prefixes close in time.

We can add (non-breaking):
1) Provider-agnostic pass-through for explicit cache reference
   - Accept an optional parameter, e.g., cached_content_id, that maps to:
     - Vertex: generationConfig.cachedContent (or top-level cachedContent in REST).
     - Gemini API (AI Studio): GenerateContentConfig.cached_content.
   - Implementation detail: Litellm proxy support varies. For direct Google GenAI usage (AI Studio), use google-genai; for Vertex via proxies, expose a way to pass arbitrary extra_body/extra headers the proxy can forward. Our LLM already supports litellm_extra_body; document how to use it for cached content when the proxy supports it.

2) Utilities and examples for cache lifecycle outside the LLM facade
   - Provide example scripts using google-genai to:
     - Create cache with contents and TTL (explicit).
     - Generate content referencing the cache; record cached token counts; verify TTL via expire_time.
   - Provide example using LLM + LiteLLM proxy to demonstrate implicit caching; log cache_read_tokens and cost deltas.

3) Telemetry and logging
   - Keep using the existing telemetry (usage_summary includes cache_read_tokens) and log to logs/caching for inspection.

## Example workflows (added in examples)

- Implicit caching (Vertex via LiteLLM proxy):
  - Use LITELLM_API_KEY and base_url https://llm-proxy.eval.all-hands.dev to call gemini-3-pro or gemini-3-flash with a long, constant prefix in two back-to-back calls.
  - Inspect logs/caching JSON for usage_summary.cache_read_tokens>0 on the second call and compare costs/latency.

- Explicit caching (Gemini API / AI Studio):
  - Use GEMINI_API_KEY with google-genai to:
    - Create a cache: contents=[large text or files], ttl="300s".
    - Generate content referencing cached_content=cache.name.
    - Print response.usage_metadata and cache.expire_time; confirm cache token count via usage/cached content metadata.

Notes:
- Vertex explicit caching also supported, but requires Google Cloud auth and project/location configuration. The example uses AI Studio path to avoid GCP auth.

## Risks and caveats

- Proxy compatibility: Passing cachedContent through LiteLLM requires proxy support. If unsupported, fall back to direct google-genai in examples.
- Token thresholds: Some forum anecdotes suggest lower minima per model. We rely on the official 2,048 token minimum. For safety, tailor explicit caches to exceed that.
- Cost visibility: Discounts apply only for cached tokens; billable storage accrues for explicit caches while alive. Monitor usage metadata and billing.

## Recommendation

- Short term: Ship examples that verify implicit and explicit caching behaviors and wire telemetry to logs/caching. Document a simple caller pattern for explicit caching (using google-genai) alongside our SDK.
- Medium term: Add a thin cache reference parameter in LLM calls, mapped through litellm_extra_body or provider-specific kwargs, guarded behind feature detection.
- Long term: Add first-class cache lifecycle helpers (optional, separate module) for Vertex and Gemini API to ease creation, refresh, and eviction.

## Appendix: How to verify

1) Implicit path (Vertex)
   - Run examples/01_standalone_sdk/31_gemini_caching_probe.py with LITELLM_API_KEY set. It will perform two calls with a shared prefix and log to logs/caching.
   - Check usage_summary.cache_read_tokens in the second call's JSON log.

2) Explicit path (AI Studio)
   - Set GEMINI_API_KEY.
   - The example creates a cache with ttl=300s, calls generate_content referencing it, and prints expire_time and usage_metadata.

