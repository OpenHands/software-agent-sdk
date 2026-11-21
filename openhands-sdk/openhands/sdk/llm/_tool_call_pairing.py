"""Enforce assistant tool-call / tool-result pairing before sending to providers.

Providers that use the Bedrock Converse API (and, more strictly than the
Anthropic-native API, several others) require that every assistant ``toolUse``
block is answered by exactly one ``toolResult`` block, and that those results
appear in the turn immediately following the assistant turn that issued them.
A mismatch is rejected with errors such as::

    BedrockException - The number of toolResult blocks at messages.N.content
    exceeds the number of toolUse blocks of previous turn.

The event log can violate this invariant even though it looks balanced overall.
The common cause is an interrupt: a tool call is recorded as an ``ActionEvent``
with no ``ObservationEvent`` yet, the user queues one or more messages while the
agent is paused, and only then is a synthetic result backfilled at the *end* of
the log. Serialised in event order this yields
``assistant(toolUse) -> user(text) -> tool(toolResult)``, so the result no
longer immediately follows its call. History condensation can split a batch the
same way.

This module repairs the *final* message list, independently of how events or
condensation produced it, so the guarantee holds for every provider:

* each assistant ``tool_calls`` entry is followed, in call order, by its
  matching tool message (pulled forward if it appears later);
* a tool call with no result anywhere is answered with a synthetic error result;
* a tool message with no matching call anywhere is dropped.

The transform is order-preserving and idempotent: a well-formed message list is
returned unchanged.
"""

from __future__ import annotations

from openhands.sdk.llm.message import Message, TextContent


_MISSING_RESULT_TEXT = (
    "No tool result was recorded for this tool call (the call was interrupted "
    "or its result was lost during history management). Treat it as failed and "
    "continue."
)


def _is_tool_result(message: Message) -> bool:
    return message.role == "tool" and message.tool_call_id is not None


def ensure_tool_call_pairing(messages: list[Message]) -> list[Message]:
    """Return a copy of ``messages`` with tool-use/tool-result pairing enforced.

    See the module docstring for the exact invariant. The input list and its
    ``Message`` objects are not mutated.
    """
    # Index the first tool result for each tool_call_id so results can be pulled
    # forward to sit right after the assistant turn that called them.
    results_by_id: dict[str, Message] = {}
    for message in messages:
        if _is_tool_result(message):
            assert message.tool_call_id is not None
            results_by_id.setdefault(message.tool_call_id, message)

    # tool_call_ids that an assistant turn actually requested.
    requested_ids: set[str] = set()
    for message in messages:
        if message.role == "assistant" and message.tool_calls:
            requested_ids.update(call.id for call in message.tool_calls)

    consumed: set[str] = set()
    output: list[Message] = []

    for message in messages:
        if _is_tool_result(message):
            # Placed (or dropped) when its assistant turn is emitted; skip here.
            # Orphans — results whose call id was never requested — are dropped.
            continue

        output.append(message)

        if message.role == "assistant" and message.tool_calls:
            for call in message.tool_calls:
                if call.id in consumed:
                    # Defensive: the same id requested twice. Only answer once.
                    continue
                consumed.add(call.id)
                result = results_by_id.get(call.id)
                if result is None:
                    result = Message(
                        role="tool",
                        content=[TextContent(text=_MISSING_RESULT_TEXT)],
                        tool_call_id=call.id,
                        name=call.name,
                    )
                output.append(result)

    return output
