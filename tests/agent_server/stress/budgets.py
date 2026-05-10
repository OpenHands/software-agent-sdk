"""Stress-test budgets, expressed as relative-to-baseline ratios where possible.

Absolute thresholds only for failure modes whose definition *is* unbounded
growth (slow-loris websocket, slow webhook).
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ParallelSubagentBudget:
    n_subagents: int = 8
    per_call_latency_s: float = 0.2
    # Wall time must be < single-agent wall × this. 1.5 leaves slack for
    # scheduling overhead while still failing on serialized execution.
    wall_time_factor: float = 1.5
    # RSS delta (peak - baseline) must be < baseline × this. With factor=2.0,
    # peak is allowed up to 3× baseline.
    rss_growth_factor: float = 2.0
    max_fd_growth: int = 64


@dataclass(frozen=True, slots=True)
class ConversationListingBudget:
    # 2000 surfaces O(N) regressions strongly in pagination/listing while
    # keeping the test under a minute on a developer laptop. We tried 10k
    # behind a --stress-full flag (with a tarball cache to skip the seed
    # cost) but ConversationService.__aenter__ still loads each meta.json
    # into a LocalConversation sequentially — that load alone takes minutes
    # at N=10k, so the cache didn't actually buy anything.
    n_conversations: int = 2000
    page_size: int = 50
    # First-page p95 latency must be < this many seconds. Tuned for a
    # developer laptop; the suite is opt-in (excluded from default CI
    # collection in pyproject.toml), so shared CI runners that need looser
    # numbers should override the budget at the call site rather than
    # loosening it here for everyone.
    p95_first_page_s: float = 0.5
    # Deep-page p95 must be < first-page p95 × this (graceful degradation).
    deep_page_factor: float = 4.0
    # 50 sequential list calls. Peak RSS during listing must stay below the
    # snapshot at listing-start + this delta. `_search_conversations` today
    # materialises a ConversationInfo for every conversation in the store
    # per call, so at N=2000 we observe ~4 MB allocator high-water per call
    # → ~200 MB across the loop. The 300 MB budget gives ~50% headroom over
    # current behaviour and would fire on a ~1.5× per-call retention
    # regression (e.g., per-call growth jumping from 4 to 6 MB).
    listing_rss_delta_mb: float = 300.0


@dataclass(frozen=True, slots=True)
class ConcurrentConversationsBudget:
    n_conversations: int = 16
    per_call_latency_s: float = 0.1
    # Concurrent wall < single-conversation wall × this.
    wall_time_factor: float = 2.5
    # RSS delta (peak - baseline) must be < baseline × this. With factor=2.0,
    # peak is allowed up to 3× baseline.
    rss_growth_factor: float = 2.0


@dataclass(frozen=True, slots=True)
class LongRunningCommandBudget:
    duration_s: float = 5.0  # quick CI mode; --stress-full bumps to 1800
    # Maximum gap between consecutive output events.
    max_output_gap_s: float = 3.0
    # /health p95 latency while bash is running.
    health_p95_s: float = 0.05
    # When sending kill, time until process tree is empty.
    cleanup_timeout_s: float = 3.0


@dataclass(frozen=True, slots=True)
class EventLoopResponsivenessBudget:
    # /health p95 must be below this under each background load.
    health_p95_s: float = 0.05
    # /health p99 — single sample tolerated to be a bit higher.
    health_p99_s: float = 0.15
    health_samples: int = 30


@dataclass(frozen=True, slots=True)
class SlowWebhookBudget:
    webhook_delay_s: float = 2.0
    # Conversation must complete within this multiple of the no-webhook
    # baseline. If we head-of-line block on the webhook, this fires.
    wall_time_factor: float = 3.0
    # Webhook subscriber RSS must stay under this delta.
    max_rss_delta_mb: float = 100.0


@dataclass(frozen=True, slots=True)
class SlowWebsocketConsumerBudget:
    n_events: int = 200
    # Server RSS delta with one stalled subscriber must be < this MB.
    # Failure mode IS unbounded growth so the budget is absolute. Each
    # ConversationStateUpdateEvent is ~1 KB on the wire, so 200 queued
    # events is ~200 KB of "real" growth; the rest of the budget is
    # headroom for allocator noise and Python interpreter overhead. A
    # genuine unbounded-buffer regression would push this into hundreds of
    # MB or GB long before brushing 150.
    max_rss_delta_mb: float = 150.0


@dataclass(frozen=True, slots=True)
class WebsocketReconnectStormBudget:
    cycles: int = 100
    # Max FD growth across the storm.
    max_fd_growth: int = 16
    # Subscriber count delta after settle.
    max_subscriber_delta: int = 1


@dataclass(frozen=True, slots=True)
class HighVolumeBashOutputBudget:
    # Run a fast-emitting command for this long.
    duration_s: float = 3.0
    # /health p95 while output streams.
    health_p95_s: float = 0.1
    # Upper bound on persisted bash events for the test's 5 MiB flood.
    # bash_service.MAX_CONTENT_CHAR_LENGTH is 1 MiB, so the expected count
    # is ~5–6 BashOutput + 1 BashCommand. 50 catches a ~7× regression and
    # absolutely catches per-line / per-byte emission (which would produce
    # millions). Don't loosen this without re-evaluating: limit=100 per
    # search page, so any value > 100 silently caps at 100 anyway and the
    # assertion stops being meaningful.
    max_events: int = 50


@dataclass(frozen=True, slots=True)
class LeaseContentionBudget:
    n_concurrent: int = 4
    # Max time for one client to win and the others to fail/yield cleanly.
    settle_timeout_s: float = 5.0


PARALLEL_SUBAGENTS = ParallelSubagentBudget()
CONVERSATION_LISTING = ConversationListingBudget()
CONCURRENT_CONVERSATIONS = ConcurrentConversationsBudget()
LONG_RUNNING_COMMAND = LongRunningCommandBudget()
EVENT_LOOP_RESPONSIVENESS = EventLoopResponsivenessBudget()
SLOW_WEBHOOK = SlowWebhookBudget()
SLOW_WEBSOCKET_CONSUMER = SlowWebsocketConsumerBudget()
WEBSOCKET_RECONNECT_STORM = WebsocketReconnectStormBudget()
HIGH_VOLUME_BASH_OUTPUT = HighVolumeBashOutputBudget()
LEASE_CONTENTION = LeaseContentionBudget()
