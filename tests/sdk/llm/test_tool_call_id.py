import re

import pytest

from openhands.sdk.llm.utils.tool_call_id import ToolCallIdPolicy


def test_policy_preserves_accepted_id():
    policy = ToolCallIdPolicy(re.compile(r"^[A-Za-z0-9_-]+$"))

    assert policy.encode("github_get_file_contents_1") == "github_get_file_contents_1"


def test_policy_deterministically_encodes_rejected_ids():
    policy = ToolCallIdPolicy(re.compile(r"^[A-Za-z0-9_-]+$"))

    first = policy.encode("github_get_file_contents:1")
    second = policy.encode("github_get_file_contents:2")

    assert first == policy.encode("github_get_file_contents:1")
    assert first != second
    assert re.fullmatch(r"^[A-Za-z0-9_-]+$", first)


def test_policy_enforces_max_length():
    policy = ToolCallIdPolicy(
        re.compile(r"^[A-Za-z0-9_-]+$"),
        generated_prefix="tc_",
        max_length=16,
    )

    encoded = policy.encode("a" * 17)

    assert len(encoded) == 16
    assert encoded.startswith("tc_")


def test_policy_rejects_incompatible_generated_ids():
    policy = ToolCallIdPolicy(
        re.compile(r"^[A-Za-z0-9_-]+$"),
        generated_prefix="invalid:",
    )

    with pytest.raises(ValueError, match="rejects its generated IDs"):
        policy.encode("original:invalid")
