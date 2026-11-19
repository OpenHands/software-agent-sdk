"""Tests for SecretRegistry credential rotation masking (#7002)."""

import json
from contextlib import closing
from pathlib import Path

from openhands.sdk import Agent, LocalConversation, Message, TextContent, Tool
from openhands.sdk.conversation.secret_registry import SecretRegistry
from openhands.sdk.llm import MessageToolCall
from openhands.sdk.secret import SecretSource
from openhands.sdk.testing import TestLLM
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.terminal import TerminalTool


class RotatingSecretSource(SecretSource):
    """Returns a fresh secret value on each get_value() call."""

    prefix: str = "dyn_oauth"
    calls: int = 0

    def get_value(self) -> str:
        self.calls += 1
        return f"{self.prefix}_v{self.calls}_secret_token"


class AlwaysFailingSecretSource(SecretSource):
    """Raises an OSError on lookup to exercise _failed_lookups backoff."""

    calls: int = 0

    def get_value(self) -> str:
        self.calls += 1
        raise OSError("vault temporarily unavailable")


def test_update_secrets_masks_both_new_and_previous_values_immediately() -> None:
    """After update_secrets() rotates an existing key, mask_secrets_in_output
    masks the newly updated secret immediately (without requiring a terminal
    command to reference $KEY) AND retains masking of the previous value even
    after $KEY is re-exported.
    """
    reg = SecretRegistry()
    reg.update_secrets({"DEPLOY_TOKEN": "static_tok_v1_secret"})

    assert (
        reg.mask_secrets_in_output("init static_tok_v1_secret")
        == "init <secret-hidden>"
    )

    # Rotate via update_secrets (e.g. POST /api/conversations/{id}/secrets)
    reg.update_secrets({"DEPLOY_TOKEN": "static_tok_v2_secret"})

    # 1. Active v2 value is masked immediately without any command referencing it
    masked_both = reg.mask_secrets_in_output(
        "old=static_tok_v1_secret new=static_tok_v2_secret"
    )
    assert masked_both == "old=<secret-hidden> new=<secret-hidden>"
    masked_msg = reg.mask_secrets_in_model(
        Message(
            role="assistant",
            content=[
                TextContent(
                    text="model old=static_tok_v1_secret new=static_tok_v2_secret"
                )
            ],
        )
    )
    assert masked_msg.content[0].text == "model old=<secret-hidden> new=<secret-hidden>"

    # 2. Exporting $DEPLOY_TOKEN returns v2 and STILL masks v1 afterwards
    env = reg.get_secrets_as_env_vars("deploy --token $DEPLOY_TOKEN")
    assert env == {"DEPLOY_TOKEN": "static_tok_v2_secret"}
    assert (
        reg.mask_secrets_in_output(
            "remote=static_tok_v1_secret env=static_tok_v2_secret"
        )
        == "remote=<secret-hidden> env=<secret-hidden>"
    )

    # 3. Streaming masker also masks both historical and active values
    stream = reg.compile_stream_mask()
    chunked = (
        stream.feed("a=static_tok_v1_")
        + stream.feed("secret b=static_tok_v2_secret")
        + stream.flush()
    )
    assert chunked == "a=<secret-hidden> b=<secret-hidden>"


def test_rotating_secret_source_retains_all_previously_exported_values() -> None:
    """When a dynamic SecretSource / LookupSecret rotates across exports,
    every previously exported token remains masked alongside the latest token.
    """
    source = RotatingSecretSource(prefix="ghs_install")
    reg = SecretRegistry()
    reg.update_secrets({"GITHUB_TOKEN": source})

    # Initial output masking resolves v1
    assert (
        reg.mask_secrets_in_output("first=ghs_install_v1_secret_token")
        == "first=<secret-hidden>"
    )

    # Two subsequent terminal exports issue v2 and v3
    env2 = reg.get_secrets_as_env_vars("git push # $GITHUB_TOKEN")
    env3 = reg.get_secrets_as_env_vars("gh pr list # $GITHUB_TOKEN")
    assert env2["GITHUB_TOKEN"] == "ghs_install_v2_secret_token"
    assert env3["GITHUB_TOKEN"] == "ghs_install_v3_secret_token"

    text = (
        "v1=ghs_install_v1_secret_token "
        "v2=ghs_install_v2_secret_token "
        "v3=ghs_install_v3_secret_token"
    )
    assert (
        reg.mask_secrets_in_output(text)
        == "v1=<secret-hidden> v2=<secret-hidden> v3=<secret-hidden>"
    )

    stream = reg.compile_stream_mask()
    assert (
        stream.feed(text) + stream.flush()
        == "v1=<secret-hidden> v2=<secret-hidden> v3=<secret-hidden>"
    )


