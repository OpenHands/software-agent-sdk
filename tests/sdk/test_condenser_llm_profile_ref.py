"""Tests for a dedicated condenser LLM (#5469).

Covering the two surfaces of the feature:

- ``LLMSummarizingCondenserSettings.build_condenser`` / ``create_agent`` — the
  embedded-LLM and fallback behavior, at the settings level.
- ``launch.resolve`` and the profile stores — a profile can name a saved LLM
  profile for summarization, resolved to an embedded LLM at launch.

The conversation-level guard lives in ``test_switch_model.py``.
"""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from openhands.sdk import LLM
from openhands.sdk.agent.base import AgentBase
from openhands.sdk.context.condenser import LLMSummarizingCondenser
from openhands.sdk.launch import UnresolvedProfileReferences, resolve
from openhands.sdk.llm.llm_profile_store import LLMProfileStore
from openhands.sdk.profiles.agent_profile import OpenHandsAgentProfile
from openhands.sdk.profiles.resolver import (
    ProfileNotFound,
    resolve_agent_profile,
    resolve_agent_profile_dry_run,
)
from openhands.sdk.settings.model import (
    LLMSummarizingCondenserSettings,
    NoOpCondenserSettings,
    OpenHandsAgentSettings,
)
from tests.sdk.launch import fakes


def _agent_llm(model: str = "agent-model", usage_id: str = "agent", **kwargs) -> LLM:
    return LLM(model=model, usage_id=usage_id, **kwargs)


def _summarizing(**kwargs) -> LLMSummarizingCondenserSettings:
    return LLMSummarizingCondenserSettings(**kwargs)


def _summarizing_condenser(agent: AgentBase) -> LLMSummarizingCondenser:
    assert isinstance(agent.condenser, LLMSummarizingCondenser)
    return agent.condenser


# ---------------------------------------------------------------------------
# build_condenser / create_agent
# ---------------------------------------------------------------------------


def test_condenser_without_an_llm_falls_back_to_the_agent_llm() -> None:
    """The unchanged path: no dedicated LLM means summarize with the agent."""
    settings = OpenHandsAgentSettings(
        llm=_agent_llm(), condenser=_summarizing(enabled=True)
    )

    condenser = _summarizing_condenser(settings.create_agent())

    assert condenser.llm.model == "agent-model"
    assert condenser.llm.usage_id == "condenser"


def test_condenser_llm_is_used_for_summarization() -> None:
    condenser_llm = LLM(model="cheap-model", usage_id="cheap", max_input_tokens=65536)
    settings = OpenHandsAgentSettings(
        llm=_agent_llm(), condenser=_summarizing(enabled=True, llm=condenser_llm)
    )

    condenser = _summarizing_condenser(settings.create_agent())

    assert condenser.llm.model == "cheap-model"
    # A dedicated LLM keeps its own usage_id: that key is how the launch
    # remembers it came from a named profile, and switch_llm respects it.
    assert condenser.llm.usage_id == "cheap"


def test_condenser_llm_is_copied_not_shared() -> None:
    """The condenser's LLM is a private copy: its metrics must not leak into
    the object the caller passed in (cost attribution is per-conversation)."""
    condenser_llm = LLM(model="cheap-model", usage_id="cheap")
    settings = OpenHandsAgentSettings(
        llm=_agent_llm(), condenser=_summarizing(enabled=True, llm=condenser_llm)
    )

    built = _summarizing_condenser(settings.create_agent()).llm

    assert built is not condenser_llm
    assert built.model == "cheap-model"


def test_condenser_llm_matching_the_agent_usage_id_rekeys_to_condenser() -> None:
    """An LLM that shares the agent's registry key would be deduped out of
    conversation stats, so the build renames it — same rule as sub-agents."""
    same_key = LLM(model="cheap-model", usage_id="agent")
    settings = OpenHandsAgentSettings(
        llm=_agent_llm(), condenser=_summarizing(enabled=True, llm=same_key)
    )

    assert _summarizing_condenser(settings.create_agent()).llm.usage_id == "condenser"


