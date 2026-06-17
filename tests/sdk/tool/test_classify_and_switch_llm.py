import json
import types
from pathlib import Path

import pytest

from openhands.sdk import LLM, LocalConversation, OpenHandsAgentSettings
from openhands.sdk.agent import Agent
from openhands.sdk.llm import Message, TextContent, llm_profile_store
from openhands.sdk.llm.llm_profile_store import LLMProfileStore
from openhands.sdk.llm.meta_profile_store import MetaProfileStore
from openhands.sdk.testing import TestLLM
from openhands.sdk.tool.builtins import (
    ClassifyAndSwitchLLMAction,
    ClassifyAndSwitchLLMExecutor,
    ClassifyAndSwitchLLMObservation,
    ClassifyAndSwitchLLMTool,
)
from openhands.sdk.tool.builtins.classify_and_switch_llm import (
    _recent_messages_text,
    build_classifier_prompt,
    parse_class_index,
)


META = {
    "classifier_model": "classifier",
    "default_model": "default",
    "classes": [
        {"description": "UI / images", "model": "fast"},
        {"description": "tests", "model": "slow"},
    ],
}


def _make_llm(model: str, usage_id: str) -> LLM:
    return TestLLM.from_messages([], model=model, usage_id=usage_id)


@pytest.fixture()
def meta_store(tmp_path: Path) -> MetaProfileStore:
    meta_dir = tmp_path / "meta-profiles"
    meta_dir.mkdir()
    (meta_dir / "balanced.json").write_text(json.dumps(META), encoding="utf-8")
    return MetaProfileStore(base_dir=meta_dir)


@pytest.fixture()
def profile_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> LLMProfileStore:
    profile_dir = tmp_path / "profiles"
    profile_dir.mkdir()
    monkeypatch.setattr(llm_profile_store, "_DEFAULT_PROFILE_DIR", profile_dir)
    store = LLMProfileStore(base_dir=profile_dir)
    store.save("fast", _make_llm("fast-model", "fast"))
    store.save("slow", _make_llm("slow-model", "slow"))
    store.save("default", _make_llm("default-profile-model", "default-profile"))
    return store


def _make_conversation() -> LocalConversation:
    return LocalConversation(
        agent=Agent(llm=_make_llm("default-model", "default"), tools=[]),
        workspace=Path.cwd(),
    )


def _patch_classifier(
    conversation: LocalConversation,
    monkeypatch: pytest.MonkeyPatch,
    reply: str,
) -> None:
    """Make the classifier profile resolve to a scripted TestLLM."""
    real_load = conversation._profile_store.load

    def fake_load(name: str, *, cipher=None):
        if name == "classifier":
            return TestLLM.from_messages(
                [Message(role="assistant", content=[TextContent(text=reply)])],
                usage_id="classifier",
            )
        return real_load(name, cipher=cipher)

    monkeypatch.setattr(conversation._profile_store, "load", fake_load)


# ── pure helpers ──────────────────────────────────────────────────────────


def test_build_classifier_prompt_lists_categories() -> None:
    from openhands.sdk.llm.meta_profile_store import MetaProfile

    prompt = build_classifier_prompt(MetaProfile.model_validate(META))
    assert "1. UI / images" in prompt
    assert "2. tests" in prompt
    assert "respond with 0" in prompt.lower()


@pytest.mark.parametrize(
    "reply,expected",
    [
        ("1", 1),
        ("2", 2),
        ("0", 0),
        ("9", 0),  # out of range -> default
        ("none", 0),  # no integer -> default
        ("Category 2 fits best", 2),
    ],
)
def test_parse_class_index(reply: str, expected: int) -> None:
    assert parse_class_index(reply, num_classes=2) == expected


