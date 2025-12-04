from openhands.sdk.context.agent_context import AgentContext
from openhands.sdk.context.skills import Skill


def make_repo_and_model_skills():
    repo_skill = Skill(
        name="repo", content="Repo baseline", source="repo.md", trigger=None
    )
    claude_skill = Skill(
        name="claude",
        content="Claude-Specific Instructions",
        source="CLAUDE.md",
        trigger=None,
    )
    gemini_skill = Skill(
        name="gemini",
        content="Gemini-Specific Instructions",
        source="GEMINI.md",
        trigger=None,
    )
    return repo_skill, claude_skill, gemini_skill


def _assert_has_repo_only(text: str):
    assert "[BEGIN context from [repo]]" in text
    assert "Repo baseline" in text
    assert "Claude-Specific Instructions" not in text
    assert "Gemini-Specific Instructions" not in text


def _assert_has_repo_and_claude(text: str):
    assert "[BEGIN context from [repo]]" in text
    assert "Repo baseline" in text
    assert "Claude-Specific Instructions" in text
    assert "Gemini-Specific Instructions" not in text


def _assert_has_repo_and_gemini(text: str):
    assert "[BEGIN context from [repo]]" in text
    assert "Repo baseline" in text
    assert "Claude-Specific Instructions" not in text
    assert "Gemini-Specific Instructions" in text


def test_model_specific_repo_instructions_for_claude():
    repo, claude, gemini = make_repo_and_model_skills()
    ctx = AgentContext(skills=[repo, claude, gemini])
    out = ctx.get_system_message_suffix(
        llm_model="litellm_proxy/anthropic/claude-sonnet-4"
    )
    assert out is not None
    _assert_has_repo_and_claude(out)


def test_model_specific_repo_instructions_for_gemini():
    repo, claude, gemini = make_repo_and_model_skills()
    ctx = AgentContext(skills=[repo, claude, gemini])
    out = ctx.get_system_message_suffix(llm_model="gemini-2.5-pro")
    assert out is not None
    _assert_has_repo_and_gemini(out)


def test_model_specific_repo_instructions_for_other_models():
    repo, claude, gemini = make_repo_and_model_skills()
    ctx = AgentContext(skills=[repo, claude, gemini])
    out = ctx.get_system_message_suffix(llm_model="openai/gpt-4o")
    assert out is not None
    _assert_has_repo_only(out)


def test_model_specific_repo_instructions_uses_canonical_name():
    repo, claude, gemini = make_repo_and_model_skills()
    ctx = AgentContext(skills=[repo, claude, gemini])
    out = ctx.get_system_message_suffix(
        llm_model="proxy/test-model", llm_model_canonical="openai/gpt-4o-mini"
    )
    assert out is not None
    _assert_has_repo_only(out)
    # Canonical claude
    out2 = ctx.get_system_message_suffix(
        llm_model="proxy/test-model", llm_model_canonical="anthropic/claude-sonnet-4"
    )
    assert out2 is not None
    _assert_has_repo_and_claude(out2)
