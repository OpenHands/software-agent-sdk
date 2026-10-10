"""Keyword-triggered skill content across condensation (issue #4544).

Scenario, end to end against a real LLM:

1. A keyword-triggered skill is installed on the agent.
2. Filler turns push the activating turn outside the condenser's protected
   ``keep_first`` prefix.
3. A user message containing the keyword activates the skill, which injects the
   body into the conversation and records the name in
   ``state.activated_knowledge_skills``.
4. The agent does real work, then a real summarizing condensation runs.
5. The keyword is sent again.

The invariant asserted is the one the issue asks the maintainers to confirm:
**after condensation, triggered content is either still present in the active
view, or recoverable when the trigger fires again.** Any of the three candidate
mechanisms (preserve, reconstruct, reactivate) satisfies it.

On current main it does not hold, so this test FAILS by design and its failure
message is the diagnosis. That is deliberate: the ``c*`` suite is optional and
non-blocking, and a maintainer asked for a live run of the whole scenario to
settle whether re-inclusion on a fresh trigger is the intended contract.

The sentinel sits deliberately PAST ``N_CHAR_PREVIEW`` (500) inside the skill
body. ``MessageEvent.__str__()`` truncates the TAIL, so a marker near the front
survives truncation, reaches the summarizer, and a capable model will carry a
distinctive token into its summary. An earlier draft did exactly that and passed
against a real model while the bug was live. Beyond the cap the marker provably
never reaches the summarizer, so the scenario stays deterministic with a real
model in the loop.
"""

from openhands.sdk import Tool
from openhands.sdk.context.agent_context import AgentContext
from openhands.sdk.context.condenser import LLMSummarizingCondenser
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.event.condenser import Condensation
from openhands.sdk.event.llm_convertible import MessageEvent
from openhands.sdk.skills import KeywordTrigger, Skill
from openhands.sdk.tool import register_tool
from openhands.tools.terminal import TerminalTool
from tests.integration.base import BaseIntegrationTest, TestResult


SENTINEL = "PRESERVE_EXACT_SENTINEL_7F3A"

# The sentinel must sit PAST N_CHAR_PREVIEW (500), not merely inside a body
# longer than it. __str__ truncates the TAIL, so a front-loaded marker survives
# and reaches the summarizer -- see the module docstring.
_PREAMBLE = (
    "Project convention for Python work in this repository. "
    "Always wrap repository access in try-finally and release the handle in "
    "the finally block, even on the error path. Prefer explicit resource "
    "lifetimes over context managers when the handle outlives the block. "
) * 4
SKILL_BODY = f"{_PREAMBLE}\n\nSpecific rule marker: {SENTINEL}\n"


INSTRUCTION: str = "This test defines its own instructions in run_instructions()."


