"""Local test for PR #6276: secrets scope grouping.

Demonstrates that secrets with `scope` / `scope_description` are rendered
grouped in the `<CUSTOM_SECRETS>` system-prompt section, and that the agent
picks the correct credential when asked to act under a specific scope.

Two levels of secrets:
  - personal:  PERSONAL_GITHUB_TOKEN  (the user's own token)
  - organization: ORG_GITHUB_TOKEN   (a shared service-account token)

The agent is asked to perform an action "as the organization" — it should
pick ORG_GITHUB_TOKEN, not the personal one.
"""

import os
import sys

from pydantic import SecretStr

from openhands.sdk import LLM, Agent, Conversation
from openhands.sdk.context.prompts.section import PromptContext, SecretInfo
from openhands.sdk.context.prompts.sections.dynamic import CustomSecretsSection
from openhands.sdk.secret import StaticSecret
from openhands.sdk.tool import Tool
from openhands.tools.terminal import TerminalTool


def render_secret_section() -> str:
    """Render the <CUSTOM_SECRETS> section with two scoped secrets."""
    ctx = PromptContext(
        secret_infos=(
            SecretInfo(
                name="PERSONAL_GITHUB_TOKEN",
                description="GitHub PAT",
                scope="personal",
                scope_description=(
                    "The user's own credentials. Actions are attributed to the "
                    "user and count against their personal rate limits."
                ),
            ),
            SecretInfo(
                name="ORG_GITHUB_TOKEN",
                description="GitHub PAT",
                scope="organization",
                scope_description=(
                    "Shared service-account credentials. Actions are attributed "
                    "to the shared account and should be used for org-level work."
                ),
            ),
        )
    )
    return CustomSecretsSection().render(ctx)


def main() -> int:
    print("=" * 70)
    print("STEP 1: Render the <CUSTOM_SECRETS> section with two scopes")
    print("=" * 70)
    rendered = render_secret_section()
    print(rendered)
    print()

    # Verify the grouping is present
    assert "### personal" in rendered, "personal scope heading missing"
    assert "### organization" in rendered, "organization scope heading missing"
    assert "The user's own credentials" in rendered, "personal scope_description missing"
    assert "Shared service-account credentials" in rendered, (
        "organization scope_description missing"
    )
    print("PASS: both scopes rendered with their descriptions as headings.")
    print()

    print("=" * 70)
    print("STEP 2: Run a conversation and have the agent pick the right token")
    print("=" * 70)

    api_key = os.getenv("LLM_API_KEY")
    if not api_key:
        print("LLM_API_KEY not set — skipping live agent step.", file=sys.stderr)
        return 0
    model = os.getenv("LLM_MODEL", "gpt-5.5")
    base_url = os.getenv("LLM_BASE_URL")

    llm = LLM(
        usage_id="pr-6276-test",
        model=model,
        base_url=base_url,
        api_key=SecretStr(api_key),
    )
    agent = Agent(
        llm=llm,
        tools=[Tool(name=TerminalTool.name)],
    )
    conversation = Conversation(agent=agent)

    # Two secrets at two "levels" (scopes) with different values.
    conversation.update_secrets(
        {
            "PERSONAL_GITHUB_TOKEN": StaticSecret(
                value=SecretStr("ghp_PERSONAL_12345"),
                description="GitHub PAT",
                scope="personal",
                scope_description=(
                    "The user's own credentials. Actions are attributed to the "
                    "user and count against their personal rate limits."
                ),
            ),
            "ORG_GITHUB_TOKEN": StaticSecret(
                value=SecretStr("ghp_ORG_67890"),
                description="GitHub PAT",
                scope="organization",
                scope_description=(
                    "Shared service-account credentials. Actions are attributed "
                    "to the shared account and should be used for org-level work."
                ),
            ),
        }
    )

    # Ask the agent to use the ORG token specifically. The scope_description
    # tells it which token is which.
    conversation.send_message(
        "We need to do an org-level audit. Print ONLY the name of the "
        "environment variable that holds the organization's shared "
        "service-account GitHub token (do not print its value). "
        "Do not use the personal token."
    )
    conversation.run()

    # Inspect the agent's final text answer.
    from openhands.sdk.event import TextContent, Message

    last_text = ""
    for event in reversed(conversation.state.events):
        if isinstance(event, Message) and event.role == "assistant":
            for block in event.content:
                if isinstance(block, TextContent) and block.text.strip():
                    last_text = block.text
                    break
            if last_text:
                break

    print("Agent's answer:")
    print(last_text or "(no assistant text found)")
    print()

    # The agent should name ORG_GITHUB_TOKEN, not the personal one.
    answer = last_text.upper()
    if "ORG_GITHUB_TOKEN" in answer and "PERSONAL_GITHUB_TOKEN" not in answer:
        print("PASS: agent picked ORG_GITHUB_TOKEN (the organization scope).")
        return 0
    print("FAIL: agent did not pick ORG_GITHUB_TOKEN.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
