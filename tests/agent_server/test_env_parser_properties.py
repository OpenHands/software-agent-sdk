"""Property-based tests for the agent-server environment parser."""

import os
from typing import Literal
from unittest.mock import patch

from hypothesis import given, strategies as st
from pydantic import BaseModel, Field

from openhands.agent_server.env_parser import MISSING, from_env, merge, to_env


json_scalar = st.none() | st.booleans() | st.integers() | st.text(max_size=5)
json_value = st.recursive(
    json_scalar,
    lambda children: (
        st.lists(children, max_size=4)
        | st.dictionaries(st.text(max_size=3), children, max_size=4)
    ),
    max_leaves=10,
)


@given(json_value, json_value)
def test_merge_accepts_any_pair_of_json_values(a, b):
    merge(a, b)


@given(st.lists(json_value, max_size=5), st.lists(json_value, max_size=5))
def test_merged_list_is_as_long_as_the_longer_list(a, b):
    merged = merge(a, b)
    assert isinstance(merged, list)
    assert len(merged) == max(len(a), len(b))


@given(json_value)
def test_merge_with_missing_or_itself_is_identity(a):
    assert merge(a, MISSING) == a
    assert merge(MISSING, a) == a
    assert merge(a, a) == a


class Item(BaseModel):
    name: str = ""
    count: int = 0


class Sample(BaseModel):
    text: str = ""
    number: int = 0
    flag: bool = False
    mode: Literal["a", "b"] = "a"
    maybe: str | None = None
    tags: list[str] = Field(default_factory=list)
    items: list[Item] = Field(default_factory=list)


# A value must fit on one ``KEY=value`` line of the template.
line = st.text(
    st.characters(
        exclude_categories=("Cs", "Cc", "Zl", "Zp"), exclude_characters="\x85"
    ),
    max_size=8,
)
sample = st.builds(
    Sample,
    text=line,
    number=st.integers(-(10**6), 10**6),
    flag=st.booleans(),
    mode=st.sampled_from(["a", "b"]),
    maybe=st.none() | line,
    tags=st.lists(line, max_size=3),
    items=st.lists(st.builds(Item, name=line, count=st.integers(0, 99)), max_size=3),
)


def _template_to_environ(template: str) -> dict[str, str]:
    environ = {}
    for entry in template.splitlines():
        if entry and not entry.startswith("#"):
            key, _, value = entry.partition("=")
            environ[key] = value
    return environ


@given(sample)
def test_env_template_loads_back_to_the_same_model(value):
    environ = _template_to_environ(to_env(value, "OH"))
    with patch.dict(os.environ, environ, clear=True):
        assert from_env(Sample, "OH") == value
