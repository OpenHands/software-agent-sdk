"""Tests for the auto-loaded-skills serialization optimisation.

When ``load_user_skills`` / ``load_public_skills`` are enabled,
``AgentContext._load_auto_skills`` resolves every matching skill and
appends it to ``self.skills`` at model-validation time. Persisting
those skills on the wire is pure duplication — the validator runs
again on every deserialization and rebuilds the same set deterministically.

The serializer drops auto-loaded skills from ``model_dump`` output but
keeps the in-memory ``self.skills`` intact so runtime prompt rendering
behaves exactly as today. These tests pin both halves of that contract.
"""

from __future__ import annotations

from unittest.mock import patch

from openhands.sdk.context import AgentContext
from openhands.sdk.skills import Skill


def _make_skill(name: str, content: str = "auto skill body") -> Skill:
    return Skill(
        name=name,
        content=content,
        source=f"/fake/{name}.md",
    )


class TestAutoLoadedSkillsSerialization:
    def test_auto_loaded_skills_drop_from_serialized_output(self):
        """``model_dump`` omits auto-loaded skills (the headline payload win)."""
        auto = {
            "auto-1": _make_skill("auto-1"),
            "auto-2": _make_skill("auto-2"),
            "auto-3": _make_skill("auto-3"),
        }
        with patch(
            "openhands.sdk.context.agent_context.load_available_skills",
            return_value=auto,
        ):
            ctx = AgentContext(load_public_skills=True)

        # In-memory: the validator populated all three.
        assert {s.name for s in ctx.skills} == {"auto-1", "auto-2", "auto-3"}
        # On the wire: dropped entirely (no caller passed explicit skills).
        dumped = ctx.model_dump()
        assert dumped["skills"] == []

    def test_explicit_skills_survive_serialization(self):
        """Caller-supplied skills are NOT dropped — only the auto-loaded ones."""
        explicit = _make_skill("user-explicit", "this one is mine")
        auto = {"auto-only": _make_skill("auto-only")}
        with patch(
            "openhands.sdk.context.agent_context.load_available_skills",
            return_value=auto,
        ):
            ctx = AgentContext(load_public_skills=True, skills=[explicit])

        # In-memory: both present.
        assert {s.name for s in ctx.skills} == {"user-explicit", "auto-only"}
        # On the wire: only the explicit one.
        dumped = ctx.model_dump()
        assert [s["name"] for s in dumped["skills"]] == ["user-explicit"]

    def test_explicit_skill_shadows_an_auto_one(self):
        """When the explicit list collides with an auto-loaded name, the
        explicit one wins in-memory AND on the wire (it was never marked
        as auto-loaded — the auto-load step skipped it).
        """
        explicit = _make_skill("shared-name", "user version")
        auto = {"shared-name": _make_skill("shared-name", "auto version")}
        with patch(
            "openhands.sdk.context.agent_context.load_available_skills",
            return_value=auto,
        ):
            ctx = AgentContext(load_public_skills=True, skills=[explicit])

        assert len(ctx.skills) == 1
        assert ctx.skills[0].content == "user version"
        dumped = ctx.model_dump()
        assert len(dumped["skills"]) == 1
        assert dumped["skills"][0]["name"] == "shared-name"
        assert dumped["skills"][0]["content"] == "user version"

    def test_round_trip_via_serialized_output_re_resolves_auto_skills(self):
        """Deserializing the trimmed payload + re-validating repopulates the
        auto-loaded skills via the same auto-load path. This is what makes
        dropping them on the wire safe: the receiver sees the same
        in-memory shape as the sender.
        """
        auto = {
            "auto-a": _make_skill("auto-a"),
            "auto-b": _make_skill("auto-b"),
        }
        with patch(
            "openhands.sdk.context.agent_context.load_available_skills",
            return_value=auto,
        ):
            ctx = AgentContext(load_public_skills=True)
            dumped = ctx.model_dump()
            # Re-validating the trimmed payload should re-fire the auto-load
            # validator and rebuild the in-memory list.
            roundtripped = AgentContext.model_validate(dumped)

        assert {s.name for s in roundtripped.skills} == {"auto-a", "auto-b"}

    def test_disabled_auto_load_leaves_explicit_skills_on_the_wire(self):
        """With both flags off, ``_load_auto_skills`` is a no-op — every
        skill is treated as explicit and serialized normally.
        """
        explicit = [_make_skill("foo"), _make_skill("bar")]
        ctx = AgentContext(skills=explicit)
        assert {s.name for s in ctx.skills} == {"foo", "bar"}
        dumped = ctx.model_dump()
        assert {s["name"] for s in dumped["skills"]} == {"foo", "bar"}

    def test_migration_stored_skills_matching_loader_are_marked_auto_loaded(self):
        """Migration path: a conversation stored before this PR carries the
        resolved auto-loaded skills inlined on ``skills``. On reload, the
        validator must recognise those as auto-loaded (the persisted
        skill equals what the loader produces) and drop them from the
        next serialization — otherwise the bloat persists until the
        conversation is recreated.
        """
        stored = [_make_skill("auto-x"), _make_skill("auto-y")]
        # Loader returns the same skill objects (matches what was
        # persisted under the old SDK).
        loader_output = {
            "auto-x": _make_skill("auto-x"),
            "auto-y": _make_skill("auto-y"),
        }
        with patch(
            "openhands.sdk.context.agent_context.load_available_skills",
            return_value=loader_output,
        ):
            ctx = AgentContext(load_public_skills=True, skills=stored)

        # No duplicates: skills stayed at 2 (migration recognised them).
        assert {s.name for s in ctx.skills} == {"auto-x", "auto-y"}
        # And the serialized payload drops them.
        dumped = ctx.model_dump()
        assert dumped["skills"] == []

    def test_migration_stored_skills_diverged_from_loader_stay_explicit(self):
        """If the persisted skill content no longer matches what the
        loader would produce (user edited the file, marketplace updated),
        the on-disk version wins — treat it as explicit and keep it on
        the wire. This is the safe default: we don't drop content
        someone may have intentionally customised.
        """
        stored = [_make_skill("auto-x", "old persisted content")]
        loader_output = {"auto-x": _make_skill("auto-x", "new loader content")}
        with patch(
            "openhands.sdk.context.agent_context.load_available_skills",
            return_value=loader_output,
        ):
            ctx = AgentContext(load_public_skills=True, skills=stored)

        # The stored ("old") version wins in-memory.
        assert len(ctx.skills) == 1
        assert ctx.skills[0].content == "old persisted content"
        # And it stays on the wire — not treated as auto-loaded.
        dumped = ctx.model_dump()
        assert len(dumped["skills"]) == 1
        assert dumped["skills"][0]["content"] == "old persisted content"

    def test_replacing_auto_skill_via_model_copy_survives_serialization(self):
        """Regression guard for OpenHands' ``_create_agent_with_skills`` flow.

        That helper does ``agent_context.model_copy(update={'skills':
        merged})`` where ``merged`` is the result of deduping
        auto-loaded + sandbox/repo-loaded skills by name, with the
        later list winning. If two lists carry the same name, the
        merged result is the *replacement* — different content from
        what ``_load_auto_skills`` produced.

        A name-only serializer would drop the replacement (the name
        matches an auto-loaded one) and the receiver would auto-load
        the original stock version, silently dropping OpenHands'
        customisations. The equality-based filter must keep the
        replacement on the wire.
        """
        # Stock auto-loaded skill ("public version").
        with patch(
            "openhands.sdk.context.agent_context.load_available_skills",
            return_value={"git-skill": _make_skill("git-skill", "public version")},
        ):
            ctx = AgentContext(load_public_skills=True)
        # OpenHands merges in a different "git-skill" (same name).
        replacement = _make_skill("git-skill", "OpenHands custom version")
        # Drop the auto-loaded skill, add the replacement. This is what
        # ``_merge_skills`` produces — later list (replacement) wins.
        merged = [s for s in ctx.skills if s.name != "git-skill"] + [replacement]
        new_ctx = ctx.model_copy(update={"skills": merged})

        # In-memory: only the replacement is present.
        assert len(new_ctx.skills) == 1
        assert new_ctx.skills[0].content == "OpenHands custom version"
        # On the wire: replacement survives (equality check, not name).
        dumped = new_ctx.model_dump()
        assert len(dumped["skills"]) == 1
        assert dumped["skills"][0]["content"] == "OpenHands custom version"

    def test_preserve_full_skills_context_flag_keeps_snapshot(self):
        """``context={"preserve_full_skills": True}`` opts out of the trim.

        This is the persistence path: ``ConversationState._save_base_state``
        passes this flag so the snapshot of auto-loaded skills is frozen
        at conversation-create time. Without it, a paused conversation
        resumed after the skill source updates would silently pick up
        the *new* content via ``_load_auto_skills``, breaking the
        guarantee that the agent runs the same skills it started with.
        """
        auto = {f"auto-{i}": _make_skill(f"auto-{i}") for i in range(5)}
        with patch(
            "openhands.sdk.context.agent_context.load_available_skills",
            return_value=auto,
        ):
            ctx = AgentContext(load_public_skills=True)

        # Default: trim.
        dumped_default = ctx.model_dump()
        assert dumped_default["skills"] == []

        # Persistence opt-out: full snapshot.
        dumped_persist = ctx.model_dump(context={"preserve_full_skills": True})
        assert len(dumped_persist["skills"]) == 5
        assert {s["name"] for s in dumped_persist["skills"]} == set(auto.keys())

    def test_wrap_serializer_propagates_exclude_none(self):
        """Caller-supplied ``exclude_none`` reaches nested ``Skill`` fields.

        ``Skill`` has several optional fields that default to ``None``
        (``trigger``, ``source``, ``mcp_tools``, …). A manual
        ``s.model_dump(...)`` inside the serializer would have dropped
        the caller's ``exclude_none`` setting. ``mode="wrap"`` +
        ``handler`` delegation preserves it.
        """
        explicit = Skill(name="x", content="hello")  # trigger / mcp_tools / etc. → None
        ctx = AgentContext(skills=[explicit])

        # Without exclude_none, None fields appear.
        dumped_keep_nones = ctx.model_dump()
        skill_dict = dumped_keep_nones["skills"][0]
        # ``trigger`` is one of the always-present None defaults; pick
        # it as the canary.
        assert "trigger" in skill_dict
        assert skill_dict["trigger"] is None

        # With exclude_none, the field is omitted from the nested skill.
        dumped_drop_nones = ctx.model_dump(exclude_none=True)
        skill_dict_pruned = dumped_drop_nones["skills"][0]
        assert "trigger" not in skill_dict_pruned

    def test_save_base_state_persists_full_skill_snapshot(self):
        """End-to-end: ``ConversationState._save_base_state`` writes the
        full skill list to disk, even though the in-memory ``model_dump``
        default trims them.
        """
        import json
        import uuid

        from openhands.sdk.conversation.state import ConversationState
        from openhands.sdk.io import InMemoryFileStore
        from openhands.sdk.workspace.local import LocalWorkspace

        auto = {f"auto-{i}": _make_skill(f"auto-{i}") for i in range(3)}
        with patch(
            "openhands.sdk.context.agent_context.load_available_skills",
            return_value=auto,
        ):
            agent_context = AgentContext(load_public_skills=True)
            # Use a real lightweight agent to satisfy ConversationState.
            from openhands.sdk.agent import Agent
            from openhands.sdk.llm import LLM

            agent = Agent(
                llm=LLM(model="test", usage_id="test"),
                agent_context=agent_context,
            )

            import tempfile

            with tempfile.TemporaryDirectory() as tmp:
                state = ConversationState.create(
                    id=uuid.uuid4(),
                    agent=agent,
                    workspace=LocalWorkspace(working_dir=tmp),
                )
                fs = InMemoryFileStore()
                state._save_base_state(fs)

                persisted = json.loads(fs.read("base_state.json"))

        # The persisted snapshot must include the auto-loaded skills so
        # a resumed conversation gets the same content even if the
        # skill source changed in the meantime.
        persisted_skill_names = [
            s["name"] for s in persisted["agent"]["agent_context"]["skills"]
        ]
        assert set(persisted_skill_names) == set(auto.keys()), (
            "persistence path lost the auto-loaded skill snapshot — "
            "without it, a resumed conversation could silently pick up "
            "newer skill content from ~/.openhands/skills"
        )

    def test_in_place_skill_mutation_preserves_customisation_on_wire(self):
        """Regression for the mutable-snapshot bug.

        ``_auto_loaded_skills`` used to store the same ``Skill``
        reference that landed in ``self.skills``. An in-place edit
        like ``ctx.skills[0].content = "custom"`` mutated both copies,
        the equality check still succeeded, and the customised skill
        silently vanished from ``model_dump`` output. Deep-copying the
        snapshot at auto-load time keeps the two references
        independent so the equality check correctly reports the
        in-memory skill as "modified" and keeps it on the wire.
        """
        auto = {"editable": _make_skill("editable", "original content")}
        with patch(
            "openhands.sdk.context.agent_context.load_available_skills",
            return_value=auto,
        ):
            ctx = AgentContext(load_public_skills=True)

        # Caller mutates the skill in-place after the auto-load.
        ctx.skills[0].content = "user customised content"

        dumped = ctx.model_dump()
        assert len(dumped["skills"]) == 1
        assert dumped["skills"][0]["content"] == "user customised content"

    def test_model_copy_toggling_load_flag_off_keeps_skills_on_wire(self):
        """Regression for the config-drift bug.

        ``ctx.model_copy(update={"load_public_skills": False})`` keeps
        the runtime auto-loaded skills (the validator doesn't re-run
        on model_copy) but used to serialize them as ``[]`` because
        the snapshot still matched the in-memory names. On
        ``model_validate(...)`` the receiver wouldn't auto-reload them
        (flag is False), losing them entirely.

        With the config-drift check, the serializer detects that the
        current config doesn't match the snapshot config and emits
        the full skill list, preserving the round-trip.
        """
        auto = {f"auto-{i}": _make_skill(f"auto-{i}") for i in range(3)}
        with patch(
            "openhands.sdk.context.agent_context.load_available_skills",
            return_value=auto,
        ):
            ctx = AgentContext(load_public_skills=True)

        # Flip the flag off via model_copy — does NOT re-run the
        # validator, so ctx.skills still has the 3 auto-loaded skills.
        flipped = ctx.model_copy(update={"load_public_skills": False})
        assert len(flipped.skills) == 3

        # Serializer must NOT trim — the config drifted. Round-trip
        # would otherwise lose them (flag is False on the receiver).
        dumped = flipped.model_dump()
        assert {s["name"] for s in dumped["skills"]} == set(auto.keys())

        # And the round-trip preserves them in-memory.
        roundtripped = AgentContext.model_validate(dumped)
        assert {s.name for s in roundtripped.skills} == set(auto.keys())

    def test_model_copy_changing_marketplace_path_keeps_skills_on_wire(self):
        """Same config-drift concern as above but for ``marketplace_path``.

        If the snapshot was taken with ``marketplace_path="A"`` and a
        copy bumps it to ``"B"``, the receiver would auto-load from
        ``"B"`` and get a *different* catalog. Preserve the original
        list on the wire so the receiver can't silently swap.
        """
        auto = {"marketplace-skill": _make_skill("marketplace-skill")}
        with patch(
            "openhands.sdk.context.agent_context.load_available_skills",
            return_value=auto,
        ):
            ctx = AgentContext(load_public_skills=True, marketplace_path="path-A")

        bumped = ctx.model_copy(update={"marketplace_path": "path-B"})
        dumped = bumped.model_dump()
        assert {s["name"] for s in dumped["skills"]} == {"marketplace-skill"}

    def test_payload_shrinks_to_explicit_only(self):
        """Concrete byte-count assertion: a 40-skill auto-load with a single
        explicit skill should serialize approximately the size of the
        single explicit skill, not 40+1.
        """
        import json

        auto = {f"auto-{i}": _make_skill(f"auto-{i}", "x" * 1000) for i in range(40)}
        explicit = _make_skill("user", "y" * 100)
        with patch(
            "openhands.sdk.context.agent_context.load_available_skills",
            return_value=auto,
        ):
            ctx = AgentContext(load_public_skills=True, skills=[explicit])

        dumped = ctx.model_dump()
        skills_bytes = len(json.dumps(dumped["skills"]))
        # The 40 auto skills total ~40 KB; the single explicit one is
        # ~150 B. The serialized ``skills`` list must clearly be the
        # explicit-only size, not the full set.
        assert skills_bytes < 1000, (
            f"serialized skills should be ~1 explicit skill (~150 B), "
            f"got {skills_bytes} B — auto skills leaked into output"
        )
