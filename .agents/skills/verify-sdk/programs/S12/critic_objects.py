"""The critic objects a program builds and reads: results and their display,
the rule-based critics run over a real (scripted) conversation's events, the
refinement config, APIBasedCritic's refine policy, follow-up prompt and API
key handling, the Qwen chat template, and a critic built from settings.
"""

import json
from pathlib import Path

from pydantic import SecretStr, ValidationError
from rich.console import Console
from sdk_verify import emit, make_llm, run_paths

from openhands.sdk import LLM, Agent, Conversation, Tool
from openhands.sdk.critic import (
    AgentFinishedCritic,
    APIBasedCritic,
    CriticBase,
    CriticResult,
    EmptyPatchCritic,
    IterativeRefinementConfig,
    PassCritic,
)
from openhands.sdk.critic.impl.api.chat_template import ChatTemplateRenderer
from openhands.sdk.critic.impl.api.taxonomy import categorize_features
from openhands.sdk.event import LLMConvertibleEvent
from openhands.sdk.settings import OpenHandsAgentSettings, VerificationSettings
from openhands.sdk.utils.cipher import Cipher
from openhands.tools.terminal import TerminalTool


REPO = Path(__file__).resolve().parents[5]
ws = run_paths()["workspace"]


def raises(fn) -> bool:
    try:
        fn()
        return False
    except (ValidationError, ValueError, TypeError):
        return True


def rendered(text) -> str:
    console = Console(width=200, record=True, file=open("/dev/null", "w"))
    console.print(text)
    return console.export_text()


# Events of a finished scripted conversation, and of one that never finished.
def events_of(script: list) -> list:
    conversation = Conversation(
        agent=Agent(llm=make_llm(script), tools=[Tool(name=TerminalTool.name)]),
        workspace=str(ws),
        visualizer=None,
    )
    conversation.send_message("Change a file.")
    conversation.run()
    events = [
        e for e in conversation.state.events if isinstance(e, LLMConvertibleEvent)
    ]
    conversation.close()
    return events


finished = events_of(
    [{"tool": "terminal", "args": {"command": "echo x > f.txt"}}, {"finish": "done"}]
)
unfinished = events_of(
    [{"tool": "terminal", "args": {"command": "echo y"}}, {"text": "I stopped."}]
)
patch = "diff --git a/f.txt b/f.txt\n+x\n"

# Probabilities as the critic service returns them, grouped the way
# APIBasedCritic.evaluate does it (display threshold 0.2, sorted).
categorized = categorize_features(
    {
        "success": 0.62,
        "loop_behavior": 0.3,
        "insufficient_testing": 0.8,
        "scope_creep": 0.1,
        "correction": 0.4,
        "infrastructure_external_issue": 0.3,
        "sentiment_positive": 0.2,
        "sentiment_neutral": 0.3,
        "sentiment_negative": 0.5,
    }
)
scored = CriticResult(
    score=0.62, message="ok", metadata={"categorized_features": categorized}
)
plain = CriticResult(score=0.2, message='{"success": 0.2, "loop_behavior": 0.7}')

api = APIBasedCritic(
    api_key="sk-critic",
    iterative_refinement=IterativeRefinementConfig(
        success_threshold=0.6, max_iterations=2
    ),
)
low_issue = CriticResult(
    score=0.9,
    message="",
    metadata={
        "categorized_features": {
            "agent_behavioral_issues": [{"name": "loop_behavior", "probability": 0.5}]
        }
    },
)
high_issue = CriticResult(
    score=0.9,
    message="",
    metadata={
        "categorized_features": {
            "agent_behavioral_issues": [{"name": "loop_behavior", "probability": 0.8}]
        }
    },
)


class AlwaysRetry(PassCritic):
    """A program's own policy: always one more round, with its own prompt."""

    def should_refine(self, critic_result: CriticResult) -> bool:  # noqa: ARG002
        return True

    def get_followup_prompt(
        self,
        critic_result: CriticResult,  # noqa: ARG002
        iteration: int,
    ) -> str:
        return f"Round {iteration}: try again."


