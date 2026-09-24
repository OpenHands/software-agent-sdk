"""Shared fake :class:`PromptSection` for prompt-registry tests."""

from dataclasses import dataclass

from openhands.sdk.context.prompts.section import CacheTier, PromptContext


@dataclass
class FakeSection:
    """Minimal :class:`PromptSection` double with tunable guard/render output."""

    name: str
    cache_tier: CacheTier
    text: str | None = None
    enabled: bool = True

    def guard(self, ctx: PromptContext) -> bool:
        return self.enabled

    def render(self, ctx: PromptContext) -> str | None:
        return self.text