def test_max_tokens_inherits_from_the_condenser_llm_not_the_agent() -> None:
    """The whole point of the split: a small-context summarizer must not get
    the agent's 1M-token budget (it would overflow on the first summarize)."""
    condenser_llm = LLM(model="cheap-model", usage_id="cheap", max_input_tokens=65536)
    settings = OpenHandsAgentSettings(
        llm=_agent_llm(max_input_tokens=1_000_000),
        condenser=_summarizing(enabled=True, llm=condenser_llm),
    )

    assert _summarizing_condenser(settings.create_agent()).max_tokens == 65536


def test_explicit_condenser_max_tokens_beats_the_inheritance() -> None:
    condenser_llm = LLM(model="cheap-model", usage_id="cheap", max_input_tokens=65536)
    settings = OpenHandsAgentSettings(
        llm=_agent_llm(max_input_tokens=1_000_000),
        condenser=_summarizing(enabled=True, llm=condenser_llm, max_tokens=4000),
    )

    assert _summarizing_condenser(settings.create_agent()).max_tokens == 4000


def test_disabled_condenser_never_needs_a_dedicated_llm() -> None:
    settings = OpenHandsAgentSettings(
        llm=_agent_llm(),
        condenser=_summarizing(enabled=False, llm=LLM(model="cheap", usage_id="cheap")),
    )

    assert settings.create_agent().condenser is None


def test_embedded_condenser_llm_round_trips_through_serialization() -> None:
    """Persisted settings keep the dedicated LLM so a resumed conversation
    still summarizes on the cheap model, exactly like the agent ``llm``."""
    cheap = LLM(model="cheap-model", usage_id="cheap")
    settings = OpenHandsAgentSettings(
        llm=_agent_llm(),
        condenser=_summarizing(enabled=True, llm_profile_ref="cheap", llm=cheap),
    )

    restored = OpenHandsAgentSettings.model_validate(settings.model_dump(mode="json"))
    condenser = restored.condenser
    assert isinstance(condenser, LLMSummarizingCondenserSettings)

    assert condenser.llm_profile_ref == "cheap"
    assert condenser.llm is not None
    assert condenser.llm.model == "cheap-model"
    assert condenser.llm.usage_id == "cheap"


def test_ref_and_embedded_llm_must_agree() -> None:
    """A launch writes both fields together; a hand-written mismatch is a bug
    caught at validation, not a conversation that silently summarizes on the
    wrong model."""
    with pytest.raises(ValueError, match="does not match"):
        _summarizing(llm_profile_ref="other", llm=LLM(model="m", usage_id="cheap"))


def test_ref_without_an_embedded_llm_is_valid() -> None:
    """The stored profile shape: a ref with no payload (resolved at launch)."""
    settings = _summarizing(llm_profile_ref="cheap")

    assert settings.llm is None
    assert settings.llm_profile_ref == "cheap"


# ---------------------------------------------------------------------------
# launch.resolve — the profile path
# ---------------------------------------------------------------------------


def _profile(**kwargs) -> OpenHandsAgentProfile:
    return OpenHandsAgentProfile(name="p", llm_profile_ref="default", **kwargs)


def _resolved_settings(*args, **kwargs) -> OpenHandsAgentSettings:
    resolved = resolve(*args, **kwargs).settings
    assert isinstance(resolved, OpenHandsAgentSettings)
    return resolved


def _llms(condenser_settings) -> LLMSummarizingCondenserSettings:
    assert isinstance(condenser_settings, LLMSummarizingCondenserSettings)
    return condenser_settings