# API keys
cipher = Cipher(secret_key="verify-secret")
agent_with_critic = Agent(
    llm=LLM(model="openai/gpt-4o-mini", usage_id="a"),
    critic=APIBasedCritic(api_key="sk-nested"),
)
agent_dump = json.dumps(agent_with_critic.model_dump(mode="json"))
agent_exposed = json.dumps(
    agent_with_critic.model_dump(mode="json", context={"expose_secrets": True})
)
agent_ciphered = agent_with_critic.model_dump(mode="json", context={"cipher": cipher})
agent_restored = Agent.model_validate(agent_ciphered, context={"cipher": cipher})


def no_key_inference() -> str | None:
    try:
        APIBasedCritic(api_key="   ")._get_api_key_value()
        return None
    except ValueError as exc:
        return str(exc)


# Chat template (fixture copy of Qwen/Qwen3-4B-Instruct-2507's tokenizer_config.json)
fixture = (
    REPO / "tests/fixtures/tokenizers/qwen3-4b-instruct-2507-tokenizer_config.json"
)
renderer = ChatTemplateRenderer(
    chat_template=json.loads(fixture.read_text())["chat_template"]
)
tool = {
    "type": "function",
    "function": {
        "name": "terminal",
        "description": "Run",
        "parameters": {"type": "object", "properties": {}},
    },
}


# Settings
def settings_critic(**verification):
    llm_key = verification.pop("llm_key", "llm-key")
    return (
        OpenHandsAgentSettings(
            llm=LLM(
                model="openai/gpt-4o-mini",
                api_key=SecretStr(llm_key) if llm_key else None,
            ),
            verification=VerificationSettings(**verification),
        )
        .create_agent()
        .critic
    )


from_settings = settings_critic(
    critic_enabled=True,
    critic_mode="all_actions",
    enable_iterative_refinement=True,
    critic_threshold=0.7,
    max_refinement_iterations=4,
    critic_server_url="http://127.0.0.1:9/vllm",
    critic_model_name="my-critic",
    critic_api_key="critic-key",
)
fallback = settings_critic(critic_enabled=True)
no_key = settings_critic(critic_enabled=True, llm_key=None)
disabled = settings_critic(critic_enabled=False)

