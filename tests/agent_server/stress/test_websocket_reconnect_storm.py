"""Stress test: rapid subscribe/unsubscribe cycles must not leak state.

Bug class this catches:
    - PubSub subscriber leak: subscribe/unsubscribe accounting drifts after
      many cycles, leaving stale entries in the dict.
    - FD leak (less likely in-process; included as a cheap sanity check).

White-box, not real WS:
    Real websocket reconnects through ASGITransport are awkward and the
    failure mode is in the *server-side* registration accounting, which we
    reach directly via ``event_service._pub_sub``.
"""

import os
from dataclasses import dataclass

import psutil
import pytest

from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.event_service import EventService
from openhands.agent_server.pub_sub import Subscriber
from openhands.sdk.event import Event
from tests.agent_server.stress.budgets import WEBSOCKET_RECONNECT_STORM
from tests.agent_server.stress.scripts import (
    SlowTestLLM,
    start_conversation_with_test_llm,
    text_message,
)


pytestmark = [pytest.mark.stress, pytest.mark.timeout(30)]


@dataclass(frozen=True, slots=True)
class _NoopSubscriber(Subscriber[Event]):
    async def __call__(self, event: Event) -> None:
        pass


async def _idle_event_service(
    conversation_service: ConversationService, *, workspace_dir: str
) -> EventService:
    """Create an idle conversation and return its event service."""
    parent_llm = SlowTestLLM.from_messages([text_message("ok")], latency_s=0.0)
    info = await start_conversation_with_test_llm(
        conversation_service,
        parent_llm=parent_llm,
        workspace_dir=workspace_dir,
        usage_id="reconn-storm",
        initial_text=None,
    )
    es = await conversation_service.get_event_service(info.id)
    assert es is not None
    return es


async def test_reconnect_storm_subscriber_accounting_balanced(
    conversation_service: ConversationService,
    tmp_path,
):
    """N subscribe/unsubscribe cycles. Subscriber count and FDs return to
    baseline."""
    workspace = str(tmp_path / "ws")
    (tmp_path / "ws").mkdir()
    es = await _idle_event_service(conversation_service, workspace_dir=workspace)

    proc = psutil.Process(os.getpid())
    pre_subscribers = len(es._pub_sub._subscribers)
    try:
        pre_fds = proc.num_fds()
    except (AttributeError, psutil.AccessDenied):
        pre_fds = -1

    # Use pub_sub.subscribe/unsubscribe directly. subscribe_to_events does
    # an initial-state sync that calls the subscriber with the FIFOLock
    # held — fine for one subscriber, but in a tight loop of 100 it can
    # contend with the lease renew loop and turn the test into a
    # latency benchmark rather than a leak check.
    for _ in range(WEBSOCKET_RECONNECT_STORM.cycles):
        sub = _NoopSubscriber()
        sid = es._pub_sub.subscribe(sub)
        ok = es._pub_sub.unsubscribe(sid)
        assert ok, "unsubscribe returned False — subscriber id mismatch"

    post_subscribers = len(es._pub_sub._subscribers)
    delta_subscribers = post_subscribers - pre_subscribers
    assert delta_subscribers <= WEBSOCKET_RECONNECT_STORM.max_subscriber_delta, (
        f"after {WEBSOCKET_RECONNECT_STORM.cycles} subscribe/unsubscribe "
        f"cycles, subscriber count grew by {delta_subscribers} (budget "
        f"{WEBSOCKET_RECONNECT_STORM.max_subscriber_delta}). Possible leak."
    )

    if pre_fds >= 0:
        post_fds = proc.num_fds()
        delta_fds = post_fds - pre_fds
        assert delta_fds <= WEBSOCKET_RECONNECT_STORM.max_fd_growth, (
            f"FDs grew by {delta_fds} across "
            f"{WEBSOCKET_RECONNECT_STORM.cycles} cycles (budget "
            f"{WEBSOCKET_RECONNECT_STORM.max_fd_growth})."
        )
