"""Stress test: slow webhook must not stall the conversation.

Bug class this catches:
    - Head-of-line blocking when an event subscriber (the webhook) posts to a
      slow downstream. PubSub.__call__ awaits subscribers sequentially
      (pub_sub.py:70-74), so a slow webhook blocks every event publication
      behind it.
    - Webhook subscriber buffer growing unbounded under sustained pressure.

What this test surfaces vs asserts:
    Today the publish path IS sequential. With ``event_buffer_size=1`` (flush
    on every event) and a 2-s slow webhook, a conversation will visibly
    stall waiting on each post. The budget below encodes "this is the
    behaviour we want to catch regressions of" — if the agent-server later
    moves to async background webhook posting, tighten the budget.

Real HTTP server (not monkeypatch) because:
    Monkeypatching ``httpx.AsyncClient`` would also affect this test's own
    ASGI client (which uses httpx). A small stdlib HTTP server is simpler.
"""

import asyncio
import http.server
import threading
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from openhands.agent_server import bash_router as bash_router_module
from openhands.agent_server.bash_service import BashEventService
from openhands.agent_server.config import Config, WebhookSpec
from openhands.agent_server.conversation_router import conversation_router
from openhands.agent_server.conversation_service import (
    ConversationService,
    WebhookSubscriber,
)
from openhands.agent_server.dependencies import get_conversation_service
from openhands.agent_server.event_router import event_router
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event.conversation_state import ConversationStateUpdateEvent
from tests.agent_server.stress.budgets import SLOW_WEBHOOK
from tests.agent_server.stress.probe import ResourceProbe
from tests.agent_server.stress.scripts import (
    SlowTestLLM,
    start_conversation_with_test_llm,
    text_message,
    wait_for_terminal,
)


pytestmark = pytest.mark.stress


class _SlowReceiver(http.server.BaseHTTPRequestHandler):
    """HTTP handler that sleeps before responding 200.

    Class attribute set per fixture so we can vary delay without rebuilding
    the handler class.
    """

    delay_s: float = SLOW_WEBHOOK.webhook_delay_s

    def do_POST(self) -> None:  # noqa: N802 — stdlib API
        # Drain the request body so the connection closes cleanly.
        length = int(self.headers.get("Content-Length", "0"))
        if length:
            self.rfile.read(length)
        time.sleep(self.delay_s)
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        # Suppress default stderr access logs — they pollute pytest output.
        pass


@pytest.fixture
def slow_webhook_url() -> Iterator[str]:
    """Spin up a slow stdlib HTTP server on a random port for this test."""
    _SlowReceiver.delay_s = SLOW_WEBHOOK.webhook_delay_s
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _SlowReceiver)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()
        t.join(timeout=2)


# These fixtures override the conftest defaults for this module so we can
# wire up a webhook-enabled ConversationService. pytest resolves them by
# locality.


@pytest_asyncio.fixture
async def conversation_service(
    tmp_path: Path, slow_webhook_url: str
) -> AsyncIterator[ConversationService]:
    persist = tmp_path / "persist"
    persist.mkdir()
    spec = WebhookSpec(
        base_url=slow_webhook_url,
        event_buffer_size=1,
        flush_delay=1.0,
        num_retries=0,
    )
    service = ConversationService(
        conversations_dir=persist,
        webhook_specs=[spec],
    )
    async with service:
        yield service


@pytest.fixture
def app(
    conversation_service: ConversationService, bash_service: BashEventService
) -> FastAPI:
    fastapi_app = FastAPI()
    fastapi_app.state.config = Config()
    fastapi_app.include_router(conversation_router, prefix="/api")
    fastapi_app.include_router(event_router, prefix="/api")
    fastapi_app.include_router(bash_router_module.bash_router, prefix="/api")
    fastapi_app.dependency_overrides[get_conversation_service] = (
        lambda: conversation_service
    )
    return fastapi_app


@pytest_asyncio.fixture
async def baseline_service(tmp_path: Path) -> AsyncIterator[ConversationService]:
    """Webhook-free service for the timing baseline. Different persist dir
    so it doesn't share state with the webhook service."""
    persist = tmp_path / "persist_baseline"
    persist.mkdir()
    service = ConversationService(conversations_dir=persist)
    async with service:
        yield service


async def _run_conversation_and_time(
    service: ConversationService,
    client: httpx.AsyncClient,
    workspace_dir: str,
    *,
    usage_id: str,
) -> tuple[float, ConversationExecutionStatus]:
    parent_llm = SlowTestLLM.from_messages([text_message("done")], latency_s=0.0)
    info = await start_conversation_with_test_llm(
        service,
        parent_llm=parent_llm,
        workspace_dir=workspace_dir,
        usage_id=usage_id,
        initial_text="hi",
    )

    t0 = time.monotonic()
    run_resp = await client.post(f"/api/conversations/{info.id.hex}/run")
    assert run_resp.status_code == 200, run_resp.text
    status = await wait_for_terminal(client, info.id, timeout_s=60.0)
    return time.monotonic() - t0, status