def test_resolve_embeds_the_condenser_profile_llm() -> None:
    llms = {
        "default": fakes.llm(),
        "cheap": fakes.llm("cheap-model"),
    }

    settings = _resolved_settings(
        _profile(condenser=_summarizing(llm_profile_ref="cheap")),
        fakes.stores(llms=llms),
    )

    assert settings.llm.model == "gpt-4o"
    condenser = _llms(settings.condenser)
    assert condenser.llm is not None
    assert condenser.llm.model == "cheap-model"
    assert condenser.llm.usage_id == "cheap"
    assert condenser.llm_profile_ref == "cheap"


def test_resolve_without_a_condenser_ref_embeds_nothing() -> None:
    condenser = _llms(_resolved_settings(_profile(), fakes.stores()).condenser)

    assert condenser.llm is None
    assert condenser.llm_profile_ref is None


def test_resolve_reuses_the_agent_llm_when_the_ref_names_it() -> None:
    """A ref pointing at the agent's own profile is a no-op — the condenser
    already follows the agent LLM, and re-embedding would steal the
    usage_id='condenser' dedup key."""
    condenser = _llms(
        _resolved_settings(
            _profile(condenser=_summarizing(llm_profile_ref="default")),
            fakes.stores(),
        ).condenser
    )

    assert condenser.llm is None


def test_resolve_reports_a_missing_condenser_llm_profile() -> None:
    profile = _profile(condenser=_summarizing(llm_profile_ref="gone"))

    with pytest.raises(UnresolvedProfileReferences) as exc_info:
        resolve(profile, fakes.stores(llms={"default": fakes.llm()}))

    detail = exc_info.value.to_detail()
    assert detail["dangling_condenser_llm_profile_ref"] == "gone"
    assert detail["dangling_llm_profile_ref"] is None
    assert "condenser LLM profile 'gone' not found" in str(exc_info.value)


def test_resolve_reports_both_missing_refs_together() -> None:
    profile = OpenHandsAgentProfile(
        name="p",
        llm_profile_ref="gone-agent",
        condenser=_summarizing(llm_profile_ref="gone-cheap"),
    )

    with pytest.raises(UnresolvedProfileReferences) as exc_info:
        resolve(profile, fakes.stores(llms={}))

    detail = exc_info.value.to_detail()
    assert detail["dangling_llm_profile_ref"] == "gone-agent"
    assert detail["dangling_condenser_llm_profile_ref"] == "gone-cheap"


def test_resolve_leaves_a_non_llm_condenser_alone() -> None:
    """NoOp condenser settings carry no ref field at all; nothing is loaded
    and nothing is embedded."""
    settings = _resolved_settings(
        _profile(condenser=NoOpCondenserSettings()), fakes.stores()
    )

    assert settings.condenser.condenser_kind == "no_op"


# ---------------------------------------------------------------------------
# legacy resolver (resolve_agent_profile) + editor dry-run diagnostics
# ---------------------------------------------------------------------------


_STORED_KEY = "sk-chea***"


def _llm_store(tmp_path):
    store = LLMProfileStore(base_dir=tmp_path / "llms")
    store.save("agent", LLM(model="agent-model", usage_id="agent"))
    store.save(
        "cheap",
        LLM(model="cheap-model", usage_id="cheap", api_key=SecretStr(_STORED_KEY)),
    )
    return store


def test_resolve_agent_profile_embeds_the_condenser_llm(tmp_path) -> None:
    store = _llm_store(tmp_path)
    profile = OpenHandsAgentProfile(
        name="p",
        llm_profile_ref="agent",
        condenser=_summarizing(llm_profile_ref="cheap"),
    )

    settings = resolve_agent_profile(
        profile, llm_store=store, mcp_config={}, available_skills=None
    )
    assert isinstance(settings, OpenHandsAgentSettings)
    condenser = _llms(settings.condenser)

    assert condenser.llm is not None
    assert condenser.llm.model == "cheap-model"


