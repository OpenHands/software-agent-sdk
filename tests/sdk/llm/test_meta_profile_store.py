import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from openhands.sdk.llm.meta_profile_store import (
    MetaProfile,
    MetaProfileClass,
    MetaProfileLimitExceeded,
    MetaProfileStore,
    MetaProfileTargetModel,
)


def _write(base: Path, name: str, data: dict) -> None:
    (base / f"{name}.json").write_text(json.dumps(data), encoding="utf-8")


VALID = {
    "classifier_model": "minimax",
    "default_model": "gpt",
    "classes": [
        {"description": "UI / images", "model": "deepseek"},
        {"description": "research", "model": "gemini"},
    ],
}


def test_load_valid_meta_profile(tmp_path: Path) -> None:
    _write(tmp_path, "balanced", VALID)
    store = MetaProfileStore(base_dir=tmp_path)

    meta = store.load("balanced")

    assert isinstance(meta, MetaProfile)
    assert meta.classifier_model == "minimax"
    assert meta.default_model == "gpt"
    assert [c.model for c in meta.classes] == ["deepseek", "gemini"]


def test_load_accepts_name_with_json_suffix(tmp_path: Path) -> None:
    _write(tmp_path, "balanced", VALID)
    store = MetaProfileStore(base_dir=tmp_path)

    assert store.load("balanced.json").classifier_model == "minimax"


def test_list_returns_sorted_valid_names(tmp_path: Path) -> None:
    _write(tmp_path, "b", VALID)
    _write(tmp_path, "a", VALID)
    # A file with an invalid stem must be ignored by list().
    (tmp_path / ".hidden.json").write_text("{}", encoding="utf-8")
    store = MetaProfileStore(base_dir=tmp_path)

    assert store.list() == ["a", "b"]


def test_load_missing_raises_file_not_found(tmp_path: Path) -> None:
    store = MetaProfileStore(base_dir=tmp_path)

    with pytest.raises(FileNotFoundError):
        store.load("nope")


def test_load_invalid_name_raises_value_error(tmp_path: Path) -> None:
    store = MetaProfileStore(base_dir=tmp_path)

    with pytest.raises(ValueError):
        store.load("../escape")


def test_load_corrupted_json_raises_value_error(tmp_path: Path) -> None:
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    store = MetaProfileStore(base_dir=tmp_path)

    with pytest.raises(ValueError):
        store.load("broken")


def test_load_schema_violation_raises_value_error(tmp_path: Path) -> None:
    _write(tmp_path, "bad", {"default_model": "gpt"})  # missing classifier_model
    store = MetaProfileStore(base_dir=tmp_path)

    with pytest.raises(ValueError):
        store.load("bad")


def test_save_then_load_roundtrip(tmp_path: Path) -> None:
    store = MetaProfileStore(base_dir=tmp_path)
    meta = MetaProfile(
        classifier_model="minimax",
        default_model="gpt",
        classes=[MetaProfileClass(description="UI / images", model="deepseek")],
    )

    store.save("balanced", meta)

    loaded = store.load("balanced")
    assert loaded.classifier_model == "minimax"
    assert loaded.default_model == "gpt"
    assert [c.model for c in loaded.classes] == ["deepseek"]


def test_direct_prompt_meta_profile_roundtrip(tmp_path: Path) -> None:
    store = MetaProfileStore(base_dir=tmp_path)
    meta = MetaProfile(
        classifier_model="minimax",
        default_model="default",
        prompt_template=(
            "Route the task.\n{{ model_table }}\nTask:\n{{ instance_text }}\n"
            'Return JSON: {"model": "<exact model name>"}'
        ),
        model_table="- GPT-5.4\n- MiniMax-M3",
        target_models=[
            MetaProfileTargetModel(model="GPT-5.4", profile="gpt"),
            MetaProfileTargetModel(model="MiniMax-M3", profile="minimax"),
        ],
    )

    store.save("pareto", meta)

    loaded = store.load("pareto")
    assert loaded.prompt_template == meta.prompt_template
    assert loaded.model_table == "- GPT-5.4\n- MiniMax-M3"
    assert [target.profile for target in loaded.target_models] == ["gpt", "minimax"]


