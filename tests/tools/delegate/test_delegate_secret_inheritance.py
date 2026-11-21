"""Tests for DelegateExecutor secret inheritance & rotation masking (#7002)."""

import json
import warnings
from contextlib import closing
from pathlib import Path

import pytest

from openhands.sdk import Agent, LocalConversation, Message, TextContent, Tool
from openhands.sdk.llm import MessageToolCall
from openhands.sdk.secret import SecretSource
from openhands.sdk.subagent.registry import _reset_registry_for_tests, register_agent
from openhands.sdk.testing import TestLLM
from openhands.tools.delegate import DelegateExecutor
from openhands.tools.delegate.definition import DelegateAction
from openhands.tools.terminal import TerminalTool


class DelegateRotatingSecretSource(SecretSource):
    calls: int = 0

    def get_value(self) -> str:
        self.calls += 1
        return f"delegate_dyn_secret_v{self.calls}_xyz"


@pytest.fixture(autouse=True)
def _clean_registry():
    _reset_registry_for_tests()
    yield
    _reset_registry_for_tests()


def _call(name: str, **arguments: str) -> Message:
    return Message(
        role="assistant",
        content=[TextContent(text="")],
        tool_calls=[
            MessageToolCall(
                id=f"call_{name}",
                name=name,
                arguments=json.dumps(arguments),
                origin="completion",
            )
        ],
    )


def test_delegate_executor_inherits_secrets_and_masks_rotated_values(
    tmp_path: Path,
) -> None:
    """DelegateExecutor propagates parent secret_sources and historical masked
    values to sub-agents, updates secrets rotated between spawn and delegate,
    and syncs sub-agent-resolved dynamic secrets back to the parent.
    """
    tok_v1 = "delegate_static_v1_secret_111"
    tok_v2 = "delegate_static_v2_secret_222"
    dyn_source = DelegateRotatingSecretSource()

    legacy_file = tmp_path / "legacy.txt"
    legacy_file.write_text(f"prior={tok_v1}\n")

    register_agent(
        "del-secret-worker",
        lambda llm: Agent(
            llm=llm,
            tools=[
                Tool(
                    name=TerminalTool.name,
                    params={"terminal_type": "subprocess"},
                )
            ],
        ),
        "Delegate secret worker",
    )

    cmd = f'cat "{legacy_file}" && echo S=$STATIC_SEC && echo D=$DYN_SEC'
    llm = TestLLM.from_messages(
        [
            _call("terminal", command=cmd),
            Message(
                role="assistant",
                content=[TextContent(text=f"Worker saw {tok_v1} and {tok_v2}")],
            ),
        ]
    )

    persist_dir = tmp_path / "persist"
    with closing(
        LocalConversation(
            agent=Agent(llm=llm),
            workspace=tmp_path,
            persistence_dir=persist_dir,
            visualizer=None,
            secrets={"STATIC_SEC": tok_v1, "DYN_SEC": dyn_source},
        )
    ) as parent:
        assert (
            parent.state.secret_registry.mask_secrets_in_output(tok_v1)
            == "<secret-hidden>"
        )
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            executor = DelegateExecutor()
            try:
                spawn_obs = executor(
                    DelegateAction(
                        command="spawn",
                        ids=["w1"],
                        agent_types=["del-secret-worker"],
                    ),
                    parent,
                )
                assert not spawn_obs.is_error

                # Rotate STATIC_SEC after spawn, before delegate
                parent.update_secrets({"STATIC_SEC": tok_v2})

                del_obs = executor(
                    DelegateAction(
                        command="delegate",
                        tasks={"w1": "Inspect secrets"},
                    ),
                    parent,
                )
                assert not del_obs.is_error
                masked_obs = parent.state.secret_registry.mask_secrets_in_model(del_obs)
                assert tok_v1 not in masked_obs.text
                assert tok_v2 not in masked_obs.text
                # Worker resolved v2 of DYN_SEC; parent must now mask it too
                assert (
                    parent.state.secret_registry.mask_secrets_in_output(
                        "delegate_dyn_secret_v2_xyz"
                    )
                    == "<secret-hidden>"
                )
            finally:
                executor.close()

    for path in persist_dir.rglob("events/*.json"):
        raw = path.read_text()
        assert tok_v1 not in raw
        assert tok_v2 not in raw
        assert "delegate_dyn_secret_v1_xyz" not in raw
        assert "delegate_dyn_secret_v2_xyz" not in raw
