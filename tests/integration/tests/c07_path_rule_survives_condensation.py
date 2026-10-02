"""Path-triggered rule content across condensation (issue #4544).

The path-rule half of the same question, and the deterministic half. Two
properties of path rules make the loss unconditional rather than probabilistic:

- ``ObservationEvent.__str__()`` is built from ``observation.to_llm_content``
  and omits ``extended_content`` entirely, so a rule body never reaches the
  summarizer at all. It cannot be preserved by a summary that never saw it.
- The ``Skill`` validator forces ``disable_model_invocation=True`` on path
  rules, so there is no ``invoke_skill`` route to fetch the guidance back.

Scenario, end to end against a real LLM: the agent edits a file matching the
rule's glob (a real tool call, so the rule is injected by the production
callback), filler turns push that observation outside ``keep_first``, a real
summarizing condensation runs, and then the agent edits the same file again.

Asserted invariant, same as c06: after condensation the rule text is either
still in the active view or comes back when the path is touched again. This
FAILS on current main by design; see c06's docstring for why a red test is the
requested artifact here.
"""

from openhands.sdk.context.agent_context import AgentContext
from openhands.sdk.context.condenser import LLMSummarizingCondenser
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.event.condenser import Condensation
from openhands.sdk.event.llm_convertible import ObservationEvent
from openhands.sdk.skills import PathTrigger, Skill
from tests.integration.base import BaseIntegrationTest, TestResult


SENTINEL = "PATH_RULE_SENTINEL_9C4E"

RULE_BODY = (
    f"{SENTINEL}\n\n"
    "Repository convention for files under src/: always wrap repository access "
    "in try-finally and release the handle in the finally block."
)

TARGET_REL_PATH = "src/pkg/app.py"

INSTRUCTION: str = "This test defines its own instructions in run_instructions()."


