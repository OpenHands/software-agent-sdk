"""Tests for AgentContext serialization and deserialization."""

import json
from base64 import urlsafe_b64encode

from pydantic import SecretStr

from openhands.sdk.context.agent_context import AgentContext
from openhands.sdk.secret import SecretSource, StaticSecret
from openhands.sdk.skills import (
    KeywordTrigger,
    Skill,
    TaskTrigger,
)
from openhands.sdk.skills.types import InputMetadata
from openhands.sdk.utils.cipher import Cipher


def test_agent_context_serialization_roundtrip():
    """Ensure AgentContext round-trips through dict and JSON serialization."""

    repo_skill = Skill(
        name="repo-guidelines",
        content="Repository guidelines",
        source="repo.md",
        trigger=None,
    )
    knowledge_skill = Skill(
        name="python-help",
        content="Use type hints in Python code",
        source="knowledge.md",
        trigger=KeywordTrigger(keywords=["python"]),
    )
    task_skill = Skill(
        name="run-task",
        content="Execute the task with ${param}",
        source="task.md",
        trigger=TaskTrigger(triggers=["run"]),
        inputs=[InputMetadata(name="param", description="Task parameter")],
    )

    context = AgentContext(
        skills=[repo_skill, knowledge_skill, task_skill],
        system_message_suffix="System suffix",
        user_message_suffix="User suffix",
    )

    serialized = context.model_dump()
    assert serialized["system_message_suffix"] == "System suffix"
    assert serialized["user_message_suffix"] == "User suffix"
    # First skill has trigger=None (always-active), others have specific triggers
    assert serialized["skills"][0]["trigger"] is None
    assert serialized["skills"][1]["trigger"]["type"] == "keyword"
    assert serialized["skills"][2]["trigger"]["type"] == "task"

    json_str = context.model_dump_json()
    parsed = json.loads(json_str)
    assert parsed["system_message_suffix"] == "System suffix"
    assert parsed["user_message_suffix"] == "User suffix"
    assert parsed["skills"][2]["inputs"][0]["name"] == "param"

    deserialized_from_dict = AgentContext.model_validate(serialized)
    assert isinstance(deserialized_from_dict.skills[0], Skill)
    assert deserialized_from_dict.skills[0].trigger is None
    assert deserialized_from_dict.skills[0] == repo_skill
    assert isinstance(deserialized_from_dict.skills[1], Skill)
    assert isinstance(deserialized_from_dict.skills[1].trigger, KeywordTrigger)
    assert deserialized_from_dict.skills[1] == knowledge_skill
    assert isinstance(deserialized_from_dict.skills[2], Skill)
    assert isinstance(deserialized_from_dict.skills[2].trigger, TaskTrigger)
    assert deserialized_from_dict.skills[2] == task_skill
    assert deserialized_from_dict.system_message_suffix == "System suffix"
    assert deserialized_from_dict.user_message_suffix == "User suffix"

    deserialized_from_json = AgentContext.model_validate_json(json_str)
    assert isinstance(deserialized_from_json.skills[0], Skill)
    assert deserialized_from_json.skills[0].trigger is None
    assert deserialized_from_json.skills[0] == repo_skill
    assert isinstance(deserialized_from_json.skills[1], Skill)
    assert isinstance(deserialized_from_json.skills[1].trigger, KeywordTrigger)
    assert deserialized_from_json.skills[1] == knowledge_skill
    assert isinstance(deserialized_from_json.skills[2], Skill)
    assert isinstance(deserialized_from_json.skills[2].trigger, TaskTrigger)
    assert deserialized_from_json.skills[2] == task_skill
    assert deserialized_from_json.model_dump() == serialized


def test_agent_context_secrets_round_trip_through_cipher_context():
    """``AgentContext.secrets`` raw-string values must round-trip cleanly
    when re-validated with a cipher.

    Regression for the same bug class as ``ACPAgent.acp_env``: the
    field has a ``field_serializer`` that encrypts under cipher
    context (via :func:`serialize_secret`) but until now had no
    matching ``field_validator``. So ciphertext survived
    ``StoredConversation.model_validate(..., context={'cipher': ...})``
    in the conversation-start flow and reached the agent's system
    prompt as ``gAAAA...`` instead of the configured value.
    """
    cipher = Cipher(urlsafe_b64encode(b"a" * 32).decode("ascii"))
    plaintext = {"GITHUB_TOKEN": "ghp-real-token", "DB_PASS": "pw"}

    ctx = AgentContext(secrets=plaintext)
    dumped = ctx.model_dump(mode="json", context={"cipher": cipher})
    # Sanity check: the dump produced Fernet ciphertext, not plaintext.
    for key, raw_value in plaintext.items():
        stored = dumped["secrets"][key]
        assert isinstance(stored, str)
        assert stored != raw_value
        assert stored.startswith("gAAAA")

    restored = AgentContext.model_validate(dumped, context={"cipher": cipher})
    assert restored.secrets == plaintext


def test_agent_context_secrets_plaintext_passes_through_with_cipher():
    """First writes from older clients carry plaintext. They must validate
    cleanly when cipher is present in context (no FERNET_TOKEN_PREFIX,
    no decryption attempted)."""
    cipher = Cipher(urlsafe_b64encode(b"a" * 32).decode("ascii"))
    ctx = AgentContext.model_validate(
        {"secrets": {"FOO": "plaintext-value"}},
        context={"cipher": cipher},
    )
    assert ctx.secrets == {"FOO": "plaintext-value"}


def test_agent_context_secrets_secret_source_passes_through_with_cipher():
    """``SecretSource`` entries serialize to a dict on the wire, so they
    must slip past ``validate_secret_dict``'s ``isinstance(value, str)``
    gate untouched while raw-string siblings are still decrypted.

    Locks in the invariant the ``_decrypt_secrets`` docstring describes:
    if ``SecretSource`` serialization ever produced a bare string, the
    str-gate would silently start mangling it and ciphertext could reach
    the prompt.
    """
    cipher = Cipher(urlsafe_b64encode(b"a" * 32).decode("ascii"))
    source = StaticSecret(value=SecretStr("source-secret"))
    ctx = AgentContext(secrets={"RAW": "plaintext", "SRC": source})

    dumped = ctx.model_dump(mode="json", context={"cipher": cipher})
    # The raw string is encrypted to a Fernet token; the SecretSource stays
    # a dict (its own nested ``value`` is the part that gets encrypted).
    assert dumped["secrets"]["RAW"].startswith("gAAAA")
    assert isinstance(dumped["secrets"]["SRC"], dict)

    restored = AgentContext.model_validate(dumped, context={"cipher": cipher})
    assert restored.secrets is not None
    assert restored.secrets["RAW"] == "plaintext"
    assert isinstance(restored.secrets["SRC"], SecretSource)
    assert restored.secrets["SRC"].get_value() == "source-secret"