def test_update_secrets_clears_stale_failed_lookup_backoff() -> None:
    """Replacing a failing secret via update_secrets clears _failed_lookups so
    the replacement secret is masked immediately instead of waiting 60s.
    """
    failing = AlwaysFailingSecretSource()
    reg = SecretRegistry()
    reg.update_secrets({"VAULT_TOKEN": failing})

    assert reg.mask_secrets_in_output("probe") == "probe"
    assert "VAULT_TOKEN" in reg._failed_lookups
    assert failing.calls == 1

    reg.update_secrets({"VAULT_TOKEN": "recovered_vault_secret_999"})
    assert "VAULT_TOKEN" not in reg._failed_lookups
    assert (
        reg.mask_secrets_in_output("value=recovered_vault_secret_999")
        == "value=<secret-hidden>"
    )


def test_local_conversation_secret_rotation_masks_file_view_and_persisted_events(
    tmp_path: Path,
) -> None:
    """End-to-end LocalConversation: mid-session update_secrets() masks both
    the newly written secret in file_editor view (no $TOKEN command reference)
    and the prior token in terminal output, with zero cleartext in persistence.
    """
    tok_v1 = "deploy_secret_v1_alpha98765"
    tok_v2 = "deploy_secret_v2_bravo54321"
    env_file = tmp_path / "service.env"
    env_file.write_text(f"OLD={tok_v1}\nNEW={tok_v2}\n")
    persist_dir = tmp_path / "persist"

    llm = TestLLM.from_messages(
        [
            Message(
                role="assistant",
                content=[TextContent(text="")],
                tool_calls=[
                    MessageToolCall(
                        id="c1",
                        name=TerminalTool.name,
                        arguments=json.dumps(
                            {"command": "echo exported=$DEPLOY_TOKEN"}
                        ),
                        origin="completion",
                    )
                ],
            ),
            Message(role="assistant", content=[TextContent(text="turn 1 done")]),
            Message(
                role="assistant",
                content=[TextContent(text="")],
                tool_calls=[
                    MessageToolCall(
                        id="c2",
                        name=FileEditorTool.name,
                        arguments=json.dumps(
                            {"command": "view", "path": str(env_file)}
                        ),
                        origin="completion",
                    ),
                    MessageToolCall(
                        id="c3",
                        name=TerminalTool.name,
                        arguments=json.dumps(
                            {"command": (f"echo cur=$DEPLOY_TOKEN && cat {env_file}")}
                        ),
                        origin="completion",
                    ),
                ],
            ),
            Message(
                role="assistant",
                content=[TextContent(text=f"Checked {tok_v1} and {tok_v2}.")],
            ),
        ]
    )

    with closing(
        LocalConversation(
            agent=Agent(
                llm=llm,
                tools=[
                    Tool(
                        name=TerminalTool.name,
                        params={"terminal_type": "subprocess"},
                    ),
                    Tool(name=FileEditorTool.name),
                ],
            ),
            workspace=tmp_path,
            persistence_dir=persist_dir,
            visualizer=None,
            secrets={"DEPLOY_TOKEN": tok_v1},
        )
    ) as conv:
        conv.send_message("Run turn 1")
        conv.run()

        # Rotate DEPLOY_TOKEN mid-conversation before turn 2
        conv.update_secrets({"DEPLOY_TOKEN": tok_v2})
        conv.send_message("Inspect service.env and verify token")
        conv.run()

    # Check persisted event files (excluding raw model ActionEvent arguments if any)
    event_files = list(persist_dir.rglob("events/*.json"))
    assert event_files
    for ef in event_files:
        raw = ef.read_text()
        assert tok_v1 not in raw, f"Prior rotated secret leaked in {ef}"
        assert tok_v2 not in raw, f"Active updated secret leaked in {ef}"