def test_recent_messages_text_takes_last_n() -> None:
    from openhands.sdk.event.llm_convertible.message import MessageEvent

    events = [
        MessageEvent(
            source="user",
            llm_message=Message(role="user", content=[TextContent(text=f"m{i}")]),
        )
        for i in range(8)
    ]
    conv = types.SimpleNamespace(state=types.SimpleNamespace(events=events))

    text = _recent_messages_text(conv, limit=3)  # type: ignore[arg-type]
    assert text == "user: m5\nuser: m6\nuser: m7"


# ── create() ──────────────────────────────────────────────────────────────


def test_create_falls_back_to_first_when_no_active_profile(
    meta_store: MetaProfileStore,
) -> None:
    # "balanced" is the only profile, so it is the first one resolved.
    # Resolution is deferred to invocation time, so check the resolver directly.
    tool = ClassifyAndSwitchLLMTool.create(meta_profile_store=meta_store)[0]
    assert isinstance(tool.executor, ClassifyAndSwitchLLMExecutor)
    assert tool.executor._resolve_meta_profile().classifier_model == "classifier"


def test_create_picks_alphabetically_first_meta_profile(
    meta_store: MetaProfileStore,
) -> None:
    # Add a second profile whose name sorts before "balanced".
    other = dict(META)
    other["classifier_model"] = "other-classifier"
    (Path(meta_store.base_dir) / "aaa.json").write_text(
        json.dumps(other), encoding="utf-8"
    )
    assert meta_store.list()[0] == "aaa"
    tool = ClassifyAndSwitchLLMTool.create(meta_profile_store=meta_store)[0]
    assert isinstance(tool.executor, ClassifyAndSwitchLLMExecutor)
    assert tool.executor._resolve_meta_profile().classifier_model == "other-classifier"


def test_create_does_not_touch_disk_when_no_meta_profiles_exist(
    tmp_path: Path,
) -> None:
    # Deferred resolution: create() must not read the store, so an empty (or
    # missing) meta-profile dir cannot break agent/conversation startup.
    empty_store = MetaProfileStore(base_dir=tmp_path / "empty")
    tools = ClassifyAndSwitchLLMTool.create(meta_profile_store=empty_store)
    assert len(tools) == 1
    assert isinstance(tools[0].executor, ClassifyAndSwitchLLMExecutor)


def test_create_does_not_load_missing_active_meta_profile(
    meta_store: MetaProfileStore,
) -> None:
    # A dangling active_meta_profile must not raise at create() time.
    tool = ClassifyAndSwitchLLMTool.create(
        active_meta_profile="does-not-exist", meta_profile_store=meta_store
    )[0]
    assert isinstance(tool.executor, ClassifyAndSwitchLLMExecutor)


def test_create_rejects_unknown_params(meta_store: MetaProfileStore) -> None:
    with pytest.raises(ValueError):
        ClassifyAndSwitchLLMTool.create(
            active_meta_profile="balanced",
            meta_profile_store=meta_store,
            bogus=1,
        )


def test_create_loads_meta_profile(meta_store: MetaProfileStore) -> None:
    tool = ClassifyAndSwitchLLMTool.create(
        active_meta_profile="balanced", meta_profile_store=meta_store
    )[0]
    assert tool.name == "classify_and_switch_llm"
    assert isinstance(tool.executor, ClassifyAndSwitchLLMExecutor)
    assert tool.executor._resolve_meta_profile().classifier_model == "classifier"


# ── executor ──────────────────────────────────────────────────────────────


def _register_tool(
    conversation: LocalConversation, meta_store: MetaProfileStore
) -> None:
    tool = ClassifyAndSwitchLLMTool.create(
        active_meta_profile="balanced", meta_profile_store=meta_store
    )[0]
    conversation._ensure_agent_ready()
    conversation.agent.tools_map[tool.name] = tool