def test_direct_prompt_requires_instance_text_placeholder() -> None:
    with pytest.raises(ValueError, match="instance_text"):
        MetaProfile(
            classifier_model="minimax",
            default_model="default",
            prompt_template="Route without the task.",
            target_models=[MetaProfileTargetModel(model="GPT-5.4", profile="gpt")],
        )


def test_direct_prompt_rejects_classes() -> None:
    with pytest.raises(ValueError, match="classes"):
        MetaProfile(
            classifier_model="minimax",
            default_model="default",
            classes=[MetaProfileClass(description="tests", model="minimax")],
            prompt_template="Task:\n{{ instance_text }}",
            target_models=[MetaProfileTargetModel(model="GPT-5.4", profile="gpt")],
        )


def test_direct_prompt_rejects_duplicate_target_models() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        MetaProfile(
            classifier_model="minimax",
            default_model="default",
            prompt_template="Task:\n{{ instance_text }}",
            target_models=[
                MetaProfileTargetModel(model="GPT-5.4", profile="gpt"),
                MetaProfileTargetModel(model="gpt-5.4", profile="other"),
            ],
        )


def test_save_overwrites_existing(tmp_path: Path) -> None:
    store = MetaProfileStore(base_dir=tmp_path)
    store.save("p", MetaProfile(classifier_model="a", default_model="b"))
    store.save("p", MetaProfile(classifier_model="c", default_model="d"))

    assert store.load("p").classifier_model == "c"
    assert store.list() == ["p"]


def test_save_invalid_name_raises_value_error(tmp_path: Path) -> None:
    store = MetaProfileStore(base_dir=tmp_path)

    with pytest.raises(ValueError):
        store.save("../escape", MetaProfile(classifier_model="a", default_model="b"))


def test_save_respects_max_profiles(tmp_path: Path) -> None:
    store = MetaProfileStore(base_dir=tmp_path)
    store.save("a", MetaProfile(classifier_model="a", default_model="b"))

    with pytest.raises(MetaProfileLimitExceeded):
        store.save(
            "b", MetaProfile(classifier_model="a", default_model="b"), max_profiles=1
        )

    # Overwriting an existing one is still allowed at the limit.
    store.save(
        "a", MetaProfile(classifier_model="x", default_model="y"), max_profiles=1
    )
    assert store.load("a").classifier_model == "x"


def test_concurrent_saves_respect_max_profiles(tmp_path: Path) -> None:
    """The file lock must serialize the limit check + write across threads.

    Without locking, concurrent creators can each pass the ``max_profiles``
    check before any write lands and overshoot the limit (TOCTOU).
    """
    store = MetaProfileStore(base_dir=tmp_path)
    limit = 10
    attempts = 30

    def attempt(i: int) -> bool:
        try:
            store.save(
                f"p{i:02d}",
                MetaProfile(classifier_model="a", default_model="b"),
                max_profiles=limit,
            )
            return True
        except MetaProfileLimitExceeded:
            return False

    with ThreadPoolExecutor(max_workers=attempts) as pool:
        results = list(pool.map(attempt, range(attempts)))

    assert sum(results) == limit
    assert len(store.list()) == limit


def test_delete_is_idempotent(tmp_path: Path) -> None:
    store = MetaProfileStore(base_dir=tmp_path)
    store.save("p", MetaProfile(classifier_model="a", default_model="b"))

    store.delete("p")
    assert store.list() == []
    # Deleting a missing meta-profile is a no-op.
    store.delete("p")


def test_list_summaries_skips_corrupted(tmp_path: Path) -> None:
    _write(tmp_path, "good", VALID)
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    store = MetaProfileStore(base_dir=tmp_path)

    summaries = store.list_summaries()

    assert summaries == [
        {
            "name": "good",
            "classifier_model": "minimax",
            "default_model": "gpt",
            "num_classes": 2,
        }
    ]