async def test_slow_webhook_does_not_unbound_growth(
    conversation_service: ConversationService,
    baseline_service: ConversationService,
    client: httpx.AsyncClient,
    tmp_path: Path,
    probe: ResourceProbe,
):
    """Conversation completes, RSS bounded, even with a 2 s webhook.

    Whether the webhook *blocks* the conversation or not is implementation-
    defined; what's not negotiable is:
      (a) the conversation eventually FINISHED, and
      (b) the webhook subscriber buffer doesn't accumulate unbounded events.
    """
    workspace = str(tmp_path / "ws")
    (tmp_path / "ws").mkdir()

    # Baseline: same flow, no webhook. Reuses the bash_service-backed app
    # but with a webhook-free ConversationService. We need a separate ASGI
    # client for it.
    baseline_app = FastAPI()
    baseline_app.state.config = Config()
    baseline_app.include_router(conversation_router, prefix="/api")
    baseline_app.include_router(event_router, prefix="/api")
    baseline_app.dependency_overrides[get_conversation_service] = (
        lambda: baseline_service
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(baseline_app),
        base_url="http://stress.test",
    ) as baseline_client:
        baseline_wall, baseline_status = await _run_conversation_and_time(
            baseline_service,
            baseline_client,
            workspace,
            usage_id="webhook-baseline",
        )
    assert baseline_status == ConversationExecutionStatus.FINISHED

    # Webhook run.
    webhook_wall, webhook_status = await _run_conversation_and_time(
        conversation_service, client, workspace, usage_id="webhook-slow"
    )

    # 1. The conversation finishes. Catches "slow webhook deadlocks the
    #    conversation forever" regressions.
    assert webhook_status == ConversationExecutionStatus.FINISHED, (
        f"conversation ended in {webhook_status} with a slow webhook in the "
        f"subscriber chain. Possible deadlock or unhandled exception."
    )

    # 2. Wall time is bounded. Today, with sequential pub_sub, the slow
    #    webhook does add latency. The budget allows for that — if the
    #    agent-server later moves webhooks to async background tasks, this
    #    will pass with much more headroom and the budget can be tightened.
    budget = baseline_wall * SLOW_WEBHOOK.wall_time_factor + (
        SLOW_WEBHOOK.webhook_delay_s * 4
    )
    assert webhook_wall < budget, (
        f"with a {SLOW_WEBHOOK.webhook_delay_s} s webhook, conversation "
        f"took {webhook_wall:.2f} s vs budget {budget:.2f} s "
        f"(baseline {baseline_wall:.2f} s × "
        f"{SLOW_WEBHOOK.wall_time_factor} + slack). The webhook may be "
        f"head-of-line blocking conversation completion more than expected."
    )

    # 3. RSS delta absolute. Failure mode for slow-webhook is *unbounded*
    #    buffer growth, so a relative budget would mask it.
    assert probe.rss_delta_mb() < SLOW_WEBHOOK.max_rss_delta_mb, (
        f"RSS grew by {probe.rss_delta_mb():.1f} MB during the slow-webhook "
        f"run (budget {SLOW_WEBHOOK.max_rss_delta_mb}). The webhook "
        f"subscriber may be buffering events without bound."
    )


class _AlwaysFailReceiver(http.server.BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        if length:
            self.rfile.read(length)
        self.send_response(503)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass


@pytest.fixture
def always_fail_webhook_url():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _AlwaysFailReceiver)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        t.join(timeout=2)


@pytest_asyncio.fixture
async def failing_webhook_service(tmp_path: Path, always_fail_webhook_url: str):
    persist = tmp_path / "persist_fail"
    persist.mkdir()
    service = ConversationService(
        conversations_dir=persist,
        webhook_specs=[
            WebhookSpec(
                base_url=always_fail_webhook_url,
                event_buffer_size=1,
                flush_delay=0.5,
                num_retries=0,
                retry_delay=0,
            )
        ],
    )
    async with service:
        yield service


@pytest.mark.xfail(
    strict=True,
    reason=(
        "conversation_service.py:1059 extends failed batches back onto "
        "self.queue without bound; sustained downstream failure → OOM."
    ),
)
async def test_webhook_queue_bounded_under_sustained_downstream_failure(
    failing_webhook_service, tmp_path
):
    (tmp_path / "ws").mkdir()
    info = await start_conversation_with_test_llm(
        failing_webhook_service,
        parent_llm=SlowTestLLM.from_messages([text_message("done")], latency_s=0.0),
        workspace_dir=str(tmp_path / "ws"),
        usage_id="webhook-fail",
        initial_text=None,
    )
    es = await failing_webhook_service.get_event_service(info.id)
    assert es is not None
    # White-box access: the bug is unbounded growth of WebhookSubscriber.queue
    # under sustained downstream failure (conversation_service.py:1059 extends
    # failed batches back without bound). There's no public API that exposes
    # an individual subscriber's queue, and adding one just for a regression
    # test would bake test concerns into production. Reach into _pub_sub
    # directly here.
    webhook_sub = next(
        s for s in es._pub_sub._subscribers.values() if isinstance(s, WebhookSubscriber)
    )

    n_events = 500
    for i in range(n_events):
        await es._pub_sub(
            ConversationStateUpdateEvent(
                key="execution_status", value=f"idle-{i}", source="environment"
            )
        )
    await asyncio.sleep(0.5)

    assert len(webhook_sub.queue) < n_events // 2, (
        f"queue grew to {len(webhook_sub.queue)} after {n_events} events; "
        f"failed batches are re-extended without bound."
    )