def test_executor_switches_to_matched_class(
    profile_store, meta_store, monkeypatch
) -> None:
    conversation = _make_conversation()
    _register_tool(conversation, meta_store)
    _patch_classifier(conversation, monkeypatch, reply="1")

    obs = conversation.execute_tool(
        "classify_and_switch_llm", ClassifyAndSwitchLLMAction()
    )

    assert isinstance(obs, ClassifyAndSwitchLLMObservation)
    assert not obs.is_error
    assert obs.model == "fast"
    assert obs.chosen_class == "UI / images"
    assert conversation.agent.llm.model == "fast-model"


def test_classifier_call_is_accounted_in_conversation_stats(
    profile_store, meta_store, monkeypatch
) -> None:
    """The classifier completion must spend through the conversation registry.

    Regression for the budget-bypass bug: the classifier LLM has to be
    registered in ``conversation.llm_registry`` (under a stable usage id) so its
    tokens/cost land in ``conversation_stats`` and count against
    ``max_budget_per_run`` — an unregistered LLM would spend off the books.
    """
    conversation = _make_conversation()
    _register_tool(conversation, meta_store)
    _patch_classifier(conversation, monkeypatch, reply="1")

    usage_id = "classifier:classifier"
    assert usage_id not in conversation.conversation_stats.usage_to_metrics

    obs = conversation.execute_tool(
        "classify_and_switch_llm", ClassifyAndSwitchLLMAction()
    )

    assert isinstance(obs, ClassifyAndSwitchLLMObservation)
    assert not obs.is_error
    # The classifier LLM is now both registered and tracked by the stats, so
    # its spend is included in the combined (budget-enforced) metrics.
    assert usage_id in conversation.llm_registry.list_usage_ids()
    assert usage_id in conversation.conversation_stats.usage_to_metrics


def test_repeated_routing_reuses_one_classifier_usage_bucket(
    profile_store, meta_store, monkeypatch
) -> None:
    """A second routing call must reuse the cached classifier, not re-register."""
    conversation = _make_conversation()
    _register_tool(conversation, meta_store)
    _patch_classifier(conversation, monkeypatch, reply="1")

    conversation.execute_tool("classify_and_switch_llm", ClassifyAndSwitchLLMAction())
    conversation.execute_tool("classify_and_switch_llm", ClassifyAndSwitchLLMAction())

    usage_ids = conversation.llm_registry.list_usage_ids()
    assert usage_ids.count("classifier:classifier") == 1


def test_executor_falls_back_to_default_when_no_class(
    profile_store, meta_store, monkeypatch
) -> None:
    conversation = _make_conversation()
    _register_tool(conversation, meta_store)
    _patch_classifier(conversation, monkeypatch, reply="0")

    obs = conversation.execute_tool(
        "classify_and_switch_llm", ClassifyAndSwitchLLMAction()
    )

    assert isinstance(obs, ClassifyAndSwitchLLMObservation)
    assert not obs.is_error
    assert obs.model == "default"
    assert obs.chosen_class is None
    assert conversation.agent.llm.model == "default-profile-model"


def test_executor_errors_when_classifier_profile_missing(
    profile_store, meta_store
) -> None:
    conversation = _make_conversation()
    _register_tool(conversation, meta_store)
    # No "classifier" profile saved and no patch -> load fails.

    obs = conversation.execute_tool(
        "classify_and_switch_llm", ClassifyAndSwitchLLMAction()
    )

    assert obs.is_error
    assert "classifier" in obs.text
    assert conversation.agent.llm.model == "default-model"


def test_executor_errors_when_target_profile_missing(
    profile_store, meta_store, monkeypatch
) -> None:
    conversation = _make_conversation()
    _register_tool(conversation, meta_store)
    # classifier picks class 2 -> "slow", but remove it from the store.
    (Path(profile_store.base_dir) / "slow.json").unlink()
    _patch_classifier(conversation, monkeypatch, reply="2")

    obs = conversation.execute_tool(
        "classify_and_switch_llm", ClassifyAndSwitchLLMAction()
    )

    assert isinstance(obs, ClassifyAndSwitchLLMObservation)
    assert obs.is_error
    assert obs.model == "slow"
    assert "not found" in obs.text