class PathRuleCondensationTest(BaseIntegrationTest):
    """Path-rule content must survive condensation or come back on a fresh touch."""

    INSTRUCTION: str = INSTRUCTION

    def __init__(self, *args, **kwargs):
        self.condensations: list[Condensation] = []
        self.activated_before: list[str] = []
        self.injected_on_first_touch: bool = False
        self.carrier_event_id: str | None = None
        self.carrier_forgotten: bool = False
        self.view_map_after: list[str] = []
        self.sentinel_echoed_after: bool = False
        self.sentinel_in_view_before: bool = False
        self.activated_after: list[str] = []
        self.sentinel_in_view_after: bool = False
        self.second_touch_seen: bool = False
        self.second_touch_extended: list[str] = []
        self.keep_first: int = 4
        super().__init__(*args, **kwargs)

    @property
    def agent_context(self) -> AgentContext:
        return AgentContext(
            skills=[
                Skill(
                    name="style_rule",
                    content=RULE_BODY,
                    source="rules/style.md",
                    trigger=PathTrigger(paths=["src/**/*.py"]),
                )
            ]
        )

    @property
    def condenser(self) -> LLMSummarizingCondenser:
        condenser_llm = self.create_llm_copy("test-condenser-llm")
        return LLMSummarizingCondenser(
            llm=condenser_llm,
            max_size=100,  # condensation happens only where asked for, explicitly
            keep_first=self.keep_first,
        )

    @property
    def max_iteration_per_run(self) -> int:
        return 30

    def conversation_callback(self, event):
        super().conversation_callback(event)
        if isinstance(event, Condensation):
            self.condensations.append(event)

    def setup(self) -> None:
        """Create the file the rule's glob matches."""
        import os

        target = os.path.join(self.workspace, TARGET_REL_PATH)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w") as f:
            f.write("def load():\n    return 1\n")

    @staticmethod
    def _sentinel_in_injection_channel(conversation: LocalConversation) -> bool:
        """Is the rule text still present through the MECHANISM that delivers it?

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
    def _rule_carrying_observations(
        conversation: LocalConversation, start_index: int = 0
    ) -> list[ObservationEvent]:
        """Observations at or after ``start_index`` carrying injected rule text."""
        found = []
        for event in list(conversation.state.events)[start_index:]:
            if isinstance(event, ObservationEvent) and event.extended_content:
                found.append(event)
        return found

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
        # 1. Filler FIRST. The rule-carrying observation must land outside the
        #    protected keep_first prefix; an earlier draft touched the file on
        #    turn one, so the carrier sat at a protected index, condensation
        #    never reached it, and nothing was ever at risk.
        for i in range(self.keep_first):
            conversation.send_message(f"Noting context item {i}; no action needed yet.")

        # 2. Touch the matching path. A real edit through a real tool, so the
        #    rule is injected by the production callback rather than a stub.
        conversation.send_message(
            f"Add a docstring to the load() function in {TARGET_REL_PATH}. "
            "Edit the file directly."
        )
        conversation.run()

        first_touch = self._rule_carrying_observations(conversation)
        self.injected_on_first_touch = any(
            SENTINEL in c.text for obs in first_touch for c in obs.extended_content
        )
        for obs in first_touch:
            if any(SENTINEL in c.text for c in obs.extended_content):
                self.carrier_event_id = obs.id
                break
        self.activated_before = list(conversation.state.activated_path_rules)
        self.sentinel_in_view_before = self._sentinel_in_injection_channel(conversation)

        # 3. Trailing turns so the carrier sits well inside the condensable
        #    range rather than at its edge.
        for i in range(10):
            conversation.send_message(f"Follow-up note {i}; still no action needed.")

        # 4. Real summarizing condensation through the public API.
        conversation.condense()

        self.activated_after = list(conversation.state.activated_path_rules)
        self.sentinel_in_view_after = self._sentinel_in_injection_channel(conversation)
        self.view_map_after = self._view_map(conversation)
        self.sentinel_echoed_after = self._sentinel_echoed_only(conversation)
        self.carrier_forgotten = not any(
            e.id == self.carrier_event_id for e in conversation.state.view.events
        )

        # 5. Touch the same path again and inspect what the new observation carries.
        mark = len(list(conversation.state.events))
        conversation.send_message(
            f"Now also add a trailing comment to {TARGET_REL_PATH}. Edit the file."
        )
        conversation.run()

        second_touch = self._rule_carrying_observations(conversation, start_index=mark)
        # Did the agent actually touch a matching file again? Look for any
        # observation past the mark, injected or not.
        self.second_touch_seen = any(
            isinstance(e, ObservationEvent)
            for e in list(conversation.state.events)[mark:]
        )
        self.second_touch_extended = [
            c.text for obs in second_touch for c in obs.extended_content
        ]

    def verify_result(self) -> TestResult:
        # --- preconditions ---
        if not self.injected_on_first_touch:
            return TestResult(
                success=False,
                reason=(
                    "SCENARIO NOT SET UP: the path rule never injected on the first "
                    f"touch (activated_path_rules={self.activated_before}). The "
                    "agent likely did not edit "
                    f"{TARGET_REL_PATH} through a path-bearing tool."
                ),
            )
        if not self.condensations:
            return TestResult(
                success=False,
                reason="SCENARIO NOT SET UP: no Condensation event was produced.",
            )
        if not self.carrier_forgotten:
            # See c06: without this, "content still in view" can mean the
            # condensation simply never reached the carrying event, and the
            # test passes while the bug is live.
            return TestResult(
                success=False,
                reason=(
                    "SCENARIO NOT SET UP: condensation did not forget the "
                    "rule-carrying observation, so the content was never at risk."
                ),
            )
        if not self.second_touch_seen:
            return TestResult(
                success=False,
                reason=(
                    "SCENARIO NOT SET UP: the agent did not touch the file again "
                    "after condensation, so re-injection was never exercised."
                ),
            )

        # --- the invariant ---
        still_present = self.sentinel_in_view_after
        recovered = any(SENTINEL in text for text in self.second_touch_extended)
        if still_present or recovered:
            return TestResult(
                success=True,
                reason=(
                    f"view={self.view_map_after} "
                    f"Rule content survived through the injection "
                    f"channel (present_in_view={still_present}, "
                    f"recovered_on_retouch={recovered})."
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
                "#4544 REPRODUCED: after condensation the rule body is absent from "
                "the active view and touching the matching path again re-injects "
                "nothing, while conversation state still reports the rule as "
                f"activated. activated_path_rules={self.activated_after}, "
                f"sentinel_in_view={self.sentinel_in_view_after}, "
                f"second_touch_extended={self.second_touch_extended}. "
                "The rule body never reached the summarizer either "
                "(ObservationEvent.__str__ omits extended_content), so no summary "
                "could have preserved it, and disable_model_invocation=True leaves "
                "no invoke_skill route back."
            ),
        )