class TriggeredSkillCondensationTest(BaseIntegrationTest):
    """Keyword-skill content must survive condensation or come back on retrigger."""

    INSTRUCTION: str = INSTRUCTION

    def __init__(self, *args, **kwargs):
        self.condensations: list[Condensation] = []
        # Facts recorded as the scenario runs, so verify_result can tell a
        # genuine reproduction apart from a scenario that never set itself up.
        self.activated_before: list[str] = []
        self.sentinel_in_view_before: bool = False
        self.skill_index_before: int | None = None
        self.skill_event_id: str | None = None
        self.carrier_forgotten: bool = False
        self.view_map_after: list[str] = []
        self.sentinel_echoed_after: bool = False
        self.keep_first: int = 4
        self.activated_after: list[str] = []
        self.sentinel_in_view_after: bool = False
        self.retrigger_activated_skills: list[str] = []
        self.retrigger_extended_content: list[str] = []
        super().__init__(*args, **kwargs)

    @property
    def tools(self) -> list[Tool]:
        register_tool("TerminalTool", TerminalTool)
        return [Tool(name="TerminalTool")]

    @property
    def agent_context(self) -> AgentContext:
        return AgentContext(
            skills=[
                Skill(
                    name="python_tips",
                    content=SKILL_BODY,
                    source="python-tips.md",
                    trigger=KeywordTrigger(keywords=["python"]),
                )
            ]
        )

    @property
    def condenser(self) -> LLMSummarizingCondenser:
        condenser_llm = self.create_llm_copy("test-condenser-llm")
        return LLMSummarizingCondenser(
            llm=condenser_llm,
            # High enough that condensation only happens where the scenario
            # asks for it explicitly, never mid-scenario by surprise.
            max_size=100,
            keep_first=self.keep_first,
        )

    @property
    def max_iteration_per_run(self) -> int:
        return 30

    def conversation_callback(self, event):
        super().conversation_callback(event)
        if isinstance(event, Condensation):
            self.condensations.append(event)

    @staticmethod
    def _sentinel_in_injection_channel(conversation: LocalConversation) -> bool:
        """Is the skill text still present through the MECHANISM that delivers it?

        Only ``extended_content`` counts, because only that is deterministic.
        A summarizer may happen to echo the text into its summary when the agent
        quoted it back, which reads as survival but is a coin flip: strong models
        do it, weaker ones do not, and the same model does not do it every run.
        Observed directly here, as a real intermittent PASS on Opus 5.

        This matches what the issue asks for: the active context should retain or
        recover triggered content *deterministically*. Counting an echo would let
        the test agree that a maybe is a contract.
        """
        for event in conversation.state.view.events:
            extended = getattr(event, "extended_content", None) or []
            if any(SENTINEL in c.text for c in extended):
                return True
        return False

    @staticmethod
    def _sentinel_echoed_only(conversation: LocalConversation) -> bool:
        """Marker present in rendered text but NOT in the injection channel."""
        for event in conversation.state.view.events:
            if SENTINEL in str(event):
                return True
        return False

    @staticmethod
    def _view_map(conversation: LocalConversation) -> list[str]:
        """Per-event map of where the marker survives, for the verdict message.

        A verdict that says only "present" or "absent" leaves the reader
        guessing which channel carried it. ``ext`` = the injection channel
        (``extended_content``, what actually reaches the model); ``str`` = the
        rendered text, which is what the summarizer is fed.
        """
        out = []
        for event in conversation.state.view.events:
            extended = getattr(event, "extended_content", None) or []
            tags = ""
            if any(SENTINEL in c.text for c in extended):
                tags += "+ext"
            if SENTINEL in str(event):
                tags += "+str"
            out.append(f"{type(event).__name__}{tags}")
        return out

    def run_instructions(self, conversation: LocalConversation) -> None:
        # 1. Filler ahead of the activating turn, so the skill-bearing event
        #    lands outside the protected prefix and the failure cannot be
        #    written off as a keep_first artifact.
        for i in range(self.keep_first):
            conversation.send_message(f"Noting context item {i}; no action needed yet.")

        # 2. Activate the skill and have the agent do real work with it present.
        conversation.send_message(
            "Using python, print the numbers 1 through 3 with three separate "
            "echo commands."
        )
        conversation.run()

        self.activated_before = list(conversation.state.activated_knowledge_skills)
        self.sentinel_in_view_before = self._sentinel_in_injection_channel(conversation)
        for idx, event in enumerate(conversation.state.view.events):
            carries = any(
                SENTINEL in c.text for c in getattr(event, "extended_content", []) or []
            ) or SENTINEL in str(event)
            if isinstance(event, MessageEvent) and carries:
                self.skill_index_before = idx
                self.skill_event_id = event.id
                break

        # 3. Trailing turns so the activating event sits well inside the
        #    condensable range. Without these the explicit condensation can
        #    summarize a window that never includes it, the content stays in
        #    view for a reason that has nothing to do with the contract, and
        #    the test passes while the bug is live. Found exactly that way.
        for i in range(10):
            conversation.send_message(f"Follow-up note {i}; still no action needed.")

        # 4. Real summarizing condensation through the public API.
        conversation.condense()

        self.activated_after = list(conversation.state.activated_knowledge_skills)
        self.sentinel_in_view_after = self._sentinel_in_injection_channel(conversation)
        self.view_map_after = self._view_map(conversation)
        self.sentinel_echoed_after = self._sentinel_echoed_only(conversation)
        self.carrier_forgotten = not any(
            e.id == self.skill_event_id for e in conversation.state.view.events
        )

        # 5. Fire the same trigger again and look at what the new turn carries.
        conversation.send_message("One more python question, same conventions apply.")
        last = conversation.state.events[-1]
        if isinstance(last, MessageEvent):
            self.retrigger_activated_skills = list(last.activated_skills)
            self.retrigger_extended_content = [c.text for c in last.extended_content]

    def verify_result(self) -> TestResult:
        # --- preconditions: did the scenario actually happen? ---
        # A precondition failure is a broken test, not a reproduction, and the
        # reason string has to say so or the two become indistinguishable.
        if "python_tips" not in self.activated_before:
            return TestResult(
                success=False,
                reason=(
                    "SCENARIO NOT SET UP: the skill never activated "
                    f"(activated_knowledge_skills={self.activated_before}). "
                    "Nothing about #4544 was exercised."
                ),
            )
        if not self.sentinel_in_view_before:
            return TestResult(
                success=False,
                reason=(
                    "SCENARIO NOT SET UP: skill activated but its body was not "
                    "in the view before condensation, so there was nothing to lose."
                ),
            )
        skill_idx = self.skill_index_before
        if skill_idx is not None and skill_idx < self.keep_first:
            return TestResult(
                success=False,
                reason=(
                    "SCENARIO NOT SET UP: the skill-bearing event landed at view "
                    f"index {skill_idx}, inside the protected "
                    f"keep_first={self.keep_first} prefix. Add filler turns."
                ),
            )
        if not self.condensations:
            return TestResult(
                success=False,
                reason="SCENARIO NOT SET UP: no Condensation event was produced.",
            )
        if not self.carrier_forgotten:
            # The decisive guard. If condensation never dropped the event that
            # carried the skill, nothing was ever at risk, and "content is
            # still in view" says nothing about the lifecycle contract.
            return TestResult(
                success=False,
                reason=(
                    "SCENARIO NOT SET UP: condensation did not forget the "
                    "skill-bearing event, so the content was never at risk. "
                    "Add trailing turns or lower the condenser's max_size until "
                    "the carrying event falls inside the condensable range."
                ),
            )

        # --- the invariant ---
        still_present = self.sentinel_in_view_after
        recovered = any(SENTINEL in text for text in self.retrigger_extended_content)
        if still_present or recovered:
            return TestResult(
                success=True,
                reason=(
                    f"view={self.view_map_after} "
                    f"Skill content survived through the injection "
                    f"channel (present_in_view={still_present}, "
                    f"recovered_on_retrigger={recovered})."
                ),
            )
        if self.sentinel_echoed_after:
            return TestResult(
                success=False,
                reason=(
                    "SUMMARY-ECHO ONLY, not a contract: the marker appears in "
                    "rendered text but NOT in the injection channel. The agent "
                    "quoted the guidance back, that message reached the "
                    "summarizer, and the summary carried it. This is exactly the "
                    '"permitted, not guaranteed" behavior the issue describes, '
                    "and it varies run to run on the same model. "
                    f"view={self.view_map_after}"
                ),
            )
        return TestResult(
            success=False,
            reason=(
                "#4544 REPRODUCED: after condensation the skill body is absent "
                "from the active view and a fresh trigger does not bring it back, "
                "while conversation state still reports the skill as activated. "
                f"activated_knowledge_skills={self.activated_after}, "
                f"sentinel_in_view={self.sentinel_in_view_after}, "
                f"retrigger_activated_skills={self.retrigger_activated_skills}, "
                f"retrigger_extended_content={self.retrigger_extended_content}. "
                "The guidance is silently inapplicable for the rest of the "
                "conversation."
            ),
        )