def test_executor_errors_when_no_meta_profile_available(
    profile_store, tmp_path
) -> None:
    # Empty store + no active profile: invocation (not startup) reports the error.
    empty_store = MetaProfileStore(base_dir=tmp_path / "empty")
    conversation = _make_conversation()
    tool = ClassifyAndSwitchLLMTool.create(meta_profile_store=empty_store)[0]
    conversation._ensure_agent_ready()
    conversation.agent.tools_map[tool.name] = tool

    obs = conversation.execute_tool(
        "classify_and_switch_llm", ClassifyAndSwitchLLMAction()
    )

    assert obs.is_error
    assert "no meta-profile" in obs.text.lower()
    assert conversation.agent.llm.model == "default-model"


def test_executor_errors_when_active_meta_profile_missing(
    profile_store, meta_store
) -> None:
    # Dangling active_meta_profile: invocation reports the error, no crash.
    conversation = _make_conversation()
    tool = ClassifyAndSwitchLLMTool.create(
        active_meta_profile="does-not-exist", meta_profile_store=meta_store
    )[0]
    conversation._ensure_agent_ready()
    conversation.agent.tools_map[tool.name] = tool

    obs = conversation.execute_tool(
        "classify_and_switch_llm", ClassifyAndSwitchLLMAction()
    )

    assert obs.is_error
    assert "resolve" in obs.text.lower()


def test_missing_active_meta_profile_does_not_break_startup(
    meta_store, monkeypatch
) -> None:
    # The whole point of deferring the load: a missing active meta-profile must
    # not break _ensure_agent_ready() / conversation startup.
    monkeypatch.setattr(
        "openhands.sdk.llm.meta_profile_store._DEFAULT_META_PROFILE_DIR",
        meta_store.base_dir,
    )
    agent = OpenHandsAgentSettings(
        llm=_make_llm("default-model", "default"),
        enable_classify_and_switch_llm_tool=True,
        active_meta_profile="does-not-exist",
    ).create_agent()
    conversation = LocalConversation(agent=agent, workspace=Path.cwd())

    # Must not raise even though the active meta-profile file does not exist.
    conversation._ensure_agent_ready()

    assert any(t.name == "ClassifyAndSwitchLLMTool" for t in agent.tools)


# ── create_agent wiring ─────────────────────────────────────────────────────


def test_create_agent_adds_tool_when_enabled(meta_store, monkeypatch) -> None:
    monkeypatch.setattr(
        "openhands.sdk.llm.meta_profile_store._DEFAULT_META_PROFILE_DIR",
        meta_store.base_dir,
    )
    agent = OpenHandsAgentSettings(
        llm=_make_llm("default-model", "default"),
        enable_classify_and_switch_llm_tool=True,
        active_meta_profile="balanced",
    ).create_agent()

    assert any(t.name == "ClassifyAndSwitchLLMTool" for t in agent.tools)


def test_create_agent_adds_tool_when_enabled_without_active_profile(
    meta_store, monkeypatch
) -> None:
    monkeypatch.setattr(
        "openhands.sdk.llm.meta_profile_store._DEFAULT_META_PROFILE_DIR",
        meta_store.base_dir,
    )
    # No active_meta_profile: the tool is still wired and resolves the first one.
    agent = OpenHandsAgentSettings(
        llm=_make_llm("default-model", "default"),
        enable_classify_and_switch_llm_tool=True,
    ).create_agent()

    assert any(t.name == "ClassifyAndSwitchLLMTool" for t in agent.tools)


def test_create_agent_omits_tool_when_disabled() -> None:
    agent = OpenHandsAgentSettings(
        llm=_make_llm("default-model", "default"),
    ).create_agent()

    assert not any(t.name == "ClassifyAndSwitchLLMTool" for t in agent.tools)
