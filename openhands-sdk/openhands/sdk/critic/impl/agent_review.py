import json
import re
from collections.abc import Sequence

from openhands.sdk import Agent, Conversation
from openhands.sdk.critic.base import CriticBase, CriticResult
from openhands.sdk.event import LLMConvertibleEvent, SystemPromptEvent
from openhands.sdk.llm import LLM, TextContent
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)


class AgentReviewCritic(CriticBase):
    """Critic that spawns another OpenHands agent to perform a review.

    Important: this critic *forks* the current agent settings (tools, context,
    condenser, etc.) from the conversation events and uses the same LLM.

    This is intended to be used together with the Stop hook: when the main agent
    tries to finish, the Stop hook can call this critic on the current
    conversation's events + git patch, and deny stop if the critic says
    `not_pass`.
    """

    llm: LLM | None = None

    review_style: str = "roasted"
    max_diff_chars: int = 100_000

    def evaluate(
        self, events: Sequence[LLMConvertibleEvent], git_patch: str | None = None
    ) -> CriticResult:
        if not git_patch or not git_patch.strip():
            return CriticResult(score=0.0, message="Empty git patch")

        root_llm = self.llm or self._extract_llm(events)
        if root_llm is None:
            return CriticResult(score=0.0, message="Could not infer agent LLM")

        root_agent = self._extract_agent(events)
        if root_agent is None:
            return CriticResult(score=0.0, message="Could not infer agent settings")

        critic_agent = root_agent.model_copy(
            update={
                "llm": root_llm.model_copy(update={"usage_id": "critic_agent"}),
            },
            deep=True,
        )

        prompt = self._build_prompt(git_patch)
        conversation = Conversation(agent=critic_agent, workspace=".")
        conversation.send_message(prompt)
        conversation.run()

        final_text = self._extract_final_text(list(conversation.state.events))
        return self._parse_output(final_text)

    def _extract_llm(self, events: Sequence[LLMConvertibleEvent]) -> LLM | None:
        for event in events:
            agent = getattr(event, "agent", None)
            llm = getattr(agent, "llm", None)
            if isinstance(llm, LLM):
                return llm

        for event in events:
            if not isinstance(event, SystemPromptEvent):
                continue
            agent = getattr(event, "agent", None)
            llm = getattr(agent, "llm", None)
            if isinstance(llm, LLM):
                return llm

        return None

    def _extract_agent(self, events: Sequence[LLMConvertibleEvent]) -> Agent | None:
        for event in events:
            agent = getattr(event, "agent", None)
            if isinstance(agent, Agent):
                return agent

        for event in events:
            if not isinstance(event, SystemPromptEvent):
                continue
            agent = getattr(event, "agent", None)
            if isinstance(agent, Agent):
                return agent

        return None

    def _build_prompt(self, git_patch: str) -> str:
        style = (
            "/codereview-roasted" if self.review_style == "roasted" else "/codereview"
        )
        patch = git_patch
        if len(patch) > self.max_diff_chars:
            patch = patch[: self.max_diff_chars] + (
                "\n\n... [diff truncated, "
                f"{len(git_patch):,} chars total, showing first "
                f"{self.max_diff_chars:,}] ...\n"
            )
        return (
            f"{style}\n\n"
            "You are a PR critic. Review the git diff and decide if it is ready "
            "to merge.\n"
            "Return your result as JSON in the last code block with keys: "
            "decision (pass|not_pass), summary.\n\n"
            "```diff\n"
            f"{patch}\n"
            "```\n"
        )

    def _extract_final_text(self, events: Sequence[object]) -> str:
        for event in reversed(list(events)):
            llm_msg = getattr(event, "llm_message", None)
            if llm_msg is None or not getattr(llm_msg, "content", None):
                continue
            parts: list[str] = []
            for c in llm_msg.content:
                if isinstance(c, TextContent):
                    parts.append(c.text)
            if parts:
                return "".join(parts)
        return ""

    def _parse_output(self, text: str) -> CriticResult:
        blocks = re.findall(r"```json\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
        candidate = blocks[-1] if blocks else None
        if candidate is None:
            m = re.search(
                r"(\{\s*\"decision\"\s*:\s*\"(pass|not_pass)\".*\})",
                text,
                re.DOTALL,
            )
            candidate = m.group(1) if m else None

        if not candidate:
            logger.warning("Critic output missing JSON block")
            return CriticResult(score=0.0, message="Critic output missing JSON")

        try:
            data = json.loads(candidate)
        except Exception as e:
            logger.warning(f"Failed to parse critic JSON: {e}")
            return CriticResult(score=0.0, message="Critic JSON parse error")

        decision = str(data.get("decision", "not_pass")).strip()
        score = 1.0 if decision == "pass" else 0.0
        summary = str(data.get("summary", "") or "").strip() or decision
        return CriticResult(score=score, message=summary)