def test_resolve_agent_profile_missing_condenser_llm_raises(tmp_path) -> None:
    store = _llm_store(tmp_path)
    profile = OpenHandsAgentProfile(
        name="p",
        llm_profile_ref="agent",
        condenser=_summarizing(llm_profile_ref="gone"),
    )

    with pytest.raises(ProfileNotFound, match="gone"):
        resolve_agent_profile(
            profile, llm_store=store, mcp_config={}, available_skills=None
        )


def test_dry_run_reports_a_dangling_condenser_llm_ref(tmp_path) -> None:
    store = _llm_store(tmp_path)
    profile = OpenHandsAgentProfile(
        name="p",
        llm_profile_ref="agent",
        condenser=_summarizing(llm_profile_ref="gone"),
    )

    diagnostics = resolve_agent_profile_dry_run(
        profile, llm_store=store, mcp_config={}, available_skills=None
    )

    assert diagnostics.valid is False
    assert diagnostics.condenser_llm_profile_ref == "gone"
    assert diagnostics.condenser_llm_profile_resolved is False
    assert any("gone" in e for e in diagnostics.errors)


def test_dry_run_reports_a_resolved_condenser_llm_ref(tmp_path) -> None:
    store = _llm_store(tmp_path)
    profile = OpenHandsAgentProfile(
        name="p",
        llm_profile_ref="agent",
        condenser=_summarizing(llm_profile_ref="cheap"),
    )

    diagnostics = resolve_agent_profile_dry_run(
        profile, llm_store=store, mcp_config={}, available_skills=None
    )
    resolved = diagnostics.resolved_settings
    assert resolved is not None

    assert diagnostics.valid is True
    assert diagnostics.condenser_llm_profile_resolved is True
    condenser_payload = resolved["condenser"]
    assert condenser_payload["llm_profile_ref"] == "cheap"
    assert condenser_payload["llm"]["model"] == "cheap-model"
    # The cheap profile carries a key; a dry-run dump (no expose context) must
    # redact it like the top-level llm does — the embedded condenser LLM must
    # not become a side channel that leaks the key into the editor preview.
    assert condenser_payload["llm"].get("api_key") != _STORED_KEY
    assert _STORED_KEY not in str(resolved)


# ---------------------------------------------------------------------------
# profile store — secret-free at rest
# ---------------------------------------------------------------------------


def test_agent_profile_drops_an_embedded_condenser_llm_on_validation() -> None:
    """A profile is secret-free at rest. Validating a payload that carries a
    resolved condenser LLM keeps the portable ref and drops the payload."""
    profile = OpenHandsAgentProfile.model_validate(
        {
            "name": "p",
            "llm_profile_ref": "default",
            "condenser": {
                "condenser_kind": "llm_summarizing",
                "llm_profile_ref": "cheap",
                "llm": {"model": "cheap-model", "usage_id": "cheap"},
            },
        }
    )

    assert _llms(profile.condenser).llm_profile_ref == "cheap"
    assert _llms(profile.condenser).llm is None


def test_llm_profile_store_round_trips_the_dedicated_condenser_profile(
    tmp_path,
) -> None:
    """End-to-end shape: the profile keeps the ref only, the launch hydrates
    the real cheap model from the store."""
    profile_dir = tmp_path / "llms"
    store = LLMProfileStore(base_dir=profile_dir)
    store.save("cheap", LLM(model="cheap-model", usage_id="cheap"))
    stored_json = (profile_dir / "cheap.json").read_text()
    cheap = store.load("cheap")

    condenser = _summarizing(llm_profile_ref="cheap")
    # The stored profile shape survives a store round trip: ref, no payload.
    profile = OpenHandsAgentProfile.model_validate(
        _profile(condenser=condenser).model_dump(mode="json")
    )

    settings = _resolved_settings(
        profile, fakes.stores(llms={"default": _agent_llm(), "cheap": cheap})
    )
    resolved_condenser = _llms(settings.condenser)
    assert resolved_condenser.llm is not None
    assert resolved_condenser.llm.model == "cheap-model"
    assert "cheap-model" in stored_json
