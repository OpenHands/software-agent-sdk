"""Tests for conversation mentions, ``[Title](conversation:<hex>)``."""

import re
from uuid import uuid4

from openhands.sdk.conversation.types import (
    CONVERSATION_MENTION_SCHEME,
    conversation_mention,
)


MENTION = re.compile(r"^\[([^\[\]()]+)\]\(conversation:([0-9a-f]{32})\)$")


def test_mention_links_the_title_to_the_conversation_id():
    cid = uuid4()

    assert conversation_mention("Fix Safari export", cid) == (
        f"[Fix Safari export]({CONVERSATION_MENTION_SCHEME}{cid.hex})"
    )


def test_mention_accepts_a_dashed_id_string():
    cid = uuid4()

    assert conversation_mention("T", str(cid)).endswith(f"(conversation:{cid.hex})")


def test_a_hostile_title_cannot_end_the_link_early():
    cid = uuid4()
    for title in (
        "a](conversation:deadbeef) [b",
        "close ] bracket",
        "paren ) and (",
        'quote " and `tick`',
        "back\\slash",
        "two\nlines",
    ):
        mention = conversation_mention(title, cid)
        match = MENTION.match(mention)
        assert match, mention
        assert match.group(2) == cid.hex
        assert "\n" not in mention


def test_a_missing_or_blank_title_still_reads():
    cid = uuid4()

    assert conversation_mention(None, cid).startswith("[untitled conversation](")
    assert conversation_mention(" []() ", cid).startswith("[untitled conversation](")


def test_a_long_title_is_cut():
    text = MENTION.match(conversation_mention("x" * 500, uuid4())).group(1)  # type: ignore[union-attr]

    assert len(text) == 80