emit(
    {
        "ok": True,
        # S12.critic-result
        "success_at_half": CriticResult(score=0.5, message=None).success,
        "success_below_half": CriticResult(score=0.49, message=None).success,
        "score_above_one_rejected": raises(
            lambda: CriticResult(score=1.1, message=None)
        ),
        "score_below_zero_rejected": raises(
            lambda: CriticResult(score=-0.1, message=None)
        ),
        # S12.critic-display
        "display_scored": rendered(scored.visualize),
        "display_plain": rendered(plain.visualize),
        # S12.rule-critics
        "pass": PassCritic().evaluate(finished).score,
        "empty_patch_none": EmptyPatchCritic().evaluate(finished, git_patch=None).score,
        "empty_patch_blank": EmptyPatchCritic()
        .evaluate(finished, git_patch="  \n")
        .score,
        "empty_patch_with_patch": EmptyPatchCritic()
        .evaluate(finished, git_patch=patch)
        .score,
        "finished_with_patch": AgentFinishedCritic()
        .evaluate(finished, git_patch=patch)
        .score,
        "finished_no_patch": AgentFinishedCritic()
        .evaluate(finished, git_patch="")
        .score,
        "unfinished_with_patch": AgentFinishedCritic()
        .evaluate(unfinished, git_patch=patch)
        .score,
        "critic_base_abstract": raises(lambda: CriticBase()),  # type: ignore[abstract]
        # S12.refinement-config
        "config_defaults": IterativeRefinementConfig().model_dump(),
        "threshold_above_one_rejected": raises(
            lambda: IterativeRefinementConfig(success_threshold=1.5)
        ),
        "threshold_below_zero_rejected": raises(
            lambda: IterativeRefinementConfig(success_threshold=-0.1)
        ),
        "zero_iterations_rejected": raises(
            lambda: IterativeRefinementConfig(max_iterations=0)
        ),
        # S12.custom-refine-policy
        "api_refines_low_score": api.should_refine(
            CriticResult(score=0.3, message=None)
        ),
        "api_stops_at_threshold": api.should_refine(
            CriticResult(score=0.6, message=None)
        ),
        "api_issue_below_threshold": api.should_refine(low_issue),
        "api_issue_above_threshold": api.should_refine(high_issue),
        "api_issue_threshold_default": api.issue_threshold,
        "no_config_never_refines": APIBasedCritic(api_key="k").should_refine(
            high_issue
        ),
        "subclass_refines": AlwaysRetry().should_refine(
            CriticResult(score=1.0, message=None)
        ),
        "subclass_prompt": AlwaysRetry().get_followup_prompt(
            CriticResult(score=1.0, message=None), 2
        ),
        # S12.api-followup-prompt
        "api_followup": api.get_followup_prompt(scored, 1),
        "base_followup": PassCritic().get_followup_prompt(plain, 3),
        # S12.api-key-handling
        "key_str_is_secret": isinstance(
            APIBasedCritic(api_key="sk-a").api_key, SecretStr
        ),
        "key_secretstr": APIBasedCritic(
            api_key=SecretStr("sk-b")
        ).api_key.get_secret_value(),
        "key_empty_is_none": APIBasedCritic(api_key="").api_key is None,
        "key_blank_is_none": APIBasedCritic(api_key="   ").api_key is None,
        "key_redacted_is_none": APIBasedCritic(api_key="**********").api_key is None,
        "no_key_inference_error": no_key_inference(),
        "repr_hides_key": "sk-critic" not in repr(api),
        "dump_hides_key": "sk-critic" not in json.dumps(api.model_dump(mode="json")),
        "expose_shows_key": APIBasedCritic(api_key="sk-x").model_dump(
            mode="json", context={"expose_secrets": True}
        )["api_key"],
        "agent_dump_hides_key": "sk-nested" not in agent_dump,
        "agent_expose_shows_key": "sk-nested" in agent_exposed,
        "agent_cipher_hides_key": "sk-nested" not in json.dumps(agent_ciphered),
        "agent_cipher_restores_key": agent_restored.critic.api_key.get_secret_value()  # type: ignore[union-attr]
        if agent_restored.critic and agent_restored.critic.api_key
        else None,
        # S12.chat-template
        # the transformers rendering of a one-message conversation
        "template_user_exact": renderer.apply_chat_template(
            [{"role": "user", "content": "Hello!"}]
        )
        == "<|im_start|>user\nHello!<|im_end|>\n",
        "template_system_first": renderer.apply_chat_template(
            [
                {"role": "system", "content": "Be brief."},
                {"role": "user", "content": "Hi"},
            ]
        ).startswith("<|im_start|>system\nBe brief."),
        "template_tools": "<tools>"
        in renderer.apply_chat_template(
            [{"role": "user", "content": "Hi"}], tools=[tool]
        )
        and '"name": "terminal"'
        in renderer.apply_chat_template(
            [{"role": "user", "content": "Hi"}], tools=[tool]
        ),
        "template_generation_prompt": renderer.apply_chat_template(
            [{"role": "user", "content": "Hi"}], add_generation_prompt=True
        ).endswith("<|im_start|>assistant\n"),
        # S12.settings-critic
        "settings_critic": type(from_settings).__name__,
        "settings_mode": from_settings.mode if from_settings else None,
        "settings_threshold": from_settings.iterative_refinement.success_threshold
        if from_settings and from_settings.iterative_refinement
        else None,
        "settings_max_iterations": from_settings.iterative_refinement.max_iterations
        if from_settings and from_settings.iterative_refinement
        else None,
        "settings_server_url": getattr(from_settings, "server_url", None),
        "settings_model_name": getattr(from_settings, "model_name", None),
        "settings_key": from_settings.api_key.get_secret_value()
        if from_settings and from_settings.api_key
        else None,
        "fallback_key": fallback.api_key.get_secret_value()
        if fallback and fallback.api_key
        else None,
        "fallback_refinement": fallback.iterative_refinement
        if fallback
        else "no critic",
        "no_key_critic": no_key,
        "disabled_critic": disabled,
    }
)
